# Verification on 17 November 2026

Checked the supplied project on Windows using Python 3.12.0 and dependencies installed with `uv sync --locked --python 3.12`.

The tested local workflows work after the fixes below. These checks do not establish that every possible execution is free of defects.

## Fixes

- ZIP upload cleanup now closes the temporary file before deleting it, including when the upload exceeds the size limit. On Windows, deleting the open file could mask the intended HTTP 413 response with a permission error. Added a regression test that verifies rejection, cleanup, and absence of a queued job.
- Process handling uses platform guards understood by mypy and stream annotations compatible with Windows temporary files. The sandbox runner retains process-group cleanup on Linux and uses process termination on Windows.
- The patch integrity test writes the diff as bytes, preserving the LF markers required by Git for files without a trailing newline.
- Git and Docker exclusions cover environment override files and generated build outputs, while retaining `.env.example`.

## Results

| Check | Result |
| --- | --- |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `mypy src/patchops` | Passed, 25 source files |
| `mypy --platform linux src/patchops` | Passed; static Linux analysis, not Linux execution |
| `python -m pytest -q` | 57 passed, 1 skipped |
| `python -m compileall -q src tests alembic fixtures` | Passed |
| `uv pip check` | All installed dependencies compatible |
| `pip-audit --path .venv/Lib/site-packages --progress-spinner off` | No known vulnerabilities found; the local PatchOps package has no PyPI audit entry |
| `uv build --offline` | Source distribution and universal Python wheel built successfully |
| CLI fixture creation, scan, plan, dry-run, Markdown/JSON reports | Passed |
| Generated fixture diff | `git apply --check` passed against its snapshot |
| Full migration without Docker | Correctly reported the missing Docker executable and exited with status 2 |
| Benchmark dry-run | All 23 cases completed for all 3 strategies, producing 69 review reports |
| Live API and separate worker | Authenticated ZIP upload, job processing, diff and report downloads passed |
| Dashboard with live API | Streamlit test harness rendered the overview and completed job's diff without an app exception |
| Alembic with SQLite | Upgrade, model/schema consistency check, and downgrade passed |
| Alembic PostgreSQL offline SQL | Generation passed; no live PostgreSQL connection tested |
| GitHub adapter | Mocked PR creation and repeat-request test passed |

The suite emits one upstream Starlette warning about its HTTPX test-client integration. It does not fail the checks.

## Remaining verification limits

Docker is not installed in this environment, so the Docker acceptance test skipped. The sandbox image build, actual isolated before/after migration validation, and PostgreSQL Compose deployment require an environment with Docker. Live model-provider requests and the application's outbound GitHub PR creation were not exercised. Windows runtime checks and Linux static checks do not establish behavior on every supported Python version or operating system.

Local smoke-test logs and generated artifacts are under `var/verification/`, excluded from Git. The earlier release record in `verification.md` describes a separate run and is retained unchanged.
