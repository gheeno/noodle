"""NOOD_0070 — architect-review fixes: noodle.yaml unknown-key warning and
localhost-only default for `noodle report serve`."""
from noodle import config


def test_yaml_unknown_key_warns_but_still_merges(tmp_path, capsys):
    (tmp_path / "noodle.yaml").write_text("broswer: firefox\ntests_dir: specs\n")
    cfg = config.load(str(tmp_path))
    err = capsys.readouterr().err
    assert "broswer" in err and "warning" in err
    assert cfg["broswer"] == "firefox"      # forward-compat: still merged
    assert cfg["tests_dir"] == "specs"      # known key, no warning needed
    assert cfg["browser"] == "chromium"     # the typo left the real key at default


def test_yaml_known_keys_no_warning(tmp_path, capsys):
    (tmp_path / "noodle.yaml").write_text("browser: firefox\nheadless: false\n")
    cfg = config.load(str(tmp_path))
    assert capsys.readouterr().err == ""
    assert cfg["browser"] == "firefox"


def test_report_serve_defaults_to_localhost(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from noodle import cli
    from noodle.reporting import builder

    seen = {}
    monkeypatch.setattr(builder, "serve_report",
                        lambda d, host, port, on_bound=None: seen.update(host=host, port=port))
    res = CliRunner().invoke(cli.app, ["report", "serve", str(tmp_path)])
    assert res.exit_code == 0
    assert seen["host"] == "127.0.0.1"
