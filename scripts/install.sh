#!/usr/bin/env bash
# Install the current checkout as a systemd service. Run with sudo.
set -euo pipefail

readonly SERVICE_NAME=photoframe SYSTEMD_DIR=/etc/systemd/system
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly TEMPLATE="$PROJECT_DIR/systemd/photoframe.service.template"

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install.sh [options]
Options: --user USER  --data-dir PATH  --uv-bin PATH  --no-start  -h|--help

Installs the current checkout as a boot-started Photoframe systemd service.
EOF
}
die() { echo "install: $*" >&2; exit 1; }
safe_path() { [[ "$1" != *$'\n'* && "$1" != *$'\r'* && "$1" != *' '* ]] || die "paths must not contain whitespace: $1"; }

APP_USER="${SUDO_USER:-}"; DATA_DIR="$PROJECT_DIR/data"; UV_BIN=""; START_SERVICE=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) APP_USER="${2:-}"; shift 2 ;;
    --data-dir) DATA_DIR="${2:-}"; shift 2 ;;
    --uv-bin) UV_BIN="${2:-}"; shift 2 ;;
    --no-start) START_SERVICE=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "run this script with sudo"
[[ -f "$PROJECT_DIR/pyproject.toml" && -f "$TEMPLATE" ]] || die "run from a complete Photoframe checkout"
[[ -n "$APP_USER" ]] || die "pass --user USER when sudo cannot identify the calling user"
id "$APP_USER" >/dev/null 2>&1 || die "user does not exist: $APP_USER"
[[ "$APP_USER" != root ]] || die "refusing to run the application as root; pass --user"
APP_GROUP="$(id -gn "$APP_USER")"; APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
[[ -n "$APP_HOME" && -d "$APP_HOME" ]] || die "could not determine home directory for $APP_USER"
if [[ -z "$UV_BIN" ]]; then UV_BIN="$(runuser -u "$APP_USER" -- sh -lc 'command -v uv' 2>/dev/null || true)"; fi
[[ -n "$UV_BIN" && -x "$UV_BIN" ]] || die "uv was not found for $APP_USER; install it or pass --uv-bin"
safe_path "$PROJECT_DIR"; safe_path "$DATA_DIR"; safe_path "$UV_BIN"; safe_path "$APP_HOME"

mkdir -p "$DATA_DIR"; chown "$APP_USER:$APP_GROUP" "$DATA_DIR"; chmod 700 "$DATA_DIR"
echo "Syncing locked dependencies with uv as $APP_USER..."
runuser -u "$APP_USER" -- env PHOTOFRAME_DATA_DIR="$DATA_DIR" "$UV_BIN" sync --frozen --extra inky

escape_sed() { printf '%s' "$1" | sed 's/[&|]/\\&/g'; }
tmp_unit="$(mktemp)"; trap 'rm -f "$tmp_unit"' EXIT
sed -e "s|@APP_USER@|$(escape_sed "$APP_USER")|g" -e "s|@APP_GROUP@|$(escape_sed "$APP_GROUP")|g" -e "s|@INSTALL_DIR@|$(escape_sed "$PROJECT_DIR")|g" -e "s|@DATA_DIR@|$(escape_sed "$DATA_DIR")|g" -e "s|@APP_HOME@|$(escape_sed "$APP_HOME")|g" -e "s|@UV_BIN@|$(escape_sed "$UV_BIN")|g" "$TEMPLATE" > "$tmp_unit"
install -m 0644 "$tmp_unit" "$SYSTEMD_DIR/$SERVICE_NAME.service"
systemctl daemon-reload; systemctl enable "$SERVICE_NAME.service"
if (( START_SERVICE )); then
  systemctl restart "$SERVICE_NAME.service"; systemctl --no-pager --full status "$SERVICE_NAME.service"
  echo "Photoframe is running at http://127.0.0.1:8000."
else
  echo "Photoframe is installed and enabled; start it with: sudo systemctl start $SERVICE_NAME"
fi
