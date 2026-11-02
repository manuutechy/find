#!/usr/bin/env bash
# Find installer for GNOME on Linux. Safe to run again to update.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="$HOME/.local/share/find"
BIN="$HOME/.local/bin"
APP_ID="io.github.manuutechy.Find"
KEY="${FIND_SHORTCUT:-<Super>k}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }

say "Checking what Find needs"
for cmd in python3 gdbus dconf; do
  command -v "$cmd" >/dev/null || { echo "Missing: $cmd"; exit 1; }
done
/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null || {
  echo "GTK 4 and libadwaita for Python are missing. Install them first:"
  echo "  Arch:          sudo pacman -S python-gobject gtk4 libadwaita"
  echo "  Fedora:        sudo dnf install python3-gobject gtk4 libadwaita"
  echo "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
  exit 1
}
command -v pdftotext >/dev/null || echo "Tip: install poppler (pdftotext, pdftoppm) so Find can read and preview PDFs."

say "Setting up Python packages"
mkdir -p "$DATA" "$BIN" "$HOME/.config/find"
[ -d "$DATA/venv" ] || /usr/bin/python3 -m venv --system-site-packages "$DATA/venv"
"$DATA/venv/bin/pip" install -q --disable-pip-version-check --upgrade anthropic

say "Adding the launchers"
cat > "$BIN/findit" <<SH
#!/bin/sh
exec "$DATA/venv/bin/python" "$DIR/app.py" "\$@"
SH
cat > "$BIN/findit-toggle" <<SH
#!/bin/sh
# Shows Find instantly through the background copy; starts it normally if that isn't running.
gdbus call --session --dest $APP_ID --object-path /io/github/manuutechy/Find \\
  --method org.freedesktop.Application.Activate "{}" >/dev/null 2>&1 || exec "$BIN/findit"
SH
chmod +x "$BIN/findit" "$BIN/findit-toggle"

mkdir -p "$HOME/.local/share/applications" "$HOME/.config/autostart" "$HOME/.config/systemd/user"
cat > "$HOME/.local/share/applications/$APP_ID.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Find
Comment=Search every file and folder on this computer, or ask Claude in plain words
Exec=$BIN/findit
Icon=system-search
Terminal=false
Categories=Utility;
Keywords=search;files;find;ai;
DESK
cat > "$HOME/.config/autostart/$APP_ID.background.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Find (background)
Comment=Keeps Find ready so the shortcut opens it instantly
Exec=$BIN/findit --background
NoDisplay=true
X-GNOME-Autostart-enabled=true
DESK

say "Keeping the index fresh every 15 minutes"
cat > "$HOME/.config/systemd/user/find-index.service" <<UNIT
[Unit]
Description=Find: refresh the search index

[Service]
Type=oneshot
Nice=15
IOSchedulingClass=idle
ExecStart=/usr/bin/python3 $DIR/indexer.py
UNIT
cat > "$HOME/.config/systemd/user/find-index.timer" <<UNIT
[Unit]
Description=Find: refresh the search index every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now find-index.timer >/dev/null 2>&1 || true

say "Adding the $KEY shortcut"
/usr/bin/python3 - "$KEY" "$BIN/findit-toggle" <<'PY'
import ast, subprocess, sys
key, cmd = sys.argv[1], sys.argv[2]
base = "/org/gnome/settings-daemon/plugins/media-keys"
mine = f"{base}/custom-keybindings/find/"
def read(path):
    out = subprocess.run(["dconf", "read", path], capture_output=True, text=True).stdout.strip()
    return out
raw = read(f"{base}/custom-keybindings")
paths = ast.literal_eval(raw.replace("@as ", "")) if raw else []
for p in paths:  # someone else already uses this key?
    if p != mine and read(p + "binding").strip("'") .lower() == key.lower():
        print(f"  {key} is already used by '{read(p + 'name')}'. Set FIND_SHORTCUT, e.g. FIND_SHORTCUT='<Super>space' ./install.sh")
        sys.exit(0)
if mine not in paths:
    paths.append(mine)
subprocess.run(["dconf", "write", f"{base}/custom-keybindings", str(paths)], check=True)
subprocess.run(["dconf", "write", mine + "name", "'Find'"], check=True)
subprocess.run(["dconf", "write", mine + "command", f"'{cmd}'"], check=True)
subprocess.run(["dconf", "write", mine + "binding", f"'{key}'"], check=True)
PY

say "Building the search index (first time takes a minute)"
/usr/bin/python3 "$DIR/indexer.py"

say "Starting Find in the background"
for p in $(pgrep -f "[v]env/bin/python $DIR/app.py" || true); do kill "$p" 2>/dev/null || true; done
setsid -f "$BIN/findit" --background >/dev/null 2>&1 < /dev/null

cat <<MSG

Done. Press $KEY to search.

Claude answers (Tab) need one of:
  - an Anthropic API key saved in ~/.config/find/api-key   (fastest)
  - the claude command (Claude Code) signed in on this computer

Optional: describe your folders in ~/.config/find/context.md so Claude knows your layout.
MSG
