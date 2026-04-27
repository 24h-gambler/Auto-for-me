#!/usr/bin/env bash
# Linux user-level systemd service — runs without sudo, starts at login.
#
#   bash scripts/autostart/install-linux.sh
#
# To remove:
#   systemctl --user disable --now auto-for-me
#   rm ~/.config/systemd/user/auto-for-me.service

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
UNIT="$HOME/.config/systemd/user/auto-for-me.service"
mkdir -p "$(dirname "$UNIT")" "$ROOT/state/logs"

cat > "$UNIT" <<EOF
[Unit]
Description=Auto-for-me bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$PY -m src.main
Restart=always
RestartSec=5
StandardOutput=append:$ROOT/state/logs/stdout.log
StandardError=append:$ROOT/state/logs/stderr.log

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now auto-for-me
echo "Installed: $UNIT"
echo "Status:    systemctl --user status auto-for-me"
echo "Logs:      journalctl --user -u auto-for-me -f"

# Make the user's services start at boot, even before they log in.
# (Optional; requires sudo.)
if [[ "${ENABLE_LINGER:-1}" == "1" ]] && command -v loginctl &>/dev/null; then
  echo "→ Enabling linger so the service starts at boot (sudo required):"
  sudo loginctl enable-linger "$USER" || true
fi
