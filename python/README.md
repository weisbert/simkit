# Python code

Runs outside Virtuoso. Python 3.11.4. Phase 1 components (to be written):

- `pvtproject/` — shared `.pvtproject` parser (used by the CLI and the ingester)
- `ingester/` — JSON dump → DuckDB loader
- `pvt_cli/` — the `pvt` command-line tool (`ingest`, `attach`, `label`, `list`, `diff`)

## Dependency policy

**All deps must be offline-installable**. No `pip install` in the deploy environment (red zone). Workflow:

1. Add dep to `requirements.txt` (pinned version).
2. On a network-connected machine, `pip download -r requirements.txt -d vendor/`.
3. Carry `vendor/` wheels to the offline machine. Install with `pip install --no-index --find-links=vendor/ -r requirements.txt`.

Candidate deps for Phase 1: `duckdb`, `pyyaml`, `click` (or `typer`). Keep the list small.
