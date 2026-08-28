# Photoframe

Photoframe is a local-first web application for running a provider-backed digital photo frame.
It connects to Immich, selects and filters an album for the installed display, prepares images at
the panel's native resolution, and drives supported Pimoroni Inky hardware. A built-in simulator
makes the complete setup flow usable before hardware is connected.

For architecture, Raspberry Pi operations, security detail, and planned work, see
[PROJECT.md](PROJECT.md).

## Prerequisites

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- An Immich server and an API key with album and asset read access
- Optional: a supported Pimoroni Inky display on Raspberry Pi

## Install and run

```powershell
uv sync
uv run photoframe
```

Open <http://127.0.0.1:8000> on the same device. Photoframe initially uses HTTP, port `8000`, and
the local-only `127.0.0.1` listener. Application data defaults to `./data`; set
`PHOTOFRAME_DATA_DIR` before startup only when a different data location is required.

On a Raspberry Pi, one command installs any missing `uv` and UFW prerequisites, the locked
application with Inky support, and its boot-started systemd service:

```bash
sudo bash ./scripts/install.sh
```

Normal installs retain the secure local-only defaults. For a Pi with no browser or keyboard, use:

```bash
sudo bash ./scripts/install.sh --headless --enable-ufw
```

Headless mode persists LAN access, HTTPS with an automatically generated local certificate, and
port `8123`; the firewall rule defaults to directly connected IPv4 subnets. Before enabling UFW,
the installer allows SSH on port `22` so the current remote session remains recoverable. Omit
`--enable-ufw` to leave an inactive firewall inactive, or use `--ssh-port` for a nonstandard SSH
port. See [Raspberry Pi and systemd](PROJECT.md#raspberry-pi-and-systemd) for service users,
firewall reach, trust warnings, and all installer options.

## First use

1. Under **Photo provider**, enter the Immich server root URL and API key, then save and verify.
2. Under **Album**, refresh the available albums and choose one.
3. Under **Display & timing**, choose orientation, rotation, photo order, and display settings.
4. Preview a photo. **Show now** updates the frame; **Start rotation here** changes the schedule.
5. If another device on the local network needs access, open **Advanced settings** and
   configure the listener before saving.

The API key is stored locally and is never rendered back into the browser. Native display width
and height must both be known before an image can be rendered; supported Inky hardware is detected
at startup when possible.

## Basic configuration

The collapsed **Advanced settings** panel controls the web listener:

- **This device only** binds to `127.0.0.1`.
- **Devices on my local network** binds to `0.0.0.0`; connect using the frame's LAN IP address.
- The listening port accepts values from `1` to `65535`.
- HTTP is the default. HTTPS can use an automatically generated local certificate or an explicitly
  supplied matching, unencrypted PEM certificate and private key.

Changing the active endpoint requires confirmation. Photoframe restarts its web server after the
response is sent, so reconnect at the address shown in the preview. `0.0.0.0` is a bind address,
not a browser address, and `localhost` does not reach the frame from another device.

## Security note

Photoframe has no application login or user authentication. LAN binding is intended only for a
local network you control; do not forward the configured port from an internet-facing router. HTTPS
encrypts traffic but does not add authentication. The automatic local certificate is not trusted
by browsers automatically, so each client must accept it or be configured to trust it. See
[Security model and limitations](PROJECT.md#security-model-and-limitations) before enabling LAN
access.

## Quality checks

```powershell
uv sync --all-groups
uv run poe check-fast
uv run poe pre-commit
uv run poe check
```

`check-fast` is the deterministic offline gate. `pre-commit` runs the repository hooks, including
secret detection. `check` is the complete pre-push gate and also performs the installed-dependency
vulnerability audit, which needs current advisory data.

## Roadmap

See the prioritized checkbox-style [Roadmap](PROJECT.md#roadmap).
