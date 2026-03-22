"""Hybrid retrieval, local reranking, and Claude final selection for re-ass."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import re
from typing import Any

from re_ass.models import ArxivPaper, PreferenceConfig
from re_ass.paper_identity import derive_identity
from re_ass.paper_summariser.providers.base import Provider
from re_ass.settings import LlmConfig


LOGGER = logging.getLogger(__name__)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "use",
    "using",
    "via",
    "with",
}
_GENERIC_PRIORITY_TOKENS = {
    "analysis",
    "benchmark",
    "calibration",
    "formation",
    "galaxies",
    "galaxy",
    "histories",
    "history",
    "method",
    "methods",
    "model",
    "models",
    "study",
    "studies",
}
_FIELD_WEIGHTS = {
    "title": 4.0,
    "summary": 2.2,
    "categories": 1.3,
    "authors": 0.6,
}
_FUSION_OFFSET = 60.0
_CONCEPT_ALIASES = {
    "little_red_dots": (
        "little red dot",
        "little red dots",
        "lrd",
        "lrds",
    ),
    "black_holes": (
        "black hole",
        "black holes",
        "supermassive black hole",
        "supermassive black holes",
        "smbh",
        "smbhs",
    ),
    "agn": (
        "agn",
        "active galactic nuclei",
        "active galactic nucleus",
        "quasar",
        "quasars",
    ),
    "semi_analytic_models": (
        "semi analytic galaxy formation model",
        "semi analytic galaxy formation models",
        "semi analytic model",
        "semi analytic models",
        "sam",
        "sams",
    ),
}
_CONCEPT_TRIGGERS = {
    "little_red_dots": ("little red dot", "little red dots", "lrd", "lrds"),
    "black_holes": ("black hole", "black holes", "smbh", "smbhs"),
    "agn": ("agn", "active galactic nuclei", "active galactic nucleus", "quasar", "quasars"),
    "semi_analytic_models": (
        "semi analytic",
        "semi analytic galaxy formation",
        "semi analytic galaxy formation model",
        "sam",
        "sams",
    ),
}
_TOKEN_EQUIVALENTS = {
    "agn": {"agn", "active", "galactic", "nuclei", "nucleus", "quasar"},
    "lrd": {"lrd", "little", "red", "dot"},
    "lrds": {"lrd", "little", "red", "dot"},
    "quasar": {"quasar", "agn", "active", "galactic", "nuclei"},
    "quasars": {"quasar", "agn", "active", "galactic", "nuclei"},
    "sam": {"sam", "semi", "analytic", "model"},
    "sams": {"sam", "semi", "analytic", "model"},
    "smbh": {"smbh", "supermassive", "black", "hole"},
    "smbhs": {"smbh", "supermassive", "black", "hole"},
}


class RankingError(RuntimeError):
    """Raised when paper ranking cannot be completed."""


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    query_id: str
    text: str
    priority_index: int
    weight: float
    kind: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievedPaper:
    paper: ArxivPaper
    paper_key: str
    source_id: str
    lexical_score: float
    semantic_score: float
    fused_score: float
    best_priority_index: int
    matched_priority_count: int
    matched_priorities: tuple[str, ...]
    retrieval_channels: tuple[str, ...]
    retrieval_notes: tuple[str, ...]

    @property
    def pre_rank_score(self) -> float:
        return self.fused_score


@dataclass(frozen=True, slots=True)
class RerankedPaper:
    paper: ArxivPaper
    paper_key: str
    source_id: str
    lexical_score: float
    semantic_score: float
    fused_score: float
    rerank_score: float
    rationale: str
    best_priority_index: int
    matched_priority_count: int
    matched_priorities: tuple[str, ...]
    retrieval_channels: tuple[str, ...]
    retrieval_notes: tuple[str, ...]

    @property
    def pre_rank_score(self) -> float:
        return self.fused_score


@dataclass(frozen=True, slots=True)
class SelectedPaper:
    paper: ArxivPaper
    paper_key: str
    source_id: str
    selection_score: float
    rationale: str
    rerank_score: float


@dataclass(frozen=True, slots=True)
class RankingSelection:
    selected_papers: list[ArxivPaper]
    candidate_count: int
    retrieval_pool: list[RetrievedPaper]
    reranked: list[RerankedPaper]
    selected: list[SelectedPaper]
    final_pool: list[RerankedPaper]
    used_passthrough: bool = False

    @property
    def shortlist(self) -> list[RetrievedPaper]:
        return self.retrieval_pool


@dataclass(frozen=True, slots=True)
class _PaperFeatures:
    paper: ArxivPaper
    paper_key: str
    source_id: str
    raw_title_tokens: frozenset[str]
    raw_summary_tokens: frozenset[str]
    raw_category_tokens: frozenset[str]
    raw_author_tokens: frozenset[str]
    raw_searchable_tokens: frozenset[str]
    title_tokens: frozenset[str]
    summary_tokens: frozenset[str]
    category_tokens: frozenset[str]
    author_tokens: frozenset[str]
    searchable_tokens: frozenset[str]
    normalized_title: str
    normalized_summary: str
    normalized_categories: str
    normalized_text: str


def _normalize_token(token: str) -> str:
    normalized = token.casefold().strip(".")
    if normalized.endswith("ies") and len(normalized) > 4:
        normalized = f"{normalized[:-3]}y"
    elif normalized.endswith("s") and len(normalized) > 3 and not normalized.endswith("ss"):
        normalized = normalized[:-1]
    return normalized


def _normalize_phrase(text: str) -> str:
    return " ".join(_normalize_token(token) for token in _TOKEN_PATTERN.findall(text) if _normalize_token(token))


def _tokenize(text: str, *, expand_equivalents: bool = True) -> set[str]:
    tokens: set[str] = set()
    for raw_token in _TOKEN_PATTERN.findall(text):
        normalized = _normalize_token(raw_token)
        if normalized:
            tokens.add(normalized)

        for separator in ("-", "/"):
            if separator in raw_token:
                for part in raw_token.split(separator):
                    normalized_part = _normalize_token(part)
                    if normalized_part:
                        tokens.add(normalized_part)

    if not expand_equivalents:
        return {token for token in tokens if token and token not in _STOPWORDS}

    expanded_tokens: set[str] = set()
    for token in tokens:
        expanded_tokens.add(token)
        expanded_tokens.update(_TOKEN_EQUIVALENTS.get(token, set()))

    return {token for token in expanded_tokens if token and token not in _STOPWORDS}


def _field_tokens(
    paper: ArxivPaper,
    *,
    expand_equivalents: bool,
) -> tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str]]:
    return (
        frozenset(_tokenize(paper.title, expand_equivalents=expand_equivalents)),
        frozenset(_tokenize(paper.summary, expand_equivalents=expand_equivalents)),
        frozenset(_tokenize(" ".join((paper.primary_category, *paper.categories)), expand_equivalents=expand_equivalents)),
        frozenset(_tokenize(" ".join(paper.authors), expand_equivalents=expand_equivalents)),
    )


def _build_features(papers: list[ArxivPaper]) -> list[_PaperFeatures]:
    features: list[_PaperFeatures] = []
    for paper in papers:
        identity = derive_identity(paper)
        raw_title_tokens, raw_summary_tokens, raw_category_tokens, raw_author_tokens = _field_tokens(
            paper,
            expand_equivalents=False,
        )
        title_tokens, summary_tokens, category_tokens, author_tokens = _field_tokens(
            paper,
            expand_equivalents=True,
        )
        features.append(
            _PaperFeatures(
                paper=paper,
                paper_key=identity.paper_key,
                source_id=identity.source_id,
                raw_title_tokens=raw_title_tokens,
                raw_summary_tokens=raw_summary_tokens,
                raw_category_tokens=raw_category_tokens,
                raw_author_tokens=raw_author_tokens,
                raw_searchable_tokens=frozenset(raw_title_tokens | raw_summary_tokens | raw_category_tokens | raw_author_tokens),
                title_tokens=title_tokens,
                summary_tokens=summary_tokens,
                category_tokens=category_tokens,
                author_tokens=author_tokens,
                searchable_tokens=frozenset(title_tokens | summary_tokens | category_tokens | author_tokens),
                normalized_title=_normalize_phrase(paper.title),
                normalized_summary=_normalize_phrase(paper.summary),
                normalized_categories=_normalize_phrase(" ".join((paper.primary_category, *paper.categories))),
                normalized_text=_normalize_phrase(paper.searchable_text),
            )
        )
    return features


def _build_queries(preferences: PreferenceConfig) -> tuple[RetrievalQuery, ...]:
    priority_queries = [
        RetrievalQuery(
            query_id=f"priority:{index}",
            text=priority.strip(),
            priority_index=index,
            weight=1.0 + max(0, len(preferences.priorities) - index - 1) * 0.35,
            kind="priority",
            aliases=_aliases_for_query(priority),
        )
        for index, priority in enumerate(preferences.priorities)
        if priority.strip()
    ]
    joined_priorities = " ".join(preferences.priorities).strip()
    if not joined_priorities:
        return tuple(priority_queries)
    return tuple(
        [
            *priority_queries,
            RetrievalQuery(
                query_id="full_preferences",
                text=joined_priorities,
                priority_index=len(priority_queries),
                weight=0.5,
                kind="full_preferences",
                aliases=tuple(sorted({alias for query in priority_queries for alias in query.aliases})),
            ),
        ]
    )


def _aliases_for_query(query_text: str) -> tuple[str, ...]:
    normalized_query = _normalize_phrase(query_text)
    query_tokens = _tokenize(query_text)
    aliases: set[str] = set()
    for concept, triggers in _CONCEPT_TRIGGERS.items():
        if any(trigger in normalized_query for trigger in triggers) or any(_normalize_token(trigger) in query_tokens for trigger in triggers):
            aliases.update(_CONCEPT_ALIASES[concept])
    aliases.discard(query_text.strip())
    return tuple(sorted(aliases))


def _anchor_tokens(texts: tuple[str, ...]) -> set[str]:
    tokens = set().union(*(_tokenize(text, expand_equivalents=True) for text in texts if text))
    anchors = {token for token in tokens if token not in _GENERIC_PRIORITY_TOKENS}
    return anchors or tokens


def _phrase_present(field_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_phrase(phrase)
    if not normalized_phrase:
        return False
    padded_field = f" {field_text} "
    return f" {normalized_phrase} " in padded_field or field_text == normalized_phrase


def _document_frequencies(features: list[_PaperFeatures]) -> dict[str, float]:
    document_count = max(1, len(features))
    counts: dict[str, int] = {}
    for feature in features:
        for token in feature.raw_searchable_tokens:
            counts[token] = counts.get(token, 0) + 1
    return {
        token: math.log((document_count + 1) / (count + 0.5)) + 1.0
        for token, count in counts.items()
    }


def _weighted_overlap(tokens: set[str], feature: _PaperFeatures, idf: dict[str, float]) -> float:
    field_scores = [
        (_FIELD_WEIGHTS["title"], tokens & feature.raw_title_tokens),
        (_FIELD_WEIGHTS["summary"], tokens & feature.raw_summary_tokens),
        (_FIELD_WEIGHTS["categories"], tokens & feature.raw_category_tokens),
        (_FIELD_WEIGHTS["authors"], tokens & feature.raw_author_tokens),
    ]
    score = 0.0
    for weight, overlap in field_scores:
        if overlap:
            score += weight * sum(idf.get(token, 1.0) for token in overlap)
    return score


def _lexical_query_score(query: RetrievalQuery, feature: _PaperFeatures, idf: dict[str, float]) -> float:
    query_tokens = _tokenize(query.text, expand_equivalents=False)
    anchor_tokens = {token for token in query_tokens if token not in _GENERIC_PRIORITY_TOKENS} or query_tokens
    phrase_bonus = 0.0
    if _phrase_present(feature.normalized_title, query.text):
        phrase_bonus += 6.0
    elif _phrase_present(feature.normalized_summary, query.text):
        phrase_bonus += 3.0

    anchor_overlap = anchor_tokens & feature.raw_searchable_tokens
    if not anchor_overlap and phrase_bonus == 0.0:
        return 0.0

    score = phrase_bonus + _weighted_overlap(anchor_tokens, feature, idf)
    generic_tokens = query_tokens - anchor_tokens
    if generic_tokens:
        score += 0.1 * _weighted_overlap(generic_tokens, feature, idf)
    return score * query.weight


def _semantic_query_score(query: RetrievalQuery, feature: _PaperFeatures) -> float:
    phrases = (query.text, *query.aliases)
    best_score = 0.0
    for phrase in phrases:
        anchor_tokens = _anchor_tokens((phrase,))
        if not anchor_tokens:
            continue
        overlap = anchor_tokens & feature.searchable_tokens
        if not overlap and not _phrase_present(feature.normalized_text, phrase):
            continue

        coverage = len(overlap) / max(1, len(anchor_tokens))
        title_overlap = len(anchor_tokens & feature.title_tokens)
        score = coverage * 2.5 + title_overlap * 0.8
        if _phrase_present(feature.normalized_title, phrase):
            score += 3.5
        elif _phrase_present(feature.normalized_summary, phrase):
            score += 2.0
        elif _phrase_present(feature.normalized_categories, phrase):
            score += 1.0
        best_score = max(best_score, score)
    return best_score * query.weight


def _raw_retrieval_matches(
    queries: tuple[RetrievalQuery, ...],
    features: list[_PaperFeatures],
    *,
    idf: dict[str, float] | None,
    scorer,
    channel_name: str,
) -> list[RetrievedPaper]:
    raw_scores: list[tuple[float, int, int, _PaperFeatures, tuple[str, ...], tuple[str, ...]]] = []
    for feature in features:
        matched: list[tuple[int, str, float]] = []
        notes: list[str] = []
        total_score = 0.0
        for query in queries:
            query_score = scorer(query, feature, idf) if idf is not None else scorer(query, feature)
            if query_score <= 0.0:
                continue
            total_score += query_score
            if query.kind == "priority":
                matched.append((query.priority_index, query.text, query_score))
                notes.append(f"{channel_name}:{query.text}")

        if total_score <= 0.0:
            continue

        best_priority_index = min((index for index, _priority, _score in matched), default=len(queries))
        matched_priorities = tuple(dict.fromkeys(priority for _index, priority, _score in sorted(matched)))
        raw_scores.append(
            (
                total_score,
                best_priority_index,
                -len(matched_priorities),
                feature,
                matched_priorities,
                tuple(dict.fromkeys(notes)),
            )
        )

    raw_scores.sort(
        key=lambda item: (
            -item[0],
            item[1],
            item[2],
            -item[3].paper.published.timestamp(),
            item[3].paper.title.casefold(),
        )
    )
    if not raw_scores:
        return []

    best_score = raw_scores[0][0]
    retrieved: list[RetrievedPaper] = []
    for score, best_priority_index, _negative_match_count, feature, matched_priorities, notes in raw_scores:
        normalized_score = score / best_score if best_score else 0.0
        lexical_score = normalized_score if channel_name == "lexical" else 0.0
        semantic_score = normalized_score if channel_name == "semantic" else 0.0
        retrieved.append(
            RetrievedPaper(
                paper=feature.paper,
                paper_key=feature.paper_key,
                source_id=feature.source_id,
                lexical_score=lexical_score,
                semantic_score=semantic_score,
                fused_score=normalized_score,
                best_priority_index=best_priority_index,
                matched_priority_count=len(matched_priorities),
                matched_priorities=matched_priorities,
                retrieval_channels=(channel_name,),
                retrieval_notes=notes,
            )
        )
    return retrieved


def _rerank_rationale(item: RetrievedPaper, matched_priorities: tuple[str, ...]) -> str:
    if not matched_priorities:
        return "Weak priority coverage; retained only as a fallback candidate."
    channels = " and ".join(item.retrieval_channels)
    priorities = ", ".join(matched_priorities[:2])
    if len(matched_priorities) > 2:
        priorities = f"{priorities}, ..."
    return f"Matches {priorities} with support from {channels} retrieval."


def _direct_retrieval_pool(candidates: list[ArxivPaper], note: str) -> list[RetrievedPaper]:
    pool: list[RetrievedPaper] = []
    for paper in candidates:
        identity = derive_identity(paper)
        pool.append(
            RetrievedPaper(
                paper=paper,
                paper_key=identity.paper_key,
                source_id=identity.source_id,
                lexical_score=0.0,
                semantic_score=0.0,
                fused_score=0.0,
                best_priority_index=99,
                matched_priority_count=0,
                matched_priorities=(),
                retrieval_channels=("direct",),
                retrieval_notes=(note,),
            )
        )
    return pool


def _direct_reranked_pool(retrieval_pool: list[RetrievedPaper], note: str) -> list[RerankedPaper]:
    return [
        RerankedPaper(
            paper=item.paper,
            paper_key=item.paper_key,
            source_id=item.source_id,
            lexical_score=item.lexical_score,
            semantic_score=item.semantic_score,
            fused_score=item.fused_score,
            rerank_score=0.0,
            rationale=note,
            best_priority_index=item.best_priority_index,
            matched_priority_count=item.matched_priority_count,
            matched_priorities=item.matched_priorities,
            retrieval_channels=item.retrieval_channels,
            retrieval_notes=item.retrieval_notes,
        )
        for item in retrieval_pool
    ]


def pre_rank_papers(papers: list[ArxivPaper], priorities: tuple[str, ...]) -> list[RetrievedPaper]:
    """Backward-compatible lexical-only pre-ranking helper."""
    preferences = PreferenceConfig(
        priorities=priorities,
        categories=(),
        raw_text="\n".join(f"{index + 1}. {priority}" for index, priority in enumerate(priorities)),
    )
    return LexicalRetriever().retrieve(preferences, papers, limit=len(papers))


class LexicalRetriever:
    """Weighted lexical retrieval with anchor-token requirements."""

    def retrieve(
        self,
        preferences: PreferenceConfig,
        candidates: list[ArxivPaper],
        *,
        limit: int,
    ) -> list[RetrievedPaper]:
        if not candidates or limit <= 0:
            return []
        features = _build_features(candidates)
        idf = _document_frequencies(features)
        queries = _build_queries(preferences)
        retrieved = _raw_retrieval_matches(
            queries,
            features,
            idf=idf,
            scorer=_lexical_query_score,
            channel_name="lexical",
        )
        return retrieved[:limit]


class SemanticRetriever:
    """Alias-aware semantic retrieval used to recover weak lexical matches."""

    def retrieve(
        self,
        preferences: PreferenceConfig,
        candidates: list[ArxivPaper],
        *,
        limit: int,
    ) -> list[RetrievedPaper]:
        if not candidates or limit <= 0:
            return []
        features = _build_features(candidates)
        queries = _build_queries(preferences)
        retrieved = _raw_retrieval_matches(
            queries,
            features,
            idf=None,
            scorer=_semantic_query_score,
            channel_name="semantic",
        )
        return retrieved[:limit]


class HybridRetriever:
    """Reciprocal-rank fusion over lexical and alias-aware semantic retrieval."""

    def __init__(
        self,
        *,
        lexical_retriever: LexicalRetriever | None = None,
        semantic_retriever: SemanticRetriever | None = None,
    ) -> None:
        self.lexical_retriever = lexical_retriever or LexicalRetriever()
        self.semantic_retriever = semantic_retriever or SemanticRetriever()

    def retrieve(
        self,
        preferences: PreferenceConfig,
        candidates: list[ArxivPaper],
        *,
        limit: int,
    ) -> list[RetrievedPaper]:
        if not candidates or limit <= 0:
            return []

        channel_limit = min(len(candidates), max(limit, min(limit * 2, 120)))
        lexical_hits = self.lexical_retriever.retrieve(preferences, candidates, limit=channel_limit)
        semantic_hits = self.semantic_retriever.retrieve(preferences, candidates, limit=channel_limit)

        fused: dict[str, dict[str, Any]] = {}
        for channel_hits, channel_name in ((lexical_hits, "lexical"), (semantic_hits, "semantic")):
            for rank, item in enumerate(channel_hits, start=1):
                state = fused.setdefault(
                    item.paper_key,
                    {
                        "paper": item.paper,
                        "paper_key": item.paper_key,
                        "source_id": item.source_id,
                        "lexical_score": 0.0,
                        "semantic_score": 0.0,
                        "raw_fused": 0.0,
                        "best_priority_index": item.best_priority_index,
                        "matched_priorities": set(item.matched_priorities),
                        "channels": set(item.retrieval_channels),
                        "notes": list(item.retrieval_notes),
                    },
                )
                state["raw_fused"] += 1.0 / (_FUSION_OFFSET + rank)
                state["best_priority_index"] = min(state["best_priority_index"], item.best_priority_index)
                state["matched_priorities"].update(item.matched_priorities)
                state["channels"].update(item.retrieval_channels)
                state["notes"].extend(item.retrieval_notes)
                if channel_name == "lexical":
                    state["lexical_score"] = max(state["lexical_score"], item.lexical_score)
                else:
                    state["semantic_score"] = max(state["semantic_score"], item.semantic_score)

        if not fused:
            return []

        max_fused = max(state["raw_fused"] for state in fused.values())
        retrieved = [
            RetrievedPaper(
                paper=state["paper"],
                paper_key=state["paper_key"],
                source_id=state["source_id"],
                lexical_score=float(state["lexical_score"]),
                semantic_score=float(state["semantic_score"]),
                fused_score=(state["raw_fused"] / max_fused) if max_fused else 0.0,
                best_priority_index=int(state["best_priority_index"]),
                matched_priority_count=len(state["matched_priorities"]),
                matched_priorities=tuple(sorted(state["matched_priorities"], key=preferences.priorities.index))
                if state["matched_priorities"]
                else (),
                retrieval_channels=tuple(sorted(state["channels"])),
                retrieval_notes=tuple(dict.fromkeys(state["notes"])),
            )
            for state in fused.values()
        ]
        retrieved.sort(
            key=lambda item: (
                -item.fused_score,
                item.best_priority_index,
                -item.semantic_score,
                -item.lexical_score,
                -item.paper.published.timestamp(),
                item.paper.title.casefold(),
            )
        )
        return retrieved[:limit]


class FeatureReranker:
    """Local reranker that promotes multi-signal matches before the final Claude decision."""

    def rerank(self, preferences: PreferenceConfig, retrieved: list[RetrievedPaper]) -> list[RerankedPaper]:
        if not retrieved:
            return []

        features_by_key = {feature.paper_key: feature for feature in _build_features([item.paper for item in retrieved])}
        idf = _document_frequencies(list(features_by_key.values()))
        priority_queries = tuple(query for query in _build_queries(preferences) if query.kind == "priority")
        reranked: list[RerankedPaper] = []

        for item in retrieved:
            feature = features_by_key[item.paper_key]
            matched: list[tuple[int, str, float]] = []
            total_priority_signal = 0.0
            for query in priority_queries:
                lexical_signal = _lexical_query_score(query, feature, idf)
                semantic_signal = _semantic_query_score(query, feature)
                priority_signal = max(lexical_signal / max(query.weight, 1.0), semantic_signal / max(query.weight, 1.0))
                if priority_signal <= 0.0:
                    continue
                matched.append((query.priority_index, query.text, priority_signal))
                total_priority_signal += priority_signal * query.weight

            matched_priorities = tuple(priority for _index, priority, _score in sorted(matched))
            best_priority_index = min((index for index, _priority, _score in matched), default=len(preferences.priorities))
            coverage_bonus = len(matched_priorities) * 8.0
            channel_bonus = 5.0 if len(item.retrieval_channels) > 1 else 0.0
            rerank_score = min(
                100.0,
                item.fused_score * 35.0
                + item.lexical_score * 10.0
                + item.semantic_score * 15.0
                + total_priority_signal * 12.0
                + coverage_bonus
                + channel_bonus,
            )
            reranked.append(
                RerankedPaper(
                    paper=item.paper,
                    paper_key=item.paper_key,
                    source_id=item.source_id,
                    lexical_score=item.lexical_score,
                    semantic_score=item.semantic_score,
                    fused_score=item.fused_score,
                    rerank_score=round(rerank_score, 6),
                    rationale=_rerank_rationale(item, matched_priorities),
                    best_priority_index=best_priority_index,
                    matched_priority_count=len(matched_priorities),
                    matched_priorities=matched_priorities,
                    retrieval_channels=item.retrieval_channels,
                    retrieval_notes=item.retrieval_notes,
                )
            )

        reranked.sort(
            key=lambda item: (
                -item.rerank_score,
                item.best_priority_index,
                -item.semantic_score,
                -item.lexical_score,
                item.paper.title.casefold(),
            )
        )
        return reranked


def _final_selection_system_prompt() -> str:
    return (
        "You select the best arXiv papers for a user based on the full preferences document.\n"
        "Return JSON only. Do not include markdown fences, prose, or commentary.\n"
        "Use only the provided candidate IDs.\n"
        "It is acceptable to return fewer papers when the pool is weak."
    )


def _final_selection_user_prompt(
    preferences: PreferenceConfig,
    candidates: list[RerankedPaper],
    *,
    max_papers: int,
) -> str:
    payload = [
        {
            "candidate_id": item.paper_key,
            "arxiv_id": item.source_id,
            "title": item.paper.title,
            "summary": item.paper.summary,
            "authors": list(item.paper.authors),
            "primary_category": item.paper.primary_category,
            "categories": list(item.paper.categories),
            "published": item.paper.published.isoformat(),
            "matched_priorities": list(item.matched_priorities),
            "retrieval_channels": list(item.retrieval_channels),
            "lexical_score": item.lexical_score,
            "semantic_score": item.semantic_score,
            "fused_score": item.fused_score,
            "rerank_score": item.rerank_score,
            "local_rationale": item.rationale,
        }
        for item in candidates
    ]
    return (
        "<task>\n"
        f"Select up to {max_papers} papers for summarisation.\n"
        "Return exactly this JSON object shape:\n"
        '{"selected_papers":[{"candidate_id":"arxiv:2603.12345","score":97,"rationale":"short reason"}]}\n'
        "Rules:\n"
        "- Use only the provided candidate IDs.\n"
        f"- Select at most {max_papers} papers.\n"
        f"- It is acceptable to return fewer than {max_papers} papers if the remaining candidates are weak fits.\n"
        "- score must be a number from 0 to 100.\n"
        "- rationale must be one sentence and under 30 words.\n"
        "- Prefer strong matches to the full preferences document, not superficial lexical overlap.\n"
        "</task>\n\n"
        "<preferences_markdown>\n"
        f"{preferences.raw_text.strip()}\n"
        "</preferences_markdown>\n\n"
        "<candidates_json>\n"
        f"{json.dumps(payload, indent=2)}\n"
        "</candidates_json>"
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


class PaperRanker:
    """Large-pool hybrid ranker with small-pool passthrough into the final Claude selector."""

    def __init__(
        self,
        *,
        provider: Provider,
        config: LlmConfig,
        retrieval_pool_size: int,
        final_pool_size: int | None = None,
        min_selection_score: float = 75.0,
        passthrough_candidate_count: int = 50,
        retriever: HybridRetriever | None = None,
        reranker: FeatureReranker | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.retrieval_pool_size = max(1, retrieval_pool_size)
        self.final_pool_size = max(1, final_pool_size or retrieval_pool_size)
        self.min_selection_score = min_selection_score
        self.passthrough_candidate_count = max(1, passthrough_candidate_count)
        self.retriever = retriever or HybridRetriever()
        self.reranker = reranker or FeatureReranker()

    def select_top_papers(
        self,
        preferences: PreferenceConfig,
        candidates: list[ArxivPaper],
        *,
        max_papers: int,
    ) -> RankingSelection:
        if not candidates:
            return RankingSelection(
                selected_papers=[],
                candidate_count=0,
                retrieval_pool=[],
                reranked=[],
                selected=[],
                final_pool=[],
            )

        if len(candidates) <= self.passthrough_candidate_count:
            retrieval_pool = _direct_retrieval_pool(candidates, "Small candidate pool sent directly to Claude.")
            reranked = _direct_reranked_pool(retrieval_pool, "Direct small-pool passthrough.")
            final_pool = reranked
            selected = self._select_final_papers(preferences, final_pool, max_papers=max_papers)
            return RankingSelection(
                selected_papers=[item.paper for item in selected],
                candidate_count=len(candidates),
                retrieval_pool=retrieval_pool,
                reranked=reranked,
                selected=selected,
                final_pool=final_pool,
                used_passthrough=True,
            )

        retrieval_limit = min(len(candidates), max(self.retrieval_pool_size, max_papers))
        retrieval_pool = self.retriever.retrieve(preferences, candidates, limit=retrieval_limit)
        if not retrieval_pool:
            retrieval_pool = _direct_retrieval_pool(
                candidates[:retrieval_limit],
                "No strong retrieval signal; fell back to a direct candidate slice.",
            )
            reranked = _direct_reranked_pool(retrieval_pool, "Retrieval fallback candidate.")
        else:
            reranked = self.reranker.rerank(preferences, retrieval_pool)

        final_pool = reranked[: min(len(reranked), max(self.final_pool_size, max_papers))]
        selected = self._select_final_papers(preferences, final_pool, max_papers=max_papers)
        LOGGER.info(
            "Ranked %s candidate(s): retrieval_pool=%s final_pool=%s selected=%s",
            len(candidates),
            len(retrieval_pool),
            len(final_pool),
            len(selected),
        )
        return RankingSelection(
            selected_papers=[item.paper for item in selected],
            candidate_count=len(candidates),
            retrieval_pool=retrieval_pool,
            reranked=reranked,
            selected=selected,
            final_pool=final_pool,
            used_passthrough=False,
        )

    def _select_final_papers(
        self,
        preferences: PreferenceConfig,
        final_pool: list[RerankedPaper],
        *,
        max_papers: int,
    ) -> list[SelectedPaper]:
        if not final_pool or max_papers <= 0:
            return []

        system_prompt = _final_selection_system_prompt()
        user_prompt = _final_selection_user_prompt(preferences, final_pool, max_papers=max_papers)
        try:
            response = self.provider.process_document(
                content="",
                is_pdf=False,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=min(self.config.max_output_tokens, 4096),
            ).strip()
        except Exception as error:
            raise RankingError(f"Ranking provider call failed: {error}") from error

        payload = _load_ranking_payload(response)
        raw_selected = payload.get("selected_papers")
        if raw_selected is None:
            raw_selected = payload.get("ranked_papers")
        if not isinstance(raw_selected, list):
            raise RankingError("Ranking payload must contain a 'selected_papers' list.")

        by_key = {item.paper_key: item for item in final_pool}
        seen_ids: set[str] = set()
        selected: list[SelectedPaper] = []
        for entry in raw_selected:
            if not isinstance(entry, dict):
                raise RankingError("Each selected paper entry must be a JSON object.")

            candidate_id = entry.get("candidate_id")
            if not isinstance(candidate_id, str):
                raise RankingError("Each selected paper entry must include a string candidate_id.")
            if candidate_id in seen_ids:
                raise RankingError(f"Ranking payload repeated candidate_id '{candidate_id}'.")
            if candidate_id not in by_key:
                raise RankingError(f"Ranking payload returned unknown candidate_id '{candidate_id}'.")

            score = entry.get("score")
            if not isinstance(score, (int, float)):
                raise RankingError(f"Ranking payload for '{candidate_id}' is missing a numeric score.")

            rationale = entry.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                raise RankingError(f"Ranking payload for '{candidate_id}' is missing a rationale.")

            seen_ids.add(candidate_id)
            if float(score) < self.min_selection_score:
                continue

            item = by_key[candidate_id]
            selected.append(
                SelectedPaper(
                    paper=item.paper,
                    paper_key=item.paper_key,
                    source_id=item.source_id,
                    selection_score=float(score),
                    rationale=rationale.strip(),
                    rerank_score=item.rerank_score,
                )
            )
            if len(selected) >= max_papers:
                break
        return selected
