# Forge

### Home Assistant development workspace

Forge is an admin-only workshop for inspecting, developing and maintaining a Home Assistant appliance. It combines a conversational workspace with live appliance tools, durable task history and explicit permission controls.

## What you get

- **Account-aware model controls.** Discover compatible models through your OpenAI account, then choose a model and a supported reasoning level for the next task.
- **A focused workspace.** Responsive dark/light themes, safe Markdown, code blocks, searchable conversations, file attachments and an activity panel.
- **Recovery that keeps its place.** Durable SQLite history, per-tool journals, duplicate-send protection and explicit recovery of interrupted work.
- **Human-controlled changes.** Read-only by default; each explicit Send may grant changes for at most 60 minutes. Running work never inherits a later UI setting.
- **Appliance tools.** Inspect Home Assistant and Supervisor apps, read/edit backed-up mapped files, validate configuration and manage apps within the granted scope.
- **Git development and publication.** One persistent account SSH key, public-key-only settings and verified host pins. Initialize or clone projects, review changes, stage files, commit, fetch and push through explicitly authorized tasks. Existing repository-scoped read-only keys remain supported.
- **Ingress-only access.** Administrator panel, actual-peer checks, CSRF protection and no published LAN port.

## Start here

1. Install Forge from a repository you trust, or use the local app source.
2. Set your OpenAI API key privately in the app's **Configuration** tab. ChatGPT subscriptions do not replace API billing.
3. Start Forge and choose **Open Web UI** in Home Assistant.
4. Open **Model & effort**, refresh the account list and choose a compatible model. Review cost/latency before using a high-effort setting.
5. Start with a read-only request. Enable **Allow changes** only for work you intend to authorize.

The existing app default is preserved: `gpt-6-astra` / `max`. It is not a promise of account access or a low-cost default. The picker lets you choose a different supported combination without restarting the app.

**This is a privileged development assistant, not a household voice assistant.** AI-generated changes can be wrong. Keep Home Assistant backups and review consequential operations. Shell and sensitive-file access remain disabled by default and can only be changed by the human administrator.

## Documentation

- [User guide, permissions and recovery](DOCS.md)
- [Model discovery and compatibility](MODELS.md)
- [Git over SSH: connections, backups and revocation](SSH.md)
- [Development and test guide](TESTING.md)
- [Release notes](CHANGELOG.md)

The app remains an **experimental release candidate** until an attended update and real Ingress/provider smoke test are complete. Source checks and mocked tests do not establish production end-to-end behavior.
