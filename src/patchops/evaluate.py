import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from tempfile import NamedTemporaryFile
from typing import Any, Literal
from zipfile import ZipFile

from pydantic import Field

from .cli import await_job, queue
from .config import Settings
from .models import Model, Request, State
from .security import relative
from .store import Store


class Case(Model):
    id: str
    fixture: str | None = None
    source: str | None = None
    dependency: Literal["requirements", "pyproject"] = "requirements"
    tests: str | None = None
    held_out: bool = False
    expected: str


class Manifest(Model):
    cases: list[Case] = Field(min_length=1, max_length=150)


def build_case(base: Path, case: Case, archive: Path) -> None:
    with ZipFile(archive, "w") as target:
        if case.fixture:
            root = base.parent.joinpath("fixtures", *relative(case.fixture).parts)
            for file in sorted(root.rglob("*")):
                if file.is_file() and "__pycache__" not in file.parts:
                    target.write(file, file.relative_to(root).as_posix())
            return
        if case.source is None:
            raise ValueError("Benchmark case requires fixture or source")
        target.writestr("models.py", case.source)
        if case.dependency == "requirements":
            target.writestr("requirements.txt", "pydantic==1.10.22\n")
        else:
            target.writestr(
                "pyproject.toml",
                """[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"
[project]
name = "patchops-benchmark-target"
version = "0.0.1"
dependencies = ["pydantic==1.10.22"]
[tool.setuptools]
py-modules = ["models"]
""",
            )
        if case.tests:
            target.writestr("tests/test_behavior.py", case.tests)


def metrics(cases: list[dict[str, Any]], *, dry_run: bool) -> dict[str, Any]:
    durations = sorted(case["seconds"] for case in cases)
    comparable = [
        row
        for case in cases
        for row in case["comparison"]
        if row["test"]
        and row["result"] in {"STILL_PASSING", "NEW_FAILURE"}
        and row.get("baseline") == "PASSED"
    ]
    held_out = [
        value
        for case in cases
        if case["held_out"]
        for check in case["post"]
        if check["name"] == "pytest"
        for value in check["outcomes"].values()
        if value != "SKIPPED"
    ]
    attempts = [item for case in cases for item in case["repairs"]]
    checks = [
        check
        for case in cases
        for phase in ("baseline", "post")
        for check in case[phase]
        if check["status"] != "NOT_CONFIGURED"
    ]
    ratio = lambda numerator, denominator: (
        numerator / denominator if denominator else None
    )
    return {
        "cases": len(cases),
        "validated_completion_rate": None
        if dry_run
        else ratio(sum(row["state"] == State.READY for row in cases), len(cases)),
        "functional_regression_rate": ratio(
            sum(row["result"] == "NEW_FAILURE" for row in comparable), len(comparable)
        ),
        "held_out_test_pass_rate": ratio(
            sum(value == "PASSED" for value in held_out), len(held_out)
        ),
        "repair_success_rate": ratio(
            sum(item["status"] == "VALIDATED" for item in attempts), len(attempts)
        ),
        "validation_coverage": ratio(
            sum(row["status"] in {"PASSED", "FAILED"} for row in checks), len(checks)
        ),
        "p50_seconds": median(durations),
        "p95_seconds": durations[math.ceil(0.95 * len(durations)) - 1],
        "changed_lines": sum(
            row["additions"] + row["deletions"]
            for case in cases
            for row in case["files"]
        ),
        "llm_tokens_charged": sum(row["llm_tokens"] for row in cases),
    }


def evaluate_manifest(
    path: Path, store: Store, settings: Settings, dry_run: bool
) -> dict[str, Any]:
    manifest = Manifest.model_validate_json(path.read_bytes())
    results = defaultdict(list)
    for case in manifest.cases:
        with NamedTemporaryFile(suffix=".zip", delete=False) as stream:
            archive = Path(stream.name)
        try:
            build_case(path.parent, case, archive)
            for strategy in ("deterministic", "llm-only", "hybrid"):
                request = Request.model_validate(
                    {
                        "source": {"type": "zip_upload", "label": case.id + ".zip"},
                        "strategy": strategy,
                        "mode": "dry-run" if dry_run else "migrate",
                        "constraints": {
                            "allow_semantic_edits": strategy != "deterministic"
                            and not case.held_out
                        },
                    }
                )
                job_id = queue(store, settings, request, archive)
                await_job(store, settings, job_id)
                job = store.get(job_id)
                assert job is not None
                report = json.loads(store.artifact(job_id, "report.json").read_bytes())
                baseline_outcomes = {
                    key: value
                    for check in report["baseline"]
                    for key, value in check["outcomes"].items()
                }
                for row in report["comparison"]:
                    row["baseline"] = baseline_outcomes.get(row["test"])
                results[strategy].append(
                    {
                        "id": case.id,
                        "job_id": job_id,
                        "held_out": case.held_out,
                        "expected": case.expected,
                        "model_assistance_allowed": request.constraints.allow_semantic_edits,
                        "seconds": round(
                            (job.finished or job.updated)
                            - (job.started or job.created),
                            3,
                        ),
                        **{
                            key: report[key]
                            for key in (
                                "state",
                                "baseline",
                                "post",
                                "comparison",
                                "repairs",
                                "files",
                                "issues",
                            )
                        },
                        "snapshot": report.get("snapshot"),
                        "llm_tokens": report.get("llm_tokens", 0),
                    }
                )
        finally:
            archive.unlink(missing_ok=True)
    return {
        "mode": "dry-run" if dry_run else "migrate",
        "model_configured": bool(settings.llm_model and settings.llm_api_key),
        "budget": {
            "seconds": settings.job_seconds,
            "llm_tokens": settings.llm_token_budget,
            "repairs": settings.max_repair_attempts,
        },
        "strategies": {
            strategy: {"metrics": metrics(cases, dry_run=dry_run), "cases": cases}
            for strategy, cases in results.items()
        },
    }
