# Photoframe project and operations guide

This document records the current scope, design, deployment model, operational behavior, security
boundaries, and planned work for Photoframe. For a short installation and first-use path, start
with [README.md](README.md).

## Scope

Photoframe is a local-first FastAPI application for configuring and operating a digital photo
frame. Version 1:

- connects to an Immich photo library with a read-scoped API key;
- loads albums and filters photos by the configured frame orientation;
- provides browser-based selection, preview, schedule, and render controls;
- prepares originals at the exact display resolution with EXIF correction, central cropping, RGB
  conversion, and Lanczos resizing;
- auto-detects supported Pimoroni Inky panels or uses explicit display dimensions;
- refreshes the catalog and bounded local photo cache unattended;
- supports local-only or local-network HTTP/HTTPS operation without requiring a reverse proxy; and
- includes a hardware-free demo provider and mock renderer for development and review.

The retired Flask implementation is no longer kept in the working tree; it remains available from
the repository's Git history if historical reference is needed.

## Architecture and persistence

The application is deliberately small and single-process:

- `src/photoframe/__main__.py` resolves the persisted listener and starts Uvicorn.
- `src/photoframe/web/` owns typed HTML-form parsing, FastAPI routes, Jinja/HTMX presentation, and
  endpoint-change restart requests.
- `src/photoframe/services/` owns provider-neutral runtime coordination and validated configuration
  workflows, keeping HTTP handlers away from mutable runtime internals.
- `src/photoframe/models.py` defines validated application, listener, display, refresh, and status
  models.
- `src/photoframe/settings.py` performs locked, validated, atomic TOML writes and manages the
  encrypted provider credential store.
- `src/photoframe/tls.py` creates or validates TLS material without returning private-key content
  to the web layer.
- `src/photoframe/providers/` isolates provider APIs; `immich.py` handles compatible Immich album
  and metadata-search response shapes.
- `selector.py`, `image_processing.py`, `renderer.py`, `display.py`, `lifecycle.py`, and `cache.py`
  separate scheduling, preparation, hardware output, refresh, and local-cache responsibilities.

The browser UI uses server-rendered Jinja fragments and a vendored HTMX 2.0.4 production build.
It has no runtime CDN dependency. FastAPI's generated API page remains available at `/docs`,
although the form routes do not yet have complete typed OpenAPI request schemas.

### Data directory

Application state defaults to `./data`. Set `PHOTOFRAME_DATA_DIR` before starting the process or
service to choose another location. The Pi installer passes its selected data directory explicitly.

The directory contains:

- `settings.toml`: human-readable, validated application configuration, including `[network]`;
- `.secret-key` and `secrets.bin`: the local Fernet key and encrypted Immich API key;
- `photo-cache/`: bounded provider originals and cache metadata;
- `tls/photoframe-local.crt` and `tls/photoframe-local.key`: automatic HTTPS material, when used;
  and
- runtime refresh and render status persisted as part of `settings.toml`.

Settings and secrets are written atomically and made owner-readable/writable where supported.
The installer additionally creates the data directory with mode `0700`. Hand edits to
`settings.toml` are validated on the next load; restart Photoframe after an offline edit.

Reset restores application and listener settings to defaults, removes the stored provider
credential and downloaded photo cache, and clears runtime selection. If the listener changes, the
server returns to HTTP on `127.0.0.1:8000` and restarts. An existing app-generated TLS identity is
retained under `tls/` and can be reused if automatic HTTPS is enabled again.

## Raspberry Pi and systemd

From the checkout, run:

```bash
sudo bash ./scripts/install.sh
```

The installer is safe to run again after an update. It:

1. refuses to run the application as root;
2. installs UFW and, when needed, `uv` using the official HTTPS installer;
3. creates an owner-only data directory and locked virtual environment, then runs
   `uv sync --frozen --extra inky` as the service account;
4. renders the hardened `systemd/photoframe.service.template` with absolute paths;
5. enables the service at boot; and
6. starts or restarts it unless `--no-start` is supplied.

The service account defaults to the non-root user who invoked `sudo` (`$SUDO_USER`). This preserves
the existing checkout ownership and avoids a privileged daemon. `--user USER` selects another
existing non-root account; that account must be able to traverse and read the checkout. The data
directory is assigned to that user and mode `0700`; runtime-created settings, secrets, cache, and
TLS keys inherit the unit's `UMask=0077`. If root invokes the script directly, `--user` is required.

Useful commands and variants:

```bash
sudo bash ./scripts/install.sh --user pi --data-dir /var/lib/photoframe
sudo bash ./scripts/install.sh --uv-bin /opt/uv/uv
sudo bash ./scripts/install.sh --no-start
sudo systemctl status photoframe
sudo journalctl -u photoframe -f
sudo bash ./scripts/uninstall.sh
```

Use `--no-install-uv` to require a preinstalled `uv`. Otherwise the installer first searches as the
service user and installs only when no executable is found. A supplied `--uv-bin` must be an
absolute executable path and must successfully run as the service user. Downloads require valid
HTTPS, CA roots, DNS, and any site proxy configuration; failures stop with a focused error rather
than leaving a partially configured service. The uninstaller removes only the systemd unit; it
preserves the checkout, UFW policy, dependencies, and data.

The service launches the `photoframe` console entrypoint, so it honors the listener saved in
`settings.toml`; it is not permanently fixed to localhost. The unit uses `NoNewPrivileges`, a
private temporary directory, read-only system paths, and an explicit writable data directory.
Generated TLS files therefore work without granting write access to the rest of the system.

### Headless first boot

A Pi connected only to an e-ink panel cannot use the default local-only web listener. The explicit
headless option provisions it before the first service start:

```bash
sudo bash ./scripts/install.sh --headless --enable-ufw
```

`--headless` changes only the persisted `[network]` settings, preserving provider, album, display,
and schedule settings on a reinstall. It selects `local_network` (`0.0.0.0`), port `8123`, HTTPS,
and an automatic certificate. The certificate is generated before systemd starts and is reused on
later installs. Without `--headless`, existing listener settings are preserved and a fresh install
continues to default to HTTP `127.0.0.1:8000`.

UFW is always installed, but firewall activation remains an administrator decision:

```bash
sudo bash ./scripts/install.sh --headless                         # stage rules; do not activate UFW
sudo bash ./scripts/install.sh --headless --enable-ufw            # allow SSH/22, then activate UFW
sudo bash ./scripts/install.sh --headless --enable-ufw --ssh-port 2222
sudo bash ./scripts/install.sh --headless --firewall-source 192.168.10.0/24
sudo bash ./scripts/install.sh --headless --firewall-source none  # manage access outside this script
```

The default firewall source, `local`, creates one rule per directly connected non-loopback IPv4
subnet. Use an explicit trusted IPv4 or IPv6 CIDR when route discovery is ambiguous. `any` is
accepted only as an explicit choice and exposes the port on every interface allowed by upstream
networking; Photoframe has no login, so this is rarely appropriate. `none` skips the application
rule. If UFW is inactive, the installer stages rules but does not activate it unless
`--enable-ufw` is present. Before activation it permits the selected SSH TCP port, preventing the
usual remote lockout. This does not account for a more complex SSH source restriction; configure
such policy yourself and omit `--enable-ufw` when necessary.

Find the Pi's local address with `hostname -I`, then browse from another device to
`https://<pi-address>:8123`. Review startup with `sudo systemctl status photoframe` and
`sudo journalctl -u photoframe -b` if it is not reachable. A host firewall must also allow the
chosen port from the local network. `sudo ufw status verbose` shows the effective host policy.

Binding to `0.0.0.0` makes the service reachable through the Pi's network interfaces; it does not
by itself publish the service to the internet. Do not add router port forwarding or a public
firewall rule. Photoframe has no application authentication, so anyone who can reach it can change
its settings. Automatic HTTPS encrypts traffic but its local certificate is not automatically
trusted by browsers; expect to accept it or install an appropriate trust configuration on each
client. After connecting, complete the normal browser setup and make later listener changes under
**Advanced settings**.

## Network and HTTPS operation

Network settings live in the collapsed **Advanced settings** panel and persist under `[network]`
in `settings.toml`.

| UI choice | Bind address | Intended reach |
| --- | --- | --- |
| This device only | `127.0.0.1` | Processes and browsers on the frame itself |
| Devices on my local network | `0.0.0.0` | Interfaces allowed by the host firewall, intended for the local network |

New installations use `127.0.0.1`, port `8000`, and HTTP. Port values are validated from `1` to
`65535`. `0.0.0.0` is a wildcard bind, not an address to enter in a browser: another device should
use the frame's LAN IP or a locally resolvable hostname. Binding to `0.0.0.0` does not itself create
public-internet exposure, but firewall rules, router port forwarding, or other external network
machinery can make it reachable beyond the LAN.

HTTP and HTTPS are both first-class server modes. No reverse proxy is required; any VPN, gateway,
firewall, public certificate automation, or other external network machinery can remain on another
system.

### Automatic local certificate

When **Automatic local certificate** is selected, Photoframe creates a self-signed certificate and
unencrypted private key under `data/tls/` if neither file exists. The TLS directory and files are
made owner-only where the platform supports it. Creation uses exclusive file semantics, so existing
or incomplete material is not silently overwritten. On later starts the same pair is validated and
reused.

The certificate includes local names and addresses known when it is generated. It encrypts traffic,
but browsers and client devices do not trust it automatically. Expect a certificate warning until
the certificate is explicitly accepted or an appropriate trust configuration is installed. A LAN
address acquired after generation can also require regenerating the automatic pair or using a
certificate whose names cover the chosen address.

Do not copy the private key into settings, browser fields, tickets, or logs. Photoframe never renders
the generated key or its path into the UI.

### Supplied certificate and key

**Use supplied files** accepts explicit filesystem paths. The certificate and key must:

- exist as readable regular files for the service account;
- be PEM encoded;
- form a matching server certificate/key pair;
- use an unencrypted private key so unattended startup cannot prompt for a password; and
- contain a certificate that is currently valid.

Photoframe validates the pair before saving the listener configuration and does not modify or
replace supplied files. With the systemd hardening in this repository, place files somewhere the
service account can read; only the Photoframe data directory is writable by the service.

### Restart and reconnect behavior

Saving any listener, port, protocol, or certificate change requires an explicit confirmation. The
response shows the next address, then the supported console entrypoint stops and recreates Uvicorn
in the same process with the saved configuration. The browser can remain on a now-inactive URL, so
reconnect using the previewed scheme, host, and port after the listener is ready.

Startup validates the saved configuration and prepares automatic TLS before binding. Invalid or
incomplete TLS material stops startup rather than falling back silently to HTTP. Correct the files
or edit `settings.toml` offline, then restart the process or service.

## Security model and limitations

Photoframe is intended for a device and local network, not direct public hosting.

- There is no application login, account system, authorization layer, or multi-user isolation.
- HTTPS provides transport encryption only; it does not authenticate a user to Photoframe.
- A client that can reach the UI can change provider, display, listener, and reset settings.
- Do not expose the configured port through public router forwarding or a public firewall rule.
- If remote access is required, enforce authentication and access policy in separately managed
  network infrastructure, such as a private VPN or authenticated gateway.
- The Immich API key should have only the album and asset read permissions Photoframe needs.
- The API key is encrypted at rest, but its Fernet key is stored on the same machine. This protects
  against casual disclosure, not compromise of the application account or host.
- Supplied private-key paths are persisted because Uvicorn needs them at startup. Their contents
  are never stored in `settings.toml`, rendered to the browser, or intentionally logged.
- Treat the application account and owner-protected data directory as the local security boundary.

## Display and image pipeline

Install Inky support with `uv sync --extra inky`; the Pi installer includes this extra. At startup,
Photoframe asks Inky to identify the panel and persists its model and native dimensions. If probing
is unavailable, choose **Pimoroni Inky** and enter both native dimensions manually. Choose **Mock**
for hardware-free development.

Each render downloads or reads the cached original, applies EXIF orientation, converts it to RGB,
centrally crops it to the panel aspect ratio, and resizes it with Lanczos resampling. Both native
dimensions must be known before rendering. Panel-specific palette conversion remains the display
driver's responsibility.

## Monitoring, refresh, and cache

The unattended worker refreshes the selected catalog, prefetches orientation-eligible originals,
and uses a bounded least-recently-used cache under `photo-cache/`. Relevant `[refresh]` settings are:

- `cache_max_bytes` (four GiB by default);
- `cache_prefetch_count`;
- `catalog_refresh_seconds`;
- `retry_seconds`; and
- `health_stale_seconds`.

A provider outage preserves the existing cache and schedules a retry. The persisted refresh status
records attempts, successes, failures, cache size, scheduled render completion, and render errors.

`GET /health` is designed for an HTTP monitor such as Uptime Kuma:

- HTTP 200 with `status: healthy` follows a successful, non-stale refresh.
- HTTP 503 with `status: degraded` is returned before the first success, after a failed refresh, or
  when the most recent success exceeds `health_stale_seconds`.

The JSON response includes retry timing, cache usage, and failure context, allowing monitoring to
detect a running process that can no longer refresh its source.

## Development and quality gates

Install the complete locked development environment:

```powershell
uv sync --all-groups
```

The main tasks are:

```powershell
uv run poe test          # pytest suite
uv run poe typecheck     # Pyright static type checking
uv run poe check-fast    # deterministic offline commit gate
uv run poe pre-commit    # repository hooks and reviewed secret baseline
uv run poe check         # complete pre-push quality and security gate
```

The complete gate covers tests, Ruff lint and formatting, Pyright static type checking, explicit
unused-name checks, dependency declaration and tree checks, Bandit, tracked-file secret detection, and an installed-dependency
vulnerability audit. The vulnerability audit needs current advisory data. Run `uv run poe --help`
for the individual `deps-*` and `security-*` tasks.

Tests are organized by subsystem under `tests/`. Network/TLS coverage includes secure defaults,
persisted Uvicorn startup, automatic certificate reuse, incomplete-material refusal, supplied-file
validation/no-overwrite behavior, port errors, confirmation/restart requests, reset behavior, and
the Advanced settings UI contract.

## Branch and release flow

`develop` is the integration branch and the only branch used for device-soak candidates. `main`
contains production-ready revisions only. GitHub Actions runs the same complete gate as local
`uv run poe check`, with each test, lint, formatting, dependency, and security command visible as a
separate step. Changes reach either branch through a reviewed pull request with that check passing.

Use this flow for normal changes and releases:

1. Create a short-lived topic branch from current `develop`, make the change, run
   `uv run poe check`, and open a pull request targeting `develop`.
2. After review and a passing GitHub Actions check, merge to `develop`. Deploy that exact
   `develop` revision to the supported Raspberry Pi and Inky hardware and record the soak result,
   including restarts, scheduled refreshes/renders, and any relevant upgrade or recovery exercise.
3. Fix soak failures through another reviewed pull request to `develop`. Do not patch `main`
   directly or promote a different revision from the one that was soaked.
4. When the candidate has passed its planned device soak, open a pull request from `develop` to
   `main`. Confirm the release checklist and release notes in that pull request, require the full
   check and a review, then merge it without adding unrelated changes.
5. Review and merge the Release Please pull request, then confirm its tag and GitHub release. Start
   subsequent work from `develop`; if an emergency production fix begins from `main`, merge the
   released fix back into `develop` immediately so the branches do not diverge.

Release Please automates the final version, changelog, tag, and GitHub release. Commit messages that
reach `main` must follow Conventional Commits: use `fix:` for a patch, `feat:` for a minor release,
and `feat!:`/`fix!:` or a `BREAKING CHANGE:` footer for a major release. Documentation, test, CI,
and maintenance-only changes may use `docs:`, `test:`, `ci:`, or `chore:` and do not trigger a
version bump by themselves. Release Please maintains a protected release pull request against
`main`; merging that reviewed PR creates the `v<version>` tag and GitHub release. Do not hand-edit
its `CHANGELOG.md` or release-version changes. `pyproject.toml` is the authoritative version source;
the Python package version and local entry in `uv.lock` are synchronized in the release PR.

Repository setup requires an Actions secret named `RELEASE_PLEASE_TOKEN`. Use a fine-grained token
for this repository with Contents, Issues, and Pull requests write access so Release Please can
create and label release PRs and their commits can trigger the required **Tests** workflow. The
built-in `GITHUB_TOKEN` is intentionally not used because GitHub suppresses workflow runs caused by
that token, which would leave a release PR unable to satisfy protected `main`.

After any push to `main`, the **Sync main to develop** workflow opens or reuses a pull request from
`main` into `develop`, waits for **Tests**, and enables GitHub auto-merge only when the pull request
is conflict-free. A conflict leaves the pull request open for manual resolution, and a merge into
`develop` cannot retrigger the workflow because it listens only to `main`. The workflow uses the
same `RELEASE_PLEASE_TOKEN`; until that secret is configured, it exits successfully with a clear
skip notice and makes no repository change. GitHub repository auto-merge must remain enabled, but
it does not bypass branch rules, reviews, or required checks.

Repository rules should block direct pushes to `main`, require a pull request and approving review,
and require the **Tests** status before merge. Apply the
same pull-request and status-check gate to `develop` so integration history always represents a
candidate that passed the repository check.

## Roadmap

### P0 — security, recovery, and release confidence

- [ ] Define and implement an optional authentication model before recommending access beyond a
  single trusted household.
- [ ] Add an explicit automatic-certificate inspect/rotate/recover workflow that never exposes the
  private key.
- [ ] Add settings schema migrations, backup/restore guidance, and recovery tests for interrupted
  upgrades.
- [ ] Exercise a release candidate on supported Raspberry Pi and Inky combinations, including
  HTTP-to-HTTPS endpoint restarts and boot recovery.

### P1 — usability, accessibility, and API clarity

- [ ] Enrich selected-photo previews with capture time, dimensions, camera data, and privacy-aware
  optional location metadata.
- [ ] Complete typed OpenAPI request bodies and response models for form routes and `/health`.
- [ ] Run a dedicated WCAG and assistive-technology audit covering focus, contrast, reduced motion,
  and HTMX status announcements.
- [ ] Add UI guidance for discovering the frame's current LAN address without implying that
  `localhost` works remotely.

### P2 — compatibility and visual coverage

- [ ] Add a small set of annotated setup/UI screenshots and, once the user supplies it, use a real
  photo of the finished physical frame as the project hero/intro image.
- [ ] Broaden visual regression coverage across mobile browsers and intermediate viewport widths.
- [ ] Track Immich API compatibility across supported server releases with fixture-based contract
  tests.
- [ ] Evaluate additional photo providers behind the existing provider interface.
- [ ] Add optional metrics export without weakening the local-first default.

## Release checklist

Before tagging or pushing a release candidate:

- [ ] Confirm the version and user-visible scope.
- [ ] Run `uv lock --check` and `uv run poe check` with current advisory data.
- [ ] Review the complete diff, including generated lock or secret-baseline changes.
- [ ] Confirm README quick-start commands and all local Markdown links.
- [ ] Test a clean first run at HTTP `127.0.0.1:8000`.
- [ ] Test confirmed changes for LAN binding, port, automatic HTTPS, and supplied HTTPS files.
- [ ] Verify systemd install, restart, boot startup, logs, and uninstall on Raspberry Pi.
- [ ] Confirm no credentials, private keys, data-directory files, caches, or test artifacts are
  tracked.
- [ ] Record known limitations, migration notes, and recovery steps in the release notes.
- [ ] Commit only reviewed changes, create the release tag, and push through the normal protected
  branch/review process.
