# Managed web updates

The Software updates page shows the actual installed distribution version. Source
checkouts can discover stable releases but cannot overwrite themselves. Managed
installations can download and verify a release, then apply it with an explicit
**Apply & restart** confirmation. Checks run weekly with jitter and can be disabled;
they never download or install automatically.

## First release and supported device

This feature is an integration candidate until a real-device soak has passed.
The production public signing key is pinned in this branch; see
[release signing setup](../release/README.md) for its fingerprint and release controls.
Physical-device acceptance is still required before the first managed release.
v1.2.0 and v1.2.1 remain source/manual releases and cannot bootstrap this
updater. Missing or invalid signatures fail closed.

The first managed target is **Linux ARM64, glibc 2.36 or newer, CPython 3.12**
(64-bit Raspberry Pi OS Bookworm or compatible). Install a root-owned Python 3.12
with its `venv`/`ensurepip` support first; the system Python on your Pi may be a
different version. A user-writable uv interpreter is unsuitable for the root
helper. The bundle contains the locked Inky extra and native wheels built on ARM64
Bookworm; installation uses its offline wheelhouse. Other CPU architectures,
Python minor versions and operating systems are not managed targets yet.

## One-time migration

Keep the existing checkout and its original virtual environment until the managed
installation has passed acceptance. Back up the existing data directory, including
hidden files, and record its path, service account, listener address and unit.
Do not publish that backup: it contains the encrypted provider credential and key.

Obtain the **future signed release's** archive and `photoframe-manifest.json` from
the official release. Obtain the reviewed `release/update-signing-key.pem` through
an independently trusted checkout and compare its fingerprint. A key downloaded
beside a bundle is not an independent trust anchor.

From a reviewed checkout containing this feature and its installed dependencies:

```bash
sudo /path/to/checkout/.venv/bin/python -m photoframe.updater.bootstrap \
  --user pi --data-dir /path/to/existing/data \
  --python /usr/bin/python3.12 \
  --bundle /path/to/photoframe-VERSION-linux-aarch64.tar.gz \
  --manifest /path/to/photoframe-manifest.json \
  --public-key /path/to/reviewed/update-signing-key.pem
```

No update PIN or passphrase is required during installation or browser updates.
Existing photos, settings, credentials, TLS identity,
album selection and scheduling remain in their original data directory. The
bootstrap replaces the systemd unit, keeping a recovery copy of the original.
Saved listener and firewall settings are preserved.

Managed layout:

| Path | Purpose |
| --- | --- |
| `/opt/photoframe/versions/VERSION` | Root-owned release and its own installed environment |
| `/opt/photoframe/current` | Atomic pointer to the active release |
| `/opt/photoframe/helper-venv` | Independent root-owned updater runtime |
| `/opt/photoframe/snapshots` | Private pre-update settings snapshots |
| `/etc/photoframe` | Pinned public key and helper token |
| `/var/lib/photoframe-updater` | Root-owned durable job and rollback state |
| Existing data directory | App-owned persistent photos, settings, secrets and TLS |

The helper protocol is intentionally versioned. A future incompatible helper
protocol or signing-key rotation requires an explicit administrator migration;
ordinary app updates cannot silently replace the trust anchor or privileged helper.

## Routine use and failure handling

Open **Software updates** and choose **Check now**. Review the release notes,
choose **Download & verify**, then **Apply &
restart**. Applying waits for application work and the physical display to become
idle; a busy frame leaves the current version running. Other writes are temporarily
rejected while activation is underway.

The helper verifies the pinned signature, repository, version, platform, compatibility,
download size and digest before installing. It rejects prereleases, downgrades,
arbitrary paths, unexpected archive contents and insufficient disk space. Installation
and switching are serialized. Repeated requests cannot create concurrent updates.

The page polls job progress and reconnects after restart with bounded backoff. The
helper checks application readiness and the expected version independently of
Immich availability, using the saved HTTP/HTTPS listener. A failed activation
restores the previous release and pre-update settings once. Failed or interrupted
rollback stops automatic attempts and reports manual recovery. A successful update
retains one previous release; **Restore previous version** also restores that
release's settings snapshot, so configuration changes since the update are lost.

If the page cannot reconnect, use SSH:

```bash
sudo systemctl status photoframe photoframe-updater
sudo journalctl -u photoframe -u photoframe-updater --since '30 minutes ago'
sudo cat /var/lib/photoframe-updater/state.json
readlink -f /opt/photoframe/current
```

Do not delete slots, snapshots or state while an operation is active. Record the
job's failure and selected version before repair. For failed initial migration,
restore the preserved original service unit and start its unchanged checkout only
after the managed service/helper are stopped and the saved settings are restored.
Do not run both installation types against the same data directory.

## Security boundaries

The web process remains unprivileged and cannot write release slots or the helper
runtime. The helper accepts only fixed versioned operations over a restricted local
socket and token; release URLs and repository are fixed. The browser uses a short
session established automatically, with HttpOnly/SameSite cookies, CSRF and Origin
checks. These checks protect browser requests; they do not authenticate users.
Anyone who can access the app can request an official signed update or rollback.
Use the app on a trusted network and use HTTPS for the LAN listener.
Existing installations with an old `update-pin.hash` file can leave it in place;
the app no longer reads it and no credential migration is necessary.

Signing authority and a compromised root account remain trusted. The helper's
local token is available to the app account, so a compromised app process can
request official signed operations but cannot supply arbitrary privileged code.
Root-owned paths and their ancestors must never be writable by the app account.

## Required Raspberry Pi acceptance drill

Record exact commit, bundle digest, OS, Python version, Pi model and Inky panel.
Use a backed-up test frame, a valid signed candidate and a deliberately unhealthy
signed test candidate from an isolated test signing identity. Never publish test
keys or broken artifacts to the production channel.

1. Migrate a working source installation with configured album, credentials, TLS,
   schedule anchor and next due time. Verify these persist, the displayed image is
   unchanged, the reported version matches the installed wheel, and both services
   survive reboot. Confirm the app cannot write immutable slots or helper files.
2. Check manually and with simulated weekly due time; turn checks off. Disconnect
   networking and confirm the cached authenticated release and clear offline state.
   No check may stage or restart the app.
3. Reject a wrong-key/wrong-repository manifest, corrupted archive, prerelease,
   downgrade, traversal archive and low-disk stage without affecting the app.
4. Start an Inky render, then request an update from two browsers. Verify safe busy
   handling, one operation only, responsive status, and rejection of concurrent writes.
5. Apply a healthy candidate. Confirm version-bound readiness over both HTTP and
   configured HTTPS, reconnect, retained previous slot, unchanged credentials/TLS,
   and the next scheduled photo render at the expected time.
6. Apply the unhealthy candidate. Confirm exactly one automatic rollback, restored
   settings ownership/mode, old version health and clear browser diagnostics.
7. Interrupt helper execution during download, installation, switching and rollback.
   Reboot and verify recoverable state or a clear manual-recovery failure, never a
   restart loop or silent success. Exercise the documented original-install recovery.
8. Soak scheduled refresh/render and reboot behavior on the exact develop revision
   before promoting that revision to main. Record failures and fixes in the PR.

Automated tests simulate these failure boundaries and Linux systemd commands;
they do not replace this physical Pi/Inky acceptance drill.
