"""Regression tests; no OpenAI, HA, Supervisor or user-data access."""
from __future__ import annotations
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from forge_safety import Redactor,bounded_json,resolve_path
from forge_store import Store,Conflict
from forge_provider import Provider,Cancelled,Interrupted,AmbiguousRequest,ProviderError,retry_seconds
from forge_runtime import Engine,AppServer,output

def tools():
    f=types.SimpleNamespace(VERSION="0.2.0",OPENAI_API_KEY="test-not-a-real-secret",MODEL="mock-model",REASONING_EFFORT="high",
       TEXT_VERBOSITY="medium",ENABLE_WEB_SEARCH=True,ENABLE_CODE_INTERPRETER=True,ALLOW_SHELL=False,ALLOW_SENSITIVE=False,
       OPENAI_TIMEOUT=60,OPENAI_RATE_LIMIT_MAX_WAIT=2,MAX_TOOL_OUTPUT=131072,MAX_TOOL_ROUNDS=8,
       CONTEXT_COMPACT_THRESHOLD=100000,OPENAI_RESPONSES_URL="https://api.invalid/v1/responses",AGENT_INSTRUCTIONS="Test",
       REDACT=Redactor(["test-not-a-real-secret"]),TOOL_CANCEL=threading.local(),
       MUTATING_TOOL_NAMES={"write_file","run_shell"},TOOL_IMPL={})
    f.tool_definitions=lambda:[]
    f.get_project_context=lambda:{"content":"Test architecture"}
    return f
def response(rid="resp_done",calls=None,text="Finished.",status="completed"):
    out=list(calls or [])
    if text:out.append({"type":"message","content":[{"type":"output_text","text":text,"annotations":[]}]})
    return {"id":rid,"status":status,"output":out}
def call(name="write_file",id="call_1"):
    return {"type":"function_call","name":name,"call_id":id,"arguments":"{}"}
class FakeProvider:
    def __init__(self,responses=()):
        self.responses=list(responses);self.payloads=[];self.retrieved=[];self.cancelled=[];self.saved={}
    def create(self,payload,client_id,check,event):
        check();self.payloads.append(payload)
        item=self.responses.pop(0)
        if isinstance(item,Exception):raise item
        return item
    def retrieve(self,rid,check,event):
        check();self.retrieved.append(rid);return self.saved[rid]
    def wait(self,response,check,event,save):
        check();return response
    def cancel(self,rid):
        self.cancelled.append(rid);return response(rid,status="cancelled")

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.s=Store(self.root/"forge.sqlite3");self.f=tools()
        self.c=self.s.create()["id"]
    def tearDown(self):
        self.s.close();self.tmp.cleanup()
    def enqueue(self,text="Please inspect.",allow=False,cid=None,key="request-12345"):
        return self.s.enqueue(cid or self.c,text,key,allow)[0]
    def engine(self,p=None):return Engine(self.f,self.s,p or FakeProvider(),start=False)

class Safety(unittest.TestCase):
    def test_redact_nested(self):
        r=Redactor(["known-secret-123"])
        data=r({"options":{"openai_api_key":"abc","password":"def"},"log":"Bearer known-secret-123","content":"known-secret-123"})
        self.assertNotIn("known-secret-123",json.dumps(data))
        self.assertNotIn('"abc"',json.dumps(data));self.assertEqual(data["content"],"[REDACTED]")
    def test_json_truncation_is_valid_and_unicode_safe(self):
        text=bounded_json({"a":"🙂"*20000},2048)
        self.assertTrue(json.loads(text)["truncated"]);self.assertLess(len(text.encode()),2048)
    def test_json_limit_includes_escaped_preview(self):
        for text in ('"\\\\'*20000,"\\t\\r\\n"*20000,"🙂"*20000):
            encoded=bounded_json({"value":text},1024)
            self.assertTrue(json.loads(encoded)["truncated"])
            self.assertLessEqual(len(encoded.encode()),1024)
    def test_canonical_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/"secrets.yaml").write_text("secret");(root/"safe").write_text("okay")
            (root/"link").symlink_to(root/"secrets.yaml");(root/"outside").symlink_to("/etc/passwd")
            (root/".storage").mkdir();(root/".storage/auth_provider.homeassistant").write_text("secret")
            roots={"config":root};prefixes=("config/secrets.yaml","config/.storage/auth_provider")
            for path in ("config/foo/../secrets.yaml","config/link","config/outside","config/.storage/auth_provider.homeassistant"):
                with self.subTest(path=path),self.assertRaises(PermissionError):resolve_path(path,roots,prefixes,set())
            self.assertEqual(resolve_path("config/safe",roots,prefixes,set())[2],root/"safe")
            with self.assertRaises(PermissionError):resolve_path("config/safe",roots,(),{"config/safe"},allow=True)
    def test_rate_headers(self):
        self.assertAlmostEqual(retry_seconds({"Retry-After":"2.5"},0),2.75)
        self.assertAlmostEqual(retry_seconds({"x-ratelimit-reset-tokens":"1m2s"},0),62.25)

class Storage(Base):
    def test_dedup_and_conflicting_content(self):
        a=self.enqueue();b=self.enqueue()
        self.assertEqual(a["id"],b["id"]);self.assertEqual(len(self.s.view(self.c)["messages"]),1)
        with self.assertRaises(Conflict):self.enqueue("Different")
    def test_cross_chat_queue_and_busy(self):
        self.enqueue()
        with self.assertRaises(Conflict):self.enqueue(key="different-key")
        c=self.s.create()["id"];self.enqueue(cid=c,key="different-key")
        a=self.s.next_queued();self.assertEqual(a["conversation_id"],self.c)
        b=self.s.next_queued();self.assertEqual(b["conversation_id"],c)
    def test_queue_capacity(self):
        for i in range(8):
            c=self.s.create()["id"];self.enqueue(cid=c,key="capacity-key"+str(i))
        with self.assertRaises(Conflict):self.enqueue()
    def test_incremental_events_bounded(self):
        t=self.enqueue()
        self.s.event(t["id"],"tool_done","inspect",{"data":"x"*100000})
        first=self.s.task_view(t["id"],0);second=self.s.task_view(t["id"],first["cursor"])
        self.assertLess(len(json.dumps(first)),19000);self.assertEqual(second["events"],[])
    def test_conversation_does_not_expose_journal(self):
        self.s.update_conv(self.c,pending_turn={"secret":"not public"})
        view=self.s.view(self.c)
        self.assertNotIn("pending_turn",view);self.assertTrue(view["has_checkpoint"])
    def test_corrupt_legacy_fail_closed(self):
        legacy=self.root/"bad.json";legacy.write_text("{bad")
        with self.assertRaises(json.JSONDecodeError):Store(self.root/"bad.sqlite3",legacy)
        self.assertEqual(legacy.read_text(),"{bad")
        db=Store.__new__(Store) # don't mask original file by falling back to an empty initialized DB
        import sqlite3
        con=sqlite3.connect(self.root/"bad.sqlite3")
        self.assertIsNone(con.execute("SELECT value FROM meta WHERE key='initialized'").fetchone());con.close()
    def test_legacy_migration_once(self):
        legacy=self.root/"old.json"
        c={"id":"legacychat","title":"Keep me","created_at":"2026-09-12","messages":[{"role":"user","content":"Important request","at":"2026-09-12"}]}
        t={"id":"legacytask","conversation_id":"legacychat","status":"running","created_at":"2026-09-12","events":[{"kind":"test","message":"saved"}]}
        raw={"version":1,"conversations":{"legacychat":c},"tasks":{"legacytask":t}}
        legacy.write_text(json.dumps(raw));dbpath=self.root/"migrated.sqlite3"
        s=Store(dbpath,legacy)
        self.assertEqual(s.view("legacychat")["messages"][0]["content"],"Important request")
        self.assertEqual(s.task("legacytask")["status"],"interrupted")
        self.assertFalse(s.task("legacytask")["allow_changes"])
        counts=s.meta("migration")["counts"];self.assertEqual(counts,{"conversations":1,"messages":1,"tasks":1,"events":1})
        s.close();self.assertTrue(legacy.with_name(legacy.name+".pre-v0.2.0").exists())
        legacy.write_text("corrupt after successful migration")
        s=Store(dbpath,legacy);self.assertEqual(len(s.view("legacychat")["messages"]),1);s.close()
    def test_restart_retains_intent_no_automatic_execution(self):
        t=self.enqueue(allow=True);self.s.next_queued()
        self.s.accept_response(self.c,t["id"],response(calls=[call()]),[call()])
        self.s.tool_intent(self.c,call())
        self.s.recover_startup()
        self.assertEqual(self.s.task(t["id"])["status"],"interrupted")
        self.assertIsNotNone(self.s.conv(self.c)["pending_turn"]["in_progress"])
        self.assertIsNone(self.s.next_queued())
    def test_atomic_rollback(self):
        original=self.s.conv(self.c)
        with self.assertRaises(RuntimeError):
            with self.s.tx():
                c=self.s.conv(self.c);c["title"]="bad";self.s._put_conv(c);raise RuntimeError()
        self.assertEqual(self.s.conv(self.c),original)
    def test_archive_preserves_history(self):
        self.enqueue();self.s.cancel(self.s.tasks(self.c)[0]["id"]);self.s.update_conv(self.c,archived=True)
        self.assertEqual(self.s.conversations(),[])
        self.assertEqual(len(self.s.export(self.c)["messages"]),1)
        with self.assertRaises(Conflict):self.enqueue(key="new-after-archive")
    def test_message_pagination(self):
        with self.s.tx():
            for i in range(100):self.s._message(self.c,{"role":"user","content":str(i)})
        v=self.s.view(self.c);self.assertEqual(len(v["messages"]),80);self.assertTrue(v["has_more_messages"])
        more=self.s.view(self.c,v["messages"][0]["seq"]);self.assertEqual(len(more["messages"]),20)

class Recovery(Base):
    def test_read_only_task_denies_mutation(self):
        ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
        p=FakeProvider([response("resp_calls",[call()],"Inspecting."),response()])
        t=self.enqueue()
        self.engine(p).run(t["id"])
        self.assertEqual(ran,[]);self.assertEqual(self.s.task(t["id"])["status"],"completed")
        self.assertIn("authorization",p.payloads[1]["input"][0]["output"])
    def test_authorized_tool_once(self):
        ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
        p=FakeProvider([response("resp_calls",[call()]),response()])
        t=self.enqueue(allow=True)
        self.engine(p).run(t["id"])
        self.assertEqual(ran,[1]);self.assertIsNone(self.s.conv(self.c)["pending_turn"])
        self.assertEqual(self.s.conv(self.c)["last_response_id"],"resp_done")
        self.assertFalse(self.s.task(t["id"])["allow_changes"])
    def test_ambiguous_tool_retains_completed_and_skips_mutation_batch(self):
        calls=[call("inspect","first"),call("write_file","ambiguous"),call("write_file","later"),call("inspect","last")]
        t=self.enqueue(allow=True)
        self.s.accept_response(self.c,t["id"],response("resp_pending",calls),calls)
        known=output(calls[0],{"ok":True,"result":"keep this"})
        self.s.tool_result(self.c,known);self.s.tool_intent(self.c,calls[1])
        ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append("write"),"inspect":lambda a:ran.append("read")}
        e=self.engine();out=e.finish_tools(t["id"],self.c,lambda:None,lambda *a:None)
        self.assertEqual(ran,["read"]);self.assertEqual(out[0],known)
        self.assertTrue(json.loads(out[1]["output"])["unknown_effect"]);self.assertTrue(json.loads(out[2]["output"])["not_executed"])
    def test_cancel_between_tools(self):
        t=self.enqueue(allow=True);tid=t["id"]
        def first(a):self.s.cancel(tid);return "finished first"
        ran=[];self.f.TOOL_IMPL={"write_file":first,"inspect":lambda a:ran.append(1)}
        p=FakeProvider([response("resp_calls",[call(),call("inspect","call_2")])])
        self.engine(p).run(tid)
        self.assertEqual(ran,[]);self.assertEqual(self.s.task(tid)["status"],"cancelled")
        pending=self.s.conv(self.c)["pending_turn"]
        self.assertTrue(json.loads(pending["outputs"]["call_1"]["output"])["ok"])
        self.assertTrue(json.loads(pending["outputs"]["call_2"]["output"])["not_executed"])
    def test_known_provider_response_is_retrieved_not_resubmitted(self):
        t=self.enqueue();self.s.update_task(t["id"],status="interrupted")
        self.s.update_conv(self.c,pending_request={"response_id":"resp_saved","task_id":t["id"],"payload":{}})
        p=FakeProvider();p.saved["resp_saved"]=response("resp_saved",text="Recovered")
        e=self.engine(p);new=e.resume(t["id"],"resume-request",False);e.run(new["id"])
        self.assertEqual(p.payloads,[]);self.assertEqual(p.retrieved,["resp_saved"])
        self.assertEqual(self.s.task(new["id"])["status"],"completed")
    def test_unknown_provider_requires_explicit_resume(self):
        t=self.enqueue();self.s.update_task(t["id"],status="interrupted")
        self.s.update_conv(self.c,pending_request={"payload":{"input":"original"},"client_id":"old","task_id":t["id"]})
        with self.assertRaises(Conflict):self.enqueue("new message",key="new-message")
        p=FakeProvider([response()]);e=self.engine(p)
        new=e.resume(t["id"],"explicit-resume");e.run(new["id"])
        self.assertEqual(len(p.payloads),1)
    def test_incomplete_does_not_execute_calls(self):
        ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
        p=FakeProvider([response("resp_incomplete",[call()],status="incomplete")])
        t=self.enqueue(allow=True);self.engine(p).run(t["id"])
        self.assertEqual(ran,[]);self.assertEqual(self.s.task(t["id"])["status"],"failed")
    def test_expired_authorization_denied(self):
        ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
        t=self.enqueue(allow=True);self.s.update_task(t["id"],change_deadline=time.time()-1)
        out=self.engine().execute(t["id"],call(),lambda *a:None,lambda:None)
        self.assertEqual(ran,[]);self.assertIn("authorization",out["output"])
    def test_new_user_request_does_not_execute_stale_tools(self):
        old=self.enqueue(allow=True);calls=[call()]
        self.s.accept_response(self.c,old["id"],response("resp_old",calls),calls)
        self.s.update_task(old["id"],status="interrupted")
        ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
        new=self.enqueue("Actually, just explain what happened.",key="new-message",allow=True)
        p=FakeProvider([response(text="Explained")]);self.engine(p).run(new["id"])
        self.assertEqual(ran,[]);self.assertTrue(json.loads(p.payloads[0]["input"][0]["output"])["not_executed"])
        self.assertIn("Actually",p.payloads[0]["input"][1]["content"])
    def test_response_acceptance_clears_old_and_installs_new_atomically(self):
        t=self.enqueue()
        self.s.accept_response(self.c,t["id"],response("resp_1",[call()]),[call()])
        self.s.update_conv(self.c,pending_request={"response_id":"resp_2"})
        new=call("inspect","new")
        self.s.accept_response(self.c,t["id"],response("resp_2",[new]),[new])
        c=self.s.conv(self.c);self.assertIsNone(c["pending_request"]);self.assertEqual(c["pending_turn"]["calls"],[new])
    def test_queued_cancel_never_runs(self):
        t=self.enqueue(allow=True);self.s.cancel(t["id"])
        self.assertIsNone(self.s.next_queued());self.assertEqual(self.s.task(t["id"])["status"],"cancelled")
        self.assertFalse(self.s.task(t["id"])["allow_changes"]);self.assertEqual(self.s.task(t["id"])["change_deadline"],0)
    def test_cancelling_provider_does_not_poison_resume_forever(self):
        t=self.enqueue()
        self.s.update_conv(self.c,pending_request={"response_id":"resp_working"})
        self.s.cancel(t["id"])
        e=self.engine();e.run(t["id"])
        self.assertEqual(self.s.task(t["id"])["status"],"cancelled")
        self.assertIsNone(self.s.conv(self.c)["pending_request"])
    def test_resume_is_idempotent(self):
        t=self.enqueue();self.s.update_task(t["id"],status="interrupted")
        e=self.engine();a=e.resume(t["id"],"resume-key");b=e.resume(t["id"],"resume-key")
        self.assertEqual(a["id"],b["id"])
    def test_legacy_dangling_keeps_exact_parent(self):
        self.s.update_conv(self.c,last_response_id="resp_legacy",legacy_check=True)
        t=self.enqueue()
        p=FakeProvider([response()]);p.saved["resp_legacy"]=response("resp_legacy",[call()])
        self.engine(p).run(t["id"])
        self.assertEqual(p.payloads[0]["previous_response_id"],"resp_legacy")
        self.assertTrue(json.loads(p.payloads[0]["input"][0]["output"])["unknown_effect"])

class ProviderTests(unittest.TestCase):
    def test_malformed_success_is_ambiguous_and_not_retried(self):
        for value in ({},{"id":"resp_weird","status":"surprise"},[]):
            count=[]
            def opener(*a,**kw):
                count.append(1);return io.BytesIO(json.dumps(value).encode())
            with self.subTest(value=value),self.assertRaises(AmbiguousRequest):
                Provider(tools(),opener).create({},"id",lambda:None,lambda *a:None)
            self.assertEqual(len(count),1)
    def test_ambiguous_post_not_automatically_retried(self):
        count=[]
        def opener(*a,**kw):count.append(1);raise TimeoutError()
        p=Provider(tools(),opener)
        with self.assertRaises(AmbiguousRequest):p.create({},"id",lambda:None,lambda *a:None)
        self.assertEqual(len(count),1)
    def test_explicit_quota_error_does_not_wait(self):
        def opener(*a,**kw):raise urllib.error.HTTPError("url",429,"quota",{},io.BytesIO(b'{"error":{"code":"insufficient_quota"}}'))
        p=Provider(tools(),opener)
        with self.assertRaises(ProviderError):p.create({},"id",lambda:None,lambda *a:None)
    def test_cancel_interrupts_backoff(self):
        def check():raise Cancelled()
        start=time.monotonic()
        with self.assertRaises(Cancelled):Provider.pause(100,check)
        self.assertLess(time.monotonic()-start,.1)
    def test_client_id_and_background_payload_transport(self):
        captured=[]
        def opener(req,timeout):
            captured.append(req)
            return io.BytesIO(json.dumps(response()).encode())
        p=Provider(tools(),opener);p.create({"background":True},"test-client-id",lambda:None,lambda *a:None)
        self.assertEqual(captured[0].get_header("X-client-request-id"),"test-client-id")
        self.assertTrue(json.loads(captured[0].data)["background"])
    def test_artifact_identifier_validation(self):
        p=Provider(tools(),lambda *a,**kw:self.fail("Must not fetch arbitrary URLs"))
        with self.assertRaises(ProviderError):p.artifact({"container_id":"https://evil","file_id":"../../secret"})
    def test_artifact_fixed_origin(self):
        captured=[]
        def opener(req,timeout):captured.append(req);return io.BytesIO(b"file-content")
        p=Provider(tools(),opener)
        self.assertEqual(p.artifact({"container_id":"cntr_test","file_id":"cfile_test"}),b"file-content")
        self.assertEqual(captured[0].full_url,"https://api.invalid/v1/containers/cntr_test/files/cfile_test/content")
    def test_poll_network_does_not_recreate(self):
        count=[]
        def opener(req,timeout):
            count.append(req.method)
            if len(count)<2:raise TimeoutError()
            return io.BytesIO(json.dumps(response()).encode())
        p=Provider(tools(),opener)
        with patch.object(p,"pause",return_value=None):r=p.retrieve("resp_known",lambda:None,lambda *a:None)
        self.assertEqual(count,["GET","GET"]);self.assertEqual(r["id"],"resp_done")

class HTTP(Base):
    def setUp(self):
        super().setUp();self.engine_=self.engine()
        self.server=AppServer(("127.0.0.1",0),self.engine_,allowed_peers={"127.0.0.1"})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base="http://127.0.0.1:"+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    def request(self,path,body=None,csrf=True):
        headers={"Content-Type":"application/json"}
        if csrf:headers["X-Forge-CSRF"]=self.server.csrf
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r),r.headers
        except urllib.error.HTTPError as r:
            with r:return r.code,json.load(r),r.headers
    def test_status_no_credentials(self):
        code,data,headers=self.request("/api/status")
        self.assertEqual(code,200);self.assertNotIn(self.f.OPENAI_API_KEY,json.dumps(data));self.assertIn("Content-Security-Policy",headers)
    def test_csrf_required(self):
        code,data,_=self.request("/api/conversations",{},False);self.assertEqual(code,403)
    def test_peer_headers_cannot_bypass(self):
        self.server.allowed_peers=frozenset({"172.30.32.2"})
        code,data,_=self.request("/api/status");self.assertEqual(code,403)
        req=urllib.request.Request(self.base+"/api/status",headers={"X-Forwarded-For":"172.30.32.2","X-Ingress-Path":"/"})
        with self.assertRaises(urllib.error.HTTPError) as err:urllib.request.urlopen(req)
        self.assertEqual(err.exception.code,403)
        err.exception.close()
    def test_correct_not_found_and_conflict(self):
        self.assertEqual(self.request("/not-found")[0],404)
        payload={"text":"inspect","request_id":"key-123456"}
        self.assertEqual(self.request("/api/conversations/"+self.c+"/messages",payload)[0],202)
        payload["request_id"]="key-new123";self.assertEqual(self.request("/api/conversations/"+self.c+"/messages",payload)[0],409)
    def test_static_csp_and_paths(self):
        with urllib.request.urlopen(self.base+"/") as r:
            text=r.read().decode();self.assertIn("./app.js",text);self.assertIn("script-src 'self'",r.headers["Content-Security-Policy"])
        self.assertEqual(self.request("/../forge.py")[0],404)
    def test_boolean_authorization_strict(self):
        code,_,_=self.request("/api/conversations/"+self.c+"/messages",
            {"text":"inspect","request_id":"key-123456","allow_changes":"false"})
        self.assertEqual(code,400)
    def test_request_reconciliation_is_readonly(self):
        t=self.enqueue()
        code,data,_=self.request("/api/conversations/"+self.c+"/requests/request-12345")
        self.assertEqual(data["result"]["id"],t["id"])
        code,data,_=self.request("/api/conversations/"+self.c+"/requests/missing12345")
        self.assertIsNone(data["result"]);self.assertEqual(len(self.s.tasks(self.c)),1)
    def raw(self,headers,body=b""):
        sock=socket.create_connection(("127.0.0.1",self.server.server_port))
        sock.sendall((f"POST /api/conversations HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nX-Forge-CSRF: {self.server.csrf}\r\n"+headers+"\r\n").encode()+body)
        sock.shutdown(socket.SHUT_WR);out=b""
        while True:
            chunk=sock.recv(4096)
            if not chunk:break
            out+=chunk
        sock.close();return int(out.split(b" ")[1])
    def test_chunked_ingress(self):
        self.assertEqual(self.raw("Transfer-Encoding: chunked\r\n",b"2\r\n{}\r\n0\r\n\r\n"),201)
    def test_framing_rejections(self):
        cases=[("Content-Length: -1\r\n",b""),
               ("Content-Length: 2\r\nTransfer-Encoding: chunked\r\n",b"{}"),
               ("Content-Length: 2\r\nContent-Length: 2\r\n",b"{}"),
               ("Transfer-Encoding: chunked\r\n",b"-1\r\n"),
               ("Transfer-Encoding: chunked\r\n",b"\r\n")]
        for h,b in cases:
            with self.subTest(h=h):self.assertEqual(self.raw(h,b),400)
    def test_artifact_must_be_recorded_and_typed(self):
        self.assertEqual(self.request("/api/messages/999/artifacts/0")[0],404)
        with self.s.tx():
            self.s._message(self.c,{"role":"assistant","content":"link","annotations":[{"type":"url_citation","url":"https://example.com"}]})
        seq=self.s.view(self.c)["messages"][-1]["seq"]
        self.assertEqual(self.request(f"/api/messages/{seq}/artifacts/0")[0],404)
    def test_body_size(self):
        self.assertEqual(self.raw("Content-Length: 70001\r\n"),413)

if __name__=="__main__":unittest.main()
