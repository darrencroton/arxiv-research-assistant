"""Explicit paper and run state persistence for re-ass."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from re_ass.settings import AppConfig


PAPER_STATUSES = (
    "selected",
    "micro_summary_generated",
    "pdf_downloaded",
    "note_written",
    "completed",
    "failed",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _paper_record_filename(paper_key: str) -> str:
    return paper_key.replace(":", "_").replace("/", "_") + ".json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


class StateStore:
    """Read and write machine-readable paper and run state."""

    def __init__(self, config: AppConfig) -> None:
        self.state_root = config.state_root
        self.papers_dir = config.state_papers_dir
        self.runs_dir = config.state_runs_dir
        self.announcement_checkpoint_path = self.state_root / "announcement-checkpoint.json"

    def bootstrap(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def paper_record_path(self, paper_key: str) -> Path:
        return self.papers_dir / _paper_record_filename(paper_key)

    def load_paper_record(self, paper_key: str) -> dict[str, Any] | None:
        path = self.paper_record_path(paper_key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def is_completed(self, paper_key: str) -> bool:
        record = self.load_paper_record(paper_key)
        return bool(record and record.get("status") == "completed")

    def completed_paper_keys(self) -> set[str]:
        keys: set[str] = set()
        if not self.papers_dir.exists():
            return keys
        for path in self.papers_dir.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("status") == "completed" and "paper_key" in record:
                keys.add(str(record["paper_key"]))
        return keys

    def _load_checkpoint_payload(self) -> dict[str, Any] | None:
        if not self.announcement_checkpoint_path.exists():
            return None
        try:
            return json.loads(self.announcement_checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def load_completed_announcement_date(self) -> date | None:
        payload = self._load_checkpoint_payload()
        if not payload:
            return self.latest_successful_pull_date()

        raw_value = payload.get("last_completed_announcement_date")
        if not isinstance(raw_value, str):
            return self.latest_successful_pull_date()
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            return self.latest_successful_pull_date()

    def latest_successful_pull_date(self) -> date | None:
        if not self.runs_dir.exists():
            return None

        for path in sorted(self.runs_dir.glob("*.json"), reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            if record.get("fatal_error") is not None:
                continue

            completed_papers = record.get("completed_papers")
            try:
                if int(completed_papers) <= 0:
                    continue
            except (TypeError, ValueError):
                continue

            raw_value = record.get("announcement_date") or record.get("run_date")
            if not isinstance(raw_value, str):
                continue
            try:
                return date.fromisoformat(raw_value)
            except ValueError:
                continue
        return None

    def save_completed_announcement_date(self, announcement_date: date) -> Path:
        payload = {
            "last_completed_announcement_date": announcement_date.isoformat(),
            "updated_at": _now_iso(),
        }
        _write_json(self.announcement_checkpoint_path, payload)
        return self.announcement_checkpoint_path

    def save_paper_record(
        self,
        *,
        paper_key: str,
        source_id: str,
        title: str,
        published: str,
        filename_stem: str,
        status: str,
        note_path: str | None = None,
        pdf_path: str | None = None,
        micro_summary: str | None = None,
        last_error: str | None = None,
    ) -> Path:
        if status not in PAPER_STATUSES:
            raise ValueError(f"Unsupported paper status '{status}'.")

        existing = self.load_paper_record(paper_key) or {}
        first_completed_at = existing.get("first_completed_at")
        if status == "completed" and first_completed_at is None:
            first_completed_at = _now_iso()

        record = {
            "paper_key": paper_key,
            "source_id": source_id,
            "title": title,
            "published": published,
            "filename_stem": filename_stem,
            "note_path": note_path,
            "pdf_path": pdf_path,
            "micro_summary": micro_summary,
            "status": status,
            "first_completed_at": first_completed_at,
            "last_attempt_at": _now_iso(),
            "last_error": last_error,
        }
        path = self.paper_record_path(paper_key)
        _write_json(path, record)
        return path

    def save_run_summary(self, run_date: str, summary: dict[str, Any]) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        path = self.runs_dir / f"{run_date}-{timestamp}.json"
        _write_json(path, summary)
        return path
