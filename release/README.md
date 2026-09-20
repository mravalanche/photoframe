# Managed release signing setup

## Production trust configuration

Established on 2026-09-20. The Ed25519 public key is
`release/update-signing-key.pem`. Its SHA-256 fingerprint, calculated over DER
SubjectPublicKeyInfo bytes, is:

```text
8066fec68b6ffd9b6dcabbda52ac73c0e0bb0cb74dfbf84e3a4542828e6f30d8
```

The private key is held in the `managed-releases` environment secret
`PHOTOFRAME_RELEASE_SIGNING_KEY`. The environment permits only `v*` tags and requires
approval from `mravalanche`; administrator bypass is disabled. The signing job will
therefore wait for owner approval after a release is published. Review the exact tag,
commit and workflow before approving. Release tags matching `v*` cannot be updated or
deleted under the active repository ruleset, which has no bypass actors.

The initial private-key copy is retained outside Git in the administrator's restricted
local signing directory. Keep an encrypted offline backup before publishing and never
attach the private key to a release or commit it. GitHub cannot return a stored secret.
Physical Pi acceptance and the first signed release have not yet been completed.

## Establishing or replacing trust

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
