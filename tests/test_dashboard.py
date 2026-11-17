from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).resolve().parents[1] / "src/patchops/dashboard.py"


def test_dashboard_creation_and_review(monkeypatch):
    queued = []
    job_id = "a" * 32
    summary = {
        "id": job_id,
        "label": "Demo catalog",
        "state": "READY_FOR_REVIEW_WITH_WARNINGS",
        "created": "2026-11-13T00:00:00Z",
        "finished": "2026-11-13T00:00:01Z",
        "cancel_requested": False,
        "pr_url": None,
        "pr_enabled": False,
    }

    def response(method, url, **kwargs):
        path = url.split("/api/v1/jobs", 1)[1].split("?", 1)[0]
        if method == "POST":
            queued.append(kwargs["json"])
            payload = {"id": job_id}
        else:
            payload = {
                "": [summary],
                f"/{job_id}": summary,
                f"/{job_id}/events": [],
                f"/{job_id}/analysis": {
                    "commit_sha": None,
                    "repository_id": "snapshot",
                    "files_scanned": 3,
                    "test_inventory": ["tests/test_catalog.py"],
                    "warnings": [],
                },
                f"/{job_id}/plan": {
                    "summary": "Update supported APIs",
                    "tasks": [],
                    "assumptions": [],
                },
                f"/{job_id}/diff": {
                    "diff": "+model_dump()",
                    "files": [],
                    "provenance": [],
                },
                f"/{job_id}/report": {
                    "summary": "Review required",
                    "baseline": [],
                    "post": [],
                    "comparison": [],
                    "repairs": [],
                    "issues": [],
                    "artifacts": [],
                },
            }[path]
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", response)
    app = AppTest.from_file(str(DASHBOARD), default_timeout=15).run()
    assert app.header[0].value == "Migration jobs"
    app.sidebar.radio[0].set_value("New migration").run()
    app.text_input[0].set_value("/repos/catalog")
    app.button[0].click().run()
    assert queued[0]["source"]["path"] == "/repos/catalog"
    assert any(job_id in item.value for item in app.success)
    app.sidebar.radio[0].set_value("Review job").run()
    assert not app.exception
    assert any("Validation was skipped" in item.value for item in app.warning)
    assert app.code[0].value == "+model_dump()"
