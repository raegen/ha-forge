"""Structured Git development tools; private SSH identity selection stays admin-owned.

No shell interface, repository hooks, config includes/helpers, forced pushes,
branch deletion, automatic pull/merge or implicit credential selection.
"""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
import uuid

from forge_provider import Cancelled, Interrupted
from forge_store import Conflict
from forge_ssh import SSHConnections, _atomic, _private_directory, _file

OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
MAX_INDEX = 32 * 1024 * 1024


class GitEffectUnknown(RuntimeError):
    """A mutation was dispatched; retained state must be inspected before retry."""


def branch_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", value)
            or any(x in value for x in ("..", "//", "/."))
            or any(p.endswith((".lock", ".")) for p in value.split("/")) or value.endswith("/")):
        raise ValueError("Invalid branch name")
    return value


def identity(name, email):
    if (not isinstance(name, str) or not name.strip() or len(name) > 120
            or re.search(r"[<>\x00-\x1f\x7f]", name)):
        raise ValueError("Supply the human-approved Git author name")
    if (not isinstance(email, str) or len(email) > 254
            or not re.fullmatch(r"[^\s<>\x00-\x1f\x7f@]+@[^\s<>\x00-\x1f\x7f@]+", email)):
        raise ValueError("Supply the human-approved Git author email (a provider no-reply address is supported)")
    return name.strip(), email


def safe_file(value):
    if (not isinstance(value, str) or not value or len(value.encode()) > 512
            or value.startswith(("/", "-")) or "\\" in value
            or re.search(r"[\x00-\x1f\x7f]", value)
            or any(x in ("", ".", "..") for x in value.split("/"))):
        raise ValueError("Stage explicit relative file paths, not directories, globs or pathspec expressions")
    parts = value.lower().split("/")
    blocked = {".git", ".ssh", ".storage", "__pycache__", "node_modules", ".venv", "venv",
               "options.json", "secrets.yaml", "secrets.yml", "id_ed25519", "id_rsa", "id_ecdsa",
               ".gitmodules", ".gitconfig", ".git-credentials", ".netrc"}
    if (any(p in blocked for p in parts) or any(p == ".env" or
            (p.startswith(".env.") and p not in (".env.example", ".env.sample", ".env.template")) for p in parts)
            or parts[-1].endswith((".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", "-wal", "-shm", ".log", ".bak", ".key", ".p12", ".pfx"))):
        raise PermissionError("Refusing a sensitive/runtime/generated file; prepare a clean source-only repository")
    return value


class GitWorkspace:
    def __init__(self, connections, receipts):
        self.connections = connections
        self.receipts = Path(receipts)
        self.lock = threading.RLock()

    @staticmethod
    def _env(home):
        env = SSHConnections._env(home)
        env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
                   GIT_CEILING_DIRECTORIES=str(home), GIT_LITERAL_PATHSPECS="1", GIT_ATTR_NOSYSTEM="1", GIT_EDITOR="/bin/false",
                   GIT_SEQUENCE_EDITOR="/bin/false", GIT_MERGE_AUTOEDIT="no")
        return env

    def _base(self, path):
        args = [self.connections._binary("git"), "--no-pager"]
        for setting in ("core.hooksPath=/dev/null", "core.fsmonitor=false", "core.pager=cat",
                        "core.attributesFile=/dev/null", "core.excludesFile=/dev/null",
                        "credential.helper=", "protocol.allow=never", "protocol.ssh.allow=always",
                        "init.templateDir=", "submodule.recurse=false", "gc.auto=0",
                        "maintenance.auto=false", "commit.gpgSign=false", "tag.gpgSign=false",
                        "push.gpgSign=false", "push.followTags=false", "push.recurseSubmodules=no",
                        "fetch.recurseSubmodules=false", "core.quotePath=true", "color.ui=false"):
            args += ["-c", setting]
        if path is not None:
            args += ["--git-dir=" + str(path / ".git"), "--work-tree=" + str(path)]
        return args

    def _run(self, path, args, home, check, *, env=None, timeout=20, allow_error=False, budget=False):
        check()
        code, text, err = self.connections._run(self._base(path) + args, home, env or self._env(home),
                                               timeout, check, path / ".git" if budget and path else None)
        if code and not allow_error:
            # Bounded output is untrusted data; do not return server banners, URLs or secrets in errors.
            raise ValueError("Git rejected the operation; inspect status/diff/refs before proceeding (exit " + str(code) + ")")
        return code, text, err

    def _validate(self, path, home, check):
        if not path.is_absolute() or not path.is_dir() or path.is_symlink():
            raise PermissionError("Expected a real, mapped project directory")
        gd = path / ".git"
        if not gd.is_dir() or gd.is_symlink():
            raise PermissionError("Expected a .git directory; linked worktrees, submodules and redirected repositories are unsupported")
        count = size = 0
        for root, dirs, files in os.walk(gd, followlinks=False):
            check()
            for name in dirs + files:
                p = Path(root) / name; info = p.lstat(); count += 1; size += info.st_size
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise PermissionError("Git metadata must not contain symlinks or special files")
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise PermissionError("Shared/hard-linked Git metadata is unsupported")
            if count > 100000 or size > 512 * 1024 * 1024:
                raise OverflowError("Git metadata exceeds the 512 MiB / 100,000-entry safety bound")
        for forbidden in ("commondir", "config.worktree", "objects/info/alternates", "objects/info/http-alternates", "info/grafts", "modules", "worktrees"):
            if (gd / forbidden).exists():
                raise PermissionError("Redirected, partial or shared Git metadata is unsupported: " + forbidden)
        config = gd / "config"
        if not config.is_file() or config.stat().st_size > 65536:
            raise PermissionError("Missing or oversized Git configuration")
        # Parse ONLY this file, outside the repository. --no-includes never opens an include.
        _, data, _ = self._run(None, ["config", "--file", str(config), "--no-includes", "--null", "--list"], home, check)
        exact = {"core.repositoryformatversion", "core.filemode", "core.bare", "core.logallrefupdates",
                 "core.ignorecase", "core.precomposeunicode", "core.symlinks", "core.autocrlf",
                 "core.eol", "user.name", "user.email", "init.defaultbranch", "extensions.objectformat"}
        for item in data.split("\0"):
            if not item: continue
            key, _, value = item.partition("\n"); key = key.lower()
            # URI-shaped remote names could shadow an explicit SSH destination; allow ordinary names only.
            if key not in exact and not re.fullmatch(r"(?:remote\.[a-z0-9][a-z0-9._-]{0,63}\.(?:url|fetch)|branch\.[^\x00-\x20]+\.(?:remote|merge))", key):
                raise PermissionError("Unsupported repository configuration (no hooks/includes/helpers/filters/rewrites): " + key)
            if key == "core.bare" and value.lower() != "false": raise PermissionError("Bare repositories are unsupported workspaces")
            if key == "extensions.objectformat" and value not in ("sha1", "sha256"): raise PermissionError("Unsupported object format")
        return gd

    @contextmanager
    def _session(self, path, check, validate=True):
        with self.lock, tempfile.TemporaryDirectory(prefix="forge-worktree-") as tmp:
            home = Path(tmp)
            check()
            if validate: self._validate(path, home, check)
            yield home

    def _head(self, path, home, check, ref="HEAD"):
        code, text, _ = self._run(path, ["rev-parse", "--verify", "--end-of-options", ref], home, check, allow_error=True)
        value = text.strip()
        if code: return None
        if not OID.fullmatch(value): raise ValueError("Git returned an invalid object ID")
        return value

    def _branch(self, path, home, check):
        code, text, _ = self._run(path, ["symbolic-ref", "--quiet", "--short", "HEAD"], home, check, allow_error=True)
        return None if code else text.strip()

    @staticmethod
    def _index(path):
        index = path / ".git/index"
        if not index.exists(): return None
        if index.stat().st_size > MAX_INDEX: raise OverflowError("Git index exceeds 32 MiB")
        return hashlib.sha256(index.read_bytes()).hexdigest()

    def _status(self, path, home, check):
        _, text, _ = self._run(path, ["status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=all"], home, check)
        return {"head": self._head(path, home, check), "branch": self._branch(path, home, check),
                "index_sha256": self._index(path), "porcelain": text, "clean": not bool(text)}

    def _receipt_dir(self, path):
        return self.receipts / hashlib.sha256(str(path).encode()).hexdigest()

    def _last(self, path):
        root = _private_directory(self._receipt_dir(path))
        p = root / "latest.json"
        if not p.exists(): return None
        _file(p)
        if p.stat().st_size > 65536: raise Conflict("Oversized Git operation receipt")
        return json.loads(p.read_text())

    @contextmanager
    def _mutation(self, path, kind, details, check):
        check()
        root = _private_directory(self._receipt_dir(path), create=True)
        receipt = {"id": uuid.uuid4().hex, "operation": kind, "state": "started", "started_at": time.time(), **details}
        def save():
            data = json.dumps(receipt, sort_keys=True) + "\n"
            _atomic(root / (receipt["id"] + ".json"), data)
            _atomic(root / "latest.json", data)
        save()
        try:
            check()
            yield receipt
            receipt.update(state="complete", completed_at=time.time()); save()
        except BaseException as exc:
            receipt.update(state="unknown_effect", error_type=type(exc).__name__, ended_at=time.time())
            try: save()
            except Exception: pass  # Initial intent remains durable; never replace the original error.
            if isinstance(exc, (Cancelled, Interrupted, KeyboardInterrupt, SystemExit)): raise
            raise GitEffectUnknown("Git " + kind + " did not finish cleanly. Inspect git_status and remote refs when relevant before any retry. Receipt " + receipt["id"] + ". " + str(exc)) from exc

    def status(self, path, check=lambda: None):
        with self._session(path, check) as home:
            return {**self._status(path, home, check), "last_operation": self._last(path)}

    def diff(self, path, staged=False, check=lambda: None):
        if not isinstance(staged, bool): raise ValueError("staged must be a boolean")
        with self._session(path, check) as home:
            args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-renames"]
            if staged: args.append("--cached")
            _, text, _ = self._run(path, args + ["--"], home, check)
            return {"staged": staged, "diff": text, "index_sha256": self._index(path), "head": self._head(path, home, check)}

    def log(self, path, limit=10, check=lambda: None):
        if type(limit) is not int or not 1 <= limit <= 50: raise ValueError("limit must be 1–50")
        with self._session(path, check) as home:
            if self._head(path, home, check) is None: return {"log": "", "unborn": True}
            _, text, _ = self._run(path, ["log", "-" + str(limit), "--no-show-signature", "--format=%H %aI %an <%ae>%n%s", "HEAD", "--"], home, check)
            return {"log": text}

    def init(self, path, branch="main", check=lambda: None):
        branch_name(branch)
        with self._session(path, check, validate=False) as home:
            if not path.is_dir() or path.is_symlink(): raise PermissionError("Create a real mapped project directory first")
            if (path / ".git").exists() or (path / ".git").is_symlink():
                raise Conflict("Git metadata already exists; inspect git_status instead of initializing again")
            with self._mutation(path, "init", {"branch": branch}, check):
                self._run(None, ["init", "--template=", "--initial-branch=" + branch, "--", str(path)], home, check)
                self._validate(path, home, check)
                result = self._status(path, home, check)
            return result

    @staticmethod
    def _work_file(path, name):
        file = path / name
        for part in (file, *file.parents):
            if part == path: break
            if part.is_symlink(): raise PermissionError("Symlinks are not supported by staging")
        if file.exists():
            info = file.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise PermissionError("Stage individual regular files only")
            if info.st_size > 8 * 1024 * 1024: raise OverflowError("Individual source files must not exceed 8 MiB")
            data = file.read_bytes()
            if re.search(rb"(?m)^-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----\r?$", data):
                raise PermissionError("A private-key header was detected; the file was not staged")

    def stage(self, path, paths, check=lambda: None):
        if not isinstance(paths, list) or not 1 <= len(paths) <= 200: raise ValueError("Supply 1–200 explicit file paths")
        names = list(dict.fromkeys(safe_file(x) for x in paths))
        with self._session(path, check) as home:
            for name in names:
                self._work_file(path, name)
                if not (path / name).exists():
                    code, _, _ = self._run(path, ["ls-files", "--error-unmatch", "--", name], home, check, allow_error=True)
                    if code: raise ValueError("A selected file is missing and not tracked")
            with self._mutation(path, "stage", {"files": names}, check):
                self._run(path, ["add", "--"] + names, home, check)
                result = self._status(path, home, check)
            return result

    def _check_index(self, path, home, check):
        _, listing, _ = self._run(path, ["ls-files", "--stage", "-z"], home, check)
        for record in listing.split("\0"):
            if not record: continue
            meta, name = record.split("\t", 1); mode, oid, stage = meta.split()
            safe_file(name)
            if mode not in ("100644", "100755") or stage != "0":
                raise PermissionError("Only resolved regular source files can be committed (no symlinks/submodules)")

    def commit(self, path, message, author_name, author_email, expected_head, expected_index_sha256, check=lambda: None):
        author_name, author_email = identity(author_name, author_email)
        if not isinstance(message, str) or not message.strip() or len(message.encode()) > 8000 or "\0" in message:
            raise ValueError("Commit message must be 1–8000 UTF-8 bytes without NUL")
        if expected_head is not None and (not isinstance(expected_head, str) or not OID.fullmatch(expected_head)):
            raise ValueError("Use the exact HEAD from git_status, or null for the first commit")
        if not isinstance(expected_index_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_index_sha256):
            raise ValueError("Review git_diff(staged=true), then supply its exact index_sha256")
        with self._session(path, check) as home:
            if self._head(path, home, check) != expected_head or self._index(path) != expected_index_sha256:
                raise Conflict("HEAD or staged files changed; inspect status and staged diff again")
            self._check_index(path, home, check)
            if self._branch(path, home, check) is None: raise Conflict("Switch to a named branch before committing")
            code, _, _ = self._run(path, ["diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv", "--"], home, check, allow_error=True)
            if code == 0: raise Conflict("No staged changes to commit")
            if code != 1: raise ValueError("Cannot inspect staged changes")
            env = self._env(home)
            env.update(GIT_AUTHOR_NAME=author_name, GIT_AUTHOR_EMAIL=author_email,
                       GIT_COMMITTER_NAME=author_name, GIT_COMMITTER_EMAIL=author_email)
            with self._mutation(path, "commit", {"previous_head": expected_head, "index_sha256": expected_index_sha256}, check) as receipt:
                self._run(path, ["commit", "--no-gpg-sign", "--no-verify", "-m", message], home, check, env=env)
                result = self._status(path, home, check); receipt["head"] = result["head"]
            return result

    def switch(self, path, branch, create=False, check=lambda: None):
        branch_name(branch)
        if not isinstance(create, bool): raise ValueError("create must be a boolean")
        with self._session(path, check) as home:
            status = self._status(path, home, check)
            if not status["clean"]: raise Conflict("Commit or otherwise preserve changes before switching; Forge will not discard them")
            if status["head"] is None: raise Conflict("Make the first commit before switching branches")
            if status["branch"] == branch and not create: return status
            with self._mutation(path, "switch", {"branch": branch, "create": create, "previous_head": status["head"]}, check):
                self._run(path, ["switch"] + (["-c"] if create else []) + [branch], home, check)
                result = self._status(path, home, check)
            return result

    def _transport(self, entry, home):
        _, env, remote = self.connections._git(entry, home)
        # Keep the isolated SSH command, plus local Git's non-executing environment.
        env.update(self._env(home))
        return env, remote

    def _network_check(self, cid, repository, write, check):
        def guard():
            check()
            self.connections.target(cid, repository, write=write)
        return guard

    def fetch(self, path, connection_id, repository, branch, check=lambda: None):
        branch_name(branch)
        entry = self.connections.target(connection_id, repository)
        check = self._network_check(connection_id, repository, False, check)
        if not self.connections.network.acquire(blocking=False): raise Conflict("Another Git/SSH operation is running")
        try:
            with self._session(path, check) as home:
                env, remote = self._transport(entry, home)
                # Repository-specific namespace prevents a fetch from another repo being mistaken for this one.
                key = hashlib.sha256((entry["id"] + "\0" + entry["repository"]).encode()).hexdigest()[:16]
                ref = "refs/remotes/forge-" + key + "/" + branch
                before = self._head(path, home, check, ref)
                with self._mutation(path, "fetch", {"connection_id": connection_id, "repository": entry["repository"], "branch": branch, "tracking_ref": ref}, check) as receipt:
                    self._run(path, ["fetch", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head", "--no-auto-maintenance", "--", remote, "refs/heads/" + branch + ":" + ref], home, check, env=env, timeout=120, budget=True)
                    commit = self._head(path, home, check, ref); receipt["head"] = commit
                return {"tracking_ref": ref, "commit": commit, "previous_commit": before, "working_tree_changed": False}
        finally: self.connections.network.release()

    def fast_forward(self, path, tracking_ref, expected_head, check=lambda: None):
        if not isinstance(tracking_ref, str) or not re.fullmatch(r"refs/remotes/forge-[0-9a-f]{16}/[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", tracking_ref):
            raise ValueError("Use the tracking_ref returned by git_fetch")
        branch_name(tracking_ref.split("/", 3)[3])
        if not isinstance(expected_head, str) or not OID.fullmatch(expected_head): raise ValueError("Supply the inspected current HEAD")
        with self._session(path, check) as home:
            status = self._status(path, home, check)
            if not status["clean"] or not status["branch"] or status["head"] != expected_head:
                raise Conflict("Fast-forward requires a clean named branch with the inspected HEAD")
            target = self._head(path, home, check, tracking_ref)
            if target is None: raise Conflict("Fetch this branch first")
            code, _, _ = self._run(path, ["merge-base", "--is-ancestor", expected_head, target], home, check, allow_error=True)
            if code: raise Conflict("Branches diverged; no merge, rebase or reset was attempted")
            if target == expected_head: return status
            with self._mutation(path, "fast_forward", {"previous_head": expected_head, "target": target}, check):
                self._run(path, ["merge", "--ff-only", "--no-edit", "--no-stat", "--no-gpg-sign", target], home, check)
                result = self._status(path, home, check)
            return result

    def _remote_head(self, path, remote, branch, home, env, check):
        _, text, _ = self._run(path, ["ls-remote", "--heads", "--", remote, "refs/heads/" + branch], home, check, env=env, timeout=12)
        lines = text.splitlines()
        if not lines: return None
        if len(lines) != 1: raise ValueError("Unexpected remote reference response")
        oid, sep, ref = lines[0].partition("\t")
        if not OID.fullmatch(oid) or sep != "\t" or ref != "refs/heads/" + branch:
            raise ValueError("Invalid remote reference response")
        return oid

    def push(self, path, connection_id, repository, branch, expected_head, check=lambda: None):
        branch_name(branch)
        if not isinstance(expected_head, str) or not OID.fullmatch(expected_head): raise ValueError("Supply the inspected branch HEAD")
        entry = self.connections.target(connection_id, repository, write=True)
        check = self._network_check(connection_id, repository, True, check)
        if not self.connections.network.acquire(blocking=False): raise Conflict("Another Git/SSH operation is running")
        try:
            with self._session(path, check) as home:
                status = self._status(path, home, check)
                if status["branch"] != branch or status["head"] != expected_head:
                    raise Conflict("Push only the current, inspected named branch; HEAD changed or branch does not match")
                if not status["clean"]: raise Conflict("Commit or preserve outstanding changes before publishing; nothing was pushed")
                env, remote = self._transport(entry, home)
                before = self._remote_head(path, remote, branch, home, env, check)
                if before == expected_head:
                    return {"published": True, "already_up_to_date": True, "verified_remote_head": before,
                            "connection_id": connection_id, "repository": entry["repository"], "branch": branch}
                # A normal, single-branch refspec with NO force flag; the server enforces fast-forward.
                with self._mutation(path, "push", {"connection_id": connection_id, "repository": entry["repository"], "branch": branch, "head": expected_head, "previous_remote_head": before}, check) as receipt:
                    self._run(path, ["push", "--porcelain", "--no-force", "--no-follow-tags", "--recurse-submodules=no", "--no-verify", "--signed=false", "--", remote, expected_head + ":refs/heads/" + branch], home, check, env=env, timeout=120)
                    after = self._remote_head(path, remote, branch, home, env, check)
                    if after != expected_head: raise ValueError("Remote branch no longer matches the pushed commit; inspect refs")
                    receipt["verified_remote_head"] = after
                return {"published": True, "verified_remote_head": after, "connection_id": connection_id,
                        "repository": entry["repository"], "branch": branch}
        finally: self.connections.network.release()
