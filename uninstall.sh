#!/usr/bin/env bash
# Removes Find. Keeps ~/.config/find (your key and notes) unless you pass --all.
set -u
APP_ID="io.github.manuutechy.Find"
systemctl --user disable --now find-index.timer 2>/dev/null
rm -f "$HOME/.config/systemd/user/find-index.service" "$HOME/.config/systemd/user/find-index.timer"
systemctl --user daemon-reload
for p in $(pgrep -f "[f]ind/app.py" || true); do kill "$p" 2>/dev/null; done
rm -f "$HOME/.local/bin/findit" "$HOME/.local/bin/findit-toggle" \
      "$HOME/.local/share/applications/$APP_ID.desktop" "$HOME/.config/autostart/$APP_ID.background.desktop"
/usr/bin/python3 - <<'PY'
import ast, subprocess
base = "/org/gnome/settings-daemon/plugins/media-keys"
mine = f"{base}/custom-keybindings/find/"
raw = subprocess.run(["dconf", "read", f"{base}/custom-keybindings"], capture_output=True, text=True).stdout.strip()
paths = [p for p in (ast.literal_eval(raw.replace("@as ", "")) if raw else []) if p != mine]
subprocess.run(["dconf", "write", f"{base}/custom-keybindings", str(paths)])
subprocess.run(["dconf", "reset", "-f", mine])
PY
rm -rf "$HOME/.local/share/find" "$HOME/.cache/find"
[ "${1:-}" = "--all" ] && rm -rf "$HOME/.config/find"
echo "Find removed."
