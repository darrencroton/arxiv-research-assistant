import importlib.util
import plistlib
import sys
from types import SimpleNamespace
from pathlib import Path


def load_ab_test_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "ab-test.py"
    spec = importlib.util.spec_from_file_location("ab_test_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_patch_plist_for_variant_updates_dict_schedule() -> None:
    ab_test = load_ab_test_module()
    source = {
        "Label": "com.user.re-ass",
        "ProgramArguments": ["/opt/homebrew/bin/uv", "run", "--project", "/repo", "re-ass"],
        "StartCalendarInterval": {"Hour": 4, "Minute": 0},
        "StandardOutPath": "/repo/logs/launchd.stdout.log",
        "StandardErrorPath": "/repo/logs/launchd.stderr.log",
    }

    patched = ab_test._patch_plist_for_variant(
        plist_text=plistlib.dumps(source).decode("utf-8"),
        name="local",
        config_path="user_preferences/settings-local.toml",
        log_dir=Path("/repo/logs-local"),
        hour=7,
        minute=30,
    )
    data = plistlib.loads(patched.encode("utf-8"))

    assert data["Label"] == "com.user.re-ass.local"
    assert data["ProgramArguments"] == [
        "/opt/homebrew/bin/uv",
        "run",
        "--project",
        "/repo",
        "re-ass",
        "--config",
        "user_preferences/settings-local.toml",
    ]
    assert data["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert data["StandardOutPath"] == "/repo/logs-local/com.user.re-ass.local.stdout.log"
    assert data["StandardErrorPath"] == "/repo/logs-local/com.user.re-ass.local.stderr.log"


def test_patch_plist_for_variant_updates_weekday_array_schedule() -> None:
    ab_test = load_ab_test_module()
    source = {
        "Label": "com.user.re-ass",
        "ProgramArguments": ["/opt/homebrew/bin/uv", "run", "re-ass"],
        "StartCalendarInterval": [
            {"Weekday": 1, "Hour": 4, "Minute": 0},
            {"Weekday": 2, "Hour": 4, "Minute": 0},
        ],
        "StandardOutPath": "/repo/logs/launchd.stdout.log",
        "StandardErrorPath": "/repo/logs/launchd.stderr.log",
    }

    patched = ab_test._patch_plist_for_variant(
        plist_text=plistlib.dumps(source).decode("utf-8"),
        name="local",
        config_path="user_preferences/settings-local.toml",
        log_dir=Path("/repo/logs-local"),
        hour=None,
        minute=None,
    )
    data = plistlib.loads(patched.encode("utf-8"))

    assert data["StartCalendarInterval"] == [
        {"Weekday": 1, "Hour": 4, "Minute": 30},
        {"Weekday": 2, "Hour": 4, "Minute": 30},
    ]


def test_setup_is_provider_agnostic_for_local_variant(tmp_path: Path, monkeypatch) -> None:
    ab_test = load_ab_test_module()
    user_prefs = tmp_path / "user_preferences"
    user_prefs.mkdir()
    benchmark_settings = user_prefs / "settings.toml"
    benchmark_settings.write_text(
        "[output]\n"
        'root = "output"\n'
        'summaries_dir = "summaries"\n'
        'daily_notes_dir = "daily-notes"\n'
        'weekly_notes_dir = "weekly-notes"\n'
        'pdfs_dir = "pdfs"\n'
        "\n"
        "[state]\n"
        'root = "state"\n'
        "\n"
        "[logs]\n"
        'root = "logs"\n'
        "\n"
        "[llm]\n"
        'mode = "cli"\n'
        'provider = "copilot"\n'
        'model = "claude-sonnet-4.6"\n'
        'effort = "high"\n'
        "timeout_seconds = 1200\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ab_test, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ab_test, "USER_PREFS", user_prefs)
    monkeypatch.setattr(ab_test, "BENCHMARK_SETTINGS", benchmark_settings)

    result = ab_test.cmd_setup(SimpleNamespace(name="local", force=False))

    assert result == 0
    variant = (user_prefs / "settings-local.toml").read_text(encoding="utf-8")
    assert 'root = "output-local"' in variant
    assert 'root = "state-local"' in variant
    assert 'root = "logs-local"' in variant
    assert 'provider = "copilot"' in variant
    assert 'effort = "high"' in variant
    assert "timeout_seconds = 1200" in variant
    assert "max_prompt_chars = 1000000" not in variant


def test_setup_refuses_symlinked_variant_config(tmp_path: Path, monkeypatch) -> None:
    ab_test = load_ab_test_module()
    user_prefs = tmp_path / "user_preferences"
    user_prefs.mkdir()
    benchmark_settings = user_prefs / "settings.toml"
    benchmark_settings.write_text(
        "[output]\n"
        'root = "output"\n'
        "[state]\n"
        'root = "state"\n'
        "[logs]\n"
        'root = "logs"\n',
        encoding="utf-8",
    )
    variant = user_prefs / "settings-local.toml"
    variant.symlink_to(benchmark_settings)
    monkeypatch.setattr(ab_test, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ab_test, "USER_PREFS", user_prefs)
    monkeypatch.setattr(ab_test, "BENCHMARK_SETTINGS", benchmark_settings)

    result = ab_test.cmd_setup(SimpleNamespace(name="local", force=True))

    assert result == 1
    assert benchmark_settings.exists()
    assert variant.is_symlink()


def test_cleanup_archives_only_variant_settings(tmp_path: Path, monkeypatch) -> None:
    ab_test = load_ab_test_module()
    user_prefs = tmp_path / "user_preferences"
    archive_dir = tmp_path / "archive" / "ab-test"
    launch_agents = tmp_path / "LaunchAgents"
    user_prefs.mkdir()
    launch_agents.mkdir()
    benchmark_settings = user_prefs / "settings.toml"
    variant_settings = user_prefs / "settings-local.toml"
    benchmark_settings.write_text("benchmark = true\n", encoding="utf-8")
    variant_settings.write_text("variant = true\n", encoding="utf-8")
    monkeypatch.setattr(ab_test, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ab_test, "USER_PREFS", user_prefs)
    monkeypatch.setattr(ab_test, "BENCHMARK_SETTINGS", benchmark_settings)
    monkeypatch.setattr(ab_test, "ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(ab_test, "LAUNCH_AGENTS_DIR", launch_agents)
    monkeypatch.setattr(ab_test.shutil, "which", lambda _name: None)

    result = ab_test.cmd_cleanup(SimpleNamespace(name="local", remove_script=False))

    assert result == 0
    assert benchmark_settings.read_text(encoding="utf-8") == "benchmark = true\n"
    assert not variant_settings.exists()
    archived = sorted(archive_dir.glob("settings-local-*.toml"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == "variant = true\n"


def test_schedule_creates_log_dir_before_bootstrap(tmp_path: Path, monkeypatch) -> None:
    ab_test = load_ab_test_module()
    user_prefs = tmp_path / "user_preferences"
    rendered_dir = tmp_path / "logs" / "launchd"
    launch_agents = tmp_path / "LaunchAgents"
    external_logs = tmp_path / "external" / "logs"
    user_prefs.mkdir()
    rendered_dir.mkdir(parents=True)
    launch_agents.mkdir()
    benchmark_settings = user_prefs / "settings.toml"
    variant_settings = user_prefs / "settings-local.toml"
    benchmark_settings.write_text("[logs]\nroot = \"logs\"\n", encoding="utf-8")
    variant_settings.write_text(
        "[output]\n"
        'root = "output-local"\n'
        "[state]\n"
        'root = "state-local"\n'
        "[logs]\n"
        f'root = "{external_logs.as_posix()}"\n',
        encoding="utf-8",
    )
    rendered_benchmark = rendered_dir / "com.user.re-ass.plist"
    rendered_benchmark.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.user.re-ass",
                "ProgramArguments": ["/opt/homebrew/bin/uv", "run", "re-ass"],
                "StartCalendarInterval": {"Hour": 4, "Minute": 0},
                "StandardOutPath": "/repo/logs/launchd.stdout.log",
                "StandardErrorPath": "/repo/logs/launchd.stderr.log",
            }
        )
    )
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        if command[:2] == ["launchctl", "bootstrap"]:
            assert external_logs.exists()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ab_test, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ab_test, "USER_PREFS", user_prefs)
    monkeypatch.setattr(ab_test, "BENCHMARK_SETTINGS", benchmark_settings)
    monkeypatch.setattr(ab_test, "RENDERED_BENCHMARK_PLIST", rendered_benchmark)
    monkeypatch.setattr(ab_test, "LAUNCH_AGENTS_DIR", launch_agents)
    monkeypatch.setattr(ab_test.shutil, "which", lambda _name: "/bin/launchctl")
    monkeypatch.setattr(ab_test.subprocess, "run", fake_run)
    monkeypatch.setattr(ab_test.os, "getuid", lambda: 501)

    result = ab_test.cmd_schedule(SimpleNamespace(name="local", hour=None, minute=None))

    assert result == 0
    assert ["launchctl", "bootout", "gui/501/com.user.re-ass.local"] in calls
    assert ["launchctl", "bootstrap", "gui/501", str(launch_agents / "com.user.re-ass.local.plist")] in calls
    installed = plistlib.loads((launch_agents / "com.user.re-ass.local.plist").read_bytes())
    assert installed["StandardOutPath"] == str(rendered_dir / "com.user.re-ass.local.stdout.log")
    assert installed["StandardErrorPath"] == str(rendered_dir / "com.user.re-ass.local.stderr.log")
