# PatchOps

PatchOps generates reviewable Python migrations from Pydantic v1 to v2. It takes a local Git repository, a public HTTPS Git URL, or a ZIP. It records an immutable snapshot, discovers migration evidence, runs baseline checks, applies scoped edits, and compares the same checks after the migration. The final bundle includes a unified diff, edit provenance, and Markdown and JSON reports.

The workflow is available through the `patchops` CLI, a FastAPI API, and a Streamlit dashboard. A separate worker claims persisted jobs. The controller owns stage transitions, patch scope, and validation. An optional model can propose one replacement at a time within an approved source range. GitHub pull requests require explicit approval and a fully validated job.

## Local quickstart

Install Python 3.12, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/). Docker is required for validated migration runs. The scan, plan, and dry-run commands work without Docker.

```bash
uv sync --locked --python 3.12
uv run python fixtures/make_demo.py
uv run patchops scan --zip var/customer_catalog.zip
uv run patchops plan --zip var/customer_catalog.zip
uv run patchops migrate --zip var/customer_catalog.zip --dry-run
```

Each command prints a job ID. The dry-run diff is at `var/<job-id>/migration.diff`. Review it and the corresponding report.

```bash
uv run patchops report --job-id <job-id>
uv run patchops report --job-id <job-id> --json
```

For a validated run, build the trusted sandbox image from this repository, then run the fixture.

```bash
docker build -f sandbox/Dockerfile -t patchops-sandbox:0.1.0 .
uv run patchops migrate --zip var/customer_catalog.zip
```

The default run applies the deterministic rules and flags the fixture's validators and legacy model configuration for review. To opt in to scoped semantic assistance, set the model environment variables and add `--allow-semantic`. The bundled [fixture report](examples/fixture-result/report.md) and [diff](examples/fixture-result/migration.diff) show a separate verification run with three mocked model proposals. Real pytest executed all six fixture tests successfully before and after the migration in a non-root Linux namespace with network isolation. The fixture report records that execution backend.

Local Git sources use the committed revision. A local path must be under `PATCHOPS_ALLOWED_ROOTS`.

```bash
uv run patchops migrate --repo ./path-to-git-checkout --ref HEAD
uv run patchops plan --git-url https://github.com/owner/repository.git --ref main
```

## API, worker and dashboard

Run these commands in three terminals from the project directory.

```bash
uv run uvicorn patchops.api:create_app --factory --no-proxy-headers --host 127.0.0.1 --port 8000
uv run python -m patchops.worker
uv run streamlit run src/patchops/dashboard.py --server.address=127.0.0.1
```

The dashboard at http://127.0.0.1:8501 has repository intake, status, timeline, diff and provenance, validation, and report downloads. The API publishes its request and response schemas at http://127.0.0.1:8000/docs. Its health endpoint reports configured capabilities. With SQLite, the API and worker share the same local database and data directory. Keep Uvicorn proxy-header trust disabled if a reverse proxy is added.

Send a ZIP to `POST /api/v1/jobs/zip` as a raw `application/zip` body with a JSON `X-PatchOps-Request` header. Example:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs/zip \
  -H 'Content-Type: application/zip' \
  -H 'X-PatchOps-Request: {"source":{"type":"zip_upload","label":"customer_catalog.zip"},"mode":"dry-run"}' \
  --data-binary @var/customer_catalog.zip
```

For Compose, copy `.env.example` to `.env`, set a random `PATCHOPS_API_TOKEN`, build the sandbox image, and start the services.

```bash
docker build -f sandbox/Dockerfile -t patchops-sandbox:0.1.0 .
docker compose up --build
```

Compose publishes the API and dashboard on loopback and runs Alembic against PostgreSQL before starting the API and worker. The worker uses the Docker socket to create ephemeral containers. Its default local repository mount is `./fixtures` at `/repos`. For local Git jobs in Compose, set `PATCHOPS_REPOS_DIR` to the host directory holding those checkouts. Uploads also work through the dashboard.

## Configuration

| Variable | Use |
| --- | --- |
| `PATCHOPS_API_TOKEN` | Optional for direct loopback API use. Required by Compose. Set the same value in the dashboard process environment when configured locally. |
| `PATCHOPS_DATABASE_URL` | Defaults to `sqlite:///./var/patchops.db`. Use Alembic for PostgreSQL. |
| `PATCHOPS_DATA_ROOT` | Job snapshots, workspaces, and downloadable evidence. Defaults to `var`. |
| `PATCHOPS_ALLOWED_ROOTS` | JSON array of server-side local repository roots. Defaults to the process working directory. |
| `PATCHOPS_SANDBOX_IMAGE` | Trusted local image tag. PatchOps resolves it to an image ID before each execution. |
| `PATCHOPS_LLM_API_KEY`, `PATCHOPS_LLM_MODEL`, `PATCHOPS_LLM_BASE_URL` | Optional OpenAI-compatible Chat Completions provider with JSON response mode. The job must separately authorize sharing a bounded source excerpt and applying scoped edits. |
| `PATCHOPS_GITHUB_TOKEN` | Optional fine-grained GitHub token with Contents write and Pull requests write for the selected repository. |
| `PATCHOPS_JOB_SECONDS`, `PATCHOPS_COMMAND_SECONDS`, `PATCHOPS_MAX_REPAIR_ATTEMPTS`, `PATCHOPS_LLM_TOKEN_BUDGET` | Execution and repair budgets. Repairs are capped at three attempts. |

## Scope and validation limits

- Automated rewriting covers selected same-module calls to `.dict()` and `.parse_obj()`, and eligible v1 requirements and PEP 621 dependencies. JSON serialization, model settings, and validators can require semantic edits. Unsupported declarations, custom method overrides, unresolved receivers, and lockfiles are flagged where detected. See [migration rules](docs/migration_rules.md).
- The worker runs installation and checks offline from a trusted wheelhouse. Extend `sandbox/wheelhouse.in` with operator-reviewed requirements before building a custom image. The default wheelhouse contains the versions used by the fixture and the supported check tools.
- This release discovers pytest, Ruff, and mypy. Job requests can enable those tools and the documented validation options. Arbitrary command execution, automatic lockfile regeneration, and direct execution on the host are outside this implementation.
- Expired worker leases fail safely instead of replaying partially applied jobs. The store retains job artifacts for operator review. Remove an inactive job's database record and directory together when applying a retention policy.
- Treat this as a single-operator application. Use a dedicated Docker host or VM when handling untrusted repositories. Review the [threat model](docs/threat_model.md) before sharing access.
- Live Docker, PostgreSQL, a real model provider and a remote PR were unavailable in the implementation environment. The suite includes a Docker acceptance test for an environment with the image present. GitHub creation and replay were verified with mocked HTTP. The benchmark example is a dry-run comparison across 23 cases with 69 jobs. See [evaluation](docs/evaluation.md).

## Tests

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/patchops
uv run pytest -q
uv run pytest -q -m docker
```

## Project layout

| Directory | Contents |
| --- | --- |
| `src/patchops/` | API, CLI, dashboard, worker, state machine, storage, repository intake, analysis, edits, sandbox client, GitHub adapter, evaluation and reports |
| `src/patchops/migrations/` | Migration plugin protocol and Pydantic v1 → v2 implementation |
| `src/patchops/sandbox_runtime/`, `sandbox/` | Image entrypoint, offline wheelhouse and pinned check tools |
| `alembic/` | Database schema migration |
| `fixtures/`, `benchmarks/`, `tests/` | Acceptance repository, benchmark manifest and automated tests |
| `docs/`, `examples/` | Architecture, threat model, migration rules, evaluation and recorded outputs |
