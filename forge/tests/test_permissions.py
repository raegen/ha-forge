"""Persistent UI preference tests: temporary databases and isolated loopback HTTP only."""
import json
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import test_forge as fx
from forge_store import Store,Conflict
from forge_runtime import AppServer

class ConversationPreferences(fx.Base):
    def pref(self,value=True,revision=0,cid=None):
        return self.s.set_change_preference(cid or self.c,value,revision)
    def test_new_and_legacy_chats_start_unchecked(self):
        self.assertIs(self.s.view(self.c)["allow_changes_default"],False)
        self.assertEqual(self.s.view(self.c)["allow_changes_revision"],0)
        with self.s.tx():
            c=self.s.conv(self.c);c.pop("allow_changes_default");c.pop("allow_changes_revision");self.s._put_conv(c)
        t=self.enqueue(allow=True)
        self.assertTrue(t["allow_changes"])
        self.assertIs(self.s.view(self.c)["allow_changes_default"],False)
        self.assertEqual(self.s.view(self.c)["allow_changes_revision"],0)
    def test_choice_persists_across_store_reopen_and_is_per_conversation(self):
        other=self.s.create()["id"]
        self.pref();path=self.s.path;self.s.close();self.s=Store(path)
        self.assertTrue(self.s.view(self.c)["allow_changes_default"])
        self.assertFalse(self.s.view(other)["allow_changes_default"])
        self.pref(False,1);self.s.close();self.s=Store(path)
        self.assertFalse(self.s.view(self.c)["allow_changes_default"])
        self.assertEqual(self.s.view(self.c)["allow_changes_revision"],2)
    def test_public_list_view_and_export_include_choice(self):
        self.pref()
        for c in (self.s.conversations()[0],self.s.view(self.c),self.s.export(self.c)["conversation"]):
            self.assertTrue(c["allow_changes_default"]);self.assertEqual(c["allow_changes_revision"],1)
    def test_preference_alone_never_creates_work_or_grants(self):
        self.pref()
        self.assertEqual(self.s.tasks(self.c),[]);self.assertEqual(self.s.view(self.c)["messages"],[])
        self.assertFalse(self.enqueue()["allow_changes"])
    def test_change_does_not_modify_or_extend_existing_grant(self):
        t=self.enqueue(allow=True);before=self.s.task(t["id"])
        self.pref();self.pref(False,1)
        self.assertEqual(self.s.task(t["id"]),before)
    def test_strict_preference_types_and_revisions(self):
        for value in (1,0,"true","false",None,[],{}):
            with self.subTest(value=value),self.assertRaises(ValueError):self.pref(value)
        for revision in (True,False,-1,1.0,"0",None,2**53):
            with self.subTest(revision=revision),self.assertRaises(ValueError):self.pref(revision=revision)
        self.assertFalse(self.s.view(self.c)["allow_changes_default"])
    def test_stale_toggle_cannot_overwrite_later_uncheck(self):
        self.pref();self.pref(False,1)
        for revision in (0,1):
            with self.assertRaises(Conflict):self.pref(True,revision)
        self.assertFalse(self.s.view(self.c)["allow_changes_default"])
    def test_same_value_is_noop_but_stale_revision_still_rejected(self):
        self.pref(False,0);self.assertEqual(self.s.view(self.c)["allow_changes_revision"],0)
        self.pref();self.pref(True,1)
        self.assertEqual(self.s.view(self.c)["allow_changes_revision"],1)
        with self.assertRaises(Conflict):self.pref(True,0)
    def test_archiving_preserves_choice_but_blocks_changes(self):
        self.pref();self.s.update_conv(self.c,archived=True)
        with self.assertRaises(Conflict):self.pref(False,1)
        self.s.update_conv(self.c,archived=False)
        self.assertTrue(self.s.view(self.c)["allow_changes_default"])
    def test_explicit_new_prompt_gets_bounded_grant_and_keeps_choice(self):
        self.pref()
        with patch("time.time",return_value=123000):
            t=self.engine().submit(self.c,"inspect","explicit-new-prompt",True,allow_changes_revision=1)
        self.assertTrue(t["allow_changes"]);self.assertEqual(t["change_deadline"],126600)
        self.engine(fx.FakeProvider([fx.response()])).run(t["id"])
        finished=self.s.task(t["id"])
        self.assertFalse(finished["allow_changes"]);self.assertEqual(finished["change_deadline"],0)
        self.assertTrue(self.s.view(self.c)["allow_changes_default"])
        next_task=self.engine().submit(self.c,"inspect again","another-prompt-key",True,allow_changes_revision=1)
        self.assertTrue(next_task["allow_changes"])
    def test_stale_checked_submission_is_rejected_after_other_browser_unchecks(self):
        self.pref();self.pref(False,1)
        with self.assertRaises(Conflict):
            self.engine().submit(self.c,"inspect","stale-request-key",True,allow_changes_revision=1)
        self.assertEqual(self.s.tasks(self.c),[])
    def test_explicit_flag_must_match_new_ui_snapshot(self):
        self.pref()
        with self.assertRaises(Conflict):
            self.engine().submit(self.c,"inspect","wrong-flag-key",False,allow_changes_revision=1)
    def test_retry_returns_original_without_renewal_even_after_preference_changes(self):
        self.pref()
        t=self.engine().submit(self.c,"inspect","retry-request-key",True,allow_changes_revision=1)
        self.pref(False,1)
        with patch("time.time",return_value=t["change_deadline"]+100):
            retry=self.engine().submit(self.c,"inspect","retry-request-key",True,allow_changes_revision=1)
        self.assertEqual(t,retry);self.assertEqual(len(self.s.tasks(self.c)),1)
    def test_expired_task_is_not_renewed_by_saved_preference(self):
        self.pref();t=self.enqueue(allow=True)
        self.s.update_task(t["id"],change_deadline=1)
        ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
        result=self.engine().execute(t["id"],fx.call(),lambda *a:None,lambda:None)
        self.assertFalse(json.loads(result["output"])["ok"]);self.assertEqual(ran,[])
        self.assertTrue(self.s.view(self.c)["allow_changes_default"])
    def test_restart_clears_live_grant_not_preference_and_resume_stays_readonly(self):
        self.pref();t=self.enqueue(allow=True);self.s.next_queued();path=self.s.path
        self.s.close();self.s=Store(path)
        interrupted=self.s.task(t["id"])
        self.assertEqual(interrupted["status"],"interrupted");self.assertFalse(interrupted["allow_changes"])
        self.assertEqual(interrupted["change_deadline"],0);self.assertTrue(self.s.view(self.c)["allow_changes_default"])
        self.assertIsNone(self.s.next_queued())
        resumed=self.engine().resume(t["id"],"readonly-resume-key")
        self.assertFalse(resumed["allow_changes"]);self.assertEqual(resumed["change_deadline"],0)
    def test_resume_explicit_checked_grant_is_still_bounded(self):
        self.pref();t=self.enqueue();self.s.cancel(t["id"])
        with patch("time.time",return_value=321000):
            resumed=self.engine().resume(t["id"],"checked-resume-key",True)
        self.assertTrue(resumed["allow_changes"]);self.assertEqual(resumed["change_deadline"],324600)
    def test_cancel_keeps_preference_but_revokes_queued_task(self):
        self.pref();t=self.enqueue(allow=True);self.s.cancel(t["id"])
        self.assertFalse(self.s.task(t["id"])["allow_changes"])
        self.assertTrue(self.s.view(self.c)["allow_changes_default"])

class PreferenceHTTP(fx.Base):
    def setUp(self):
        super().setUp();self.engine_=self.engine()
        self.server=AppServer(("127.0.0.1",0),self.engine_,allowed_peers={"127.0.0.1"})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base="http://127.0.0.1:"+str(self.server.server_port)
        self.url="/api/conversations/"+self.c+"/change-preference"
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    def request(self,path,body=None,csrf=True):
        headers={"Content-Type":"application/json"}
        if csrf:headers["X-Forge-CSRF"]=self.server.csrf
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as r:
            with r:return r.code,json.load(r)
    def payload(self):return {"allow_changes_default":True,"allow_changes_revision":0}
    def test_saved_choice_roundtrip_and_read_only_get(self):
        code,data=self.request(self.url,self.payload());self.assertEqual(code,200)
        self.assertTrue(data["result"]["allow_changes_default"])
        for _ in range(2):
            code,data=self.request("/api/conversations/"+self.c)
            self.assertEqual(data["result"]["allow_changes_revision"],1)
        self.assertEqual(self.s.tasks(self.c),[])
    def test_preference_route_requires_actual_peer_and_csrf(self):
        self.assertEqual(self.request(self.url,self.payload(),False)[0],403)
        self.server.allowed_peers=frozenset({"172.30.32.2"})
        self.assertEqual(self.request(self.url,self.payload())[0],403)
        self.assertFalse(self.s.view(self.c)["allow_changes_default"])
    def test_preference_payload_is_strict_and_revision_checked(self):
        for data in ({},{"allow_changes_default":True},{"allow_changes_revision":0},
                     {**self.payload(),"extra":1},{**self.payload(),"allow_changes_default":"false"},
                     {**self.payload(),"allow_changes_revision":True}):
            with self.subTest(data=data):self.assertEqual(self.request(self.url,data)[0],400)
        self.assertEqual(self.request(self.url,self.payload())[0],200)
        self.assertEqual(self.request(self.url,self.payload())[0],409)
    def test_messages_require_explicit_flag_never_inherit_default(self):
        self.request(self.url,self.payload())
        code,data=self.request("/api/conversations/"+self.c+"/messages",{"text":"inspect","request_id":"omitted-flag-key"})
        self.assertEqual(code,202);self.assertFalse(data["result"]["allow_changes"])
    def test_new_ui_submission_rejects_stale_revision_and_invalid_types(self):
        self.request(self.url,self.payload())
        path="/api/conversations/"+self.c+"/messages"
        payload={"text":"inspect","request_id":"revision-http-key","allow_changes":True,"allow_changes_revision":0}
        self.assertEqual(self.request(path,payload)[0],409)
        for revision in (True,-1,1.0,None,"1",2**53):
            with self.subTest(revision=revision):
                self.assertEqual(self.request(path,{**payload,"allow_changes_revision":revision})[0],400)
        self.assertEqual(self.request(path,{**payload,"allow_changes_revision":1})[0],202)
    def test_preference_not_set_by_generic_metadata_or_message(self):
        self.request("/api/conversations/"+self.c,self.payload())
        self.assertFalse(self.s.view(self.c)["allow_changes_default"])
        code,data=self.request("/api/conversations/"+self.c+"/messages",{
            "text":"inspect","request_id":"manual-old-ui-key","allow_changes":True})
        self.assertEqual(code,202);self.assertTrue(data["result"]["allow_changes"])
        self.assertFalse(self.s.view(self.c)["allow_changes_default"])
    def test_permissions_script_is_served_with_same_ingress_csp(self):
        with urllib.request.urlopen(self.base+"/permissions.js") as r:
            self.assertIn(b"class ConversationPermission",r.read())
            self.assertIn("script-src 'self'",r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["Cache-Control"],"no-store")
    def test_new_page_wires_helper_but_resume_remains_explicit(self):
        static=Path(__file__).resolve().parents[1]/"static"
        page=(static/"index.html").read_text();script=(static/"app.js").read_text()
        self.assertLess(page.index("./permissions.js"),page.index("./app.js"))
        self.assertIn('addEventListener("change",()=>permissions.save())',script)
        self.assertIn('$("resume-changes").checked=false',script)
        self.assertNotIn('$("allow-changes").checked=false',script)
        self.assertIn('allow_changes_revision:permissions.revision',script)

if __name__=="__main__":unittest.main()
