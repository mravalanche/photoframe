#!/usr/bin/env bash
# Provision the current checkout as a boot-started systemd service on Raspberry Pi OS.
set -euo pipefail

readonly SERVICE_NAME=photoframe SYSTEMD_DIR=/etc/systemd/system
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly TEMPLATE="$PROJECT_DIR/systemd/photoframe.service.template"

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install.sh [options]

Install the current checkout, its locked dependencies, and a boot-started
Photoframe systemd service. The default service user is the user who invoked
sudo. Use --user to select another existing, non-root account.

Options:
  --headless              Persist LAN access, HTTPS with an automatic local
                          certificate, and port 8123 before the first start.
  --user USER             Run the service as USER (default: $SUDO_USER).
  --data-dir PATH         Persist application data at PATH (default: ./data).
  --uv-bin PATH           Use this uv executable instead of finding/installing it.
  --no-install-uv         Fail instead of installing uv when it is missing.
  --firewall-source SPEC  Headless UFW reach: local (default), a trusted IPv4/IPv6
                          CIDR, any (explicit broad exposure), or none.
  --enable-ufw            Enable UFW after first allowing SSH. Without this option,
                          rules are installed but an inactive UFW remains inactive.
  --ssh-port PORT         SSH port protected before --enable-ufw (default: 22).
  --no-start              Install and enable the service without starting it now.
  -h, --help              Show this help.

Normal mode preserves existing listener settings and new installs remain on
HTTP 127.0.0.1:8000. Headless mode intentionally replaces only [network]
settings. Photoframe has no login: never use --firewall-source any unless the
network policy around this Pi is trusted and deliberate.
EOF
}

die() { echo "install: $*" >&2; exit 1; }
note() { echo "install: $*"; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "$1 requires a value"; }
safe_path() {
  [[ "$1" != *$'\n'* && "$1" != *$'\r'* && "$1" != *$'\t'* && "$1" != *' '* &&
    "$1" != *'"'* && "$1" != *'%'* && "$1" != *'\\'* ]] ||
    die "paths must not contain whitespace or systemd-special characters: $1"
}
valid_port() { [[ "$1" =~ ^[0-9]+$ && "$1" -ge 1 && "$1" -le 65535 ]]; }

APP_USER="${SUDO_USER:-}"; DATA_DIR="$PROJECT_DIR/data"; UV_BIN=""
INSTALL_UV=1; HEADLESS=0; START_SERVICE=1; ENABLE_UFW=0
FIREWALL_SOURCE=local; SSH_PORT=22
while [[ $# -gt 0 ]]; do
  case "$1" in
    --headless) HEADLESS=1; shift ;;
    --user) need_value "$@"; APP_USER="$2"; shift 2 ;;
    --data-dir) need_value "$@"; DATA_DIR="$2"; shift 2 ;;
    --uv-bin) need_value "$@"; UV_BIN="$2"; shift 2 ;;
    --no-install-uv) INSTALL_UV=0; shift ;;
    --firewall-source) need_value "$@"; FIREWALL_SOURCE="$2"; shift 2 ;;
    --enable-ufw) ENABLE_UFW=1; shift ;;
    --ssh-port) need_value "$@"; SSH_PORT="$2"; shift 2 ;;
    --no-start) START_SERVICE=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (use --help for usage)" ;;
  esac
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "run this script with sudo"
[[ -f "$PROJECT_DIR/pyproject.toml" && -f "$PROJECT_DIR/uv.lock" && -f "$TEMPLATE" ]] ||
  die "run from a complete Photoframe checkout"
[[ -n "$APP_USER" ]] ||
  die "could not identify the sudo user; pass --user with an existing non-root account"
id -- "$APP_USER" >/dev/null 2>&1 || die "service user does not exist: $APP_USER"
[[ "$APP_USER" != root ]] || die "refusing to run the application as root; pass --user"
valid_port "$SSH_PORT" || die "invalid SSH port: $SSH_PORT"
[[ "$FIREWALL_SOURCE" =~ ^(local|any|none)$ || "$FIREWALL_SOURCE" =~ ^[0-9A-Fa-f:.]+/[0-9]+$ ]] ||
  die "--firewall-source must be local, any, none, or an IPv4/IPv6 CIDR"
(( HEADLESS )) || [[ "$FIREWALL_SOURCE" == local ]] ||
  die "--firewall-source is only meaningful with --headless"

APP_GROUP="$(id -gn -- "$APP_USER")"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
[[ -n "$APP_HOME" && -d "$APP_HOME" ]] || die "could not determine home directory for $APP_USER"
safe_path "$PROJECT_DIR"; safe_path "$DATA_DIR"; safe_path "$APP_HOME"
DATA_DIR="$(realpath -m -- "$DATA_DIR")"
case "$DATA_DIR" in
  /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|"$APP_HOME"|"$PROJECT_DIR")
    die "refusing unsafe data directory: $DATA_DIR"
    ;;
esac

apt_install() {
  local missing=() package
  for package in "$@"; do
    dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'ok installed' || missing+=("$package")
  done
  ((${#missing[@]})) || return 0
  command -v apt-get >/dev/null 2>&1 || die "missing packages (${missing[*]}) and apt-get is unavailable"
  note "Installing required OS packages: ${missing[*]}"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
}

apt_install ufw
if [[ -z "$UV_BIN" ]]; then
  UV_BIN="$(runuser -u "$APP_USER" -- env HOME="$APP_HOME" sh -lc 'command -v uv' 2>/dev/null || true)"
fi
if [[ -z "$UV_BIN" ]]; then
  (( INSTALL_UV )) || die "uv was not found for $APP_USER; omit --no-install-uv or pass --uv-bin"
  apt_install ca-certificates curl
  UV_BIN="$APP_HOME/.local/bin/uv"
  if [[ ! -x "$UV_BIN" ]]; then
    note "Installing uv for $APP_USER with the official installer"
    uv_installer="$(mktemp)"
    trap 'rm -f "${tmp_unit:-}" "${uv_installer:-}"' EXIT
    curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o "$uv_installer" ||
      die "could not download the uv installer; check DNS, HTTPS, and proxy settings"
    chown "$APP_USER:$APP_GROUP" "$uv_installer"
    runuser -u "$APP_USER" -- env HOME="$APP_HOME" UV_INSTALL_DIR="$APP_HOME/.local/bin" \
      UV_NO_MODIFY_PATH=1 sh "$uv_installer" || die "uv installation failed"
  fi
fi
[[ "$UV_BIN" = /* ]] || die "uv path must be absolute: $UV_BIN"
[[ -x "$UV_BIN" ]] || die "uv is not executable: $UV_BIN"
safe_path "$UV_BIN"
runuser -u "$APP_USER" -- env HOME="$APP_HOME" "$UV_BIN" --version >/dev/null ||
  die "uv cannot run as $APP_USER: $UV_BIN"

install -d -o "$APP_USER" -g "$APP_GROUP" -m 0700 "$DATA_DIR"
note "Syncing locked dependencies with uv as $APP_USER"
runuser -u "$APP_USER" -- env HOME="$APP_HOME" PHOTOFRAME_DATA_DIR="$DATA_DIR" \
  UV_PROJECT_ENVIRONMENT="$DATA_DIR/venv" \
  "$UV_BIN" sync --frozen --extra inky

if (( HEADLESS )); then
  note "Persisting headless listener settings and preparing its automatic certificate"
  runuser -u "$APP_USER" -- env HOME="$APP_HOME" PHOTOFRAME_DATA_DIR="$DATA_DIR" \
    UV_PROJECT_ENVIRONMENT="$DATA_DIR/venv" \
    "$UV_BIN" run --no-sync python - "$DATA_DIR" <<'PY'
import sys
from pathlib import Path

from photoframe.models import CertificateMode, NetworkAccess, WebProtocol
from photoframe.settings import SettingsRepository
from photoframe.tls import generate_local_certificate

data_dir = Path(sys.argv[1])
repository = SettingsRepository(data_dir)
settings = repository.load()
settings.network.access = NetworkAccess.LOCAL_NETWORK
settings.network.port = 8123
settings.network.protocol = WebProtocol.HTTPS
settings.network.certificate_mode = CertificateMode.AUTOMATIC
settings.network.certificate_path = None
settings.network.private_key_path = None
repository.save(settings)
generate_local_certificate(data_dir)
PY
fi

configure_ufw_rule() {
  local source="$1"
  if [[ "$source" == any ]]; then
    note "Adding explicitly requested broad UFW rule for TCP port 8123"
    ufw allow 8123/tcp comment 'Photoframe headless'
  else
    note "Allowing TCP port 8123 in UFW from $source"
    ufw allow from "$source" to any port 8123 proto tcp comment 'Photoframe headless'
  fi
}

if (( HEADLESS )) && [[ "$FIREWALL_SOURCE" != none ]]; then
  if [[ "$FIREWALL_SOURCE" == local ]]; then
    mapfile -t local_subnets < <(
      ip -o -4 route show scope link 2>/dev/null |
        awk '$1 != "default" && $1 != "127.0.0.0/8" && $1 ~ /^[0-9]+\./ {print $1}' |
        sort -u
    )
    ((${#local_subnets[@]})) ||
      die "no directly connected IPv4 subnet found; pass --firewall-source CIDR (or none)"
    for subnet in "${local_subnets[@]}"; do configure_ufw_rule "$subnet"; done
  else
    configure_ufw_rule "$FIREWALL_SOURCE"
  fi
fi

if (( ENABLE_UFW )); then
  note "Protecting SSH port $SSH_PORT before enabling UFW"
  ufw allow "$SSH_PORT/tcp" comment 'SSH - preserved by Photoframe installer'
  ufw --force enable
elif ufw status 2>/dev/null | grep -q '^Status: inactive'; then
  note "UFW remains inactive; rules are staged. Re-run with --enable-ufw to activate it safely."
fi

escape_sed() { printf '%s' "$1" | sed 's/[&|]/\\&/g'; }
tmp_unit="$(mktemp)"
trap 'rm -f "${tmp_unit:-}" "${uv_installer:-}"' EXIT
sed -e "s|@APP_USER@|$(escape_sed "$APP_USER")|g" \
  -e "s|@APP_GROUP@|$(escape_sed "$APP_GROUP")|g" \
  -e "s|@INSTALL_DIR@|$(escape_sed "$PROJECT_DIR")|g" \
  -e "s|@DATA_DIR@|$(escape_sed "$DATA_DIR")|g" \
  -e "s|@APP_HOME@|$(escape_sed "$APP_HOME")|g" \
  -e "s|@UV_BIN@|$(escape_sed "$UV_BIN")|g" "$TEMPLATE" > "$tmp_unit"
install -m 0644 "$tmp_unit" "$SYSTEMD_DIR/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME.service"

if (( START_SERVICE )); then
  systemctl restart "$SERVICE_NAME.service"
  systemctl --no-pager --full status "$SERVICE_NAME.service"
  if (( HEADLESS )); then
    note "Photoframe is available at https://<pi-address>:8123 (browser trust warning expected)."
  else
    note "Photoframe is running with its saved listener configuration."
    note "New installs use HTTP 127.0.0.1:8000; manage the address in Advanced settings."
  fi
else
  note "Photoframe is installed and enabled; start it with: sudo systemctl start $SERVICE_NAME"
fi
