import json

import pytest

from re_ass.paper_identity import derive_identity
from re_ass.models import PreferenceConfig
from re_ass.ranking import (
    FeatureReranker,
    HybridRetriever,
    LexicalRetriever,
    PaperRanker,
    RankingError,
    RerankedPaper,
    RetrievedPaper,
)
from tests.fixtures.ranking_cases import (
    astroph_preferences,
    hybrid_recall_papers,
    lexical_false_positive_papers,
    rerank_pool_papers,
)
from tests.support import make_app_config, make_paper


class RecordingProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = []

    def process_document(self, content, is_pdf, system_prompt, user_prompt, max_tokens=12288):
        self.calls.append(
            {
                "content": content,
                "is_pdf": is_pdf,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
            }
        )
        return self.response


class StaticRetriever:
    def __init__(self, retrieved: list[RetrievedPaper]) -> None:
        self.retrieved = retrieved

    def retrieve(self, _preferences, _candidates, *, limit: int) -> list[RetrievedPaper]:
        return self.retrieved[:limit]


class StaticReranker:
    def __init__(self, reranked: list[RerankedPaper]) -> None:
        self.reranked = reranked

    def rerank(self, _preferences, _retrieved) -> list[RerankedPaper]:
        return list(self.reranked)


class FailIfCalledRetriever:
    def retrieve(self, _preferences, _candidates, *, limit: int) -> list[RetrievedPaper]:
        raise AssertionError(f"retriever should not run for small pools (limit={limit})")


class FailIfCalledReranker:
    def rerank(self, _preferences, _retrieved) -> list[RerankedPaper]:
        raise AssertionError("reranker should not run for small pools")


def _retrieved(
    paper,
    *,
    lexical_score: float,
    semantic_score: float,
    fused_score: float,
    matched_priorities: tuple[str, ...],
    retrieval_channels: tuple[str, ...],
) -> RetrievedPaper:
    identity = derive_identity(paper)
    return RetrievedPaper(
        paper=paper,
        paper_key=identity.paper_key,
        source_id=identity.source_id,
        lexical_score=lexical_score,
        semantic_score=semantic_score,
        fused_score=fused_score,
        best_priority_index=0 if matched_priorities else 99,
        matched_priority_count=len(matched_priorities),
        matched_priorities=matched_priorities,
        retrieval_channels=retrieval_channels,
        retrieval_notes=("test fixture",),
    )


def _reranked(
    retrieved: RetrievedPaper,
    *,
    rerank_score: float,
    rationale: str,
) -> RerankedPaper:
    return RerankedPaper(
        paper=retrieved.paper,
        paper_key=retrieved.paper_key,
        source_id=retrieved.source_id,
        lexical_score=retrieved.lexical_score,
        semantic_score=retrieved.semantic_score,
        fused_score=retrieved.fused_score,
        rerank_score=rerank_score,
        rationale=rationale,
        best_priority_index=retrieved.best_priority_index,
        matched_priority_count=retrieved.matched_priority_count,
        matched_priorities=retrieved.matched_priorities,
        retrieval_channels=retrieved.retrieval_channels,
        retrieval_notes=retrieved.retrieval_notes,
    )


def _single_priority_preferences(priority: str) -> PreferenceConfig:
    return PreferenceConfig(
        priorities=(priority,),
        categories=("astro-ph.GA",),
        raw_text=f"# Priorities\n1. {priority}\n",
    )


def test_lexical_retriever_requires_anchor_terms_for_generic_model_overlap() -> None:
    preferences = _single_priority_preferences("Semi-analytic galaxy formation models")
    papers = lexical_false_positive_papers()

    retrieved = LexicalRetriever().retrieve(preferences, papers, limit=2)

    assert [item.paper.title for item in retrieved] == ["Semi-analytic black-hole growth in galaxy assembly"]


def test_hybrid_retriever_recovers_alias_based_match_missing_from_lexical_stage() -> None:
    preferences = _single_priority_preferences("Semi-analytic galaxy formation models")
    papers = hybrid_recall_papers()

    lexical_titles = [
        item.paper.title
        for item in LexicalRetriever().retrieve(preferences, papers, limit=2)
    ]
    hybrid_titles = [item.paper.title for item in HybridRetriever().retrieve(preferences, papers, limit=2)]

    assert "Galaxy assembly in SAMs with quasar feedback" not in lexical_titles
    assert hybrid_titles[0] == "Galaxy assembly in SAMs with quasar feedback"


def test_feature_reranker_can_reorder_the_fused_retrieval_pool() -> None:
    preferences = astroph_preferences()
    generic_paper, strong_match = rerank_pool_papers()
    generic_retrieved = _retrieved(
        generic_paper,
        lexical_score=0.88,
        semantic_score=0.18,
        fused_score=0.91,
        matched_priorities=("Semi-analytic galaxy formation models",),
        retrieval_channels=("lexical",),
    )
    strong_retrieved = _retrieved(
        strong_match,
        lexical_score=0.62,
        semantic_score=0.81,
        fused_score=0.84,
        matched_priorities=("Black holes and AGN", "Semi-analytic galaxy formation models"),
        retrieval_channels=("lexical", "semantic"),
    )

    reranked = FeatureReranker().rerank(preferences, [generic_retrieved, strong_retrieved])

    assert [item.paper.title for item in reranked] == [
        "AGN feedback in semi-analytic SAM galaxy assembly",
        "Galaxy formation benchmark models",
    ]


def test_paper_ranker_uses_full_preferences_document_in_final_selection_prompt(tmp_path) -> None:
    paper = make_paper(arxiv_id="2603.40011", title="Semantic Agents")
    provider = RecordingProvider(
        json.dumps(
            {
                "selected_papers": [
                    {"candidate_id": "arxiv:2603.40011", "score": 96, "rationale": "Best matches the document."}
                ]
            }
        )
    )
    config = make_app_config(tmp_path).llm
    retrieved = _retrieved(
        paper,
        lexical_score=0.7,
        semantic_score=0.9,
        fused_score=0.95,
        matched_priorities=("Agents",),
        retrieval_channels=("lexical", "semantic"),
    )
    reranked = _reranked(retrieved, rerank_score=0.96, rationale="Best local match.")
    ranker = PaperRanker(
        provider=provider,
        config=config,
        retrieval_pool_size=12,
        final_pool_size=6,
        min_selection_score=80.0,
        retriever=StaticRetriever([retrieved]),
        reranker=StaticReranker([reranked]),
    )
    preferences = type(
        "Preferences",
        (),
        {
            "priorities": ("Agents",),
            "categories": ("cs.AI",),
            "raw_text": "# Priorities\n1. Agents\n2. Tool use\n",
        },
    )()

    selection = ranker.select_top_papers(preferences, [paper], max_papers=1)

    assert [item.title for item in selection.selected_papers] == ["Semantic Agents"]
    assert "# Priorities" in provider.calls[0]["user_prompt"]
    assert "Tool use" in provider.calls[0]["user_prompt"]


def test_paper_ranker_respects_final_selector_output_and_threshold(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40021", title="Strong Lexical Match", summary="Agents agents agents"),
        make_paper(arxiv_id="2603.40022", title="Semantic Best Match", summary="General reasoning"),
        make_paper(arxiv_id="2603.40023", title="Third Choice", summary="Useful tooling"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "selected_papers": [
                    {"candidate_id": "arxiv:2603.40022", "score": 99, "rationale": "Best overall fit."},
                    {"candidate_id": "arxiv:2603.40023", "score": 81, "rationale": "Second-best fit."},
                    {"candidate_id": "arxiv:2603.40021", "score": 61, "rationale": "Mostly lexical overlap."},
                ]
            }
        )
    )
    retrieved = [
        _retrieved(
            papers[0],
            lexical_score=0.95,
            semantic_score=0.25,
            fused_score=0.92,
            matched_priorities=("Agents",),
            retrieval_channels=("lexical",),
        ),
        _retrieved(
            papers[1],
            lexical_score=0.55,
            semantic_score=0.92,
            fused_score=0.90,
            matched_priorities=("Agents",),
            retrieval_channels=("semantic",),
        ),
        _retrieved(
            papers[2],
            lexical_score=0.65,
            semantic_score=0.78,
            fused_score=0.80,
            matched_priorities=("Tool use",),
            retrieval_channels=("lexical", "semantic"),
        ),
    ]
    reranked = [
        _reranked(retrieved[1], rerank_score=0.98, rationale="Best overall fit."),
        _reranked(retrieved[2], rerank_score=0.87, rationale="Second-best fit."),
        _reranked(retrieved[0], rerank_score=0.59, rationale="Mostly lexical overlap."),
    ]
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        retrieval_pool_size=12,
        final_pool_size=6,
        min_selection_score=80.0,
        retriever=StaticRetriever(retrieved),
        reranker=StaticReranker(reranked),
    )
    preferences = type(
        "Preferences",
        (),
        {
            "priorities": ("Agents", "Tool use"),
            "categories": ("cs.AI",),
            "raw_text": "# Priorities\n1. Agents with strong evaluation\n2. Tool use\n",
        },
    )()

    selection = ranker.select_top_papers(preferences, papers, max_papers=2)

    assert [paper.title for paper in selection.selected_papers] == ["Semantic Best Match", "Third Choice"]
    assert len(selection.selected) == 2


def test_paper_ranker_sends_small_candidate_pools_wholesale_without_retrieval_truncation(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40041", title="Little Red Dots in JWST"),
        make_paper(arxiv_id="2603.40042", title="AGN feedback in SAMs"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "selected_papers": [
                    {"candidate_id": "arxiv:2603.40042", "score": 97, "rationale": "Best overall fit."},
                    {"candidate_id": "arxiv:2603.40041", "score": 91, "rationale": "Strong secondary fit."},
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        retrieval_pool_size=1,
        final_pool_size=1,
        min_selection_score=80.0,
        passthrough_candidate_count=50,
        retriever=FailIfCalledRetriever(),
        reranker=FailIfCalledReranker(),
    )
    preferences = astroph_preferences()

    selection = ranker.select_top_papers(preferences, papers, max_papers=2)

    assert [paper.title for paper in selection.selected_papers] == ["AGN feedback in SAMs", "Little Red Dots in JWST"]
    assert "Little Red Dots in JWST" in provider.calls[0]["user_prompt"]
    assert "AGN feedback in SAMs" in provider.calls[0]["user_prompt"]


def test_paper_ranker_uses_relaxed_score_floor_for_direct_small_pools(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40051", title="AGN metallicity calibrations"),
        make_paper(arxiv_id="2603.40052", title="PAH variations in AGN hosts"),
        make_paper(arxiv_id="2603.40053", title="Black hole spin-up in proto-stellar clusters"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "selected_papers": [
                    {"candidate_id": "arxiv:2603.40051", "score": 72, "rationale": "Strong AGN relevance."},
                    {"candidate_id": "arxiv:2603.40052", "score": 55, "rationale": "Useful secondary AGN fit."},
                    {"candidate_id": "arxiv:2603.40053", "score": 48, "rationale": "Marginal black-hole fit."},
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        retrieval_pool_size=12,
        final_pool_size=6,
        min_selection_score=80.0,
        passthrough_candidate_count=50,
        retriever=FailIfCalledRetriever(),
        reranker=FailIfCalledReranker(),
    )

    selection = ranker.select_top_papers(astroph_preferences(), papers, max_papers=3)

    assert [paper.title for paper in selection.selected_papers] == [
        "AGN metallicity calibrations",
        "PAH variations in AGN hosts",
    ]


def test_paper_ranker_rejects_unknown_candidate_in_final_selection_payload(tmp_path) -> None:
    paper = make_paper(arxiv_id="2603.40031", title="Only Paper")
    provider = RecordingProvider(
        json.dumps(
            {
                "selected_papers": [
                    {"candidate_id": "arxiv:2603.49999", "score": 99, "rationale": "Unknown candidate."}
                ]
            }
        )
    )
    retrieved = _retrieved(
        paper,
        lexical_score=0.7,
        semantic_score=0.7,
        fused_score=0.75,
        matched_priorities=("Agents",),
        retrieval_channels=("lexical", "semantic"),
    )
    reranked = _reranked(retrieved, rerank_score=0.88, rationale="Good fit.")
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        retrieval_pool_size=12,
        final_pool_size=6,
        min_selection_score=80.0,
        retriever=StaticRetriever([retrieved]),
        reranker=StaticReranker([reranked]),
    )
    preferences = type(
        "Preferences",
        (),
        {"priorities": ("Agents",), "categories": ("cs.AI",), "raw_text": "1. Agents"},
    )()

    with pytest.raises(RankingError, match="unknown candidate_id"):
        ranker.select_top_papers(preferences, [paper], max_papers=1)
