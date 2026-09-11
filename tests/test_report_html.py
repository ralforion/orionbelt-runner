"""Tests for the HTML report renderer."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Literal

import pytest

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
    """Only **quantity-formatted** numeric columns right-align.

    The distinction matters because OBSL returns bare integer IDs (Order
    Key, Customer ID) as ``type == "number"`` with no format pattern, and
    coded integers like YYYYMM reporting periods carry a bare ``"0"`` /
    ``"000000"`` format — neither is a measure, both should stay left.
    Only quantity formats (containing a separator, decimal, ``%``, or
    currency symbol) trigger the GFM ``---:`` syntax, which Python-Markdown's
    tables extension turns into ``style="text-align: right"``.
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
                # Coded integer (YYYYMM period) with a bare-integer format
                # → must NOT right-align.
                ColumnMetadata(name="Reporting Period", type="number", format="0"),
                # Formatted measure → MUST right-align.
                ColumnMetadata(name="Revenue", type="number", format="#,##0.00"),
            ],
            rows=[
                ["DE", "52965", "202604", "5.000,00"],
                ["US", "29158", "202604", "7.345,00"],
            ],
            row_count=2,
        ),
    }

    md = render_markdown(spec, results)
    # Country (string), Order Key (unformatted number), and Reporting Period
    # (bare-integer format) stay left; Revenue (quantity format) right-aligns.
    assert "| --- | --- | --- | ---: |" in md, md

    html = render_html(spec, results)
    # String column has no inline style.
    assert "<th>Country</th>" in html
    # Unformatted numeric column ALSO has no inline style — this is the
    # behaviour the user asked for ("number-as-text IDs stay left").
    assert "<th>Order Key</th>" in html
    # Coded-integer numeric column stays left too (the YYYYMM period fix).
    assert "<th>Reporting Period</th>" in html
    # Quantity-formatted numeric column IS right-aligned.
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
    assert "<title>Q1 &quot;Revenue&quot; &amp; &lt;growth&gt; — 2026-05-04</title>" in html


class _Body(HTMLParser):
    """Collect the tags in ``<body>`` and the text of each cell, value and item."""

    _TEXT_TAGS = frozenset({"th", "td", "strong", "li"})

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.texts: dict[str, list[str]] = {t: [] for t in self._TEXT_TAGS}
        self._in_body = False
        self._open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self._in_body = True
        elif self._in_body:
            self.tags.append(tag)
        if tag in self._TEXT_TAGS:
            self._open.append(tag)
            self.texts[tag].append("")

    def handle_endtag(self, tag: str) -> None:
        if self._open and self._open[-1] == tag:
            self._open.pop()

    def handle_data(self, data: str) -> None:
        if self._open:
            self.texts[self._open[-1]][-1] += data


# Values a warehouse can hold that markdown would otherwise read as markup.
HOSTILE_VALUES = [
    # HTML, and the ways to put a URL in front of the browser or WeasyPrint.
    "<script>alert(1)</script>",
    '<img src="http://169.254.169.254/latest/meta-data/">',
    "<http://169.254.169.254/>",
    "![x](http://169.254.169.254/latest/meta-data/)",
    "[click](javascript:alert(1))",
    # Pre-escaped brackets: without escaping the backslash as well, each
    # inserted escape would pair with the existing one and re-open the link.
    "\\[click\\](javascript:alert(1))",
    # Inline syntax, live anywhere in a value.
    "*em*",
    "__bold__",
    "snake_case_name",
    "`<b>not code</b>`",
    "R&D &amp; &lt;b&gt; &#60;",
    "a | b \\| c",
    # Block syntax, live where a value starts a line, as a list item does.
    "# heading",
    "#heading",
    "> quote",
    "---",
    "- - -",
    "***",
    "___",
    "1. Quartal",
    "- item",
    "+ item",
    "* item",
    "    indented",
]

Render = Literal["table", "value", "list"]


def _render_as(render: Render, value: Any) -> _Body:
    """Render ``value`` as the only column name and cell of a one-section report."""
    spec = ReportSpec(
        format="html",
        output="r.html",
        title="Hostile",
        sections=[ReportSection(heading="Section", query="q", render=render)],
    )
    results = {
        "q": ExecuteResult(
            sql="SELECT 1",
            dialect="postgres",
            columns=[ColumnMetadata(name=str(value), type="string")],
            rows=[[value]],
            row_count=1,
        ),
    }
    body = _Body()
    body.feed(render_html(spec, results))
    return body


@pytest.mark.parametrize("render", ["table", "value", "list"])
@pytest.mark.parametrize("value", HOSTILE_VALUES)
def test_query_values_render_as_text_not_markup(value: str, render: Render) -> None:
    """A cell, value or list item shows its data verbatim and adds no markup.

    Query results are warehouse data, so anyone who can write a row can put
    text in a report — unlike the spec, which the operator writes. Comparing
    against a plain value's elements, rather than an allow-list, also catches
    markup built from allowed tags, such as a list nested inside a list item.
    """
    body = _render_as(render, value)

    assert body.tags == _render_as(render, "plain").tags
    shown = value.strip()  # surrounding whitespace is collapsed, as HTML would
    if render == "table":
        assert body.texts["th"] == [shown]  # column names are data too
        assert body.texts["td"] == [shown]
    elif render == "value":
        assert body.texts["strong"] == [shown]
    else:
        assert body.texts["li"] == [shown]


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_value_renders_nothing_rather_than_a_rule(value: str | None) -> None:
    """``**`` around nothing is ``****``, which markdown draws as a rule."""
    assert _render_as("value", value).tags == ["h1", "h2"]


def test_markdown_report_escapes_markup_and_leaves_plain_values_alone() -> None:
    """The .md file is rendered by other viewers too, so it must carry no live
    markup — and escaping only what would act as markup keeps it readable."""
    plain = ["-5,00", "+49 30", "R&D", "2026-04-29", "5.000,50", "#1"]
    spec = ReportSpec(
        output="r.md",
        title="Hostile",
        sections=[ReportSection(heading="List", query="q", render="list")],
    )
    results = {
        "q": ExecuteResult(
            sql="SELECT 1",
            dialect="postgres",
            columns=["Name"],
            rows=[["<script>x</script> ![x](http://example.test/) *em*"], ["1. Quartal"]]
            + [[v] for v in plain],
            row_count=2 + len(plain),
        ),
    }

    md = render_markdown(spec, results)

    assert "- &lt;script>x&lt;/script> !\\[x\\](http://example.test/) \\*em\\*\n" in md
    assert "- 1\\. Quartal\n" in md
    for value in plain[:-1]:
        assert f"- {value}\n" in md
    # A leading "#" is always a heading marker in Python-Markdown, even unspaced.
    assert "- \\#1\n" in md
