import hmac
import json
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi import Request as HTTPRequest
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .github import GitHub
from .models import Analysis, Model, Plan, Request, State
from .security import Blocked
from .service import submit
from .store import Job, Store, iso, now

ARTIFACTS = ("analysis.json", "plan.json", "migration.diff", "report.json", "report.md")
PREFIX = "/api/v1"


class JobCreated(Model):
    id: str
    state: State = State.CREATED


class JobSummary(Model):
    id: str
    label: str
    state: State
    created: str
    updated: str
    started: str | None
    finished: str | None
    cancel_requested: bool
    pr_url: str | None
    pr_enabled: bool


class Event(Model):
    id: int
    type: str
    stage: str
    time: str
    data: dict[str, Any]


class Artifact(Model):
    name: str
    url: str
    bytes: int


class Diff(Model):
    diff: str
    files: list[dict[str, Any]]
    provenance: list[dict[str, Any]]


class PullRequest(Model):
    approved: bool


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or Settings()
    store = store or Store(settings)
    app = FastAPI(title="PatchOps", version="0.1.0")
    config, database = settings, store

    @app.exception_handler(RequestValidationError)
    async def invalid_input(
        _: HTTPRequest, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                    for item in exc.errors()
                ]
            },
        )

    @app.exception_handler(Blocked)
    async def blocked(_: HTTPRequest, exc: Blocked) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def authorize(
        request: HTTPRequest, authorization: Annotated[str | None, Header()] = None
    ) -> None:
        if config.api_token is None:
            if request.client is None or request.client.host not in {
                "127.0.0.1",
                "::1",
            }:
                raise HTTPException(
                    403, "Configure an API token for non-loopback clients"
                )
        elif not hmac.compare_digest(
            (authorization or "").encode(),
            ("Bearer " + config.api_token.get_secret_value()).encode(),
        ):
            raise HTTPException(401, "Valid bearer token required")

    secured = [Depends(authorize)]

    def require_job(job_id: str) -> Job:
        job = database.get(job_id) if len(job_id) == 32 else None
        if job is None:
            raise HTTPException(404, "Job not found")
        return job

    def describe(job: Job) -> JobSummary:
        source = job.request["source"]
        return JobSummary(
            id=job.id,
            state=State(job.state),
            label=job.label,
            created=iso(job.created),
            updated=iso(job.updated),
            started=iso(job.started) if job.started else None,
            finished=iso(job.finished) if job.finished else None,
            cancel_requested=job.cancelled,
            pr_url=job.pr_url,
            pr_enabled=bool(
                config.github_token
                and job.state == State.READY
                and source.get("url", "").startswith("https://github.com/")
            ),
        )

    def artifact(job_id: str, name: str) -> Path:
        require_job(job_id)
        if name not in ARTIFACTS:
            raise HTTPException(404, "Artifact not found")
        try:
            return database.artifact(job_id, name)
        except Blocked as exc:
            raise HTTPException(404, "Artifact is not available yet") from exc

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "docker_cli": shutil.which("docker") is not None,
            "semantic_provider": bool(config.llm_api_key and config.llm_model),
            "github": bool(config.github_token),
        }

    @app.post(PREFIX + "/jobs", dependencies=secured, status_code=202)
    def create(body: Request) -> JobCreated:
        return JobCreated(id=submit(database, config, body))

    @app.post(PREFIX + "/jobs/zip", dependencies=secured, status_code=202)
    async def zip_job(
        request: HTTPRequest,
        x_patchops_request: Annotated[str, Header(max_length=8000)],
    ) -> JobCreated:
        if request.headers.get("content-type", "").split(";")[0] != "application/zip":
            raise HTTPException(415, "Send a raw application/zip body")
        try:
            body = Request.model_validate_json(x_patchops_request)
        except ValidationError as exc:
            raise HTTPException(422, "Invalid ZIP job metadata") from exc
        total = 0
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(dir=database.root, delete=False) as stream:
                temporary = Path(stream.name)
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > config.max_repo_mb * 1024 * 1024:
                        raise HTTPException(
                            413, "ZIP exceeds the compressed byte limit"
                        )
                    stream.write(chunk)
            return JobCreated(
                id=await run_in_threadpool(submit, database, config, body, temporary)
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @app.get(PREFIX + "/jobs", dependencies=secured)
    def jobs(
        limit: Annotated[int, Query(ge=1, le=200)] = 30,
        offset: Annotated[int, Query(ge=0)] = 0,
        state: State | None = None,
    ) -> list[JobSummary]:
        return [describe(job) for job in database.listing(limit, offset, state)]

    @app.get(PREFIX + "/jobs/{job_id}", dependencies=secured)
    def detail(job_id: str) -> JobSummary:
        return describe(require_job(job_id))

    @app.post(PREFIX + "/jobs/{job_id}/cancel", dependencies=secured, status_code=202)
    def cancel(job_id: str) -> JobSummary:
        require_job(job_id)
        database.cancel(job_id)
        return describe(require_job(job_id))

    @app.get(PREFIX + "/jobs/{job_id}/events", dependencies=secured)
    def events(
        job_id: str,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[Event]:
        require_job(job_id)
        return [
            Event.model_validate(row)
            for row in database.records(job_id, limit=limit, offset=offset)
        ]

    @app.get(PREFIX + "/jobs/{job_id}/analysis", dependencies=secured)
    def analysis(job_id: str) -> Analysis:
        return Analysis.model_validate_json(
            artifact(job_id, "analysis.json").read_bytes()
        )

    @app.get(PREFIX + "/jobs/{job_id}/plan", dependencies=secured)
    def plan(job_id: str) -> Plan:
        return Plan.model_validate_json(artifact(job_id, "plan.json").read_bytes())

    @app.get(PREFIX + "/jobs/{job_id}/report", dependencies=secured)
    def report(job_id: str) -> dict[str, Any]:
        return json.loads(artifact(job_id, "report.json").read_bytes())

    @app.get(PREFIX + "/jobs/{job_id}/diff", dependencies=secured)
    def diff(job_id: str) -> Diff:
        content = artifact(job_id, "migration.diff").read_bytes().decode()
        records = database.records(job_id, "patch", limit=20_000)
        job_report = (
            report(job_id)
            if (database.folder(job_id) / "report.json").is_file()
            else {}
        )
        return Diff(
            diff=content,
            files=job_report.get("files", []),
            provenance=[row["data"] for row in records],
        )

    @app.get(PREFIX + "/jobs/{job_id}/artifacts", dependencies=secured)
    def artifacts(job_id: str) -> list[Artifact]:
        require_job(job_id)
        folder = database.folder(job_id)
        return [
            Artifact(
                name=name,
                url=f"{PREFIX}/jobs/{job_id}/artifacts/{name}",
                bytes=artifact(job_id, name).stat().st_size,
            )
            for name in ARTIFACTS
            if (folder / name).is_file()
        ]

    @app.get(PREFIX + "/jobs/{job_id}/artifacts/{name}", dependencies=secured)
    def download(job_id: str, name: str) -> FileResponse:
        return FileResponse(
            artifact(job_id, name),
            filename=name,
            media_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.post(PREFIX + "/jobs/{job_id}/pull-request", dependencies=secured)
    def pull_request(job_id: str, body: PullRequest) -> dict[str, str]:
        if not body.approved:
            raise HTTPException(400, "Explicit approval is required")
        require_job(job_id)
        with Session(database.engine) as session:
            job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            assert job is not None
            if job.pr_url:
                return {"url": job.pr_url}
            if not describe(job).pr_enabled:
                raise HTTPException(
                    409, "The job is not eligible for GitHub PR creation"
                )
            provider = GitHub(config)
            try:
                url = provider.create(job, database.folder(job_id), report(job_id))
            finally:
                provider.client.close()
            job.pr_url = url
            job.updated = now()
            database.record(
                session, job_id, job.state, "pull_request_created", {"url": url}
            )
            session.commit()
            return {"url": url}

    return app
