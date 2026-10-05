"""SSH regressions: temporary credentials only; no real provider or appliance access."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch
from forge_ssh import SSHConnections, clean_connection, public_key, _atomic, _file
from forge_store import Conflict
from forge_provider import Cancelled
from forge_runtime import AppServer
from test_forge import Base, call

BLOB = struct.pack(">I",11)+b"ssh-ed25519"+struct.pack(">I",32)+bytes(range(32))
PUB = "ssh-ed25519 "+base64.b64encode(BLOB).decode()
HOST_BLOB = struct.pack(">I",11)+b"ssh-ed25519"+struct.pack(">I",32)+bytes(range(32,64))
HOST = "ssh-ed25519 "+base64.b64encode(HOST_BLOB).decode()
CID = "a"*32

def payload(**changes):
    data={"request_id":CID,"name":"App source","host":"github.com","port":22,"username":"git","repository":"owner/project.git"}
    data.update(changes);return data

class Fixture:
    def __init__(self):self.calls=[];self.failure=None;self.refs="1"*40+"\trefs/heads/main\n"
    def run(self,args,cwd,env,timeout,check=lambda:None,budget=None):
        check();self.calls.append((args,Path(cwd),dict(env),timeout,budget))
        if self.failure:raise self.failure
        if "-t" in args and "-N" in args:
            key=Path(args[args.index("-f")+1]);key.write_text("PRIVATE-TEST-FIXTURE-NOT-A-REAL-KEY\n");key.chmod(0o600)
            key.with_suffix(".pub").write_text(PUB+" forge-test\n")
            return 0,"",""
        if "ssh-keyscan" in args[0]:return 0,"github.com "+HOST+"\n","# untrusted server banner"
        if "ls-remote" in args:return 0,self.refs,""
        if "clone" in args:(Path(args[-1])/"README.md").write_text("fixture source, not executed\n");return 0,"",""
        raise AssertionError("Unexpected fixture command "+repr(args))

class SSHBase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.m=SSHConnections(self.root/"ssh");self.fixture=Fixture()
        self.run_patch=patch.object(self.m,"_run",side_effect=self.fixture.run);self.run_patch.start();self.addCleanup(self.run_patch.stop)
        self.binary_patch=patch.object(self.m,"_binary",side_effect=lambda n:"/fixture/"+n);self.binary_patch.start();self.addCleanup(self.binary_patch.stop)
    def tearDown(self):self.tmp.cleanup()
    def create(self):return self.m.create(payload())
    def ready(self):
        entry=self.create()
        return self.m.pin(CID,{"revision":entry["revision"],"public_key":HOST,"expected_fingerprint":public_key(HOST)["fingerprint"]})

class Validation(unittest.TestCase):
    def test_valid_hosts(self):
        for host in ["github.com","Git.Example.NET","10.0.0.2","::1","2001:db8::1","xn--caf-dma.example"]:
            with self.subTest(host=host):self.assertTrue(clean_connection(payload(host=host))["host"])
    def test_host_rejects_commands_urls_alias_lists_and_ranges(self):
        for host in ["-oProxyCommand=id","github.com;id","example.com\nHost *","a,b","a b","https://github.com","git@github.com","[::1]","127.0.0.0/8","a..b","foo.","*.example.com","$(id)","a%h",""]:
            with self.subTest(host=host),self.assertRaises(ValueError):clean_connection(payload(host=host))
    def test_repo_scope_cannot_inject_command_or_escape(self):
        for repo in ["../secret","/owner/repo","owner/../repo","-o/foo","owner/-x","owner//repo","owner/repo;id","owner/repo'", "owner/repo\n", "file:///tmp/repo","x~user","."]:
            with self.subTest(repo=repo),self.assertRaises(ValueError):clean_connection(payload(repository=repo))
    def test_port_user_and_name_validation(self):
        for field,values in {"port":[True,"22",0,-1,65536],"username":["-o","git@x","a;id","a\n","root/evil"],"name":["", "x"*81,"a\x00b"]}.items():
            for value in values:
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):clean_connection(payload(**{field:value}))
    def test_strict_fields_and_request_ids(self):
        for changes in [{"request_id":"../../data"},{"private_key":"hidden"},{"enabled":True},{"port":None}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):clean_connection(payload(**changes))
        data=payload();del data["host"]
        with self.assertRaises(ValueError):clean_connection(data)
    def test_public_key_fingerprint_and_comment(self):
        self.assertEqual(public_key(PUB),public_key(PUB+" fixture"))
        self.assertRegex(public_key(PUB)["fingerprint"],r"^SHA256:[A-Za-z0-9+/]{43}$")
    def test_public_key_parser_rejects_cert_options_private_and_trailing_data(self):
        for value in ["command=evil "+PUB,PUB+"\n",PUB+" x y","ssh-rsa AAAA","-----BEGIN OPENSSH PRIVATE KEY-----","ssh-ed25519 BAD!","ssh-ed25519 "+base64.b64encode(BLOB+b"x").decode(),"ssh-ed25519 "+base64.b64encode(BLOB[:-1]).decode()]:
            with self.subTest(value=value),self.assertRaises(ValueError):public_key(value)

class Persistence(SSHBase):
    def test_status_is_lazy_and_has_no_side_effect(self):
        self.assertEqual(self.m.listing()["connections"],[])
        self.assertFalse(self.m.root.exists());self.assertFalse(self.fixture.calls)
    def test_creation_permissions_and_public_only_view(self):
        entry=self.create();directory=self.m.root/CID
        self.assertEqual(entry["state"],"ready");self.assertEqual(entry["public_key"],PUB)
        for p in [self.m.root,directory]:self.assertEqual(p.stat().st_mode&0o777,0o700)
        for p in directory.iterdir():self.assertEqual(p.stat().st_mode&0o777,0o600)
        encoded=json.dumps(self.m.listing());self.assertNotIn("PRIVATE-TEST",encoded);self.assertNotIn("id_ed25519",encoded);self.assertNotIn(str(self.root),encoded)
    def test_duplicate_create_reuses_key_and_same_response(self):
        first=self.create();second=self.create()
        self.assertEqual(first,second);self.assertEqual(len(self.fixture.calls),1)
    def test_new_instance_keeps_identity_without_keygen(self):
        first=self.create();again=SSHConnections(self.m.root)
        with patch.object(again,"_run",side_effect=AssertionError("No regeneration")):
            self.assertEqual(first,again.create(payload()))
            self.assertEqual(again.listing()["connections"][0]["fingerprint"],first["fingerprint"])
    def test_duplicate_with_changed_repository_is_conflict(self):
        self.create()
        with self.assertRaises(Conflict):self.m.create(payload(repository="other/repo.git"))
        self.assertEqual(len(self.fixture.calls),1)
    def test_failed_creation_retains_incomplete_record_no_retry(self):
        self.fixture.failure=TimeoutError("fixture timeout")
        with self.assertRaises(TimeoutError):self.create()
        self.fixture.failure=None
        entry=self.create();self.assertEqual(entry["state"],"creating");self.assertFalse(entry["enabled"])
        self.assertEqual(len(self.fixture.calls),1)
        with self.assertRaises(Conflict):self.m.set_enabled(CID,{"revision":0,"enabled":True})
    def test_connection_limit_preserves_existing(self):
        self.create()
        with patch("forge_ssh.MAX_CONNECTIONS",1),self.assertRaises(Conflict):self.m.create(payload(request_id="b"*32))
        self.assertEqual(len(self.m.listing()["connections"]),1)
    def test_missing_dependencies_do_not_allocate_key_record(self):
        with patch.object(self.m,"_binary",side_effect=Conflict("missing")),self.assertRaises(Conflict):self.create()
        self.assertFalse(self.m.root.exists())
    def test_symlink_root_and_id_rejected(self):
        other=self.root/"other";other.mkdir();self.m.root.symlink_to(other,target_is_directory=True)
        with self.assertRaises(PermissionError):self.create()
        self.assertFalse(list(other.iterdir()))
    def test_private_key_symlink_and_world_readable_rejected(self):
        self.ready();key=self.m.root/CID/"id_ed25519"
        key.chmod(0o644)
        with self.assertRaises(PermissionError):self.m.refs(CID)
        key.chmod(0o600);outside=self.root/"other";outside.write_text("preserved");key.unlink();key.symlink_to(outside)
        with self.assertRaises(PermissionError):self.m.refs(CID)
        self.assertEqual(outside.read_text(),"preserved")
    def test_hardlinked_credentials_rejected(self):
        self.ready();key=self.m.root/CID/"id_ed25519";os.link(key,self.root/"hardlink")
        with self.assertRaises(PermissionError):self.m.refs(CID)
    def test_metadata_corruption_is_not_overwritten(self):
        self.create();p=self.m.root/CID/"connection.json";p.write_text('{"version":99}')
        with self.assertRaises(Conflict):self.create()
        self.assertEqual(p.read_text(),'{"version":99}')
    def test_stored_metadata_is_revalidated_before_use(self):
        self.ready();p=self.m.root/CID/"connection.json";original=p.read_text();entry=json.loads(original)
        for field,value in [("host","-oProxyCommand=evil"),("repository","../secret"),("port",True),("username","git;id"),("revision",True),("enabled","yes"),("state","bad"),("fingerprint","wrong"),("host_fingerprint","wrong")]:
            with self.subTest(field=field):
                changed={**entry,field:value};p.write_text(json.dumps(changed))
                with self.assertRaises(Conflict):self.m.refs(CID)
                self.assertEqual(json.loads(p.read_text()),changed)
        p.write_text(original)
        self.assertTrue(self.m.refs(CID)["authenticated"])
    def test_writable_directory_rejected_without_repair(self):
        self.ready();directory=self.m.root/CID;directory.chmod(0o777)
        with self.assertRaises(PermissionError):self.m.refs(CID)
        self.assertEqual(directory.stat().st_mode&0o777,0o777)
    def test_connection_ids_never_become_paths(self):
        for cid in ["../secret","/data","x"*32,"a"*31,1,None]:
            with self.subTest(cid=cid),self.assertRaises(ValueError):self.m.refs(cid)
    def test_atomic_write_never_follows_symlink(self):
        target=self.root/"target";target.write_text("keep");link=self.root/"link";link.symlink_to(target)
        with self.assertRaises(PermissionError):_atomic(link,"replace")
        self.assertEqual(target.read_text(),"keep")

class TrustAndGit(SSHBase):
    def test_no_authentication_before_explicit_host_trust(self):
        self.create()
        with self.assertRaises(PermissionError):self.m.refs(CID)
        self.assertEqual(len(self.fixture.calls),1)
    def test_scan_is_untrusted_and_does_not_mutate_metadata(self):
        entry=self.create();result=self.m.scan(CID,{})
        self.assertFalse(result["trusted"]);self.assertEqual(result["candidates"][0]["public_key"],HOST)
        self.assertEqual(self.m.listing()["connections"][0],entry)
        args=self.fixture.calls[-1][0];self.assertEqual(args[-1],"github.com");self.assertIn("ed25519",args)
    def test_scan_does_not_accept_arbitrary_hosts_or_commands(self):
        self.create()
        with self.assertRaises(ValueError):self.m.scan(CID,{"host":"evil"})
        self.assertEqual(len(self.fixture.calls),1)
    def test_pin_requires_independently_supplied_matching_fingerprint(self):
        entry=self.create()
        with self.assertRaises(ValueError):self.m.pin(CID,{"revision":1,"public_key":HOST,"expected_fingerprint":public_key(PUB)["fingerprint"]})
        self.assertEqual(self.m.listing()["connections"][0],entry)
    def test_pin_durable_but_not_private_key_import(self):
        entry=self.ready();self.assertEqual(entry["host_fingerprint"],public_key(HOST)["fingerprint"])
        self.assertEqual(SSHConnections(self.m.root).listing()["connections"][0],entry)
        with self.assertRaises(ValueError):self.m.pin(CID,{"revision":2,"public_key":HOST,"expected_fingerprint":entry["host_fingerprint"],"private_key":"injected"})
    def test_stale_revision_cannot_reenable_or_replace_trust(self):
        self.ready();self.m.set_enabled(CID,{"revision":2,"enabled":False})
        with self.assertRaises(Conflict):self.m.set_enabled(CID,{"revision":2,"enabled":True})
        with self.assertRaises(Conflict):self.m.pin(CID,{"revision":2,"public_key":PUB,"expected_fingerprint":public_key(PUB)["fingerprint"]})
        self.assertFalse(self.m.listing()["connections"][0]["enabled"])
    def test_disable_preserves_key_and_rejects_future_operations(self):
        self.ready();key=self.m.root/CID/"id_ed25519";original=key.read_bytes()
        self.m.set_enabled(CID,{"revision":2,"enabled":False})
        for operation in [lambda:self.m.refs(CID),lambda:self.m.scan(CID,{})]:
            with self.assertRaises(Conflict):operation()
        self.assertEqual(key.read_bytes(),original)
        entry=self.m.set_enabled(CID,{"revision":3,"enabled":True});self.assertTrue(entry["enabled"])
    def test_enable_payload_strict(self):
        self.create()
        for data in [{"revision":1,"enabled":"yes"},{"revision":True,"enabled":False},{"revision":1,"enabled":False,"grant":True}]:
            with self.subTest(data=data),self.assertRaises(ValueError):self.m.set_enabled(CID,data)
    def test_refs_read_only_and_bounded_public_result(self):
        self.ready();result=self.m.refs(CID)
        self.assertTrue(result["authenticated"]);self.assertFalse(result["remote_write_tested"])
        self.assertEqual(result["refs"][0]["ref"],"refs/heads/main")
        args=self.fixture.calls[-1][0];self.assertIn("ls-remote",args);self.assertNotIn("push",args)
        self.assertEqual(args[-1],"ssh://git@github.com:22/owner/project.git")
    def test_refs_empty_repo_is_valid(self):
        self.ready();self.fixture.refs="";self.assertTrue(self.m.refs(CID)["authenticated"])
    def test_refs_limit_and_malformed_remote_output(self):
        self.ready();self.fixture.refs="".join("1"*40+"\trefs/heads/branch"+str(i)+"\n" for i in range(250))
        result=self.m.refs(CID);self.assertEqual(len(result["refs"]),200);self.assertTrue(result["truncated"])
        for text in ["banner\n","x\trefs/heads/main\n","1"*40+"\trefs/heads/evil\x1b[0m\n"]:
            self.fixture.refs=text
            with self.assertRaises(ValueError):self.m.refs(CID)
    def test_ssh_and_git_ignore_ambient_config_and_credentials(self):
        self.ready()
        with patch.dict(os.environ,{"SUPERVISOR_TOKEN":"do-not-pass","SSH_AUTH_SOCK":"agent","GIT_CONFIG_COUNT":"1","GIT_SSH_COMMAND":"evil"}):self.m.refs(CID)
        args,cwd,env,_,_=self.fixture.calls[-1]
        for key in ["SUPERVISOR_TOKEN","SSH_AUTH_SOCK","GIT_CONFIG_COUNT","OPENAI_API_KEY"]:self.assertNotIn(key,env)
        ssh=shlex.split(env["GIT_SSH_COMMAND"])
        for option in ["StrictHostKeyChecking=yes","IdentityAgent=none","IdentitiesOnly=yes","ProxyCommand=none","ProxyJump=none","GlobalKnownHostsFile=/dev/null","UpdateHostKeys=no","PermitLocalCommand=no"]:self.assertIn(option,ssh)
        self.assertIn("/dev/null",ssh);self.assertEqual(env["GIT_CONFIG_GLOBAL"],"/dev/null");self.assertEqual(env["GIT_ALLOW_PROTOCOL"],"ssh")
        self.assertIn("core.hooksPath=/dev/null",args);self.assertIn("submodule.recurse=false",args)
        self.assertFalse(cwd.exists())  # Per-operation public host/config files cleaned.
    def test_known_hosts_uses_exact_host_and_port(self):
        self.ready();entry=self.m._ready(CID)
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);self.m._git(entry,home)
            self.assertEqual((home/"known_hosts").read_text(),"github.com "+HOST+"\n")
            entry["port"]=2222;self.m._git(entry,home)
            self.assertEqual((home/"known_hosts").read_text(),"[github.com]:2222 "+HOST+"\n")
    def test_failed_remote_does_not_expose_banner_or_stderr(self):
        self.ready()
        for message in ["Permission denied. PRIVATE-TEST", "REMOTE HOST IDENTIFICATION HAS CHANGED PRIVATE-TEST","untrusted banner PRIVATE-TEST"]:
            with patch.object(self.m,"_run",return_value=(128,"",message)),self.assertRaises(ValueError) as err:self.m.refs(CID)
            self.assertNotIn("PRIVATE-TEST",str(err.exception))
    def test_only_one_network_operation_at_a_time(self):
        self.ready();self.m.network.acquire()
        try:
            with self.assertRaises(Conflict):self.m.refs(CID)
            with self.assertRaises(Conflict):self.m.scan(CID,{})
        finally:self.m.network.release()
    def test_cancel_before_dispatch_runs_nothing(self):
        self.ready();before=len(self.fixture.calls)
        def cancel():raise Cancelled()
        with self.assertRaises(Cancelled):self.m.refs(CID,cancel)
        self.assertEqual(len(self.fixture.calls),before)

class Clone(SSHBase):
    def test_clone_is_shallow_fixed_new_workspace_and_no_hooks(self):
        self.ready();result=self.m.clone(CID,self.root/"clones","project","main")
        self.assertEqual(result["state"],"complete");self.assertEqual(result["path"],"share/forge_git/project")
        args=self.fixture.calls[-1][0]
        for option in ["--depth","1","--no-recurse-submodules","--template=","core.hooksPath=/dev/null","--single-branch"]:self.assertIn(option,args)
        self.assertEqual((self.root/"clones"/"project"/"README.md").read_text(),"fixture source, not executed\n")
    def test_existing_destination_or_receipt_is_never_overwritten(self):
        self.ready();root=self.root/"clones";root.mkdir();(root/"project").mkdir();(root/"project"/"keep").write_text("keep")
        with self.assertRaises(Conflict):self.m.clone(CID,root,"project")
        self.assertEqual((root/"project"/"keep").read_text(),"keep")
        (root/".another.forge-clone.json").write_text("pending")
        with self.assertRaises(Conflict):self.m.clone(CID,root,"another")
        self.assertEqual((root/".another.forge-clone.json").read_text(),"pending")
    def test_clone_cancel_leaves_receipt_and_never_replays(self):
        self.ready();self.fixture.failure=Cancelled()
        with self.assertRaises(Cancelled):self.m.clone(CID,self.root/"clones","project")
        receipt=json.loads((self.root/"clones"/".project.forge-clone.json").read_text());self.assertEqual(receipt["state"],"incomplete")
        self.fixture.failure=None
        with self.assertRaises(Conflict):self.m.clone(CID,self.root/"clones","project")
    def test_destination_and_branch_injection_rejected_before_write(self):
        self.ready()
        for name in ["../apps","/tmp/x","a/b","-branch","x;id",None]:
            with self.subTest(name=name),self.assertRaises(ValueError):self.m.clone(CID,self.root/"clones",name)
        for branch in ["-u", "../x", "main;id", "heads/../x", "x.lock", "x//y", "x/.hidden", "x."]:
            with self.subTest(branch=branch),self.assertRaises(ValueError):self.m.clone(CID,self.root/"clones","project",branch)
        self.assertFalse((self.root/"clones").exists())
    def test_symlink_clone_root_cannot_redirect_writes(self):
        self.ready();outside=self.root/"outside";outside.mkdir();link=self.root/"clones";link.symlink_to(outside,target_is_directory=True)
        with self.assertRaises(PermissionError):self.m.clone(CID,link,"project")
        self.assertFalse(list(outside.iterdir()))
    def test_clone_budget_counts_data_without_following_links(self):
        root=self.root/"budget";root.mkdir();(root/"large").write_bytes(b"x"*200)
        with patch("forge_ssh.MAX_CLONE_BYTES",100),self.assertRaises(OverflowError):self.m._budget(root)
        (root/"large").unlink();(root/"link").symlink_to(self.root/"outside")
        self.m._budget(root)
    def test_clone_entry_budget(self):
        root=self.root/"budget";root.mkdir();(root/"a").touch();(root/"b").touch()
        with patch("forge_ssh.MAX_CLONE_FILES",1),self.assertRaises(OverflowError):self.m._budget(root)

class ProcessRunner(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def run_code(self,code,timeout=3,check=lambda:None):
        return SSHConnections._run([sys.executable,"-c",code],self.root,SSHConnections._env(self.root),timeout,check)
    def test_captures_and_drains_both_streams(self):
        code,out,err=self.run_code("import sys;print('hello');print('diagnostic',file=sys.stderr)")
        self.assertEqual((code,out,err),(0,"hello\n","diagnostic\n"))
    def test_process_output_bound(self):
        with self.assertRaises(OverflowError):self.run_code("import sys;sys.stdout.write('x'*400000)")
    def test_timeout_is_bounded(self):
        start=time.monotonic()
        with self.assertRaises(TimeoutError):self.run_code("import time;time.sleep(30)",.2)
        self.assertLess(time.monotonic()-start,3)
    def test_cancel_stops_group(self):
        start=time.monotonic()
        def cancel():
            if time.monotonic()-start>.2:raise Cancelled()
        with self.assertRaises(Cancelled):self.run_code("import time;time.sleep(30)",5,cancel)
        self.assertLess(time.monotonic()-start,3)
    def test_leader_exit_with_child_pipe_does_not_hang(self):
        start=time.monotonic()
        with self.assertRaises(TimeoutError):self.run_code("import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])",.3)
        self.assertLess(time.monotonic()-start,3)

class SSHHTTP(Base):
    def setUp(self):
        super().setUp();self.engine_=self.engine();self.m=self.engine_.git;self.fixture=Fixture()
        self.patch_run=patch.object(self.m,"_run",side_effect=self.fixture.run);self.patch_run.start()
        self.patch_binary=patch.object(self.m,"_binary",side_effect=lambda n:"/fixture/"+n);self.patch_binary.start()
        self.server=AppServer(("127.0.0.1",0),self.engine_,allowed_peers={"127.0.0.1"})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base="http://127.0.0.1:"+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.patch_run.stop();self.patch_binary.stop();super().tearDown()
    def request(self,path,body=None,csrf=True,extra=None):
        headers={"Content-Type":"application/json"}
        if csrf:headers["X-Forge-CSRF"]=self.server.csrf
        headers.update(extra or {})
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as r:
            with r:return r.code,json.load(r)
    def test_empty_status_no_generation(self):
        code,data=self.request("/api/git/connections");self.assertEqual(code,200);self.assertEqual(data["result"]["connections"],[]);self.assertFalse(self.fixture.calls)
    def test_create_requires_csrf_and_actual_peer(self):
        self.assertEqual(self.request("/api/git/connections",payload(),False)[0],403)
        self.server.allowed_peers=frozenset({"172.30.32.2"})
        self.assertEqual(self.request("/api/git/connections",payload(),True,{"X-Forwarded-For":"172.30.32.2"})[0],403)
        self.assertFalse(self.fixture.calls)
    def test_no_get_mutation_or_private_key_route(self):
        self.m.create(payload())
        for p in ["/api/git/connections/"+CID+"/private-key","/api/git/connections/"+CID+"/scan","/data/ssh/"+CID+"/id_ed25519","/forge_ssh.py"]:
            with self.subTest(path=p):self.assertEqual(self.request(p)[0],404)
    def test_create_duplicate_and_public_response(self):
        first=self.request("/api/git/connections",payload());second=self.request("/api/git/connections",payload())
        self.assertEqual(first[0],201);self.assertEqual(first,second);self.assertEqual(len(self.fixture.calls),1)
        self.assertNotIn("PRIVATE-TEST",json.dumps(first));self.assertNotIn("/data/",json.dumps(first))
    def test_full_admin_flow_mocked_transport(self):
        code,result=self.request("/api/git/connections",payload());self.assertEqual(code,201)
        route="/api/git/connections/"+CID
        self.assertEqual(self.request(route+"/test",{})[0],403)
        self.assertEqual(self.request(route+"/scan",{})[0],200)
        code,_=self.request(route+"/pin",{"revision":1,"public_key":HOST,"expected_fingerprint":public_key(HOST)["fingerprint"]});self.assertEqual(code,200)
        code,result=self.request(route+"/test",{});self.assertEqual(code,200);self.assertTrue(result["result"]["authenticated"])
        self.assertEqual(self.request(route+"/enabled",{"revision":2,"enabled":False})[0],200)
        self.assertEqual(self.request(route+"/test",{})[0],409)
        self.assertEqual(self.request(route+"/enabled",{"revision":2,"enabled":True})[0],409)
    def test_admin_route_cannot_grant_task_or_accept_command(self):
        self.assertEqual(self.request("/api/git/connections",payload(allow_changes=True))[0],400)
        self.m.create(payload())
        self.assertEqual(self.request("/api/git/connections/"+CID+"/test",{"command":"push"})[0],400)
    def test_new_assets_served_same_csp(self):
        for path in ["/connections.js","/connections.css"]:
            with urllib.request.urlopen(self.base+path) as r:self.assertEqual(r.status,200);self.assertIn("script-src 'self'",r.headers["Content-Security-Policy"])
    def test_shutdown_refuses_new_connection_mutations(self):
        self.engine_.stopping.set()
        self.assertEqual(self.request("/api/git/connections",payload())[0],409);self.assertFalse(self.fixture.calls)
    def test_clone_requires_current_task_grant(self):
        seen=[];self.f.MUTATING_TOOL_NAMES.add("clone_git_repository");self.f.TOOL_IMPL["clone_git_repository"]=lambda a:seen.append(a)
        t=self.enqueue();result=self.engine_.execute(t["id"],call("clone_git_repository"),lambda *a:None,lambda:None)
        self.assertFalse(json.loads(result["output"])["ok"]);self.assertFalse(seen)

    def test_account_test_requires_explicit_repo_and_is_read_only(self):
        self.assertEqual(self.request('/api/git/connections',payload(repository='',scope='account',access='write'))[0],201)
        route='/api/git/connections/'+CID
        self.assertEqual(self.request(route+'/pin',{'revision':1,'public_key':HOST,'expected_fingerprint':public_key(HOST)['fingerprint']})[0],200)
        before=len(self.fixture.calls)
        self.assertEqual(self.request(route+'/test',{})[0],400)
        self.assertEqual(len(self.fixture.calls),before)
        code,result=self.request(route+'/test',{'repository':'owner/another.git'})
        self.assertEqual(code,200);self.assertTrue(result['result']['authenticated'])
        self.assertIn('ls-remote',self.fixture.calls[-1][0]);self.assertEqual(self.fixture.calls[-1][0][-1],'ssh://git@github.com:22/owner/another.git')
        self.assertFalse(any('push' in call[0] for call in self.fixture.calls))

    def test_access_requires_actual_peer_csrf_and_revision(self):
        self.m.create(payload());route='/api/git/connections/'+CID+'/access';data={'revision':1,'access':'write'}
        self.assertEqual(self.request(route,data,False)[0],403)
        self.server.allowed_peers=frozenset({'172.30.32.2'})
        self.assertEqual(self.request(route,data,True,{'X-Forwarded-For':'172.30.32.2'})[0],403)
        self.assertEqual(self.m.listing()['connections'][0]['access'],'read')
        self.server.allowed_peers=frozenset({'127.0.0.1'})
        code,result=self.request(route,data);self.assertEqual(code,200)
        self.assertEqual(result['result']['scope'],'repository');self.assertEqual(result['result']['access'],'write')
        self.assertEqual(self.request(route,data)[0],409)
        self.assertEqual(self.request(route)[0],404)

    def test_access_endpoint_cannot_extend_grant_or_replace_credentials(self):
        self.m.create(payload());route='/api/git/connections/'+CID+'/access'
        before=self.m.listing()
        for extra in ({'allow_changes':True},{'scope':'account'},{'private_key':'NO'},{'repository':'other.git'}):
            with self.subTest(extra=extra):self.assertEqual(self.request(route,{'revision':1,'access':'write',**extra})[0],400)
        self.assertEqual(self.m.listing(),before);self.assertEqual(len(self.fixture.calls),1)

    def test_access_change_is_refused_during_shutdown(self):
        self.m.create(payload());self.engine_.stopping.set()
        self.assertEqual(self.request('/api/git/connections/'+CID+'/access',{'revision':1,'access':'write'})[0],409)
        self.assertEqual(self.m.listing()['connections'][0]['access'],'read')

CLIENTS=all(shutil.which(n) for n in ("git","ssh","ssh-keygen","ssh-keyscan"))
@unittest.skipUnless(CLIENTS,"Git/OpenSSH clients not installed; mocked tests remain available")
class OpenSSHInterop(unittest.TestCase):
    """Actual client parsing/key generation only. NO provider or network connection."""
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.m=SSHConnections(self.root/"ssh")
    def tearDown(self):self.tmp.cleanup()
    def test_real_key_generation_persistence_and_fingerprint(self):
        entry=self.m.create(payload());key=self.m.root/CID/"id_ed25519"
        result=subprocess.run([shutil.which("ssh-keygen"),"-l","-E","sha256","-f",str(key.with_suffix(".pub"))],capture_output=True,text=True,check=True)
        self.assertIn(entry["fingerprint"],result.stdout)
        digest=__import__("hashlib").sha256(key.read_bytes()).hexdigest()
        again=SSHConnections(self.m.root).create(payload());self.assertEqual(again,entry)
        self.assertEqual(__import__("hashlib").sha256(key.read_bytes()).hexdigest(),digest)
    def test_real_ssh_parses_hardened_options_without_connecting(self):
        self.m.create(payload());entry=self.m.pin(CID,{"revision":1,"public_key":HOST,"expected_fingerprint":public_key(HOST)["fingerprint"]})
        with tempfile.TemporaryDirectory() as tmp:
            _,env,_=self.m._git(self.m._ready(CID),Path(tmp))
            args=shlex.split(env["GIT_SSH_COMMAND"])+["-G","git@github.com"]
            code,out,err=self.m._run(args,Path(tmp),env,3)
            self.assertEqual(code,0,err)
            for option in ["stricthostkeychecking true","batchmode yes","identitiesonly yes","identityagent none","forwardagent no","hostkeyalgorithms ssh-ed25519"]:self.assertIn(option,out)
    def test_real_git_empty_remote_syntax_error_is_bounded(self):
        self.m.create(payload());self.m.pin(CID,{"revision":1,"public_key":HOST,"expected_fingerprint":public_key(HOST)["fingerprint"]})
        # A local false transport confirms Git accepts the fixed argv, without network or key use.
        with tempfile.TemporaryDirectory() as tmp:
            git,env,remote=self.m._git(self.m._ready(CID),Path(tmp));env["GIT_SSH_COMMAND"]="/bin/false"
            code,_,err=self.m._run(git+["ls-remote","--heads","--tags","--",remote],Path(tmp),env,3)
            self.assertNotEqual(code,0);self.assertNotIn("unknown option",err)

if __name__=="__main__":unittest.main()
