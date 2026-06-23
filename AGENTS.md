# AGENTS.md

Guidance for coding agents working in this repository.

## Project Overview

**OrionBelt Runner** runs OBSL query batches and emits reports. A run is a single YAML spec. Output today is markdown; PDF and chart embedding are planned.

This repo **does not vendor OBSL**. All access goes through the public REST API of [orionbelt-semantic-layer](https://github.com/ralforion/orionbelt-semantic-layer) via a small `ObslClient` protocol (`src/orionbelt_runner/client.py`). When OBSL changes, only the HTTP client adapter needs to follow.

## Commands

```bash
uv sync                                                 # install
uv run orionbelt-runner run examples/monthly-revenue.yaml
uv run pytest                                           # tests
uv run ruff check src/ tests/                           # lint
uv run ruff format src/ tests/                          # format
uv run mypy src/                                        # type check
```

## Architecture

```
src/orionbelt_runner/
├── __init__.py    # __version__
├── client.py      # ObslClient protocol + HttpObslClient
├── spec.py        # Pydantic models for the YAML spec + load_spec()
├── runner.py      # Runner — orchestrates query execution + report rendering
├── report.py      # Markdown rendering (table / value / list)
└── cli.py         # Typer CLI: orionbelt-runner run / version
```

## Design rules

- **The Protocol is the seam.** Anything the runner needs from OBSL goes through `ObslClient`. Tests use a fake; a future in-process client lives next to `HttpObslClient` without touching `runner.py` / `report.py` / `cli.py`.
- **Pass query bodies through unchanged.** The runner does not parse or transform OBML queries — it forwards them to OBSL and treats the result as data.
- **Spec is the public contract.** Validate with Pydantic; keep `extra="forbid"` on `RunSpec` so typos surface early.
- **Reports are pure functions.** `render_markdown(spec, results, context)` takes the spec and the materialized rows; no I/O. The `Runner` is the only place that writes files.

## Conventions

- Python 3.12+, `from __future__ import annotations` everywhere
- Pydantic v2 for all I/O models
- Ruff: `["E", "F", "I", "N", "UP", "B", "A", "SIM"]`, line-length 100
- mypy strict mode with `pydantic.mypy` plugin
- structlog for logging — JSON-friendly when piped to a log collector

## OBSL version compatibility

The runner's minor line is pinned to an OBSL minor series: **0.6.x ↔ OBSL 2.16.x**. `HttpObslClient.preflight()` calls the unauthenticated `GET /health` (which returns the OBSL release `version` and the active `auth_mode`) before any query and raises `ObslVersionError` if the server is outside the supported line, or `ObslPreflightError` if the server enforces `AUTH_MODE=api_key` but no key was configured. The CLI runs preflight automatically (skippable with `--skip-preflight`). The pin lives in `client.py` as `SUPPORTED_OBSL_MAJOR` / `SUPPORTED_OBSL_MINOR` — bump them in lockstep with the runner's minor version.

Note: `GET /v1/settings` also exposes `version` (release) plus `api_version` (the REST prefix, currently `"v1"` — *not* a semver). The runner still reads `settings()` mid-run to capture `version` / `api_version` into the run log, but the version *gate* is the `/health` preflight.

## Out of scope (for now)

- Scheduling — drive from cron / systemd / Cloud Scheduler / GitHub Actions
- PDF rendering — landing later, likely WeasyPrint
- Chart generation — landing later, likely via OrionBelt Analytics
- Multi-model session orchestration — supported via `model_id` only

When any of these arrive, keep them behind the same `ObslClient` boundary or add a sibling module — do not couple them into `runner.py` directly.
