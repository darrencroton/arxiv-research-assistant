import json

import pytest

from re_ass.models import PreferenceConfig
from re_ass.ranking import PaperRanker, RankingError
from tests.support import make_app_config, make_paper


class RecordingProvider:
    def __init__(self, response: str | list[str]) -> None:
        self.responses = [response] if isinstance(response, str) else list(response)
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
        if not self.responses:
            raise AssertionError("provider was called more times than expected")
        return self.responses.pop(0)


def _preferences(*priorities: str) -> PreferenceConfig:
    return PreferenceConfig(
        priorities=priorities,
        categories=("astro-ph.GA",),
    )


def test_ranker_uses_ordered_priorities_and_compact_candidate_payload(tmp_path) -> None:
    paper = make_paper(
        arxiv_id="2603.40011",
        title="Semantic Agents",
        summary="Agentic workflows for tool use.",
        authors=("Author One", "Author Two"),
    )
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {"candidate_id": "arxiv:2603.40011", "score": 96, "rationale": "Best match to the highest priorities."}
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=3,
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )

    selection = ranker.rank_papers(_preferences("Agents", "Tool use"), [paper])

    assert [item.paper.title for item in selection.selected] == ["Semantic Agents"]
    prompt = provider.calls[0]["user_prompt"]
    assert "1. Agents" in prompt
    assert "2. Tool use" in prompt
    assert "Semantic Agents" in prompt
    assert "Agentic workflows for tool use." in prompt
    assert "Author One" not in prompt
    assert "Matching multiple priorities is a bonus, not a requirement." in prompt


def test_ranker_requires_science_and_method_matches_when_sections_are_present(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40015", title="Dual Match"),
        make_paper(arxiv_id="2603.40016", title="Science Only"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {
                        "candidate_id": "arxiv:2603.40015",
                        "score": 91,
                        "science_match": True,
                        "method_match": True,
                        "rationale": "Matches the target science and method priorities directly.",
                    },
                    {
                        "candidate_id": "arxiv:2603.40016",
                        "score": 95,
                        "science_match": True,
                        "method_match": False,
                        "rationale": "Strong science topic but lacks the requested method angle.",
                    },
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=5,
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )
    preferences = PreferenceConfig(
        priorities=("Little red dots", "Semi-analytic models"),
        categories=("astro-ph.GA",),
        science_priorities=("Little red dots",),
        method_priorities=("Semi-analytic models",),
    )

    selection = ranker.rank_papers(preferences, papers)

    assert [paper.title for paper in selection.selected_papers] == ["Dual Match"]
    prompt = provider.calls[0]["user_prompt"]
    assert "<science_priorities>" in prompt
    assert "<method_priorities>" in prompt
    assert "A paper should only receive a strong score if both science_match and method_match are true." in prompt
    assert '"science_match":true' in prompt


def test_ranker_filters_by_threshold_and_cap(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40021", title="Strong Fit"),
        make_paper(arxiv_id="2603.40022", title="Second Fit"),
        make_paper(arxiv_id="2603.40023", title="Weak Fit"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {"candidate_id": "arxiv:2603.40021", "score": 94, "rationale": "Strongest match."},
                    {"candidate_id": "arxiv:2603.40022", "score": 81, "rationale": "Solid secondary match."},
                    {"candidate_id": "arxiv:2603.40023", "score": 61, "rationale": "Only a borderline fit."},
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=2,
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert [paper.title for paper in selection.selected_papers] == ["Strong Fit", "Second Fit"]
    assert selection.weekly_interest == []
    assert [item.paper.title for item in selection.ranked] == ["Strong Fit", "Second Fit", "Weak Fit"]


def test_ranker_sorts_by_score_when_provider_returns_unsorted_payload(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40031", title="Lower Score"),
        make_paper(arxiv_id="2603.40032", title="Higher Score"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {"candidate_id": "arxiv:2603.40031", "score": 60, "rationale": "Lower relevance."},
                    {"candidate_id": "arxiv:2603.40032", "score": 95, "rationale": "Highest relevance."},
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=1,
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert [item.paper.title for item in selection.ranked] == ["Higher Score", "Lower Score"]
    assert [paper.title for paper in selection.selected_papers] == ["Higher Score"]
    assert [item.paper.title for item in selection.weekly_interest] == []


def test_ranker_always_keeps_top_band_and_overflows_mid_band_to_weekly_interest(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40033", title="Exceptional Fit"),
        make_paper(arxiv_id="2603.40034", title="Strong Mid Fit"),
        make_paper(arxiv_id="2603.40035", title="Overflow Mid Fit"),
        make_paper(arxiv_id="2603.40036", title="Below Threshold"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {"candidate_id": "arxiv:2603.40033", "score": 97, "rationale": "Must keep."},
                    {"candidate_id": "arxiv:2603.40034", "score": 84, "rationale": "Good enough to fill."},
                    {"candidate_id": "arxiv:2603.40035", "score": 79, "rationale": "Interesting overflow."},
                    {"candidate_id": "arxiv:2603.40036", "score": 65, "rationale": "Not relevant enough."},
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=2,
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert [paper.title for paper in selection.selected_papers] == ["Exceptional Fit", "Strong Mid Fit"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Overflow Mid Fit"]


def test_ranker_can_exceed_max_papers_for_multiple_top_band_matches(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40037", title="Top Fit One"),
        make_paper(arxiv_id="2603.40038", title="Top Fit Two"),
        make_paper(arxiv_id="2603.40039", title="Top Fit Three"),
        make_paper(arxiv_id="2603.40040", title="Mid Fit"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {"candidate_id": "arxiv:2603.40037", "score": 98, "rationale": "Excellent."},
                    {"candidate_id": "arxiv:2603.40038", "score": 95, "rationale": "Excellent."},
                    {"candidate_id": "arxiv:2603.40039", "score": 92, "rationale": "Excellent."},
                    {"candidate_id": "arxiv:2603.40040", "score": 81, "rationale": "Good fallback."},
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=2,
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert [paper.title for paper in selection.selected_papers] == ["Top Fit One", "Top Fit Two", "Top Fit Three"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Mid Fit"]


def test_ranker_with_zero_max_papers_keeps_only_always_summarize_matches(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40061", title="Always Keep"),
        make_paper(arxiv_id="2603.40062", title="Weekly Only"),
        make_paper(arxiv_id="2603.40063", title="Below Threshold"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {"candidate_id": "arxiv:2603.40061", "score": 94, "rationale": "Always summarize."},
                    {"candidate_id": "arxiv:2603.40062", "score": 78, "rationale": "Interesting, but not top tier."},
                    {"candidate_id": "arxiv:2603.40063", "score": 60, "rationale": "Too weak."},
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=0,
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert [paper.title for paper in selection.selected_papers] == ["Always Keep"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Weekly Only"]


def test_ranker_with_zero_max_papers_can_return_weekly_interest_only(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40064", title="Weekly Only One"),
        make_paper(arxiv_id="2603.40065", title="Weekly Only Two"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {"candidate_id": "arxiv:2603.40064", "score": 83, "rationale": "Good weekly-only fit."},
                    {"candidate_id": "arxiv:2603.40065", "score": 75, "rationale": "Also worth listing."},
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=0,
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert selection.selected_papers == []
    assert [item.paper.title for item in selection.weekly_interest] == ["Weekly Only One", "Weekly Only Two"]


def test_ranker_repairs_invalid_payload_once(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40041", title="Paper One"),
        make_paper(arxiv_id="2603.40042", title="Paper Two"),
    ]
    provider = RecordingProvider(
        [
            json.dumps(
                {
                    "ranked_papers": [
                        {"candidate_id": "arxiv:2603.49999", "score": 97, "rationale": "Unknown id."},
                        {"candidate_id": "arxiv:2603.40042", "score": 75, "rationale": "Known id."},
                    ]
                }
            ),
            json.dumps(
                {
                    "ranked_papers": [
                        {"candidate_id": "arxiv:2603.40041", "score": 97, "rationale": "Best match."},
                        {"candidate_id": "arxiv:2603.40042", "score": 75, "rationale": "Secondary match."},
                    ]
                }
            ),
        ]
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=2,
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert [paper.title for paper in selection.selected_papers] == ["Paper One", "Paper Two"]
    assert len(provider.calls) == 2
    assert "validation_error" in provider.calls[1]["user_prompt"]


def test_ranker_requires_full_ranked_list_after_repair(tmp_path) -> None:
    paper = make_paper(arxiv_id="2603.40051", title="Only Paper")
    provider = RecordingProvider(
        [
            json.dumps({"ranked_papers": []}),
            json.dumps({"ranked_papers": []}),
        ]
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        max_papers=1,
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )

    with pytest.raises(RankingError, match="remained invalid after repair attempt"):
        ranker.rank_papers(_preferences("Agents"), [paper])
    assert len(provider.calls) == 2
