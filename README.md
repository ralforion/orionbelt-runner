# OrionBelt Runner

Run [OrionBelt Semantic Layer](https://github.com/ralfbecher/orionbelt-semantic-layer) query batches and emit reports.

A run is a YAML document combining:

- An **OBSL endpoint** (base URL, optional auth, optional locale/timezone, optional model to load)
- A list of **named queries** — any valid OBML query body
- A **report config** — markdown, HTML, or PDF output with sections bound to queries

Numeric and timestamp cells are pre-rendered server-side using each column's `format` pattern from the OBML model (the runner sends `format_values=true` on every query), so reports show e.g. `1.853.429,67` for `locale: de` without any client-side formatting. See [`examples/monthly-revenue-2026-04-29.md`](examples/monthly-revenue-2026-04-29.md) (markdown), [`examples/monthly-revenue-2026-04-29.html`](examples/monthly-revenue-2026-04-29.html) (HTML), and [`examples/monthly-revenue-2026-04-29.pdf`](examples/monthly-revenue-2026-04-29.pdf) (PDF) for sample outputs.

## Status

Early scaffold (v0.4.0). Markdown, HTML, and PDF reports, with optional per-query TSV exports and an always-on YAML run log sidecar. No scheduler yet — drive it from cron / systemd / GitHub Actions / Cloud Scheduler / etc.

## Install

```bash
uv sync                  # core: markdown + HTML reports
uv sync --extra pdf      # also enable PDF output (requires Pango / Cairo)
```

PDF output needs WeasyPrint, which depends on system libraries (Pango,
Cairo, GDK-Pixbuf). On macOS: `brew install pango`. On Debian / Ubuntu:
`apt install libpango-1.0-0 libpangoft2-1.0-0`. See the
[WeasyPrint install guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation)
for other platforms. Skip the extra if you only need markdown / HTML.

> **Apple Silicon note:** Homebrew installs libraries to `/opt/homebrew/lib`,
> which Python's loader doesn't search by default. If WeasyPrint can't find
> `libgobject-2.0-0`, prefix the runner with:
>
> ```bash
> DYLD_LIBRARY_PATH=/opt/homebrew/lib:/usr/local/lib \
> DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:/usr/local/lib \
> uv run orionbelt-runner run spec.yaml
> ```
>
> `DYLD_FALLBACK_LIBRARY_PATH` is often enough, but setting both variables
> covers Python/cffi environments that call `dlopen()` with bare library
> names such as `libgobject-2.0-0`.

## Run

```bash
uv run orionbelt-runner run examples/monthly-revenue.yaml
```

Override the OBSL endpoint without editing the spec:

```bash
uv run orionbelt-runner run examples/monthly-revenue.yaml \
  --base-url http://my-obsl:8080
```

Or via env (`.env` or shell):

```bash
OBSL_BASE_URL=http://my-obsl:8080 \
OBSL_API_TOKEN=... \
uv run orionbelt-runner run examples/monthly-revenue.yaml
```

## Server expectations

The runner calls **`/v1/query/execute`**, so OBSL needs to be configured to execute queries (not just compile them):

- `QUERY_EXECUTE=true` (or `FLIGHT_ENABLED=true`)
- DB driver credentials configured for the dialect(s) you query

Three deployment shapes are supported, in order of preference:

1. **Single-model mode** (`MODEL_FILE=...` on the server). Spec leaves `obsl.model` and `obsl.model_id` unset; the runner uses top-level shortcut endpoints.
2. **Multi-model server** with a model already loaded. Set `obsl.model_id` in the spec; the runner still uses shortcut endpoints and keys into the named model.
3. **Runner-loaded model**. Set `obsl.model.yaml_path` in the spec — the runner creates a session, posts the model to `/v1/sessions/{id}/models`, runs queries against `/v1/sessions/{id}/query/execute`, and deletes the session in a `finally` block. Useful for ad-hoc reports against a model you keep next to the spec file.

## Spec format

See [`examples/monthly-revenue.yaml`](examples/monthly-revenue.yaml) for a full spec.

```yaml
name: Monthly Revenue
obsl:
  base_url: http://localhost:8080
  locale: de                       # optional — BCP-47, drives display formatting
  # timezone: Europe/Berlin        # optional — IANA TZ
  # model_id: sales                # multi-model server with a pre-loaded model
  # model:                         # OR: load your own model into a fresh session
  #   yaml_path: ./sales.obml.yaml # path is resolved relative to this spec file
  #   extends: [./fragments/dim-time.yaml]
queries:
  - name: total_revenue
    dialect: postgres
    query:
      select:
        measures: [Total Revenue]
report:
  format: markdown
  output: reports/{name}-{date}.md
  title: "Monthly Revenue — {date}"
  sections:
    - heading: Headline number
      query: total_revenue
      render: value
```

**Section render modes:**

| `render` | Output |
|---|---|
| `table` | Table of all rows |
| `value` | Single bold value (first numeric column of first row by default) |
| `list`  | Bullet list of one column |

**Path placeholders** are accepted in `report.output`, `report.title`, `report.intro`, and `report.footer`. The instant comes from OBSL's `GET /v1/settings` (server clock + effective TZ); falls back to the runner's UTC clock when settings is unreachable.

| Placeholder | Example | Notes |
|---|---|---|
| `{name}` | `Monthly Revenue` | the spec name |
| `{date}` | `2026-04-29` | YYYY-MM-DD in the resolved TZ |
| `{datetime}` | `2026-04-29T18-02-06Z` | filesystem-safe; trailing `Z` only when TZ is UTC |
| `{time}` | `18:02:06` | colons are unsafe on Windows paths — use `{time_filename}` |
| `{time_filename}` | `18_02_06` | filesystem-safe |
| `{tz}` | `Europe/Berlin` | IANA name |
| `{tz_filename}` / `{timezone}` | `Europe, Berlin` | `/` replaced with `, ` for path safety |
| `{runner_version}` | `0.4.0` | the OrionBelt Runner version that produced this report |

`report.footer` additionally accepts result-derived counters: `{number_of_queries}`, `{number_of_sections}`, `{number_of_rows}` (camelCase aliases — `{numberOfQueries}` etc. — also work).

### Queries from a folder

For larger query libraries, point at a directory instead of (or in addition to) inline `queries:`:

```yaml
queries_dir: ./queries        # recursive *.yaml + *.yml, alpha-sorted
queries:                      # optional, runs after dir queries in spec order
  - name: ad_hoc
    query: { ... }
```

Each file is a full `QuerySpec` (`name`, `dialect`, `query`, optional `description`). When `name:` is omitted the filename stem is used verbatim — `total-revenue.yaml` → `total-revenue`. Duplicate names across the dir + inline list raise an error at load time. Paths resolve relative to the spec file.

## Outputs

A run produces up to three artefacts in the report directory:

```
reports/monthly-revenue-2026-04-29.md           ← report
reports/monthly-revenue-2026-04-29.run.yaml     ← run log (always)
reports/monthly-revenue-2026-04-29_exports/     ← TSV exports (opt-in)
  ├── total_revenue.tsv
  ├── revenue_by_nation.tsv
  └── top_orders_raw.tsv
```

### Report — markdown, HTML, or PDF

`format: markdown` (default) writes a `.md`. `format: html` writes a self-contained HTML5 document with inline default CSS and a light/dark theme — no external assets, so the file works when emailed, opened from disk, or served from a static host. The output extension follows the template you set in `output:`.

```yaml
report:
  format: html
  output: reports/{name}-{date}.html
  title: "Monthly Revenue — {date}"
```

See [`examples/monthly-revenue-2026-04-29.html`](examples/monthly-revenue-2026-04-29.html) for a rendered HTML sample.

`format: pdf` reuses the same HTML pipeline and runs the result through [WeasyPrint](https://weasyprint.org/) — so PDF layout stays automatically in lockstep with the HTML output. A print-only stylesheet adds page margins and a `page n / total` footer; section headings (`h2`) get a `page-break-before: auto` / `page-break-after: avoid` hint so tables don't get orphaned.

```yaml
report:
  format: pdf
  output: reports/{name}-{date}.pdf
  title: "Monthly Revenue — {date}"
  pdf_page_size: A4         # "A4" (default) or "A3"
  pdf_orientation: portrait # "portrait" (default) or "landscape"
```

`pdf_page_size` and `pdf_orientation` are ignored for `markdown` / `html` output. Reach for **A3** or **landscape** when a table has many columns or wide cell values that wrap awkwardly in A4 portrait — the same content, just more horizontal room.

PDF requires the optional `pdf` extra (`uv sync --extra pdf`) and WeasyPrint's [system libraries](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation) — see [Install](#install). See [`examples/monthly-revenue-2026-04-29.pdf`](examples/monthly-revenue-2026-04-29.pdf) for a rendered PDF sample.

### Run log (YAML sidecar) — always written

Always emitted next to the report at `<report-stem>.run.yaml`, even when queries fail (when it's most useful). Captures, for each query: the compiled SQL as a literal block scalar, the OBSL `explain` plan (planner + reasons + joins + CFL legs), the `resolved` info (fact tables / dimensions / measures), wall-clock and server-side timing, warnings, and any errors. The file header records the spec name, OBSL `version` / `api_version`, session/model IDs, and the report path.

YAML so it's both human-skimmable and trivially machine-parseable in one step. Useful for debugging, audit trails, and downstream tooling that wants the SQL or the plan.

See [`examples/monthly-revenue-2026-04-29.run.yaml`](examples/monthly-revenue-2026-04-29.run.yaml).

### Per-query TSV exports — opt-in

Set `report.export_results: true` to write each query's rows as TSV into a sibling `<report-stem>_exports/` directory:

```yaml
report:
  output: reports/{name}-{date}.md
  export_results: true
```

One file per query, named after the query and sanitised to safe path chars. TSV uses `\t` separator and `\n` line endings; cells with embedded tabs / newlines / quotes are quoted (compatible with `pandas.read_csv(..., sep='\t')`). Cells reflect the same `format_values=true` data the report shows. Exports are only written on a fully successful run.

See [`examples/monthly-revenue-2026-04-29_exports/`](examples/monthly-revenue-2026-04-29_exports/).

## Architecture

The runner talks to OBSL through a small `ObslClient` Protocol. One implementation today (HTTP). Tests can drop in a fake; an in-process implementation can be added later without touching the runner, report, or CLI code.

```
spec.yaml ──▶ load_spec ──▶ Runner ──▶ ObslClient ──▶ OBSL (HTTP)
                              │
                              ├─▶ render_markdown / render_html / render_pdf ──▶ report.md|html|pdf
                              ├─▶ render_runlog                 ──▶ report.run.yaml
                              └─▶ render_tsv (× N)              ──▶ report_exports/*.tsv
```

## License

BSL-1.1 (mirrors OrionBelt Semantic Layer).
