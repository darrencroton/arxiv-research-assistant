#!/usr/bin/env python3
"""A/B test re-ass against an alternative LLM provider.

PURPOSE
-------
Run an identical re-ass pipeline against any candidate LLM provider (a local
OpenAI-compatible endpoint, codex, gemini, perplexity, ...) alongside your production
benchmark, with isolated
output / state / log directories so the two sides never collide. Then produce a
side-by-side report of rankings, selections, paper notes, and weekly
synthesis.

PREREQUISITES
-------------
1. Your benchmark `user_preferences/settings.toml` is configured and a manual
   `uv run re-ass` succeeds.
2. The candidate provider is configured for non-interactive use (e.g. local
   inference server running, codex CLI logged in, gemini API key set, ...).
3. Python 3.13+ (matches the project's `requires-python`).

LIFECYCLE
---------
Every variant is identified by a short `<name>` (e.g. `local`, `codex`,
`gemini`). One variant at a time, run:

    python scripts/ab-test.py setup    --name local
    # then edit user_preferences/settings-local.toml [llm] block
    python scripts/ab-test.py schedule --name local
    python scripts/ab-test.py compare  [--name local]
    python scripts/ab-test.py cleanup  --name local

To test a different provider later, repeat with `--name codex`,
`--name gemini`, etc. The script is variant-agnostic.

STEP-BY-STEP
------------

1. setup --name <variant>

    Copies user_preferences/settings.toml -> user_preferences/settings-<name>.toml,
    rewriting the writable-output paths to suffixed siblings:

      [output] root
        -> append "-<name>" only if the value is a relative path
           (e.g. "output" -> "output-<name>"). Absolute / "~"-prefixed roots
           (e.g. an iCloud or Dropbox vault) are left alone, since the leaf
           dirs below already get suffixed inside the same vault.
      [output] summaries_dir, daily_notes_dir, weekly_notes_dir, pdfs_dir
        -> append "-<name>" to each value (e.g. "Science/Papers" ->
           "Science/Papers-<name>"). Trailing slashes are stripped before
           appending.
      [state] root  -> append "-<name>"
      [logs]  root  -> append "-<name>"

    The [llm] mode/provider/model/effort fields are left identical to the
    benchmark so a diff between settings.toml and settings-<name>.toml shows
    exactly the variable under test.

    AFTER setup, edit user_preferences/settings-<name>.toml [llm] to point at
    your candidate provider/model and any runtime limits that differ from the
    benchmark.

    Common local-model variant using LM Studio, llama.cpp, vLLM, LocalAI, or
    another OpenAI-compatible server:

        [llm]
        mode = "api"
        provider = "openai-compatible"
        model = "your-loaded-model-name"
        effort = ""
        base_url = "http://127.0.0.1:1234/v1"
        api_key_env = ""
        env_file = ""
        timeout_seconds = 3600
        max_prompt_chars = 1000000

    `effort = ""` prevents CLI reasoning-effort settings from leaking into an
    API-backed local model. Increase timeout_seconds and max_prompt_chars when
    testing slow, large-context local models. For authenticated endpoints, set
    api_key_env to an environment variable name; launchd runs can resolve it
    from env_file or the default ~/.llm/.env.llm without storing secrets in the
    plist.

    `--force` overwrites an existing variant settings file.

2. schedule --name <variant>

    Renders a parallel launchd plist by reusing
    scripts/launchd/render-plist.sh (so uv-bin / PATH detection stays in one
    place) and post-substituting:

      - Label:           com.user.re-ass.<name>
      - ProgramArguments adds: --config user_preferences/settings-<name>.toml
      - StandardOut/ErrorPath: <variant [logs].root>/launchd.{stdout,stderr}.log
        (i.e. resolved from the variant TOML, not hardcoded — so an absolute
        [logs].root is honored)
      - StartCalendarInterval: same Hour as benchmark, Minute +30 by default
        (configurable via --hour / --minute). The 30-minute offset keeps both
        sides visible to the same arXiv listing window without competing for
        the fetch.

    The plist is installed via launchctl bootout/bootstrap (idempotent).

    Verify:

        launchctl list | grep re-ass
        launchctl kickstart -k gui/$(id -u)/com.user.re-ass.<name>

    If launchctl is unavailable (CI, non-mac, sandbox), this command prints
    the manual fallback instead and exits 0:

        uv run re-ass --config user_preferences/settings-<name>.toml

3. compare [--name <variant>] [options]

    Reads both TOMLs, uses the [state] and [output] roots inside them to
    discover artifacts, and produces a side-by-side report calibrated for
    AI-assisted analysis (see docs/ab-test.md for the user stories, rubric,
    and reviewer playbook the report is shaped around).

    Options:
      --name <variant>     auto-detected if exactly one settings-*.toml exists
      --benchmark PATH     defaults to user_preferences/settings.toml
      --date YYYY-MM-DD    specific announcement_date; default = most recent
                           date with run-summaries on both sides
      --last N             last N shared days
      --week               shared days since the most recent [notes].rotation_day
                           (current synthesis cycle)
      --all                every shared day
      --markdown           also write to docs/ab-test-<name>-<date>.md
      --json               emit machine-readable findings (no markdown)
      --no-ai-hints        omit the AI-reviewer header block

    Report sections per day, in reviewer-friendly order:
      - Scoreboard (read first)
      - Provider stamp (resolved from run-summary llm field, fallback settings)
      - Reliability (fatal/warning/error log events scoped to this announcement)
      - Candidate alignment (fetch-time parity prerequisite)
      - Threshold discipline (how many papers each side pushed into the
        min-summarize and min-selection bands)
      - Top-10 ranking overlap (Jaccard at top-3/5/10 + Kendall τ on shared)
      - Selection overlap (the visible top-N picks)
      - Per-paper score deltas (sorted by |delta|)
      - Rationale side-by-side for the union of selected papers
      - Daily note health (top-paper match, managed-heading present)
      - Weekly synthesis health (word band, orphan H2/H1 count, excerpt)
      - Paper-summary structure (words, sections, glossary terms, footnotes)

4. cleanup --name <variant>

    Archives the variant settings file to archive/ab-test/ (NEVER deletes),
    uninstalls the launchd job if present, and prints (does not run) the mv
    commands you can use to also archive the *-<name>/ directories.

    --remove-script prints the one-liner to also archive this script and
    revert .gitignore. Git-touching is left to you.

COMMON VARIANTS
---------------
  --name local    API provider backed by a local OpenAI-compatible endpoint
  --name codex    Codex CLI (must be logged in)
  --name gemini   Gemini API (GOOGLE_API_KEY or GOOGLE_APPLICATION_CREDENTIALS)
  --name claude   Claude API or Claude CLI
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
USER_PREFS = REPO_ROOT / "user_preferences"
BENCHMARK_SETTINGS = USER_PREFS / "settings.toml"
LAUNCHD_DIR = REPO_ROOT / "scripts" / "launchd"
LAUNCHD_TEMPLATE = LAUNCHD_DIR / "com.user.re-ass.plist.template"
RENDER_SCRIPT = LAUNCHD_DIR / "render-plist.sh"
RENDERED_BENCHMARK_PLIST = REPO_ROOT / "logs" / "launchd" / "com.user.re-ass.plist"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
ARCHIVE_DIR = REPO_ROOT / "archive" / "ab-test"
DOCS_DIR = REPO_ROOT / "docs"

VARIANT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


# (section, key) -> rewrite strategy.
# "append": new value is f"{old}-{name}".
# "logs_debug_prefix": replace a leading "logs/debug/" with f"logs-{name}/debug/".
SETUP_RULES: dict[tuple[str, str], str] = {
    # output.root: only suffix when relative. If the user has set it to an
    # absolute iCloud/Dropbox vault path, the leaves below already get
    # suffixed inside the same vault — duplicating the vault would be wrong.
    ("output", "root"): "append_if_relative",
    ("output", "summaries_dir"): "append",
    ("output", "daily_notes_dir"): "append",
    ("output", "weekly_notes_dir"): "append",
    ("output", "pdfs_dir"): "append",
    ("state", "root"): "append",
    ("logs", "root"): "append",
}

# At least one of these (section, key) entries must be successfully rewritten;
# otherwise the variant config would silently collide with the benchmark on
# state/logs/output and the comparison would be meaningless.
REQUIRED_REWRITE_KEYS: set[tuple[str, str]] = {
    ("state", "root"),
    ("logs", "root"),
}

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
KEY_VALUE_RE = re.compile(r'^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)"([^"]*)"(.*)$')


def cmd_setup(args: argparse.Namespace) -> int:
    name = _validate_variant(args.name)
    target = USER_PREFS / f"settings-{name}.toml"

    if not BENCHMARK_SETTINGS.exists():
        print(f"error: benchmark config not found: {BENCHMARK_SETTINGS}", file=sys.stderr)
        return 1
    if target.exists() and not args.force:
        print(f"error: {target} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 1

    rewritten_lines: list[str] = []
    rewrites: list[tuple[str, str, str]] = []  # (section.key, old, new)
    rewritten_keys: set[tuple[str, str]] = set()
    current_section = ""

    for raw_line in BENCHMARK_SETTINGS.read_text(encoding="utf-8").splitlines(keepends=True):
        line = raw_line.rstrip("\n")
        section_match = SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group(1).strip()
            rewritten_lines.append(raw_line)
            continue

        kv_match = KEY_VALUE_RE.match(line)
        if kv_match:
            indent, key, eq, value, trailing = kv_match.groups()
            rule = SETUP_RULES.get((current_section, key))
            if rule:
                new_value = _apply_rule(rule, value, name)
                if new_value != value:
                    rewrites.append((f"[{current_section}] {key}", value, new_value))
                    rewritten_keys.add((current_section, key))
                    new_line = f'{indent}{key}{eq}"{new_value}"{trailing}\n'
                    rewritten_lines.append(new_line)
                    continue
        rewritten_lines.append(raw_line)

    missing_required = REQUIRED_REWRITE_KEYS - rewritten_keys
    if missing_required:
        formatted = ", ".join(f"[{s}].{k}" for s, k in sorted(missing_required))
        print(
            f"error: required path keys were not rewritten ({formatted}). "
            "settings.toml structure has drifted; aborting to avoid a variant "
            "config that collides with the benchmark.",
            file=sys.stderr,
        )
        return 1

    target.write_text("".join(rewritten_lines), encoding="utf-8")

    print(f"Wrote {target.relative_to(REPO_ROOT)}")
    print("\nPath rewrites applied:")
    for label, old, new in rewrites:
        print(f"  {label}")
        print(f"    {old}")
        print(f"    -> {new}")

    print("\nNext steps:")
    print(f"  1. Edit {target.relative_to(REPO_ROOT)} [llm] block to point at your candidate provider/model.")
    print(f"  2. Sanity-check with: uv run re-ass --config {target.relative_to(REPO_ROOT)} --date YYYY-MM-DD")
    print(f"  3. Schedule it alongside the benchmark: python scripts/ab-test.py schedule --name {name}")
    return 0


def _apply_rule(rule: str, value: str, name: str) -> str:
    if rule in ("append", "append_if_relative"):
        if not value:
            return value
        if rule == "append_if_relative":
            expanded = Path(value).expanduser()
            if expanded.is_absolute() or value.startswith("~"):
                return value
        # Strip a single trailing slash so "Science/Papers/" becomes
        # "Science/Papers-<name>", not "Science/Papers/-<name>".
        stripped = value.rstrip("/")
        suffix = value[len(stripped):]  # "" or "/"
        return f"{stripped}-{name}{suffix}"
    return value


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------


def cmd_schedule(args: argparse.Namespace) -> int:
    name = _validate_variant(args.name)
    variant_settings = USER_PREFS / f"settings-{name}.toml"
    if not variant_settings.exists():
        print(f"error: {variant_settings} does not exist. Run `setup --name {name}` first.", file=sys.stderr)
        return 1

    if shutil.which("launchctl") is None:
        _print_manual_schedule_fallback(name)
        return 0

    if not RENDERED_BENCHMARK_PLIST.exists():
        print(f"Rendering benchmark plist via {RENDER_SCRIPT.relative_to(REPO_ROOT)}…")
        result = subprocess.run([str(RENDER_SCRIPT)], cwd=REPO_ROOT)
        if result.returncode != 0:
            print("error: render-plist.sh failed; aborting.", file=sys.stderr)
            return result.returncode
    if not RENDERED_BENCHMARK_PLIST.exists():
        print(f"error: rendered benchmark plist not found at {RENDERED_BENCHMARK_PLIST}", file=sys.stderr)
        return 1

    variant_paths = _resolve_variant_paths(name, variant_settings)
    plist_text = RENDERED_BENCHMARK_PLIST.read_text(encoding="utf-8")
    variant_plist_text = _patch_plist_for_variant(
        plist_text=plist_text,
        name=name,
        config_path=variant_settings.relative_to(REPO_ROOT).as_posix(),
        log_dir=variant_paths.logs_root,
        hour=args.hour,
        minute=args.minute,
    )

    rendered_dir = REPO_ROOT / "logs" / "launchd"
    rendered_dir.mkdir(parents=True, exist_ok=True)
    rendered_variant = rendered_dir / f"com.user.re-ass.{name}.plist"
    rendered_variant.write_text(variant_plist_text, encoding="utf-8")
    print(f"Rendered: {rendered_variant.relative_to(REPO_ROOT)}")

    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    installed = LAUNCH_AGENTS_DIR / f"com.user.re-ass.{name}.plist"
    shutil.copy2(rendered_variant, installed)

    lint = subprocess.run(["plutil", "-lint", str(installed)])
    if lint.returncode != 0:
        print("error: plutil -lint failed.", file=sys.stderr)
        return lint.returncode

    domain = f"gui/{os.getuid()}"
    label = f"com.user.re-ass.{name}"
    subprocess.run(["launchctl", "bootout", domain, str(installed)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bootstrap = subprocess.run(["launchctl", "bootstrap", domain, str(installed)])
    if bootstrap.returncode != 0:
        print("error: launchctl bootstrap failed.", file=sys.stderr)
        return bootstrap.returncode

    variant_paths.logs_root.mkdir(parents=True, exist_ok=True)

    try:
        logs_display = variant_paths.logs_root.relative_to(REPO_ROOT)
    except ValueError:
        logs_display = variant_paths.logs_root
    print(f"\nInstalled LaunchAgent: {installed}")
    print(f"Service:               {domain}/{label}")
    print(f"Schedule:              {_format_schedule_for_display(variant_plist_text)}")
    print(f"Logs land in:          {logs_display}/launchd.{{stdout,stderr}}.log")
    print("\nKick off a real run now (uses today's arXiv listing):")
    print(f"  launchctl kickstart -k {domain}/{label}")
    print("\nVerify both jobs are loaded:")
    print("  launchctl list | grep re-ass")
    return 0


def _patch_plist_for_variant(
    *, plist_text: str, name: str, config_path: str,
    log_dir: Path, hour: int | None, minute: int | None,
) -> str:
    label = f"com.user.re-ass.{name}"
    data = plistlib.loads(plist_text.encode("utf-8"))
    data["Label"] = label
    data["ProgramArguments"] = _variant_program_arguments(
        data.get("ProgramArguments", []),
        config_path,
    )
    data["StartCalendarInterval"] = _variant_start_calendar_interval(
        data.get("StartCalendarInterval"),
        hour,
        minute,
    )
    log_dir_str = log_dir.as_posix()
    data["StandardOutPath"] = f"{log_dir_str}/launchd.stdout.log"
    data["StandardErrorPath"] = f"{log_dir_str}/launchd.stderr.log"
    return plistlib.dumps(data, sort_keys=False).decode("utf-8")


def _variant_program_arguments(program_args: Any, config_path: str) -> list[str]:
    args = [str(arg) for arg in program_args] if isinstance(program_args, list) else []
    if not args:
        return ["uv", "run", "re-ass", "--config", config_path]

    if "--config" in args:
        config_index = args.index("--config")
        if config_index + 1 < len(args):
            args[config_index + 1] = config_path
        else:
            args.append(config_path)
        return args

    try:
        re_ass_index = args.index("re-ass")
    except ValueError:
        args.extend(["--config", config_path])
        return args
    return args[: re_ass_index + 1] + ["--config", config_path] + args[re_ass_index + 1:]


def _variant_start_calendar_interval(schedule: Any, hour: int | None, minute: int | None) -> Any:
    if isinstance(schedule, list):
        patched = []
        for entry in schedule:
            if isinstance(entry, dict):
                updated = dict(entry)
                updated["Hour"], updated["Minute"] = _resolve_schedule_time(entry, hour, minute)
                patched.append(updated)
            else:
                patched.append(entry)
        return patched

    if isinstance(schedule, dict):
        updated = dict(schedule)
        updated["Hour"], updated["Minute"] = _resolve_schedule_time(schedule, hour, minute)
        return updated

    resolved_hour, resolved_minute = _resolve_schedule_time({}, hour, minute)
    return {"Hour": resolved_hour, "Minute": resolved_minute}


def _resolve_schedule_time(entry: dict[str, Any], hour: int | None, minute: int | None) -> tuple[int, int]:
    base_hour = _coerce_int(entry.get("Hour"), 7)
    base_minute = _coerce_int(entry.get("Minute"), 0)

    resolved_hour = base_hour if hour is None else hour
    if minute is None:
        total_minutes = resolved_hour * 60 + base_minute + 30
        return (total_minutes // 60) % 24, total_minutes % 60
    return resolved_hour % 24, minute % 60


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_schedule_for_display(plist_text: str) -> str:
    data = plistlib.loads(plist_text.encode("utf-8"))
    schedule = data.get("StartCalendarInterval")
    if isinstance(schedule, list):
        entries = [entry for entry in schedule if isinstance(entry, dict)]
        if not entries:
            return "none"
        hours_minutes = {(entry.get("Hour"), entry.get("Minute")) for entry in entries}
        weekdays = [entry.get("Weekday") for entry in entries if entry.get("Weekday") is not None]
        if len(hours_minutes) == 1:
            hour, minute = next(iter(hours_minutes))
            if weekdays:
                return f"hour={hour}, minute={minute}, weekdays={','.join(str(w) for w in weekdays)}"
            return f"hour={hour}, minute={minute}"
        return f"{len(entries)} calendar entries"
    if isinstance(schedule, dict):
        return f"hour={schedule.get('Hour')}, minute={schedule.get('Minute')}"
    return "none"


def _print_manual_schedule_fallback(name: str) -> None:
    print("launchctl is not available; skipping launchd installation.")
    print("\nManual schedule fallback:")
    print(f"  After your usual benchmark run each day, run:")
    print(f"      uv run re-ass --config user_preferences/settings-{name}.toml")
    print("  Or wrap it in a cron entry / systemd timer / Windows Task Scheduler.")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@dataclass
class VariantPaths:
    name: str
    settings_path: Path
    output_root: Path
    summaries_dir: Path
    daily_notes_dir: Path
    weekly_notes_dir: Path
    state_runs_dir: Path
    logs_root: Path
    last_run_log: Path
    history_log: Path
    weekly_note_filename: str
    rotation_day: str
    llm_block: dict[str, Any]
    arxiv_thresholds: dict[str, Any]
    weekly_synthesis_heading: str
    weekly_synthesis_target: tuple[int, int]
    daily_top_paper_heading: str

    def daily_note(self, isodate: str) -> Path:
        return self.daily_notes_dir / f"{isodate}.md"

    def weekly_live_note(self) -> Path:
        return self.weekly_notes_dir / self.weekly_note_filename


def cmd_compare(args: argparse.Namespace) -> int:
    benchmark_settings = Path(args.benchmark) if args.benchmark else BENCHMARK_SETTINGS
    if not benchmark_settings.exists():
        print(f"error: benchmark settings not found: {benchmark_settings}", file=sys.stderr)
        return 1

    name = args.name
    if name is None:
        candidates = sorted(USER_PREFS.glob("settings-*.toml"))
        if len(candidates) == 1:
            name = candidates[0].stem.removeprefix("settings-")
            print(f"(auto-detected variant: {name})", file=sys.stderr)
        elif len(candidates) == 0:
            print("error: no user_preferences/settings-*.toml found. Run `setup --name <variant>` first.", file=sys.stderr)
            return 1
        else:
            print("error: multiple variant settings found; pass --name <variant>:", file=sys.stderr)
            for c in candidates:
                print(f"  {c.relative_to(REPO_ROOT)}", file=sys.stderr)
            return 1
    name = _validate_variant(name)

    variant_settings = USER_PREFS / f"settings-{name}.toml"
    if not variant_settings.exists():
        print(f"error: {variant_settings} does not exist.", file=sys.stderr)
        return 1

    bench = _resolve_variant_paths("benchmark", benchmark_settings)
    variant = _resolve_variant_paths(name, variant_settings)

    bench_runs = _load_announcement_runs(bench.state_runs_dir)
    variant_runs = _load_announcement_runs(variant.state_runs_dir)
    shared_dates = sorted(set(bench_runs) & set(variant_runs))

    if not shared_dates:
        print("error: no announcement_date is present on both sides yet.", file=sys.stderr)
        print(f"  benchmark dates: {sorted(bench_runs)[-5:] or 'none'}", file=sys.stderr)
        print(f"  {name} dates:    {sorted(variant_runs)[-5:] or 'none'}", file=sys.stderr)
        return 1

    target_dates = _select_target_dates(shared_dates, args, bench.rotation_day)
    if not target_dates:
        print("error: no dates matched the requested filter.", file=sys.stderr)
        return 1

    if args.json:
        findings = {
            "variant": name,
            "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "dates": list(target_dates),
            "days": [
                _structured_day_findings(d, bench_runs[d], variant_runs[d], bench, variant)
                for d in target_dates
            ],
        }
        print(json.dumps(findings, indent=2, default=str))
        return 0

    sections: list[str] = []
    for d in target_dates:
        sections.append(_format_day_report(d, bench_runs[d], variant_runs[d], bench, variant))

    report = _wrap_report(name, target_dates, sections, ai_hints=not args.no_ai_hints)
    print(report)

    if args.markdown:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        if len(target_dates) == 1:
            out_path = DOCS_DIR / f"ab-test-{name}-{target_dates[0]}.md"
        else:
            tag = f"{target_dates[0]}_to_{target_dates[-1]}"
            out_path = DOCS_DIR / f"ab-test-{name}-{tag}.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"\n(wrote {out_path.relative_to(REPO_ROOT)})", file=sys.stderr)

    return 0


def _structured_day_findings(
    isodate: str,
    bench: dict[str, Any],
    variant: dict[str, Any],
    bench_paths: VariantPaths,
    variant_paths: VariantPaths,
) -> dict[str, Any]:
    """Machine-readable mirror of `_format_day_report` for AI/eval pipelines.

    Same numbers; no markdown. Lets downstream tooling (eval rigs, dashboards,
    other AI runs) consume the comparison without parsing prose.
    """
    bench_top = _topn_keys(bench.get("ranking_results") or [], n=10)
    variant_top = _topn_keys(variant.get("ranking_results") or [], n=10)
    shared = [k for k in bench_top if k in variant_top]
    return {
        "date": isodate,
        "providers": {
            "benchmark": _llm_view(bench, bench_paths),
            variant_paths.name: _llm_view(variant, variant_paths),
        },
        "reliability": {
            "benchmark": _reliability_for_side(isodate, bench, bench_paths),
            variant_paths.name: _reliability_for_side(isodate, variant, variant_paths),
        },
        "candidate_counts": {
            "benchmark": len(bench.get("candidate_keys") or []),
            variant_paths.name: len(variant.get("candidate_keys") or []),
            "jaccard": _jaccard(set(bench.get("candidate_keys") or []), set(variant.get("candidate_keys") or [])),
        },
        "selection": {
            "benchmark": list(bench.get("selected_paper_keys") or []),
            variant_paths.name: list(variant.get("selected_paper_keys") or []),
            "jaccard": _jaccard(set(bench.get("selected_paper_keys") or []), set(variant.get("selected_paper_keys") or [])),
        },
        "topn": {
            "benchmark_top10": bench_top,
            f"{variant_paths.name}_top10": variant_top,
            "kendall_tau_shared": _kendall_tau(shared, bench_top, variant_top) if len(shared) >= 2 else None,
        },
        "weekly_synthesis": {
            "benchmark": _weekly_synthesis_health(bench_paths),
            variant_paths.name: _weekly_synthesis_health(variant_paths),
        },
        "daily_note": {
            "benchmark": _daily_note_health(bench_paths.daily_note(str(bench.get("note_date") or isodate)), bench_paths.daily_top_paper_heading),
            variant_paths.name: _daily_note_health(variant_paths.daily_note(str(variant.get("note_date") or isodate)), variant_paths.daily_top_paper_heading),
        },
        "paper_summaries": {
            key: {
                "benchmark": _paper_summary_stats(bench_paths.summaries_dir, key),
                variant_paths.name: _paper_summary_stats(variant_paths.summaries_dir, key),
            }
            for key in sorted(set(bench.get("selected_paper_keys") or []) | set(variant.get("selected_paper_keys") or []))
        },
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not (a | b):
        return 0.0
    return len(a & b) / len(a | b)


def _resolve_variant_paths(name: str, settings_path: Path) -> VariantPaths:
    data = tomllib.loads(settings_path.read_text(encoding="utf-8"))
    output = data.get("output", {})
    state = data.get("state", {})
    logs = data.get("logs", {})
    notes = data.get("notes", {})
    llm = data.get("llm", {})
    ranking_llm = data.get("llm-ranking", {})
    summary_llm = data.get("llm-summary", {})
    arxiv_cfg = data.get("arxiv", {})

    output_root = _expand(output.get("root", "output"))
    summaries_dir = _join(output_root, output.get("summaries_dir", "summaries"))
    daily_notes_dir = _join(output_root, output.get("daily_notes_dir", "daily-notes"))
    weekly_notes_dir = _join(output_root, output.get("weekly_notes_dir", "weekly-notes"))

    state_root = _expand(state.get("root", "state"))
    state_runs_dir = _join(state_root, state.get("runs_dir", "runs"))

    logs_root = _expand(logs.get("root", "logs"))
    if not logs_root.is_absolute():
        logs_root = (REPO_ROOT / logs_root).resolve()
    last_run_log = logs_root / str(logs.get("last_run_file", "last-run.log"))
    history_log = logs_root / str(logs.get("history_file", "history.log"))

    band = (
        _coerce_int(notes.get("weekly_synthesis_word_limit_start"), 100),
        _coerce_int(notes.get("weekly_synthesis_word_limit_end"), 200),
    )

    return VariantPaths(
        name=name,
        settings_path=settings_path,
        output_root=output_root,
        summaries_dir=summaries_dir,
        daily_notes_dir=daily_notes_dir,
        weekly_notes_dir=weekly_notes_dir,
        state_runs_dir=state_runs_dir,
        logs_root=logs_root,
        last_run_log=last_run_log,
        history_log=history_log,
        weekly_note_filename=str(notes.get("weekly_note_file", "this-weeks-arxiv-papers.md")),
        rotation_day=str(notes.get("rotation_day", "monday")).strip().lower(),
        llm_block=_settings_llm_view(llm, ranking_llm, summary_llm),
        arxiv_thresholds={
            "min_summarize_score": arxiv_cfg.get("min_summarize_score", 85),
            "min_selection_score": arxiv_cfg.get("min_selection_score", 70),
            "max_summarized_papers": arxiv_cfg.get("max_summarized_papers", 3),
        },
        weekly_synthesis_heading=str(notes.get("weekly_synthesis_heading", "## SYNTHESIS")),
        weekly_synthesis_target=band,
        daily_top_paper_heading=str(notes.get("daily_top_paper_heading", "## TODAY'S TOP PAPER")),
    )


def _settings_llm_view(
    llm: object,
    ranking_llm: object,
    summary_llm: object,
) -> dict[str, Any]:
    base = dict(llm) if isinstance(llm, dict) else {}
    ranking = {**base, **ranking_llm} if isinstance(ranking_llm, dict) else base
    summary = {**base, **summary_llm} if isinstance(summary_llm, dict) else base
    return {**base, "base": base, "ranking": ranking, "summary": summary}


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def _join(root: Path, sub: str) -> Path:
    sub_path = _expand(sub)
    if sub_path.is_absolute():
        return sub_path
    if root.is_absolute():
        return root / sub
    return (REPO_ROOT / root / sub).resolve()


_RUN_FILENAME_TS_RE = re.compile(r"--(\d{8}T\d{6})\.json$")


def _load_announcement_runs(runs_dir: Path) -> dict[str, dict[str, Any]]:
    """Map announcement_date -> latest run summary for that date.

    Skips overall and *-fatal labels; keeps only `announcement-<isodate>`.
    The "latest" run is identified by the timestamp encoded in the
    filename (state_store writes ``…--<YYYYMMDDTHHMMSS>.json``), not by
    filesystem mtime — mtime is perturbed by rsync/restore/git checkout.
    """
    if not runs_dir.exists():
        return {}
    candidates: dict[str, tuple[str, dict[str, Any]]] = {}
    for path in sorted(runs_dir.glob("*.json")):
        if "--announcement-" not in path.name or path.name.endswith("-fatal.json"):
            continue
        ts_match = _RUN_FILENAME_TS_RE.search(path.name)
        ts_key = ts_match.group(1) if ts_match else path.name  # fallback orders lexically
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ann = data.get("announcement_date")
        if not isinstance(ann, str):
            continue
        if ann not in candidates or ts_key > candidates[ann][0]:
            candidates[ann] = (ts_key, data)
    return {k: v[1] for k, v in candidates.items()}


_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _current_week_start(today: dt.date, rotation_day: str) -> dt.date:
    """Return the most recent rotation_day on or before `today`.

    Mirrors how note_manager rotates the weekly note: the synthesis cycle
    runs from the last rotation_day inclusive up through today.
    """
    rotation_idx = _WEEKDAY_INDEX.get(rotation_day, 0)
    days_since = (today.weekday() - rotation_idx) % 7
    return today - dt.timedelta(days=days_since)


def _select_target_dates(
    shared: list[str], args: argparse.Namespace, rotation_day: str,
) -> list[str]:
    if args.date:
        return [args.date] if args.date in shared else []
    if args.all:
        return shared
    if args.last:
        return shared[-args.last:]
    if args.week:
        cutoff = _current_week_start(dt.date.today(), rotation_day)
        return [d for d in shared if dt.date.fromisoformat(d) >= cutoff]
    return shared[-1:]


# --------- report formatting ---------


_AI_REVIEWER_PROMPT = """\
> **For the AI reviewer**: read each day's `### Scoreboard` first; that tells
> you whether a fatal/structural concern dominates a quality concern. Then walk
> sections top-down. The report is designed so you should rarely need to open
> the underlying notes or logs — but `docs/ab-test.md` (the rubric + user
> stories) is the canonical lens for grading. Write your assessment against
> those user stories; do not invent your own framing.
"""


def _wrap_report(name: str, dates: list[str], sections: list[str], *, ai_hints: bool) -> str:
    header = [
        f"# A/B comparison: benchmark vs {name}",
        "",
        f"Dates: {', '.join(dates)}",
        f"Generated: {dt.datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    if ai_hints:
        header.append(_AI_REVIEWER_PROMPT)
        header.append("")
    return "\n".join(header) + "\n\n".join(sections) + "\n"


def _format_day_report(
    isodate: str,
    bench: dict[str, Any],
    variant: dict[str, Any],
    bench_paths: VariantPaths,
    variant_paths: VariantPaths,
) -> str:
    """Render a complete per-day comparison.

    Sections are ordered to support an AI reviewer working top-down:
    1. Headline scoreboard — read-in-5-seconds verdict per dimension.
    2. Provider stamp — what each side actually used.
    3. Reliability — fatal/warning/error events scoped to this announcement.
    4. Candidate alignment — fetch-time parity, prerequisite for everything else.
    5. Threshold discipline — selection-pressure calibration vs config.
    6. Top-N ranking overlap, selection overlap, score deltas, rationales.
    7. Daily-note and weekly-synthesis content (with structural integrity flags).
    8. Paper-summary structure (word count, sections, glossary, footnotes).
    """
    lines: list[str] = [f"## {isodate}", ""]

    lines += _section_scoreboard(isodate, bench, variant, bench_paths, variant_paths)
    lines += _section_provider(isodate, bench, variant, bench_paths, variant_paths)
    lines += _section_reliability(isodate, bench, variant, bench_paths, variant_paths)
    lines += _section_candidate_alignment(bench, variant, variant_paths.name)
    lines += _section_threshold_discipline(bench, variant, bench_paths, variant_paths)
    lines += _section_topn_overlap(bench, variant, variant_paths.name)
    lines += _section_selection_overlap(bench, variant, variant_paths.name)
    lines += _section_score_deltas(bench, variant, variant_paths.name)
    lines += _section_rationale_side_by_side(bench, variant, variant_paths.name)
    lines += _section_daily_note(isodate, bench, variant, bench_paths, variant_paths)
    lines += _section_weekly_synthesis(bench_paths, variant_paths)
    lines += _section_paper_summary_structure(bench, variant, bench_paths, variant_paths)

    return "\n".join(lines)


def _llm_view(run: dict[str, Any], paths: VariantPaths) -> dict[str, Any]:
    """Prefer the run-summary's recorded llm stamp; fall back to settings TOML.

    The settings TOML is what the *next* run will use — the recorded stamp
    is what already happened.
    """
    stamp = run.get("llm")
    if isinstance(stamp, dict) and stamp:
        return stamp
    return paths.llm_block


def _section_provider(
    isodate: str,
    bench: dict[str, Any],
    variant: dict[str, Any],
    bench_paths: VariantPaths,
    variant_paths: VariantPaths,
) -> list[str]:
    out = ["### Provider stamp", ""]
    out.append(f"- benchmark: {_provider_blurb(bench, bench_paths)}")
    out.append(f"- {variant_paths.name}: {_provider_blurb(variant, variant_paths)}")
    out.append("")
    out.append(
        "  (Resolved from the run-summary `llm` field where present, falling "
        "back to the `[llm]`, `[llm-ranking]`, and `[llm-summary]` blocks of each "
        "settings TOML. The TOML reflects what the *next* run will use; the stamp "
        "reflects what already happened.)"
    )
    out.append("")
    return out


def _provider_blurb(run: dict[str, Any], paths: VariantPaths) -> str:
    view = _llm_view(run, paths)
    parts = []
    for key in ("mode", "provider", "model", "effort"):
        value = view.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    for role in ("ranking", "summary"):
        role_view = view.get(role)
        if not isinstance(role_view, dict):
            continue
        role_parts = []
        for key in ("mode", "provider", "model", "effort"):
            value = role_view.get(key)
            if value not in (None, ""):
                role_parts.append(f"{key}={value}")
        if role_parts:
            parts.append(f"{role}=({', '.join(role_parts)})")
    source = "run-summary" if (isinstance(run.get("llm"), dict) and run.get("llm")) else "settings TOML"
    parts.append(f"(source: {source})")
    log_status = (
        f"last-run.log mtime={_format_mtime(paths.last_run_log)}"
        if paths.last_run_log.exists()
        else "last-run.log missing"
    )
    parts.append(log_status)
    return " ".join(parts) if parts else "(no llm metadata)"


def _format_mtime(path: Path) -> str:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return "?"


def _section_candidate_alignment(bench: dict[str, Any], variant: dict[str, Any], name: str) -> list[str]:
    bench_keys = set(bench.get("candidate_keys") or [])
    variant_keys = set(variant.get("candidate_keys") or [])
    only_b = sorted(bench_keys - variant_keys)
    only_v = sorted(variant_keys - bench_keys)
    out = [
        "### Candidate alignment",
        "",
        f"- benchmark candidates: {len(bench_keys)}",
        f"- {name} candidates:    {len(variant_keys)}",
    ]
    if not only_b and not only_v:
        out.append("- candidate sets are identical")
    else:
        if only_b:
            out.append(f"- benchmark only ({len(only_b)}): {', '.join(only_b[:8])}{'…' if len(only_b) > 8 else ''}")
        if only_v:
            out.append(f"- {name} only ({len(only_v)}): {', '.join(only_v[:8])}{'…' if len(only_v) > 8 else ''}")
        out.append("- non-empty diff suggests a fetch-time anomaly (rate-limit, timing skew). Investigate before drawing quality conclusions.")
    out.append("")
    return out


def _section_score_deltas(bench: dict[str, Any], variant: dict[str, Any], name: str) -> list[str]:
    bench_by_key = {item["paper_key"]: item for item in (bench.get("ranking_results") or [])}
    variant_by_key = {item["paper_key"]: item for item in (variant.get("ranking_results") or [])}
    shared = sorted(set(bench_by_key) & set(variant_by_key))
    if not shared:
        return ["### Per-paper score deltas", "", "(no shared candidates)", ""]

    rows: list[tuple[float, str, str]] = []
    for key in shared:
        b = bench_by_key[key]
        v = variant_by_key[key]
        bs = float(b.get("score") or 0.0)
        vs = float(v.get("score") or 0.0)
        delta = vs - bs
        sci_b, sci_v = b.get("science_match"), v.get("science_match")
        met_b, met_v = b.get("method_match"), v.get("method_match")
        agreement = []
        if sci_b is not None or sci_v is not None:
            agreement.append(f"sci={sci_b}/{sci_v}")
        if met_b is not None or met_v is not None:
            agreement.append(f"meth={met_b}/{met_v}")
        title = (b.get("title") or v.get("title") or "")[:80]
        rows.append((abs(delta), f"  {key}  bench={bs:.1f}  {name}={vs:.1f}  Δ={delta:+.1f}  {' '.join(agreement)}".rstrip(), title))

    rows.sort(reverse=True)
    out = ["### Per-paper score deltas (sorted by |Δ|)", ""]
    for _, row, title in rows[:30]:
        out.append(row)
        out.append(f"      {title}")
    if len(rows) > 30:
        out.append(f"  … {len(rows) - 30} more")
    out.append("")
    return out


def _section_selection_overlap(bench: dict[str, Any], variant: dict[str, Any], name: str) -> list[str]:
    b_sel = set(bench.get("selected_paper_keys") or [])
    v_sel = set(variant.get("selected_paper_keys") or [])
    union = b_sel | v_sel
    inter = b_sel & v_sel
    jaccard = (len(inter) / len(union)) if union else 1.0
    out = [
        "### Selection overlap",
        "",
        f"- benchmark selected: {sorted(b_sel)}",
        f"- {name} selected:    {sorted(v_sel)}",
        f"- Jaccard: {jaccard:.2f}",
    ]
    only_b = sorted(b_sel - v_sel)
    only_v = sorted(v_sel - b_sel)
    if only_b:
        out.append(f"- benchmark only: {only_b}")
    if only_v:
        out.append(f"- {name} only:    {only_v}")
    out.append("")
    return out


def _section_rationale_side_by_side(bench: dict[str, Any], variant: dict[str, Any], name: str) -> list[str]:
    b_sel = set(bench.get("selected_paper_keys") or [])
    v_sel = set(variant.get("selected_paper_keys") or [])
    union = sorted(b_sel | v_sel)
    if not union:
        return ["### Rationale comparison", "", "(no selections on either side)", ""]

    bench_by_key = {item["paper_key"]: item for item in (bench.get("ranking_results") or [])}
    variant_by_key = {item["paper_key"]: item for item in (variant.get("ranking_results") or [])}

    out = ["### Rationale side-by-side", ""]
    for key in union:
        title = ((bench_by_key.get(key) or variant_by_key.get(key) or {}).get("title") or "")[:100]
        out.append(f"**{key}** — {title}")
        b_rationale = (bench_by_key.get(key, {}).get("rationale") or "(not in benchmark ranking)").strip()
        v_rationale = (variant_by_key.get(key, {}).get("rationale") or "(not in variant ranking)").strip()
        out.append(f"- benchmark: {b_rationale}")
        out.append(f"- {name}:    {v_rationale}")
        out.append("")
    return out


# --------- new AI-focused diagnostics ---------

_TS_IN_FILENAME_RE = re.compile(r"--(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6}Z)\.json$")
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})[,.]?(\d{3,6})?")
_PIPELINE_BANNER_RE = re.compile(r"re-ass run (started|finished)")


def _filename_timestamp(path: Path) -> dt.datetime | None:
    m = _TS_IN_FILENAME_RE.search(path.name)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S-%fZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _fatal_runs_for_announcement(runs_dir: Path, announcement_date: str) -> list[Path]:
    if not runs_dir.exists():
        return []
    return sorted(runs_dir.glob(f"*--announcement-{announcement_date}-fatal--*.json"))


def _scan_log_window(log_path: Path, *, anchor: dt.datetime | None, window_minutes: int = 90) -> dict[str, Any]:
    """Count WARNING / ERROR / fatal events around `anchor` in a log file.

    Re-ass writes its history to `[logs].history_file`. Each line is prefixed
    with an `asctime`-style timestamp. We count any line tagged WARNING or
    ERROR within `±window_minutes` of `anchor` (the run-summary's filename
    timestamp). When `anchor` is None we count over the whole file — useful
    for a quick smoke check.

    Returns a dict {warnings, errors, fatal, samples (deduped, ≤8)}.
    """
    out = {"warnings": 0, "errors": 0, "fatal": 0, "samples": [], "log_exists": log_path.exists()}
    if not log_path.exists():
        return out
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        out["log_exists"] = False
        return out

    if anchor is not None:
        start = anchor - dt.timedelta(minutes=window_minutes)
        end = anchor + dt.timedelta(minutes=window_minutes)
    else:
        start = end = None

    counts: dict[str, int] = {}
    for raw in text.splitlines():
        m = _LOG_TS_RE.match(raw)
        if start is not None:
            if m is None:
                continue
            try:
                ts = dt.datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
            except ValueError:
                continue
            if ts < start or ts > end:
                continue
        if " WARNING " in raw or raw.endswith(" WARNING"):
            out["warnings"] += 1
            counts[_log_pattern(raw)] = counts.get(_log_pattern(raw), 0) + 1
        elif " ERROR " in raw or raw.endswith(" ERROR"):
            out["errors"] += 1
            counts[_log_pattern(raw)] = counts.get(_log_pattern(raw), 0) + 1
        if "Fatal" in raw or "fatal_error" in raw:
            out["fatal"] += 1

    out["samples"] = [
        f"{count}× {pattern}" for pattern, count in sorted(counts.items(), key=lambda kv: -kv[1])[:8]
    ]
    return out


def _log_pattern(line: str) -> str:
    """Reduce a log line to a stable shape so duplicates can be counted.

    Strips the leading timestamp/logger fields and elides obvious variable
    bits (arxiv ids, paper keys, numbers) so "Glossary generation failed;
    skipping section: …" lines bucket together regardless of paper.
    """
    body = line
    body = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.][\d]+\s+(WARNING|ERROR|INFO|DEBUG)\s+\S+:\s*", "", body)
    body = re.sub(r"arxiv:\d{4}\.\d{4,6}", "arxiv:<id>", body)
    body = re.sub(r"\b\d{4,}\b", "<n>", body)
    return body.strip()[:120]


def _section_reliability(
    isodate: str,
    bench: dict[str, Any],
    variant: dict[str, Any],
    bench_paths: VariantPaths,
    variant_paths: VariantPaths,
) -> list[str]:
    out = [
        "### Reliability (this announcement window)",
        "",
        "Counts of WARNING / ERROR log lines within ±90 minutes of the run-summary "
        "timestamp, plus any `*-fatal.json` siblings for this announcement date. A "
        "clean side is `fatal=0 warnings=0 errors=0`. Recurring patterns (e.g. "
        "\"Glossary generation failed\", \"tags outside supplied keyword list\") "
        "point at the variant's prompt-following budget.",
        "",
    ]
    for label, run, paths in (
        ("benchmark", bench, bench_paths),
        (variant_paths.name, variant, variant_paths),
    ):
        diag = _reliability_for_side(isodate, run, paths)
        out.append(f"- **{label}**: fatal={diag['fatal']} warnings={diag['warnings']} errors={diag['errors']}")
        if diag["fatal_messages"]:
            for msg in diag["fatal_messages"]:
                out.append(f"    - FATAL: {msg}")
        if diag["samples"]:
            out.append("    - top patterns:")
            for sample in diag["samples"]:
                out.append(f"      - {sample}")
    out.append("")
    return out


def _reliability_for_side(isodate: str, run: dict[str, Any], paths: VariantPaths) -> dict[str, Any]:
    # Anchor on the run-summary filename timestamp if we can find it; the
    # caller already loaded the run dict but not its path, so re-locate.
    anchor: dt.datetime | None = None
    for p in paths.state_runs_dir.glob(f"*--announcement-{isodate}--*.json"):
        if "fatal" in p.name:
            continue
        ts = _filename_timestamp(p)
        if ts is not None and (anchor is None or ts > anchor):
            anchor = ts

    log_diag = _scan_log_window(paths.history_log, anchor=anchor)
    fatal_paths = _fatal_runs_for_announcement(paths.state_runs_dir, isodate)
    fatal_messages: list[str] = []
    for fp in fatal_paths:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        err = data.get("fatal_error") or "(no fatal_error field)"
        fatal_messages.append(f"{fp.name}: {err}")
    if run.get("fatal_error"):
        fatal_messages.append(f"in this run: {run['fatal_error']}")

    return {
        "fatal": len(fatal_messages),
        "fatal_messages": fatal_messages,
        "warnings": log_diag["warnings"],
        "errors": log_diag["errors"],
        "samples": log_diag["samples"],
        "log_exists": log_diag["log_exists"],
    }


def _section_threshold_discipline(
    bench: dict[str, Any],
    variant: dict[str, Any],
    bench_paths: VariantPaths,
    variant_paths: VariantPaths,
) -> list[str]:
    out = [
        "### Threshold discipline",
        "",
        "How many papers each side pushed into the `min_summarize_score` and "
        "`min_selection_score` bands. Papers clearing `min_summarize_score` are "
        "selected up to `max_summarized_papers`; overflow demotes to weekly interest. "
        "A healthy run has 0–1 papers in the summarise band; consistently hitting the "
        "cap suggests score inflation on borderline papers rather than a misconfiguration.",
        "",
    ]
    for label, run, paths in (
        ("benchmark", bench, bench_paths),
        (variant_paths.name, variant, variant_paths),
    ):
        min_summary = float(paths.arxiv_thresholds.get("min_summarize_score", 85))
        minsel = float(paths.arxiv_thresholds.get("min_selection_score", 70))
        cap = int(paths.arxiv_thresholds.get("max_summarized_papers", 3))
        rr = run.get("ranking_results") or []
        n_summary = sum(1 for x in rr if (x.get("score") or 0) >= min_summary)
        n_minsel = sum(1 for x in rr if (x.get("score") or 0) >= minsel)
        selected = len(run.get("selected_paper_keys") or [])
        overflow = max(0, n_summary - cap)
        hit_cap = selected >= cap and n_summary >= cap
        flag = " ⚠ hit cap — check whether capped papers are genuinely on-priority" if hit_cap else ""
        out.append(
            f"- **{label}**: min_summarize_score={min_summary} cap={cap} | "
            f"≥{min_summary}: {n_summary} | ≥{minsel}: {n_minsel} | "
            f"selected: {selected} | overflow_to_interest: {overflow}{flag}"
        )
    out.append("")
    return out


def _section_topn_overlap(bench: dict[str, Any], variant: dict[str, Any], name: str) -> list[str]:
    """Top-N agreement is a more honest signal than selection overlap.

    Selection overlap can be 0 between two providers who agree to within a
    few points on the top-3 ranking but happen to straddle the min_summarize
    threshold. Top-N overlap captures the underlying agreement: are they reading
    the same pool of papers as "interesting"?
    """
    bench_top = _topn_keys(bench.get("ranking_results") or [], n=10)
    variant_top = _topn_keys(variant.get("ranking_results") or [], n=10)
    out = ["### Top-10 ranking overlap", ""]
    for n in (3, 5, 10):
        b = set(bench_top[:n])
        v = set(variant_top[:n])
        inter = b & v
        union = b | v
        j = (len(inter) / len(union)) if union else 1.0
        out.append(f"- top-{n} Jaccard: {j:.2f} (shared {len(inter)} / union {len(union)})")
    out.append("")
    out.append(f"  benchmark top-10: {bench_top}")
    out.append(f"  {name} top-10:    {variant_top}")
    out.append("")
    shared_positions = [k for k in bench_top if k in variant_top]
    if len(shared_positions) >= 2:
        tau = _kendall_tau(shared_positions, bench_top, variant_top)
        out.append(f"  Kendall τ on shared keys (top-10): {tau:+.2f}  (+1 = same order, 0 = unrelated, −1 = reversed)")
    out.append("")
    return out


def _topn_keys(ranking_results: list[dict[str, Any]], *, n: int) -> list[str]:
    ranked = sorted(ranking_results, key=lambda x: -float(x.get("score") or 0))
    return [str(item.get("paper_key", "?")) for item in ranked[:n]]


def _kendall_tau(shared: list[str], bench_top: list[str], variant_top: list[str]) -> float:
    bench_rank = {k: i for i, k in enumerate(bench_top)}
    variant_rank = {k: i for i, k in enumerate(variant_top)}
    pairs = [(bench_rank[a], variant_rank[a], bench_rank[b], variant_rank[b])
             for i, a in enumerate(shared) for b in shared[i + 1:]]
    if not pairs:
        return 0.0
    concordant = sum(1 for ba, va, bb, vb in pairs if (ba < bb) == (va < vb))
    discordant = len(pairs) - concordant
    return (concordant - discordant) / len(pairs)


def _section_daily_note(
    isodate: str,
    bench: dict[str, Any],
    variant: dict[str, Any],
    bench_paths: VariantPaths,
    variant_paths: VariantPaths,
) -> list[str]:
    out = [
        "### Daily note (the note the user sees on the matching weekday)",
        "",
        "Word count is scoped to the body inside the configured top-paper "
        "heading. The daily-note file may contain user-owned sections "
        "(tasks, meetings, freeform notes) that re-ass has no opinion about.",
        "",
    ]
    bench_note_date = str(bench.get("note_date") or isodate)
    variant_note_date = str(variant.get("note_date") or isodate)
    bench_top = (bench.get("selected_paper_keys") or [None])[0]
    variant_top = (variant.get("selected_paper_keys") or [None])[0]
    out.append(
        f"- top-paper match: {'✓' if bench_top and bench_top == variant_top else '✗'}"
        f" — benchmark={bench_top}  {variant_paths.name}={variant_top}"
    )
    for label, note_date, paths in (
        ("benchmark", bench_note_date, bench_paths),
        (variant_paths.name, variant_note_date, variant_paths),
    ):
        note = paths.daily_note(note_date)
        diag = _daily_note_health(note, paths.daily_top_paper_heading)
        out.append(
            f"- {label} {note.name}: exists={diag['exists']}"
            f" managed_section_present={diag['heading_present']}"
            f" managed_body_words={diag['body_words']}"
        )
        if diag["concerns"]:
            for c in diag["concerns"]:
                out.append(f"    ⚠ {c}")
    out.append("")
    return out


def _daily_note_health(note_path: Path, top_paper_heading: str) -> dict[str, Any]:
    """Diagnostics for the part of the daily note re-ass actually owns.

    Word count is scoped to the body inside `top_paper_heading`, not the whole
    file: users often keep tasks, meetings and notes in the same daily note,
    and re-ass has no opinion about that content. Structural concerns
    (heading missing, heading duplicated) are still legitimate at the
    whole-file level because the configured heading is reserved for re-ass.
    """
    diag: dict[str, Any] = {
        "exists": note_path.exists(),
        "body_words": 0,
        "heading_present": False,
        "concerns": [],
    }
    if not note_path.exists():
        diag["concerns"].append(f"note missing at {note_path}")
        return diag
    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError as e:
        diag["concerns"].append(f"unreadable: {e}")
        return diag
    diag["heading_present"] = top_paper_heading in text
    if diag["heading_present"]:
        diag["body_words"] = len(_extract_section(text, top_paper_heading).split())
    else:
        diag["concerns"].append(f"configured top-paper heading '{top_paper_heading}' not found")
    if text.count(top_paper_heading) > 1:
        diag["concerns"].append(f"duplicate heading '{top_paper_heading}' present ({text.count(top_paper_heading)}×)")
    return diag


def _section_weekly_synthesis(bench_paths: VariantPaths, variant_paths: VariantPaths) -> list[str]:
    out = [
        "### Weekly synthesis (current rolling note)",
        "",
        "The synthesis body sits inside the managed heading. Word-band targets "
        "come from `[notes].weekly_synthesis_word_limit_*`. Orphan H2/H1 inside "
        "the body indicates the model split the section — historically a "
        "self-healing bug after re-ass v0.x.",
        "",
    ]
    for label, paths in (("benchmark", bench_paths), (variant_paths.name, variant_paths)):
        diag = _weekly_synthesis_health(paths)
        target = f"{paths.weekly_synthesis_target[0]}–{paths.weekly_synthesis_target[1]}"
        in_band = paths.weekly_synthesis_target[0] <= diag["body_words"] <= paths.weekly_synthesis_target[1]
        band_flag = "" if in_band else f" ⚠ outside target band {target}"
        out.append(f"- **{label}**: body_words={diag['body_words']} target={target}{band_flag}")
        out.append(f"    file: {diag['path']}")
        if diag["orphan_h2_count"]:
            out.append(f"    ⚠ {diag['orphan_h2_count']} orphan H2 line(s) inside synthesis body: {diag['orphan_h2_samples']}")
        if diag["orphan_h1_count"]:
            out.append(f"    ⚠ {diag['orphan_h1_count']} orphan H1 line(s) inside synthesis body")
        if diag["excerpt"]:
            out.append("    excerpt (first ~60 words):")
            out.append(f"      {diag['excerpt']}")
    out.append("")
    return out


def _weekly_synthesis_health(paths: VariantPaths) -> dict[str, Any]:
    diag: dict[str, Any] = {
        "path": str(paths.weekly_live_note()),
        "body_words": 0,
        "orphan_h2_count": 0,
        "orphan_h2_samples": [],
        "orphan_h1_count": 0,
        "excerpt": "",
    }
    note_path = paths.weekly_live_note()
    if not note_path.exists():
        return diag
    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError:
        return diag
    body = _extract_section(text, paths.weekly_synthesis_heading)
    diag["body_words"] = len(body.split())

    h2_lines = [line for line in body.splitlines() if line.startswith("## ")]
    h1_lines = [line for line in body.splitlines() if line.startswith("# ")]
    diag["orphan_h2_count"] = len(h2_lines)
    diag["orphan_h2_samples"] = h2_lines[:3]
    diag["orphan_h1_count"] = len(h1_lines)

    words = body.split()
    diag["excerpt"] = " ".join(words[:60]) + ("…" if len(words) > 60 else "")
    return diag


def _extract_section(text: str, heading: str) -> str:
    """Return the body of a `## Foo` section, ending at the next `---` line
    that's followed by `## ` (the re-ass managed-section convention)."""
    lines = text.splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return ""
    end = len(lines)
    for i in range(start, len(lines) - 1):
        if lines[i].strip() == "---":
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and lines[j].startswith("## "):
                end = i
                break
    return "\n".join(lines[start:end]).strip()


_PAPER_FILENAME_KEY_RE = re.compile(r"\[arXiv\s+(\d{4}\.\d{4,6})\]")


def _section_paper_summary_structure(
    bench: dict[str, Any],
    variant: dict[str, Any],
    bench_paths: VariantPaths,
    variant_paths: VariantPaths,
) -> list[str]:
    out = [
        "### Paper-summary structure (union of selected papers)",
        "",
        "Per side, for each selected paper, surface enough structural data "
        "that an AI reviewer doesn't have to open the file just to know "
        "whether sections are present and the glossary survived validation. "
        "Big asymmetries (e.g. one side has 0 glossary terms because of "
        "retries) signal where the model's instruction-following failed.",
        "",
    ]
    union = sorted(set(bench.get("selected_paper_keys") or []) | set(variant.get("selected_paper_keys") or []))
    if not union:
        out.append("(no selections on either side)")
        out.append("")
        return out

    for key in union:
        out.append(f"- **{key}**")
        for label, paths in (("benchmark", bench_paths), (variant_paths.name, variant_paths)):
            stats = _paper_summary_stats(paths.summaries_dir, key)
            if stats is None:
                out.append(f"    - {label}: (no summary file found)")
                continue
            out.append(
                f"    - {label}: words={stats['words']} "
                f"sections={stats['section_count']} "
                f"glossary_terms={stats['glossary_terms']} "
                f"footnotes={stats['footnotes']} "
                f"weaknesses={'yes' if stats['has_weaknesses'] else 'no'}"
            )
            if stats["missing_sections"]:
                out.append(f"    ⚠ {label} missing sections: {', '.join(stats['missing_sections'])}")
    out.append("")
    return out


_EXPECTED_SECTIONS = (
    "Key Ideas", "Introduction", "Data", "Method", "Results",
    "Discussion", "Weaknesses", "Conclusions", "Glossary", "Tags", "References",
)


def _paper_summary_stats(summaries_dir: Path, paper_key: str) -> dict[str, Any] | None:
    if not summaries_dir.exists():
        return None
    arxiv_id = paper_key.split(":", 1)[-1]
    matches = list(summaries_dir.glob(f"*{arxiv_id}*.md"))
    if not matches:
        return None
    text = matches[0].read_text(encoding="utf-8", errors="replace")
    section_count = sum(1 for line in text.splitlines() if line.startswith("## "))
    present = {name.lower() for name in _EXPECTED_SECTIONS if f"## {name}".lower() in text.lower()}
    missing = [name for name in _EXPECTED_SECTIONS if name.lower() not in present]
    glossary_terms = 0
    if "## Glossary" in text:
        gloss = text.split("## Glossary", 1)[1].split("\n## ", 1)[0]
        glossary_terms = sum(1 for line in gloss.splitlines() if line.strip().startswith("| **"))
    footnotes = len(re.findall(r"^\[\^[^\]]+\]:", text, flags=re.MULTILINE))
    return {
        "path": str(matches[0]),
        "words": len(text.split()),
        "section_count": section_count,
        "glossary_terms": glossary_terms,
        "footnotes": footnotes,
        "has_weaknesses": "## Weaknesses" in text,
        "missing_sections": missing,
    }


def _section_scoreboard(
    isodate: str,
    bench: dict[str, Any],
    variant: dict[str, Any],
    bench_paths: VariantPaths,
    variant_paths: VariantPaths,
) -> list[str]:
    """One-glance verdict for the AI reviewer.

    Distilled to: did each side complete; do their candidate pools agree;
    do their top picks agree; how clean is the variant's reliability log;
    is the weekly synthesis structurally OK. Tells the reviewer where to
    look first.
    """
    bench_rel = _reliability_for_side(isodate, bench, bench_paths)
    var_rel = _reliability_for_side(isodate, variant, variant_paths)
    bench_top = (bench.get("selected_paper_keys") or [None])[0]
    variant_top = (variant.get("selected_paper_keys") or [None])[0]
    cand_bench = set(bench.get("candidate_keys") or [])
    cand_var = set(variant.get("candidate_keys") or [])
    cand_jaccard = (len(cand_bench & cand_var) / len(cand_bench | cand_var)) if (cand_bench | cand_var) else 1.0

    bench_weekly = _weekly_synthesis_health(bench_paths)
    var_weekly = _weekly_synthesis_health(variant_paths)

    def _flag(cond: bool) -> str:
        return "✓" if cond else "✗"

    rows = [
        ("candidate-set parity (Jaccard)", f"{cand_jaccard:.2f}", _flag(cand_jaccard >= 0.95)),
        ("top-1 selection agreement", _flag(bool(bench_top) and bench_top == variant_top), ""),
        (f"benchmark reliability (fatal/W/E)", f"{bench_rel['fatal']}/{bench_rel['warnings']}/{bench_rel['errors']}", _flag(bench_rel["fatal"] == 0)),
        (f"{variant_paths.name} reliability (fatal/W/E)", f"{var_rel['fatal']}/{var_rel['warnings']}/{var_rel['errors']}", _flag(var_rel["fatal"] == 0)),
        (f"benchmark weekly synthesis structurally clean", _flag(bench_weekly["orphan_h2_count"] == 0 and bench_weekly["orphan_h1_count"] == 0), ""),
        (f"{variant_paths.name} weekly synthesis structurally clean", _flag(var_weekly["orphan_h2_count"] == 0 and var_weekly["orphan_h1_count"] == 0), ""),
    ]
    out = ["### Scoreboard (read first)", ""]
    for label, value, flag in rows:
        out.append(f"- {label}: **{value}** {flag}".rstrip())
    out.append("")
    return out


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def cmd_cleanup(args: argparse.Namespace) -> int:
    name = _validate_variant(args.name)

    settings_path = USER_PREFS / f"settings-{name}.toml"
    archived_to: Path | None = None
    if settings_path.exists():
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        archived_to = ARCHIVE_DIR / f"settings-{name}-{ts}.toml"
        shutil.move(str(settings_path), str(archived_to))
        print(f"Archived {settings_path.relative_to(REPO_ROOT)} -> {archived_to.relative_to(REPO_ROOT)}")
    else:
        print(f"(no {settings_path.relative_to(REPO_ROOT)} to archive)")

    label = f"com.user.re-ass.{name}"
    installed = LAUNCH_AGENTS_DIR / f"{label}.plist"
    if shutil.which("launchctl") and installed.exists():
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(installed)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        plist_archive_dir = ARCHIVE_DIR / "launchd"
        plist_archive_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        archived_plist = plist_archive_dir / f"{label}-{ts}.plist"
        shutil.move(str(installed), str(archived_plist))
        print(f"Uninstalled launchd job: {label}")
        print(f"Archived plist:          {archived_plist.relative_to(REPO_ROOT)}")
    else:
        print(f"(no launchd job to uninstall: {label})")

    print("\n*-{n}/ directories were NOT touched. To archive them too:".format(n=name))
    for sub in (f"output-{name}", f"state-{name}", f"logs-{name}"):
        full = REPO_ROOT / sub
        if full.exists():
            print(f"  mv {full.relative_to(REPO_ROOT)} archive/ab-test/{sub}-$(date -u +%Y%m%dT%H%M%S)/")
    print("(Note: any output paths inside iCloud or Dropbox vaults — e.g. summaries_dir / pdfs_dir — live outside the repo and must be moved by hand.)")

    if args.remove_script:
        print("\nTo also archive this script and revert .gitignore (run by hand):")
        print(f"  git mv scripts/ab-test.py archive/ab-test/ab-test.py")
        print(f"  git checkout -- .gitignore  # only if the four *-*/ globs were the only change")
    return 0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _validate_variant(name: str) -> str:
    if not name or not VARIANT_NAME_RE.match(name):
        print(f"error: invalid --name {name!r}; use lowercase letters, digits, dashes or underscores.", file=sys.stderr)
        sys.exit(2)
    return name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="A/B test re-ass against an alternative LLM provider. See module docstring for the manual.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_setup = sub.add_parser("setup", help="create user_preferences/settings-<name>.toml")
    p_setup.add_argument("--name", required=True)
    p_setup.add_argument("--force", action="store_true", help="overwrite existing variant settings file")
    p_setup.set_defaults(func=cmd_setup)

    p_sched = sub.add_parser("schedule", help="install com.user.re-ass.<name> launchd job")
    p_sched.add_argument("--name", required=True)
    p_sched.add_argument("--hour", type=int, default=None,
                         help="StartCalendarInterval Hour (default: benchmark hour)")
    p_sched.add_argument("--minute", type=int, default=None,
                         help="StartCalendarInterval Minute (default: benchmark minute + 30)")
    p_sched.set_defaults(func=cmd_schedule)

    p_cmp = sub.add_parser("compare", help="report on most recent shared run (default) or a specific date/range")
    p_cmp.add_argument("--name", default=None, help="variant name; auto-detected if exactly one variant config exists")
    p_cmp.add_argument("--benchmark", default=None, help="path to benchmark settings.toml (default: user_preferences/settings.toml)")
    g = p_cmp.add_mutually_exclusive_group()
    g.add_argument("--date", help="specific announcement_date YYYY-MM-DD")
    g.add_argument("--last", type=int, help="last N shared days")
    g.add_argument("--week", action="store_true",
                   help="shared days since the most recent [notes].rotation_day (current synthesis cycle)")
    g.add_argument("--all", action="store_true", help="every shared day")
    p_cmp.add_argument("--markdown", action="store_true", help="also write the report under docs/")
    p_cmp.add_argument("--json", action="store_true",
                       help="emit machine-readable JSON findings instead of the markdown report")
    p_cmp.add_argument("--no-ai-hints", action="store_true",
                       help="omit the 'for the AI reviewer' header block in the markdown report")
    p_cmp.set_defaults(func=cmd_compare)

    p_clean = sub.add_parser("cleanup", help="archive variant settings + uninstall launchd job")
    p_clean.add_argument("--name", required=True)
    p_clean.add_argument("--remove-script", action="store_true",
                         help="also print the commands to archive this script and revert .gitignore")
    p_clean.set_defaults(func=cmd_cleanup)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
