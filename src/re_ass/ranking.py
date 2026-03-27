"""LLM-first paper ranking for re-ass."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from re_ass.models import ArxivPaper, PreferenceConfig
from re_ass.paper_identity import derive_identity
from re_ass.paper_summariser.providers.base import Provider
from re_ass.settings import LlmConfig


LOGGER = logging.getLogger(__name__)


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


def _ordered_priorities_text(preferences: PreferenceConfig) -> str:
    return "\n".join(f"{index + 1}. {priority}" for index, priority in enumerate(preferences.priorities))


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
            f"{_ordered_priorities_text(preferences)}\n"
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
    response_shape = (
        '{"ranked_papers":[{"candidate_id":"arxiv:2603.12345","score":97,"science_match":true,"method_match":true,"rationale":"short reason"}]}'
        if dual_match
        else '{"ranked_papers":[{"candidate_id":"arxiv:2603.12345","score":97,"rationale":"short reason"}]}'
    )
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
            "- A paper should only receive a strong score if both science_match and method_match are true.\n"
            "- Papers with only science_match or only method_match are partial fits and should score materially lower.\n"
            "- More matches are better, but one direct science match plus one direct method match can still be a strong fit.\n"
            "- Earlier numbers within each section matter more than later ones.\n"
            "- Score fit to the user's priorities, not general paper quality.\n"
        )
        scoring_guide = (
            "- 90-100: exceptionally strong direct fit with at least one science match and one method match\n"
            "- 80-89: clear keep with one good science match and one good method match\n"
            "- 60-79: partial fit, usually science-only, method-only, or a weak connection between them\n"
            "- 0-59: weak fit\n"
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
    response_shape = (
        '{"ranked_papers":[{"candidate_id":"arxiv:2603.12345","score":97,"science_match":true,"method_match":true,"rationale":"short reason"}]}'
        if dual_match
        else '{"ranked_papers":[{"candidate_id":"arxiv:2603.12345","score":97,"rationale":"short reason"}]}'
    )
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
    if len(raw_ranked) != len(by_key):
        raise RankingError(
            f"Ranking payload must return exactly {len(by_key)} ranked paper(s), got {len(raw_ranked)}."
        )

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

    ranked.sort(key=lambda item: (-item[1].score, item[0], item[1].paper.title.casefold()))
    return [item for _position, item in ranked]


class PaperRanker:
    """Rank all candidate papers, always keep the strongest, and overflow the rest to weekly interest."""

    def __init__(
        self,
        *,
        provider: Provider,
        config: LlmConfig,
        max_papers: int,
        always_summarize_score: float,
        min_selection_score: float,
    ) -> None:
        self.provider = provider
        self.config = config
        self.max_papers = max(0, max_papers)
        self.always_summarize_score = always_summarize_score
        self.min_selection_score = min_selection_score

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
        system_prompt = _ranking_system_prompt()
        user_prompt = _ranking_user_prompt(preferences, candidates)
        try:
            response = self.provider.process_document(
                content="",
                is_pdf=False,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=self.config.max_output_tokens,
            ).strip()
        except Exception as error:
            raise RankingError(f"Ranking provider call failed: {error}") from error

        try:
            ranked = _parse_ranked_payload(
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
            )
            try:
                ranked = _parse_ranked_payload(
                    repair_response,
                    candidates,
                    require_dual_match=dual_match_required,
                )
            except RankingError as repair_error:
                raise RankingError(
                    f"Ranking payload remained invalid after repair attempt: {repair_error}"
                ) from repair_error

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
        remaining_slots = max(0, self.max_papers - len(always_selected))
        selected = always_selected + fill_candidates[:remaining_slots]
        weekly_interest = fill_candidates[remaining_slots:]
        LOGGER.info(
            "Ranked %s candidate(s): selected=%s always_threshold=%s interest_threshold=%s target_cap=%s weekly_interest=%s dual_match_required=%s",
            len(candidates),
            len(selected),
            self.always_summarize_score,
            self.min_selection_score,
            self.max_papers,
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

    def _repair_ranking_payload(
        self,
        preferences: PreferenceConfig,
        candidates: list[ArxivPaper],
        *,
        invalid_response: str,
        validation_error: str,
    ) -> str:
        system_prompt = _ranking_repair_system_prompt()
        user_prompt = _ranking_repair_user_prompt(
            preferences,
            candidates,
            invalid_response=invalid_response,
            validation_error=validation_error,
        )
        try:
            return self.provider.process_document(
                content="",
                is_pdf=False,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=self.config.max_output_tokens,
            ).strip()
        except Exception as error:
            raise RankingError(f"Ranking repair call failed: {error}") from error
