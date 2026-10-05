"""Account model discovery and conservative, documented Responses capabilities.

GET /v1/models has no capability/effort fields. Never invent them from the
account inventory. Profiles below are reviewed against OpenAI model docs;
unknown/specialized models are reported but not offered for task execution.
See MODELS.md for sources, review date and maintenance instructions.
"""
from __future__ import annotations
import copy
import datetime as dt
import re
import threading
import time
from forge_provider import ProviderError

REVIEWED = "2026-10-03"
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def profile(model):
    # Only exact aliases and dated snapshots; no optimistic prefix matching.
    base = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
    common = dict(verbosity=True, compaction=True, web=True, code=True, images=True)
    if base in {"gpt-6-astra", "gpt-6.1-sol"}:
        efforts = ["low", "medium", "high", "xhigh", "max"]
    elif base in {"gpt-6-sol", "gpt-6-luna", "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}:
        efforts = ["none", "low", "medium", "high", "xhigh", "max"]
    elif base in {"gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.2"}:
        efforts = ["none", "low", "medium", "high", "xhigh"]
        if base.endswith("nano"): common.update(web=False, code=False)
    elif base == "gpt-5.1":
        efforts = ["none", "low", "medium", "high"]
        common["compaction"] = False
    elif base in {"gpt-5", "gpt-5-mini", "gpt-5-nano"}:
        efforts = ["minimal", "low", "medium", "high"]
        common["compaction"] = False
        if base.endswith("nano"): common.update(web=False, code=False)
    elif base in {"gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "gpt-4o-mini"}:
        efforts = []
        common.update(verbosity=False, compaction=False)
        if base.endswith("nano"): common.update(web=False, code=False)
    elif base in {"o3", "o4-mini"}:
        efforts = ["low", "medium", "high"]
        common.update(verbosity=False, compaction=False)
    else:
        return None
    return dict(common, family=base, efforts=efforts, suggested_effort="medium" if efforts else None)


def clean_settings(value):
    if not isinstance(value, dict) or set(value) != {"model", "reasoning_effort"}:
        raise ValueError("model_settings requires model and reasoning_effort only")
    model, effort = value["model"], value["reasoning_effort"]
    if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
        raise ValueError("Invalid model identifier")
    if effort is not None and (not isinstance(effort, str) or effort not in EFFORTS):
        raise ValueError("Invalid reasoning effort")
    p = profile(model)
    if p and (effort not in p["efforts"] if p["efforts"] else effort is not None):
        raise ValueError("Reasoning effort is not supported by this model; review the model controls")
    return {"model": model, "reasoning_effort": effort}


def defaults(tools):
    p = profile(tools.MODEL)
    return {"model": tools.MODEL, "reasoning_effort": tools.REASONING_EFFORT if not p or p["efforts"] else None}


class ModelCatalog:
    """Single-flight, bounded GET cache; refresh is not an inference request."""
    def __init__(self, tools, provider, clock=time.monotonic):
        self.f, self.provider, self.clock = tools, provider, clock
        self.lock = threading.Lock()
        self.items = None
        self.fetched_at = None
        self.checked = float("-inf")
        self.error = None

    def get(self, refresh=False):
        with self.lock:
            age = self.clock() - self.checked
            ttl = 60 if self.error else 300
            if age >= (30 if refresh else ttl):
                self.checked = self.clock()
                try:
                    if not self.f.OPENAI_API_KEY:
                        raise ProviderError("Set an OpenAI API key in app options to discover models.")
                    data = self.provider.list_models()
                    if not isinstance(data, list) or len(data) > 5000:
                        raise ProviderError("Invalid model inventory returned by OpenAI.")
                    items = {}
                    for item in data:
                        if not isinstance(item, dict): continue
                        mid = item.get("id")
                        if not isinstance(mid, str) or not MODEL_ID.fullmatch(mid): continue
                        shutdown = item.get("shutdown_date")
                        if not isinstance(shutdown, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", shutdown): shutdown = None
                        items[mid] = {"id": mid, "shutdown_date": shutdown}
                    if not items: raise ProviderError("OpenAI returned no usable model identifiers.")
                    self.items = items
                    self.fetched_at = dt.datetime.now(dt.timezone.utc).isoformat()
                    self.error = None
                except ProviderError as exc:
                    self.error = self.f.REDACT.text(str(exc))[:300]
                except Exception:
                    self.error = "Could not refresh the OpenAI model list. Saved task settings are unchanged."
            rows = []
            today = dt.date.today().isoformat()
            for mid, item in sorted((self.items or {}).items()):
                p = profile(mid)
                if not p: continue
                expired = bool(item["shutdown_date"] and item["shutdown_date"] <= today)
                rows.append(dict(item, **p, available=not expired, configured=mid == self.f.MODEL))
            if not any(r["id"] == self.f.MODEL for r in rows):
                p = profile(self.f.MODEL)
                rows.insert(0, dict(p or {"efforts": [self.f.REASONING_EFFORT], "suggested_effort": self.f.REASONING_EFFORT},
                                    id=self.f.MODEL, available=False, configured=True, shutdown_date=None,
                                    note="Configured model; not confirmed in the compatible account list."))
            return copy.deepcopy({"models": rows, "total_count": len(self.items or {}),
                                  "compatible_count": sum(r["available"] for r in rows),
                                  "fetched_at": self.fetched_at, "stale": bool(self.error), "error": self.error,
                                  "defaults": defaults(self.f), "capabilities_reviewed": REVIEWED,
                                  "capabilities_source": "OpenAI documentation; not provided by /v1/models",
                                  "refresh_interval_seconds": 30})

    def validate(self, value):
        value = clean_settings(value)
        # Preserve the administrator's existing configured model during a catalog outage.
        # It is an explicit fallback, never a claim of account availability.
        if value == defaults(self.f): return value
        row = next((x for x in self.get()["models"] if x["id"] == value["model"]), None)
        if not row or not row["available"]:
            raise ValueError("This model is not in the compatible account list. Refresh models or use the configured default.")
        if not profile(value["model"]): raise ValueError("Model capabilities have not been verified for Forge")
        return value


def apply_capabilities(payload, settings, tools):
    """Snapshot selection only; never inspect mutable browser settings mid-task."""
    p = profile(settings["model"])
    payload["model"] = settings["model"]
    effort = settings["reasoning_effort"]
    if p is None:
        # Administrator-configured custom models keep the pre-0.4 request contract.
        payload["reasoning"] = {"effort": effort, "summary": "auto"}
        return payload
    if p["efforts"]:
        payload["reasoning"] = {"effort": effort}
        if effort != "none": payload["reasoning"]["summary"] = "auto"
    else:
        payload.pop("reasoning", None)
    if not p["verbosity"]: payload.pop("text", None)
    if not p["compaction"]: payload.pop("context_management", None)
    web = p["web"] and not (p["family"] in {"gpt-5", "gpt-5-mini", "gpt-5-nano"} and effort == "minimal")
    payload["tools"] = [t for t in payload["tools"] if
                        not (t.get("type") in {"web_search", "web_search_preview"} and not web) and
                        not (t.get("type") == "code_interpreter" and not p["code"])]
    return payload
