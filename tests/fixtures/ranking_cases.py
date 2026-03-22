from __future__ import annotations

from re_ass.models import PreferenceConfig
from tests.support import make_paper


def astroph_preferences() -> PreferenceConfig:
    return PreferenceConfig(
        priorities=(
            "Little red dots",
            "Black holes and AGN",
            "Semi-analytic galaxy formation models",
        ),
        categories=("astro-ph.CO", "astro-ph.GA", "astro-ph.HE"),
        raw_text=(
            "# Arxiv Priorities\n\n"
            "## Categories\n"
            "- astro-ph.CO\n"
            "- astro-ph.GA\n"
            "- astro-ph.HE\n\n"
            "## Priorities\n"
            "1. Little red dots\n"
            "2. Black holes and AGN\n"
            "3. Semi-analytic galaxy formation models\n"
        ),
    )


def lexical_false_positive_papers():
    return [
        make_paper(
            arxiv_id="2603.41001",
            title="Flexible models of galaxy formation histories",
            summary=(
                "Generic statistical models for galaxy formation histories and calibration "
                "without any explicit physical prior."
            ),
            primary_category="astro-ph.GA",
            categories=("astro-ph.GA",),
        ),
        make_paper(
            arxiv_id="2603.41002",
            title="Semi-analytic black-hole growth in galaxy assembly",
            summary=(
                "A semi-analytic model of galaxy assembly with explicit AGN feedback "
                "and black-hole growth."
            ),
            primary_category="astro-ph.GA",
            categories=("astro-ph.GA", "astro-ph.HE"),
        ),
    ]


def hybrid_recall_papers():
    return [
        make_paper(
            arxiv_id="2603.41011",
            title="Flexible models of galaxy formation histories",
            summary=(
                "Generic statistical models for galaxy formation histories and calibration "
                "without any explicit physical prior."
            ),
            primary_category="astro-ph.GA",
            categories=("astro-ph.GA",),
        ),
        make_paper(
            arxiv_id="2603.41012",
            title="Galaxy assembly in SAMs with quasar feedback",
            summary=(
                "A SAM analysis of quasar feedback, black-hole growth, and high-redshift "
                "galaxy assembly."
            ),
            primary_category="astro-ph.GA",
            categories=("astro-ph.GA", "astro-ph.HE"),
        ),
        make_paper(
            arxiv_id="2603.41013",
            title="Weak-lensing calibration for survey masks",
            summary="Calibration systematics for weak-lensing survey masks.",
            primary_category="astro-ph.CO",
            categories=("astro-ph.CO",),
        ),
    ]


def rerank_pool_papers():
    return [
        make_paper(
            arxiv_id="2603.41021",
            title="Galaxy formation benchmark models",
            summary="Benchmark models for broad galaxy formation histories.",
            primary_category="astro-ph.GA",
            categories=("astro-ph.GA",),
        ),
        make_paper(
            arxiv_id="2603.41022",
            title="AGN feedback in semi-analytic SAM galaxy assembly",
            summary=(
                "Semi-analytic SAM predictions for AGN feedback, black-hole growth, "
                "and galaxy assembly."
            ),
            primary_category="astro-ph.GA",
            categories=("astro-ph.GA", "astro-ph.HE"),
        ),
    ]
