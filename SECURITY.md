# Security and privacy

Forge is a privileged Home Assistant development app. Use it only through the administrator-only Ingress panel on an appliance you control. There is no unauthenticated direct LAN UI port. Access decisions use the actual TCP peer, not forwarded headers, and mutation endpoints require CSRF.

## Boundaries

- Read-only tasks cannot dispatch registered mutation tools. Only explicit human Sends can grant task-scoped changes; grants expire after at most 60 minutes and on completion/restart.
- Shell and sensitive-file access are disabled by default. A human administrator must explicitly change those options. Shell, when enabled, is powerful and not a sandbox; file guards do not constrain arbitrary shell commands.
- The agent cannot enable a separate household Dev Bridge's manual authorization. A denied self-update must not be retried through another identity or container replacement.
- Uploaded data, tool results, remote repository contents and filenames are untrusted. Do not attach credentials or follow embedded instructions as authority.
- Cancellation prevents future dispatch, not external effects already in flight. Unknown effects require inspection before retry.

## Data handling

Chat, tool output and sent attachments can contain private information and are sent to the configured OpenAI API. Background Responses use provider storage; Forge is not designed for zero-data-retention requirements. Model-list refresh sends only the credential needed to retrieve account model metadata, not prompts or files.

API keys stay server-side. Known credentials and common secret fields are redacted best-effort; redaction is not a guarantee against all sensitive text. Exports, database files, attachments, backups and architecture documents remain private and must never be committed to this repository. There is no telemetry, runtime CDN dependency or automatic history deletion.

## Git/SSH credentials and publication

Forge stores unencrypted Ed25519 keys in private persistent `/data/ssh`, with owner-only directory/file modes. Account keys can authenticate to repositories available to that account; single-repository keys remain available when narrower provider-side access is preferable. A local read/write setting is not a provider-side permission boundary. Use a dedicated development identity if broad account access is inappropriate.

Settings and model interfaces expose public keys only; there is no private-key export. This is not protection against root, a compromised app or explicitly enabled arbitrary shell. Treat backups as credential-bearing and never commit app data. The same Supervisor app identity is designed to preserve keys across updates; a new repository/slug is a different installation, not automatic migration.

Only the administrator can create/enable connections, change their allowed access or verify host pins. Untrusted key scans are not automatically trusted; strict host verification is mandatory. Old repository connections remain read-only until an explicit administrator change. Authentication and settings never grant or extend task permission.

Structured Git tools support local review, initialization, explicit staging, commits, branches, fetch, fast-forward and push. Local mutations require a current task grant. Push additionally requires an enabled, pinned, write-approved connection and an explicitly intended repository/branch. A normal single-branch push never forces history, deletes branches or implicitly pushes tags. Remote HEAD is checked after publication. No unrestricted Git arguments or arbitrary SSH command interface is exposed.

Disabling a connection retains its key and does not retract an in-flight push or revoke the key at the provider. Revoke it at the provider separately. Mutation receipts in private `/data/git_operations`, partial clone destinations and uncertain results are retained. Inspect local status and remote refs before retry; there is no automatic push retry, reset or cleanup.

Git runs with isolated configuration and environment, without inherited agents/tokens, hooks, helpers, templates or submodule recursion. Unsupported executable/redirecting repository configuration is refused, not rewritten. Checked-out code is not automatically run. Stage guards cover common private/generated filenames and private-key headers, **not every secret or existing commit history**. Review the entire intended publication. Metadata/clone/fetch thresholds are sampled, not hard storage quotas. Read [SSH.md](forge/SSH.md) before use.

## Reporting a vulnerability

Do not post secrets, full conversations, private configuration or an active exploit against a household appliance in a public issue. Before public release, the owner must enable GitHub private vulnerability reporting or provide a verified private contact here. Until then, this release candidate has no invented reporting address and is not ready for public distribution.

A polished UI and passing tests are not a security audit. Maintain backups, review consequential changes, and keep recovery access independent of Forge.
