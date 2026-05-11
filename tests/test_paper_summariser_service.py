from pathlib import Path

import pytest

import re_ass.paper_summariser.service as paper_service
from re_ass.paper_summariser.providers.base import Provider
from re_ass.paper_summariser.service import (
    GLOSSARY_MAX_TERMS,
    PaperSummariser,
    PaperSummariserError,
    ProjectKnowledge,
    SourceMetadata,
    build_fallback_tags,
    generate_tags,
    create_system_prompt,
    create_user_prompt,
    filter_keywords_for_categories,
    fit_prompt_to_provider_budget,
    insert_section,
    normalise_extracted_text,
    normalise_tags_section,
    read_project_knowledge,
    validate_glossary_section,
    validate_tags_section,
)
from tests.support import make_paper, make_app_config


class RecordingProvider(Provider):
    def setup(self):
        self.calls: list[dict[str, object]] = []
        self._supports_direct_pdf = bool(self.config.get("supports_direct_pdf", False))
        raw_responses = self.config["response"]
        if isinstance(raw_responses, list):
            self.responses = [str(response) for response in raw_responses]
        else:
            self.responses = [str(raw_responses)]

    def supports_direct_pdf(self):
        return self._supports_direct_pdf

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
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def get_max_context_size(self):
        return 200_000


def _main_summary() -> str:
    return (
        "# Agents for Research\n\n"
        "Authors: Doe J., Smith J.\n"
        "Published: March 2026 ([Link](https://arxiv.org/abs/1234.5678))\n\n"
        "## Key Ideas\n"
        "- Important point[^1]\n\n"
        "## References\n"
        '[^1]: "Quoted support" (Abstract, p.1)\n'
    )


def _tags_section() -> str:
    return "## Tags\n\n#JWST\n\n#CosmologyObservations"


def _glossary_section() -> str:
    return (
        "## Glossary\n\n"
        "| Term | Definition |\n"
        "|---|---|\n"
        "| **Inference** | A method for estimating model parameters from data. |"
    )


def test_summarise_source_uses_extracted_text(tmp_path: Path) -> None:
    provider = RecordingProvider(
        {
            "response": [_main_summary(), _tags_section(), _glossary_section()],
        }
    )
    source_path = tmp_path / "1234.5678.pdf"
    source_path.write_bytes(b"%PDF-1.4")

    def input_reader(_path: Path, _provider: Provider, _config):
        return "arXiv: 1234.5678\nExtracted paper text.", None

    summariser = PaperSummariser(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        input_reader=input_reader,
    )

    result = summariser.summarise_source(make_paper(arxiv_id="1234.5678", title="Agents for Research"), source_path)

    assert "## Key Ideas" in result.raw_summary
    assert result.pdf_url == "https://arxiv.org/pdf/1234.5678.pdf"
    assert result.raw_summary.index("## Glossary") < result.raw_summary.index("## Tags")
    assert result.raw_summary.index("## Tags") < result.raw_summary.index("## References")
    assert provider.calls[0]["content"] == "arXiv: 1234.5678\nExtracted paper text."
    assert "Canonical paper link: https://arxiv.org/abs/1234.5678" in str(provider.calls[0]["user_prompt"])
    assert "## Glossary" not in str(provider.calls[0]["user_prompt"])
    assert "## Tags" not in str(provider.calls[0]["user_prompt"])
    assert "Available science-area keywords" in str(provider.calls[1]["user_prompt"])
    assert "COSMOLOGY" in str(provider.calls[1]["user_prompt"])
    assert "PLANETARY SYSTEMS" not in str(provider.calls[1]["user_prompt"])
    assert provider.calls[1]["content"] == ""
    assert provider.calls[1]["max_tokens"] == 512
    assert "---BEGIN SUMMARY---" in str(provider.calls[2]["user_prompt"])
    assert provider.calls[2]["content"] == ""
    assert provider.calls[2]["max_tokens"] == 2048


def test_summarise_source_uses_direct_pdf_when_provider_supports_it(tmp_path: Path) -> None:
    provider = RecordingProvider(
        {
            "supports_direct_pdf": True,
            "response": [_main_summary(), _tags_section(), _glossary_section()],
        }
    )
    source_path = tmp_path / "1234.5678.pdf"
    source_path.write_bytes(b"%PDF-1.4 direct pdf")

    def input_reader(_path: Path, _provider: Provider, _config):
        return b"%PDF-1.4 direct pdf", None

    summariser = PaperSummariser(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        input_reader=input_reader,
    )

    summariser.summarise_source(make_paper(arxiv_id="1234.5678", title="Agents for Research"), source_path)

    assert provider.calls[0]["content"] == b"%PDF-1.4 direct pdf"
    assert provider.calls[0]["is_pdf"] is True
    assert "---BEGIN PAPER---" not in str(provider.calls[0]["user_prompt"])
    assert provider.calls[1]["content"] == ""
    assert provider.calls[1]["is_pdf"] is False


def test_normalise_extracted_text_removes_pathological_marker_lines() -> None:
    wide_table_row = "|" + (" noisy cell |" * 120)
    html_noise_line = "<br>".join(["1.23+0.04"] * 140)
    text = "\n".join(
        [
            "The abstract remains intact.",
            "| Filter | Value |",
            "| F150W | 1.23 |",
            wide_table_row,
            html_noise_line,
            "The conclusion remains intact.",
        ]
    )

    cleaned = normalise_extracted_text(text)

    assert "The abstract remains intact." in cleaned
    assert "| Filter | Value |" in cleaned
    assert "| F150W | 1.23 |" in cleaned
    assert wide_table_row not in cleaned
    assert html_noise_line not in cleaned
    assert "The conclusion remains intact." in cleaned


def test_fit_prompt_to_provider_budget_drops_markdown_references_heading() -> None:
    provider = RecordingProvider(
        {
            "response": "# Summary\n\n## Key Ideas\n- Point\n",
            "max_prompt_chars": 1400,
        }
    )
    system_prompt = "system"
    prompt_template = "$SUMMARY_TEMPLATE\n$SOURCE_METADATA_BLOCK$PAPER_INPUT_BLOCK"
    paper_text = "\n".join(
        [
            "# Paper",
            "Main science result.",
            "## References and Notes",
            "Reference noise. " * 200,
        ]
    )

    reduced_text, user_prompt = fit_prompt_to_provider_budget(
        provider,
        system_prompt,
        paper_text,
        "template",
        prompt_template,
    )

    assert "Main science result." in reduced_text
    assert "Reference noise." not in reduced_text
    assert "Reference noise." not in user_prompt


def test_fit_prompt_to_provider_budget_drops_reference_heading_variants() -> None:
    provider = RecordingProvider(
        {
            "response": "# Summary\n\n## Key Ideas\n- Point\n",
            "max_prompt_chars": 1400,
        }
    )
    system_prompt = "system"
    prompt_template = "$SUMMARY_TEMPLATE\n$SOURCE_METADATA_BLOCK$PAPER_INPUT_BLOCK"
    paper_text = "\n".join(
        [
            "# Paper",
            "Main science result.",
            "### Works Cited.",
            "Reference noise. " * 200,
        ]
    )

    reduced_text, user_prompt = fit_prompt_to_provider_budget(
        provider,
        system_prompt,
        paper_text,
        "template",
        prompt_template,
    )

    assert "Main science result." in reduced_text
    assert "Reference noise." not in reduced_text
    assert "Reference noise." not in user_prompt


def test_fit_prompt_to_provider_budget_drops_appendix_after_references() -> None:
    provider = RecordingProvider(
        {
            "response": "# Summary\n\n## Key Ideas\n- Point\n",
            "max_prompt_chars": 1400,
        }
    )
    system_prompt = "system"
    prompt_template = "$SUMMARY_TEMPLATE\n$SOURCE_METADATA_BLOCK$PAPER_INPUT_BLOCK"
    paper_text = "\n".join(
        [
            "# Paper",
            "Main science result.",
            "#### REFERENCES",
            "Reference noise. " * 200,
            "### Appendix A",
            "Appendix noise. " * 200,
        ]
    )

    reduced_text, user_prompt = fit_prompt_to_provider_budget(
        provider,
        system_prompt,
        paper_text,
        "template",
        prompt_template,
    )

    assert "Main science result." in reduced_text
    assert "Reference noise." not in reduced_text
    assert "Appendix noise." not in reduced_text
    assert "Appendix noise." not in user_prompt


def test_fit_prompt_to_provider_budget_drops_supplementary_materials() -> None:
    provider = RecordingProvider(
        {
            "response": "# Summary\n\n## Key Ideas\n- Point\n",
            "max_prompt_chars": 1400,
        }
    )
    system_prompt = "system"
    prompt_template = "$SUMMARY_TEMPLATE\n$SOURCE_METADATA_BLOCK$PAPER_INPUT_BLOCK"
    paper_text = "\n".join(
        [
            "# Paper",
            "Main science result.",
            "## Supplementary Materials:",
            "Supplement noise. " * 200,
        ]
    )

    reduced_text, user_prompt = fit_prompt_to_provider_budget(
        provider,
        system_prompt,
        paper_text,
        "template",
        prompt_template,
    )

    assert "Main science result." in reduced_text
    assert "Supplement noise." not in reduced_text
    assert "Supplement noise." not in user_prompt


def test_summarise_source_fits_prompt_before_calling_provider(tmp_path: Path) -> None:
    provider = RecordingProvider(
        {
            "max_prompt_chars": 50_000,
            "response": [_main_summary(), _tags_section(), _glossary_section()],
        }
    )
    source_path = tmp_path / "1234.5678.pdf"
    source_path.write_bytes(b"%PDF-1.4")
    paper_text = "\n".join(
        [
            "arXiv: 1234.5678",
            "Main science result.",
            "## References",
            "Reference noise. " * 6000,
        ]
    )

    def input_reader(_path: Path, _provider: Provider, _config):
        return paper_text, None

    summariser = PaperSummariser(
        provider=provider,
        config=make_app_config(tmp_path).llm,
        input_reader=input_reader,
    )

    summariser.summarise_source(make_paper(arxiv_id="1234.5678", title="Agents for Research"), source_path)

    user_prompt = str(provider.calls[0]["user_prompt"])
    assert "Main science result." in user_prompt
    assert "Reference noise." not in user_prompt


def test_read_project_knowledge_loads_prompt_files() -> None:
    project_knowledge = read_project_knowledge()

    assert isinstance(project_knowledge, ProjectKnowledge)
    assert project_knowledge.keywords
    assert "$KEYWORDS" not in project_knowledge.system_prompt_template
    assert "$SUMMARY_TEMPLATE" in project_knowledge.user_prompt_template
    assert "$WORKED_EXAMPLE_BLOCK" in project_knowledge.user_prompt_template
    assert project_knowledge.summary_worked_example


def test_read_project_knowledge_allows_missing_worked_example(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_dir = tmp_path / "project_knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "keywords.txt").write_text("GENERAL\n#General", encoding="utf-8")
    (knowledge_dir / "paper-summary-template.md").write_text("# Template", encoding="utf-8")
    (knowledge_dir / "system-prompt.md").write_text("system", encoding="utf-8")
    (knowledge_dir / "user-prompt.md").write_text("$SUMMARY_TEMPLATE", encoding="utf-8")
    monkeypatch.setattr(paper_service, "_KNOWLEDGE_DIR", knowledge_dir)

    project_knowledge = read_project_knowledge()

    assert project_knowledge.summary_worked_example == ""


def test_create_prompt_uses_external_prompt_templates() -> None:
    system_prompt = create_system_prompt("system prompt")
    user_prompt = create_user_prompt(
        "body text",
        "summary template",
        "user $SUMMARY_TEMPLATE $SOURCE_METADATA_BLOCK$WORKED_EXAMPLE_BLOCK$PAPER_INPUT_BLOCK",
        source_metadata=SourceMetadata(
            source_type="arxiv",
            identifier="2312.5678",
            canonical_url="https://arxiv.org/abs/2312.5678",
            published_label="December 2023",
        ),
        worked_example="## Results\n\n- Example result.[^1]",
    )

    assert system_prompt == "system prompt"
    assert "summary template" in user_prompt
    assert "Published line date: December 2023" in user_prompt
    assert "<worked_example>" in user_prompt
    assert "do not copy its topic, claims, names, or references" in user_prompt
    assert "---BEGIN PAPER---" in user_prompt
    assert user_prompt.index("<source_metadata>") < user_prompt.index("<worked_example>")
    assert user_prompt.index("<worked_example>") < user_prompt.index("---BEGIN PAPER---")


_KEYWORDS = "\n\n".join(
    [
        "GENERAL\n#General",
        "PHYSICAL DATA AND PROCESSES\n#BlackHolePhysics",
        "ASTRONOMICAL INSTRUMENTATION, METHODS AND TECHNIQUES\n#Telescopes",
        "ASTRONOMICAL DATABASES\n#Surveys",
        "GALAXIES\n#GalaxiesEvolution\n#GalaxiesHighRedshift",
        "COSMOLOGY\n#CosmologyObservations",
        "PLANETARY SYSTEMS\n#PlanetsAndSatellitesDetection",
    ]
)


def test_filter_keywords_for_categories_keeps_relevant_sections_and_defaults() -> None:
    filtered = filter_keywords_for_categories(_KEYWORDS, ("astro-ph.CO",))

    assert "COSMOLOGY" in filtered
    assert "#CosmologyObservations" in filtered
    assert "GALAXIES" in filtered
    assert "GENERAL" in filtered
    assert "ASTRONOMICAL DATABASES" in filtered
    assert "PLANETARY SYSTEMS" not in filtered


def test_filter_keywords_for_unknown_category_falls_back_to_full_list() -> None:
    assert filter_keywords_for_categories(_KEYWORDS, ("cond-mat.stat-mech",)) == _KEYWORDS


def test_validate_glossary_section_accepts_markdown_table() -> None:
    validate_glossary_section(
        "## Glossary\n\n"
        "| Term | Definition |\n"
        "|---|---|\n"
        "| **Redshift** | Stretching of observed wavelength by cosmic expansion. |"
    )


def test_validate_glossary_section_rejects_extra_content() -> None:
    with pytest.raises(ValueError, match="only a two-column table"):
        validate_glossary_section(
            "## Glossary\n\n"
            "| Term | Definition |\n"
            "|---|---|\n"
            "| **Redshift** | Stretching of observed wavelength by cosmic expansion. |\n\n"
            "Additional commentary."
        )


def test_validate_glossary_section_rejects_extra_columns() -> None:
    with pytest.raises(ValueError, match="only a two-column table"):
        validate_glossary_section(
            "## Glossary\n\n"
            "| Term | Definition |\n"
            "|---|---|\n"
            "| **Redshift** | Meaning | Extra |"
        )


def test_validate_glossary_section_rejects_too_many_terms() -> None:
    rows = "\n".join(
        f"| **Term {index}** | Definition. |"
        for index in range(GLOSSARY_MAX_TERMS + 1)
    )

    with pytest.raises(ValueError, match="no more than"):
        validate_glossary_section(
            "## Glossary\n\n"
            "| Term | Definition |\n"
            "|---|---|\n"
            f"{rows}"
        )


def test_normalise_tags_section_accepts_labeled_comma_separated_lines() -> None:
    assert normalise_tags_section(
        "## Tags\n\n"
        "Proper nouns: #JWST, #CEERS\n\n"
        "- Science keywords: #GalaxiesHighRedshift, #CosmologyObservations",
        _KEYWORDS,
    ) == (
        "## Tags\n\n"
        "#JWST #CEERS\n\n"
        "#GalaxiesHighRedshift #CosmologyObservations"
    )


def test_normalise_tags_section_routes_keyword_tags_from_first_line_to_science_line() -> None:
    assert normalise_tags_section(
        "## Tags\n\n#Surveys #JWST\n\n#GalaxiesHighRedshift",
        _KEYWORDS,
    ) == "## Tags\n\n#JWST\n\n#Surveys #GalaxiesHighRedshift"


def test_normalise_tags_section_drops_unknown_science_line_tags() -> None:
    assert normalise_tags_section(
        "## Tags\n\n#JWST\n\n#MadeUpScienceTag #GalaxiesHighRedshift",
        _KEYWORDS,
    ) == "## Tags\n\n#JWST\n\n#GalaxiesHighRedshift"


def test_normalise_tags_section_truncates_too_many_tags() -> None:
    assert normalise_tags_section(
        "## Tags\n\n#A #B #C #D #E #F\n\n#GalaxiesHighRedshift",
        _KEYWORDS,
    ) == "## Tags\n\n#A #B #C #D #E\n\n#GalaxiesHighRedshift"


def test_validate_tags_section_accepts_one_or_two_hashtag_lines() -> None:
    validate_tags_section(
        "## Tags\n\n#JWST #CEERS\n\n#GalaxiesHighRedshift #CosmologyObservations",
        _KEYWORDS,
    )
    validate_tags_section("## Tags\n\n#JWST", _KEYWORDS)


def test_build_fallback_tags_derives_science_tags_from_summary() -> None:
    result = build_fallback_tags(
        "# Summary\n\nCosmology observations of galaxies.",
        _KEYWORDS,
    )

    assert "#CosmologyObservations" in result


def test_generate_tags_falls_back_for_unparseable_model_output(tmp_path: Path) -> None:
    provider = RecordingProvider({"response": "## Tags\n\nNo useful tags."})

    result = generate_tags(
        "# Summary\n\nCosmology observations of galaxies.",
        _KEYWORDS,
        provider,
        config=make_app_config(tmp_path).llm,
    )

    assert "#CosmologyObservations" in result


def test_generate_tags_falls_back_on_provider_failure(tmp_path: Path) -> None:
    class FailingProvider(Provider):
        def setup(self):
            pass

        def process_document(self, content, is_pdf, system_prompt, user_prompt, max_tokens=12288):
            raise ValueError("API key missing")

        def get_max_context_size(self):
            return 200_000

    result = generate_tags(
        "# Summary\n\nCosmology observations of galaxies.",
        _KEYWORDS,
        FailingProvider(),
        config=make_app_config(tmp_path).llm,
    )
    assert "## Tags" in result


def test_insert_section_places_generated_content_before_references() -> None:
    summary = "# Paper\n\n## Results\n\n- Result[^1]\n\n## References\n\n[^1]: \"quote\""
    result = insert_section(summary, "## Tags\n\n#JWST\n\n#Galaxies")

    assert result.index("## Tags") < result.index("## References")
    assert "## Results" in result
