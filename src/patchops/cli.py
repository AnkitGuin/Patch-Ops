import argparse
import json
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep

from .config import Settings
from .models import TERMINAL, Request, State
from .security import Blocked
from .service import submit, work_once
from .store import Store


def queue(
    store: Store, settings: Settings, request: Request, archive: Path | None = None
) -> str:
    if archive is None:
        return submit(store, settings, request)
    if archive.stat().st_size > settings.max_repo_mb * 1024 * 1024:
        raise Blocked("ZIP exceeds the compressed byte limit")
    with NamedTemporaryFile(dir=store.root, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(archive, temporary)
        return submit(store, settings, request, temporary)
    finally:
        temporary.unlink(missing_ok=True)


def await_job(store: Store, settings: Settings, job_id: str) -> State:
    while True:
        job = store.get(job_id)
        assert job is not None
        state = State(job.state)
        if state in TERMINAL:
            return state
        if work_once(store, settings) is None:
            sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(prog="patchops")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("scan", "plan", "migrate"):
        command = commands.add_parser(action)
        sources = command.add_mutually_exclusive_group(required=True)
        sources.add_argument("--repo")
        sources.add_argument("--git-url")
        sources.add_argument("--zip", type=Path)
        command.add_argument("--ref")
        command.add_argument(
            "--migration", default="pydantic-v1-to-v2", choices=["pydantic-v1-to-v2"]
        )
        command.add_argument("--source-version", default="1.x")
        command.add_argument("--target-version", default="2.x")
        command.add_argument(
            "--strategy",
            choices=["hybrid", "deterministic", "llm-only"],
            default="hybrid",
        )
        command.add_argument("--allow-semantic", action="store_true")
        command.add_argument("--keep-dependencies", action="store_true")
        command.add_argument("--queue", action="store_true")
        if action == "migrate":
            command.add_argument("--dry-run", action="store_true")
    report = commands.add_parser("report")
    report.add_argument("--job-id", required=True)
    report.add_argument("--json", action="store_true")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--benchmark", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, default=Path("var/evaluation.json"))
    evaluate.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    store = Store(settings)
    try:
        if args.action == "report":
            print(
                store.artifact(
                    args.job_id, "report.json" if args.json else "report.md"
                ).read_text(encoding="utf-8")
            )
            return
        if args.action == "evaluate":
            from .evaluate import evaluate_manifest

            result = evaluate_manifest(args.benchmark, store, settings, args.dry_run)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(args.output)
            return
        source = (
            {"type": "zip_upload", "label": args.zip.name}
            if args.zip
            else {"type": "git_url", "url": args.git_url, "ref": args.ref}
            if args.git_url
            else {"type": "local", "path": args.repo, "ref": args.ref}
        )
        request = Request.model_validate(
            {
                "source": source,
                "migration": {
                    "id": args.migration,
                    "source_version": args.source_version,
                    "target_version": args.target_version,
                },
                "mode": "dry-run" if getattr(args, "dry_run", False) else args.action,
                "strategy": args.strategy,
                "constraints": {
                    "allow_semantic_edits": args.allow_semantic,
                    "allow_dependency_update": not args.keep_dependencies,
                },
            }
        )
        job_id = queue(store, settings, request, args.zip)
        print(f"Job {job_id}", flush=True)
        if args.queue:
            return
        state = await_job(store, settings, job_id)
        name = {"scan": "analysis.json", "plan": "plan.json"}.get(
            args.action, "report.md"
        )
        try:
            print(store.artifact(job_id, name).read_text(encoding="utf-8"))
        except Blocked:
            print(store.artifact(job_id, "report.md").read_text(encoding="utf-8"))
        if state in {State.BLOCKED, State.FAILED, State.CANCELLED}:
            raise SystemExit(2)
    except (Blocked, OSError, ValueError) as exc:
        parser.exit(2, f"patchops: {exc}\n")
