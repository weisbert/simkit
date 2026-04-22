# Tests

- Unit tests for Python modules (`pvtproject`, `ingester`, `pvt_cli`)
- Fixture JSON dumps for ingester tests
- End-to-end validation scripts (require Cadence; run manually from dev env)

Framework: stdlib `unittest` (the red-zone target has no pytest and stdlib keeps us dependency-free). Keep tests offline-runnable where possible — reserve Cadence-dependent tests for explicit end-to-end validation steps in `TODO.md` section 6.

## Run

From the repo root:

```
PYTHONPATH=python python3.11 -m unittest discover -s tests -v
```

Or a single module:

```
PYTHONPATH=python python3.11 -m unittest tests.test_project_loader -v
```
