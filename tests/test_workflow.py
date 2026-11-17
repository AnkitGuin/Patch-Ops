import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from patchops.api import create_app
from patchops.llm import FakeProvider
from patchops.models import Check, Edit, Request, State
from patchops.sandbox import DockerSandbox
from patchops.security import Blocked
from patchops.service import submit, work_once
from patchops.store import Job, Store, iso, now
from patchops.validation import compare

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/customer_catalog"
SIMPLE = """from pydantic import BaseModel

class Entry(BaseModel):
    count: int

def dump(count):
    return Entry(count=count).dict()
"""


class EvidenceSandbox:
    """Deterministic contract fake. Reads target text and never imports target code."""

    def __init__(self, *, regression=False, preexisting=False):
        self.calls = []
        self.regression = regression
        self.preexisting = preexisting

    def checks(self, root, names, seconds, checkpoint, focus=None):
        checkpoint()
        source = (root / "models.py").read_text()
        post = root.name == "working"
        self.calls.append((root.name, names, focus))
        assert seconds > 0
        evidence = []
        for name in ["install", "resolved", *names]:
            failed = name == "pytest" and (
                self.preexisting or (post and self.regression and "mode=" not in source)
            )
            evidence.append(
                make_check(
                    name,
                    "FAILED" if failed else "PASSED",
                    stdout=("2.11.10" if post else "1.10.22")
                    if name == "migration"
                    else "models.py:7: AssertionError"
                    if failed
                    else "",
                    outcomes={
                        "tests.test_behavior::test_contract": "FAILED:123"
                        if failed
                        else "PASSED"
                    }
                    if name == "pytest"
                    else {},
                )
            )
        return evidence


def make_check(name: str, status: Any = "PASSED", **kwargs):
    timestamp = iso(now())
    return Check(
        name=name,
        status=status,
        exit_code=0 if status == "PASSED" else 1,
        started_at=timestamp,
        finished_at=timestamp,
        environment={"image": "fake-contract", "network": "none", "python": "3.12"},
        **kwargs,
    )


def start(
    store, settings, archive, *, semantic=False, mode="migrate", strategy="hybrid"
):
    body = Request.model_validate(
        {
            "source": {"type": "zip_upload", "label": "test-fixture"},
            "constraints": {"allow_semantic_edits": semantic},
            "mode": mode,
            "strategy": strategy,
        }
    )
    return submit(store, settings, body, archive)


def simple_archive(archive):
    return archive(
        {
            "models.py": SIMPLE,
            "requirements.txt": "pydantic==1.10.22\n",
            "tests/test_behavior.py": "def test_contract(): assert True\n",
        }
    )


def report(store, job):
    return json.loads(store.artifact(job, "report.json").read_bytes())


def edit(path, start, end, before, after):
    return Edit(
        path=path,
        start=start,
        end=end,
        expected=before,
        replacement=after,
        rationale="Use equivalent v2 API.",
        behavior="Preserve the fixture contract.",
        uncertainty="Review errors for invalid input.",
        validation="Configured pytest checks.",
    )


def fixture_edits():
    lines = (FIXTURE / "models.py").read_text().splitlines(keepends=True)

    def replacement(first, last, content):
        return edit("models.py", first, last, "".join(lines[first - 1 : last]), content)

    return [
        replacement(
            23,
            27,
            "    from pydantic import model_validator as _model_validator\n\n"
            "    @_model_validator(mode='after')\n    def validate_bounds(self):\n"
            "        if self.minimum > self.maximum:\n"
            '            raise ValueError("minimum cannot exceed maximum")\n        return self\n',
        ),
        replacement(
            11,
            16,
            "    from pydantic import field_validator as _field_validator\n\n"
            '    @_field_validator("name")\n    @classmethod\n    def normalize(cls, value):\n'
            "        value = value.strip()\n        if not value:\n"
            '            raise ValueError("name cannot be empty")\n        return value\n',
        ),
        replacement(
            8,
            9,
            "    from pydantic import ConfigDict as _ConfigDict\n"
            "    model_config = _ConfigDict(populate_by_name=True)\n",
        ),
    ]


def test_deterministic_migration_baseline_order_and_evidence(settings, store, archive):
    job = start(store, settings, simple_archive(archive))
    sandbox = EvidenceSandbox()
    assert work_once(store, settings, sandbox) == job
    output = report(store, job)
    assert output["state"] == State.READY
    assert [call[0] for call in sandbox.calls] == ["snapshot", "working"]
    assert {row["path"] for row in output["files"]} == {"models.py", "requirements.txt"}
    assert {row["classification"] for row in output["provenance"]} == {
        "DETERMINISTIC_REWRITE"
    }
    assert all(row["status"] == "PASSED" for row in output["baseline"] + output["post"])
    assert all(row["result"] == "STILL_PASSING" for row in output["comparison"])
    assert ".dict()" in (store.folder(job) / "snapshot/models.py").read_text()
    assert ".model_dump()" in (store.folder(job) / "working/models.py").read_text()
    assert (
        "The configured checks passed" in store.artifact(job, "report.md").read_text()
    )


@pytest.mark.parametrize("baseline_problem", ["missing", "timeout", "no-test-cases"])
def test_incomplete_baseline_never_qualifies_as_fully_validated(
    settings, store, archive, baseline_problem
):
    class IncompleteBaseline(EvidenceSandbox):
        def checks(self, root, names, seconds, checkpoint, focus=None):
            checks = super().checks(root, names, seconds, checkpoint, focus)
            if root.name != "snapshot":
                return checks
            if baseline_problem == "missing":
                return [check for check in checks if check.name != "resolved"]
            pytest_check = next(check for check in checks if check.name == "pytest")
            pytest_check.outcomes = {}
            if baseline_problem == "timeout":
                pytest_check.status = "TIMED_OUT"
            return checks

    job = start(store, settings, simple_archive(archive))
    work_once(store, settings, IncompleteBaseline())
    output = report(store, job)
    assert output["state"] == State.WARNINGS
    assert all(check["status"] == "PASSED" for check in output["post"])
    assert any(
        item["result"] in {"NOT_RUN", "NOT_COMPARABLE"} for item in output["comparison"]
    )


def test_catalog_end_to_end_semantic_provider(settings, store, archive):
    job = start(store, settings, archive(FIXTURE), semantic=True)
    work_once(store, settings, EvidenceSandbox(), FakeProvider(fixture_edits()))
    output = report(store, job)
    assert output["state"] == State.WARNINGS
    assert (
        len(
            [
                item
                for item in output["provenance"]
                if item["classification"] == "LLM_SEMANTIC_EDIT"
            ]
        )
        == 3
    )
    assert all(item["status"] == "PASSED" for item in output["post"])
    assert len(output["issues"]) == 3
    assert "model_validator" in store.artifact(job, "migration.diff").read_text()
    assert not any("rejected" in issue for issue in output["issues"])


@pytest.mark.parametrize(
    "proposal",
    [
        edit("../escape", 21, 21, "x\n", "y\n"),
        edit("tests/test_catalog.py", 21, 21, "x\n", "y\n"),
        edit("models.py", 1, 1, "x\n", "import os\n"),
        Blocked("Invalid structured response"),
    ],
)
def test_rejects_out_of_scope_or_invalid_proposal(settings, store, archive, proposal):
    job = start(store, settings, archive(FIXTURE), semantic=True)
    work_once(store, settings, EvidenceSandbox(), FakeProvider([proposal]))
    output = report(store, job)
    assert any("rejected" in issue for issue in output["issues"])
    assert all(
        item["classification"] == "DETERMINISTIC_REWRITE"
        for item in output["provenance"]
    )


def test_preexisting_failure_is_preserved(settings, store, archive):
    job = start(store, settings, simple_archive(archive))
    work_once(store, settings, EvidenceSandbox(preexisting=True))
    output = report(store, job)
    assert output["state"] == State.WARNINGS
    assert any(
        item["result"] == "PRE_EXISTING_FAILURE" for item in output["comparison"]
    )
    assert not any(item["result"] == "NEW_FAILURE" for item in output["comparison"])


def test_repair_runs_focused_then_full(settings, store, archive):
    job = start(store, settings, simple_archive(archive), semantic=True)
    old = "    return Entry(count=count).model_dump()\n"
    repair = edit("models.py", 7, 7, old, old.replace("()", "(mode='python')"))
    sandbox = EvidenceSandbox(regression=True)
    work_once(store, settings, sandbox, FakeProvider([repair]))
    output = report(store, job)
    assert output["repairs"][0]["status"] == "VALIDATED"
    assert sandbox.calls[-2][2] == ["tests/test_behavior.py::test_contract"]
    assert sandbox.calls[-1][2] is None
    assert any(row["type"] == "repair_started" for row in store.records(job))


def test_repair_budget_enforced(settings, store, archive):
    settings.max_repair_attempts = 1
    job = start(store, settings, simple_archive(archive), semantic=True)
    old = "    return Entry(count=count).model_dump()\n"
    repair = edit("models.py", 7, 7, old, old.replace("()", "(by_alias=True)"))
    work_once(
        store,
        settings,
        EvidenceSandbox(regression=True),
        FakeProvider([repair, repair]),
    )
    output = report(store, job)
    assert len(output["repairs"]) == 1
    assert output["repairs"][0]["status"] == "REGRESSIONS_REMAIN"
    assert "Post-migration validation contains new failures." in output["issues"]


def test_docker_unavailable_fails_closed(settings, store, archive, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("patchops.sandbox.run", missing)
    job = start(store, settings, simple_archive(archive))
    work_once(store, settings, DockerSandbox(settings))
    output = report(store, job)
    assert output["state"] == State.BLOCKED
    assert output["baseline"][0]["status"] == "INFRA_ERROR"
    assert not (store.folder(job) / "working").exists()


def test_state_lease_and_restart(settings, store, archive):
    job = start(store, settings, simple_archive(archive))
    assert store.claim("worker-one") == job
    assert store.claim("worker-two") is None
    with pytest.raises(Blocked, match="Illegal"):
        store.transition(job, "worker-one", State.READY)
    store.transition(job, "worker-one", State.INGESTING)
    with Session(store.engine) as session:
        session.execute(update(Job).where(Job.id == job).values(lease_until=0))
        session.commit()
    restarted = Store(settings)
    restarted.recover()
    assert restarted.get(job).state == State.FAILED
    assert restarted.records(job)[-1]["type"] == "lease_expired"


def test_api_zip_queue_cancel_and_artifact_boundaries(settings, store, archive):
    app = create_app(settings, store)
    client = TestClient(app, client=("127.0.0.1", 9101))
    assert client.get("/healthz").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    body = {"source": {"type": "zip_upload", "label": "app.zip"}}
    response = client.post(
        "/api/v1/jobs/zip",
        content=simple_archive(archive).read_bytes(),
        headers={
            "Content-Type": "application/zip",
            "X-PatchOps-Request": json.dumps(body),
        },
    )
    assert response.status_code == 202
    job = response.json()["id"]
    path = f"/api/v1/jobs/{job}"
    assert client.get(path).json()["state"] == State.CREATED
    assert client.get(path + "/artifacts/input.zip").status_code == 404
    assert client.post(path + "/cancel").status_code == 202
    work_once(store, settings, EvidenceSandbox())
    assert client.get(path + "/report").json()["state"] == State.CANCELLED
    assert (
        client.post(path + "/pull-request", json={"approved": False}).status_code == 400
    )


def test_api_requires_non_loopback_auth(settings, store):
    client = TestClient(create_app(settings, store), client=("192.0.2.1", 1000))
    assert client.get("/api/v1/jobs").status_code == 403


def test_api_oversized_upload_is_rejected_and_cleaned(settings, store):
    settings.max_repo_mb = 1
    client = TestClient(create_app(settings, store), client=("127.0.0.1", 9101))
    response = client.post(
        "/api/v1/jobs/zip",
        content=b"x" * (1024 * 1024 + 1),
        headers={
            "Content-Type": "application/zip",
            "X-PatchOps-Request": json.dumps({"source": {"type": "zip_upload"}}),
        },
    )
    assert response.status_code == 413
    assert list(store.root.iterdir()) == []
    assert client.get("/api/v1/jobs").json() == []


def test_compare_differing_failures_and_skips():
    base = [
        make_check(
            "pytest",
            "FAILED",
            outcomes={"a": "FAILED:old", "b": "PASSED", "c": "PASSED"},
        )
    ]
    post = [
        make_check(
            "pytest",
            "FAILED",
            outcomes={"a": "FAILED:new", "b": "PASSED", "c": "SKIPPED"},
        )
    ]
    assert {row["test"]: row["result"] for row in compare(base, post)} == {
        "a": "NEW_FAILURE",
        "b": "STILL_PASSING",
        "c": "NOT_COMPARABLE",
    }


def test_expired_worker_produces_partial_report(settings, store, archive):
    job = start(store, settings, simple_archive(archive))
    assert store.claim("expired-worker") == job
    store.transition(job, "expired-worker", State.INGESTING)
    with Session(store.engine) as session:
        session.execute(update(Job).where(Job.id == job).values(lease_until=0))
        session.commit()
    assert work_once(Store(settings), settings, EvidenceSandbox()) is None
    assert report(store, job)["state"] == State.FAILED
    assert "Worker lease expired" in store.artifact(job, "report.md").read_text()


def test_semantic_edit_cannot_weaken_assertion():
    from patchops.models import Signal
    from patchops.patch import semantic_candidate

    before = "def check(value):\n    assert value > 0\n    return value\n"
    proposal = edit("models.py", 2, 2, "    assert value > 0\n", "    assert True\n")
    signal = Signal(
        path="models.py",
        line=2,
        end=2,
        rule="repair",
        evidence="Scoped failure",
        strategy="semantic",
        risk="high",
    )
    with pytest.raises(Blocked, match="assertion"):
        semantic_candidate(before, proposal, signal, preserve_api=True)
