# Photoframe

A local-first FastAPI application for configuring and proving a photo-frame workflow before
connecting e-ink hardware. Version one connects to Immich, selects an album, filters images to
the frame orientation, and deterministically shows which photo is active now and when it changes.

The previous Flask application is preserved unchanged in `archive/legacy-flask/`.

## Run

```powershell
uv sync
uv run photoframe
```

Photoframe listens on localhost by default. Open <http://127.0.0.1:8000> on the same machine.
Set `PHOTOFRAME_HOST` to change the bind address, `PHOTOFRAME_PORT` to change the port, and
`PHOTOFRAME_DATA_DIR` to change the data directory (which defaults to `./data`). To allow other
devices on a trusted local network to connect, set `PHOTOFRAME_HOST=0.0.0.0` and open
`http://<device-ip>:8000`.

The application has no user login in version one. Binding to `0.0.0.0` makes it reachable on
interfaces permitted by the host firewall, so do not expose port 8000 directly to the public
internet. For access outside a trusted home network, place it behind a TLS reverse proxy with
authentication or use a private VPN.

Create an Immich API key with album and asset read access. Enter the server root URL (for example
`https://photos.example.com`) and the key in the app. The key is never rendered back to the browser.
It is encrypted locally in `data/secrets.bin`; its separate key file and data directory are made
owner-only where the platform supports it. Treat the machine account as the security boundary.

Configuration is human-editable TOML in `data/settings.toml`. Writes are validated and atomic.
After hand-editing it, restart the app so validation is applied.

## Display image preparation

Set **Native width** and **Native height** in the workflow settings to the exact pixel dimensions
of the installed e-ink panel. They are deliberately not guessed: a production frame must identify
its own panel resolution. Both fields must be set before a render can begin.

On each render, Photoframe downloads the selected asset's original image and uses Pillow to apply
EXIF orientation, convert it to RGB, crop it centrally to the panel's aspect ratio, and resize it
with Lanczos resampling. The result exactly matches the configured panel dimensions. It is retained
in memory as the handoff to the e-ink driver; panel-specific palette conversion remains the
driver's responsibility.

## Pimoroni Inky display

Install the official hardware support on the Pi with `uv sync --extra inky`. At startup,
Photoframe asks Inky to auto-detect the attached panel and saves its model and native resolution
in `settings.toml`; the image pipeline prepares images at exactly that resolution. If detection is
unavailable, select **Pimoroni Inky** in Frame settings and enter a model label plus width and
height manually. Development hosts remain hardware-free, and selecting **Mock** disables probing.

## Raspberry Pi installation

Using a `systemd` service with `uv` is an appropriate deployment model for a single Pi: systemd
starts the app at boot and restarts it after a crash, while `uv` creates and uses the checkout's
locked virtual environment. Install `uv` for the regular Linux user that owns this checkout, then
on the Pi run:

```bash
sudo bash ./scripts/install.sh
```

The installer synchronizes `uv.lock`, creates `data/` with owner-only permissions, installs and
enables `photoframe.service`, and starts it. It runs the service as the user who invoked `sudo`,
never as root. The service binds to localhost on port 8000; use a private VPN or an authenticated
TLS reverse proxy if you need remote access. Useful variants are:

```bash
sudo bash ./scripts/install.sh --user pi --data-dir /var/lib/photoframe
sudo bash ./scripts/install.sh --no-start
sudo systemctl status photoframe
sudo journalctl -u photoframe -f
```

Run the installer again after updating the checkout to re-sync dependencies and refresh the unit.
To remove just the boot service, while preserving configuration, encrypted credentials, and the
checkout, run:

```bash
sudo bash ./scripts/uninstall.sh
```

## Unattended refresh, cache, and monitoring

Photoframe maintains a local cache of provider originals under `data/photo-cache/`.
The worker refreshes the selected album catalog, prefetches a bounded number of
orientation-eligible images, and uses the cached original during rendering. Its
settings are in the `[refresh]` section of `settings.toml`: `cache_max_bytes`
(four GiB by default), `cache_prefetch_count`, `catalog_refresh_seconds`, and
`retry_seconds`. Least-recently-used originals are removed before a new download
would exceed the configured capacity. A source outage leaves the previously
cached images intact and the worker retries after `retry_seconds`.

`GET /health` is suitable for an Uptime Kuma HTTP monitor. It returns HTTP 200
and `status: healthy` after a successful, non-stale refresh. Before the first
success, after a failed refresh, or once the last success is older than
`health_stale_seconds`, it returns HTTP 503 with `status: degraded`, the retry
time, cache usage, and failure count. Monitoring the endpoint therefore detects
an application which is running but can no longer refresh its photo source.

## Development

```powershell
uv sync --all-groups
uv run poe check
```

`uv run poe check` is the complete pre-push gate. It runs the tests, Ruff linting and
format verification, explicit unused import/variable checks, dependency declaration checks,
the full UV dependency tree, Bandit static security analysis, tracked-file secret detection,
and a published-vulnerability audit of the installed environment.

For a fast, deterministic check before each local commit, use:

```powershell
uv run poe check-fast
uv run poe pre-commit
```

The first command runs the offline quality and source-security gate. The second runs the local
pre-commit configuration, including secret detection against the reviewed `.secrets.baseline`.
The dependency vulnerability audit needs advisory data, so it remains part of the full
`uv run poe check` gate. Run `uv run poe --help` to list every focused task, including
`deps-tree`, `deps-check`, and the individual `security-*` checks.

The Immich adapter keeps API paths and compatibility parsing isolated in
`src/photoframe/providers/immich.py`. It supports both album responses containing `assets` and
newer servers that require metadata search. Any incompatible server response is surfaced in the UI
instead of being guessed silently.

HTMX is vendored at `src/photoframe/static/vendor/htmx-2.0.4.min.js` from the
official `htmx.org@2.0.4` production distribution. The UI therefore has no
runtime CDN or internet dependency.

## Nice-to-have improvements

- Enrich the selected-photo preview with human-friendly image metadata instead of relying only on
  often-opaque phone filenames. Surface useful fields when available, such as capture date and
  time, camera and lens, dimensions, orientation, and optional location, using provider metadata
  or embedded EXIF with graceful handling for missing fields and sensitive GPS data.
- Enrich FastAPI's generated OpenAPI documentation with typed request bodies for the form-based
  workflow routes and explicit response models for endpoints such as `GET /health`. All routes are
  already listed at `/docs`, but these schemas would make the page a complete, useful API contract.
- Run a dedicated WCAG and assistive-technology audit, including focus visibility, contrast,
  reduced-motion behaviour, and screen-reader announcements for HTMX-updated status content.
- Broaden visual regression coverage across additional mobile browsers and intermediate viewport
  widths beyond the acceptance sizes used for version one.
