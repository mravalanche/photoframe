# Managed release signing setup

The updater deliberately has no development key or unsigned fallback. Before the
first managed release, the repository owner must:

1. Generate an Ed25519 signing key on a trusted offline/admin machine. Keep the
   private key out of Git and build logs. Export its public key in PEM format.
2. Review and commit only that public key as `release/update-signing-key.pem`.
   Independently compare its fingerprint before installing it on a frame.
3. Create the GitHub environment `managed-releases`, restrict it to protected
   release tags, and store the private PEM as its secret
   `PHOTOFRAME_RELEASE_SIGNING_KEY`. Restrict who can modify this environment.
4. Complete the real Pi acceptance drill in `docs/managed-updates.md`, then use the
   ordinary develop-to-main and Release Please flow. A future published stable
   release builds on ARM64/Python 3.12, runs the quality gate, and signs its bundle.

The workflow refuses missing keys, mismatched public/private keys, prereleases,
versions at or below 1.2.1, and replacement of existing assets. A published source
release is not installable by the managed updater until its signed assets exist.
Do not add assets retrospectively to v1.2.0 or v1.2.1.

The signed manifest binds the official repository, exact commit, stable version,
platform, protocol, schema compatibility, bundle name, byte size and SHA-256.
The release archive is deterministic for a given wheelhouse. Dependencies come
from the hash-checked lock export; native wheel compilation can depend on the
builder toolchain, so this is not a claim of bit-for-bit reproducible builds
across arbitrary build hosts. Its install has no package-index network access.
