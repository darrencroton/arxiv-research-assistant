from pathlib import Path

from re_ass.paper_summariser.providers.base import Provider
from re_ass.paper_summariser.service import (
    PaperSummariser,
    ProjectKnowledge,
    SourceMetadata,
    create_system_prompt,
    create_user_prompt,
    fit_prompt_to_provider_budget,
    normalise_extracted_text,
    read_project_knowledge,
)
from tests.support import make_paper, make_app_config


class RecordingProvider(Provider):
    def setup(self):
        self.calls: list[dict[str, object]] = []
        self._supports_direct_pdf = bool(self.config.get("supports_direct_pdf", False))
        self.response = str(self.config["response"])

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
        return self.response

    def get_max_context_size(self):
        return 200_000


def test_summarise_source_uses_extracted_text(tmp_path: Path) -> None:
    provider = RecordingProvider(
        {
            "response": (
                "# Agents for Research\n\n"
                "Authors: Doe J., Smith J.\n"
                "Published: March 2026 ([Link](https://arxiv.org/abs/1234.5678))\n\n"
                "## Key Ideas\n"
                "- Important point[^1]\n\n"
                "## References\n"
                '[^1]: "Quoted support" (Abstract, p.1)\n'
            )
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
    assert provider.calls[0]["content"] == "arXiv: 1234.5678\nExtracted paper text."
    assert "Canonical paper link: https://arxiv.org/abs/1234.5678" in str(provider.calls[0]["user_prompt"])


def test_summarise_source_uses_direct_pdf_when_provider_supports_it(tmp_path: Path) -> None:
    provider = RecordingProvider(
        {
            "supports_direct_pdf": True,
            "response": (
                "# Agents for Research\n\n"
                "Authors: Doe J., Smith J.\n"
                "Published: March 2026 ([Link](https://arxiv.org/abs/1234.5678))\n\n"
                "## Key Ideas\n"
                "- Important point[^1]\n"
            ),
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
            "response": (
                "# Agents for Research\n\n"
                "Authors: Doe J.\n"
                "Published: March 2026 ([Link](https://arxiv.org/abs/1234.5678))\n\n"
                "## Key Ideas\n"
                "- Important point[^1]\n"
            ),
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
    assert "$KEYWORDS" in project_knowledge.system_prompt_template
    assert "$SUMMARY_TEMPLATE" in project_knowledge.user_prompt_template


def test_create_prompt_uses_external_prompt_templates() -> None:
    system_prompt = create_system_prompt("keyword list", "system $KEYWORDS")
    user_prompt = create_user_prompt(
        "body text",
        "summary template",
        "user $SUMMARY_TEMPLATE $SOURCE_METADATA_BLOCK$PAPER_INPUT_BLOCK",
        source_metadata=SourceMetadata(
            source_type="arxiv",
            identifier="2312.5678",
            canonical_url="https://arxiv.org/abs/2312.5678",
            published_label="December 2023",
        ),
    )

    assert system_prompt == "system keyword list"
    assert "summary template" in user_prompt
    assert "Published line date: December 2023" in user_prompt
    assert "---BEGIN PAPER---" in user_prompt
