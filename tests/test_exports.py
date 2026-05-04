"""Tests for the per-query TSV exporter."""

from __future__ import annotations

from orionbelt_runner.client import ExecuteResult
from orionbelt_runner.exports import render_tsv, safe_export_filename


def test_render_tsv_writes_header_then_rows() -> None:
    result = ExecuteResult(
        sql="SELECT 1",
        dialect="postgres",
        columns=[{"name": "Country"}, {"name": "Revenue"}],
        rows=[["DE", 5000], ["US", 7345]],
        row_count=2,
    )
    text = render_tsv(result)
    lines = text.splitlines()
    assert lines[0] == "Country\tRevenue"
    assert lines[1] == "DE\t5000"
    assert lines[2] == "US\t7345"
    # \n line terminator (no \r\n) so unix consumers don't see stray \r.
    assert "\r" not in text


def test_render_tsv_quotes_cells_with_embedded_tabs() -> None:
    """Embedded tabs / newlines must be quoted so the row count stays correct."""
    result = ExecuteResult(
        sql="SELECT 1",
        dialect="postgres",
        columns=[{"name": "Note"}, {"name": "Value"}],
        rows=[["has\ttab", 1], ["has\nnewline", 2], ['has"quote', 3]],
        row_count=3,
    )
    text = render_tsv(result)
    # Each input row should still produce exactly one logical TSV record —
    # csv.writer wraps cells with embedded delimiters/newlines/quotes in
    # double-quotes (and escapes inner quotes).
    assert '"has\ttab"\t1' in text
    assert '"has\nnewline"' in text
    assert '"has""quote"\t3' in text


def test_render_tsv_renders_none_as_empty_cell() -> None:
    result = ExecuteResult(
        sql="SELECT 1",
        dialect="postgres",
        columns=[{"name": "A"}, {"name": "B"}],
        rows=[[None, "x"], ["y", None]],
        row_count=2,
    )
    text = render_tsv(result)
    assert "\tx" in text  # empty leading cell
    assert "y\t\n" in text  # empty trailing cell, line ends after the tab


def test_render_tsv_empty_rows_still_emits_header() -> None:
    """Zero rows must still produce a single header line — downstream tooling
    expects at least the column names so it can detect schema."""
    result = ExecuteResult(
        sql="SELECT 1",
        dialect="postgres",
        columns=[{"name": "X"}, {"name": "Y"}],
        rows=[],
        row_count=0,
    )
    text = render_tsv(result)
    assert text.splitlines() == ["X\tY"]


def test_safe_export_filename_strips_unsafe_chars() -> None:
    # Path separators and weird chars must not let exports escape the dir.
    assert safe_export_filename("orders/by_country") == "orders_by_country.tsv"
    assert safe_export_filename("../etc/passwd") == "etc_passwd.tsv"
    assert safe_export_filename("name with spaces") == "name_with_spaces.tsv"
    # Already-safe names pass through.
    assert safe_export_filename("total_revenue") == "total_revenue.tsv"
    # Pathological input (only unsafe chars) gets a sentinel name rather
    # than silently writing to ``.tsv``.
    assert safe_export_filename("///") == "query.tsv"
