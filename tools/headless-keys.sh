#!/usr/bin/env bash
# Drives Find with real key presses in a private, invisible GNOME (mutter) session.
# Usage: dbus-run-session -- tools/headless-keys.sh "words to type" [seconds to wait after Tab]
# Then read ~/.cache/find/debug.log to see each key, the Claude call and the result.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEXT="${1:-invoice}"; WAIT="${2:-12}"
mutter --headless --no-x11 --wayland-display=find-test --virtual-monitor 1280x800 >/dev/null 2>&1 &
MP=$!; sleep 3
export WAYLAND_DISPLAY=find-test GDK_BACKEND=wayland FIND_DEBUG=1 FIND_KEEP=1
python3 "$DIR/app.py" >/dev/null 2>&1 &
AP=$!; sleep 3
python3 - "$TEXT" "$WAIT" <<'PY'
import sys, time
from gi.repository import Gio, GLib
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
def call(path, iface, method, args=None, sig=None):
    return bus.call_sync("org.gnome.Mutter.RemoteDesktop", path, iface, method, args,
                         GLib.VariantType(sig) if sig else None, 0, 5000, None)
sp = call("/org/gnome/Mutter/RemoteDesktop", "org.gnome.Mutter.RemoteDesktop", "CreateSession", None, "(o)")[0]
call(sp, "org.gnome.Mutter.RemoteDesktop.Session", "Start")
def key(sym):
    for down in (True, False):
        call(sp, "org.gnome.Mutter.RemoteDesktop.Session", "NotifyKeyboardKeysym", GLib.Variant("(ub)", (sym, down)))
        time.sleep(0.02)
key(0xffe1)  # Shift first: the virtual keyboard drops its very first key
for ch in sys.argv[1]:
    key(0x20 if ch == " " else ord(ch)); time.sleep(0.05)
time.sleep(0.5); key(0xff09)  # Tab
time.sleep(float(sys.argv[2]))
PY
kill $AP $MP 2>/dev/null
tail -20 "$HOME/.cache/find/debug.log"
