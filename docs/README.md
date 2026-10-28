# Documentation

Start with the [installation and first-use guide](../README.md). Documentation describes the
checked-out branch and may include work newer than an installed stable release; consult the
[release notes](../CHANGELOG.md) for that version's features.

## Run and troubleshoot a frame

| Task | Guide |
| --- | --- |
| Install or configure the app | [README](../README.md#install-and-run) |
| Operate Raspberry Pi and systemd installs | [Project operations](../PROJECT.md#raspberry-pi-and-systemd) |
| Configure LAN access or HTTPS | [Network and HTTPS](../PROJECT.md#network-and-https-operation) |
| Understand access and trust limitations | [Security model](../PROJECT.md#security-model-and-limitations) |
| Install, update or recover a managed release | [Managed updates](managed-updates.md) |
| Investigate failures or Wi-Fi recovery | [Diagnostics and connectivity](diagnostics.md) |

## Develop and maintain Photoframe

| Task | Guide |
| --- | --- |
| Understand the application and stored data | [Architecture and persistence](../PROJECT.md#architecture-and-persistence) |
| Run Python and browser quality checks | [Development and quality gates](../PROJECT.md#development-and-quality-gates) |
| Follow shared UI conventions | [v2 design system](ui-v2-design-system.md) |
| Follow repository-specific working rules | [AGENTS.md](../AGENTS.md) |
| Plan remaining product and reliability work | [Roadmap](../PROJECT.md#roadmap) |
| Promote tested work through releases | [Branch and release flow](../PROJECT.md#branch-and-release-flow) |
| Review signing trust and administrator setup | [Signing setup](../release/README.md) |

## Keep repository state tidy

After a release, confirm the automated `main` → `develop` synchronization PR has merged before
starting the next topic branch. Keep documentation aligned with the implementation, mark completed
roadmap entries, and check relative file links and heading anchors when moving content. Keep
release-managed versions and changelogs under Release Please control.

For old branches, fetch current refs with pruning, inspect merged PRs, and confirm each branch tip
is an ancestor of `develop`. Check `git worktree list` before removing a local branch; retain any
active or unfinished checkout. GitHub normally removes a topic's remote branch after merge.
Delete only specifically verified merged topic branches, preserving `main`, `develop`, tags and
active release work. Use the managed-worktree archive operation for managed checkouts so their
state remains recoverable; prune a stale registration only after confirming its directory is gone.
