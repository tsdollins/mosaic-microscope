

uses the python-zwoasi python library and SDK from ZWO

https://github.com/python-zwoasi/python-zwoasi
zwoasi-0.1.0.1-py2.py3-none-any.whl

	pip install zwoasi

On MacOS, the default security settings prevent the SDK library from being accessed

xattr -d com.apple.quarantine ScopeFoundryHW/zwo_camera/ASI_linux_mac_SDK_V1.22/lib/mac/libASICamera2.dylib.1.22 

You must also have libusb installed (homebrew works):
	brew install libusb 

