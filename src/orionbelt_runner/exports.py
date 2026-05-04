"""Per-query TSV export rendering.

Pure functions: ``render_tsv(result)`` returns the file body; the runner is
the only place that writes files. The TSV uses Python's ``excel-tab``
dialect with ``QUOTE_MINIMAL`` so embedded tabs / newlines / quotes are
handled the same way ``pandas.read_csv(..., sep='\\t')`` expects.

Cell values come straight from the ``ExecuteResult`` rows. Because the
runner calls OBSL with ``format_values=True``, numeric and date cells are
already locale-formatted strings — exports therefore mirror what the
report shows. ``None`` becomes an empty cell.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from orionbelt_runner.client import ExecuteResult


def render_tsv(result: ExecuteResult) -> str:
    """Render an ``ExecuteResult`` as TSV (header row + one row per result row)."""
    buf = io.StringIO()
    # Override excel-tab's default \r\n line terminator — TSVs are typically
    # consumed on unix-y stacks (pandas / awk / DuckDB) and \n is friendlier.
    writer = csv.writer(buf, dialect="excel-tab", lineterminator="\n")
    writer.writerow([c.name for c in result.columns])
    for row in result.rows:
        writer.writerow([_cell(v) for v in row])
    return buf.getvalue()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


# Filenames are derived from query names. Strip anything that isn't a safe
# path char so the runner can't accidentally write outside the exports dir
# when a spec uses an unusual query name (e.g. one containing ``/``).
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_export_filename(query_name: str) -> str:
    """Return a filesystem-safe TSV filename for a query.

    Replaces any non-``[A-Za-z0-9._-]`` run with a single underscore so that
    a query named ``orders/by_country`` never escapes the exports directory.
    Empty results after sanitisation fall back to ``query``.
    """
    sanitized = _UNSAFE_FILENAME.sub("_", query_name).strip("._-")
    return f"{sanitized or 'query'}.tsv"
