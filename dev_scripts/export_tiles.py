"""Export individual scan tiles from a simple_tiled_image H5 file as PNGs.

The scan measurement stores every acquired tile in a single dataset:

    measurement/simple_tiled_image/live_img_map

with shape (Nk, Nv, Nh, th, tw)      for grayscale, or
          (Nk, Nv, Nh, th, tw, 3)    for RGB.

Nk is the outer scan index (almost always 1), Nv/Nh are the vertical/horizontal
tile counts (rows/cols), and th/tw are the tile height/width. Tiles are stored
uint8 and are already in the correct display orientation (the acquisition flip is
baked in), so we write them out as-is.

Examples
--------
Export every tile:
    python export_tiles.py data\\260709_161955_simple_tiled_image.h5 --all

Export specific tiles by (row, col):
    python export_tiles.py scan.h5 --tiles 0,0 3,5 3,6

Export a rectangular block of rows/cols:
    python export_tiles.py scan.h5 --rows 2-4 --cols 0-9

Choose an output directory:
    python export_tiles.py scan.h5 --all --out my_tiles
"""

import argparse
import os

import h5py
import imageio.v2 as imageio

DSET = "measurement/simple_tiled_image/live_img_map"


def parse_range(spec):
    """Expand a "start-end" (inclusive) or single "n" spec into a list of ints."""
    out = []
    for part in spec.replace(",", " ").split():
        if "-" in part:
            lo, hi = part.split("-")
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def parse_tiles(specs):
    """Parse ["r,c", ...] pairs into a list of (row, col) tuples."""
    pairs = []
    for s in specs:
        r, c = s.split(",")
        pairs.append((int(r), int(c)))
    return pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("h5_path", help="Path to the *_simple_tiled_image.h5 file")
    ap.add_argument("--out", default=None,
                    help="Output directory (default: <h5>_tiles next to the file)")
    ap.add_argument("--all", action="store_true",
                    help="Export every tile in the scan")
    ap.add_argument("--tiles", nargs="+", metavar="R,C", default=None,
                    help="Specific tiles as row,col pairs (e.g. 0,0 3,5)")
    ap.add_argument("--rows", default=None,
                    help="Row selection, e.g. '2-4' or '0,2,5' (pairs with --cols)")
    ap.add_argument("--cols", default=None,
                    help="Col selection, e.g. '0-9' or '1,3' (pairs with --rows)")
    ap.add_argument("--k", type=int, default=0,
                    help="Outer scan index (default 0; usually the only one)")
    args = ap.parse_args()

    if not (args.all or args.tiles or (args.rows and args.cols)):
        ap.error("select tiles with --all, --tiles, or --rows/--cols")

    out_dir = args.out or (os.path.splitext(args.h5_path)[0] + "_tiles")
    os.makedirs(out_dir, exist_ok=True)

    with h5py.File(args.h5_path, "r") as f:
        if DSET not in f:
            raise KeyError(f"{DSET!r} not found in {args.h5_path}. "
                           f"Is this a simple_tiled_image scan?")
        imgs = f[DSET]
        shape = imgs.shape
        if len(shape) not in (5, 6):
            raise ValueError(f"Unexpected dataset shape {shape}")
        Nk, Nv, Nh = shape[0], shape[1], shape[2]
        print(f"dataset {shape}  ->  Nk={Nk} rows(Nv)={Nv} cols(Nh)={Nh}, "
              f"{'RGB' if len(shape) == 6 else 'grayscale'}")

        if args.k >= Nk:
            raise IndexError(f"--k {args.k} out of range (Nk={Nk})")

        # Build the (row, col) work list from whichever selector was given.
        if args.all:
            targets = [(r, c) for r in range(Nv) for c in range(Nh)]
        elif args.tiles:
            targets = parse_tiles(args.tiles)
        else:
            rows = parse_range(args.rows)
            cols = parse_range(args.cols)
            targets = [(r, c) for r in rows for c in cols]

        written = 0
        for r, c in targets:
            if not (0 <= r < Nv and 0 <= c < Nh):
                print(f"  skip {r},{c}: out of range")
                continue
            tile = imgs[args.k, r, c, ...]
            name = f"tile_k{args.k}_r{r}_c{c}.png"
            imageio.imsave(os.path.join(out_dir, name), tile)
            written += 1
            print(f"  wrote {name}")

    print(f"\nDone: {written} tile(s) -> {out_dir}")


if __name__ == "__main__":
    main()
