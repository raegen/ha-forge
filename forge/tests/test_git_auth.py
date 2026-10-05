"""Account/repository scope and structured Git task authority regressions."""
import json
import time
from unittest.mock import patch
import test_ssh as sshfx
import test_forge as fx
from forge_ssh import SSHConnections, clean_connection
from forge_store import Conflict
from forge_git import GitEffectUnknown

class AccountKeys(sshfx.SSHBase):
    def account(self,access='write'):
        entry=self.m.create(sshfx.payload(repository='',scope='account',access=access))
        return self.m.pin(sshfx.CID,{'revision':entry['revision'],'public_key':sshfx.HOST,'expected_fingerprint':sshfx.public_key(sshfx.HOST)['fingerprint']})
    def test_account_key_is_reused_across_explicit_repositories(self):
        entry=self.account()
        self.m.refs(sshfx.CID,repository='owner/one.git');self.m.refs(sshfx.CID,repository='owner/two.git')
        self.assertEqual(sum('-N' in x[0] for x in self.fixture.calls),1)
        self.assertEqual(entry['scope'],'account');self.assertEqual(entry['access'],'write')
        self.assertEqual(entry['repository'],'')
        self.assertEqual(self.fixture.calls[-1][0][-1],'ssh://git@github.com:22/owner/two.git')
        again=SSHConnections(self.m.root)
        self.assertEqual(again.listing()['connections'][0]['fingerprint'],entry['fingerprint'])
    def test_account_requires_repository_each_time(self):
        self.account();before=len(self.fixture.calls)
        for value in (None,'','../outside','ssh://other/repo','owner/x;id'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.m.refs(sshfx.CID,repository=value)
        self.assertEqual(len(self.fixture.calls),before)
    def test_legacy_read_only_repository_is_unchanged_on_read(self):
        self.ready();record=self.m.root/sshfx.CID/'connection.json';before=record.read_bytes()
        result=self.m.listing()['connections'][0]
        self.assertEqual(result['scope'],'repository');self.assertEqual(result['access'],'read')
        self.m.refs(sshfx.CID)
        self.assertEqual(record.read_bytes(),before)
        with self.assertRaises(PermissionError):self.m.target(sshfx.CID,write=True)
    def test_legacy_repository_access_edit_does_not_corrupt_or_widen_scope(self):
        entry=self.ready()
        changed=self.m.set_access(sshfx.CID,{'revision':entry['revision'],'access':'write'})
        self.assertEqual(changed['access'],'write');self.assertEqual(changed['scope'],'repository')
        self.assertEqual(self.m.target(sshfx.CID,write=True)['repository'],'owner/project.git')
        with self.assertRaises(PermissionError):self.m.target(sshfx.CID,'another/repo.git',write=True)
        self.assertEqual(self.m.listing()['connections'][0]['fingerprint'],entry['fingerprint'])
    def test_access_revision_and_fields_are_strict(self):
        entry=self.account('read')
        with self.assertRaises(Conflict):self.m.set_access(sshfx.CID,{'revision':entry['revision']-1,'access':'write'})
        for data in ({'revision':entry['revision'],'access':'admin'},{'revision':entry['revision'],'access':'write','allow_changes':True},{'revision':True,'access':'write'}):
            with self.subTest(data=data),self.assertRaises(ValueError):self.m.set_access(sshfx.CID,data)
        self.assertEqual(self.m.listing()['connections'][0]['access'],'read')
    def test_access_change_does_not_enable_disabled_key(self):
        entry=self.account('read');entry=self.m.set_enabled(sshfx.CID,{'revision':entry['revision'],'enabled':False})
        entry=self.m.set_access(sshfx.CID,{'revision':entry['revision'],'access':'write'})
        self.assertFalse(entry['enabled'])
        with self.assertRaises(Conflict):self.m.target(sshfx.CID,'owner/x.git',write=True)
    def test_account_shallow_clone_uses_selected_repo_and_receipt(self):
        self.account()
        result=self.m.clone(sshfx.CID,self.root/'clones','one',repository='owner/one.git')
        self.assertEqual(result['repository'],'owner/one.git')
        self.assertIn('ssh://git@github.com:22/owner/one.git',self.fixture.calls[-1][0])
    def test_account_creation_idempotence_and_immutable_scope(self):
        data=sshfx.payload(repository='',scope='account',access='write')
        first=self.m.create(data);again=self.m.create(data);self.assertEqual(first,again)
        with self.assertRaises(Conflict):self.m.create(sshfx.payload())
        self.assertEqual(len(self.fixture.calls),1)
    def test_invalid_scope_or_access_never_allocates_key(self):
        for changes in ({'scope':'any'},{'access':'admin'},{'scope':'account'},{'scope':'repository','repository':''}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.m.create(sshfx.payload(**changes))
        self.assertFalse(self.fixture.calls);self.assertFalse(self.m.root.exists())
    def test_omitted_access_defaults_read_even_for_account_scope(self):
        result=clean_connection(sshfx.payload(scope='account',repository=''))
        self.assertEqual(result['access'],'read')
    def test_list_never_generates_keys_or_tests_push_access(self):
        self.account();before=len(self.fixture.calls)
        result=self.m.listing();self.assertTrue(result['remote_write']);self.assertTrue(result['account_keys'])
        self.assertEqual(len(self.fixture.calls),before)

class GitTaskAuthority(fx.Base):
    def test_all_new_mutations_reject_read_only_task(self):
        names={'git_init','git_stage','git_commit','git_switch','git_fetch','git_fast_forward','git_push'}
        self.f.MUTATING_TOOL_NAMES.update(names);seen=[]
        self.f.TOOL_IMPL={n:lambda a:seen.append(a) for n in names}
        t=self.enqueue();e=self.engine()
        for name in names:
            result=e.execute(t['id'],fx.call(name),lambda *a:None,lambda:None)
            self.assertFalse(json.loads(result['output'])['ok'])
        self.assertFalse(seen)
    def test_grant_expiry_is_checked_inside_git_tool(self):
        self.f.MUTATING_TOOL_NAMES.add('git_push');t=self.enqueue(allow=True);seen=[]
        def work(args):
            seen.append('entered')
            self.s.update_task(t['id'],change_deadline=time.time()-1)
            self.f.TOOL_CANCEL.check()
            seen.append('must not happen')
        self.f.TOOL_IMPL={'git_push':work}
        result=self.engine().execute(t['id'],fx.call('git_push'),lambda *a:None,lambda:None)
        self.assertEqual(seen,['entered']);self.assertIn('expired',result['output']);self.assertIsNone(self.f.TOOL_CANCEL.check)
    def test_unknown_effect_blocks_later_mutations_in_same_fresh_batch(self):
        seen=[]
        def ambiguous(args):seen.append('push');raise GitEffectUnknown('inspect remote before retry')
        self.f.MUTATING_TOOL_NAMES.add('git_push')
        self.f.TOOL_IMPL={'git_push':ambiguous,'write_file':lambda a:seen.append('wrong'),'git_status':lambda a:seen.append('inspect')}
        calls=[fx.call('git_push','first'),fx.call('write_file','second'),fx.call('git_status','last')]
        t=self.enqueue(allow=True);self.s.accept_response(self.c,t['id'],fx.response('resp_pending',calls),calls)
        out=self.engine().finish_tools(t['id'],self.c,lambda:None,lambda *a:None)
        self.assertEqual(seen,['push','inspect'])
        self.assertTrue(json.loads(out[0]['output'])['unknown_effect']);self.assertTrue(json.loads(out[1]['output'])['not_executed'])
    def test_read_only_git_inspection_still_allowed_without_grant(self):
        seen=[];self.f.TOOL_IMPL={'git_status':lambda a:seen.append('read')}
        result=self.engine().execute(self.enqueue()['id'],fx.call('git_status'),lambda *a:None,lambda:None)
        self.assertTrue(json.loads(result['output'])['ok']);self.assertEqual(seen,['read'])
