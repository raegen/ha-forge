"""Admin-managed persistent SSH identities; narrow Git operations, never an SSH shell.

Private credentials live outside mapped model roots. This is NOT a vault against
an administrator or explicitly enabled arbitrary shell in the same container.
"""
from __future__ import annotations
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from forge_store import Conflict

MAX_CONNECTIONS = 32
MAX_CLONE_BYTES = 512 * 1024 * 1024
MAX_CLONE_FILES = 100000
IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")


def public_key(value):
    """Accept only a complete plain Ed25519 public key, never arbitrary SSH lines."""
    if not isinstance(value, str) or len(value) > 300 or "\n" in value or "\r" in value:
        raise ValueError("A single Ed25519 public key is required")
    parts = value.strip().split()
    if len(parts) not in (2, 3) or parts[0] != "ssh-ed25519":
        raise ValueError("Only Ed25519 keys are supported")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid public key encoding") from exc
    prefix = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
    if len(blob) != len(prefix) + 32 or not blob.startswith(prefix):
        raise ValueError("Invalid Ed25519 public key")
    encoded = base64.b64encode(blob).decode("ascii")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    return {"public_key": "ssh-ed25519 " + encoded, "fingerprint": fingerprint}


def clean_connection(data):
    required = {"request_id", "name", "host", "port", "username", "repository"}
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - {"scope", "access"}:
        raise ValueError("Connection requires request_id, name, host, port, username, repository and optional scope/access")
    scope, access = data.get("scope", "repository"), data.get("access", "read")
    if scope not in {"repository", "account"} or access not in {"read", "write"}:
        raise ValueError("Scope must be repository/account and access must be read/write")
    if not isinstance(data["request_id"], str) or not re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", data["request_id"]):
        raise ValueError("Invalid creation request ID")
    name = data["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Name must be 1–80 characters without control characters")
    host = data["host"]
    if not isinstance(host, str) or not 1 <= len(host) <= 253:
        raise ValueError("Invalid SSH host")
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        if not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in host.split(".")):
            raise ValueError("Use a DNS host or IP address, not a URL, network range or SSH command")
        host = host.lower()
    port = data["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Port must be an integer from 1 to 65535")
    user = data["username"]
    if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", user):
        raise ValueError("Invalid SSH username")
    repo = data["repository"]
    if not (scope == "account" and repo == ""):
        repository_path(repo)
    if scope == "account" and repo:
        raise ValueError("An account connection has no fixed repository; leave repository empty")
    result = {"name": name.strip(), "host": host, "port": port, "username": user, "repository": repo}
    if "scope" in data or "access" in data:
        result.update(scope=scope, access=access)
    return result


def repository_path(repo):
    if not isinstance(repo, str) or not 1 <= len(repo) <= 240 or not all(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", p) and p not in {".", ".."} for p in repo.split("/")):
        raise ValueError("Use a repository path such as owner/project.git, without a URL, spaces or traversal")
    return repo


def _private_directory(path, create=False):
    path = Path(path).absolute()
    if path.resolve() != path or path.is_symlink():
        raise PermissionError("SSH storage must not contain symlinks")
    if path.exists():
        st = path.stat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022:
            raise PermissionError("SSH directory ownership or permissions are unsafe")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def _file(path, required=True):
    if path.is_symlink() or path.resolve() != path.absolute():
        raise PermissionError("SSH files must not contain symlinks")
    if path.exists():
        st = path.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise PermissionError("SSH file ownership or permissions are unsafe")
    elif required:
        raise Conflict("SSH connection is incomplete; no key was replaced. Inspect Connections before retrying")
    return path


def _atomic(path, data):
    _file(path, required=False)
    data = data.encode("utf-8") if isinstance(data, str) else data
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if tmp.exists(): tmp.unlink()  # Only our unpublished atomic-write temporary.


class SSHConnections:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.lock = threading.RLock()
        self.network = threading.BoundedSemaphore(1)
        # No credentials, files or network activity on startup or status requests.

    @staticmethod
    def capabilities():
        return {"available": all(shutil.which(n) for n in ("git", "ssh", "ssh-keygen", "ssh-keyscan")),
                "max_connections": MAX_CONNECTIONS, "key_type": "ed25519", "remote_write": True, "account_keys": True}

    @staticmethod
    def _binary(name):
        found = shutil.which(name)
        if not found:
            raise Conflict("SSH client dependencies are unavailable. Update Forge through Home Assistant first")
        return found

    def _path(self, cid):
        if not isinstance(cid, str) or not IDENTIFIER.fullmatch(cid):
            raise ValueError("Invalid connection ID")
        _private_directory(self.root)
        return _private_directory(self.root / cid)

    def _load(self, cid):
        path = self._path(cid) / "connection.json"
        if not path.exists(): raise KeyError("Connection not found")
        _file(path)
        if path.stat().st_size > 16384: raise Conflict("Invalid connection metadata")
        try:
            result = json.loads(path.read_text())
            if not isinstance(result, dict) or result.get("id") != cid or result.get("version") != 1:
                raise ValueError("Unsupported record")
            fields = clean_connection({"request_id": cid, **{k: result.get(k) for k in ("name", "host", "port", "username", "repository")},
                                       **{k: result[k] for k in ("scope", "access") if k in result}})
            if any(result.get(k) != v for k, v in fields.items()): raise ValueError("Noncanonical connection")
            if type(result.get("enabled")) is not bool or type(result.get("revision")) is not int or not 0 <= result["revision"] < 2**53:
                raise ValueError("Invalid revision or enabled state")
            if result.get("state") not in {"ready", "creating"}: raise ValueError("Invalid key state")
            if result["state"] == "creating" and result["enabled"]: raise ValueError("Incomplete identity enabled")
            if result["state"] == "ready" and public_key(result.get("public_key"))["fingerprint"] != result.get("fingerprint"):
                raise ValueError("Public key mismatch")
            if result.get("host_public_key"):
                if public_key(result["host_public_key"])["fingerprint"] != result.get("host_fingerprint"):
                    raise ValueError("Host fingerprint mismatch")
            elif result.get("host_fingerprint") is not None: raise ValueError("Missing host key")
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise Conflict("Invalid SSH connection metadata; nothing was overwritten") from None
        return result

    def _save(self, entry):
        _atomic(self._path(entry["id"]) / "connection.json", json.dumps(entry, sort_keys=True) + "\n")

    @staticmethod
    def _view(entry):
        # Explicit allowlist: never publish on-disk paths or private credential bytes.
        result = {k: entry.get(k) for k in ("id", "name", "host", "port", "username", "repository", "enabled",
                "revision", "created_at", "state", "public_key", "fingerprint", "host_fingerprint")}
        result.update(scope=entry.get("scope", "repository"), access=entry.get("access", "read"))
        return result

    def listing(self):
        with self.lock:
            _private_directory(self.root)
            entries = []
            if self.root.exists():
                for p in sorted(self.root.iterdir()):
                    if IDENTIFIER.fullmatch(p.name) and p.is_dir():
                        entries.append(self._view(self._load(p.name)))
            return {"connections": entries, **self.capabilities()}

    def create(self, data):
        fields = clean_connection(data)
        cid = uuid.UUID(data["request_id"]).hex
        with self.lock:
            directory = self._path(cid)
            if directory.exists():
                saved = self._load(cid)
                if any(saved.get(k) != v for k, v in fields.items()):
                    raise Conflict("This request ID already belongs to a different connection")
                # An uncertain/restarted creation never silently generates another key.
                return self._view(saved)
            binary = self._binary("ssh-keygen")
            if len(self.listing()["connections"]) >= MAX_CONNECTIONS:
                raise Conflict("Connection limit reached; no credentials were deleted")
            _private_directory(self.root, create=True)
            directory.mkdir(mode=0o700)
            saved = {**fields, "id": cid, "version": 1, "state": "creating", "enabled": False,
                     "revision": 0, "created_at": time.time(), "public_key": None,
                     "fingerprint": None, "host_fingerprint": None, "host_public_key": None}
            self._save(saved)
            key = directory / "id_ed25519"
            code, _, _ = self._run([binary, "-q", "-t", "ed25519", "-N", "", "-C", "forge-" + cid[:12], "-f", str(key)],
                                    directory, self._env(directory), 5)
            if code != 0:
                raise Conflict("Key creation did not complete. Existing files were retained; use Connections to inspect")
            _file(key)
            pub = key.with_suffix(".pub")
            if pub.is_symlink() or not pub.is_file() or pub.stat().st_size > 512:
                raise Conflict("Invalid generated public key; existing files retained")
            os.chmod(pub, 0o600)
            info = public_key(pub.read_text().strip())
            saved.update(info, state="ready", enabled=True, revision=1)
            self._save(saved)
            return self._view(saved)

    def _revision(self, cid, revision):
        if type(revision) is not int or not 0 <= revision < 2**53:
            raise ValueError("A valid connection revision is required")
        entry = self._load(cid)
        if entry["revision"] != revision:
            raise Conflict("Connection changed in another tab. Refresh before deciding again")
        return entry

    def set_enabled(self, cid, data):
        if not isinstance(data, dict) or set(data) != {"revision", "enabled"} or type(data["enabled"]) is not bool:
            raise ValueError("Expected revision and boolean enabled only")
        with self.lock:
            entry = self._revision(cid, data["revision"])
            if data["enabled"] and entry["state"] != "ready":
                raise Conflict("Incomplete credentials cannot be enabled; create a new connection explicitly")
            entry["enabled"] = data["enabled"]; entry["revision"] += 1
            self._save(entry)
            return self._view(entry)

    def set_access(self, cid, data):
        """Administrator HTTP endpoint only; never exposed as a model tool."""
        if not isinstance(data, dict) or set(data) != {"revision", "access"} or data["access"] not in {"read", "write"}:
            raise ValueError("Expected revision and access read/write only")
        with self.lock:
            entry = self._revision(cid, data["revision"])
            if entry["state"] != "ready": raise Conflict("Connection is not ready")
            entry.update(scope=entry.get("scope", "repository"), access=data["access"], revision=entry["revision"] + 1)
            self._save(entry)
            return self._view(entry)

    def target(self, cid, repository=None, write=False):
        entry = self._ready(cid)
        if write and entry.get("access", "read") != "write":
            raise PermissionError("This SSH connection is read-only. Only the administrator can enable write access in Connections")
        if entry.get("scope", "repository") == "account":
            repo = repository_path(repository)
        else:
            repo = entry["repository"]
            if repository is not None and repository != repo:
                raise PermissionError("Repository is outside this connection's administrator-approved scope")
        return {**entry, "repository": repo}

    def pin(self, cid, data):
        if not isinstance(data, dict) or set(data) != {"revision", "public_key", "expected_fingerprint"}:
            raise ValueError("Expected revision, public_key and independently verified expected_fingerprint only")
        key = public_key(data["public_key"])
        if data["expected_fingerprint"] != key["fingerprint"]:
            raise ValueError("The independently obtained fingerprint does not match this host key")
        with self.lock:
            entry = self._revision(cid, data["revision"])
            if entry["state"] != "ready": raise Conflict("Connection is not ready")
            entry.update(host_public_key=key["public_key"], host_fingerprint=key["fingerprint"], revision=entry["revision"] + 1)
            self._save(entry)
            return self._view(entry)

    def _ready(self, cid, pinned=True):
        with self.lock:
            entry = self._load(cid)
            if entry["state"] != "ready" or not entry["enabled"]:
                raise Conflict("Connection is disabled or incomplete")
            _file(self._path(cid) / "id_ed25519")
            if pinned and not entry["host_public_key"]:
                raise PermissionError("The administrator must verify and pin the SSH host fingerprint in Connections first")
            if pinned and public_key(entry["host_public_key"])["fingerprint"] != entry["host_fingerprint"]:
                raise Conflict("Stored host fingerprint does not match; nothing was trusted")
            return entry

    def scan(self, cid, data):
        if data != {}: raise ValueError("Host scan accepts an empty object only")
        entry = self._ready(cid, pinned=False)
        if not self.network.acquire(blocking=False): raise Conflict("Another Git/SSH operation is running")
        try:
            with tempfile.TemporaryDirectory(prefix="forge-scan-") as tmp:
                code, text, _ = self._run([self._binary("ssh-keyscan"), "-T", "5", "-p", str(entry["port"]), "-t", "ed25519", entry["host"]],
                                          Path(tmp), self._env(Path(tmp)), 7)
            candidates = {}
            for line in text.splitlines():
                parts = line.split()
                if len(parts) != 3 or parts[1] != "ssh-ed25519": continue
                try: candidate = public_key(" ".join(parts[1:]))
                except ValueError: continue
                candidates[candidate["fingerprint"]] = candidate
            if code or not candidates:
                raise ValueError("Could not read an Ed25519 host key. Check the host, port, network and host key support")
            return {"candidates": list(candidates.values()), "revision": entry["revision"], "trusted": False}
        finally: self.network.release()

    @staticmethod
    def _env(home):
        # No API/Supervisor tokens, ssh-agent, Git config injection, prompts or user config.
        return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "LANG": "C", "LC_ALL": "C",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
                "SSH_ASKPASS_REQUIRE": "never", "GIT_ALLOW_PROTOCOL": "ssh", "GIT_PROTOCOL_FROM_USER": "0"}

    def _git(self, entry, home):
        key = self._path(entry["id"]) / "id_ed25519"
        _file(key)
        hosts = home / "known_hosts"
        label = entry["host"] if entry["port"] == 22 else "[" + entry["host"] + "]:" + str(entry["port"])
        _atomic(hosts, label + " " + entry["host_public_key"] + "\n")
        ssh = [self._binary("ssh"), "-F", "/dev/null", "-i", str(key), "-p", str(entry["port"])]
        for option in ("BatchMode=yes", "IdentitiesOnly=yes", "IdentityAgent=none", "StrictHostKeyChecking=yes",
                       "UserKnownHostsFile=" + str(hosts), "GlobalKnownHostsFile=/dev/null", "UpdateHostKeys=no",
                       "HostKeyAlgorithms=ssh-ed25519", "VerifyHostKeyDNS=no", "ConnectTimeout=7", "ConnectionAttempts=1",
                       "PasswordAuthentication=no", "KbdInteractiveAuthentication=no", "PreferredAuthentications=publickey",
                       "ForwardAgent=no", "ClearAllForwardings=yes", "ControlMaster=no", "ControlPath=none",
                       "ProxyCommand=none", "ProxyJump=none", "PermitLocalCommand=no", "CanonicalizeHostname=no",
                       "AddKeysToAgent=no", "RequestTTY=no", "LogLevel=ERROR", "ServerAliveInterval=5", "ServerAliveCountMax=1"):
            ssh.extend(["-o", option])
        env = self._env(home)
        # Git requires a command string; all components are fixed or internally generated and shell-quoted.
        env.update(GIT_SSH_COMMAND=shlex.join(ssh), GIT_SSH_VARIANT="ssh")
        git = [self._binary("git"), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "credential.helper=", "-c", "protocol.allow=never", "-c", "protocol.ssh.allow=always",
               "-c", "init.templateDir=", "-c", "core.pager=cat", "-c", "submodule.recurse=false"]
        host = "[" + entry["host"] + "]" if ":" in entry["host"] else entry["host"]
        remote = "ssh://" + entry["username"] + "@" + host + ":" + str(entry["port"]) + "/" + entry["repository"]
        return git, env, remote

    @staticmethod
    def _failure(stderr):
        # Remote banners/errors are untrusted and might contain credentials or control text.
        if "Host key verification failed" in stderr or "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
            return "SSH host verification failed. Do not bypass it; independently verify any host key change"
        if "Permission denied" in stderr:
            return "SSH authentication failed. Add this connection's public key at the Git provider and check repository access"
        return "Git could not access the repository. Check network, public-key registration, repository path and provider permissions"

    def refs(self, cid, check=lambda: None, repository=None):
        check(); entry = self.target(cid, repository)
        if not self.network.acquire(blocking=False): raise Conflict("Another Git/SSH operation is running")
        try:
            with tempfile.TemporaryDirectory(prefix="forge-git-") as tmp:
                home = Path(tmp); git, env, remote = self._git(entry, home)
                code, text, err = self._run(git + ["ls-remote", "--heads", "--tags", "--", remote], home, env, 12, check)
            if code: raise ValueError(self._failure(err))
            refs = []
            for line in text.splitlines():
                if not line: continue
                parts = line.split("\t")
                if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parts[0]) or not re.fullmatch(r"refs/(?:heads|tags)/[\x21-\x7e]{1,240}", parts[1]):
                    raise ValueError("The Git server returned an invalid reference list")
                if len(refs) < 200: refs.append({"commit": parts[0], "ref": parts[1]})
            return {"connection_id": cid, "authenticated": True, "remote_write_tested": False,
                    "refs": refs, "truncated": len(text.splitlines()) > 200}
        finally: self.network.release()

    def clone(self, cid, clone_root, name, branch=None, check=lambda: None, repository=None):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise ValueError("Directory name must contain 1–64 letters, digits, underscores or hyphens")
        if branch is not None and (not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", branch) or any(x in branch for x in ("..", "//", "/.")) or branch.endswith(("/", ".", ".lock"))):
            raise ValueError("Invalid branch or tag name")
        check(); entry = self.target(cid, repository)
        root = _private_directory(clone_root)
        if not self.network.acquire(blocking=False): raise Conflict("Another Git/SSH operation is running")
        marker = None
        try:
            check(); _private_directory(root, create=True)
            dest = root / name
            marker = root / ("." + name + ".forge-clone.json")
            if dest.exists() or dest.is_symlink() or marker.exists() or marker.is_symlink():
                marker = None
                raise Conflict("Clone destination or receipt already exists. Inspect it; do not repeat an uncertain clone")
            dest.mkdir(mode=0o700)
            receipt = {"connection_id": cid, "repository": entry["repository"], "path": "share/forge_git/" + name, "state": "started", "branch": branch}
            _atomic(marker, json.dumps(receipt) + "\n")
            with tempfile.TemporaryDirectory(prefix="forge-git-") as tmp:
                home = Path(tmp); git, env, remote = self._git(entry, home)
                args = ["clone", "--depth", "1", "--single-branch", "--no-recurse-submodules", "--template="]
                if branch is not None: args += ["--branch", branch]
                code, _, err = self._run(git + args + ["--", remote, str(dest)], home, env, 120, check, dest)
            if code: raise ValueError(self._failure(err) + ". Inspect the clone receipt before any retry")
            receipt.update(state="complete", shallow=True)
            _atomic(marker, json.dumps(receipt) + "\n")
            return receipt
        except BaseException:
            if marker is not None and marker.exists():
                receipt["state"] = "incomplete"
                _atomic(marker, json.dumps(receipt) + "\n")
            raise
        finally: self.network.release()

    @staticmethod
    def _budget(directory):
        size = count = 0
        for root, dirs, files in os.walk(directory, followlinks=False):
            count += len(dirs) + len(files)
            for name in files:
                try: size += (Path(root) / name).lstat().st_size
                except FileNotFoundError: pass
            if size > MAX_CLONE_BYTES or count > MAX_CLONE_FILES:
                raise OverflowError("Clone exceeded the 512 MiB / 100,000 entry safety limit; inspect its receipt and retained files")

    @staticmethod
    def _run(args, cwd, env, timeout, check=lambda: None, budget=None):
        check()
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        selector = selectors.DefaultSelector()
        streams = {process.stdout: bytearray(), process.stderr: bytearray()}
        for stream in streams: selector.register(stream, selectors.EVENT_READ)
        start = time.monotonic(); last_budget = 0
        try:
            while selector.get_map() or process.poll() is None:
                check()
                elapsed = time.monotonic() - start
                if elapsed > timeout: raise TimeoutError("Git/SSH operation timed out; no automatic retry was made")
                if budget is not None and elapsed - last_budget >= .5:
                    SSHConnections._budget(budget); last_budget = elapsed
                for key, _ in selector.select(.1):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk: selector.unregister(key.fileobj); continue
                    streams[key.fileobj].extend(chunk)
                    if len(streams[key.fileobj]) > 262144:
                        raise OverflowError("Git/SSH output exceeded the safety limit")
            if budget is not None: SSHConnections._budget(budget)
            return process.wait(), streams[process.stdout].decode("utf-8", "replace"), streams[process.stderr].decode("utf-8", "replace")
        finally:
            selector.close()
            # Kill the process group even if its leader exited but a child retained a pipe.
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=.5)
            except subprocess.TimeoutExpired: pass
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
            for stream in streams: stream.close()
