# Forge — operator guide

## Getting started

Open Forge through Home Assistant's admin-only app panel. Existing chats are migrated automatically.
Set the OpenAI API key in app options; never paste a key into chat. Existing model, reasoning, web search,
code interpreter, backup and file-access options are preserved.

New and existing chats start **read-only** until you choose otherwise. **Allow changes** is remembered
**per conversation until unchecked**, including across page refreshes, devices and Forge restarts.
Changing the checkbox saves that conversation's preference immediately; it does not create work or
change a running task's authorization. Other conversations are unaffected. A failed/uncertain save
is read back, never automatically retried; sending waits for a known preference.

Each new prompt you explicitly **Send** carries the visible checkbox choice and receives its own
bounded task grant when checked, for at most **60 minutes**. A stale browser cannot silently submit
its old checked preference after another browser unchecks it: it must refresh and you must send again.
Retrying an accepted request keeps its original task and deadline, not a new grant. Reconnecting,
reloading, polling and server startup never send a prompt or renew authorization.

Authorization still ends when the task ends or Forge restarts, independently of the remembered choice.
**Resume safely** keeps its separate, initially unchecked authorization box. Use **Stop task** to stop
ongoing work; the remembered checkbox is a preference for future prompts, not a live task control.
Neither the model nor a tool may set the preference for you or renew its own grant. The household
Dev Bridge's manual Dev Mode is separate and is not enabled by Forge.

Shell remains disabled by default. If explicitly enabled in app options it can run arbitrary commands
with the app's privileges. It is NOT a filesystem sandbox. Keep it disabled unless you need trusted
developer work. Protected file checks and best-effort redaction do not turn enabled shell into a
security boundary. Sensitive/auth storage access remains disabled by default.

## Workspace

- Mobile: open the menu at the top left to create or switch conversations.
- Search chat titles; rename, archive/restore or export using the conversation menu.
- Archive is non-destructive; it does not delete chat history or attachments.
- Text drafts and selected chat are saved in this browser, without credentials or tool results.
- Enter sends, Shift+Enter adds a line; composition/IME Enter does not send.
- Code/text copy, conservative Markdown, source citation links and provider artifact downloads.
- Provider files are downloadable only when cited in a stored message. Downloads are limited to
  20 MiB and two concurrent requests. Provider containers can expire; regenerate expired artifacts.
- Activity shows tool starts/results, provider waits, interruptions and errors.
- Send an answer/change of direction during work with **Stop & send**. The update is saved atomically with the request to stop old future actions. One worker continues from saved results after the old task stops; an already-dispatched action can still finish. A second update waits until that handoff finishes.
- **Waiting for you** is a real execution pause created by `ask_user`, not punctuation detection. Reply normally to continue; Stop/Resume are hidden while waiting. Stop remains available for active work and Resume for interrupted/failed/cancelled work.
- The theme can follow the system or be set explicitly to light/dark.

## Model and effort controls

Open **Model & effort** in the top bar or the model label below the composer. Forge fetches
available account model IDs from OpenAI and offers compatible Responses models. Effort is a native
select, not a numeric slider: the allowed named levels differ by model. Non-reasoning models show
Not applicable. See [Model selection](MODELS.md) for the compatibility table, sources and fallback.

A choice affects the next explicit Send in this conversation, not a running task. The accepted task
saves its own immutable settings; uncertain retries and Resume keep those settings. Selection never
grants change permission, starts generation or changes app options. Refreshing the list is read-only.
App defaults are retained on upgrades; higher reasoning effort can increase latency and cost.

Fresh installations create a generic `config/FORGE_CONTEXT.md` when absent. An existing
`config/SMARTASS_CONTEXT.md` remains authoritative on this appliance; Forge neither renames nor
replaces it. Project context is architectural memory, never change authorization. Existing chat,
permission, attachment, options and export compatibility are preserved.

## Git development and publication

Open **Connections** to create one persistent account SSH key, copy its public key and independently
verify/pin the host fingerprint. Register that public key with your Git account once, then use it
for repositories your account can access. A repository-scoped key is also available. Existing 0.5
keys retain their scope and read-only access; no automatic permission upgrade occurs.

With a fresh **Allow changes** task, Forge can initialize or clone a source project, inspect diffs,
stage explicit files, commit with your approved author identity, fetch/fast-forward updates and push
a reviewed branch to your explicit destination. Push requires a write-enabled connection and provider
write access; it never force-pushes, deletes branches or publishes automatically. Structured tools do
not require unrestricted shell access. Opening settings generates no keys and contacts no host.

Git workspaces are project subdirectories under `addons/` or `share/`, not private config/data roots.
SSH authentication does not renew a grant or let the model create/enable keys, change access or trust
hosts. Key material stays in `/data/ssh`, operation receipts in `/data/git_operations`; preserve both
with the app's consistent backup. Same-app updates are designed to retain them, while a new repository
slug is not an automatic migration. Explicitly enabled arbitrary shell is not a credential sandbox.

See [Git over SSH](SSH.md) for the short setup/publication workflow, tool reference, staged-content
review, limits and retained uncertain effects. Connection read tests do not establish push access;
local Git tests do not prove real SSH publication. Repository contents remain untrusted data, not
instructions or permission to execute code.

## Photos and files

Use **Attach files**, drop files on the workspace, or paste a clipboard image into the composer.
Images have previews; PDFs and text/code files have download cards. Remove unsent files from the draft
before sending if necessary. A message can contain files without accompanying text.

Supported: JPEG, PNG, WebP, still GIF, PDF, and supported UTF-8 text/code/config/log extensions.
HEIC/HEIF photos must first be exported as JPEG or PNG. Animated images, SVG, archives, audio, video,
executables, binary text and unsupported types are rejected. PDFs receive basic format checks only;
Forge neither renders them locally nor executes their contents. A model may reject a damaged or
password-protected PDF even after upload succeeds.

Limits are enforced by the server, not just the browser:
- Four files per message; 10 MiB per file and 20 MiB per message.
- UTF-8 text files: 256 KiB each.
- Images: bounded dimensions/pixel count, corrected orientation, maximum stored edge 3072 pixels.
  Stored images are normalized and EXIF/GPS/comments removed; the original on your device is unchanged.
- Local attachment quota: `attachment_storage_mb`, default 256 MiB, configurable 32–4096 MiB.
  Previews and orphaned files count toward usage. A 1024-object ceiling and 1 GiB free-space floor also
  apply. Lowering the quota never deletes existing files; new uploads fail clearly when space is low.

Uploading stages a **local private draft**. File contents are sent to OpenAI only when you send the
message, alongside the conversation. Photos/PDFs use native model inputs; text is labeled untrusted
reference material. Files do not grant appliance-change permission. Do not upload secrets: redaction
is best-effort for model-bound text only, not images, PDFs or locally stored/downloaded originals.

Draft files survive browser reloads and server restarts. A lost upload response is reconciled with a
read-only draft lookup; if still uncertain, manually reselect the same file to retry its upload ID.
**Refresh files** retries draft discovery without sending anything. Message retries use the same
request ID and atomically bind file ownership with the chat/task. Switching chats cannot retarget an
in-flight upload. Reconnecting does not automatically send a draft or renew authorization.

Attachments live under `/data/attachments` and are indexed in SQLite. Only private opaque references
enter stored model checkpoints; native payloads are materialized and integrity-checked immediately
before a provider request. Missing/changed files cause an explicit error, not silent omission.
Sent attachments are retained for history and recovery; they cannot be removed through the draft UI.
There is no automatic expiry/deletion. Archive does not reclaim space. Chat exports include attachment
metadata, not the file bytes. Use a Supervisor app backup to preserve both history and attachments.

## Reliability and recovery

All local appliance tasks execute through one FIFO worker (up to eight active/queued tasks).
Sending/retrying the same request ID does not create a duplicate task. Reconnecting is read-only;
it does not create work or grant changes.

If an earlier browser submission has an uncertain outcome, **Previous submission needs checking**
stays visible above the composer even after the connection recovers. Opening that chat or pressing
**Check submission / Check status** looks up the saved request ID without sending anything. If the
request was accepted, its pending marker clears and any edited draft remains available. The newest
task stays selected; an old deduplication result does not replace it.

A not-found lookup is not proof that a delayed submission cannot still arrive, so it does not silently
discard the request ID or create a new one. **Review & retry original** shows the original text, file
count and permission snapshot. Only explicit **Retry original** confirmation resends that unchanged
request ID/payload, not your edited draft. It returns the original task/deadline if already accepted,
or may start the original work if it never arrived. A stale permission revision or Stop & send target
is still rejected by the server. Keep the draft and retry the status check if the lookup fails.
Do not clear browser storage or delete conversation history as a recovery step.


Model requests use OpenAI Responses background mode, with an early saved response ID.
Recoverable GET/poll failures retry within bounded limits. An ambiguous POST is NOT automatically
resubmitted: explicit Resume may submit it again and incur additional provider cost if no ID was
received. A tracking request ID is not a provider exactly-once guarantee.

A complete response is saved before its local tools run. Each tool records an intent before dispatch
and a result afterward. A new response acknowledges previous results and installs its own pending
tool journal in one database transaction.

If a process dies mid-change, the effect may be unknown. Forge retains completed results, does not
replay the uncertain action, and skips further mutations in that batch until inspection. New user
messages do not automatically execute stale unstarted actions. Legacy dangling tool responses are
acknowledged using explicit unknown-effect outputs instead of silently rewinding conversation history.

Stop prevents future tool dispatch and interrupts provider waits, shell process groups and Supervisor
job polling. An already-dispatched external HA/Supervisor action may finish; it cannot be rolled back.
Other synchronous external actions stop at their next boundary, not necessarily immediately.
Forge asks the provider to cancel known background responses; failed cancellation is reported.

After restart, queued/running work becomes **interrupted**, never automatically replayed.
Click **Resume safely** after reviewing the saved activity. Reauthorize changes only if appropriate.
Updating Forge from its Home Assistant app page can disconnect it: reopen/refresh and resume from the
durable checkpoint. Supervisor forbids an app from updating itself. Use the human-controlled Update
action on Forge's app page; do not retry a rejected self-update through another identity or tool.

## Questions, mid-task replies and API pacing

The runtime-owned `ask_user` tool is a durable barrier. It records the question and tool results together,
skips all unstarted sibling calls even if they precede the question in the model's response, ends the
current task in `waiting_input`, and revokes its grant. It does not occupy the worker while waiting.
Only a new explicit human Send starts the next task; reload, silence and startup do not answer questions.
The model is instructed to use this tool whenever an answer is necessary. Arbitrary prose cannot be
reliably classified as a blocking question, so this is not a guarantee that every badly-worded progress
message will be detected. Progress is not the same as Waiting for you.

**Stop & send** is explicit cancellation plus a saved successor, not live WebSocket steering and not
parallel task execution. The request carries the task ID visible when Send was pressed; a stale tab
cannot stop a newer task. Validation, file binding, deduplication, cancellation and enqueue are one
transaction. Duplicate delivery returns the same task and original deadline. The old grant is revoked
immediately. The next Send uses its own visible checkbox/revision; changing that preference never
extends or cancels the current task. A queued successor cannot execute before the old worker returns.

If the old OpenAI POST loses its response ID, the new message is retained, but execution becomes
interrupted rather than silently resubmitting. Review the activity and use **Resume safely** explicitly;
its warning about possible duplicate provider cost still applies. Restart interrupts both queued and
running tasks, preserves the newest message/files and requires manual recovery. In-flight external
effects cannot be undone; completed and unknown-effect results are never represented as skipped success.

There is no deliberate RPM/TPM sleep on successful OpenAI submissions. The rate-limit option only
bounds retries after actual HTTP429 (not insufficient quota); Retry-After/reset headers remain honored.
The 1.5-second loop retrieves background response status, not a pre-request throttle. Polling, bounded
recovery and one appliance worker are kept for reliability even when the provider's limits are higher.

After a human-controlled Supervisor update, check Waiting for you, an ordinary reply, and Stop & send
on a harmless read-only task. See [Testing](TESTING.md) for the release checks and their limits.
Do not create a real permission preference or appliance action merely for an automated test.
Versions before 0.3.2 do not understand the waiting state; versions before 0.4.0 do not honor per-task
model snapshots. Preserve DB/WAL/SHM and attachments. Recover checkpoints on a version that understands
them rather than deleting history or replaying pending actions on an older runtime.

## Persistence and rollback

Supervisor backups use **cold** mode: Forge is stopped briefly so its SQLite files form a consistent
backup, then restarted. Active tasks become interrupted and require manual Resume. A health endpoint
is configured for the Supervisor watchdog; it checks HTTP availability and worker liveness.

Runtime database: `/data/forge.sqlite3`, SQLite WAL with FULL synchronization.
The remembered Allow changes preference is additive conversation metadata (old chats default off).
It is not copied from historical tasks, and 0.3.0 rollback ignores it while preserving the data.
Messages, tasks and incremental bounded event previews are separate records; full history is not
rewritten on every event or returned in every poll. History has no automatic deletion policy.

Migration validates the old JSON state, retains `/data/forge_state.json`, and creates the private
`/data/forge_state.json.pre-v0.2.0` backup. A corrupt legacy file or database fails startup rather than
silently starting an empty chat store. Migration counts/checksum are stored in the database metadata.
The original JSON becomes a migration/rollback snapshot, not a continuously updated mirror.

Take a Supervisor app backup before downgrading. Version 0.1 cannot read new SQLite history; a source-only
downgrade displays the pre-upgrade JSON snapshot, not messages created in 0.2 or later. Preserve the
SQLite DB, WAL and SHM together, or use SQLite's backup API while running. Do not delete them to fix startup.
Version 0.2 does not understand attachment-bearing messages/checkpoints: do not resume those tasks on
0.2. Preserve `/data/attachments` alongside the database, including when restoring or rolling back.

App options/API key are not changed by migration. File edits continue to create private backups
under `/data/file_backups` when backup-on-write is enabled. Batch writes are prevalidated and each
file is atomic, but an OS/disk failure can still leave a partially written multi-file batch, which is
reported with its completed and remaining files.

## Access and privacy

Only the Home Assistant Ingress TCP peer is allowed to reach the UI/API. Forwarded IP headers cannot
grant access. The public app has no direct LAN port. Mutation endpoints require a per-process CSRF token.
A minimal `/health` endpoint is available to local/Supervisor health checks without exposing history.
Ordinary successful poll requests do not flood logs; errors and lifecycle changes remain visible.

Diagnostics omit Supervisor app options and redact known credentials/common secret keys before model
output, event storage and browser display. Redaction is best-effort: do not put secrets in chat.
Chat exports, backups and source/configuration can still contain private information.
No API key is embedded in the frontend or stored in browser localStorage.

OpenAI tool requests and their outputs leave the appliance. Existing `store: true` behavior is retained;
background mode uses provider-side storage and is not appropriate for a zero-data-retention requirement.
The normal household assistant gains no additional permissions.

## Development / verification

Runtime modules:
- `forge.py`: existing appliance tool schemas/adapters and options
- `forge_safety.py`: canonical path checks, redaction, bounded serialization
- `forge_store.py`: SQLite migration, history and execution journal
- `forge_provider.py`: bounded Responses transport/polling/cancellation and read-only model discovery
- `forge_models.py`: bounded account catalog, reviewed compatibility and immutable task-setting validation
- `forge_attachments.py`: bounded private attachment storage, validation and model-input materialization
- `forge_ssh.py`: persistent account/repository identities, admin access/host pins and bounded SSH transport
- `forge_git.py`: structured local development, explicit fetch/push and durable mutation receipts
- `forge_runtime.py`: serialized engine, authorization and secure ingress HTTP
- `static/`: dependency-free UI; no runtime CDN requests
- `tests/`: isolated regression tests using the standard library plus Pillow image fixtures

With Python, Pillow, Git and OpenSSH clients installed, run `python3 -m unittest discover -s tests -v`
from the source directory. Tests use temporary storage and mock providers, not the appliance or a
real OpenAI key. The image installs these dependencies and runs the suite before deployment. SSH
client checks use temporary credentials and no network authentication. Run `node tests/test_connections.js`
for the separate mocked DOM/API component checks; see TESTING.md for their limits.
Python tests cover temporary-store persistence, task authorization, model settings and guarded
loopback HTTP. Browser fixtures and syntax checks cannot establish live HA Ingress, production-storage
or real-provider behavior. Those still require verification after the human-controlled update.
Do not mark a release stable based only on mocked tests; see [Testing](TESTING.md).

Protocol references:
- Home Assistant Ingress: https://developers.home-assistant.io/docs/apps/presentation/#ingress
- OpenAI background mode: https://developers.openai.com/api/docs/guides/background
- OpenAI image inputs: https://developers.openai.com/api/docs/guides/images-vision
- OpenAI file inputs: https://developers.openai.com/api/docs/guides/pdf-files
- OpenAI container files: https://developers.openai.com/api/reference/cli/resources/containers

Physical appliance behavior and real-provider behavior must be checked separately from mocked tests.
