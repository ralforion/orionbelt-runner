"""Markdown + HTML rendering for ExecuteResult sections.

The markdown renderer is the source of truth: ``render_html`` converts the
same markdown body to HTML and wraps it in a self-contained, styled document.
That keeps the two formats in lockstep — only the markdown shape changes,
HTML rendering follows.
"""

from __future__ import annotations

import html
import re
from typing import Any

import markdown as md_lib

from orionbelt_runner.client import ColumnMetadata, ExecuteResult
from orionbelt_runner.spec import ReportSection, ReportSpec

# Anchored regex spans (e.g. ``^[A-Z]{2}$``) inside a description trip
# markdown's link / inline-code heuristics. Wrap any such span in
# backticks so it renders as inline code instead of distorting the line.
# Conservative: only matches `^…$` chunks with no whitespace and no
# pre-existing backticks, so we never double-wrap.
_ANCHORED_REGEX = re.compile(r"(?<!`)(\^[^\s`]+\$)(?!`)")


def render_markdown(
    spec: ReportSpec,
    results: dict[str, ExecuteResult],
    context: dict[str, Any] | None = None,
) -> str:
    """Render a complete markdown report for the given spec + results."""
    ctx: dict[str, Any] = dict(context or {})
    # Result-derived counters available to title / intro / footer templates.
    # Both snake_case and camelCase forms are exposed; pick whichever fits.
    number_of_queries = len(results)
    number_of_sections = len(spec.sections)
    number_of_rows = sum(r.row_count for r in results.values())
    ctx.setdefault("number_of_queries", number_of_queries)
    ctx.setdefault("numberOfQueries", number_of_queries)
    ctx.setdefault("number_of_sections", number_of_sections)
    ctx.setdefault("numberOfSections", number_of_sections)
    ctx.setdefault("number_of_rows", number_of_rows)
    ctx.setdefault("numberOfRows", number_of_rows)

    parts: list[str] = []

    title = spec.title.format(**ctx)
    parts.append(f"# {title}\n")
    if spec.intro:
        parts.append(spec.intro.format(**ctx) + "\n")

    for section in spec.sections:
        if section.query not in results:
            parts.append(f"## {section.heading}\n\n_Missing query result: `{section.query}`_\n")
            continue
        parts.append(_render_section(section, results[section.query]))

    if spec.footer:
        parts.append(spec.footer.format(**ctx))

    return "\n".join(parts).rstrip() + "\n"


def _render_section(section: ReportSection, result: ExecuteResult) -> str:
    lines = [f"## {_safe_description(section.heading)}\n"]
    if section.description:
        lines.append(_safe_description(section.description) + "\n")

    if section.render == "table":
        lines.append(_render_table(result))
    elif section.render == "value":
        lines.append(_render_value(result, section.value_column))
    elif section.render == "list":
        lines.append(_render_list(result, section.list_column))

    return "\n".join(lines) + "\n"


def _safe_description(text: str) -> str:
    """Wrap anchored-regex-looking spans in backticks for markdown safety."""
    return _ANCHORED_REGEX.sub(r"`\1`", text)


def _render_table(result: ExecuteResult) -> str:
    if not result.columns:
        return "_No columns returned._"
    if not result.rows:
        return _table_header(result.columns) + "\n_No rows._"
    rows = [_format_row(r) for r in result.rows]
    return _table_header(result.columns) + "\n" + "\n".join(rows)


def _table_header(columns: list[ColumnMetadata]) -> str:
    """Render the GFM ``| name | name |`` / ``| ---: | --- |`` header rows.

    Right-alignment is reserved for **quantity-formatted** numeric columns —
    ``type == "number"`` AND a ``format`` pattern that contains something
    other than ``0`` / ``#`` (a separator, decimal, ``%``, or currency
    symbol). Bare-integer formats like ``"0"`` or ``"000000"`` (used for
    coded integers such as YYYYMM reporting periods or padded IDs) stay
    left-aligned alongside their text neighbours, and columns with no
    format pattern at all stay left-aligned for the same reason.

    The alignment hint propagates to HTML / PDF automatically because
    Python-Markdown's ``tables`` extension emits ``text-align: right`` on
    the rendered ``<th>`` / ``<td>`` when the separator is ``---:``.
    """
    header = "| " + " | ".join(c.name for c in columns) + " |"
    sep = "| " + " | ".join(_align_marker(c) for c in columns) + " |"
    return f"{header}\n{sep}"


# A "quantity" format contains at least one character that isn't a pure
# digit placeholder — separator, decimal, percent, or currency symbol.
# Bare patterns like "0" or "000000" match nothing here and stay left.
_QUANTITY_FORMAT = re.compile(r"[^0#]")


def _align_marker(col: ColumnMetadata) -> str:
    if col.type == "number" and col.format and _QUANTITY_FORMAT.search(col.format):
        return "---:"
    return "---"


def _format_row(row: list[Any]) -> str:
    return "| " + " | ".join(_format_cell(c) for c in row) + " |"


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _render_value(result: ExecuteResult, column: str | int | None) -> str:
    if not result.rows:
        return "_No rows._"
    idx = _resolve_column_index(result, column, prefer_numeric=True)
    cell = result.rows[0][idx] if idx is not None else result.rows[0][0]
    return f"**{_format_cell(cell)}**"


def _render_list(result: ExecuteResult, column: str | int | None) -> str:
    if not result.rows:
        return "_No rows._"
    idx = _resolve_column_index(result, column, prefer_numeric=False)
    if idx is None:
        idx = 0
    items = [f"- {_format_cell(row[idx])}" for row in result.rows]
    return "\n".join(items)


def render_html(
    spec: ReportSpec,
    results: dict[str, ExecuteResult],
    context: dict[str, Any] | None = None,
    *,
    extra_css: str = "",
) -> str:
    """Render the report as a self-contained styled HTML document.

    Pipeline: ``render_markdown`` builds the body, then Python-Markdown's
    ``tables`` extension converts pipe tables to ``<table>`` markup, and the
    result is embedded in a minimal HTML5 shell with default CSS. The shell
    is intentionally self-contained (no external assets) so the output works
    when emailed, opened from disk, or served from a static host.

    The ``<title>`` mirrors the spec's ``title`` after placeholder
    substitution, so browser tabs and PDF "Save as" dialogs land on a
    meaningful name.

    ``extra_css`` is appended after the default stylesheet so PDF rendering
    can inject ``@page`` rules without forking the template.
    """
    ctx: dict[str, Any] = dict(context or {})
    # Mirror the counters render_markdown injects so callers passing only the
    # base time/tz context still get identical title substitution behaviour.
    ctx.setdefault("number_of_queries", len(results))
    ctx.setdefault("numberOfQueries", len(results))
    ctx.setdefault("number_of_sections", len(spec.sections))
    ctx.setdefault("numberOfSections", len(spec.sections))
    ctx.setdefault("number_of_rows", sum(r.row_count for r in results.values()))
    ctx.setdefault("numberOfRows", ctx["number_of_rows"])

    body_md = render_markdown(spec, results, context=ctx)
    body_html = md_lib.markdown(body_md, extensions=["tables"], output_format="html")
    title = html.escape(spec.title.format(**ctx))
    css = _DEFAULT_CSS + extra_css
    return _HTML_TEMPLATE.format(title=title, css=css, body=body_html)


def render_pdf(
    spec: ReportSpec,
    results: dict[str, ExecuteResult],
    context: dict[str, Any] | None = None,
) -> bytes:
    """Render the report as a PDF document, returning the raw bytes.

    PDF is produced by handing the HTML render to WeasyPrint, so the layout
    stays in lockstep with ``render_html`` automatically — only the page box
    (margins, page numbers, page-break hints on ``h2``) is added on top via
    ``@page`` and print-only CSS.

    WeasyPrint is an optional dependency (it pulls in Pango / Cairo at the
    system level). Install with ``uv sync --extra pdf`` or
    ``pip install orionbelt-runner[pdf]``. The import is local so the rest
    of the package keeps working without it.
    """
    try:
        from weasyprint import HTML  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover — exercised by docs / install path
        raise RuntimeError(
            "PDF output requires WeasyPrint. Install the PDF extra with "
            "`uv sync --extra pdf` (or `pip install orionbelt-runner[pdf]`). "
            "WeasyPrint also needs Pango / Cairo system libraries — see "
            "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation"
        ) from exc

    print_css = _print_css(spec.pdf_page_size, spec.pdf_orientation)
    html_doc = render_html(spec, results, context=context, extra_css=print_css)
    # WeasyPrint is untyped, so write_pdf() comes back as Any — cast back
    # to bytes (write_pdf(target=None) is documented to return bytes).
    pdf_bytes: bytes = HTML(string=html_doc).write_pdf()
    return pdf_bytes


_DEFAULT_CSS = """\
:root { color-scheme: light dark; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "Helvetica Neue", Arial, sans-serif;
  max-width: 960px;
  margin: 2rem auto;
  padding: 0 1.25rem;
  color: #1a1a1a;
  line-height: 1.55;
}
h1 { font-size: 2rem; border-bottom: 2px solid #d0d7de; padding-bottom: .3rem; }
h2 { font-size: 1.35rem; margin-top: 2rem; border-bottom: 1px solid #eaeef2;
     padding-bottom: .2rem; }
h3 { font-size: 1.1rem; margin-top: 1.5rem; }
p { margin: .6rem 0; }
strong { color: #0b5394; }
hr { border: none; border-top: 1px solid #eaeef2; margin: 2rem 0; }
ul { padding-left: 1.4rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: .95rem;
        font-variant-numeric: tabular-nums; }
th, td { border: 1px solid #d0d7de; padding: .4rem .65rem; text-align: left;
         vertical-align: top; }
th { background: #f6f8fa; font-weight: 600; }
tbody tr:nth-child(even) td { background: #fafbfc; }
code { background: #f3f4f6; padding: 1px 5px; border-radius: 3px;
       font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
       font-size: .9em; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #e6edf3; }
  h1 { border-color: #30363d; }
  h2, hr { border-color: #21262d; }
  th { background: #161b22; }
  th, td { border-color: #30363d; }
  tbody tr:nth-child(even) td { background: #11161d; }
  code { background: #161b22; }
  strong { color: #79c0ff; }
}
"""


def _print_css(page_size: str, orientation: str) -> str:
    """Build the print-only CSS overlay for a given page size + orientation.

    Appended after ``_DEFAULT_CSS`` when rendering for PDF. WeasyPrint
    honours ``@page`` (page box / margins / page numbers) and CSS print
    rules — neither has any effect when the same HTML is viewed in a
    browser, so the on-screen HTML output is unaffected. The dark-mode
    block in ``_DEFAULT_CSS`` is neutralised here so the PDF is reliably
    light-on-white regardless of the host OS appearance setting.

    ``page_size`` is ``"A4"`` (default) or ``"A3"``; ``orientation`` is
    ``"portrait"`` (default) or ``"landscape"``. The spec validates the
    literals so callers can't pass anything else.
    """
    # CSS @page: "A4" / "A3" for portrait; append " landscape" otherwise.
    size_rule = page_size if orientation == "portrait" else f"{page_size} landscape"
    return f"""
@page {{
  size: {size_rule};
  margin: 18mm 16mm 20mm 16mm;
  @bottom-right {{
    content: counter(page) " / " counter(pages);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    font-size: 9pt;
    color: #6b7280;
  }}
}}
@media print {{
  body {{ max-width: none; margin: 0; padding: 0; background: #fff; color: #1a1a1a; }}
  h1 {{ border-color: #d0d7de; }}
  h2 {{ page-break-before: auto; page-break-after: avoid; border-color: #eaeef2; }}
  h3 {{ page-break-after: avoid; }}
  table {{ page-break-inside: auto; }}
  tr   {{ page-break-inside: avoid; page-break-after: auto; }}
  thead {{ display: table-header-group; }}
  tfoot {{ display: table-footer-group; }}
  th {{ background: #f6f8fa; }}
  tbody tr:nth-child(even) td {{ background: #fafbfc; }}
  code {{ background: #f3f4f6; }}
  strong {{ color: #0b5394; }}
}}
"""


_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def _resolve_column_index(
    result: ExecuteResult, column: str | int | None, *, prefer_numeric: bool
) -> int | None:
    if isinstance(column, int):
        return column
    if isinstance(column, str):
        for i, c in enumerate(result.columns):
            if c.name == column:
                return i
    if prefer_numeric:
        # Prefer column.type when OBSL provides it (format_values=true makes
        # cells strings, so runtime isinstance checks are unreliable). Fall
        # back to runtime sniffing for legacy callers passing untyped columns.
        for i, c in enumerate(result.columns):
            if c.type == "number":
                return i
        if result.rows:
            for i, cell in enumerate(result.rows[0]):
                if isinstance(cell, int | float) and not isinstance(cell, bool):
                    return i
    return None
