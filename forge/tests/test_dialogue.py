"""Dialogue barriers and Stop & send: temp stores, mocked provider/tools, loopback HTTP only."""
import io
import json
import threading
import urllib.error
import urllib.request
import unittest
from unittest.mock import patch
import test_forge as fx
from forge_store import Store, Conflict
from forge_runtime import AppServer, ASK_USER_TOOL, WaitingForInput, output
from forge_provider import Provider


def ask(question="Which language?", ident="ask_1"):
    return {"type":"function_call","name":"ask_user","call_id":ident,
            "arguments":json.dumps({"question":question})}


class QuestionBarrier(fx.Base):
    def waiting(self, calls=None, allow=True):
        t=self.enqueue(allow=allow)
        p=fx.FakeProvider([fx.response("resp_question", calls or [ask()], text="")])
        self.engine(p).run(t["id"])
        self.assertEqual(self.s.task(t["id"])["status"],"waiting_input")
        return t,p

    def test_question_blocks_all_unstarted_siblings_even_reads(self):
        ran=[]
        self.f.TOOL_IMPL={"inspect":lambda a:ran.append("read"),"write_file":lambda a:ran.append("write")}
        calls=[fx.call("inspect","read_1"),ask(),fx.call("write_file","write_1")]
        t,p=self.waiting(calls)
        self.assertEqual(ran,[]);self.assertEqual(len(p.payloads),1)
        task=self.s.task(t["id"])
        self.assertFalse(task["allow_changes"]);self.assertEqual(task["change_deadline"],0)
        journal=self.s.conv(self.c)["pending_turn"]
        self.assertEqual(len(journal["outputs"]),3);self.assertIsNone(journal["in_progress"])
        self.assertTrue(json.loads(journal["outputs"]["ask_1"]["output"])["awaiting_user"])
        for cid in ("read_1","write_1"):
            self.assertTrue(json.loads(journal["outputs"][cid]["output"])["not_executed"])
        message=self.s.view(self.c)["messages"][-1]
        self.assertTrue(message["waiting_input"]);self.assertEqual(message["content"],"Which language?")
        self.assertIsNone(self.s.next_queued())

    def test_question_at_last_model_round_still_enters_waiting(self):
        self.f.MAX_TOOL_ROUNDS=1
        t,p=self.waiting()
        self.assertEqual(len(p.payloads),1)
        self.assertFalse(self.s.task(t["id"])["allow_changes"])

    def test_read_only_question_never_creates_permission(self):
        t,_=self.waiting(allow=False)
        self.assertFalse(self.s.task(t["id"])["allow_changes"])
        self.assertFalse(self.s.view(self.c)["allow_changes_default"])

    def test_waiting_preserves_saved_human_choice_but_not_task_grant(self):
        self.s.set_change_preference(self.c,True,0)
        t,_=self.waiting()
        self.assertTrue(self.s.view(self.c)["allow_changes_default"])
        self.assertFalse(self.s.task(t["id"])["allow_changes"])

    def test_reply_uses_saved_outputs_and_new_answer_no_stale_actions(self):
        ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
        old,_=self.waiting([ask(),fx.call("write_file","stale_write")])
        reply=self.enqueue("English",key="reply-english-1")
        p=fx.FakeProvider([fx.response("resp_reply")]);self.engine(p).run(reply["id"])
        self.assertEqual(ran,[]);self.assertEqual(len(p.payloads),1)
        payload=p.payloads[0]
        self.assertEqual(payload["previous_response_id"],"resp_question")
        self.assertEqual(payload["input"][0]["call_id"],"ask_1")
        self.assertIn("English",payload["input"][-1]["content"])
        self.assertIn("Skipped/uncertain",payload["input"][-1]["content"])
        self.assertEqual(self.s.task(reply["id"])["status"],"completed")
        self.assertEqual(self.s.task(old["id"])["status"],"waiting_input")

    def test_answer_text_does_not_authorize_new_mutation(self):
        self.waiting();ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
        reply=self.enqueue("Yes, do it",key="reply-no-permission")
        p=fx.FakeProvider([fx.response("resp_write",[fx.call()],text=""),fx.response()])
        self.engine(p).run(reply["id"])
        self.assertEqual(ran,[])
        self.assertIn("authorization",p.payloads[1]["input"][0]["output"])

    def test_waiting_survives_reopen_without_dispatch_or_renewal(self):
        t,_=self.waiting();path=self.s.path;self.s.close();self.s=Store(path)
        self.assertEqual(self.s.task(t["id"])["status"],"waiting_input")
        self.assertEqual(self.s.task(t["id"])["change_deadline"],0)
        self.assertIsNone(self.s.next_queued())
        reply=self.enqueue("English",key="after-restart-answer")
        self.engine(fx.FakeProvider([fx.response()])).run(reply["id"])
        self.assertEqual(self.s.task(reply["id"])["status"],"completed")

    def test_waiting_requires_reply_not_resume(self):
        t,_=self.waiting()
        with self.assertRaises(Conflict):self.engine().resume(t["id"],"invalid-resume-wait")

    def test_invalid_question_skips_siblings_and_model_can_repair(self):
        for raw in ("{bad", "[]", '{}', '{"question":true}', '{"question":" "}',
                    '{"question":"okay","allow_changes":true}',json.dumps({"question":"x"*4001})):
            with self.subTest(raw=raw[:45]):
                c=self.s.create()["id"];t=self.enqueue(cid=c,key="bad-question-key")
                malformed=ask();malformed["arguments"]=raw
                ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
                p=fx.FakeProvider([fx.response("resp_bad",[fx.call(),malformed],text=""),
                                   fx.response("resp_fixed",[ask()],text="")])
                self.engine(p).run(t["id"])
                self.assertEqual(ran,[]);self.assertEqual(self.s.task(t["id"])["status"],"waiting_input")
                self.assertEqual(len(p.payloads),2)
                for item in p.payloads[1]["input"]:
                    self.assertTrue(json.loads(item["output"])["not_executed"])

    def test_multiple_questions_are_rejected_without_sibling_dispatch(self):
        ran=[];self.f.TOOL_IMPL={"inspect":lambda a:ran.append(1)}
        p=fx.FakeProvider([fx.response("resp_bad",[ask(ident="a"),fx.call("inspect","r"),ask(ident="b")],text=""),
                           fx.response("resp_fixed",[ask()],text="")])
        t=self.enqueue();self.engine(p).run(t["id"])
        self.assertEqual(ran,[]);self.assertEqual(self.s.task(t["id"])["status"],"waiting_input")
        self.assertIn("exactly one",p.payloads[1]["input"][0]["output"])

    def test_question_redacted_before_message_and_tool_output(self):
        self.s.redact=self.f.REDACT
        self.waiting([ask("Keep test-not-a-real-secret private?")])
        for obj in (self.s.view(self.c)["messages"], self.s.conv(self.c)["pending_turn"]["outputs"]):
            self.assertNotIn("test-not-a-real-secret",json.dumps(obj))
            self.assertIn("[REDACTED]",json.dumps(obj))

    def test_cancel_wins_before_atomic_wait_commit(self):
        t=self.enqueue();self.s.next_queued();calls=[ask()]
        self.s.accept_response(self.c,t["id"],fx.response("resp_ask",calls,text=""),calls)
        self.s.cancel(t["id"])
        result=self.s.wait_for_input(self.c,t["id"],"Question?",{},"resp_ask")
        self.assertFalse(result)
        self.assertFalse(any(m.get("waiting_input") for m in self.s.view(self.c)["messages"]))

    def test_completed_and_uncertain_outputs_are_kept_without_replay(self):
        t=self.enqueue(allow=True);self.s.next_queued()
        calls=[fx.call("inspect","known"),fx.call("write_file","uncertain"),ask()]
        self.s.accept_response(self.c,t["id"],fx.response("resp_ask",calls,text=""),calls)
        known=output(calls[0],{"ok":True,"result":"verified earlier"})
        self.s.tool_result(self.c,known);self.s.tool_intent(self.c,calls[1])
        ran=[];self.f.TOOL_IMPL={"inspect":lambda a:ran.append(1),"write_file":lambda a:ran.append(2)}
        with self.assertRaises(WaitingForInput):
            self.engine().finish_tools(t["id"],self.c,lambda:None,lambda *a:None)
        journal=self.s.conv(self.c)["pending_turn"]
        self.assertEqual(ran,[]);self.assertEqual(journal["outputs"]["known"],known)
        self.assertTrue(json.loads(journal["outputs"]["uncertain"]["output"])["unknown_effect"])

    def test_old_unstarted_question_is_not_asked_after_new_message(self):
        t=self.enqueue();self.s.next_queued();calls=[ask("Old irrelevant question?")]
        self.s.accept_response(self.c,t["id"],fx.response("resp_old",calls,text=""),calls)
        self.s.update_task(t["id"],status="interrupted",allow_changes=False,change_deadline=0)
        reply=self.enqueue("Different request",key="supersede-question")
        p=fx.FakeProvider([fx.response()]);self.engine(p).run(reply["id"])
        self.assertEqual(self.s.task(reply["id"])["status"],"completed")
        self.assertTrue(json.loads(p.payloads[0]["input"][0]["output"])["not_executed"])
        self.assertFalse(any(m.get("waiting_input") for m in self.s.view(self.c)["messages"]))

    def test_tool_is_runtime_owned_strict_and_always_advertised(self):
        payload=self.engine().payload(self.c,[],None)
        defs=[x for x in payload["tools"] if x.get("name")=="ask_user"]
        self.assertEqual(defs,[ASK_USER_TOOL]);self.assertTrue(defs[0]["strict"])
        self.assertFalse(defs[0]["parameters"]["additionalProperties"])
        self.assertIn("question in a progress message does NOT pause",payload["instructions"])
        self.assertNotIn("ask_user",self.f.TOOL_IMPL)


class ActiveUpdates(fx.Base):
    def running(self,allow=True):
        t=self.enqueue(allow=allow);self.s.next_queued();return t
    def update(self,old,text="English",key="new-update-key",allow=False,**kw):
        return self.s.enqueue(self.c,text,key,allow,interrupt_task_id=old["id"],**kw)[0]

    def test_update_atomically_cancels_old_and_owns_new_permission(self):
        old=self.running()
        with patch("time.time",return_value=123000):new=self.update(old,allow=True)
        stopped=self.s.task(old["id"])
        self.assertTrue(stopped["cancel_requested"]);self.assertEqual(stopped["status"],"cancelling")
        self.assertFalse(stopped["allow_changes"]);self.assertEqual(stopped["change_deadline"],0)
        self.assertTrue(new["allow_changes"]);self.assertEqual(new["change_deadline"],126600)
        self.assertEqual(new["interrupts_task_id"],old["id"]);self.assertTrue(new["steering_pending"])
        self.assertEqual(self.s.view(self.c)["messages"][-1]["content"],"English")
        self.assertFalse(self.s.view(self.c)["allow_changes_default"])
        self.assertEqual(self.s.public_task(new)["interrupts_task_id"],old["id"])

    def test_scheduler_wont_pick_reply_while_old_same_chat_is_active(self):
        old=self.running();new=self.update(old)
        self.assertIsNone(self.s.next_queued())
        other=self.s.create()["id"];other_task=self.enqueue(cid=other,key="other-chat-key")
        self.assertEqual(self.s.next_queued()["id"],other_task["id"])
        self.s.update_task(old["id"],status="cancelled")
        self.assertEqual(self.s.next_queued()["id"],new["id"])

    def test_stale_permission_rejection_does_not_cancel_old_task(self):
        old=self.running();before=self.s.task(old["id"])
        self.s.set_change_preference(self.c,True,0)
        with self.assertRaises(Conflict):self.update(old,allow=False,allow_changes_revision=0)
        self.assertEqual(self.s.task(old["id"]),before);self.assertEqual(len(self.s.tasks(self.c)),1)

    def test_retry_preserves_accepted_update_and_deadline(self):
        old=self.running();self.s.set_change_preference(self.c,True,0)
        new=self.update(old,allow=True,allow_changes_revision=1)
        self.s.set_change_preference(self.c,False,1)
        with patch("time.time",return_value=new["change_deadline"]+999):
            again=self.update(old,allow=True,allow_changes_revision=1)
        self.assertEqual(again,new);self.assertEqual(len(self.s.tasks(self.c)),2)
        with self.assertRaises(Conflict):self.update(new,allow=True,allow_changes_revision=1)
        with self.assertRaises(Conflict):self.update(old,text="Different",allow=True,allow_changes_revision=1)

    def test_second_update_cannot_silently_cancel_queued_answer(self):
        old=self.running();new=self.update(old)
        with self.assertRaises(Conflict):self.update(new,key="second-update-key")
        self.assertFalse(self.s.task(new["id"])["cancel_requested"])

    def test_wrong_chat_or_stale_id_does_not_cancel_current_work(self):
        old=self.running();other=self.s.create()["id"]
        with self.assertRaises(Conflict):
            self.s.enqueue(other,"Answer","cross-chat-update",False,interrupt_task_id=old["id"])
        with self.assertRaises(Conflict):self.update({"id":"obsolete-task"})
        self.assertFalse(self.s.task(old["id"])["cancel_requested"])

    def test_invalid_interrupt_types_and_resume_combination_rejected(self):
        old=self.running()
        for bad in (True,3,[],{},"../escape",""):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                self.s.enqueue(self.c,"Answer","bad-interrupt-key",interrupt_task_id=bad)
        with self.assertRaises(ValueError):
            self.s.enqueue(self.c,"Answer","bad-resume-key",resumed_from=old["id"],interrupt_task_id=old["id"])
        self.assertFalse(self.s.task(old["id"])["cancel_requested"])

    def test_completed_race_still_accepts_explicit_update_once(self):
        old=self.running();self.s.update_task(old["id"],status="completed",allow_changes=False,change_deadline=0)
        new=self.update(old)
        self.assertEqual(new["status"],"queued")
        self.assertFalse(self.s.task(old["id"])["cancel_requested"])

    def test_queue_full_failure_leaves_old_running(self):
        old=self.running()
        for i in range(7):self.enqueue(cid=self.s.create()["id"],key="full-queue-"+str(i))
        with self.assertRaises(Conflict):self.update(old)
        self.assertFalse(self.s.task(old["id"])["cancel_requested"])
        self.assertTrue(self.s.task(old["id"])["allow_changes"])

    def test_queued_old_can_be_replaced_at_queue_capacity(self):
        old=self.enqueue(allow=True)
        for i in range(7):self.enqueue(cid=self.s.create()["id"],key="full-queue-"+str(i))
        new=self.update(old)
        self.assertEqual(self.s.task(old["id"])["status"],"cancelled")
        self.assertEqual(new["status"],"queued")

    def test_attachment_binding_failure_does_not_cancel_old(self):
        old=self.running();before=self.s.task(old["id"])
        with self.assertRaises((ValueError,KeyError,Conflict)):
            self.update(old,attachment_ids=["missing_"+"a"*24])
        self.assertEqual(self.s.task(old["id"]),before)

    def test_reply_attachment_is_saved_and_bound_atomically(self):
        old=self.running();e=self.engine()
        attachment=e.attachments.stage(self.c,"answer.txt",b"English","upload-answer-key")
        new=self.update(old,attachment_ids=[attachment["id"]])
        self.assertEqual(new["attachment_ids"],[attachment["id"]])
        self.assertEqual(self.s.view(self.c)["messages"][-1]["attachments"][0]["id"],attachment["id"])

    def test_inflight_tool_finishes_once_then_answer_uses_its_saved_result(self):
        entered=threading.Event();release=threading.Event();ran=[]
        def first(args):
            entered.set()
            if not release.wait(5):raise RuntimeError("test release timed out")
            ran.append("first");return "first known effect"
        self.f.TOOL_IMPL={"write_file":first,"inspect":lambda a:ran.append("stale")}
        p=fx.FakeProvider([fx.response("resp_tools",[fx.call("write_file","first"),fx.call("inspect","stale")],text=""),fx.response()])
        e=self.engine(p);old=self.enqueue(allow=True)
        worker=threading.Thread(target=e.run,args=(old["id"],))
        worker.start()
        try:
            self.assertTrue(entered.wait(3));new=self.update(old)
            self.assertIsNone(self.s.next_queued());self.assertEqual(len(p.payloads),1)
        finally:
            release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(ran,["first"])
        self.assertEqual(self.s.task(old["id"])["status"],"cancelled")
        self.assertEqual(self.s.next_queued()["id"],new["id"]);e.run(new["id"])
        self.assertEqual(ran,["first"])
        values=p.payloads[-1]["input"]
        self.assertIn("first known effect",values[0]["output"])
        self.assertTrue(json.loads(values[1]["output"])["not_executed"])
        self.assertIn("English",values[-1]["content"])

    def test_unknown_provider_post_accepts_answer_but_never_auto_resubmits(self):
        old=self.running()
        self.s.update_conv(self.c,pending_request={"payload":{},"client_id":"client_old","task_id":old["id"],"state":"submitting"})
        new=self.update(old);self.s.update_task(old["id"],status="interrupted")
        p=fx.FakeProvider([]);self.engine(p).run(new["id"])
        self.assertEqual(p.payloads,[]);self.assertEqual(p.retrieved,[])
        self.assertEqual(self.s.task(new["id"])["status"],"interrupted")
        self.assertIn("Resume",self.s.task(new["id"])["error"])
        self.assertTrue(self.s.task(new["id"])["steering_pending"])
        self.assertEqual(self.s.view(self.c)["messages"][-1]["content"],"English")

    def test_known_provider_response_is_retrieved_not_reposted(self):
        old=self.running()
        self.s.update_conv(self.c,pending_request={"payload":{},"response_id":"resp_old","task_id":old["id"]})
        new=self.update(old);self.s.update_task(old["id"],status="cancelled")
        p=fx.FakeProvider([fx.response("resp_answer")]);p.saved["resp_old"]=fx.response("resp_old",[fx.call("write_file","old_action")],text="")
        ran=[];self.f.TOOL_IMPL={"write_file":lambda a:ran.append(1)}
        self.engine(p).run(new["id"])
        self.assertEqual(p.retrieved,["resp_old"]);self.assertEqual(len(p.payloads),1);self.assertEqual(ran,[])
        self.assertTrue(json.loads(p.payloads[0]["input"][0]["output"])["not_executed"])
        self.assertIn("English",p.payloads[0]["input"][-1]["content"])

    def test_restart_retains_update_but_revokes_both_and_does_not_dispatch(self):
        old=self.running();new=self.update(old,allow=True)
        path=self.s.path;self.s.close();self.s=Store(path)
        for t in (old,new):
            recovered=self.s.task(t["id"])
            self.assertEqual(recovered["status"],"interrupted")
            self.assertFalse(recovered["allow_changes"]);self.assertEqual(recovered["change_deadline"],0)
        self.assertIsNone(self.s.next_queued())
        self.assertEqual(self.s.tasks(self.c,1)[0]["user_text"],"English")
        self.assertTrue(self.s.task(new["id"])["steering_pending"])

    def test_regular_cancel_revokes_running_grant_immediately(self):
        old=self.running();self.s.cancel(old["id"])
        stopped=self.s.task(old["id"])
        self.assertEqual(stopped["status"],"cancelling")
        self.assertFalse(stopped["allow_changes"]);self.assertEqual(stopped["change_deadline"],0)


class NoArtificialThrottle(fx.Base):
    def test_successful_submissions_have_no_sleep_or_rate_queue(self):
        captured=[]
        def opener(req,timeout):captured.append(req);return io.BytesIO(json.dumps(fx.response()).encode())
        p=Provider(self.f,opener)
        with patch.object(p,"pause",side_effect=AssertionError("No delay on success")):
            for i in range(3):p.create({},"client-"+str(i),lambda:None,lambda *a:None)
        self.assertEqual([r.method for r in captured],["POST"]*3)


class DialogueHTTP(fx.Base):
    def setUp(self):
        super().setUp();self.engine_=self.engine()
        self.server=AppServer(("127.0.0.1",0),self.engine_,allowed_peers={"127.0.0.1"})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base="http://127.0.0.1:"+str(self.server.server_port)
        self.url="/api/conversations/"+self.c+"/messages"
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    def request(self,data,csrf=True):
        headers={"Content-Type":"application/json"}
        if csrf:headers["X-Forge-CSRF"]=self.server.csrf
        req=urllib.request.Request(self.base+self.url,data=json.dumps(data).encode(),headers=headers)
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as r:
            with r:return r.code,json.load(r)
    def test_active_update_route_keeps_peer_and_csrf_guards(self):
        old=self.enqueue(allow=True);self.s.next_queued()
        data={"text":"English","request_id":"http-update-key","interrupt_task_id":old["id"],"allow_changes":False,"allow_changes_revision":0}
        self.assertEqual(self.request(data,False)[0],403)
        self.server.allowed_peers=frozenset({"172.30.32.2"})
        self.assertEqual(self.request(data)[0],403)
        self.assertFalse(self.s.task(old["id"])["cancel_requested"])
        self.server.allowed_peers=frozenset({"127.0.0.1"})
        code,result=self.request(data);self.assertEqual(code,202)
        self.assertEqual(result["result"]["interrupts_task_id"],old["id"])
        self.assertFalse(result["result"]["allow_changes"])
    def test_missing_explicit_interrupt_keeps_old_active_task(self):
        old=self.enqueue();self.s.next_queued()
        data={"text":"English","request_id":"no-interrupt-key"}
        self.assertEqual(self.request(data)[0],409)
        self.assertFalse(self.s.task(old["id"])["cancel_requested"])
    def test_wrong_interrupt_type_rejected_before_cancellation(self):
        old=self.enqueue();self.s.next_queued()
        data={"text":"English","request_id":"bad-http-update","interrupt_task_id":True}
        self.assertEqual(self.request(data)[0],400)
        self.assertFalse(self.s.task(old["id"])["cancel_requested"])


if __name__=="__main__":unittest.main()
