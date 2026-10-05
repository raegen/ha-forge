"""Small, dependency-free safety primitives shared by tools and HTTP/runtime."""
from __future__ import annotations
import json
import re
from pathlib import Path

class Redactor:
    """Best-effort log/model-output redaction; not a sandbox for enabled shell."""
    KEY = re.compile(r"(?:^|[_-])(?:password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key)(?:$|[_-])", re.I)
    TOKEN = re.compile(r"\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}|eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b")
    BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/-]{12,}", re.I)

    LITERAL = re.compile(r"""((?:["']?(?:openai_api_key|api_key|password|passwd|access_token|refresh_token|client_secret)["']?)\s*:\s*)(["'])([^"'\r\n]+)\2""", re.I)

    def __init__(self, secrets=()):
        self.secrets = tuple(sorted((str(s) for s in secrets if s and len(str(s)) >= 8), key=len, reverse=True))

    def text(self, text):
        text = str(text)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = self.TOKEN.sub("[REDACTED]", text)
        text = self.LITERAL.sub(lambda m: m[1]+m[2]+"[REDACTED]"+m[2], text)
        return self.BEARER.sub(r"\1[REDACTED]", text)

    def __call__(self, value, depth=0):
        if depth > 40:
            return "[nested data omitted]"
        if isinstance(value, dict):
            return {str(k): ("[REDACTED]" if self.KEY.search(str(k)) and v not in (None, "", False)
                             else self(v, depth+1)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self(v, depth+1) for v in value]
        return self.text(value) if isinstance(value, str) else value

def bounded_json(value, limit=131072):
    text = json.dumps(value, ensure_ascii=False, default=str)
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    # Bound the encoded envelope too: JSON escaping can double a sliced preview.
    preview=raw[:max(0,limit-256)].decode("utf-8","ignore")
    def envelope(end):
        return json.dumps({"truncated":True,"original_bytes":len(raw),"preview":preview[:end]},ensure_ascii=False)
    result=envelope(len(preview))
    if len(result.encode("utf-8"))<=limit:return result
    low,high=0,len(preview)
    while low<high:
        mid=(low+high+1)//2
        if len(envelope(mid).encode("utf-8"))<=limit:low=mid
        else:high=mid-1
    return envelope(low)

def sensitive(path, prefixes, always, allow=False):
    path = str(path).strip("/")
    if any(path == p or path.startswith(p+"/") or path.startswith(p+".") for p in always):
        return True
    return not allow and any(path == p or path.startswith(p+"/") or path.startswith(p+".") for p in prefixes)

def resolve_path(value, roots, prefixes, always, allow=False, must_exist=False):
    if not isinstance(value, str) or "\0" in value:
        raise ValueError("Invalid mapped path")
    value = value.strip().replace("\\", "/").lstrip("/")
    parts = value.split("/")
    alias = parts[0]
    if alias not in roots:
        raise ValueError("Path must begin with one of: " + ", ".join(roots))
    # Reject traversal outright, even when it would resolve back inside the root.
    if ".." in parts:
        raise PermissionError("Parent traversal is not allowed")
    root = Path(roots[alias]).resolve()
    path = (root / Path(*parts[1:])).resolve(strict=False)
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise PermissionError("Path escapes mapped root") from exc
    canonical = alias if rel == "." else alias + "/" + rel
    if sensitive(value, prefixes, always, allow) or sensitive(canonical, prefixes, always, allow):
        raise PermissionError("Sensitive path is blocked by Forge configuration")
    if must_exist and not path.exists():
        raise FileNotFoundError(canonical)
    return alias, root, path
