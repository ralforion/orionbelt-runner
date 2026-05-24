"""Tests for the HTML report renderer."""

from __future__ import annotations

from orionbelt_runner.client import ColumnMetadata, ExecuteResult
from orionbelt_runner.report import render_html, render_markdown
from orionbelt_runner.spec import ReportSection, ReportSpec


def _spec() -> ReportSpec:
    return ReportSpec(
        format="html",
        output="r.html",
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


def test_render_html_emits_self_contained_doc() -> None:
    html = render_html(_spec(), _results(), context={"date": "2026-05-04"})
    # HTML5 shell.
    assert html.startswith("<!doctype html>")
    assert "</html>" in html.rstrip()
    # Title is substituted from spec.title with the date placeholder.
    assert "<title>Smoke — 2026-05-04</title>" in html
    # CSS travels inline so the file is portable (no external assets).
    assert "<style>" in html
    assert "table { border-collapse: collapse" in html
    # Markdown body conversion: heading + table tags exist.
    assert "<h1>" in html
    assert "<h2>" in html
    assert "<table>" in html
    assert "<th>Country</th>" in html
    assert "<td>DE</td>" in html


def test_formatted_numeric_columns_right_align_in_markdown_and_html() -> None:
    """Only **formatted** numeric columns right-align.

    The distinction matters because OBSL returns bare integer IDs (Order
    Key, Customer ID) as ``type == "number"`` but without a format pattern
    — those should stay left-aligned alongside their text neighbours. A
    "true" measure (Revenue with format ``#,##0.00``) gets the right-align
    treatment via GFM ``---:`` syntax, which Python-Markdown's tables
    extension turns into ``style="text-align: right"``.
    """
    spec = ReportSpec(
        format="html",
        output="r.html",
        title="Align",
        sections=[ReportSection(heading="Mix", query="mix", render="table")],
    )
    results = {
        "mix": ExecuteResult(
            sql="SELECT 1",
            dialect="postgres",
            columns=[
                ColumnMetadata(name="Country", type="string"),
                # Bare numeric ID — no format → must NOT right-align.
                ColumnMetadata(name="Order Key", type="number"),
                # Formatted measure → MUST right-align.
                ColumnMetadata(name="Revenue", type="number", format="#,##0.00"),
            ],
            rows=[
                ["DE", "52965", "5.000,00"],
                ["US", "29158", "7.345,00"],
            ],
            row_count=2,
        ),
    }

    md = render_markdown(spec, results)
    # Country (string) and Order Key (unformatted number) stay left;
    # Revenue (formatted number) gets the GFM right-align marker.
    assert "| --- | --- | ---: |" in md, md

    html = render_html(spec, results)
    # String column has no inline style.
    assert "<th>Country</th>" in html
    # Unformatted numeric column ALSO has no inline style — this is the
    # behaviour the user asked for ("number-as-text IDs stay left").
    assert "<th>Order Key</th>" in html
    # Formatted numeric column IS right-aligned.
    assert '<th style="text-align: right;">Revenue</th>' in html


def test_render_html_escapes_head_title() -> None:
    """The ``<title>`` element is markup we author, so it must be HTML-escaped.

    The body ``<h1>`` follows Python-Markdown's normal pass-through behaviour
    — same trust model as the markdown renderer, which doesn't sanitize
    spec.title either. Specs are YAML written by the operator, not user input.
    """
    spec = ReportSpec(
        format="html",
        output="r.html",
        title='Q1 "Revenue" & <growth> — {date}',
    )
    html = render_html(spec, {}, context={"date": "2026-05-04"})
    assert (
        "<title>Q1 &quot;Revenue&quot; &amp; &lt;growth&gt; — 2026-05-04</title>" in html
    )
