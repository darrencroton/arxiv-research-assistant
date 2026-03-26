import json
from datetime import date
from pathlib import Path

from re_ass.state_store import StateStore
from tests.support import make_app_config


def test_state_store_tracks_completed_paper_keys(tmp_path: Path) -> None:
    store = StateStore(make_app_config(tmp_path))
    store.bootstrap()

    store.save_paper_record(
        paper_key="arxiv:2603.15732",
        source_id="2603.15732",
        title="Example Paper",
        published="2026-03-21T12:00:00+00:00",
        filename_stem="Doe - 2026 - Example Paper [arXiv 2603.15732]",
        status="completed",
    )

    assert store.is_completed("arxiv:2603.15732") is True
    assert store.completed_paper_keys() == {"arxiv:2603.15732"}


def test_state_store_preserves_first_completed_timestamp(tmp_path: Path) -> None:
    store = StateStore(make_app_config(tmp_path))
    store.bootstrap()

    store.save_paper_record(
        paper_key="arxiv:2603.15732",
        source_id="2603.15732",
        title="Example Paper",
        published="2026-03-21T12:00:00+00:00",
        filename_stem="Doe - 2026 - Example Paper [arXiv 2603.15732]",
        status="completed",
    )
    first_completed_at = store.load_paper_record("arxiv:2603.15732")["first_completed_at"]

    store.save_paper_record(
        paper_key="arxiv:2603.15732",
        source_id="2603.15732",
        title="Example Paper",
        published="2026-03-21T12:00:00+00:00",
        filename_stem="Doe - 2026 - Example Paper [arXiv 2603.15732]",
        status="completed",
    )

    assert store.load_paper_record("arxiv:2603.15732")["first_completed_at"] == first_completed_at


def test_state_store_saves_run_summary_json(tmp_path: Path) -> None:
    store = StateStore(make_app_config(tmp_path))
    store.bootstrap()

    path = store.save_run_summary({"run_date": "2026-03-22", "completed_papers": 1}, label="announcement-2026-03-22")

    assert path.exists()
    assert "announcement-2026-03-22" in path.name
    assert json.loads(path.read_text(encoding="utf-8"))["completed_papers"] == 1


def test_state_store_returns_latest_successful_pull_date_for_migration(tmp_path: Path) -> None:
    store = StateStore(make_app_config(tmp_path))
    store.bootstrap()

    store.save_run_summary(
        {
            "run_date": "2026-03-21",
            "fatal_error": "boom",
            "completed_papers": 1,
        },
        label="overall-fatal",
    )
    store.save_run_summary(
        {
            "run_date": "2026-03-22",
            "fatal_error": None,
            "completed_papers": 0,
        },
        label="overall",
    )
    store.save_run_summary(
        {
            "run_date": "2026-03-23",
            "fatal_error": None,
            "completed_papers": 2,
        },
        label="announcement-2026-03-23",
    )

    assert store.latest_successful_pull_date().isoformat() == "2026-03-23"


def test_state_store_persists_completed_announcement_date_checkpoint(tmp_path: Path) -> None:
    store = StateStore(make_app_config(tmp_path))
    store.bootstrap()

    store.save_completed_announcement_date(date(2026, 3, 25))

    assert store.load_completed_announcement_date().isoformat() == "2026-03-25"
