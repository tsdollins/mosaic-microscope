from ScopeFoundry import BaseMicroscopeApp
from ScopeFoundryHW.zwo_camera.zwo_camera_hw import ZWOCameraHW
from ScopeFoundryHW.zwo_camera.zwo_camera_capture_measure import ZWOCameraCaptureMeasure

class ZWOCameraTestApp(BaseMicroscopeApp):
    
    name = 'zwo_camera_app'
    
    def setup(self):
        hw = self.add_hardware(ZWOCameraHW(self))
        
        self.add_measurement(ZWOCameraCaptureMeasure(self))
        
                
if __name__ == '__main__':
    import sys
    app = ZWOCameraTestApp(sys.argv)
    app.exec_()