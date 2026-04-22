# Tests

- Unit tests for Python modules (`pvtproject`, `ingester`, `pvt_cli`)
- Fixture JSON dumps for ingester tests
- End-to-end validation scripts (require Cadence; run manually from dev env)

Framework: `pytest`. Keep tests offline-runnable where possible — reserve Cadence-dependent tests for explicit end-to-end validation steps in `TODO.md` section 6.
