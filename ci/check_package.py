"""Offline repository checks. Run from the repository root with PyYAML installed."""
from pathlib import Path
import re
import yaml

root=Path(__file__).resolve().parents[1]
app=root/'forge'
repo=yaml.safe_load((root/'repository.yaml').read_text())
config=yaml.safe_load((app/'config.yaml').read_text())
assert repo['name']=='Forge'
assert config['name']=='Forge' and config['slug']=='forge'
assert re.fullmatch(r'\d+\.\d+\.\d+', config['version'])
assert config['arch']==['aarch64'], 'Do not advertise untested architectures'
assert config['ingress'] is True and config['panel_admin'] is True
assert config['init'] is False and config['backup']=='cold'
assert not config.get('ports'), 'No direct LAN exposure'
assert config['options']['openai_api_key']==''
assert config['options']['allow_shell_commands'] is False
assert config['options']['allow_sensitive_files'] is False
for name in ('README.md','DOCS.md','MODELS.md','TESTING.md','CHANGELOG.md','Dockerfile','run.sh','.dockerignore','icon.png','logo.png','forge_models.py','forge_ssh.py','forge_git.py','tests/test_git.py','tests/test_git_auth.py','SSH.md','tests/test_connections.js','static/connections.js','static/connections.css','static/models.js','static/forge.svg'):
    assert (app/name).is_file(), name
banned={'apply_adapter.py','tool_overrides.py.txt','backend_test_report.txt','ui_test_report.json','options.json','forge.sqlite3','forge_state.json','SMARTASS_CONTEXT.md','FORGE_CONTEXT.md','connection.json','id_ed25519','id_ed25519.pub','id_rsa','.ssh','data','git_operations','__pycache__'}
for p in app.rglob('*'):
    assert not p.is_symlink(), f'Symlink in release: {p}'
    assert p.name not in banned, f'Private/generated/obsolete file in release: {p}'
    if p.is_file():
        assert not p.name.endswith(('.db','.sqlite3','.db-wal','.db-shm','.log','.b64','.pem','.key','.pyc','.pyo')), p
        if p.suffix in {'.py','.js','.md','.yaml','.yml','.html','.css','.sh'}:
            text=p.read_text()
            assert not re.search(r'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}',text), f'Possible credential: {p}'
assert 'forge_models.py' in (app/'Dockerfile').read_text()
assert 'forge_ssh.py' in (app/'Dockerfile').read_text()
assert 'forge_git.py' in (app/'Dockerfile').read_text()
assert re.search(r'^VERSION\s*=\s*[\"\']'+re.escape(config['version'])+r'[\"\']', (app/'forge.py').read_text(), re.M), 'Source/config versions must match'
assert 'git openssh-client-default' in (app/'Dockerfile').read_text()
assert 'python3 -m unittest' in (app/'Dockerfile').read_text()
print('Repository metadata and offline hygiene checks passed. This is not a build, license approval or live deployment test.')
