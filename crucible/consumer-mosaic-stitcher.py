print("importing packages")

import os
import json
import threading
import logging
from datetime import datetime, timezone

from crucible import CrucibleClient
from crucible.models import Dataset
import mfid

from utils import setup_pika_client, get_secret
from dotenv import load_dotenv

# Set up logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

# Vars ===========================
load_dotenv()
rmq_host = os.environ.get("RMQ_HOST", "localhost")
rmq_port = int(os.environ.get("RMQ_PORT", 5672))
rmq_pw = get_secret("RABBITMQ_DEFAULT_PW", "rabbitmq_default_pw/versions/1")

crucible_api_url = os.environ.get("CRUCIBLE_API_URL", "https://crucible.lbl.gov/api/v2")
crucible_api_key = get_secret("ADMIN_APIKEY", "crucible_admin_apikey/versions/4")

# Queue this consumer listens on (+ its dead-letter queue for failures).
QUEUE = "mosaic-stitch"
FAILED_QUEUE = "mosaic-stitch-failed"

# measurement/data_type stamped on the derived (stitched) child dataset.
STITCH_MEASUREMENT = "stitched_mosaic"

# RMQ Setup ===========================
connection, channel = setup_pika_client(rmq_host, rmq_port, rmq_pw)
for q in (QUEUE, FAILED_QUEUE):
    channel.queue_declare(queue=q)

# Crucible  ===========================
client = CrucibleClient(api_url=crucible_api_url, api_key=crucible_api_key)


# Functions  ===========================
def download_raw_scan(crucible_client, raw_mfid):
    """Download the raw scan's HDF5 into a per-request directory and return it.

    Unlike the RGA workflow (which downloads + unzips a .zip), a mosaic scan is a
    single ScopeFoundry .h5 uploaded directly, so we fetch it straight into a
    directory that crucible_stitch_process.main() then globs for the scan file.
    """
    directory = os.path.join(".", raw_mfid)
    os.makedirs(directory, exist_ok=True)
    crucible_client.datasets.download(
        raw_mfid, output_dir=directory, no_record=True, include=["*.h5"]
    )
    return directory


def find_existing_stitched_child(crucible_client, raw_mfid):
    """Return the id of an existing stitched child of raw_mfid, or None.

    Makes re-processing idempotent: a re-run reuses the same child dataset id
    (files dedup by SHA256 server-side) instead of creating duplicates.
    """
    try:
        children = crucible_client.datasets.list_children(raw_mfid)
    except Exception as err:
        logger.warning(f"could not list children of {raw_mfid}: {err}")
        return None
    for child in children:
        if child.get("measurement") == STITCH_MEASUREMENT:
            return child["unique_id"]
    return None


def create_stitched_dataset(crucible_client, og_dataset, stitched):
    """Create (or update) the stitched-mosaic child dataset and link it to the raw.

    `stitched` is the dict returned by crucible_stitch_process.main().

    Idempotent: if a stitched child already exists (a re-run), we UPDATE it in
    place. datasets.create() 409s on an existing id ("use PATCH to update it"),
    so reusing the id with create() is wrong -- we add the new file + refresh
    metadata instead. Files dedup by SHA256 server-side, so re-uploading an
    identical mosaic is a no-op.
    """
    raw_mfid = og_dataset["unique_id"]
    mosaic_path = stitched["mosaic_path"]
    child_name = f"stitched_{og_dataset['dataset_name']}"
    timestamp = datetime.fromtimestamp(
        os.path.getmtime(mosaic_path), tz=timezone.utc
    ).isoformat()

    # Stitch QC + provenance -> child scientific metadata.
    scientific_metadata = {
        "source_h5": os.path.basename(stitched["source_h5"]),
        "pixel_size_um": stitched["pixel_size_um"],
        "n_tiles": stitched["n_tiles"],
        "mosaic_shape": stitched["mosaic_shape"],
        "median_correction_px": stitched["median_correction_px"],
        "max_correction_px": stitched["max_correction_px"],
    }

    # Pixel<->stage geometry so the mosaic viewer can place this mosaic as a
    # detail region on a lower-mag mosaic of the same sample (sample-grouped).
    # `geometry` is None for older scans without stage bounds -- skip if so.
    geometry = stitched.get("geometry")
    if geometry:
        scientific_metadata.update({
            "magnification": geometry.get("magnification"),
            "stage_bbox_mm": geometry["stage_bbox_mm"],
            "stage_origin_mm": geometry["stage_origin_mm"],
            "mm_per_px": geometry["mm_per_px"],
            "y_axis_up": geometry["y_axis_up"],
            # coord_frame ties mosaics sharing one stage coordinate system
            # (same instrument + session). Used later to gate cross-session
            # auto-placement; recorded now so no data migration is needed.
            "coord_frame": (f"{og_dataset.get('instrument_name')}/"
                            f"{og_dataset.get('session_name')}"),
        })

    existing_child = find_existing_stitched_child(crucible_client, raw_mfid)
    if existing_child:
        child_id = existing_child
        logger.info(f"stitched child {child_id} already exists; updating in place")
        crucible_client.datasets.add_file_to_dataset(
            child_id, mosaic_path, wait_for_ingestion_response=False)
        crucible_client.datasets.replace_scientific_metadata(
            child_id, scientific_metadata)
        try:
            crucible_client.datasets.update(child_id, timestamp=timestamp)
        except Exception as err:
            logger.warning(f"could not update timestamp on {child_id}: {err}")
    else:
        child_id = mfid.mfid()[0]
        sds = Dataset(
            unique_id=child_id,
            dataset_name=child_name,
            instrument_name=og_dataset.get("instrument_name"),
            measurement=STITCH_MEASUREMENT,
            data_type=STITCH_MEASUREMENT,
            project_id=og_dataset["project_id"],   # inherit the parent's project
            owner_orcid=og_dataset.get("owner_orcid"),
            session_name=og_dataset.get("session_name"),
        )
        sds.timestamp = timestamp
        crucible_client.datasets.create(
            sds,
            files_to_upload=[mosaic_path],
            scientific_metadata=scientific_metadata,
            wait_for_ingestion_response=False,
        )
        # Raw scan (parent) -> stitched mosaic (child)
        crucible_client.datasets.link_parent_child(raw_mfid, child_id)

    # Propagate the parent's sample link(s) to the derived dataset (both paths).
    try:
        for sample in crucible_client.samples.list(dataset_id=raw_mfid):
            crucible_client.samples.add_dataset(sample["unique_id"], child_id)
    except Exception as err:
        logger.warning(f"could not propagate samples to {child_id}: {err}")

    # Attach the client-generated mosaic preview, if one was produced.
    thumbnail_path = stitched.get("thumbnail_path")
    if thumbnail_path:
        try:
            crucible_client.datasets.add_thumbnail(
                child_id, thumbnail_path, thumbnail_name=f"{child_name}_thumb")
        except Exception as err:
            logger.warning(f"thumbnail upload failed for {child_id}: {err}")

    logger.info(f"stitched mosaic -> {child_id} (child of {raw_mfid})")
    return child_id


def run_stitch(ch, method, body, connection):
    message = json.loads(body.decode("utf-8").strip())
    raw_mfid = message["dsid"]

    logger.info(f"received message {message} .. starting processing")
    try:
        # get the dataset SQL record
        og_dataset = client.datasets.get(raw_mfid, include_metadata=True)

        # download the raw scan .h5 into a working directory
        directory = download_raw_scan(client, raw_mfid)

        # run the stitch analysis script (Crucible-agnostic; returns a dict)
        import crucible_stitch_process
        stitched = crucible_stitch_process.main(directory)
        logger.info("Stitching complete")

        # create the derived child dataset + link it to the raw parent
        create_stitched_dataset(client, og_dataset, stitched)
        logger.info("Crucible upload complete.")

        # acknowledge the message
        connection.add_callback_threadsafe(
            lambda: ch.basic_ack(delivery_tag=method.delivery_tag))

    except Exception as err:
        logger.error(f"Error processing dataset {raw_mfid}: {err}")

        def on_failure():
            ch.basic_publish(
                exchange='',
                routing_key=FAILED_QUEUE,
                body=json.dumps(message),
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)

        connection.add_callback_threadsafe(on_failure)


def callback(ch, method, properties, body):
    '''
    Expects a RMQ message with:
    dsid:     The raw dataset ID the stitch request was made for; the stitched
              mosaic is uploaded as a child of it.
    '''
    # Run in a worker thread so the long-running stitch does not block pika
    # heartbeats; the ack/nack is marshalled back via add_callback_threadsafe.
    thread = threading.Thread(
        target=run_stitch, args=(ch, method, body, connection))
    thread.start()


# subscribe to the queue
channel.basic_consume(queue=QUEUE,
                      auto_ack=False,
                      on_message_callback=callback)

# always be listening
print(' [*] Waiting for messages. To exit press CTRL+C')
channel.start_consuming()
