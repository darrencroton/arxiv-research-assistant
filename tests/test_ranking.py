import json

import pytest

from re_ass.models import PreferenceConfig
from re_ass.ranking import PaperRanker, RankingError, _split_into_batches
from tests.support import make_app_config, make_paper


class RecordingProvider:
    def __init__(self, response: str | list[str]) -> None:
        self.responses = [response] if isinstance(response, str) else list(response)
        self.calls = []

    def process_document(self, content, is_pdf, system_prompt, user_prompt, max_tokens=12288, temperature=None):
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


class FlakyProvider:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def process_document(self, content, is_pdf, system_prompt, user_prompt, max_tokens=12288, temperature=None):
        del content, is_pdf, system_prompt, user_prompt, max_tokens, temperature
        self.calls += 1
        if not self.responses:
            raise AssertionError("provider was called more times than expected")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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
    assert '"candidate_id":"C001"' in prompt
    assert "arxiv:2603.40011" not in prompt
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
    # Partial-match paper scores above min_selection_score but fails dual-match, so it
    # must appear in weekly_interest rather than being silently excluded.
    assert [item.paper.title for item in selection.weekly_interest] == ["Science Only"]
    # Selected papers must not appear in weekly_interest.
    selected_titles = {paper.title for paper in selection.selected_papers}
    assert not any(item.paper.title in selected_titles for item in selection.weekly_interest)
    prompt = provider.calls[0]["user_prompt"]
    assert "<science_priorities>" in prompt
    assert "<method_priorities>" in prompt
    assert "do not push clear daily-summary candidates below 85" in prompt
    assert "A paper should normally need both science_match and method_match true to score 85+" in prompt
    assert "85-89: clear dual fits" in prompt
    assert "Reserve 90+ for papers that clearly deserve one of the day's top 1-3 summary slots" in prompt
    assert '"science_match":true' in prompt


def test_ranker_filters_by_threshold(tmp_path) -> None:
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
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    # Strong Fit clears always_summarize_score → selected; Second Fit is mid-band but
    # always_selected is non-empty so remaining_slots=0 → weekly_interest.
    assert [paper.title for paper in selection.selected_papers] == ["Strong Fit"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Second Fit"]
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
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    # Exceptional Fit clears always_summarize_score → always selected. Mid-band papers
    # get remaining_slots=0 (always_selected is non-empty) → all overflow to weekly_interest.
    assert [paper.title for paper in selection.selected_papers] == ["Exceptional Fit"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Strong Mid Fit", "Overflow Mid Fit"]


def test_ranker_keeps_all_top_band_papers_regardless_of_count(tmp_path) -> None:
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
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert [paper.title for paper in selection.selected_papers] == ["Top Fit One", "Top Fit Two", "Top Fit Three"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Mid Fit"]


def test_ranker_rescues_one_near_top_dual_match_when_top_papers_exist(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40110", title="Always Keep"),
        make_paper(arxiv_id="2603.40111", title="Near Top Rescue"),
        make_paper(arxiv_id="2603.40112", title="Second Near Top"),
        make_paper(arxiv_id="2603.40113", title="Interest Only"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {
                        "candidate_id": "arxiv:2603.40110",
                        "score": 94,
                        "science_match": True,
                        "method_match": True,
                        "rationale": "Clearly top tier.",
                    },
                    {
                        "candidate_id": "arxiv:2603.40111",
                        "score": 88,
                        "science_match": True,
                        "method_match": True,
                        "rationale": "Strong near-top dual match.",
                    },
                    {
                        "candidate_id": "arxiv:2603.40112",
                        "score": 87,
                        "science_match": True,
                        "method_match": True,
                        "rationale": "Also a near-top dual match.",
                    },
                    {
                        "candidate_id": "arxiv:2603.40113",
                        "score": 75,
                        "science_match": True,
                        "method_match": True,
                        "rationale": "Interesting but lower priority.",
                    },
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )
    preferences = PreferenceConfig(
        priorities=("Galaxy evolution", "Hydrodynamical simulations"),
        categories=("astro-ph.GA",),
        science_priorities=("Galaxy evolution",),
        method_priorities=("Hydrodynamical simulations",),
    )

    selection = ranker.rank_papers(preferences, papers)

    assert [paper.title for paper in selection.selected_papers] == ["Always Keep", "Near Top Rescue"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Second Near Top", "Interest Only"]


def test_ranker_does_not_rescue_near_top_dual_match_outside_top_three(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40120", title="Top Fit One"),
        make_paper(arxiv_id="2603.40121", title="Top Fit Two"),
        make_paper(arxiv_id="2603.40122", title="Top Fit Three"),
        make_paper(arxiv_id="2603.40123", title="Near Top Fourth"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {
                        "candidate_id": "arxiv:2603.40120",
                        "score": 96,
                        "science_match": True,
                        "method_match": True,
                        "rationale": "Top tier.",
                    },
                    {
                        "candidate_id": "arxiv:2603.40121",
                        "score": 94,
                        "science_match": True,
                        "method_match": True,
                        "rationale": "Top tier.",
                    },
                    {
                        "candidate_id": "arxiv:2603.40122",
                        "score": 92,
                        "science_match": True,
                        "method_match": True,
                        "rationale": "Top tier.",
                    },
                    {
                        "candidate_id": "arxiv:2603.40123",
                        "score": 88,
                        "science_match": True,
                        "method_match": True,
                        "rationale": "Near-top but outside the top three.",
                    },
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )
    preferences = PreferenceConfig(
        priorities=("Galaxy evolution", "Hydrodynamical simulations"),
        categories=("astro-ph.GA",),
        science_priorities=("Galaxy evolution",),
        method_priorities=("Hydrodynamical simulations",),
    )

    selection = ranker.rank_papers(preferences, papers)

    assert [paper.title for paper in selection.selected_papers] == ["Top Fit One", "Top Fit Two", "Top Fit Three"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Near Top Fourth"]


def test_ranker_selects_always_band_and_overflows_mid_band_when_top_papers_exist(tmp_path) -> None:
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
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert [paper.title for paper in selection.selected_papers] == ["Always Keep"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Weekly Only"]


def test_ranker_selects_one_fill_paper_when_no_top_band_papers_exist(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40064", title="Fill Candidate One"),
        make_paper(arxiv_id="2603.40065", title="Fill Candidate Two"),
    ]
    provider = RecordingProvider(
        json.dumps(
            {
                "ranked_papers": [
                    {"candidate_id": "arxiv:2603.40064", "score": 83, "rationale": "Good above-min fit."},
                    {"candidate_id": "arxiv:2603.40065", "score": 75, "rationale": "Also worth listing."},
                ]
            }
        )
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    # No always-band papers → _ABOVE_MIN_FILL_COUNT=1 slot opened → top fill candidate selected.
    assert [paper.title for paper in selection.selected_papers] == ["Fill Candidate One"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Fill Candidate Two"]


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
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    # Paper One (97) is always-band; Paper Two (75) is mid-band but remaining_slots=0.
    assert [paper.title for paper in selection.selected_papers] == ["Paper One"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Paper Two"]
    assert len(provider.calls) == 2
    assert "validation_error" in provider.calls[1]["user_prompt"]


def test_ranker_retries_missing_papers_instead_of_filling_score_zero(tmp_path) -> None:
    paper = make_paper(arxiv_id="2603.40051", title="Only Paper")
    provider = RecordingProvider([json.dumps({"ranked_papers": []}) for _ in range(6)])
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )

    with pytest.raises(RankingError, match="omitted candidate_id"):
        ranker.rank_papers(_preferences("Agents"), [paper])

    assert len(provider.calls) == 6


def test_split_into_batches_single_batch_within_overflow_tolerance() -> None:
    papers = [make_paper(arxiv_id=f"2603.{40070 + i}") for i in range(32)]
    # 32 papers, batch_size=30: effective_max=36, fits in one batch
    batches = _split_into_batches(papers, 30)
    assert len(batches) == 1
    assert len(batches[0]) == 32


def test_split_into_batches_even_split_uses_overflow_limit_as_divisor() -> None:
    papers = [make_paper(arxiv_id=f"2603.{40070 + i}") for i in range(62)]
    # 62 papers, batch_size=30: effective_max=36, ceil(62/36)=2 → [31, 31]
    batches = _split_into_batches(papers, 30)
    assert len(batches) == 2
    assert [len(b) for b in batches] == [31, 31]


def test_split_into_batches_three_way_split() -> None:
    papers = [make_paper(arxiv_id=f"2603.{40070 + i}") for i in range(73)]
    # 73 papers, batch_size=30: effective_max=36, ceil(73/36)=3 → [25, 24, 24]
    batches = _split_into_batches(papers, 30)
    assert len(batches) == 3
    assert [len(b) for b in batches] == [25, 24, 24]


def test_split_into_batches_at_exact_effective_max() -> None:
    papers = [make_paper(arxiv_id=f"2603.{40070 + i}") for i in range(36)]
    # 36 papers, batch_size=30: effective_max=36, still one batch
    batches = _split_into_batches(papers, 30)
    assert len(batches) == 1
    assert len(batches[0]) == 36


def test_ranker_batches_candidates_and_merges_by_score(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40070", title="Batch One A"),
        make_paper(arxiv_id="2603.40071", title="Batch One B"),
        make_paper(arxiv_id="2603.40072", title="Batch Two Winner"),
        make_paper(arxiv_id="2603.40073", title="Batch Two D"),
    ]
    # batch_size=2: effective_max=3, ceil(4/3)=2 → [2, 2]
    # After both batches a finalist re-ranking pass fires for papers scoring ≥60
    # (min_selection_score-10), producing a third provider call.
    provider = RecordingProvider(
        [
            json.dumps(
                {
                    "ranked_papers": [
                        {"candidate_id": "arxiv:2603.40070", "score": 75, "rationale": "Good fit."},
                        {"candidate_id": "arxiv:2603.40071", "score": 60, "rationale": "Partial fit."},
                    ]
                }
            ),
            json.dumps(
                {
                    "ranked_papers": [
                        {"candidate_id": "arxiv:2603.40072", "score": 95, "rationale": "Excellent fit."},
                        {"candidate_id": "arxiv:2603.40073", "score": 40, "rationale": "Weak fit."},
                    ]
                }
            ),
            # Finalist re-ranking: the three papers that scored ≥50 get a calibrated pass.
            json.dumps(
                {
                    "ranked_papers": [
                        {"candidate_id": "arxiv:2603.40072", "score": 95, "rationale": "Excellent fit."},
                        {"candidate_id": "arxiv:2603.40070", "score": 72, "rationale": "Good fit."},
                        {"candidate_id": "arxiv:2603.40071", "score": 55, "rationale": "Partial fit."},
                    ]
                }
            ),
        ]
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=70.0,
        batch_size=2,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert len(provider.calls) == 3
    assert [item.paper.title for item in selection.ranked] == [
        "Batch Two Winner",
        "Batch One A",
        "Batch One B",
        "Batch Two D",
    ]
    assert [paper.title for paper in selection.selected_papers] == ["Batch Two Winner"]
    assert not any(r.score_filled for r in selection.ranked)


def test_ranker_keeps_finalist_rerank_one_sided_papers_in_weekly_interest(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40080", title="Stable Dual Match"),
        make_paper(arxiv_id="2603.40081", title="Reranked Science Only"),
        make_paper(arxiv_id="2603.40082", title="Below Finalist"),
    ]
    provider = RecordingProvider(
        [
            json.dumps(
                {
                    "ranked_papers": [
                        {
                            "candidate_id": "arxiv:2603.40080",
                            "score": 76,
                            "science_match": True,
                            "method_match": True,
                            "rationale": "Matches both priority sections.",
                        },
                        {
                            "candidate_id": "arxiv:2603.40081",
                            "score": 75,
                            "science_match": True,
                            "method_match": True,
                            "rationale": "Initially appears to match both sections.",
                        },
                    ]
                }
            ),
            json.dumps(
                {
                    "ranked_papers": [
                        {
                            "candidate_id": "arxiv:2603.40082",
                            "score": 20,
                            "science_match": False,
                            "method_match": False,
                            "rationale": "Does not match either section.",
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "ranked_papers": [
                        {
                            "candidate_id": "arxiv:2603.40080",
                            "score": 92,
                            "science_match": True,
                            "method_match": True,
                            "rationale": "Still matches both priority sections.",
                        },
                        {
                            "candidate_id": "arxiv:2603.40081",
                            "score": 95,
                            "science_match": True,
                            "method_match": False,
                            "rationale": "Strong science topic but lacks the requested method angle.",
                        },
                    ]
                }
            ),
        ]
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=70.0,
        batch_size=1,
    )
    preferences = PreferenceConfig(
        priorities=("Little red dots", "Semi-analytic models"),
        categories=("astro-ph.GA",),
        science_priorities=("Little red dots",),
        method_priorities=("Semi-analytic models",),
    )

    selection = ranker.rank_papers(preferences, papers)

    assert len(provider.calls) == 3
    assert [paper.title for paper in selection.selected_papers] == ["Stable Dual Match"]
    assert [item.paper.title for item in selection.weekly_interest] == ["Reranked Science Only"]
    assert selection.weekly_interest[0].score == 95.0


def test_ranker_retries_invalid_payloads_before_failing(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2603.40051", title="Paper A"),
        make_paper(arxiv_id="2603.40052", title="Paper B"),
    ]
    provider = RecordingProvider(
        [
            json.dumps(
                {
                    "ranked_papers": [
                        {"candidate_id": "arxiv:2603.49999", "score": 97, "rationale": "Bad id."},
                        {"candidate_id": "arxiv:2603.40052", "score": 75, "rationale": "Known id."},
                    ]
                }
            )
            for _ in range(6)
        ]
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )

    with pytest.raises(RankingError, match="remained invalid after 6 validation attempts"):
        ranker.rank_papers(_preferences("Agents"), papers)
    assert len(provider.calls) == 6


def test_ranker_splits_large_invalid_batch_without_dropping_papers(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id=f"2603.{40100 + i}", title=f"Paper {i:02d}")
        for i in range(20)
    ]
    invalid_full_batch = json.dumps(
        {
            "ranked_papers": [
                {"candidate_id": "C999", "score": 97, "rationale": "Invalid local id."}
            ]
        }
    )
    valid_half_batch = json.dumps(
        {
            "ranked_papers": [
                {"candidate_id": f"C{i:03d}", "score": 60 - i, "rationale": "Recovered ranking."}
                for i in range(1, 11)
            ]
        }
    )
    provider = RecordingProvider(
        [invalid_full_batch for _ in range(6)]
        + [valid_half_batch, valid_half_batch]
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=70.0,
    )

    selection = ranker.rank_papers(_preferences("Agents"), papers)

    assert len(provider.calls) == 8
    assert len(selection.ranked) == 20
    assert {item.paper_key for item in selection.ranked} == {
        f"arxiv:2603.{40100 + i}" for i in range(20)
    }
    assert not any(item.score_filled for item in selection.ranked)


def test_ranker_retries_once_after_retryable_provider_failure(tmp_path, monkeypatch) -> None:
    paper = make_paper(arxiv_id="2603.40091", title="Retryable Ranking")
    provider = FlakyProvider(
        [
            RuntimeError("copilot timed out after 1200s"),
            json.dumps(
                {
                    "ranked_papers": [
                        {"candidate_id": "arxiv:2603.40091", "score": 92, "rationale": "Recovered after a retry."}
                    ]
                }
            ),
        ]
    )
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )
    sleep_calls: list[int] = []
    monkeypatch.setattr("re_ass.ranking.time.sleep", lambda seconds: sleep_calls.append(seconds))

    selection = ranker.rank_papers(_preferences("Agents"), [paper])

    assert [item.paper.title for item in selection.selected] == ["Retryable Ranking"]
    assert provider.calls == 2
    assert sleep_calls == [2]


def test_ranker_does_not_retry_non_retryable_provider_failure(tmp_path, monkeypatch) -> None:
    paper = make_paper(arxiv_id="2603.40092", title="Auth Failure")
    provider = FlakyProvider([RuntimeError("copilot authentication failed")])
    ranker = PaperRanker(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        always_summarize_score=90.0,
        min_selection_score=80.0,
    )
    sleep_calls: list[int] = []
    monkeypatch.setattr("re_ass.ranking.time.sleep", lambda seconds: sleep_calls.append(seconds))

    with pytest.raises(RankingError, match="authentication failed"):
        ranker.rank_papers(_preferences("Agents"), [paper])

    assert provider.calls == 1
    assert sleep_calls == []
