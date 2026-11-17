import json
import shutil

import httpx
import pytest
from pydantic import SecretStr
from test_workflow import FIXTURE, fixture_edits, report, start

from patchops.evaluate import evaluate_manifest
from patchops.github import GitHub
from patchops.llm import FakeProvider
from patchops.models import Request, State
from patchops.process import run
from patchops.security import Blocked
from patchops.service import work_once
from patchops.store import Job


def test_benchmark_dry_run_leaves_validation_metrics_unmeasured(
    settings, store, tmp_path
):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "sample",
                        "source": "value = 1\n",
                        "expected": "Patch the dependency",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    results = evaluate_manifest(manifest, store, settings, dry_run=True)
    assert set(results["strategies"]) == {"deterministic", "llm-only", "hybrid"}
    for strategy in results["strategies"].values():
        assert strategy["cases"][0]["state"] == State.WARNINGS
        assert strategy["metrics"]["changed_lines"] == 2
        assert strategy["metrics"]["validated_completion_rate"] is None
        assert strategy["metrics"]["validation_coverage"] is None


@pytest.mark.docker
def test_catalog_with_live_docker(settings, store, archive):
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable")
    image = run(["docker", "image", "inspect", settings.sandbox_image], seconds=10)
    if image.code:
        pytest.skip("Docker sandbox image or daemon unavailable")
    job = start(store, settings, archive(FIXTURE), semantic=True)
    work_once(store, settings, provider=FakeProvider(fixture_edits()))
    output = report(store, job)
    assert output["state"] == State.WARNINGS
    for phase in ("baseline", "post"):
        evidence = next(row for row in output[phase] if row["name"] == "pytest")
        assert evidence["status"] == "PASSED"
        assert len(evidence["outcomes"]) == 6
        assert set(evidence["outcomes"].values()) == {"PASSED"}
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


def test_github_explicit_branch_create_and_repeat(settings, tmp_path):
    settings = settings.model_copy(update={"github_token": SecretStr("mock-token")})
    calls = []
    commit, tree = "a" * 40, "b" * 40
    repo = "/repos/example/catalog"
    pull_url = "https://github.com/example/catalog/pull/12"
    original = tmp_path / "working"
    original.mkdir()
    changed = original / "models.py"
    changed.write_text("value = 2\n")
    from hashlib import sha256

    job = Job(
        id="a" * 32,
        state=State.READY,
        request=Request.model_validate(
            {
                "source": {
                    "type": "git_url",
                    "url": "https://github.com/example/catalog.git",
                    "ref": "main",
                }
            }
        ).model_dump(mode="json"),
    )
    report_data = {
        "snapshot": {"commit": commit},
        "files": [{"path": "models.py"}],
        "scope": {"migration": {"source_version": "1.x", "target_version": "2.x"}},
        "provenance": [
            {
                "path": "models.py",
                "classification": "DETERMINISTIC_REWRITE",
                "after_sha256": sha256(changed.read_bytes()).hexdigest(),
            }
        ],
        "baseline": [],
        "post": [],
    }
    created = False

    def transport(request):
        nonlocal created
        assert request.headers["Authorization"] == "Bearer mock-token"
        path = request.url.path.removeprefix(repo)
        calls.append((request.method, path))
        if path == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/pulls" and request.method == "GET":
            return httpx.Response(200, json=[{"html_url": pull_url}] if created else [])
        if path == "/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": commit}})
        if path == "/git/commits/" + commit:
            return httpx.Response(200, json={"tree": {"sha": tree}})
        if path == "/git/blobs":
            return httpx.Response(201, json={"sha": "c" * 40})
        if path == "/git/trees":
            assert json.loads(request.content)["base_tree"] == tree
            return httpx.Response(201, json={"sha": "d" * 40})
        if path.startswith("/git/ref/heads/patchops"):
            return httpx.Response(404, json={"message": "Not found"})
        if path == "/git/commits":
            assert json.loads(request.content)["parents"] == [commit]
            return httpx.Response(201, json={"sha": "e" * 40})
        if path == "/git/refs":
            assert json.loads(request.content)["ref"] == "refs/heads/patchops/" + job.id
            return httpx.Response(201, json={})
        if path == "/pulls" and request.method == "POST":
            created = True
            return httpx.Response(201, json={"html_url": pull_url})
        raise AssertionError(path)

    github = GitHub(settings, httpx.MockTransport(transport))
    with github.client:
        assert github.create(job, tmp_path, report_data) == pull_url
        assert github.create(job, tmp_path, report_data) == pull_url
        assert calls.count(("POST", "/pulls")) == 1
        assert not any(method in {"PUT", "PATCH", "DELETE"} for method, _ in calls)
        job.state = State.WARNINGS
        with pytest.raises(Blocked):
            github.create(job, tmp_path, report_data)
