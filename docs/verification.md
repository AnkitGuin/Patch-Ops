# Release verification

Verified on 16 November 2026 with Python 3.12.14 and the locked controller dependencies.

| Check | Observed result |
| --- | --- |
| Ruff lint and format | Both passed |
| mypy | Passed for all 25 source files |
| PatchOps pytest suite | 56 passed, 1 skipped. The Docker acceptance test skipped because the Docker CLI was unavailable. |
| Fixture behavior | Six pytest tests passed with Pydantic 1.10.22 and six with 2.11.10. The trusted runner used a local wheelhouse and Linux user, network, and PID namespaces. A fake model provider supplied three semantic edits. |
| Patch integrity | `git apply --check` passed for both the deterministic and model-assisted catalog diffs. The suite also applied a diff containing CRLF and an embedded form feed and compared the resulting bytes. |
| CLI | Fixture ZIP creation, scan, plan, dry-run, and report retrieval passed. Full migration reported the unavailable Docker CLI and exited with status 2. |
| API and Streamlit | A running FastAPI server accepted an authenticated ZIP upload, a separate worker processed it, and Streamlit's test harness rendered the resulting diff and validation warning. |
| Database schema | Alembic offline SQL generation, SQLite upgrade, model/schema check, and downgrade passed. |
| GitHub | The mocked HTTP integration exercised PR creation and repeat-request behavior. |
| Benchmark | All 23 cases ran in each of the three dry-run strategies, producing 69 reports. |

Live Docker, PostgreSQL Compose, a real model provider, remote Git retrieval, and outbound GitHub PR creation remain unverified in this run. The package includes commands for the Docker check and examples of the fixture and benchmark outputs.
