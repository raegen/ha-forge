"""Actual local Git integration, no production credentials or external network.

The fixture substitutes a temporary local bare repo for the approved transport
only inside this test process. Production always uses pinned SSH.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from forge_git import GitWorkspace, GitEffectUnknown, branch_name, safe_file, identity
from forge_provider import Cancelled
from forge_store import Conflict
from forge_ssh import SSHConnections, repository_path

CID = "a" * 32
REPO = "owner/project.git"

class LoopbackConnections(SSHConnections):
    def __init__(self, root, bare):
        super().__init__(root)
        self.bare=bare;self.enabled=True;self.write=True;self.pinned=True;self.calls=[];self.lose_push=False
    def target(self, cid, repository=None, write=False):
        if cid!=CID or not self.enabled or not self.pinned: raise PermissionError("Unapproved fixture connection")
        if write and not self.write: raise PermissionError("Administrator has not enabled write access")
        if repository_path(repository)!=REPO: raise PermissionError("Fixture repository not approved")
        return {"id":CID,"repository":REPO,"scope":"account","access":"write" if self.write else "read"}
    def _git(self, entry, home):
        return [self._binary('git')],self._env(home),"ssh://git@fixture.invalid:22/"+entry['repository']
    def _run(self, args, cwd, env, timeout, check=lambda:None, budget=None):
        self.calls.append(list(args))
        url="ssh://git@fixture.invalid:22/"+REPO
        # Exact, fixture-only transport substitution; no arbitrary network traffic.
        if any(x.startswith('ssh://') and x!=url for x in args):raise AssertionError('unexpected network destination')
        if url in args:
            args=[str(self.bare) if x==url else x for x in args]
            args=args[:1]+['-c','protocol.file.allow=always']+args[1:]
            env={**env,'GIT_ALLOW_PROTOCOL':'file'}
        result=SSHConnections._run(args,cwd,env,timeout,check,budget)
        if 'push' in args and self.lose_push:
            self.lose_push=False
            raise TimeoutError('fixture lost reply after accepted push')
        return result

@unittest.skipUnless(shutil.which('git'),'Git client required')
class GitDevelopment(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'project';self.path.mkdir()
        self.bare=self.root/'remote.git'
        self.raw(None,'init','--bare','--template=',str(self.bare))
        self.ssh=LoopbackConnections(self.root/'credentials',self.bare)
        self.git=GitWorkspace(self.ssh,self.root/'receipts')
        self.git.init(self.path,'main')
    def raw(self,path,*args):
        env=SSHConnections._env(self.root)
        cmd=[shutil.which('git'),'-c','core.hooksPath=/dev/null','-c','init.templateDir=']
        if path:cmd+=['-C',str(path)]
        p=subprocess.run(cmd+list(args),cwd=self.root,env=env,text=True,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        if p.returncode:raise AssertionError(p.stderr)
        return p.stdout.strip()
    def commit_file(self,name='README.md',text='Workshop source\n',path=None):
        path=path or self.path;(path/name).write_text(text)
        self.git.stage(path,[name]);diff=self.git.diff(path,True)
        return self.git.commit(path,'Develop the appliance','Fixture Author','fixture@example.invalid',diff['head'],diff['index_sha256'])
    def push(self,head=None):
        head=head or self.git.status(self.path)['head']
        return self.git.push(self.path,CID,REPO,'main',head)

    def test_first_commit_and_real_push_verified_without_ssh(self):
        result=self.commit_file()
        self.assertTrue(result['clean']);self.assertEqual(result['branch'],'main')
        pushed=self.push(result['head']);self.assertEqual(pushed['verified_remote_head'],result['head'])
        self.assertEqual(self.raw(self.bare,'rev-parse','refs/heads/main'),result['head'])
        receipt=self.git.status(self.path)['last_operation'];self.assertEqual(receipt['state'],'complete')
        self.assertEqual(receipt['operation'],'push')
        self.assertFalse((self.root/'credentials').exists())
        self.assertTrue(all('ssh'!=Path(c[0]).name for c in self.ssh.calls))

    def test_repeated_push_is_read_reconciled_without_another_push(self):
        self.commit_file();self.push();before=sum('push' in x for x in self.ssh.calls)
        result=self.push();self.assertTrue(result['already_up_to_date'])
        self.assertEqual(sum('push' in x for x in self.ssh.calls),before)

    def test_remote_failure_after_acceptance_is_retained_not_automatically_retried(self):
        head=self.commit_file()['head'];self.ssh.lose_push=True
        with self.assertRaises(GitEffectUnknown):self.push(head)
        self.assertEqual(sum('push' in x for x in self.ssh.calls),1)
        self.assertEqual(self.raw(self.bare,'rev-parse','main'),head)
        receipt=self.git.status(self.path)['last_operation'];self.assertEqual(receipt['state'],'unknown_effect')
        self.assertEqual(receipt['head'],head)
        self.assertTrue(self.push(head)['already_up_to_date'])
        self.assertEqual(sum('push' in x for x in self.ssh.calls),1)

    def test_read_only_connection_rejects_push_before_contact(self):
        head=self.commit_file()['head'];self.ssh.write=False;n=len(self.ssh.calls)
        with self.assertRaises(PermissionError):self.push(head)
        self.assertEqual(len(self.ssh.calls),n)

    def test_disabled_unpinned_or_wrong_repository_cannot_publish(self):
        head=self.commit_file()['head']
        for key in ('enabled','pinned'):
            setattr(self.ssh,key,False)
            with self.assertRaises(PermissionError):self.push(head)
            setattr(self.ssh,key,True)
        with self.assertRaises(PermissionError):self.git.push(self.path,CID,'other/repo.git','main',head)
        self.assertFalse(any('push' in x for x in self.ssh.calls))

    def test_fetch_then_clean_fast_forward_without_auto_merge(self):
        first=self.commit_file()['head'];self.push(first)
        other=self.root/'other';other.mkdir();self.git.init(other,'main')
        # Seed an identical initial commit by fetching; no worktree is changed by fetch.
        fetched=self.git.fetch(other,CID,REPO,'main')
        self.assertEqual(list(p.name for p in other.iterdir()),['.git'])
        self.raw(other,'update-ref','refs/heads/main',first)
        self.raw(other,'reset','--hard',first)  # Test fixture ONLY; production has no reset tool.
        second=self.commit_file(text='A second reviewed revision\n',path=other)['head']
        self.git.push(other,CID,REPO,'main',second)
        fetched=self.git.fetch(self.path,CID,REPO,'main')
        self.assertEqual(self.git.status(self.path)['head'],first)
        self.assertEqual(fetched['commit'],second);self.assertFalse(fetched['working_tree_changed'])
        result=self.git.fast_forward(self.path,fetched['tracking_ref'],first)
        self.assertEqual(result['head'],second);self.assertEqual((self.path/'README.md').read_text(),'A second reviewed revision\n')

    def test_divergence_is_never_force_pushed_or_merged(self):
        first=self.commit_file()['head'];self.push(first)
        other=self.root/'diverged';other.mkdir();self.git.init(other,'main')
        self.git.fetch(other,CID,REPO,'main');self.raw(other,'update-ref','refs/heads/main',first);self.raw(other,'reset','--hard',first)
        remote=self.commit_file(text='remote change\n',path=other)['head'];self.git.push(other,CID,REPO,'main',remote)
        local=self.commit_file(text='local change\n')['head']
        with self.assertRaises(GitEffectUnknown):self.push(local)
        self.assertEqual(self.raw(self.bare,'rev-parse','main'),remote)
        fetched=self.git.fetch(self.path,CID,REPO,'main')
        with self.assertRaises(Conflict):self.git.fast_forward(self.path,fetched['tracking_ref'],local)
        self.assertEqual(self.git.status(self.path)['head'],local)
        commands=[x for x in self.ssh.calls if 'push' in x]
        self.assertTrue(commands)
        for cmd in commands:
            self.assertIn('--no-force',cmd);self.assertFalse(any(x in cmd for x in ('--force','--mirror','--all','--tags','--delete')))
            self.assertFalse(any(x.startswith('+') for x in cmd))

    def test_named_clean_branch_switch_and_create(self):
        head=self.commit_file()['head']
        self.assertEqual(self.git.switch(self.path,'feature/one',True)['head'],head)
        self.assertEqual(self.git.status(self.path)['branch'],'feature/one')
        self.assertEqual(self.git.switch(self.path,'main')['branch'],'main')
        (self.path/'untracked.txt').write_text('preserve me')
        with self.assertRaises(Conflict):self.git.switch(self.path,'feature/one')
        self.assertEqual((self.path/'untracked.txt').read_text(),'preserve me')

    def test_push_refuses_dirty_wrong_branch_and_stale_head(self):
        head=self.commit_file()['head']
        with self.assertRaises(Conflict):self.git.push(self.path,CID,REPO,'other',head)
        with self.assertRaises(Conflict):self.push('0'*40)
        (self.path/'README.md').write_text('not committed')
        with self.assertRaises(Conflict):self.push(head)
        self.assertFalse(any('push' in x for x in self.ssh.calls))

    def test_explicit_deletion_and_literal_glob_filename(self):
        self.commit_file('literal*.txt')
        (self.path/'literal*.txt').unlink();self.git.stage(self.path,['literal*.txt'])
        d=self.git.diff(self.path,True);result=self.git.commit(self.path,'Remove reviewed source','Fixture','fixture@example.invalid',d['head'],d['index_sha256'])
        self.assertTrue(result['clean'])

    def test_source_modes_and_no_hooks_executed(self):
        sentinel=self.root/'executed'
        hook=self.path/'.git/hooks/pre-commit';hook.parent.mkdir();hook.write_text('#!/bin/sh\ntouch '+str(sentinel)+'\n');hook.chmod(0o755)
        for name in ('post-commit','post-checkout','pre-push'):
            p=hook.parent/name;p.write_text(hook.read_text());p.chmod(0o755)
        self.commit_file();self.git.switch(self.path,'feature',True);self.git.switch(self.path,'main');self.push()
        self.assertFalse(sentinel.exists())

    def test_environment_drops_tokens_agent_and_config_injection(self):
        with patch.dict(os.environ,{'SUPERVISOR_TOKEN':'never-export','OPENAI_API_KEY':'never-export','SSH_AUTH_SOCK':'/bad','GIT_CONFIG_COUNT':'2','GIT_SSH_COMMAND':'bad'}):
            env=self.git._env(self.root)
        self.assertNotIn('never-export',str(env));self.assertNotIn('SSH_AUTH_SOCK',env);self.assertNotIn('GIT_CONFIG_COUNT',env);self.assertNotIn('GIT_SSH_COMMAND',env)
        self.assertEqual(env['GIT_NO_REPLACE_OBJECTS'],'1');self.assertEqual(env['GIT_OPTIONAL_LOCKS'],'0')

    def test_read_operations_do_not_change_index_or_receipt(self):
        self.commit_file();index=(self.path/'.git/index').read_bytes();latest=self.git._receipt_dir(self.path)/'latest.json'
        saved=latest.read_bytes();before=latest.stat().st_mtime_ns
        self.git.status(self.path);self.git.diff(self.path);self.git.log(self.path)
        self.assertEqual((self.path/'.git/index').read_bytes(),index)
        self.assertEqual(latest.read_bytes(),saved);self.assertEqual(latest.stat().st_mtime_ns,before)

    def test_legacy_plain_git_repository_works_and_local_author_not_modified(self):
        self.raw(self.path,'config','user.name','Original human');self.raw(self.path,'config','user.email','original@example.invalid')
        self.commit_file();self.assertEqual(self.raw(self.path,'config','user.name'),'Original human')
        self.assertIn('Fixture Author',self.git.log(self.path)['log'])

    def test_init_existing_repository_preserves_it(self):
        before=(self.path/'.git/config').read_bytes()
        with self.assertRaises(Conflict):self.git.init(self.path)
        self.assertEqual((self.path/'.git/config').read_bytes(),before)

    def test_unborn_status_log_and_empty_commit(self):
        status=self.git.status(self.path);self.assertIsNone(status['head']);self.assertIsNone(status['index_sha256'])
        self.assertEqual(self.git.log(self.path),{'log':'','unborn':True})
        with self.assertRaises(ValueError):self.git.commit(self.path,'empty','Test','test@example.invalid',None,None)
        self.commit_file();d=self.git.diff(self.path,True)
        with self.assertRaises(Conflict):self.git.commit(self.path,'empty','Test','test@example.invalid',d['head'],d['index_sha256'])

    def test_stale_index_or_head_refused_without_commit(self):
        (self.path/'README.md').write_text('one');self.git.stage(self.path,['README.md']);d=self.git.diff(self.path,True)
        (self.path/'README.md').write_text('two');self.git.stage(self.path,['README.md'])
        with self.assertRaises(Conflict):self.git.commit(self.path,'review required','Test','test@example.invalid',d['head'],d['index_sha256'])
        self.assertIsNone(self.git.status(self.path)['head'])

    def test_cancellation_before_mutation_leaves_no_effect(self):
        def stop():raise Cancelled('fixture')
        before=self.git.status(self.path)['last_operation']
        (self.path/'README.md').write_text('one')
        with self.assertRaises(Cancelled):self.git.stage(self.path,['README.md'],check=stop)
        self.assertIsNone(self.git.status(self.path)['index_sha256'])
        self.assertEqual(self.git.status(self.path)['last_operation'],before)

    def test_cancellation_after_dispatch_retains_unknown_receipt(self):
        (self.path/'README.md').write_text('one');original=self.ssh._run
        def cancel_after_add(args,*a,**kw):
            result=original(args,*a,**kw)
            if 'add' in args:raise Cancelled('fixture cancelled after add')
            return result
        with patch.object(self.ssh,'_run',side_effect=cancel_after_add),self.assertRaises(Cancelled):self.git.stage(self.path,['README.md'])
        self.assertIn('A ',self.git.status(self.path)['porcelain'])
        self.assertEqual(self.git.status(self.path)['last_operation']['state'],'unknown_effect')

    def test_unsafe_configuration_fails_before_operation_without_execution(self):
        config=self.path/'.git/config';original=config.read_text();sentinel=self.root/'unsafe'
        cases=['\n[include]\npath = /dev/null\n',
               '\n[core]\nhooksPath = /tmp\n','\n[core]\nsshCommand = touch '+str(sentinel)+'\n',
               '\n[filter "unsafe"]\nclean = touch '+str(sentinel)+'\n',
               '\n[url "ssh://evil/"]\ninsteadOf = ssh://fixture/\n',
               '\n[diff "evil"]\ntextconv = touch '+str(sentinel)+'\n',
               '\n[remote "origin"]\nuploadpack = evil\n',
               '\n[extensions]\nworktreeConfig = true\n']
        for value in cases:
            config.write_text(original+value)
            with self.subTest(value=value),self.assertRaises(PermissionError):self.git.status(self.path)
            self.assertFalse(sentinel.exists())
        config.write_text(original)

    def test_uri_shaped_remote_name_cannot_retarget_explicit_destination(self):
        self.commit_file();config=self.path/'.git/config';before=config.read_text()
        config.write_text(before+'\n[remote "ssh://git@fixture.invalid:22/owner/project.git"]\nurl = ssh://git@fixture.invalid:22/unapproved/repository.git\n')
        with self.assertRaises(PermissionError):self.push()
        self.assertNotEqual(config.read_text(),before)  # Refuse, never silently rewrite user metadata.
        self.assertEqual(self.raw(self.bare,'for-each-ref','--format=%(refname)','refs/heads/'),'')

    def test_standard_origin_and_branch_config_are_preserved(self):
        self.raw(self.path,'remote','add','origin','ssh://git@fixture.invalid:22/owner/project.git')
        self.raw(self.path,'config','branch.main.remote','origin')
        self.raw(self.path,'config','branch.main.merge','refs/heads/main')
        before=(self.path/'.git/config').read_bytes()
        self.commit_file();self.push()
        self.assertEqual((self.path/'.git/config').read_bytes(),before)

    def test_git_metadata_redirects_and_symlinks_rejected(self):
        bad=self.path/'.git/objects/info/alternates';bad.parent.mkdir(exist_ok=True)
        bad.write_text('/tmp/other')
        with self.assertRaises(PermissionError):self.git.status(self.path)
        bad.unlink()  # Disposable fixture only.
        p=self.path/'.git/linked';p.symlink_to(self.root)
        with self.assertRaises(PermissionError):self.git.status(self.path)

    def test_stage_rejects_symlink_directory_secret_and_external_hardlink(self):
        outside=self.root/'private';outside.write_text('retained')
        (self.path/'link').symlink_to(outside);(self.path/'folder').mkdir();os.link(outside,self.path/'hard')
        (self.path/'secrets.yaml').write_text('private')
        for name in ['link','folder','hard','secrets.yaml']:
            with self.subTest(name=name),self.assertRaises(PermissionError):self.git.stage(self.path,[name])
        self.assertIsNone(self.git.status(self.path)['index_sha256'])
        self.assertEqual(outside.read_text(),'retained')

    def test_stage_does_not_expand_directories_or_missing_paths(self):
        (self.path/'folder').mkdir();(self.path/'folder/file.txt').write_text('safe')
        for paths in [[],['.'],['folder'],['missing.txt'],[':(glob)**'],['../private']]:
            with self.subTest(paths=paths),self.assertRaises((ValueError,PermissionError)):self.git.stage(self.path,paths)
        self.assertIsNone(self.git.status(self.path)['index_sha256'])

    def test_private_key_header_refused_but_documented_literal_allowed(self):
        (self.path/'unknown.txt').write_text('-----BEGIN OPENSSH PRIVATE KEY-----\nfixture\n')
        with self.assertRaises(PermissionError):self.git.stage(self.path,['unknown.txt'])
        (self.path/'example.py').write_text("HEADER = '-----BEGIN OPENSSH PRIVATE KEY-----'\n")
        self.git.stage(self.path,['example.py'])

    def test_no_commit_of_manually_staged_runtime_files_or_gitlinks(self):
        (self.path/'options.json').write_text('{}');self.raw(self.path,'add','options.json');d=self.git.diff(self.path,True)
        with self.assertRaises(PermissionError):self.git.commit(self.path,'unsafe','Test','test@example.invalid',d['head'],d['index_sha256'])

    def test_metadata_size_and_index_bound(self):
        (self.path/'.git/config').write_bytes(b'x'*65537)
        with self.assertRaises(PermissionError):self.git.status(self.path)

    def test_receipt_persists_on_new_manager(self):
        head=self.commit_file()['head'];other=GitWorkspace(self.ssh,self.root/'receipts')
        self.assertEqual(other.status(self.path)['head'],head)
        self.assertEqual(other.status(self.path)['last_operation']['operation'],'commit')
        self.assertEqual((other._receipt_dir(self.path)/'latest.json').stat().st_mode & 0o777,0o600)

    def test_write_revoked_during_network_operation_is_rechecked(self):
        head=self.commit_file()['head'];count=[0]
        def check():
            count[0]+=1
            if count[0]>3:self.ssh.write=False
        with self.assertRaises((PermissionError,GitEffectUnknown)):
            self.git.push(self.path,CID,REPO,'main',head,check=check)
        self.assertFalse(any('push' in x for x in self.ssh.calls))

    def test_diff_does_not_run_attribute_commands(self):
        self.commit_file();(self.path/'.gitattributes').write_text('*.md diff=evil\n')
        (self.path/'README.md').write_text('changed\n')
        result=self.git.diff(self.path)
        self.assertIn('+changed',result['diff'])
        commands=[x for x in self.ssh.calls if 'diff' in x]
        self.assertTrue(all('--no-ext-diff' in x and '--no-textconv' in x for x in commands))

class InputGuards(unittest.TestCase):
    def test_invalid_branches(self):
        for value in ['-main','refs/../escape','a..b','a//b','a.lock/b','a./b','a/.b','a/','a:refs/heads/b','main;evil','x\nmain',True,None]:
            with self.subTest(value=value),self.assertRaises(ValueError):branch_name(value)
        self.assertEqual(branch_name('feature/one-2'),'feature/one-2')
    def test_invalid_identities(self):
        for name,email in [('','x@y'),('Name\nX','x@y'),('Name','bad'),('Name','x@y\nX'),('Name <evil>','x@y')]:
            with self.subTest(name=name),self.assertRaises(ValueError):identity(name,email)
    def test_common_sensitive_and_pathspec_inputs(self):
        for name in ['.env','.env.production','.ssh/host','a/.storage/auth','id_rsa','private.key','data.sqlite','data.db-wal','__pycache__/x.pyc','foo/.git/config','options.json','secrets.yaml','a/../b','./x','/tmp/x','-A','x\ny']:
            with self.subTest(name=name),self.assertRaises((ValueError,PermissionError)):safe_file(name)
        for name in ['README.md','forge/config.yaml','.env.example','.github/workflows/ci.yml','file with spaces.txt']:
            self.assertEqual(safe_file(name),name)

if __name__=='__main__':unittest.main()
