import runpy
import sys
from pathlib import Path

import pytest

import re_ass.main as main
from tests.support import make_app_config


def test_cli_loads_config_and_invokes_pipeline(tmp_path: Path, monkeypatch) -> None:
    config = make_app_config(tmp_path)
    called = {}

    monkeypatch.setattr(main, "load_config", lambda _config_path=None: config)
    monkeypatch.setattr(main, "configure_logging", lambda _config=None: called.setdefault("configured", True))

    def fake_run(loaded_config, run_date, *, backfill):
        called["config"] = loaded_config
        called["run_date"] = run_date
        called["backfill"] = backfill
        return 7

    sys.modules.pop("re_ass.pipeline", None)
    import types

    fake_pipeline = types.ModuleType("re_ass.pipeline")
    fake_pipeline.run = fake_run
    sys.modules["re_ass.pipeline"] = fake_pipeline

    try:
        exit_code = main.cli(["--date", "2026-03-24"])
    finally:
        sys.modules.pop("re_ass.pipeline", None)

    assert exit_code == 7
    assert called["configured"] is True
    assert called["config"] == config
    assert called["run_date"].isoformat() == "2026-03-24"
    assert called["backfill"] is True


def test___main___raises_system_exit_with_cli_result(monkeypatch) -> None:
    monkeypatch.setattr(main, "cli", lambda: 5)

    with pytest.raises(SystemExit) as error:
        runpy.run_module("re_ass.__main__", run_name="__main__")

    assert error.value.code == 5
