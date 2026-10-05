# Testing Forge

## Isolated backend regressions

From the app directory, with Python 3.12+, Pillow, Git and OpenSSH clients (`ssh`, `ssh-keygen`, `ssh-keyscan`) installed:

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary directories/databases, local subprocesses, mocked providers/HA/Supervisor APIs and loopback HTTP servers. No API key, production database, provider account or appliance hardware is needed. SSH interoperability tests generate disposable keys, parse `ssh -G` locally and use a failing local transport rather than a Git host. Git workspace tests use real disposable worktrees and bare local repositories with a test-only loopback transport. They do not read production credentials or contact an external Git server. Missing-client tests skip; a skip is not a pass. The Docker build installs the clients and runs the suite.

Coverage includes model inventory/errors/caching, effort profiles, immutable task snapshots, retry/resume/handoff behavior, permission deadlines, stale tabs, execution barriers, uncertain effects, file guards, attachments and Ingress peer/CSRF enforcement. SSH coverage includes idempotent key creation, persistence, public-only views, unsafe metadata refusal, strict host pins, clean client environments, account/repository scope and legacy read-only compatibility. Git tests cover init/status/diff/log/stage/commit/switch/fetch/fast-forward/push, exact index/HEAD review, approved authors, read-only/expired/revoked permissions, host/connection refusal, normal non-fast-forward rejection, lost push responses, receipts, selected deletions, literal filenames, blocked configuration/hook execution, cancellation and bounded processes. Nothing in the suite requires enabling the live appliance's shell or changing its task grants.

## Browser and source checks

```sh
for script in static/*.js; do node --check "$script"; done
node tests/test_connections.js
```

The UI is dependency-free. Review desktop/mobile layouts, both themes, keyboard focus, dialog scrolling, long repository labels and unavailable services. Component checks with mocked APIs/DOM objects are not browser layout tests; a browser fixture is not a real Home Assistant Ingress test.

## Attended activation checklist

Take a Supervisor app backup. Updating Forge may interrupt its current task; use the human-controlled Update button, never a self-update workaround.

1. Confirm the running version is 0.6.0 and reopen the UI through Home Assistant Ingress. A source version/update offer alone does not prove activation.
2. Confirm conversations, history, drafts, sent attachments, options and Allow changes preferences are retained. Do not toggle production preferences merely to test them.
3. Refresh **Model & effort**. Confirm account-specific compatible IDs, supported effort levels and clear errors. Refresh must not create a task.
4. Choose a supported effort and send a harmless read-only request. Confirm its saved task label and response use that choice; next-draft edits must not change running work.
5. Check a human-answer barrier, Stop & send, unresolved-send recovery and explicit Resume with harmless fixtures. Original snapshots and deadlines must remain intact.
6. Open **Connections**. Confirm no automatic key generation or host contact. Inspect any existing 0.5 identities: they must retain their public key, fixed repository and read-only access unless the human explicitly changed access.
7. With explicit consent and a chosen test account/repository, follow [SSH.md](SSH.md): generate/register an account public key, independently verify the host fingerprint and test read access. Treat an account key's broad scope deliberately. A read test is not write-access proof.
8. Use a disposable source-only directory and remote repository. Verify a read-only task cannot init/stage/commit/fetch/push. Then grant a fresh task, approve the author name/email and destination, make a reviewed commit, push and verify the remote branch. Test fetch/fast-forward and an expected non-fast-forward refusal only with disposable branches. Do not use production history for destructive tests.
9. Close/reopen Connections and, in an attended window, restart normally. Confirm the same public key/fingerprint remains. Test an in-place update and backup/restore separately with consent; manager-recreation unit tests do not replace them.
10. On a disposable connection, verify read-only access prevents pushes and disabling prevents future remote use without deleting the key. Neither setting revokes the provider key or undoes an in-flight push. Do not interrupt a real ongoing task for a test.
11. Inspect retained operation receipts after a fixture cancellation/lost response before any retry. Use fixtures rather than intentionally duplicating real credentials or interrupting a production publication.
12. Check keyboard navigation, scope-dependent fields, access-change confirmation and a narrow mobile viewport; no inaccessible controls or horizontal overflow. Inspect logs for accidental private keys/credentials.

Readiness, unit tests and local Git tests are not external SSH authentication, physical appliance or end-to-end provider proof.

## Rollback

Keep a consistent cold backup of DB/WAL/SHM, attachments, options, `/data/ssh` and `/data/git_operations`. Protect it as a credential-bearing backup. Do not resume a 0.4 model-snapshot task, 0.5 SSH task or 0.6 Git mutation on an older runtime that lacks those interfaces. Downgrading source is not a data rollback; deleting keys/history/receipts is not recovery. Prefer a consistent app-backup restore with human authorization. A new repository slug needs a separately authorized migration.
