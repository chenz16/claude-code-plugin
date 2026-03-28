"""
Shared global hotkey detection via evdev.

Works without X11/Wayland — suitable for systemd services.
User must be in 'input' group: sudo usermod -aG input $USER
"""

import evdev
from evdev import ecodes


def find_keyboard():
    """Find the first keyboard device with standard keys."""
    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    for dev in devices:
        caps = dev.capabilities(verbose=False)
        if ecodes.EV_KEY in caps:
            keys = caps[ecodes.EV_KEY]
            if ecodes.KEY_SPACE in keys and ecodes.KEY_A in keys:
                print(f"  Keyboard: {dev.name} ({dev.path})", flush=True)
                return dev
    return None


def require_keyboard():
    """Find keyboard or exit with error."""
    dev = find_keyboard()
    if not dev:
        print("ERROR: No keyboard found. Make sure user is in 'input' group.", flush=True)
        print("  Run: sudo usermod -aG input $USER", flush=True)
        exit(1)
    return dev
