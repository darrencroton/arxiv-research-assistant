from dataclasses import replace

import pytest

from re_ass.models import PreferenceConfig
from re_ass.paper_summariser.providers import create_provider
from re_ass.paper_summariser.providers.api import OpenAIDecisionsAPI
from re_ass.ranking import PaperRanker, RankingError
from tests.support import make_app_config, make_paper


def _score_answer(score: float, *, top_label: str = "Top fit", top_probability: float = 0.9) -> dict:
    return {
        "type": "score",
        "name": "relevance",
        "score": score,
        "probabilities": [{"value": 4, "label": top_label, "probability": top_probability}],
        "confidence": 0.8,
    }


def _predicate(name: str, probability: float) -> dict:
    return {"type": "predicate", "name": name, "probability": probability}


class FakeDecisionsProvider:
    """Answers keyed by paper title, found in the request input."""

    def __init__(self, answers_by_title: dict[str, list[object]]) -> None:
        self.answers_by_title = {title: list(answers) for title, answers in answers_by_title.items()}
        self.calls: list[tuple[str, list[dict]]] = []

    def decide(self, input_text, questions):
        self.calls.append((input_text, questions))
        title = next(title for title in self.answers_by_title if f"Title: {title}\n" in input_text)
        answer = self.answers_by_title[title].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def process_document(self, *args, **kwargs):
        raise AssertionError("decisions mode must not request generated text")


def _decisions_config(tmp_path):
    return replace(make_app_config(tmp_path).llm, mode="decisions", provider="openai", model="gpt-6-luna")


def _ranker(provider, tmp_path, **overrides) -> PaperRanker:
    options = {
        "min_summarize_score": 90.0,
        "min_selection_score": 70.0,
        "max_summarized_papers": 3,
    }
    options.update(overrides)
    return PaperRanker(provider=provider, config=_decisions_config(tmp_path), **options)


def test_decisions_ranking_maps_level_index_to_score_and_selects(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2610.00001", title="Strong Paper", summary="Radiation feedback."),
        make_paper(arxiv_id="2610.00002", title="Weak Paper", summary="Unrelated topic."),
    ]
    provider = FakeDecisionsProvider(
        {
            "Strong Paper": [[_score_answer(3.8)]],
            "Weak Paper": [[_score_answer(2.0, top_label="Partial fit", top_probability=0.6)]],
        }
    )

    selection = _ranker(provider, tmp_path).rank_papers(
        PreferenceConfig(priorities=("Radiation feedback",), categories=("astro-ph.GA",)),
        papers,
    )

    assert [(item.paper.title, item.score) for item in selection.ranked] == [
        ("Strong Paper", 95.0),
        ("Weak Paper", 50.0),
    ]
    assert [item.paper.title for item in selection.selected] == ["Strong Paper"]
    assert selection.weekly_interest == []
    assert "most likely level 'Top fit' (p=0.90)" in selection.ranked[0].rationale
    input_text, questions = provider.calls[0]
    assert "1. Radiation feedback" in input_text
    assert [question["name"] for question in questions] == ["relevance"]
    assert len(questions[0]["levels"]) == 5


def test_decisions_ranking_requires_both_predicates_for_dual_match(tmp_path) -> None:
    papers = [
        make_paper(arxiv_id="2610.00003", title="Dual Match"),
        make_paper(arxiv_id="2610.00004", title="Science Only"),
    ]
    provider = FakeDecisionsProvider(
        {
            "Dual Match": [[_score_answer(3.7), _predicate("science_match", 0.9), _predicate("method_match", 0.8)]],
            "Science Only": [[_score_answer(4.0), _predicate("science_match", 0.95), _predicate("method_match", 0.2)]],
        }
    )
    preferences = PreferenceConfig(
        priorities=("Little red dots", "Semi-analytic models"),
        categories=("astro-ph.GA",),
        science_priorities=("Little red dots",),
        method_priorities=("Semi-analytic models",),
    )

    selection = _ranker(provider, tmp_path).rank_papers(preferences, papers)

    assert [item.paper.title for item in selection.selected] == ["Dual Match"]
    science_only = next(item for item in selection.ranked if item.paper.title == "Science Only")
    assert (science_only.science_match, science_only.method_match) == (True, False)
    assert "method_match p=0.20" in science_only.rationale
    assert [question["name"] for question in provider.calls[0][1]] == ["relevance", "science_match", "method_match"]
    assert "<method_priorities>" in provider.calls[0][0]


def test_decisions_refusal_scores_zero(tmp_path) -> None:
    paper = make_paper(arxiv_id="2610.00005", title="Refused Paper")
    provider = FakeDecisionsProvider({"Refused Paper": [[{"type": "refusal", "name": "relevance"}]]})

    selection = _ranker(provider, tmp_path).rank_papers(
        PreferenceConfig(priorities=("Anything",), categories=("astro-ph.GA",)),
        [paper],
    )

    assert selection.ranked[0].score == 0.0
    assert "refused" in selection.ranked[0].rationale
    assert selection.selected == []


def test_decisions_invalid_answer_is_retried(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("re_ass.ranking.time.sleep", lambda _seconds: None)
    paper = make_paper(arxiv_id="2610.00006", title="Flaky Paper")
    provider = FakeDecisionsProvider({"Flaky Paper": [[], RuntimeError("connection reset"), [_score_answer(4.0)]]})

    selection = _ranker(provider, tmp_path).rank_papers(
        PreferenceConfig(priorities=("Anything",), categories=("astro-ph.GA",)),
        [paper],
    )

    assert len(provider.calls) == 3
    assert selection.ranked[0].score == 100.0


def test_decisions_failure_after_retries_raises_ranking_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("re_ass.ranking.time.sleep", lambda _seconds: None)
    paper = make_paper(arxiv_id="2610.00007", title="Broken Paper")
    provider = FakeDecisionsProvider({"Broken Paper": [[], [], []]})

    with pytest.raises(RankingError, match="Decisions ranking call"):
        _ranker(provider, tmp_path).rank_papers(
            PreferenceConfig(priorities=("Anything",), categories=("astro-ph.GA",)),
            [paper],
        )


def test_decisions_ranking_ignores_batch_size(tmp_path) -> None:
    papers = [make_paper(arxiv_id=f"2610.0001{index}", title=f"Paper {index}") for index in range(3)]
    provider = FakeDecisionsProvider({f"Paper {index}": [[_score_answer(3.0)]] for index in range(3)})

    selection = _ranker(provider, tmp_path, batch_size=1).rank_papers(
        PreferenceConfig(priorities=("Anything",), categories=("astro-ph.GA",)),
        papers,
    )

    assert len(provider.calls) == 3
    assert [item.score for item in selection.ranked] == [75.0, 75.0, 75.0]


class FakeOpenAIClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.posts: list[tuple[str, dict]] = []

    def post(self, path, *, body, cast_to):
        self.posts.append((path, body))
        return {"model": body["model"], "answers": [_score_answer(1.0)]}


def test_decisions_provider_resolves_key_from_env_file_and_posts_body(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.setattr("openai.OpenAI", FakeOpenAIClient)

    provider = create_provider("decisions", "openai", config={"env_file": str(env_file), "timeout": 30})
    answers = provider.decide("input text", [{"type": "predicate", "name": "p", "instructions": "x"}])

    assert isinstance(provider, OpenAIDecisionsAPI)
    assert provider.client.kwargs["api_key"] == "sk-test"
    assert provider.client.posts == [
        (
            "/decisions",
            {
                "model": "gpt-6-luna",
                "input": "input text",
                "questions": [{"type": "predicate", "name": "p", "instructions": "x"}],
            },
        )
    ]
    assert answers[0]["name"] == "relevance"


def test_decisions_provider_rejects_missing_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_provider("decisions", "openai", config={"env_file": str(tmp_path / "missing.env")})


def test_decisions_provider_cannot_generate_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("openai.OpenAI", FakeOpenAIClient)
    provider = create_provider("decisions", "openai")

    with pytest.raises(ValueError, match="cannot generate text"):
        provider.process_document("", False, "system", "user")
