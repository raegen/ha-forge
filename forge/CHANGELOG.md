# Changelog

## 0.6.0 — 2026-10-04

- Add persistent account-scoped SSH keys for the development/publication workflow. Register one public key with a Git account and select explicit repositories per operation; retain repository-scoped keys and all legacy read-only permissions.
- Add structured init/status/diff/log/stage/commit/branch/fetch/fast-forward/push tools for source projects under addons/share. Review exact staged index and HEAD, supply the approved author identity and publish one explicitly chosen branch. No unrestricted shell is needed.
- Require a current task grant for every Git mutation and administrator-approved connection write access for push. Refuse force pushes, branch deletion, dirty/diverged updates, redirected metadata and executable Git configuration. Settings cannot grant or extend a task.
- Retain durable mutation receipts, recheck grants during operations and reconcile remote HEAD. Mark ambiguous dispatched effects; skip later sibling mutations until inspected, with no automatic retry or destructive cleanup.
- Extend Connections with account/repository scope, explicit read/write permission controls and account-key registration guidance. Preserve existing identities, host pins, history, options, model controls and local app identity.
- Add actual disposable local Git workflow tests, account/legacy/HTTP/adapter/grant regressions and updated mock UI checks. External SSH/GitHub publication, Supervisor update persistence and full Ingress/layout remain attended release gates.

## 0.5.0 — 2026-10-03

- Add Connections settings for explicitly generated, persistent per-repository Ed25519 keys. Display/copy public keys only; no private-key export, startup generation or automatic replacement.
- Require independently verified, revision-checked host-key pins. Provide bounded read-access tests, connection enable/disable and uncertain-creation reconciliation without automatic POST retries.
- Add read-only connection/reference tools and a fresh-grant-controlled shallow clone tool with a fixed workspace, retained receipts, cancellation and safety bounds. No pushes, arbitrary SSH commands or OAuth.
- Keep credentials in private persistent app data; install Git/OpenSSH clients without enabling shell or sensitive-file access. Preserve the existing app identity, history, attachments, options and permissions.
- Add SSH storage, transport, real offline client interoperability, HTTP/authorization and mock UI-component regressions, plus setup/backup/revocation documentation. Real account authentication, container replacement and attended Ingress/layout validation remain separate release gates.

## 0.4.0 — 2026-10-03

- Rebrand the development workspace to Forge, with an anvil identity and responsive charcoal/copper UI.
- Discover account models server-side from OpenAI; offer compatible model and named reasoning-effort selects. No browser API keys or discovery inference requests.
- Save immutable model/effort snapshots per task. Running work, Resume and uncertain retries retain their original settings; supported capability differences are applied to requests.
- Keep the configured default as an explicit discovery-outage fallback; report unconfirmed availability and unsupported effort instead of silently changing a selection.
- Add isolated discovery, validation, persistence, transport, HTTP and UI regression coverage. Preserve permissions, execution barriers, journals, history, attachments and options.
- Replace appliance-specific first-run context with a generic seed; preserve an existing legacy context in place.
- Add repository preparation, installation/security/model/testing documentation, original artwork and build-context exclusions. Keep the app experimental pending attended deployment checks.
- Retain this installation's legacy app identity. A separately packaged public-repository app uses the Forge slug; it is not an automatic data migration.

## 0.3.3 — 2026-10-01

- Fix browser drafts trapped behind an uncertain previous submission after editing or switching chats.
- Keep dedicated recovery controls visible independently of transient connection banners; check saved request IDs on conversation selection and Send while pending.
- Preserve edited drafts and newer task displays. An explicit review/confirmation can retry only the frozen original payload and request ID.
- No automatic retries, history/data deletion, task-grant extension, backend schema changes or self-update workaround.

## 0.3.2 — 2026-09-22

- Explicit ask_user execution barrier, durable Waiting for you state and grant revocation until a new human reply.
- Stop & send atomically saves an update, cancels old future actions and queues a serialized successor; no simultaneous appliance workers.
- Completed/uncertain results, attachments, immutable request IDs and separate per-Send permissions survive interruptions. Unknown provider submissions still require explicit Resume.
- Allow changes can be selected for the next Send while work is running; it never changes the old task grant.
- No successful-request OpenAI throttle exists or was added. Bounded 429 backoff, response polling and ambiguity protection retained.
- Source update requires the human Supervisor Update action; no self-update workaround.

## 0.3.1 — 2026-09-22

- Remember Allow changes per conversation until unchecked, across prompts, reloads, devices and restarts.
- Persist the human preference separately from expiring task grants; new/legacy conversations default off.
- Revision-checked saves/submissions prevent stale tabs from overriding a later uncheck.
- No automatic submission, grant renewal or mutation retry; accepted request retries retain their deadline.
- Resume retains its separate unchecked authorization; cancellation, restart recovery and task expiry are unchanged.
- Isolated preference, authorization, HTTP and UI regression coverage.

## 0.3.0 — 2026-09-17

- Incoming photo, PDF and UTF-8 text/code attachments, including attachment-only messages.
- File picker, drag/drop, clipboard images, upload progress, image previews and persistent per-chat drafts.
- Bounded private storage with configurable quota; normalized still images strip EXIF/GPS metadata.
- Idempotent uploads and atomic message binding; reconnect reconciles rather than silently resending.
- Native model image/PDF inputs and explicitly untrusted text inputs; no automatic change authorization.
- Durable attachment references and steering recovery, without storing base64 payloads in chat/checkpoints.
- Strict Ingress/CSRF, upload framing/deadlines, integrity and quota checks; no uploaded-file execution.
- Pillow image dependency, 34 new isolated backend tests and a separately recorded Chromium UI test pass.

## 0.2.0 — 2026-09-13

- Rebuilt responsive, accessible chat/activity UI with safe Markdown, drafts, cancellation, resume,
  archive/restore, exports, source links, themes and cited provider-artifact downloads.
- Transactional SQLite/WAL persistence with validated, non-destructive JSON migration.
- Serialized task queue, request idempotency and incremental event polling.
- Early background response checkpoints and recovery that never silently repeats uncertain mutations.
- Manual expiring per-task change authorization; no automatic replay after restart.
- Ingress peer enforcement, CSRF, bounded HTTP parsing, safe file canonicalization and diagnostic redaction.
- Bounded/cancellable shell groups and Supervisor job polling, corrected log endpoints and safer batch writes.
- Isolated storage, recovery, authorization, transport, HTTP/security and tool regression tests.

## 0.1.3

Previous checkpointed JSON/runtime implementation, retained as the migration/rollback baseline.
