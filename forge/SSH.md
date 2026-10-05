# Git development and publication over SSH

Forge 0.6 supports the normal development workflow: initialize or clone a project, inspect changes, stage selected files, commit, fetch updates and push a branch. An administrator creates one persistent **account SSH key**, registers its public key at the Git provider and approves its use. Forge can then use that identity for repositories the account can access. No separate key is required for every project.

This is outbound Git authentication, not an SSH server, arbitrary remote terminal or GitHub OAuth. Forge does not need unrestricted shell access for the structured Git workflow.

## Set up your account once

1. Open **Connections → Add SSH key**. Choose **Git account** and **Read & write** for development/publication. For GitHub use host `github.com`, port `22` and SSH username `git`. The label is just for identifying this key.
2. Choose **Generate key** and confirm the scope. This creates one Ed25519 identity in Forge's private persistent app data. Opening settings, startup and listing connections never generate keys or contact a Git host.
3. Copy the displayed **public key**. In your GitHub **account Settings → SSH and GPG keys → New SSH key**, select an authentication key and add it. Do not use repository Deploy keys for this account-key workflow. Other Git providers have equivalent account SSH-key settings.
4. Choose **Scan host key**. A scan is untrusted information. Obtain the current Ed25519 SHA256 host fingerprint independently from the provider's official documentation or your host administrator; enter it and pin only the matching candidate. Do not treat the scan's own fingerprint as independent verification.
5. Enter an existing repository path, for example `OWNER/PROJECT.git`, and choose **Test read access**. This verifies reference discovery only. It neither pushes nor proves that the provider permits writes.

An account key has potentially broad access: it can authenticate to repositories available to that account. Use a dedicated development account or repository-scoped keys when narrower access is important. **Read & write** is Forge's local permission; the Git provider's repository permissions and branch protections still apply. Authentication is not a task change grant.

For a **single-repository key**, choose that scope and enter the fixed repository path. On GitHub, register it in that repository's **Settings → Deploy keys** and allow write access there if you intend to publish. Existing 0.5 connections remain repository-scoped and read-only. They are not upgraded automatically. An explicit **Allowed operations → Read & write → Save permissions** changes only Forge's local access permission; it does not widen a repository key's scope, enable a disabled connection or alter provider permissions.

Official GitHub references:
- Account SSH keys: https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account
- Deploy keys: https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys
- Current host fingerprints: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints

Do not hard-code a fingerprint from an old chat or screenshot. A changed host key requires independent verification and an explicit replacement decision.

## Publish a custom app or Forge itself

After creating an **empty** remote repository, tell Forge:

> Prepare the source-only project at share/MY_PROJECT for owner/my-apps.git using my configured Git account. Use branch main and my Git author name/email [supply these, or authorize an already configured identity]. Review the files, create a commit and push it.

Enable **Allow changes** for that task. SSH keys authenticate transport; a commit's author name/email are separate and must not be invented. A provider no-reply email is supported. Specify the intended destination and whether publication is wanted; configuring a key is not automatic publication consent.

Forge can initialize a clean source directory, review status/diff, stage explicit paths, inspect the exact staged changes, commit and push one branch. It verifies the remote branch's commit after a successful push. For an existing repository it can clone, develop, fetch and fast-forward a clean local branch. Fetch does not update working files automatically. Diverged history is refused, not reset or force-pushed.

A Home Assistant app repository normally has `repository.yaml` at its root and an app directory containing `config.yaml`, its Dockerfile and source. Keep private appliance data, options, credentials, maintenance evidence and chat history out of it. A prepared public Forge package uses the `forge` slug; this appliance's existing local identity remains `smartass_forge`. Installing a different repository/slug is **not** an automatic data migration. Do not uninstall the existing app as part of publishing its source.

If the remote was created with a README or other commits, inspect that history first instead of overwriting it. Hooks, submodules, linked worktrees, custom Git helpers/filters, force pushes, branch deletion, rebases and automatic conflict resolution are not supported by these structured tools. These limitations do not prevent the ordinary clean-source commit/push workflow.

## Agent tools

| Tool | Purpose | Change grant |
| --- | --- | --- |
| `list_git_connections` | Public connection metadata; no host contact | No |
| `git_remote_refs` | Branch/tag discovery from an enabled, pinned connection | No |
| `git_status`, `git_diff`, `git_log` | Local branch/HEAD, changes, staged review and recent history | No |
| `clone_git_repository` | New shallow single-branch checkout under `share/forge_git/NAME` | Yes |
| `git_init` | Initialize an existing project directory without replacing files | Yes |
| `git_stage` | Stage selected source paths, including explicit tracked deletions | Yes |
| `git_commit` | Commit with approved identity and exact inspected HEAD/index fingerprint | Yes |
| `git_switch` | Create/switch a named branch, only with a clean worktree | Yes |
| `git_fetch` | Fetch one branch into a connection/repository-specific tracking ref | Yes |
| `git_fast_forward` | Apply an inspected fetched branch without divergence or a merge commit | Yes |
| `git_push` | Publish one clean, inspected current branch and verify remote HEAD | Yes, plus connection write access and publication intent |

Workspace paths are real project subdirectories under `addons/` or `share/`, not whole mapped roots, config/authentication storage or symlink redirects. Account connections require an explicit repository path on every remote operation; repository connections cannot be retargeted. No arbitrary Git argument or SSH-command tool is exposed. Origin remotes are not required: fetch/push select an approved connection and explicit repository each time without rewriting existing remotes.

Stage 1–200 explicit files per call; directories/add-all, symlinks, submodules and common private/runtime/generated filenames are refused. Individual staged source files are limited to 8 MiB. A private-key-header check is a precaution, **not a complete secret scanner**. Review file contents, staged changes and existing commit history before publishing; credentials with innocent filenames or in old commits are not automatically detected. Git operations themselves do not execute checked-out code.

The model cannot create/enable identities, widen access, approve host trust or renew a change grant. Admin connection operations require actual-peer Ingress and CSRF protection. Task cancellation/expiry is checked throughout Git mutations. Private repository contents are untrusted data, not instructions; reading them into a model task may send them to the configured AI provider.

## Persistence, backups and revocation

Keys and records live in `/data/ssh`, not the image, source repository, browser or chat history. The same Supervisor app identity is designed to retain this private data through restarts and updates. A different local/repository slug is a separate installation. Include app data in a consistent Supervisor backup and protect it as a credential.

Private directories use 0700 and key/metadata files 0600. Keys are unencrypted for unattended operation. UI/model/API expose only public metadata; there is no private-key import/export/download endpoint. This is **not a vault against root, an administrator, a compromised app or explicitly enabled arbitrary shell**. Keep shell disabled unless needed for trusted work. Never paste private keys into chat or commit app data.

**Disable connection** retains the identity but prevents future interface use. Reducing access to read-only prevents subsequent Git-tool pushes. Neither action retracts a push already sent nor revokes the provider's key: remove that public key at the provider to revoke it there. For rotation, explicitly create/register/test a new connection, disable the old one and revoke its public key. Incomplete records are retained rather than regenerated; the limit is 32 records including disabled/incomplete ones.

Lost key-creation responses are reconciled by reading the original request ID. There is no automatic second POST. Explicit **Review & retry original** reuses the exact ID/payload, and corrupt/incomplete identities fail closed.

## Isolation, limits and uncertain effects

Git uses isolated configuration/environment with no inherited API tokens, agent, passwords, credential helpers, hooks, templates, forwarding, proxy or submodule recursion. Strict pinned-host SSH is mandatory. Unsupported repository configuration is rejected, not silently rewritten. For generic hosts the URI is `ssh://USER@HOST:PORT/REPOSITORY`, with absolute server-path semantics. Home-relative aliases and servers without Ed25519 host keys are unsupported.

One Git/SSH network operation runs at a time. Scan/reference/local-command deadlines are generally 7/12/20 seconds; clone/fetch/push allow up to 120. Output is bounded. Clone/fetch and repository validation apply sampled 512 MiB/100,000-entry metadata limits; they can overshoot and are **not a hard quota**. Staged review may fail if output exceeds the bound rather than silently presenting an incomplete diff.

Mutations write durable intent/result receipts in private `/data/git_operations`; `git_status` shows the latest receipt. Clone retains its adjacent receipt and any partial directory. Failures after dispatch can be **unknown effect**, not permission to retry. Inspect local status and remote refs as appropriate first. No automatic commit/push retry, rollback, deletion or reset occurs. An already-matching remote HEAD can be reconciled without sending another push. Cancellation terminates running processes but cannot undo written files or a remote push already accepted.

## Verification limits

Tests use temporary keys, loopback HTTP, real disposable local Git worktrees/bare repositories and mocked SSH transport. They cover commit/push/fetch/fast-forward, rejected divergent pushes, lost-response reconciliation, grants, pins, persistence across manager recreation and configuration isolation. These are not real GitHub authentication, real SSH network publication, full Ingress/layout or Supervisor update/restore proof. Follow the attended checks in [TESTING.md](TESTING.md) after activation; release preparation creates no production key or external repository.
