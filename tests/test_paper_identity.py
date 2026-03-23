from re_ass.paper_identity import derive_identity, render_link
from tests.support import make_paper


def test_derive_identity_uses_versionless_arxiv_id_and_canonical_filename() -> None:
    paper = make_paper(
        arxiv_id="2603.15732v2",
        title="Field-Level Inference from Galaxies: BAO Reconstruction",
        authors=("Marius Bayer", "Jane Doe"),
    )

    identity = derive_identity(paper)

    assert identity.paper_key == "arxiv:2603.15732"
    assert identity.source_id == "2603.15732"
    assert identity.filename_stem == "Bayer et al - 2026 - Field-Level Inference from Galaxies BAO Reconstruction [arXiv 2603.15732]"
    assert identity.note_filename.endswith(".md")
    assert identity.pdf_filename.endswith(".pdf")


def test_derive_identity_sanitizes_invalid_filename_characters() -> None:
    paper = make_paper(
        arxiv_id="2603.20001",
        title='A "Quoted" [Title] / With: Invalid*Chars?',
        authors=("Jane Doe",),
    )

    identity = derive_identity(paper)

    assert identity.filename_stem == "Doe - 2026 - A Quoted Title With Invalid Chars [arXiv 2603.20001]"


def test_render_link_supports_wikilink_and_markdown() -> None:
    filename_stem = "Doe - 2026 - Example Paper [arXiv 2603.20002]"

    assert render_link(filename_stem, "Example Paper", style="wikilink") == f"[[{filename_stem}|Example Paper]]"
    assert render_link(filename_stem, "Example Paper", style="markdown", from_subdir="daily") == (
        "[Example Paper](../summaries/Doe%20-%202026%20-%20Example%20Paper%20%5BarXiv%202603.20002%5D.md)"
    )


def test_canonical_filename_prevents_collisions_for_same_title() -> None:
    first = derive_identity(make_paper(arxiv_id="2603.20010", title="Same Title"))
    second = derive_identity(make_paper(arxiv_id="2603.20011", title="Same Title"))

    assert first.filename_stem != second.filename_stem
