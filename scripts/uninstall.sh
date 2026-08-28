#!/usr/bin/env bash
# Remove only the systemd unit installed by scripts/install.sh. Project code and data remain.
set -euo pipefail

readonly SERVICE_NAME=photoframe UNIT_PATH=/etc/systemd/system/photoframe.service
case "${1:-}" in
  "") ;;
  -h|--help) echo "Usage: sudo ./scripts/uninstall.sh"; exit 0 ;;
  *) echo "uninstall: unknown option: $1" >&2; exit 1 ;;
esac
[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "uninstall: run this script with sudo" >&2; exit 1; }
if [[ -e "$UNIT_PATH" ]]; then
  systemctl disable --now "$SERVICE_NAME.service" || true
  rm -f -- "$UNIT_PATH"; systemctl daemon-reload; systemctl reset-failed "$SERVICE_NAME.service" || true
  echo "Photoframe service removed. The project checkout and data directory were left intact."
else
  echo "Photoframe service is not installed; nothing to remove."
fi
