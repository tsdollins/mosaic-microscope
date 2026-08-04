from ScopeFoundry import HardwareComponent
from qtpy import QtCore
import os
import threading
import numpy as np

class ZWOCameraHW(HardwareComponent):
    
    name ='zwo_camera'
    

    def setup(self):    
        S = self.settings
        S.New('cam_id', dtype=int, initial=0)
        S.New('name', dtype=str, ro=True)
        S.New('img_type', dtype=str, choices=self.img_types.keys())
        S.New('live_update', dtype=bool, initial=True)
        S.New('live_update_period', dtype=int, unit='ms', initial=100)

        # Sensor pixel pitch (auto-read from the camera on connect) and the
        # current ROI. Used to compute the sample field-of-view per objective.
        S.New('pixel_size_um', dtype=float, initial=3.76, ro=True, unit='um')
        S.New('roi_width', dtype=int, initial=3584, vmin=8,
              description='Camera ROI width in px (multiple of 8)')
        S.New('roi_height', dtype=int, initial=3584, vmin=2,
              description='Camera ROI height in px (multiple of 2)')

        # Orientation correction baked into every delivered frame (see
        # orient_frame). Applied identically by the live preview, snap, and the
        # scan data-writers so live view, saved data, and stitching all agree.
        # Default is no flip: raw frames are already in the correct orientation
        # (the live view's vertical display is handled by the plot's invertY,
        # not here). Toggle these only if the physical camera mounting changes.
        S.New('flip_h', dtype=bool, initial=False,
              description='Flip frames horizontally (left-right)')
        S.New('flip_v', dtype=bool, initial=False,
              description='Flip frames vertically (up-down)')

        # Create Logged Quantities for each supported camera control
        for c in self.possible_controls.values():
            unit = c.get('unit',None)
            dtype_str = c.get('dtype', None)
            if dtype_str=='bool':
                dtype=bool
            else:
                dtype=int
            S.New(c['Name'],
                  dtype=dtype,
                  initial = c['DefaultValue'],
                  vmin = c['MinValue'],
                  vmax = c['MaxValue'],
                  description=c['Description'],
                  ro = not c['IsWritable'],
                  unit=unit)
            if c['IsAutoSupported']:
                S.New(c['Name']+"_auto", dtype=bool)
        
        self.live_update_timer = QtCore.QTimer()
        self.live_update_timer.timeout.connect(self.on_live_update_timer)        
        self.live_update_timer.start(100)
        S.live_update_period.add_listener(self.on_new_live_update_period)

        self._video_capture_on = False
        # Re-entrant lock serializing camera SDK operations that change the video
        # stream state (start/stop/set_img_type/close) against the blocking frame
        # grab. The ASI SDK is not safe to call these concurrently for one camera.
        # Re-entrant because set_img_type() calls stop/start_video_capture().
        self._cam_lock = threading.RLock()

        S.roi_width.add_listener(self._apply_roi)
        S.roi_height.add_listener(self._apply_roi)

    def _apply_roi(self):
        """Apply the roi_width/roi_height settings to the camera (auto-centered).
        Safe to call before connect (no-op) and while capturing (stops/restarts)."""
        if not hasattr(self, 'camera'):
            return
        with self._cam_lock:
            vc = self._video_capture_on
            if vc:
                self.stop_video_capture()
            self.camera.set_roi(width=self.settings['roi_width'],
                                height=self.settings['roi_height'])
            if vc:
                self.start_video_capture()

    def on_new_live_update_period(self):
        #print("asdf")
        self.live_update_timer.setInterval(
            self.settings['live_update_period'])
        
    def on_live_update_timer(self):
        S = self.settings
        if S['connected'] and S['live_update']:
            # This runs on the GUI thread. Only poll control values if the camera
            # SDK is free right now -- if a scan / acquisition thread is mid-grab
            # (holding _cam_lock), skip this tick instead of blocking the GUI for
            # the duration of the grab. The re-entrant lock is released again
            # immediately; the individual control read_funcs re-acquire it.
            if self._cam_lock.acquire(blocking=False):
                try:
                    self.read_from_hardware()
                finally:
                    self._cam_lock.release()
    
    def connect(self):
        import zwoasi
        from sys import platform
        if platform == "linux" or platform == "linux2":
            # linux
            zwoasi.init(os.path.dirname(__file__) + "/ASI_linux_mac_SDK_V1.22/lib/x64/libASICamera2.so")
        elif platform == "darwin":
            # OS X
            zwoasi.init(os.path.dirname(__file__) + "/ASI_linux_mac_SDK_V1.22/lib/mac/libASICamera2.dylib")
        elif platform == "win32":
            # Windows
            zwoasi.init(os.path.dirname(__file__) + r"\ASI_Windows_SDK_V1.28\ASI SDK\lib\x64\ASICamera2.dll")
            #zwoasi.init(r"C:\Users\lab\Documents\foundry_scope\ScopeFoundryHW\zwo_camera\ASI_Windows_SDK_V1.28\ASI SDK\lib\x64\ASICamera2.dll")
        S = self.settings

        # Defensively close in case a prior session left the camera handle open
        try:
            zwoasi._close_camera(S['cam_id'])
        except Exception:
            pass

        print(zwoasi.get_num_cameras())

        print(zwoasi.list_cameras())

        self.camera = cam = zwoasi.Camera(S['cam_id'])
        
        self.cam_props = cam.get_camera_property()
        print(self.cam_props)
        
        S['name'] = self.cam_props['Name']
        # Auto-read the sensor pixel pitch (um) from the camera
        if 'PixelSize' in self.cam_props:
            S['pixel_size_um'] = float(self.cam_props['PixelSize'])
        
        
        S.img_type.connect_to_hardware(
            write_func = self.set_img_type)
        S.img_type.write_to_hardware()


        self.controls = cam.get_controls()

        
        for c in self.controls.values():
            print(c)
            if c['Name'] not in S.as_dict().keys():
                print("Skipping control because it is not in LQs", c)
                continue
            lq = S.get_lq(c['Name'])
            lq.change_readonly(not c['IsWritable'])
            if lq.dtype != bool:
                lq.change_min_max(
                    vmin = c['MinValue'],
                    vmax = c['MaxValue'])
                
            # All control get/set go through the SAME _cam_lock as frame capture.
            # These read_funcs are driven by a GUI-thread QTimer (live_update_timer)
            # while the acquisition/measurement thread grabs frames; the ASI SDK is
            # not thread-safe per camera, so unlocked control access here races the
            # frame grabs and can wedge the camera handle (breaking live preview
            # until reconnect -- notably right after a scan finishes).
            def read_func(c=c):
                with self._cam_lock:
                    value,auto = self.camera.get_control_value(c['ControlType'])
                #print("read", c['Name'], value,auto)
                return value
            def write_func(x, c=c):
                #print("write", c['Name'], x)
                with self._cam_lock:
                    self.camera.set_control_value(c['ControlType'], x)
            lq.connect_to_hardware(
                read_func = read_func,
                write_func = write_func
                )
            if c['IsAutoSupported']:
                #print(c['Name'], "auto supported")
                lq_auto = S.get_lq(c['Name']+"_auto")
                def read_func(c=c):
                    with self._cam_lock:
                        value,auto = self.camera.get_control_value(c['ControlType'])
                    return auto
                def write_func(auto,c=c):
                    with self._cam_lock:
                        self.camera.set_control_value(c['ControlType'], self.settings[c['Name']], auto)
                lq_auto.connect_to_hardware(
                    read_func = read_func,
                    write_func = write_func
                    )
                
        for pc in self.possible_controls.values():
            if pc['Name'] not in self.controls.keys():
                lq = S.get_lq(pc['Name'])
                print(f"Possible Control {pc['Name']} not in current camera controls")
                lq.change_readonly(True)
        # Apply the configured ROI (auto-centered). width % 8 == 0, height % 2 == 0.
        self._apply_roi()

            
    def disconnect(self):
        self.settings.disconnect_all_from_hardware()

        with self._cam_lock:
            if self._video_capture_on:
                try:
                    self.camera.stop_video_capture()
                except Exception:
                    pass
                self._video_capture_on = False

            if hasattr(self, 'camera'):
                try:
                    self.camera.close()
                except Exception:
                    pass
    
    
    
    
    def set_img_type(self,imtype):
        type_id = self.img_types[imtype]
        with self._cam_lock:
            vc = self._video_capture_on
            if vc:
                self.stop_video_capture()
            self.camera.set_image_type(type_id)
            if vc:
                self.start_video_capture()

    def start_video_capture(self):
        with self._cam_lock:
            self._video_capture_on = True
            self.camera.start_video_capture()

    def stop_video_capture(self):
        with self._cam_lock:
            self.camera.stop_video_capture()
            self._video_capture_on = False

    def capture_video_frame(self, timeout=None):
        # Holds the lock for the duration of the (blocking) grab so a concurrent
        # stop/close/set_img_type cannot tear the stream down mid-read. Callers
        # should run this off the GUI thread (see the acquisition thread).
        with self._cam_lock:
            if not self._video_capture_on:
                raise IOError("Need to Start video to capture frame")
            if timeout is None:
                return self.camera.capture_video_frame()
            return self.camera.capture_video_frame(timeout=timeout)

    def capture_fresh_frame(self, drain_timeout_ms=50):
        """Return a freshly-exposed frame, discarding any stale buffered frames.

        In video mode the camera continuously exposes and queues frames (FIFO).
        After the stage stops moving the queue can still hold blurred frames that
        were exposed while it was in motion. This drains everything already
        queued, then waits for the next frame -- which is exposed entirely after
        the drain, i.e. while the stage is stationary.

        Call only after the stage has stopped (and settled). Requires video
        capture to be running. The whole drain+grab is done under the camera lock
        so it is one atomic, serialized fresh capture.
        """
        with self._cam_lock:
            if not self._video_capture_on:
                raise IOError("Need to Start video to capture frame")

            # Drain frames already in the buffer (exposed while the stage moved).
            # capture_video_frame raises on timeout once the buffer is empty.
            while True:
                try:
                    self.camera.capture_video_frame(timeout=drain_timeout_ms)
                except Exception:
                    break

            # The next delivered frame is exposed after the drain -> stage stationary.
            return self.camera.capture_video_frame()

    def orient_frame(self, frame):
        """Normalize a raw camera frame: orientation flips + color order.

        Used by BOTH the live preview and the scan data-writers so what you see
        live matches what is saved and later stitched. Flips the first two
        (spatial) axes per flip_h/flip_v, and converts the color channel order
        from the SDK's native BGR to standard RGB. The ZWO ASI SDK delivers
        RGB24 frames in B,G,R byte order (Windows/OpenCV convention); converting
        here -- the single chokepoint -- means saved h5/tif/jpg data and the live
        view are all standard RGB, so downstream tools (napari, stitching) get
        correct colors without swapping. Non-color frames (RAW8/RAW16/Y8) have
        no trailing 3-channel axis and are left in their original channel form.
        """
        S = self.settings
        if S['flip_v']:
            frame = frame[::-1]
        if S['flip_h']:
            frame = frame[:, ::-1]
        # BGR -> RGB for 3-channel color frames only.
        if frame.ndim == 3 and frame.shape[-1] == 3:
            frame = frame[:, :, ::-1]
        return np.ascontiguousarray(frame)

    img_types = {
        'RAW8' : 0,
        'RGB24' : 1,
        'RAW16' : 2,
        'Y8' : 3,
        }    

    # Possible Controls are control dictionaries that
    # come from camera.get_controls()
    # all controls are integer datatypes internally
    # use dtype to override, for example for bools
    # units can be added
    possible_controls = {
         'Gain': {'Name': 'Gain',
          'Description': 'Gain',
          'MaxValue': 600,
          'MinValue': 0,
          'DefaultValue': 0,
          'IsAutoSupported': True,
          'IsWritable': True,
          'ControlType': 0},
         'Exposure': {'Name': 'Exposure',
          'unit': 'us',                      
          'Description': 'Exposure Time(us)',
          'MaxValue': 2000000000,
          'MinValue': 32,
          'DefaultValue': 10000,
          'IsAutoSupported': True,
          'IsWritable': True,
          'ControlType': 1},
         'WB_R': {'Name': 'WB_R',
          'unit': '%',                                   
          'Description': 'White balance: Red component',
          'MaxValue': 99,
          'MinValue': 1,
          'DefaultValue': 60,
          'IsAutoSupported': True,
          'IsWritable': True,
          'ControlType': 3},
         'WB_B': {'Name': 'WB_B',
          'unit': '%',                                   
          'Description': 'White balance: Blue component',
          'MaxValue': 99,
          'MinValue': 1,
          'DefaultValue': 99,
          'IsAutoSupported': True,
          'IsWritable': True,
          'ControlType': 4},
         'Offset': {'Name': 'Offset',
          'Description': 'offset',
          'MaxValue': 80,
          'MinValue': 0,
          'DefaultValue': 8,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 5},
         'BandWidth': {'Name': 'BandWidth',
          'unit': '%',
          'Description': 'The total data transfer rate percentage',
          'MaxValue': 100,
          'MinValue': 40,
          'DefaultValue': 50,
          'IsAutoSupported': True,
          'IsWritable': True,
          'ControlType': 6},
         'Flip': {'Name': 'Flip',
          'Description': 'Flip: 0->None 1->Horiz 2->Vert 3->Both',
          'MaxValue': 3,
          'MinValue': 0,
          'DefaultValue': 0,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 9},
         'AutoExpMaxGain': {'Name': 'AutoExpMaxGain',
          'Description': 'Auto exposure maximum gain value',
          'MaxValue': 600,
          'MinValue': 0,
          'DefaultValue': 300,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 10},
         'AutoExpMaxExpMS': {'Name': 'AutoExpMaxExpMS',
          'unit': 'ms',                                              
          'Description': 'Auto exposure maximum exposure value(unit ms)',
          'MaxValue': 60000,
          'MinValue': 1,
          'DefaultValue': 100,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 11},
         'AutoExpTargetBrightness': {'Name': 'AutoExpTargetBrightness',
          'Description': 'Auto exposure target brightness value',
          'MaxValue': 160,
          'MinValue': 50,
          'DefaultValue': 100,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 12},
         'HardwareBin': {'Name': 'HardwareBin',
          'dtype': 'bool',
          'Description': 'Is hardware bin2:0->No 1->Yes',
          'MaxValue': 1,
          'MinValue': 0,
          'DefaultValue': 0,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 13},
         'MonoBin': {'Name': 'MonoBin',
          'dtype': 'bool',                     
          'Description': 'bin R G G B to one pixel for color camera, color will loss',
          'MaxValue': 1,
          'MinValue': 0,
          'DefaultValue': 0,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 18},
         'Temperature': {'Name': 'Temperature',
          'unit': 'C',                                   
          'Description': 'Sensor temperature(degrees Celsius)',
          'MaxValue': 1000,
          'MinValue': -500,
          'DefaultValue': 20,
          'IsAutoSupported': False,
          'IsWritable': False,
          'ControlType': 8},
         'CoolPowerPerc': {'Name': 'CoolPowerPerc',
          'unit': '%',                 
          'Description': 'Cooler power percent',
          'MaxValue': 100,
          'MinValue': 0,
          'DefaultValue': 0,
          'IsAutoSupported': False,
          'IsWritable': False,
          'ControlType': 15},
         'TargetTemp': {'Name': 'TargetTemp',
          'unit': 'C',          
          'Description': 'Target temperature(cool camera only)',
          'MaxValue': 30,
          'MinValue': -40,
          'DefaultValue': 0,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 16},
         'CoolerOn': {'Name': 'CoolerOn',
          'dtype': 'bool',              
          'Description': 'turn on/off cooler(cool camera only)',
          'MaxValue': 1,
          'MinValue': 0,
          'DefaultValue': 0,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 17},
         'AntiDewHeater': {'Name': 'AntiDewHeater',
          'dtype': 'bool',                            
          'Description': 'turn on/off anti dew heater(cool camera only)',
          'MaxValue': 1,
          'MinValue': 0,
          'DefaultValue': 0,
          'IsAutoSupported': False,
          'IsWritable': True,
          'ControlType': 21},
         'HighSpeedMode':{ 'Name': 'HighSpeedMode',
           'dtype': 'bool', 
           'Description': 'Is high speed mode:0->No 1->Yes',
           'MaxValue': 1,
           'MinValue': 0,
           'DefaultValue': 0,
           'IsAutoSupported': False,
           'IsWritable': True,
           'ControlType': 14},
         'GPS':{'Name': 'GPS', 
           'dtype': 'bool',                 
           'Description': 'the camera has a GPS or not',
           'MaxValue': 1, 
           'MinValue': 0,
           'DefaultValue': 0,
           'IsAutoSupported': False,
           'IsWritable': False,
           'ControlType': 22}
         }
