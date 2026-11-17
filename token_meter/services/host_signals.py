"""Read-only host signals for pacing background work on macOS: thermal state and time since the last input.

Each reading is a number or None where unknown or unsupported; nothing here raises, and no reading reveals
what the user is doing, only how long the Mac has gone without keyboard, mouse, or trackpad input.
"""

import re
import subprocess
import sys

_thermal = None  # (send function, NSProcessInfo instance, thermalState selector), opened once per process


def thermal_state():
    """macOS thermal state: 0 nominal, 1 fair, 2 serious, 3 critical; None where unknown."""
    global _thermal
    if sys.platform != "darwin":
        return None
    import ctypes
    import ctypes.util
    try:
        if _thermal is None:
            objc = ctypes.CDLL(ctypes.util.find_library("objc"))
            ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
            objc.objc_getClass.restype = objc.sel_registerName.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = objc.sel_registerName.argtypes = [ctypes.c_char_p]
            address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
            send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(address)
            send_long = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(address)
            info_class = objc.objc_getClass(b"NSProcessInfo")
            if not info_class:
                return None
            info = send_id(info_class, objc.sel_registerName(b"processInfo"))
            if not info:
                return None
            _thermal = (send_long, info, objc.sel_registerName(b"thermalState"))
        send_long, info, selector = _thermal
        state = send_long(info, selector)
    except Exception:  # an unexpected Objective-C runtime only makes the reading unknown
        return None
    return state if 0 <= state <= 3 else None


def seconds_since_input():
    """Seconds since the last keyboard, mouse, or trackpad input on this Mac; None where unknown."""
    if sys.platform != "darwin":
        return None
    try:
        output = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-r", "-d", "1", "-k", "HIDIdleTime"],
                                capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'"HIDIdleTime" = (\d+)', output)
    return int(match.group(1)) / 1e9 if match else None
