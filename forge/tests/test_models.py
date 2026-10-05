"""Model discovery/selection regressions. All providers and data are isolated fixtures."""
import io
import json
import threading
import unittest
import urllib.error
from unittest.mock import patch
import test_forge as fixtures
from forge_models import ModelCatalog, profile, clean_settings, defaults, apply_capabilities
from forge_provider import Provider, ProviderError, NoModelRedirect
from forge_store import Conflict


def setting(model="gpt-5.5", effort="high"):
    return {"model": model, "reasoning_effort": effort}


class Inventory(fixtures.FakeProvider):
    def __init__(self, rows=None, responses=()):
        super().__init__(responses)
        self.rows=rows if rows is not None else [{"id":"gpt-5.5"},{"id":"gpt-4.1"}]
        self.reads=0
    def list_models(self):
        self.reads+=1
        if isinstance(self.rows,Exception):raise self.rows
        return self.rows


class Profiles(unittest.TestCase):
    def test_documented_efforts(self):
        cases={"gpt-6-astra":["low","medium","high","xhigh","max"],
               "gpt-6.1-sol":["low","medium","high","xhigh","max"],
               "gpt-6-sol":["none","low","medium","high","xhigh","max"],
               "gpt-5.6-terra":["none","low","medium","high","xhigh","max"],
               "gpt-5.5":["none","low","medium","high","xhigh"],
               "gpt-5.4-mini":["none","low","medium","high","xhigh"],
               "gpt-5.2":["none","low","medium","high","xhigh"],
               "gpt-5.1":["none","low","medium","high"],
               "gpt-5-mini":["minimal","low","medium","high"],
               "gpt-4.1":[],"gpt-4o-mini":[],"o3":["low","medium","high"]}
        for model,efforts in cases.items():
            with self.subTest(model=model):self.assertEqual(profile(model)["efforts"],efforts)
    def test_snapshot_and_specialized_exclusion(self):
        self.assertEqual(profile("gpt-5.5-2026-04-23"),profile("gpt-5.5"))
        for model in ("gpt-5.5-pro","gpt-5.5-codex","gpt-5.5-instruct","gpt-audio","text-embedding-3-small","gpt-6-new"):
            self.assertIsNone(profile(model))
    def test_strict_selection_schema(self):
        for value in (None,[],{},setting("../secret"),setting("a\n"),setting("a"*161),setting(effort=True),
                      setting(effort="max"),setting("gpt-4.1","high"),{**setting(),"allow_changes":True}):
            with self.subTest(value=value),self.assertRaises(ValueError):clean_settings(value)
        self.assertEqual(clean_settings(setting("gpt-4.1",None)),setting("gpt-4.1",None))
    def test_nonreasoning_default(self):
        f=fixtures.tools();f.MODEL="gpt-4.1"
        self.assertIsNone(defaults(f)["reasoning_effort"])
    def test_payload_removes_unsupported_fields(self):
        payload={"model":"old","reasoning":{},"text":{"verbosity":"high"},"context_management":[{}],
                 "tools":[{"type":"function"},{"type":"web_search"},{"type":"code_interpreter"}],"previous_response_id":"resp_keep"}
        apply_capabilities(payload,setting("gpt-4.1-nano",None),fixtures.tools())
        self.assertNotIn("reasoning",payload);self.assertNotIn("text",payload);self.assertNotIn("context_management",payload)
        self.assertEqual(payload["tools"],[{"type":"function"}]);self.assertEqual(payload["previous_response_id"],"resp_keep")
    def test_none_omits_summary(self):
        p=apply_capabilities({"tools":[]},setting(effort="none"),fixtures.tools())
        self.assertEqual(p["reasoning"],{"effort":"none"})
    def test_minimal_omits_web(self):
        p=apply_capabilities({"tools":[{"type":"web_search"},{"type":"function"}]},setting("gpt-5","minimal"),fixtures.tools())
        self.assertEqual(p["tools"],[{"type":"function"}])
    def test_unknown_configured_contract_preserved(self):
        p=apply_capabilities({"tools":[]},setting("custom-admin-model","max"),fixtures.tools())
        self.assertEqual(p["reasoning"],{"effort":"max","summary":"auto"})


class Catalog(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.tools();self.p=Inventory();self.clock=[0]
        self.c=ModelCatalog(self.f,self.p,lambda:self.clock[0])
    def test_bounded_inventory_filter_and_no_owner_leak(self):
        self.p.rows=[{"id":"gpt-5.5","owned_by":"private-owner"},{"id":"gpt-5.5"},
                     {"id":"gpt-audio"},{"id":"../secret"},{"id":"gpt-4.1"},None,"bad"]
        r=self.c.get();self.assertEqual(r["total_count"],3);self.assertEqual(r["compatible_count"],2)
        self.assertNotIn("private-owner",json.dumps(r));self.assertEqual(self.p.payloads,[])
    def test_cache_and_refresh_cooldown(self):
        self.c.get();self.c.get(True);self.clock[0]=29;self.c.get(True);self.assertEqual(self.p.reads,1)
        self.clock[0]=30;self.c.get(True);self.assertEqual(self.p.reads,2)
        self.clock[0]=329;self.c.get();self.assertEqual(self.p.reads,2)
        self.clock[0]=330;self.c.get();self.assertEqual(self.p.reads,3)
    def test_failed_refresh_retains_rows_and_backoff(self):
        self.c.get();self.p.rows=ProviderError("Temporary outage");self.clock[0]=300
        r=self.c.get();self.assertTrue(r["stale"]);self.assertEqual(r["compatible_count"],2)
        self.clock[0]=359;self.c.get();self.assertEqual(self.p.reads,2)
        self.clock[0]=360;self.c.get();self.assertEqual(self.p.reads,3)
    def test_single_flight_concurrent_get(self):
        threads=[threading.Thread(target=self.c.get) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(self.p.reads,1)
    def test_no_key_does_not_call_provider(self):
        self.f.OPENAI_API_KEY="";r=self.c.get();self.assertTrue(r["stale"]);self.assertEqual(self.p.reads,0)
    def test_error_redaction(self):
        self.p.rows=ProviderError(self.f.OPENAI_API_KEY)
        self.assertNotIn(self.f.OPENAI_API_KEY,json.dumps(self.c.get()))
    def test_generic_exception_does_not_leak(self):
        self.p.rows=RuntimeError("private details")
        self.assertNotIn("private details",json.dumps(self.c.get()))
    def test_unusable_envelopes(self):
        for rows in ([],{},[{}]*5001):
            with self.subTest(rows_type=type(rows).__name__):
                c=ModelCatalog(self.f,Inventory(rows));self.assertTrue(c.get()["stale"])
    def test_results_are_copies(self):
        r=self.c.get();r["models"].clear();self.assertEqual(self.c.get()["compatible_count"],2)
    def test_expired_model_disabled(self):
        self.p.rows=[{"id":"gpt-5.5","shutdown_date":"2000-01-01"}]
        self.assertFalse(next(r for r in self.c.get()["models"] if r["id"]=="gpt-5.5")["available"])
        with self.assertRaises(ValueError):self.c.validate(setting())
    def test_configured_default_available_without_network(self):
        self.assertEqual(self.c.validate(defaults(self.f)),defaults(self.f));self.assertEqual(self.p.reads,0)
    def test_unknown_or_unlisted_rejected(self):
        for s in (setting("gpt-5.4"),setting("custom-model"),setting("mock-model","low")):
            with self.subTest(s=s),self.assertRaises(ValueError):self.c.validate(s)
    def test_listed_nonreasoning_allowed(self):
        self.assertEqual(self.c.validate(setting("gpt-4.1",None)),setting("gpt-4.1",None))


class Transport(unittest.TestCase):
    def test_fixed_get_origin_and_auth_server_only(self):
        captured=[]
        def opener(req,timeout):
            captured.append((req,timeout));return io.BytesIO(b'{"data":[{"id":"gpt-5.5"}]}')
        p=Provider(fixtures.tools(),opener);self.assertEqual(p.list_models(),[{"id":"gpt-5.5"}])
        req,timeout=captured[0];self.assertEqual(req.full_url,"https://api.openai.com/v1/models")
        self.assertEqual(req.method,"GET");self.assertIsNone(req.data);self.assertEqual(timeout,10)
        self.assertTrue(req.get_header("Authorization").startswith("Bearer "))
    def test_size_and_envelope_bounds(self):
        for raw in (b"x"*(1024*1024+1),b"{}",b'{"data":{}}',b"not-json"):
            with self.subTest(size=len(raw)),self.assertRaises(ProviderError):
                Provider(fixtures.tools(),lambda *a,**k:io.BytesIO(raw)).list_models()
    def test_http_errors_are_sanitized_not_retried_and_closed(self):
        for code in (401,403,429,500):
            stream=io.BytesIO(b"private account details");calls=[]
            def opener(*a,**kw):
                calls.append(1);raise urllib.error.HTTPError("url",code,"private details",{},stream)
            with self.assertRaises(ProviderError) as error:Provider(fixtures.tools(),opener).list_models()
            self.assertNotIn("private",str(error.exception));self.assertIn(str(code),str(error.exception))
            self.assertEqual(len(calls),1);self.assertTrue(stream.closed)
    def test_redirect_rejected_before_credentials_forwarded(self):
        with self.assertRaises(ProviderError):
            NoModelRedirect().redirect_request(None,None,302,"",{},"https://untrusted.invalid")
    def test_no_key_no_network(self):
        f=fixtures.tools();f.OPENAI_API_KEY=""
        with self.assertRaises(ProviderError):Provider(f,lambda *a,**kw:self.fail("network")).list_models()


class Snapshots(fixtures.Base):
    def test_new_task_snapshots_default(self):
        e=self.engine(Inventory());t=e.submit(self.c,"inspect","model-default")
        self.assertEqual(t["model_settings"],defaults(self.f));self.assertFalse(t["allow_changes"])
        self.assertEqual(self.s.view(self.c)["model_settings"],defaults(self.f))
    def test_selected_model_frozen_across_turns(self):
        p=Inventory(responses=[fixtures.response("resp_tools",[fixtures.call("inspect")]),fixtures.response()])
        e=self.engine(p)
        self.f.TOOL_IMPL={"inspect":lambda a:setattr(self.f,"MODEL","gpt-4.1")}
        t=e.submit(self.c,"inspect","model-frozen",model_settings=setting())
        e.run(t["id"])
        self.assertEqual(len(p.payloads),2)
        for payload in p.payloads:self.assertEqual(payload["model"],"gpt-5.5");self.assertEqual(payload["reasoning"]["effort"],"high")
    def test_dedup_does_not_refresh_or_renew(self):
        p=Inventory();e=self.engine(p);t=e.submit(self.c,"inspect","model-dedup",True,model_settings=setting())
        deadline=self.s.task(t["id"])["change_deadline"];p.rows=ProviderError("offline")
        e.models.checked=float("-inf")
        again=e.submit(self.c,"inspect","model-dedup",True,model_settings=setting())
        self.assertEqual(again["id"],t["id"]);self.assertEqual(p.reads,1)
        self.assertEqual(self.s.task(t["id"])["change_deadline"],deadline)
    def test_dedup_different_settings_conflict(self):
        e=self.engine(Inventory());e.submit(self.c,"inspect","model-conflict",model_settings=setting())
        with self.assertRaises(Conflict):e.submit(self.c,"inspect","model-conflict",model_settings=setting(effort="low"))
    def test_legacy_retry_omitting_settings_returns_original(self):
        e=self.engine(Inventory());t=e.submit(self.c,"inspect","model-legacy",model_settings=setting())
        self.assertEqual(e.submit(self.c,"inspect","model-legacy")["model_settings"],t["model_settings"])
    def test_resume_keeps_original_and_no_grant(self):
        p=Inventory();e=self.engine(p);t=e.submit(self.c,"inspect","model-resume",True,model_settings=setting())
        self.s.update_task(t["id"],status="interrupted");self.f.MODEL="gpt-4.1";p.rows=ProviderError("offline")
        resumed=e.resume(t["id"],"model-resume-next")
        self.assertEqual(resumed["model_settings"],setting());self.assertFalse(resumed["allow_changes"]);self.assertEqual(p.reads,1)
    def test_invalid_selection_does_not_cancel_old_task(self):
        e=self.engine(Inventory());t=e.submit(self.c,"inspect","model-old-task")
        with self.assertRaises(ValueError):e.submit(self.c,"update","model-new-task",interrupt_task_id=t["id"],model_settings=setting(effort="max"))
        self.assertFalse(self.s.task(t["id"])["cancel_requested"])
    def test_interrupt_new_task_keeps_independent_selection(self):
        e=self.engine(Inventory());t=e.submit(self.c,"inspect","model-interrupt",model_settings=setting())
        n=e.submit(self.c,"update","model-successor",interrupt_task_id=t["id"],model_settings=setting("gpt-4.1",None))
        self.assertEqual(self.s.task(t["id"])["model_settings"],setting());self.assertEqual(n["model_settings"],setting("gpt-4.1",None))
    def test_selection_persists_after_restart_recovery(self):
        e=self.engine(Inventory());t=e.submit(self.c,"inspect","model-recover",True,model_settings=setting())
        self.s.recover_startup();saved=self.s.task(t["id"])
        self.assertEqual(saved["model_settings"],setting());self.assertEqual(saved["status"],"interrupted");self.assertFalse(saved["allow_changes"])


class ModelHTTP(fixtures.Base):
    def setUp(self):
        super().setUp();self.engine_=self.engine()
        self.server=fixtures.AppServer(("127.0.0.1",0),self.engine_,allowed_peers={"127.0.0.1"})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base="http://127.0.0.1:"+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    request=fixtures.HTTP.request
    def test_endpoint_inventory_and_private_cache(self):
        self.engine_.models.provider=Inventory()
        code,data,headers=self.request("/api/models?refresh=1")
        self.assertEqual(code,200);self.assertEqual(data["result"]["compatible_count"],2)
        self.assertIn("no-store",headers["Cache-Control"]);self.assertNotIn(self.f.OPENAI_API_KEY,json.dumps(data))
    def test_model_endpoint_requires_actual_peer(self):
        p=Inventory();self.engine_.models.provider=p;self.server.allowed_peers=frozenset({"172.30.32.2"})
        self.assertEqual(self.request("/api/models")[0],403);self.assertEqual(p.reads,0)
    def test_bad_effort_is_400_and_creates_no_task(self):
        self.engine_.models.provider=Inventory()
        self.assertEqual(self.request("/api/conversations/"+self.c+"/messages",{
            "text":"inspect","request_id":"model-http-test","model_settings":setting(effort="max")})[0],400)
        self.assertEqual(self.s.tasks(self.c),[])
    def test_models_script_and_icon_served_with_csp(self):
        import urllib.request
        for path,mime in (("/models.js","javascript"),("/forge.svg","image/svg+xml")):
            with urllib.request.urlopen(self.base+path) as r:
                self.assertIn(mime,r.headers["Content-Type"]);self.assertIn("script-src 'self'",r.headers["Content-Security-Policy"])


if __name__=="__main__":unittest.main()
