# Python code

Runs outside Virtuoso. Python 3.11.4. Phase 1 components:

- `simkit/project.py` — `.pvtproject` loader (walker + JSON parser + env fallback). **Built.**
- `simkit/ingester/` — JSON dump → DuckDB loader. (TODO)
- `simkit/cli/` — the `pvt` command-line tool (`ingest`, `attach`, `label`, `list`, `diff`). (TODO)

## Dependency policy

**All deps must be offline-installable**. No `pip install` in the deploy environment (red zone). Workflow:

1. Add dep to `requirements.txt` (pinned version).
2. On a network-connected machine, `pip download -r requirements.txt -d vendor/`.
3. Carry `vendor/` wheels to the offline machine. Install with `pip install --no-index --find-links=vendor/ -r requirements.txt`.

Candidate deps for Phase 1: `duckdb`, `click` (or `typer`). `.pvtproject` uses JSON (stdlib) per Decision #13, so no YAML dep. Keep the list small.
