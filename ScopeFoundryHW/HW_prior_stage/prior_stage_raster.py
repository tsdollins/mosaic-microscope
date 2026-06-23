from ScopeFoundry.scanning import BaseRaster2DSlowScan
import time


class PriorStage2DScan(BaseRaster2DSlowScan):

    name = 'prior_stage_raster'

    def __init__(self, app):
        BaseRaster2DSlowScan.__init__(self, app,
                                      h_limits=(-50, 50), v_limits=(-50, 50),
                                      h_spinbox_step=0.1, v_spinbox_step=0.1,
                                      h_unit="mm", v_unit="mm",
                                      circ_roi_size=0.5)

    def setup(self):
        BaseRaster2DSlowScan.setup(self)
        self.stage = self.app.hardware['prior_stage']

    def new_pt_pos(self, x, y):
        if not self.stage.settings['connected']:
            raise IOError("Not connected to Prior stage")
        self.stage.settings["x_target"] = x
        self.stage.settings["y_target"] = y
        while self.stage.is_busy_xy():
            time.sleep(0.03)

    def move_position_start(self, h, v):
        print(f'start scan, moving to x={h:.4f}, y={v:.4f}')
        self.stage.settings["x_target"] = h
        self.stage.settings["y_target"] = v
        while self.stage.is_busy_xy():
            time.sleep(0.03)

    def move_position_slow(self, h, v, dh, dv):
        print(f'new line, moving to x={h:.4f}, y={v:.4f}')
        # Approach from slightly behind to minimise backlash error
        #self.stage.settings["x_target"] = h - 0.02
        self.stage.settings["y_target"] = v
        #while self.stage.is_busy_xy():
        #    time.sleep(0.03)
        self.stage.settings["x_target"] = h
        while self.stage.is_busy_xy():
            time.sleep(0.03)

    def move_position_fast(self, h, v, dh, dv):
        self.stage.settings["x_target"] = h
        # Wait estimated travel time rather than polling to avoid limiting pixel rate
        time.sleep(1.2 * abs(dh) / self.stage.settings['speed_xy'])
