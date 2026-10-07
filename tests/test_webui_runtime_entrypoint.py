"""Offline subprocess coverage: never import or execute the installed core."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def launch(tmp_path):
    app = tmp_path / "app with spaces"
    core = tmp_path / "synthetic core"
    cwd = tmp_path / "unrelated"
    for directory in (app, core, cwd):
        directory.mkdir()
    shutil.copyfile(ROOT / "webui_runtime.py", app / "webui_runtime.py")
    (app / "app_only.py").write_text("VALUE = 'webui'\n")
    (core / "app_only.py").write_text("VALUE = 'wrong-core-shadow'\n")
    (cwd / "app_only.py").write_text("raise AssertionError('cwd shadow')\n")
    (app / "server.py").write_text("""
import json, os, sys
import app_only
if os.environ.get('TEST_RESTART') and not os.environ.get('TEST_RESTARTED'):
    os.environ['TEST_RESTARTED'] = '1'
    os.execv(sys.executable, [sys.executable] + sys.argv)
if os.environ.get('TEST_OVERLAY_IMPORT'):
    import overlay_only
print(json.dumps({'app': app_only.VALUE, 'argv': sys.argv,
    'env': os.environ.get('HERMES_WEBUI_PORT'), 'cwd': os.getcwd(),
    'reexec': os.environ.get('TEST_REEXEC'), 'restart': os.environ.get('TEST_RESTARTED'),
    'path': sys.path, 'bootstrap_count': os.environ.get('BOOTSTRAP_COUNT'),
    'isolated': sys.flags.isolated}))
""")
    # -S prevents any real editable-install/sitecustomize bootstrap. The
    # synthetic core's -I -S hop models relaunch_command's isolated -c/run_path.
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'TMPDIR') if key in os.environ}
    env.update(HOME=str(tmp_path), HERMES_HOME=str(tmp_path / 'hermes'),
               HERMES_WEBUI_STATE_DIR=str(tmp_path / 'state'),
               HERMES_WEBUI_AGENT_DIR=str(core), HERMES_WEBUI_PORT='9123')

    def run(extra=None, args=()):
        return subprocess.run([sys.executable, '-S', str(app / 'webui_runtime.py'), *args],
                              cwd=cwd, env={**env, **(extra or {})},
                              capture_output=True, text=True, timeout=15)
    return app, core, cwd, run


REEXEC_CORE = """
import os, sys
from pathlib import Path
root = str(Path(__file__).resolve().parent)
os.environ['BOOTSTRAP_COUNT'] = str(int(os.environ.get('BOOTSTRAP_COUNT', '0')) + 1)
if not os.environ.get('TEST_REEXEC'):
    os.environ['TEST_REEXEC'] = '1'
    prefix = f'import sys, runpy; sys.path.insert(0, {root!r}); sys.argv = {sys.argv!r}; '
    body = f'runpy.run_path({str(Path(sys.argv[0]).absolute())!r}, run_name="__main__")'
    os.execv(sys.executable, [sys.executable, '-I', '-S', '-c', prefix + body])
sys.path.insert(0, root)
"""


def decoded(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_reexec_and_second_launch_from_unrelated_cwd(launch):
    app, core, cwd, run = launch
    (core / 'hermes_bootstrap.py').write_text(REEXEC_CORE)
    for _ in range(2):
        data = decoded(run(args=('--example', 'argument with spaces')))
        assert data['app'] == 'webui'
        assert data['argv'] == [str(app / 'webui_runtime.py'), '--example', 'argument with spaces']
        assert data['env'] == '9123'
        assert data['cwd'] == str(cwd)
        assert data['reexec'] == '1'
        assert data['bootstrap_count'] == '2'
        assert data['isolated'] == 1
        assert data['path'][0] == str(app)


def test_direct_server_relaunch_reproduces_lost_app_path(launch):
    app, core, cwd, run = launch
    # The documented core relaunch adds ONLY core to an isolated process.
    # This demonstrates why directly re-entering server.py is insufficient.
    code = (
        f"import sys, runpy; sys.path.insert(0, {str(core)!r}); "
        f"runpy.run_path({str(app / 'server.py')!r}, run_name='__main__')"
    )
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code],
                            cwd=cwd, env={'HOME': str(cwd)},
                            capture_output=True, text=True, timeout=15)
    # The core's colliding module is selected rather than the app's.
    assert decoded(result)['app'] == 'wrong-core-shadow'
    (core / 'app_only.py').unlink()
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code],
                            cwd=cwd, env={'HOME': str(cwd)},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "No module named 'app_only'" in result.stderr


def test_process_restart_retains_durable_launcher(launch):
    app, core, cwd, run = launch
    (core / 'hermes_bootstrap.py').write_text(REEXEC_CORE)
    data = decoded(run({'TEST_RESTART': '1'}))
    assert data['restart'] == '1'
    assert data['bootstrap_count'] == '3'
    assert data['argv'][0] == str(app / 'webui_runtime.py')
    assert data['app'] == 'webui'


def test_older_core_without_bootstrap_needs_no_overlay(launch):
    app, core, cwd, run = launch
    data = decoded(run())
    assert data['app'] == 'webui'
    assert data['reexec'] is None
    assert not any('runtime-deps' in entry for entry in data['path'])


def test_bootstrap_errors_are_not_treated_as_old_core(launch):
    app, core, cwd, run = launch
    (core / 'hermes_bootstrap.py').write_text('import deliberately_missing_bootstrap_dependency\n')
    result = run()
    assert result.returncode != 0
    assert 'deliberately_missing_bootstrap_dependency' in result.stderr


def test_overlay_is_explicit_and_selected_after_bootstrap(launch, tmp_path):
    app, core, cwd, run = launch
    overlay = tmp_path / 'optional-deps'
    selected = overlay / f'python{sys.version_info.major}.{sys.version_info.minor}'
    selected.mkdir(parents=True)
    (selected / 'overlay_only.py').write_text('VALUE = True\n')
    (selected / 'app_only.py').write_text("VALUE = 'wrong-overlay-shadow'\n")
    # A .pth must not be executed by this launcher.
    (selected / 'danger.pth').write_text("import sys; sys.exit(88)\n")
    wrong = overlay / 'python0.0'
    wrong.mkdir()
    (core / 'hermes_bootstrap.py').write_text(REEXEC_CORE + "\nassert not any('optional-deps' in p for p in sys.path)\n")
    data = decoded(run({'HERMES_WEBUI_RUNTIME_DEPS': str(overlay), 'TEST_OVERLAY_IMPORT': '1'}))
    assert data['path'][-1] == str(selected)
    assert str(wrong) not in data['path']
    assert data['app'] == 'webui'


@pytest.mark.parametrize('overlay', ['relative-path', '/nonexistent-webui-overlay'])
def test_bad_explicit_overlay_fails_clearly(launch, overlay):
    result = launch[3]({'HERMES_WEBUI_RUNTIME_DEPS': overlay})
    assert result.returncode != 0
    assert 'HERMES_WEBUI_RUNTIME_DEPS' in result.stderr or 'dependency overlay' in result.stderr


def test_docker_uses_durable_script_without_changing_launch_contract():
    source = (ROOT / 'docker_init.bash').read_text()
    assert 'cd /app; python webui_runtime.py || error_exit "hermes-webui failed or exited with an error"' in source
