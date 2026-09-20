# Photoframe

Software updates: see the [managed web updater and one-time migration guide](docs/managed-updates.md).
Managed release installation requires the signing setup described there; existing source installs
continue to work without it.

[![Tests](https://github.com/mravalanche/photoframe/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/mravalanche/photoframe/actions/workflows/tests.yml?query=branch%3Amain)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](https://github.com/mravalanche/photoframe/blob/main/LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Latest release](https://img.shields.io/github/v/release/mravalanche/photoframe?display_name=tag&sort=semver)](https://github.com/mravalanche/photoframe/releases/latest)

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

Frame updates and album changes appear in a pinned activity bar, so their status stays visible
while you scroll on a phone. Album changes run in the background: the bar first reports loading,
then the number of photos checked for the frame. The current album remains selected until the
new album is ready. Keep the page open to see completion, or reopen it to see the running job.
Other changes wait until the album job finishes.

To limit memory use on the frame, Immich responses are capped at 32 MiB and albums at 20,000
assets. Large JPEGs are downsampled before decoding; images that still exceed the decoding limit
use the provider preview when available. Unsupported images are excluded from the frame's photo
list.

## Basic configuration

Portrait and square photos can be browsed alongside landscape photos. Open a photo's
framing controls and choose **Show whole photo** to retain the entire image. Choose **Soft photo
background** for a heavily blurred, muted extension, **Colour wash** for a quiet colour sampled
from the photograph, or plain **White** / **Black**. The foreground stays sharp and unchanged.
Choose **Fill frame** to crop it to the display instead. Saving framing includes
that photo in rotation; use **Show now** when you want to change the physical frame immediately.
These preferences belong to this frame and do not edit your Immich originals.

Use **Hide from this frame** to remove a photo from rotation without deleting it from Immich.
Undo the action or restore it from **Hidden photos**. Hiding does not erase the photo currently
on the physical display; if no photos remain in rotation, restore or include a photo to resume.

For interval scheduling, use the slider to choose common durations from 30 seconds to 1 day.
Choose **Custom interval** for a whole number of seconds, minutes, hours or days—for example,
every 7 minutes or every 90 minutes, up to 30 days. Existing custom durations stay exact.
The next-update preview shows the effect before you save; daily and weekly schedules remain available.

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

## Tests

```powershell
uv sync --all-groups
uv run poe typecheck
uv run poe check-fast
uv run poe pre-commit
uv run poe check
```

`check-fast` is the deterministic offline gate. `pre-commit` runs the repository hooks, including
secret detection. `check` is the complete pre-push gate and also performs the installed-dependency
vulnerability audit, which needs current advisory data.

## Roadmap

See the prioritized checkbox-style [Roadmap](PROJECT.md#roadmap).

## License

Photoframe is free software licensed under the
[GNU Affero General Public License v3.0 or later](LICENSE).

Copyright © 2026 mravalanche
