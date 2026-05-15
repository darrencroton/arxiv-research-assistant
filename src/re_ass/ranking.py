"""LLM-first paper ranking for re-ass."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
import json
import logging
import time
from typing import Any

from re_ass.llm_retry import is_retryable_llm_error
from re_ass.models import ArxivPaper, PreferenceConfig
from re_ass.paper_identity import derive_identity
from re_ass.paper_summariser.providers.base import Provider
from re_ass.prompt_logger import PromptLogger
from re_ass.settings import LlmConfig


LOGGER = logging.getLogger(__name__)
_RANKING_RETRY_WAIT_SECONDS = 2
# When no paper clears always_summarize_score, take this many from the top of the
# above-min_selection_score pool so the daily note is never silently blank.
_ABOVE_MIN_FILL_COUNT = 1


class RankingError(RuntimeError):
    """Raised when paper ranking cannot be completed."""


@dataclass(frozen=True, slots=True)
class RankedPaper:
    paper: ArxivPaper
    paper_key: str
    source_id: str
    score: float
    rationale: str
    science_match: bool | None = None
    method_match: bool | None = None
    score_filled: bool = False


@dataclass(frozen=True, slots=True)
class RankingSelection:
    selected_papers: list[ArxivPaper]
    candidate_count: int
    ranked: list[RankedPaper]
    selected: list[RankedPaper]
    weekly_interest: list[RankedPaper]


def _candidate_records(candidates: list[ArxivPaper]) -> list[tuple[ArxivPaper, str, str]]:
    records: list[tuple[ArxivPaper, str, str]] = []
    for paper in candidates:
        identity = derive_identity(paper)
        records.append((paper, identity.paper_key, identity.source_id))
    return records


def _format_priority_list(priorities: tuple[str, ...]) -> str:
    return "\n".join(f"{index + 1}. {priority}" for index, priority in enumerate(priorities))


def _requires_dual_match(preferences: PreferenceConfig) -> bool:
    return bool(preferences.science_priorities and preferences.method_priorities)


def _additional_priorities(preferences: PreferenceConfig) -> tuple[str, ...]:
    remaining = list(preferences.priorities)
    for priority in (*preferences.science_priorities, *preferences.method_priorities):
        try:
            remaining.pop(remaining.index(priority))
        except ValueError:
            continue
    return tuple(remaining)


def _priority_prompt_text(preferences: PreferenceConfig) -> str:
    if not _requires_dual_match(preferences):
        return (
            "<ordered_priorities>\n"
            f"{_format_priority_list(preferences.priorities)}\n"
            "</ordered_priorities>"
        )

    blocks = [
        "<science_priorities>\n"
        f"{_format_priority_list(preferences.science_priorities)}\n"
        "</science_priorities>",
        "<method_priorities>\n"
        f"{_format_priority_list(preferences.method_priorities)}\n"
        "</method_priorities>",
    ]
    additional = _additional_priorities(preferences)
    if additional:
        blocks.append(
            "<additional_priorities>\n"
            f"{_format_priority_list(additional)}\n"
            "</additional_priorities>"
        )
    return "\n\n".join(blocks)


def _ranking_system_prompt() -> str:
    return (
        "You rank arXiv papers for a user against an ordered list of research priorities.\n"
        "Earlier numbered priorities matter more than later ones.\n"
        "Return JSON only. Do not include markdown fences, prose, or commentary.\n"
        "Use only the provided candidate IDs.\n"
        "Rank every candidate exactly once."
    )


def _ranking_response_shape(*, dual_match: bool) -> str:
    if dual_match:
        return (
            '{"ranked_papers":[{"candidate_id":"arxiv:0000.00000","score":97,'
            '"science_match":true,"method_match":true,"rationale":"short reason"}]}'
        )
    return '{"ranked_papers":[{"candidate_id":"arxiv:0000.00000","score":97,"rationale":"short reason"}]}'


def _ranking_user_prompt(preferences: PreferenceConfig, candidates: list[ArxivPaper]) -> str:
    payload = [
        {
            "candidate_id": paper_key,
            "title": paper.title,
            "abstract": paper.summary,
            "primary_category": paper.primary_category,
        }
        for paper, paper_key, _source_id in _candidate_records(candidates)
    ]
    dual_match = _requires_dual_match(preferences)
    response_shape = _ranking_response_shape(dual_match=dual_match)
    if dual_match:
        rules = (
            "- Rank every provided candidate exactly once.\n"
            "- Sort ranked_papers from highest score to lowest score.\n"
            "- Use only the provided candidate IDs.\n"
            "- score must be a number from 0 to 100.\n"
            "- science_match and method_match must be boolean values.\n"
            "- rationale must be one sentence and under 30 words.\n"
            "- science_match is true only if the paper clearly matches at least one science priority.\n"
            "- method_match is true only if the paper clearly matches at least one method priority.\n"
            "- More matches are better, but one direct science match plus one direct method match can still be a strong fit.\n"
            "- Earlier numbers within each priority matter more than later ones.\n"
            "- Score fit to the user's priorities, not general paper quality.\n"
        )
        scoring_guide = (
            "- 90-100: strong dual fit — clear science match AND clear method match to high-ranked priorities\n"
            "- 70-89: dual fit — both science_match and method_match are true; at least one a strong hit\n"
            "- 40-69: one-sided — only science_match OR only method_match is true; score must not exceed 69\n"
            "- 0-39: weak fit — no clear match in either category\n"
        )
    else:
        rules = (
            "- Rank every provided candidate exactly once.\n"
            "- Sort ranked_papers from highest score to lowest score.\n"
            "- Use only the provided candidate IDs.\n"
            "- score must be a number from 0 to 100.\n"
            "- rationale must be one sentence and under 30 words.\n"
            "- Earlier priority numbers matter more than later ones.\n"
            "- A strong direct match to any single priority can deserve a high score.\n"
            "- Matching multiple priorities is a bonus, not a requirement.\n"
            "- Score fit to the user's priorities, not general paper quality.\n"
        )
        scoring_guide = (
            "- 90-100: exceptionally strong direct fit to at least one priority, especially a higher-ranked one\n"
            "- 80-89: clear keep; strong direct fit to one priority even without multiple hits\n"
            "- 60-79: partial, indirect, or weaker fit\n"
            "- 0-59: weak fit\n"
        )
    return (
        "<task>\n"
        "Score every candidate from 0 to 100 for relevance to the user's ordered priorities.\n"
        "Return exactly this JSON object shape:\n"
        f"{response_shape}\n"
        "Rules:\n"
        f"{rules}"
        "Scoring guide:\n"
        f"{scoring_guide}"
        "</task>\n\n"
        f"{_priority_prompt_text(preferences)}\n\n"
        "<candidates_json>\n"
        f"{json.dumps(payload, separators=(',', ':'))}\n"
        "</candidates_json>"
    )


def _ranking_repair_system_prompt() -> str:
    return (
        "You repair invalid JSON emitted by an arXiv paper ranker.\n"
        "Return JSON only. Do not include markdown fences, prose, or commentary.\n"
        "Use only the provided candidate IDs.\n"
        "Preserve the previous ranking intent where possible.\n"
        "Rank every candidate exactly once."
    )


def _ranking_repair_user_prompt(
    preferences: PreferenceConfig,
    candidates: list[ArxivPaper],
    *,
    invalid_response: str,
    validation_error: str,
) -> str:
    allowed_candidates = [
        {
            "candidate_id": paper_key,
            "title": paper.title,
        }
        for paper, paper_key, _source_id in _candidate_records(candidates)
    ]
    dual_match = _requires_dual_match(preferences)
    response_shape = _ranking_response_shape(dual_match=dual_match)
    extra_rule = (
        "- science_match and method_match must be present for every candidate.\n"
        if dual_match
        else ""
    )
    fit_rule = (
        "- Papers need both a science match and a method match to remain strong fits.\n"
        if dual_match
        else "- A strong direct match to one priority may still deserve a high score.\n"
    )
    return (
        "<task>\n"
        "Repair the invalid ranked-paper JSON.\n"
        "Return exactly this JSON object shape:\n"
        f"{response_shape}\n"
        "Rules:\n"
        "- Rank every provided candidate exactly once.\n"
        "- Sort ranked_papers from highest score to lowest score.\n"
        "- Use only the provided candidate IDs.\n"
        "- Preserve the previous ranking intent where possible.\n"
        f"{fit_rule}"
        f"{extra_rule}"
        "</task>\n\n"
        "<validation_error>\n"
        f"{validation_error}\n"
        "</validation_error>\n\n"
        "<previous_response>\n"
        f"{invalid_response.strip()}\n"
        "</previous_response>\n\n"
        f"{_priority_prompt_text(preferences)}\n\n"
        "<allowed_candidates_json>\n"
        f"{json.dumps(allowed_candidates, separators=(',', ':'))}\n"
        "</allowed_candidates_json>"
    )


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _load_ranking_payload(response_text: str) -> dict[str, Any]:
    cleaned = _strip_code_fences(response_text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise RankingError("Ranking provider did not return a JSON object.")

    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise RankingError(f"Ranking provider returned invalid JSON: {error}") from error

    if not isinstance(payload, dict):
        raise RankingError("Ranking provider returned a non-object JSON payload.")
    return payload


def _ranked_entries_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    ranked = payload.get("ranked_papers")
    if not isinstance(ranked, list):
        raise RankingError("Ranking payload must contain a 'ranked_papers' list.")
    return ranked


def _candidate_lookup(candidates: list[ArxivPaper]) -> dict[str, tuple[ArxivPaper, str]]:
    return {
        paper_key: (paper, source_id)
        for paper, paper_key, source_id in _candidate_records(candidates)
    }


def _parse_ranked_payload(
    response_text: str,
    candidates: list[ArxivPaper],
    *,
    require_dual_match: bool,
) -> list[RankedPaper]:
    payload = _load_ranking_payload(response_text)
    raw_ranked = _ranked_entries_from_payload(payload)
    by_key = _candidate_lookup(candidates)

    ranked: list[tuple[int, RankedPaper]] = []
    seen_ids: set[str] = set()
    for position, entry in enumerate(raw_ranked):
        if not isinstance(entry, dict):
            raise RankingError("Each ranked paper entry must be a JSON object.")

        candidate_id = entry.get("candidate_id")
        if not isinstance(candidate_id, str):
            raise RankingError("Each ranked paper entry must include a string candidate_id.")
        if candidate_id in seen_ids:
            raise RankingError(f"Ranking payload repeated candidate_id '{candidate_id}'.")
        if candidate_id not in by_key:
            raise RankingError(f"Ranking payload returned unknown candidate_id '{candidate_id}'.")

        score = entry.get("score")
        if not isinstance(score, (int, float)):
            raise RankingError(f"Ranking payload for '{candidate_id}' is missing a numeric score.")
        numeric_score = float(score)
        if numeric_score < 0.0 or numeric_score > 100.0:
            raise RankingError(f"Ranking payload for '{candidate_id}' has score {numeric_score}, outside 0-100.")

        rationale = entry.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise RankingError(f"Ranking payload for '{candidate_id}' is missing a rationale.")

        science_match: bool | None = None
        method_match: bool | None = None
        if require_dual_match:
            science_match = entry.get("science_match")
            method_match = entry.get("method_match")
            if not isinstance(science_match, bool):
                raise RankingError(f"Ranking payload for '{candidate_id}' is missing boolean science_match.")
            if not isinstance(method_match, bool):
                raise RankingError(f"Ranking payload for '{candidate_id}' is missing boolean method_match.")

        paper, source_id = by_key[candidate_id]
        ranked.append(
            (
                position,
                RankedPaper(
                    paper=paper,
                    paper_key=candidate_id,
                    source_id=source_id,
                    score=numeric_score,
                    rationale=rationale.strip(),
                    science_match=science_match,
                    method_match=method_match,
                ),
            )
        )
        seen_ids.add(candidate_id)

    missing_keys = set(by_key) - seen_ids
    if missing_keys:
        LOGGER.warning(
            "LLM returned %s of %s ranked papers; %s missing candidates filled with score 0.",
            len(seen_ids),
            len(by_key),
            len(missing_keys),
        )
        fill_position = len(raw_ranked)
        for missing_key in sorted(missing_keys):
            paper, source_id = by_key[missing_key]
            ranked.append(
                (
                    fill_position,
                    RankedPaper(
                        paper=paper,
                        paper_key=missing_key,
                        source_id=source_id,
                        score=0.0,
                        rationale="not ranked",
                        science_match=False if require_dual_match else None,
                        method_match=False if require_dual_match else None,
                        score_filled=True,
                    ),
                )
            )
            fill_position += 1

    ranked.sort(key=lambda item: (-item[1].score, item[0], item[1].paper.title.casefold()))
    return [item for _position, item in ranked]


_BATCH_OVERFLOW_FRACTION = 0.2


def _apply_dual_match_cap(ranked: list[RankedPaper]) -> list[RankedPaper]:
    """Clamp one-sided papers to ≤69, enforcing the scoring guide regardless of model compliance."""
    return [
        dataclasses.replace(r, score=69.0)
        if (r.science_match is False or r.method_match is False) and r.score > 69.0
        else r
        for r in ranked
    ]


def _split_into_batches(candidates: list[ArxivPaper], batch_size: int) -> list[list[ArxivPaper]]:
    """Split candidates into the fewest evenly-sized batches where each fits within batch_size + 20%."""
    n = len(candidates)
    effective_max = batch_size + max(1, round(batch_size * _BATCH_OVERFLOW_FRACTION))
    num_batches = max(1, (n + effective_max - 1) // effective_max)
    base, extra = divmod(n, num_batches)
    batches: list[list[ArxivPaper]] = []
    start = 0
    for i in range(num_batches):
        size = base + (1 if i < extra else 0)
        batches.append(candidates[start : start + size])
        start += size
    return batches


class PaperRanker:
    """Rank all candidate papers, always keep the strongest, and overflow the rest to weekly interest."""

    def __init__(
        self,
        *,
        provider: Provider,
        config: LlmConfig,
        always_summarize_score: float,
        min_selection_score: float,
        batch_size: int = 0,
        prompt_logger: PromptLogger | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.always_summarize_score = always_summarize_score
        self.min_selection_score = min_selection_score
        self.batch_size = max(0, batch_size)
        self.prompt_logger = prompt_logger

    def rank_papers(
        self,
        preferences: PreferenceConfig,
        candidates: list[ArxivPaper],
    ) -> RankingSelection:
        if not candidates:
            return RankingSelection(
                selected_papers=[],
                candidate_count=0,
                ranked=[],
                selected=[],
                weekly_interest=[],
            )

        dual_match_required = _requires_dual_match(preferences)
        batches = _split_into_batches(candidates, self.batch_size) if self.batch_size > 0 else [candidates]
        LOGGER.info(
            "Ranking %s candidate(s) in %s batch(es).",
            len(candidates),
            len(batches),
        )
        all_ranked: list[RankedPaper] = []
        # Track papers whose scores were capped so weekly_interest can still surface them.
        capped_keys: set[str] = set()
        for batch_index, batch in enumerate(batches, 1):
            if len(batches) > 1:
                LOGGER.info("Ranking batch %s/%s (%s candidate(s)).", batch_index, len(batches), len(batch))
            batch_label = "ranking" if len(batches) == 1 else f"ranking-{batch_index:02d}"
            batch_ranked = self._rank_candidates_batch(preferences, batch, label=batch_label)
            if dual_match_required:
                for r in batch_ranked:
                    if (r.science_match is False or r.method_match is False) and r.score > 69.0:
                        capped_keys.add(r.paper_key)
                batch_ranked = _apply_dual_match_cap(batch_ranked)
            all_ranked.extend(batch_ranked)

        if len(batches) > 1:
            finalist_threshold = max(0.0, self.min_selection_score - 10.0)
            # Restrict re-rank to dual-match eligible papers; one-sided papers can never be
            # selected and including them in the finalist pool skews the re-rank ordering.
            if dual_match_required:
                finalist_papers = [
                    r.paper for r in all_ranked
                    if r.score >= finalist_threshold
                    and r.science_match is True and r.method_match is True
                ]
            else:
                finalist_papers = [r.paper for r in all_ranked if r.score >= finalist_threshold]
            if len(finalist_papers) > 1:
                LOGGER.info(
                    "Re-ranking %s finalist(s) from %s batches for calibrated global ordering.",
                    len(finalist_papers),
                    len(batches),
                )
                try:
                    re_ranked = self._rank_candidates_batch(preferences, finalist_papers, label="ranking-finalist")
                    if dual_match_required:
                        for r in re_ranked:
                            if (r.science_match is False or r.method_match is False) and r.score > 69.0:
                                capped_keys.add(r.paper_key)
                        re_ranked = _apply_dual_match_cap(re_ranked)
                    re_ranked_by_key = {r.paper_key: r for r in re_ranked}
                    all_ranked = [re_ranked_by_key.get(r.paper_key, r) for r in all_ranked]
                except RankingError as error:
                    LOGGER.warning("Finalist re-ranking failed; using per-batch scores: %s", error)

        ranked = sorted(all_ranked, key=lambda r: (-r.score, r.paper.title.casefold()))
        eligible = [
            item
            for item in ranked
            if item.score >= self.min_selection_score
            and (
                not dual_match_required
                or (item.science_match is True and item.method_match is True)
            )
        ]
        always_selected = [item for item in eligible if item.score >= self.always_summarize_score]
        fill_candidates = [item for item in eligible if item.score < self.always_summarize_score]
        remaining_slots = 0 if always_selected else _ABOVE_MIN_FILL_COUNT
        selected = always_selected + fill_candidates[:remaining_slots]
        selected_keys = {item.paper_key for item in selected}
        # Use score-only threshold for weekly interest so that partial-match papers
        # (science-only or method-only) are not silently excluded when dual_match is
        # required for selection. Capped papers are included regardless of their clamped
        # score, since the cap is a selection guard, not a signal that the paper is uninteresting.
        weekly_interest = [
            item for item in ranked
            if (item.score >= self.min_selection_score or item.paper_key in capped_keys)
            and item.paper_key not in selected_keys
        ]
        LOGGER.info(
            "Ranked %s candidate(s): selected=%s always_threshold=%s interest_threshold=%s above_min_fill=%s weekly_interest=%s dual_match_required=%s",
            len(candidates),
            len(selected),
            self.always_summarize_score,
            self.min_selection_score,
            _ABOVE_MIN_FILL_COUNT,
            len(weekly_interest),
            dual_match_required,
        )
        return RankingSelection(
            selected_papers=[item.paper for item in selected],
            candidate_count=len(candidates),
            ranked=ranked,
            selected=selected,
            weekly_interest=weekly_interest,
        )

    def _rank_candidates_batch(
        self,
        preferences: PreferenceConfig,
        candidates: list[ArxivPaper],
        label: str = "ranking",
    ) -> list[RankedPaper]:
        dual_match_required = _requires_dual_match(preferences)
        response = self._request_ranking_response(preferences, candidates, label=label)
        try:
            return _parse_ranked_payload(
                response,
                candidates,
                require_dual_match=dual_match_required,
            )
        except RankingError as error:
            LOGGER.warning("Ranking payload validation failed; retrying once: %s", error)
            repair_response = self._repair_ranking_payload(
                preferences,
                candidates,
                invalid_response=response,
                validation_error=str(error),
                label=f"{label}-repair",
            )
            try:
                return _parse_ranked_payload(
                    repair_response,
                    candidates,
                    require_dual_match=dual_match_required,
                )
            except RankingError as repair_error:
                raise RankingError(
                    f"Ranking payload remained invalid after repair attempt: {repair_error}"
                ) from repair_error

    def _request_ranking_response(
        self,
        preferences: PreferenceConfig,
        candidates: list[ArxivPaper],
        label: str = "ranking",
    ) -> str:
        system_prompt = _ranking_system_prompt()
        user_prompt = _ranking_user_prompt(preferences, candidates)
        if self.prompt_logger:
            self.prompt_logger.write(label, system_prompt, user_prompt)
        max_attempts = min(2, max(1, self.config.retry_attempts))
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            try:
                return self.provider.process_document(
                    content="",
                    is_pdf=False,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=self.config.max_output_tokens,
                    temperature=0.0,
                ).strip()
            except Exception as error:
                last_error = error
                if not is_retryable_llm_error(error) or attempt == max_attempts - 1:
                    break
                LOGGER.warning(
                    "Ranking provider call failed on attempt %s/%s; retrying in %ss: %s",
                    attempt + 1,
                    max_attempts,
                    _RANKING_RETRY_WAIT_SECONDS,
                    error,
                )
                time.sleep(_RANKING_RETRY_WAIT_SECONDS)

        raise RankingError(f"Ranking provider call failed: {last_error}") from last_error

    def _repair_ranking_payload(
        self,
        preferences: PreferenceConfig,
        candidates: list[ArxivPaper],
        *,
        invalid_response: str,
        validation_error: str,
        label: str = "ranking-repair",
    ) -> str:
        system_prompt = _ranking_repair_system_prompt()
        user_prompt = _ranking_repair_user_prompt(
            preferences,
            candidates,
            invalid_response=invalid_response,
            validation_error=validation_error,
        )
        if self.prompt_logger:
            self.prompt_logger.write(label, system_prompt, user_prompt)
        try:
            return self.provider.process_document(
                content="",
                is_pdf=False,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=self.config.max_output_tokens,
                temperature=0.0,
            ).strip()
        except Exception as error:
            raise RankingError(f"Ranking repair call failed: {error}") from error
