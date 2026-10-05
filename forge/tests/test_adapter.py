"""Tool-adapter integration checks with only temporary files and mocked HA APIs."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from forge_provider import Cancelled
from forge_safety import Redactor

SOURCE=Path(__file__).resolve().parents[1]/"forge.py"
@unittest.skipUnless(SOURCE.exists(),"Tool adapter has not been staged here")
class Adapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location("adapter_test_fixture",SOURCE)
        cls.f=importlib.util.module_from_spec(spec)
        # Import source with defaults; never read real app credentials in a test.
        with patch.object(Path,"read_text",return_value="{}"),patch.dict(os.environ,{"SUPERVISOR_TOKEN":""}):
            spec.loader.exec_module(cls.f)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
        roots={key:self.base/key for key in ("config","addons","share","media","backup","addon_configs")}
        for root in roots.values():root.mkdir()
        self.roots=roots
        for name,value in {"ROOTS":roots,"BACKUP_ROOT":self.base/"private-backups","BACKUP_ON_WRITE":True,
                           "ALLOW_SENSITIVE":False,"ALLOW_SHELL":False,"MAX_READ":4096,"MAX_WRITE":8192,
                           "REDACT":Redactor(["test-credential-never-export"])}.items():
            p=patch.object(self.f,name,value);p.start();self.addCleanup(p.stop)
        self.f.TOOL_CANCEL.check=None

    def test_schema_and_runtime_interfaces_preserved(self):
        self.assertEqual(self.f.VERSION,"0.6.0")
        self.assertEqual(self.f.OPTIONS["attachment_storage_mb"],256)
        self.assertGreaterEqual(len(self.f.TOOL_IMPL),25)
        definitions=self.f.tool_definitions()
        names={d["name"] for d in definitions if d["type"]=="function"}
        self.assertEqual(names,set(self.f.TOOL_IMPL)-{"run_shell"})
        with patch.object(self.f,"ALLOW_SHELL",True):
            all_names={d["name"] for d in self.f.tool_definitions() if d["type"]=="function"}
        self.assertEqual(all_names,set(self.f.TOOL_IMPL))
        self.assertTrue(self.f.MUTATING_TOOL_NAMES<=all_names)
        for name in names:self.assertTrue(callable(self.f.TOOL_IMPL[name]))
        self.assertFalse(hasattr(self.f,"STATE"))

    def test_git_adapters_keep_private_root_and_clone_scope_runtime_owned(self):
        class Connections:
            def listing(inner):return {"connections":[]}
            def refs(inner,cid,check,repository=None):check();return {"connection_id":cid}
            def clone(inner,cid,root,name,branch,check,repository=None):
                check();self.assertEqual(root,self.roots["share"]/"forge_git")
                return {"connection_id":cid,"name":name,"branch":branch}
        with patch.object(self.f,"GIT_CONNECTIONS",Connections()):
            self.assertEqual(self.f.TOOL_IMPL["list_git_connections"]({}),{"connections":[]})
            self.assertEqual(self.f.TOOL_IMPL["git_remote_refs"]({"connection_id":"fixture"}),{"connection_id":"fixture"})
            self.assertEqual(self.f.TOOL_IMPL["clone_git_repository"]({"connection_id":"fixture","directory_name":"sample","branch":"main"}),{"connection_id":"fixture","name":"sample","branch":"main"})
        self.assertIn("clone_git_repository",self.f.MUTATING_TOOL_NAMES)
        self.assertNotIn("git_remote_refs",self.f.MUTATING_TOOL_NAMES)
        self.assertNotIn("list_git_connections",self.f.MUTATING_TOOL_NAMES)
        for name in self.f.TOOL_IMPL:
            self.assertFalse(any(x in name for x in ("ssh_key","ssh_pin","ssh_enable","ssh_access")))

    def test_git_workspace_cannot_redirect_within_or_outside_share(self):
        share=self.roots["share"];other=share/"other";other.mkdir()
        (share/"forge_git").symlink_to(other,target_is_directory=True)
        with self.assertRaises(PermissionError):self.f.clone_git_repository("fixture","sample")
        self.assertEqual(list(other.iterdir()),[])

    def test_git_projects_are_limited_to_real_mapped_subdirectories(self):
        project=self.roots['share']/'project';project.mkdir()
        app=self.roots['addons']/'custom';app.mkdir()
        self.assertEqual(self.f.git_workspace_path('share/project'),project)
        self.assertEqual(self.f.git_workspace_path('addons/custom'),app)
        (self.roots['share']/'alias').symlink_to(project,target_is_directory=True)
        (self.roots['share']/'file').write_text('source')
        for value in ('share','addons','config','config/project','media/project','backup/project',
                      'addon_configs/project','share/../addons/custom','/share/project','share//project',
                      'share/./project','share/project/.git','share/.ssh','share/.storage','share/alias','share/file',None):
            with self.subTest(value=value),self.assertRaises((PermissionError,FileNotFoundError)):
                self.f.git_workspace_path(value)

    def test_structured_git_adapters_forward_check_and_explicit_destination(self):
        project=self.roots['share']/'project';project.mkdir();seen=[]
        class Workspace:
            def push(inner,path,check,**kwargs):check();seen.append((path,kwargs));return {'published':True}
        args={'path':'share/project','connection_id':'fixture','repository':'owner/app.git','branch':'main','expected_head':'a'*40}
        with patch.object(self.f,'GIT_WORKSPACE',Workspace()):
            self.assertEqual(self.f.TOOL_IMPL['git_push'](args),{'published':True})
        self.assertEqual(seen,[(project,{k:v for k,v in args.items() if k!='path'})])
        self.assertIn('path',args)
        for name in ('git_status','git_diff','git_log'):self.assertNotIn(name,self.f.MUTATING_TOOL_NAMES)
        for name in ('git_init','git_stage','git_commit','git_switch','git_fetch','git_fast_forward','git_push'):
            self.assertIn(name,self.f.MUTATING_TOOL_NAMES)

    def test_account_refs_and_clone_pass_repository_without_selecting_credentials(self):
        seen=[]
        class Connections:
            def refs(inner,cid,check,repository=None):seen.append((cid,repository));return {}
            def clone(inner,cid,root,name,branch,check,repository=None):seen.append((cid,repository));return {}
        with patch.object(self.f,'GIT_CONNECTIONS',Connections()):
            self.f.TOOL_IMPL['git_remote_refs']({'connection_id':'explicit-key','repository':'owner/one.git'})
            self.f.TOOL_IMPL['clone_git_repository']({'connection_id':'explicit-key','directory_name':'one','repository':'owner/two.git'})
        self.assertEqual(seen,[('explicit-key','owner/one.git'),('explicit-key','owner/two.git')])

    def test_sensitive_alias_and_escaping_symlinks_denied(self):
        root=self.roots["config"];(root/".storage").mkdir()
        (root/".storage/auth_provider.homeassistant").write_text("secret")
        (root/"alias.txt").symlink_to(root/".storage/auth_provider.homeassistant")
        (root/"escape").symlink_to(self.base)
        for path in ("config/alias.txt","config/escape","config/../share"):
            with self.subTest(path=path),self.assertRaises(PermissionError):self.f.read_file(path)
        self.assertFalse(any(Path(x["path"]).name in {"alias.txt","escape"} for x in self.f.list_directory("config")["items"]))

    def test_backed_up_write_and_empty_replace_guard(self):
        root=self.roots["config"];(root/"actual.txt").write_text("before")
        (root/"alias.txt").symlink_to(root/"actual.txt")
        result=self.f.write_file("config/alias.txt","after")
        self.assertEqual((root/"actual.txt").read_text(),"after")
        self.assertEqual(Path(result["backup"]).read_text(),"before")
        self.assertIn("config",str(Path(result["backup"]).relative_to(self.f.BACKUP_ROOT)))
        with self.assertRaises(ValueError):self.f.replace_text("config/actual.txt","","x",None)
        self.assertEqual((root/"actual.txt").read_text(),"after")

    def test_batch_rejected_before_first_write(self):
        (self.roots["config"]/"safe.txt").write_text("unchanged")
        with self.assertRaises(PermissionError):
            self.f.write_files([{"path":"config/safe.txt","content":"bad"},{"path":"config/secrets.yaml","content":"bad"}])
        self.assertEqual((self.roots["config"]/"safe.txt").read_text(),"unchanged")

    def test_large_file_bounded_line_reads(self):
        (self.roots["config"]/"large.txt").write_text("0123456789\n"*2000)
        with self.assertRaises(ValueError):self.f.read_file("config/large.txt")
        result=self.f.read_file("config/large.txt",1500,3)
        self.assertEqual(result["content"],"0123456789\n"*3)
        self.assertEqual(result["end_line"],1502);self.assertTrue(result["truncated_by_range"])

    def test_search_ignores_bad_symlinks(self):
        root=self.roots["config"]
        (root/"ok.txt").write_text("safe needle")
        (self.base/"outside.txt").write_text("private needle")
        (root/"escaped.txt").symlink_to(self.base/"outside.txt")
        result=self.f.search_files("config","needle","*.txt",10)
        self.assertIn("ok.txt",json.dumps(result));self.assertNotIn("private needle",json.dumps(result))

    def test_inspection_redacts_credentials_and_uses_correct_log_parameter(self):
        endpoints=[]
        def supervisor(method,path,**kw):
            endpoints.append(path)
            if path.endswith("/info"):
                return {"version":"test","options":{"api_key":"test-credential-never-export"},"schema":{"token":"password"}}
            return 'api_key="test-credential-never-export"'
        with patch.object(self.f,"resolve_app_slug",return_value=("local_test",None)),patch.object(self.f,"supervisor_request",side_effect=supervisor):
            value=self.f.inspect_app("test",42)
        encoded=json.dumps(value)
        self.assertNotIn("test-credential-never-export",encoded)
        self.assertNotIn("options",value["info"]);self.assertNotIn("schema",value["info"])
        self.assertIn("/addons/local_test/logs?lines=42",endpoints)

    def test_model_cannot_enable_dev_mode(self):
        with patch.object(self.f,"ha_request") as request:
            with self.assertRaises(PermissionError):self.f.call_service("switch","turn_on",target={"entity_id":"switch.dev_mode"})
            request.assert_not_called()
            self.f.call_service("switch","turn_off",target={"entity_id":"switch.dev_mode"})
            request.assert_called_once()

    def test_shell_permission_and_output_bounds(self):
        with self.assertRaises(PermissionError):self.f.run_shell("true","config",1)
        with patch.object(self.f,"ALLOW_SHELL",True):
            result=self.f.run_shell(f'{sys.executable} -c "print(chr(120)*250000)"',"config",5)
        self.assertEqual(result["returncode"],0)
        self.assertLessEqual(len(result["stdout"].encode()),102400)
        self.assertTrue(result["output_truncated"])

    def test_shell_cancellation_is_cooperative(self):
        def stop():raise Cancelled("fixture cancellation")
        self.f.TOOL_CANCEL.check=stop
        with patch.object(self.f,"ALLOW_SHELL",True),self.assertRaises(Cancelled):
            self.f.run_shell("sleep 10","config",20)

    def test_generic_context_seed_is_created_once_and_backed_up(self):
        context=self.roots["config"]/"FORGE_CONTEXT.md"
        with patch.object(self.f,"PROJECT_CONTEXT_PATH",context):
            result=self.f.get_project_context()
            self.assertEqual(result["path"],"config/FORGE_CONTEXT.md")
            self.assertIn("Forge",result["content"])
            self.assertNotIn("SmartAss",result["content"])
            context.write_text("existing human architecture")
            self.f.ensure_project_context()
            self.assertEqual(context.read_text(),"existing human architecture")
            result=self.f.write_project_context("updated human architecture")
            self.assertEqual(Path(result["backup"]).read_text(),"existing human architecture")
            self.assertEqual(context.read_text(),"updated human architecture")

    def test_legacy_context_path_selection_is_upgrade_compatible(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                spec=importlib.util.spec_from_file_location("context_path_fixture",SOURCE)
                module=importlib.util.module_from_spec(spec)
                with patch.object(Path,"read_text",return_value="{}"),patch.dict(os.environ,{"SUPERVISOR_TOKEN":""}),patch.object(Path,"exists",lambda p: legacy and str(p)=="/homeassistant/SMARTASS_CONTEXT.md"):
                    spec.loader.exec_module(module)
                self.assertEqual(module.PROJECT_CONTEXT_PATH.name,"SMARTASS_CONTEXT.md" if legacy else "FORGE_CONTEXT.md")

    def test_invalid_options_fail_closed(self):
        with patch.object(Path,"read_text",return_value='{"allow_shell_commands":"false"}'):
            with self.assertRaises(RuntimeError):self.f.load_options()
        with patch.object(Path,"read_text",return_value="[]"):
            with self.assertRaises(RuntimeError):self.f.load_options()
        with patch.object(Path,"read_text",return_value="{bad"):
            with self.assertRaises(RuntimeError):self.f.load_options()

if __name__=="__main__":unittest.main()
