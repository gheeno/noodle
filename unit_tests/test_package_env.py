"""Per-app resources/ loader self-check — precedence per NOOD_0133.

No browser, no LLM. Verifies the <app_dir>/resources/.env + <app>_secrets.env
cascade in hooks.py. Precedence (the documented per-app-overrides story, now
true): shell/CI env  >  package files  >  root files. Before NOOD_0133 plain
load_dotenv() (first write wins) made every package-level override of a
root-.env key silently dead.
"""
import os

import pytest

_KEYS = ("MYAPP_TOKEN", "MYAPP_SECRET", "MYAPP")


@pytest.fixture(autouse=True)
def clean_state():
    from noodle import hooks
    hooks._loaded_package_dirs.clear()
    hooks._shell_env_keys.clear()
    hooks._shell_snapshot_taken = False
    for k in _KEYS:
        os.environ.pop(k, None)
    yield
    hooks._loaded_package_dirs.clear()
    hooks._shell_env_keys.clear()
    hooks._shell_snapshot_taken = False
    for k in _KEYS:
        os.environ.pop(k, None)


def _make_package(tmp_path, env_lines=None, secrets_lines=None):
    pkg = tmp_path / "tests" / "myapp"
    res_dir = pkg / "resources"
    res_dir.mkdir(parents=True)
    if env_lines:
        (res_dir / ".env").write_text("\n".join(env_lines) + "\n")
    if secrets_lines:
        (res_dir / "myapp_secrets.env").write_text("\n".join(secrets_lines) + "\n")
    return pkg


class TestLoadPackageEnv:
    def test_new_key_lands_in_environ(self, tmp_path):
        from noodle import hooks

        pkg = _make_package(tmp_path, env_lines=["MYAPP_TOKEN=from-package"])
        hooks._load_package_env(pkg)
        assert os.environ["MYAPP_TOKEN"] == "from-package"

    def test_package_overrides_root_env_file(self, tmp_path):
        """NOOD_0133 — a key sourced from the ROOT .env (loaded after the
        shell snapshot) loses to the package's own value. This was the dead
        config: NOODLE_TIMEOUT in resources/.env could never take effect."""
        from noodle import hooks

        hooks._snapshot_shell_env()             # before_all order: snapshot…
        os.environ["MYAPP_TOKEN"] = "from-root"  # …then root .env loads
        pkg = _make_package(tmp_path, env_lines=["MYAPP_TOKEN=from-package"])
        hooks._load_package_env(pkg)
        assert os.environ["MYAPP_TOKEN"] == "from-package"

    def test_shell_env_beats_package(self, tmp_path):
        """A real pre-run env var (shell export, CI variable, CLI-injected
        NOODLE_*) always wins — files never clobber it."""
        from noodle import hooks

        os.environ["MYAPP_TOKEN"] = "from-shell"  # set BEFORE the snapshot
        hooks._snapshot_shell_env()
        pkg = _make_package(tmp_path, env_lines=["MYAPP_TOKEN=from-package"])
        hooks._load_package_env(pkg)
        assert os.environ["MYAPP_TOKEN"] == "from-shell"

    def test_env_wins_over_secrets_on_conflict(self, tmp_path):
        """Within one package the priority stays .env > secrets.env — same
        winners as before_all's root cascade."""
        from noodle import hooks

        hooks._snapshot_shell_env()
        pkg = _make_package(
            tmp_path,
            env_lines=["MYAPP_SECRET=from-env"],
            secrets_lines=["MYAPP_SECRET=from-secrets"],
        )
        hooks._load_package_env(pkg)
        assert os.environ["MYAPP_SECRET"] == "from-env"

    def test_plain_secrets_env_picked_up(self, tmp_path):
        """NOOD_0108 — resources/secrets.env (no app prefix) loads too, so an
        app package can use the same filenames as the workspace root."""
        from noodle import hooks

        pkg = _make_package(tmp_path)
        (pkg / "resources" / "secrets.env").write_text("MYAPP_SECRET=plain\n")
        hooks._load_package_env(pkg)
        assert os.environ["MYAPP_SECRET"] == "plain"

    def test_missing_resources_folder_is_a_noop(self, tmp_path):
        from noodle import hooks

        pkg = tmp_path / "tests" / "empty_app"
        pkg.mkdir(parents=True)
        hooks._load_package_env(pkg)  # must not raise

    def test_loaded_once_per_package_dir(self, tmp_path):
        """Second call for the same package dir is a no-op — editing the file
        after the first load must not change os.environ (before_feature fires
        once per .feature file, many files share one package dir)."""
        from noodle import hooks

        pkg = _make_package(tmp_path, env_lines=["MYAPP_TOKEN=first"])
        hooks._load_package_env(pkg)
        assert os.environ["MYAPP_TOKEN"] == "first"

        (pkg / "resources" / ".env").write_text("MYAPP_TOKEN=second\n")
        hooks._load_package_env(pkg)
        assert os.environ["MYAPP_TOKEN"] == "first"

    def test_lazy_snapshot_preserves_old_behaviour_standalone(self, tmp_path):
        """A caller that never ran before_all (repl/MCP helper): whatever is
        in os.environ at first load reads as shell-owned, so the package
        cannot clobber it — the safe pre-NOOD_0133 behaviour."""
        from noodle import hooks

        os.environ["MYAPP_TOKEN"] = "parent-loaded"
        pkg = _make_package(tmp_path, env_lines=["MYAPP_TOKEN=from-package"])
        hooks._load_package_env(pkg)  # no snapshot ran — lazy one fires here
        assert os.environ["MYAPP_TOKEN"] == "parent-loaded"


class TestLoadEnvironmentsGlob:
    def test_package_environments_yaml_picked_up(self, tmp_path, monkeypatch):
        from noodle import hooks

        monkeypatch.chdir(tmp_path)
        pkg_res = tmp_path / "tests" / "myapp" / "resources"
        pkg_res.mkdir(parents=True)
        (pkg_res / "myapp_environments.yaml").write_text("myapp: http://localhost:9999\n")

        hooks._load_environments()

        assert os.environ["MYAPP"] == "http://localhost:9999"

    def test_package_environments_yaml_wins_over_root(self, tmp_path, monkeypatch):
        """NOOD_0133 — same model as .env: per-app file beats root file."""
        from noodle import hooks

        monkeypatch.chdir(tmp_path)
        hooks._snapshot_shell_env()
        (tmp_path / "environments.yaml").write_text("myapp: http://root\n")
        pkg_res = tmp_path / "tests" / "myapp" / "resources"
        pkg_res.mkdir(parents=True)
        (pkg_res / "myapp_environments.yaml").write_text("myapp: http://package\n")

        hooks._load_environments()

        assert os.environ["MYAPP"] == "http://package"

    def test_shell_env_beats_package_environments_yaml(self, tmp_path, monkeypatch):
        from noodle import hooks

        monkeypatch.chdir(tmp_path)
        os.environ["MYAPP"] = "http://from-shell"
        hooks._snapshot_shell_env()
        pkg_res = tmp_path / "tests" / "myapp" / "resources"
        pkg_res.mkdir(parents=True)
        (pkg_res / "myapp_environments.yaml").write_text("myapp: http://package\n")

        hooks._load_environments()

        assert os.environ["MYAPP"] == "http://from-shell"

    def test_plain_package_environments_yaml_picked_up(self, tmp_path, monkeypatch):
        """NOOD_0108 — resources/environments.yaml (no app prefix) loads the
        same way as <app>_environments.yaml: per-app base URLs live in the
        app package, not the workspace-root .env."""
        from noodle import hooks

        monkeypatch.chdir(tmp_path)
        pkg_res = tmp_path / "tests" / "myapp" / "resources"
        pkg_res.mkdir(parents=True)
        (pkg_res / "environments.yaml").write_text("myapp: http://localhost:8888\n")

        hooks._load_environments()

        assert os.environ["MYAPP"] == "http://localhost:8888"
