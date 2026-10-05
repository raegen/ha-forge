# Forge

**Home Assistant development workspace** — an admin-only, Ingress-based workshop for inspecting and improving your appliance with explicit permissions and recoverable tasks.

Forge includes account-scoped SSH authentication and a structured Git development/publication workflow: initialize or clone a project, review changes, commit, fetch and push an explicitly selected branch. One administrator-created public key can serve multiple repositories; each change still needs the task's permission. See [Git setup and workflow](forge/SSH.md).

This repository contains a source-built Home Assistant app in [`forge/`](forge/README.md). Supported architecture for this candidate: **aarch64**. Other platforms must be built and verified before advertising support.

## Installation

This is an **experimental development repository**, not a stable release or a claim of acceptance into Home Assistant's official repository. Review the release gates below before installing.

To add the repository, use **Settings → Apps → App store → Repositories** in Home Assistant with:

```text
https://github.com/raegen/ha-forge
```

Install **Forge**, configure the OpenAI API key privately in the app options, start it and open the Web UI.

## Release status

Source version: **0.6.0**. Read [PUBLISHING.md](PUBLISHING.md) for the remaining release checks. No license has been selected, and no open-source license is implied. Owner-approved licensing, a private security-reporting route and the remaining build/Ingress checks are still required before a stable public release. Version control does not mark those checks complete.

- [App guide](forge/DOCS.md)
- [Model compatibility](forge/MODELS.md)
- [Git over SSH](forge/SSH.md)
- [Testing](forge/TESTING.md)
- [Security and privacy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

### Existing local installations

The public candidate uses slug `forge`. Existing local installations keep the internal slug `smartass_forge` so their identity and private data are not silently replaced by a rebrand. A repository install has a different Supervisor identity and data directory: **it is not an in-place migration**. Do not run two development workers or uninstall/delete the original to switch. A separately reviewed, backed-up migration is required.
