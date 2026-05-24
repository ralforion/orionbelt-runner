"""Tests for the PDF report renderer.

WeasyPrint is an optional dependency — these tests skip cleanly when it (or
its underlying Pango / Cairo libraries) isn't available, so the suite stays
green for installs that don't pull in the ``pdf`` extra.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orionbelt_runner.client import ExecuteResult, ObslClient
from orionbelt_runner.runner import Runner
from orionbelt_runner.spec import (
    ObslSpec,
    QuerySpec,
    ReportSection,
    ReportSpec,
    RunSpec,
)
from tests.test_runner import FakeObslClient

# Import-time skip: if WeasyPrint (or its underlying Pango / Cairo system
# libs) isn't usable on this machine, every test in this module is
# irrelevant — skip cleanly so the suite stays green on installs that
# omitted the `pdf` extra or don't have the system libraries.
#
# The two failure modes look different: a missing Python package raises
# ImportError on import, but a present package missing its native deps
# raises OSError during a render attempt. Probe both with a 1×1 doc.
try:
    from weasyprint import HTML  # type: ignore[import-not-found]

    HTML(string="<html><body>x</body></html>").write_pdf()
except Exception as _exc:  # noqa: BLE001 — broad on purpose; we don't care why
    pytest.skip(
        f"PDF tests require WeasyPrint + its system libs (Pango / Cairo): {_exc}",
        allow_module_level=True,
    )

from orionbelt_runner.report import render_pdf  # noqa: E402 — after the skip probe


def _spec() -> ReportSpec:
    return ReportSpec(
        format="pdf",
        output="r.pdf",
        title="Smoke — {date}",
        intro="Auto-generated.",
        sections=[
            ReportSection(heading="Total", query="headline", render="value"),
            ReportSection(heading="By country", query="by_country", render="table"),
        ],
    )


def _results() -> dict[str, ExecuteResult]:
    return {
        "headline": ExecuteResult(
            sql="SELECT 1",
            dialect="postgres",
            columns=["Total"],
            rows=[[12345]],
            row_count=1,
        ),
        "by_country": ExecuteResult(
            sql="SELECT 1",
            dialect="postgres",
            columns=["Country", "Revenue"],
            rows=[["DE", 5000], ["US", 7345]],
            row_count=2,
        ),
    }


def test_render_pdf_returns_pdf_bytes() -> None:
    """A real PDF starts with the ``%PDF-`` magic and ends with ``%%EOF``."""
    data = render_pdf(_spec(), _results(), context={"date": "2026-05-04"})
    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF-"), data[:8]
    # %%EOF is the PDF end-of-file marker; trailing newline is allowed.
    assert b"%%EOF" in data[-32:]


def test_print_css_emits_expected_at_page_rule() -> None:
    """``_print_css`` is the seam between spec settings and WeasyPrint —
    it builds the ``@page { size: ... }`` declaration. Unit-testing the
    string is cheap and decisive: we trust WeasyPrint to honour what the
    CSS says, so checking the CSS proves the right paper + rotation
    reach the renderer.
    """
    from orionbelt_runner.report import _print_css

    assert "size: A4;" in _print_css("A4", "portrait")
    assert "size: A4 landscape;" in _print_css("A4", "landscape")
    assert "size: A3;" in _print_css("A3", "portrait")
    assert "size: A3 landscape;" in _print_css("A3", "landscape")


def test_render_pdf_honours_page_size_and_orientation() -> None:
    """End-to-end smoke: each of A4/A3 × portrait/landscape produces a
    valid PDF, and all four byte streams differ.

    The CSS construction is unit-tested above; this test guards against
    the wiring breaking — e.g. ``render_pdf`` forgetting to read the
    spec fields and falling through to a single default for everything.
    """
    from orionbelt_runner.spec import ReportSection, ReportSpec

    def _pdf(page_size: str, orientation: str) -> bytes:
        spec = ReportSpec(
            format="pdf",
            output="r.pdf",
            title="Geom",
            pdf_page_size=page_size,  # type: ignore[arg-type]
            pdf_orientation=orientation,  # type: ignore[arg-type]
            sections=[ReportSection(heading="By country", query="by_country", render="table")],
        )
        return render_pdf(spec, _results(), context={"date": "2026-05-04"})

    pdfs = {
        "a4_p": _pdf("A4", "portrait"),
        "a4_l": _pdf("A4", "landscape"),
        "a3_p": _pdf("A3", "portrait"),
        "a3_l": _pdf("A3", "landscape"),
    }
    for name, data in pdfs.items():
        assert data.startswith(b"%PDF-"), f"{name}: not a PDF"
        assert b"%%EOF" in data[-32:], f"{name}: missing EOF"
    # No combination is silently collapsing to the same render.
    assert len(set(pdfs.values())) == 4


def test_runner_writes_pdf_report(tmp_path: Path) -> None:
    """``report.format: pdf`` writes a binary .pdf and still emits the runlog
    as ``<stem>.run.yaml`` (not ``<stem>.pdf.run.yaml``)."""
    fake = FakeObslClient(
        {
            "headline": ExecuteResult(
                sql="SELECT 1",
                dialect="postgres",
                columns=["Total"],
                rows=[[12345]],
                row_count=1,
            ),
            "by_country": ExecuteResult(
                sql="SELECT 1",
                dialect="postgres",
                columns=["Country", "Revenue"],
                rows=[["DE", 5000]],
                row_count=1,
            ),
        }
    )
    spec = RunSpec(
        name="PdfSmoke",
        obsl=ObslSpec(base_url="http://unused"),
        queries=[
            QuerySpec(
                name="headline",
                query={"__test_name": "headline", "select": {"measures": ["Total"]}},
            ),
            QuerySpec(
                name="by_country",
                query={"__test_name": "by_country", "select": {"dimensions": ["Country"]}},
            ),
        ],
        report=ReportSpec(
            format="pdf",
            output=str(tmp_path / "report-{date}.pdf"),
            title="Pdf — {date}",
            sections=[
                ReportSection(heading="Total", query="headline", render="value"),
                ReportSection(heading="By country", query="by_country", render="table"),
            ],
        ),
    )
    runner = Runner(_as_protocol(fake))
    result = runner.run(spec)

    assert result.succeeded
    assert result.report_path is not None
    assert result.report_path.suffix == ".pdf"
    assert result.report_path.read_bytes().startswith(b"%PDF-")
    # Runlog sits alongside with .run.yaml stem (not .pdf.run.yaml).
    assert result.runlog_path is not None
    assert result.runlog_path.name.endswith(".run.yaml")
    assert result.runlog_path.stem.removesuffix(".run") == result.report_path.stem


def _as_protocol(c: FakeObslClient) -> ObslClient:
    """Same type-narrowing helper as test_runner — keeps mypy honest."""
    return c


@pytest.fixture(autouse=True)
def _quiet_logs() -> None:
    import logging

    logging.getLogger("orionbelt_runner").setLevel(logging.WARNING)
