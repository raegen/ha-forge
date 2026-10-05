"""Transactional local history and execution journal. No automatic data deletion."""
from __future__ import annotations
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid
from forge_safety import Redactor, bounded_json

ACTIVE = {"queued", "running", "cancelling"}
TERMINAL = {"completed", "failed", "interrupted", "cancelled", "waiting_input"}
def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()
def uid():
    return uuid.uuid4().hex[:16]
def dumps(x):
    return json.dumps(x, ensure_ascii=False, default=str, separators=(",", ":"))
class Conflict(Exception):
    pass

class Store:
    def __init__(self, path, legacy=None, redactor=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redact = redactor or Redactor()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Forge database failed integrity check; refusing to discard history")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 2):
            raise RuntimeError(f"Unsupported Forge database schema {version}; no data changed")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL REFERENCES conversations(id),
          data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS messages_chat ON messages(conversation_id,seq);
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
          status TEXT NOT NULL, created_at TEXT NOT NULL, request_key TEXT, data TEXT NOT NULL,
          UNIQUE(conversation_id,request_key));
        CREATE INDEX IF NOT EXISTS tasks_chat ON tasks(conversation_id,created_at);
        CREATE TABLE IF NOT EXISTS events(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id), data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_task ON events(task_id,seq);
        -- Additive to schema 2: old history/checkpoints remain readable on rollback.
        CREATE TABLE IF NOT EXISTS attachments(
          id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
          request_key TEXT NOT NULL, message_seq INTEGER REFERENCES messages(seq),
          deleted INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL,
          UNIQUE(conversation_id,request_key));
        CREATE INDEX IF NOT EXISTS attachments_chat ON attachments(conversation_id,message_seq,deleted);
        PRAGMA user_version=2;
        """)
        try:
            if not self.meta("initialized"):
                self._migrate(Path(legacy) if legacy else None)
            self.recover_startup()
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def tx(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self):
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.close()

    def meta(self, key):
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def _setmeta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key,dumps(value)))

    def _migrate(self, legacy):
        raw = {"version": 1, "conversations": {}, "tasks": {}}
        if legacy and legacy.exists():
            # Parse BEFORE marking initialized. Corruption must never look like an empty app.
            payload = legacy.read_bytes()
            raw = json.loads(payload)
            if not isinstance(raw, dict) or raw.get("version",1) != 1:
                raise RuntimeError("Invalid legacy Forge state; original file retained")
            if not isinstance(raw.get("conversations"), dict) or not isinstance(raw.get("tasks"), dict):
                raise RuntimeError("Invalid legacy conversations/tasks; original file retained")
            backup = legacy.with_name(legacy.name + ".pre-v0.2.0")
            if not backup.exists():
                with backup.open("xb") as stream:
                    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
                os.chmod(backup, 0o600)
                fd = os.open(str(backup.parent), os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
            digest = hashlib.sha256(payload).hexdigest()
        else:
            digest = None
        counts = {"conversations":0,"messages":0,"tasks":0,"events":0}
        with self.tx():
            if self.db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]:
                raise RuntimeError("Uninitialized database contains history; manual recovery required")
            for cid, original in raw["conversations"].items():
                if not isinstance(original,dict) or original.get("id",cid) != cid or not isinstance(original.get("messages",[]),list):
                    raise RuntimeError("Invalid legacy conversation; migration rolled back")
                c = dict(original); messages = c.pop("messages", [])
                c.update(id=cid, archived=bool(c.get("archived",False)), legacy_check=True)
                c.setdefault("pending_request",None)
                self._put_conv(c); counts["conversations"] += 1
                for m in messages:
                    if not isinstance(m,dict): raise RuntimeError("Invalid legacy message")
                    self._message(cid,self.redact(m)); counts["messages"] += 1
            for tid, original in raw["tasks"].items():
                if not isinstance(original,dict) or original.get("conversation_id") not in raw["conversations"]:
                    raise RuntimeError("Invalid legacy task; migration rolled back")
                t = dict(original); events = t.pop("events",[])
                if not isinstance(events,list): raise RuntimeError("Invalid legacy events")
                t.update(id=tid, allow_changes=False, change_deadline=0)
                # Legacy tasks omitted the request; find its preceding user message for Resume.
                msgs = raw["conversations"][t["conversation_id"]].get("messages",[])
                earlier = [m for m in msgs if m.get("role")=="user" and m.get("at","")<=t.get("created_at","~")]
                t.setdefault("user_text", str(earlier[-1].get("content","Continue carefully from the saved checkpoint.")) if earlier else "Continue carefully from the saved checkpoint.")
                self._put_task(t); counts["tasks"] += 1
                for e in events:
                    if not isinstance(e,dict): raise RuntimeError("Invalid legacy event")
                    self._event(tid,e); counts["events"] += 1
            self._setmeta("migration",{"at":now(),"sha256":digest,"counts":counts,"source":str(legacy) if digest else None})
            self._setmeta("initialized",True)

    def _put_conv(self, c):
        self.db.execute("INSERT INTO conversations VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",(c["id"],dumps(c)))
    def conv(self, cid):
        with self.lock:
            r = self.db.execute("SELECT data FROM conversations WHERE id=?",(cid,)).fetchone()
            if not r: raise KeyError("Conversation not found")
            return json.loads(r[0])
    def update_conv(self,cid,**values):
        with self.tx():
            c=self.conv(cid); c.update(values,updated_at=now()); self._put_conv(c)
            return c
    def set_change_preference(self,cid,allow,revision):
        """Persist only the human UI default; never create, renew or alter a task grant."""
        if not isinstance(allow,bool):raise ValueError("allow_changes_default must be boolean")
        if type(revision) is not int or not 0<=revision<2**53-1:
            raise ValueError("allow_changes_revision must be a nonnegative safe integer")
        with self.tx():
            c=self.conv(cid)
            if c.get("archived"):raise Conflict("Restore this chat before changing its preference")
            if revision!=c.get("allow_changes_revision",0):
                raise Conflict("Allow changes was changed elsewhere. Reload the conversation and review the checkbox.")
            if allow is not c.get("allow_changes_default",False):
                c.update(allow_changes_default=allow,allow_changes_revision=revision+1,updated_at=now())
                self._put_conv(c)
            return c
    def _put_task(self,t):
        self.db.execute("""INSERT INTO tasks VALUES(?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data""",
        (t["id"],t["conversation_id"],t["status"],t.get("created_at",now()),t.get("request_key"),dumps(t)))
    def task(self,tid):
        with self.lock:
            r=self.db.execute("SELECT data FROM tasks WHERE id=?",(tid,)).fetchone()
            if not r: raise KeyError("Task not found")
            return json.loads(r[0])
    def update_task(self,tid,**values):
        with self.tx():
            t=self.task(tid); t.update(values,updated_at=now()); self._put_task(t)
            return t
    def _message(self,cid,m):
        return self.db.execute("INSERT INTO messages(conversation_id,data) VALUES(?,?)",(cid,dumps(m))).lastrowid
    def _event(self,tid,e):
        clean=self.redact(e)
        if "data" in clean:
            clean["data"]=json.loads(bounded_json(clean["data"],16384))
        clean["message"]=str(clean.get("message",""))[:20000]
        self.db.execute("INSERT INTO events(task_id,data) VALUES(?,?)",(tid,dumps(clean)))
    def event(self,tid,kind,message,data=None):
        with self.tx():
            self._event(tid,{"at":now(),"kind":kind,"message":message,**({"data":data} if data is not None else {})})
            t=self.task(tid);t.update(updated_at=now(),phase=message[:240]);self._put_task(t)

    def create(self,title="",request_key=None):
        with self.tx():
            if request_key:
                old=self.db.execute("SELECT data FROM conversations WHERE json_extract(data,'$.request_key')=?",(request_key,)).fetchone()
                if old: return json.loads(old[0])
            c={"id":uid(),"title":title.strip()[:120] or "New Forge chat","created_at":now(),"updated_at":now(),
               "last_response_id":None,"pending_turn":None,"pending_request":None,"archived":False,
               "legacy_check":False,"request_key":request_key,"allow_changes_default":False,"allow_changes_revision":0}
            self._put_conv(c);return c

    def conversations(self, archived=False):
        with self.lock:
            rows=self.db.execute("SELECT data FROM conversations ORDER BY json_extract(data,'$.updated_at') DESC")
            return [self.public_conv(c) for r in rows if bool((c:=json.loads(r[0])).get("archived",False))==archived]
    def public_conv(self,c):
        return {**{k:c.get(k) for k in ("id","title","created_at","updated_at","archived","last_response_id","model_settings")},
                "allow_changes_default":c.get("allow_changes_default",False) is True,
                "allow_changes_revision":c.get("allow_changes_revision",0)}
    def public_task(self,t):
        keys=("id","conversation_id","status","created_at","updated_at","phase","error","result","cancel_requested",
              "provider_response_id","round","allow_changes","change_deadline","resumed_from","interrupts_task_id","model_settings")
        return self.redact({k:t.get(k) for k in keys})
    def tasks(self,cid,limit=20):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute(
                "SELECT data FROM tasks WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",(cid,limit))]
    def messages(self,cid,before=0,limit=80):
        with self.lock:
            rows=self.db.execute("""SELECT seq,data FROM messages WHERE conversation_id=? AND (?=0 OR seq<?)
                ORDER BY seq DESC LIMIT ?""",(cid,before,before,limit+1)).fetchall()
            return {"messages":[{**self.redact(json.loads(r["data"])),"seq":r["seq"]} for r in reversed(rows[:limit])],
                    "has_more_messages":len(rows)>limit}
    def view(self,cid,before=0):
        with self.lock:
            c=self.conv(cid); tasks=self.tasks(cid)
            return {**self.public_conv(c),**self.messages(cid,before),
                    "tasks":[self.public_task(t) for t in tasks],
                    "has_checkpoint":bool(c.get("pending_turn") or c.get("pending_request"))}
    def events(self,tid,after=0,limit=100):
        with self.lock:
            self.task(tid)
            rows=self.db.execute("SELECT seq,data FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                                 (tid,after,limit+1)).fetchall()
            selected=rows[:limit]
            return {"events":[{**self.redact(json.loads(r["data"])),"seq":r["seq"]} for r in selected],
                    "cursor":selected[-1]["seq"] if selected else after,"has_more":len(rows)>limit}
    def task_view(self,tid,after=0):
        with self.lock:
            return {**self.public_task(self.task(tid)),**self.events(tid,after)}
    def export(self,cid):
        with self.lock:
            c=self.conv(cid)
            messages=[self.redact(json.loads(r[0])) for r in self.db.execute(
                "SELECT data FROM messages WHERE conversation_id=? ORDER BY seq",(cid,))]
            return {"format":"smartass-forge-chat","version":2,"exported_at":now(),
                    "conversation":self.public_conv(c),"messages":messages,
                    "tasks":[self.public_task(t) for t in self.tasks(cid,10000)]}

    def artifact(self,seq,index):
        with self.lock:
            row=self.db.execute("SELECT data FROM messages WHERE seq=?",(seq,)).fetchone()
            if not row:raise KeyError("Message not found")
            annotations=json.loads(row[0]).get("annotations") or []
            if index<0 or index>=len(annotations):raise KeyError("Artifact not found")
            a=annotations[index]
            if a.get("type")!="container_file_citation":raise KeyError("Artifact not found")
            return a

    def find_request(self,cid,key):
        with self.lock:
            self.conv(cid)
            r=self.db.execute("SELECT data FROM tasks WHERE conversation_id=? AND request_key=?",(cid,key)).fetchone()
            return self.public_task(json.loads(r[0])) if r else None

    def enqueue(self,cid,text,key,allow_changes=False,resumed_from=None,attachment_ids=None,allow_changes_revision=None,interrupt_task_id=None,model_settings=None):
        import time
        from forge_attachments import valid_ids, bindings, public
        from forge_models import clean_settings
        if model_settings is not None:model_settings=clean_settings(model_settings)
        ids=valid_ids([] if attachment_ids is None else attachment_ids)
        if not isinstance(allow_changes,bool):raise ValueError("allow_changes must be boolean")
        if allow_changes_revision is not None and (type(allow_changes_revision) is not int or not 0<=allow_changes_revision<2**53):
            raise ValueError("allow_changes_revision must be a nonnegative safe integer")
        if not isinstance(text,str) or (not text.strip() and not ids) or len(text.encode())>65536:
            raise ValueError("Message needs text or attachments; text is limited to 65536 bytes")
        if not isinstance(key,str) or not 8<=len(key)<=128:
            raise ValueError("A request_id of 8–128 characters is required for safe retries")
        if interrupt_task_id is not None and (not isinstance(interrupt_task_id,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",interrupt_task_id)):
            raise ValueError("interrupt_task_id must identify the task shown when Send was pressed")
        if resumed_from and interrupt_task_id is not None:raise ValueError("Resume cannot interrupt a task")
        with self.tx():
            existing=self.db.execute("SELECT data FROM tasks WHERE conversation_id=? AND request_key=?",(cid,key)).fetchone()
            if existing:
                t=json.loads(existing[0])
                if t.get("user_text")!=text.strip() or t.get("resumed_from")!=resumed_from or t.get("attachment_ids",[])!=ids or t.get("interrupts_task_id")!=interrupt_task_id:
                    raise Conflict("This request_id was already used with different content")
                if model_settings is not None and t.get("model_settings")!=model_settings:
                    raise Conflict("This request_id was already used with different model settings")
                return t,False
            c=self.conv(cid)
            # Dedup above precedes this check: retry never renews an accepted grant.
            # The preference itself is not authority; explicit allow_changes is still required.
            if not resumed_from and allow_changes_revision is not None and (
                    allow_changes_revision!=c.get("allow_changes_revision",0) or
                    allow_changes is not c.get("allow_changes_default",False)):
                raise Conflict("Allow changes was changed elsewhere. Review the checkbox and send again.")
            req=c.get("pending_request")
            if req and not req.get("response_id") and not resumed_from and interrupt_task_id is None:
                raise Conflict("The previous provider submission has an unknown outcome. Resume that task explicitly, or start a new chat.")
            if c.get("archived"): raise Conflict("Restore this chat before sending a message")
            active=[json.loads(r[0]) for r in self.db.execute("SELECT data FROM tasks WHERE conversation_id=? AND status IN ('queued','running','cancelling')",(cid,))]
            old=None
            if interrupt_task_id is not None:
                latest=self.tasks(cid,1)
                if not latest or latest[0]["id"]!=interrupt_task_id:
                    raise Conflict("The task changed before your update arrived. Reload and review before sending again.")
                old=latest[0]
                if len(active)>1 or any(t["id"]!=interrupt_task_id for t in active):
                    raise Conflict("An update is already waiting for the previous task to stop.")
            elif active:
                raise Conflict("This conversation already has an active task")
            count=self.db.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('queued','running','cancelling')").fetchone()[0]
            if count-int(bool(old and old["status"]=="queued"))>=8:
                raise Conflict("The task queue is full (8). Wait or cancel queued work.")
            attachments=bindings(self,cid,ids,resumed_from)
            # This transaction binds the human message and requests cancellation together.
            # It never dispatches tools, changes the preference, or extends the old grant.
            if old and old["status"] in ACTIVE:
                old.update(cancel_requested=True,status="cancelled" if old["status"]=="queued" else "cancelling",
                           allow_changes=False,change_deadline=0,updated_at=now())
                self._put_task(old)
                self._event(old["id"],{"at":now(),"kind":"status","message":"Stopped for a new user update. An action already in progress may finish; saved results are retained."})
            t={"id":uid(),"conversation_id":cid,"status":"queued","created_at":now(),"updated_at":now(),
               "request_key":key,"user_text":text.strip(),"attachment_ids":ids,"cancel_requested":False,"allow_changes":bool(allow_changes),
               "change_deadline":time.time()+3600 if allow_changes else 0,"resumed_from":resumed_from,"round":0,
               "steering_pending":self.task(resumed_from).get("steering_pending",False) if resumed_from else bool(interrupt_task_id or c.get("pending_turn") or req),
               "interrupts_task_id":interrupt_task_id,"model_settings":model_settings}
            self._put_task(t)
            if not resumed_from:
                seq=self._message(cid,{"role":"user","content":text.strip(),"at":now(),"task_id":t["id"],
                                      **({"attachments":[public(a) for a in attachments]} if attachments else {})})
                for aid in ids:
                    self.db.execute("UPDATE attachments SET message_seq=? WHERE id=?",(seq,aid))
            if c.get("title")=="New Forge chat":
                c["title"]=" ".join(text.split())[:72] or attachments[0]["filename"][:72]
            if model_settings is not None and not resumed_from:c["model_settings"]=model_settings
            c["updated_at"]=now();self._put_conv(c)
            message="Queued. Changes authorized for up to 60 minutes." if allow_changes else "Queued in read-only mode."
            if old:message="Update saved; waiting for the previous task to stop. "+message
            self._event(t["id"],{"at":now(),"kind":"status","message":message})
            return t,True

    def checkpoint_request(self,cid,tid,request,steering_pending=None):
        """Publish a provider intent and its new-user-message acknowledgement atomically."""
        with self.tx():
            c=self.conv(cid);c.update(pending_request=request,updated_at=now());self._put_conv(c)
            if steering_pending is not None:
                t=self.task(tid);t.update(steering_pending=bool(steering_pending),updated_at=now());self._put_task(t)

    def next_queued(self):
        with self.tx():
            r=self.db.execute("SELECT q.data FROM tasks q WHERE q.status='queued' AND NOT EXISTS (SELECT 1 FROM tasks busy WHERE busy.conversation_id=q.conversation_id AND busy.status IN ('running','cancelling')) ORDER BY q.created_at LIMIT 1").fetchone()
            if not r: return None
            t=json.loads(r[0]);t.update(status="running",updated_at=now());self._put_task(t);return t

    def recover_startup(self):
        with self.tx():
            rows=self.db.execute("SELECT data FROM tasks WHERE status IN ('running','queued','cancelling')").fetchall()
            for r in rows:
                t=json.loads(r[0]);t.update(status="interrupted",allow_changes=False,change_deadline=0,updated_at=now(),
                    error="Forge restarted. Saved results are retained. Resume explicitly; uncertain changes are not repeated.")
                self._put_task(t)
                self._event(t["id"],{"at":now(),"kind":"recovery","message":t["error"]})

    def cancel(self,tid):
        with self.tx():
            t=self.task(tid)
            if t["status"] in ACTIVE:
                t.update(cancel_requested=True,updated_at=now(),status="cancelled" if t["status"]=="queued" else "cancelling")
                t.update(allow_changes=False,change_deadline=0)
                self._put_task(t)
                self._event(tid,{"at":now(),"kind":"status","message":"Cancellation requested. An action already in progress may finish; it cannot be rolled back."})
            return t

    def accept_response(self,cid,tid,response,calls,text="",annotations=None,finalize=True):
        """Atomically acknowledge the previous journal and install the next one."""
        with self.tx():
            c=self.conv(cid);t=self.task(tid);rid=response["id"]
            c.update(pending_request=None,legacy_check=False,updated_at=now())
            t.update(provider_response_id=rid,updated_at=now())
            if calls:
                c["pending_turn"]={"response_id":rid,"task_id":tid,"calls":calls,"outputs":{},"in_progress":None}
                if text:
                    self._message(cid,self.redact({"role":"assistant","content":text,"at":now(),"task_id":tid,
                                                  "partial":True,"annotations":annotations or []}))
                    self._event(tid,{"at":now(),"kind":"assistant_update","message":text})
            else:
                c.update(last_response_id=rid,pending_turn=None)
                if response.get("status")=="completed":
                    self._message(cid,self.redact({"role":"assistant","content":text,"at":now(),"task_id":tid,"annotations":annotations or [],"partial":not finalize}))
                    if finalize:t.update(status="completed",result=self.redact.text(text),phase="Completed")
            self._put_conv(c);self._put_task(t)

    def wait_for_input(self,cid,tid,question,outputs,response_id):
        """Atomically publish the question, journal the barrier, and revoke this task's grant."""
        with self.tx():
            t=self.task(tid);c=self.conv(cid);p=c.get("pending_turn")
            if t.get("cancel_requested"):return False
            if not p or p["response_id"]!=response_id or t["status"]!="running":
                raise Conflict("Question checkpoint changed; no actions were dispatched")
            p["outputs"].update(outputs);p["in_progress"]=None
            c.update(pending_turn=p,updated_at=now());self._put_conv(c)
            question=self.redact.text(question)
            self._message(cid,{"role":"assistant","content":question,"at":now(),"task_id":tid,"waiting_input":True})
            t.update(status="waiting_input",phase="Waiting for your answer",result=question,error=None,
                     allow_changes=False,change_deadline=0,updated_at=now());self._put_task(t)
            self._event(tid,{"at":now(),"kind":"waiting_input","message":"Waiting for your answer. No further tools run until you explicitly send a reply."})
            return True

    def tool_intent(self,cid,call):
        with self.tx():
            c=self.conv(cid)
            if not c.get("pending_turn"): raise RuntimeError("No tool journal")
            c["pending_turn"]["in_progress"]={"call_id":call["call_id"],"name":call["name"],"started_at":now()}
            self._put_conv(c)

    def tool_result(self,cid,output):
        with self.tx():
            c=self.conv(cid)
            if not c.get("pending_turn"): raise RuntimeError("No tool journal")
            c["pending_turn"]["outputs"][output["call_id"]]=output
            c["pending_turn"]["in_progress"]=None
            self._put_conv(c)
