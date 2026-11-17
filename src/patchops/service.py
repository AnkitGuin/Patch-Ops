import logging
import shutil
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Any
from uuid import uuid4

from .analysis import inspect_repo
from .config import Settings
from .intake import fingerprint, snapshot, validate_source
from .llm import PROMPT_VERSION, CompatibleProvider, Provider, context_pack
from .migrations.base import Plugin
from .migrations.pydantic_v2 import RULE_VERSION, PydanticV2, version_matches
from .models import Analysis, Check, Plan, Request, Signal, State, Zip
from .patch import apply, combined_diff, semantic_candidate
from .reporting import save_report
from .sandbox import DockerSandbox, Sandbox, unavailable
from .security import Blocked, Cancelled, redact, relative, safe_file
from .store import Store
from .validation import compare, configured

LOGGER = logging.getLogger("patchops")
PLUGINS: dict[str, Plugin] = {PydanticV2.migration_id: PydanticV2()}


def checks_json(checks: list[Check]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in checks]


def submit(
    store: Store, settings: Settings, request: Request, upload: Path | None = None
) -> str:
    if isinstance(request.source, Zip) != (upload is not None):
        raise Blocked("ZIP sources require exactly one uploaded archive")
    if (
        upload is not None
        and upload.stat().st_size > settings.max_repo_mb * 1024 * 1024
    ):
        raise Blocked("ZIP exceeds the compressed byte limit")
    label = validate_source(request, settings)
    request.objective = redact(request.objective, secrets(settings))
    return store.submit(request, label, upload)


def secrets(settings: Settings) -> tuple[str, ...]:
    return tuple(
        secret.get_secret_value()
        for secret in (settings.api_token, settings.llm_api_key, settings.github_token)
        if secret
    )


@dataclass
class Execution:
    store: Store
    settings: Settings
    job_id: str
    owner: str
    request: Request
    sandbox: Sandbox
    provider: Provider | None
    deadline: float
    report: dict[str, Any] = field(default_factory=dict)
    used_tokens: int = 0

    @property
    def folder(self) -> Path:
        return self.store.folder(self.job_id)

    @property
    def plugin(self) -> Plugin:
        return PLUGINS[self.request.migration.id]

    def checkpoint(self) -> None:
        self.store.checkpoint(self.job_id, self.owner)
        if monotonic() >= self.deadline:
            raise Blocked("Job deadline exhausted")

    def state(self, state: State, message: str = "") -> None:
        self.checkpoint()
        self.store.transition(self.job_id, self.owner, state, message)
        LOGGER.info("job_id=%s stage=%s", self.job_id, state)

    def event(self, kind: str, data: dict[str, Any]) -> None:
        self.store.add(self.job_id, self.owner, kind, data)

    def issue(self, message: str) -> None:
        safe = redact(message, secrets(self.settings))
        if safe not in self.report["issues"]:
            self.report["issues"].append(safe)

    def run_checks(
        self,
        root: Path,
        names: list[str],
        stage: str,
        no_tests: str,
        focus: list[str] | None = None,
    ) -> list[Check]:
        self.checkpoint()
        self.event("sandbox_run_started", {"phase": stage, "checks": names})
        found = self.sandbox.checks(
            root, names, self.deadline - monotonic(), self.checkpoint, focus
        )
        found = [
            check.model_copy(
                update={
                    "status": "NOT_CONFIGURED",
                    "signatures": [],
                    "stderr": "NO_TESTS_DISCOVERED",
                }
            )
            if check.name == "pytest"
            and check.status == "FAILED"
            and check.exit_code == 5
            and not check.outcomes
            else check
            for check in found
        ]
        known = {check.name for check in found}
        found.extend(
            unavailable(name, "Check did not run")
            for name in ("install", "resolved", *names)
            if name not in known
        )
        if "pytest" not in names and focus is None:
            found.append(unavailable("pytest", no_tests, "NOT_CONFIGURED"))
        for check in found:
            self.event("command", {"phase": stage, **check.model_dump(mode="json")})
        self.event(
            "sandbox_run_completed",
            {"phase": stage, "outcomes": {check.name: check.status for check in found}},
        )
        return found

    def record_patch(
        self,
        plan: Plan,
        root: Path,
        path: str,
        before: str,
        after: str,
        evidence: list[Signal],
        classification: str,
        version: str,
        rationale: str,
        metadata: dict[str, str] | None = None,
    ) -> bool:
        self.checkpoint()
        provenance = apply(
            root,
            self.plugin,
            plan,
            path,
            before,
            after,
            evidence,
            classification,
            version,
            redact(rationale, secrets(self.settings)),
        )
        if provenance is None:
            return False
        if metadata:
            provenance.update(metadata)
        self.report["provenance"].append(provenance)
        self.event("patch", provenance)
        return True

    def semantic(
        self,
        root: Path,
        plan: Plan,
        analysis: Analysis,
        item: Signal,
        failure: str = "",
    ) -> bool:
        if self.provider is None or not self.request.constraints.allow_semantic_edits:
            return False
        path = safe_file(root, item.path)
        if not item.path.endswith(".py") or not self.plugin.permits(
            root, item.path, plan
        ):
            raise Blocked("Semantic proposal target is outside scope")
        source = path.read_bytes().decode()
        context = redact(
            context_pack(root, item, analysis, failure), secrets(self.settings)
        )
        last_error = "No valid semantic proposal"
        retries = 0 if failure else self.settings.llm_retries
        for _ in range(retries + 1):
            self.checkpoint()
            remaining = self.settings.llm_token_budget - self.used_tokens
            estimate = len(context.encode()) + 3600
            if remaining < estimate:
                raise Blocked("LLM token budget exhausted")
            try:
                proposal = self.provider.propose(
                    context, remaining, self.deadline - monotonic()
                )
            except Blocked as exc:
                self.used_tokens += estimate
                self.report["llm_tokens"] = self.used_tokens
                last_error = str(exc)
                continue
            self.used_tokens += (
                max(1, proposal.tokens) if proposal.tokens is not None else estimate
            )
            self.report["llm_tokens"] = self.used_tokens
            if self.used_tokens > self.settings.llm_token_budget:
                raise Blocked("LLM token budget exhausted")
            self.event(
                "llm_patch_proposed",
                {
                    "path": item.path,
                    "rule": item.rule,
                    "model": redact(proposal.model),
                    "tokens": proposal.tokens,
                    "range": [proposal.edit.start, proposal.edit.end],
                },
            )
            after = semantic_candidate(
                source,
                proposal.edit,
                item,
                self.request.constraints.preserve_public_api,
            )
            metadata = {
                key: redact(getattr(proposal.edit, key), secrets(self.settings))
                for key in ("behavior", "uncertainty")
            }
            metadata["suggested_validation"] = redact(
                proposal.edit.validation, secrets(self.settings)
            )
            return self.record_patch(
                plan,
                root,
                item.path,
                source,
                after,
                [item],
                "LLM_SEMANTIC_EDIT",
                f"{PROMPT_VERSION}/{redact(proposal.model)}",
                proposal.edit.rationale,
                metadata,
            )
        raise Blocked(last_error)

    def make_diff(self, plan: Plan, original: Path, working: Path) -> None:
        diff, files = combined_diff(original, working, plan)
        self.store.write(self.job_id, "migration.diff", diff)
        self.report["files"] = files

    def repair(
        self,
        plan: Plan,
        analysis: Analysis,
        original: Path,
        working: Path,
        baseline: list[Check],
        post: list[Check],
        names: list[str],
        no_tests: str,
    ) -> list[Check]:
        for number in range(1, self.settings.max_repair_attempts + 1):
            new = [
                row
                for row in compare(baseline, post)
                if row["result"] == "NEW_FAILURE" and row["check"] == "pytest"
            ]
            if (
                not new
                or self.provider is None
                or not self.request.constraints.allow_semantic_edits
            ):
                break
            failures = "\n".join(
                check.stdout + "\n" + check.stderr
                for check in post
                if check.name == "pytest"
            )
            changed = {str(file["path"]) for file in self.report["files"]}
            candidates = [
                path
                for path in sorted(changed)
                if path.endswith(".py")
                and (path in failures or ("/" not in path and path + ":" in failures))
            ]
            if len(candidates) != 1:
                self.issue(
                    "New pytest failure could not be attributed to one changed source file."
                )
                break
            path = candidates[0]
            old = safe_file(original, path).read_bytes().decode()
            current = safe_file(working, path).read_bytes().decode()
            from difflib import SequenceMatcher

            spans = [
                (a + 1, b)
                for tag, _, _, a, b in SequenceMatcher(
                    None,
                    StringIO(old, newline="").readlines(),
                    StringIO(current, newline="").readlines(),
                ).get_opcodes()
                if tag in {"replace", "insert"} and b - a <= 50
            ]
            if not spans:
                break
            start, end = spans[0]
            self.state(State.REPAIRING)
            signal = Signal(
                path=path,
                line=start,
                end=end,
                rule="repair",
                evidence="Changed source range named in newly failing pytest output",
                strategy="semantic",
                risk="high",
            )
            attempt: dict[str, Any] = {
                "number": number,
                "path": path,
                "failures": new,
                "status": "STARTED",
            }
            self.report["repairs"].append(attempt)
            self.event("repair_started", attempt.copy())
            try:
                applied = self.semantic(working, plan, analysis, signal, failures)
            except Blocked as exc:
                attempt.update(status="REJECTED", reason=redact(str(exc)))
                self.event("repair_completed", attempt)
                self.issue(f"Repair {number} rejected: {exc}")
                self.state(State.POST_VALIDATING)
                break
            self.state(State.POST_VALIDATING)
            if not applied:
                attempt.update(status="NO_CHANGE")
                self.event("repair_completed", attempt)
                break
            self.make_diff(plan, original, working)
            test_files = set(analysis.test_inventory)
            focus = []
            for row in new:
                node = row["test"].split("::", 1)
                if len(node) != 2:
                    continue
                module, _, test_class = node[0].rpartition(".")
                for dotted, class_name in [(node[0], ""), (module, test_class)]:
                    test_file = dotted.replace(".", "/") + ".py"
                    if test_file in test_files and all(
                        char.isprintable() for char in node[1]
                    ):
                        focus.append(
                            "::".join(
                                item
                                for item in (test_file, class_name, node[1])
                                if item
                            )
                        )
                        break
            if focus:
                attempt["focused"] = checks_json(
                    self.run_checks(
                        working, ["pytest"], f"repair-{number}-focused", no_tests, focus
                    )
                )
            post = self.run_checks(working, names, f"repair-{number}-full", no_tests)
            self.report["post"] = checks_json(post)
            still_new = [
                row for row in compare(baseline, post) if row["result"] == "NEW_FAILURE"
            ]
            attempt.update(
                status="REGRESSIONS_REMAIN"
                if still_new
                else "VALIDATED"
                if all(check.status == "PASSED" for check in post)
                else "VALIDATION_INCOMPLETE",
                remaining_regressions=still_new,
            )
            self.event("repair_completed", attempt)
            if not still_new:
                break
        return post

    def execute(self) -> tuple[State, str]:
        request = self.request
        self.state(State.INGESTING)
        fixed = snapshot(request, self.folder, self.settings, self.checkpoint)
        job = self.store.get(self.job_id)
        assert job is not None
        self.report["snapshot"] = {
            "label": job.label,
            "commit": fixed.commit,
            "sha256": fixed.digest,
            "ref": fixed.branch,
            "language": "Python",
            "captured_at": job.created,
        }
        self.event("repository_snapshot_created", self.report["snapshot"])
        (self.folder / "input.zip").unlink(missing_ok=True)
        self.state(State.ANALYZING)
        analysis = inspect_repo(fixed, request, self.plugin, self.checkpoint)
        self.store.write(self.job_id, "analysis.json", analysis.model_dump(mode="json"))
        self.report["findings"] = {
            "files_scanned": analysis.files_scanned,
            "files_skipped": analysis.files_skipped,
            "python": analysis.python_version,
            "dependencies": analysis.dependencies,
            "signals": len(analysis.migration_signals),
            "tests": analysis.test_inventory,
            "lockfiles": analysis.lockfiles,
            "warnings": analysis.warnings,
        }
        self.event(
            "analysis_completed",
            {
                "files_scanned": analysis.files_scanned,
                "signals": len(analysis.migration_signals),
            },
        )
        for warning in analysis.warnings:
            self.issue(warning)
        if request.mode == "scan":
            self.state(State.REPORTING)
            return (
                State.WARNINGS,
                "Repository inspection only. Validation was not requested.",
            )
        pyproject = fixed.root / "pyproject.toml"
        config = (
            pyproject.read_text(encoding="utf-8", errors="replace")
            if pyproject.is_file()
            else ""
        )
        names = configured(analysis, request.validation, config)
        no_tests = (
            "NO_TESTS_DISCOVERED"
            if request.validation.run_tests
            else "Tests disabled by request"
        )
        baseline = []
        if request.mode == "migrate":
            self.state(State.BASELINE_VALIDATING)
            baseline = self.run_checks(fixed.root, names, "baseline", no_tests)
            self.report["baseline"] = checks_json(baseline)
            failed = [check.name for check in baseline if check.status == "FAILED"]
            if failed:
                self.issue("Baseline failures recorded: " + ", ".join(failed))
            self.event("baseline_tests_completed", {"failures": failed})
        self.state(State.PLANNING)
        plan = self.plugin.plan(analysis, request, names)
        self.store.write(self.job_id, "plan.json", plan.model_dump(mode="json"))
        self.report["plan"] = plan.model_dump(mode="json")
        self.event(
            "migration_plan_created",
            {"tasks": len(plan.tasks), "files": len(plan.files)},
        )
        if request.mode == "plan":
            self.state(State.REPORTING)
            return (
                State.WARNINGS,
                "Migration plan generated. No patch or validation was requested.",
            )
        if not analysis.migration_signals:
            raise Blocked("No supported Pydantic migration evidence was found")
        if baseline:
            install = next(
                (check for check in baseline if check.name == "install"), None
            )
            if (
                install is None
                or install.status != "PASSED"
                or any(check.name == "runner" for check in baseline)
            ):
                raise Blocked(
                    "Baseline environment unavailable. Run dry-run to generate an unvalidated patch."
                )
            migration = next(check for check in baseline if check.name == "migration")
            if migration.status != "PASSED" or not version_matches(
                migration.stdout.strip(), request.migration.source_version
            ):
                raise Blocked(
                    "Baseline did not establish the requested Pydantic v1 version"
                )
        if fingerprint(fixed.root) != fixed.digest:
            raise Blocked("Snapshot integrity check failed")
        self.state(State.PATCHING)
        working = self.folder / "working"
        shutil.copytree(fixed.root, working)
        try:
            for path in plan.files:
                self.checkpoint()
                relative(path)
                old = safe_file(working, path).read_bytes().decode()
                candidate = self.plugin.rewrite(path, old, request)
                evidence = [
                    item
                    for item in plan.tasks
                    if item.path == path and item.strategy == "deterministic"
                ]
                if candidate != old:
                    if not evidence:
                        raise Blocked(
                            "Deterministic change has no corresponding plan evidence"
                        )
                    self.record_patch(
                        plan,
                        working,
                        path,
                        old,
                        candidate,
                        evidence,
                        "DETERMINISTIC_REWRITE",
                        RULE_VERSION,
                        "Applied source-aware Pydantic migration rules.",
                    )
            remaining = 0
            for item in sorted(plan.tasks, key=lambda task: (task.path, -task.line)):
                if item.strategy == "manual":
                    self.issue(
                        f"Manual review for {item.rule} in {item.path}:{item.line}"
                    )
                if item.strategy != "semantic":
                    continue
                try:
                    changed = False
                    if (
                        request.strategy != "deterministic"
                        and remaining < self.settings.max_semantic_edits
                    ):
                        remaining += 1
                        changed = self.semantic(working, plan, analysis, item)
                    if not changed:
                        self.issue(
                            f"Semantic review required for {item.rule} in {item.path}:{item.line}"
                        )
                    else:
                        self.issue(
                            f"Review semantic edit for {item.rule} in {item.path}:{item.line}"
                        )
                except Blocked as exc:
                    self.event(
                        "patch_rejected",
                        {"path": item.path, "reason": redact(str(exc))},
                    )
                    self.issue(f"Proposal for {item.path}:{item.line} rejected: {exc}")
            if analysis.lockfiles:
                self.issue(
                    "Lockfiles need repository-specific regeneration and review."
                )
        finally:
            self.make_diff(plan, fixed.root, working)
        if request.mode == "dry-run":
            self.state(State.REPORTING)
            return (
                State.WARNINGS,
                "Patch generated for review. Baseline and post-migration checks were skipped by dry-run.",
            )
        self.state(State.POST_VALIDATING)
        post = self.run_checks(working, names, "post", no_tests)
        self.report["post"] = checks_json(post)
        post = self.repair(
            plan, analysis, fixed.root, working, baseline, post, names, no_tests
        )
        comparison = compare(baseline, post)
        self.report["comparison"] = comparison
        if any(row["result"] == "NEW_FAILURE" for row in comparison):
            self.issue("Post-migration validation contains new failures.")
        if any(row["result"] == "NOT_COMPARABLE" for row in comparison):
            self.issue("Some baseline and post-migration results are not comparable.")
        if any(check.status != "PASSED" for check in baseline):
            self.issue("Baseline validation includes failing or incomplete checks.")
        if any(check.status != "PASSED" for check in post):
            self.issue(
                "Post-migration validation includes failing or incomplete checks."
            )
        if any(
            check.status == "PASSED" and check.name == "pytest" and not check.outcomes
            for check in (*baseline, *post)
        ):
            self.issue("Pytest exited successfully without usable test-case evidence.")
        version = next(check for check in post if check.name == "migration")
        if version.status != "PASSED" or not version_matches(
            version.stdout.strip(), request.migration.target_version
        ):
            self.issue(
                "Installed dependency did not establish the requested Pydantic v2 target."
            )
        if not self.report["files"]:
            self.issue("No migration changes were generated.")
        self.event(
            "validation_completed",
            {"rows": len(comparison), "issues": len(self.report["issues"])},
        )
        self.state(State.REPORTING)
        if self.report["issues"]:
            return (
                State.WARNINGS,
                "Review the patch and the recorded validation findings before merging.",
            )
        return (
            State.READY,
            "The configured checks passed after migration. The patch is ready for human review.",
        )

    def run(self) -> None:
        self.report = {
            "job_id": self.job_id,
            "objective": self.request.objective,
            "scope": {
                "migration": self.request.migration.model_dump(),
                "constraints": self.request.constraints.model_dump(),
                "mode": self.request.mode,
            },
            "strategy": self.request.strategy,
            "baseline": [],
            "post": [],
            "comparison": [],
            "provenance": [],
            "repairs": [],
            "issues": [],
            "files": [],
        }
        try:
            state, summary = self.execute()
        except (Blocked, Cancelled) as exc:
            state = State.CANCELLED if isinstance(exc, Cancelled) else State.BLOCKED
            summary = redact(str(exc), secrets(self.settings))
            self.issue(summary)
        except Exception as exc:  # noqa: BLE001 - job boundary must persist unexpected failures
            LOGGER.error("job_id=%s failure=%s", self.job_id, type(exc).__name__)
            state, summary = (
                State.FAILED,
                f"Unexpected {type(exc).__name__} during migration.",
            )
            self.issue(summary)
        job = self.store.get(self.job_id)
        if job and job.owner == self.owner:
            if job.cancelled:
                state, summary = State.CANCELLED, "Cancellation requested"
                self.issue(summary)
            save_report(self.store, self.job_id, self.report, state, summary)
            self.store.transition(self.job_id, self.owner, state, summary)


def work_once(
    store: Store,
    settings: Settings,
    sandbox: Sandbox | None = None,
    provider: Provider | None = None,
) -> str | None:
    for expired in store.recover():
        job = store.get(expired)
        if job is None:
            continue
        request = Request.model_validate(job.request)
        summary = "Worker lease expired before the job could finish. Submit a new job to retry."
        records = store.records(expired, limit=20_000)
        data = {
            "job_id": expired,
            "objective": request.objective,
            "strategy": request.strategy,
            "scope": {
                "migration": request.migration.model_dump(),
                "constraints": request.constraints.model_dump(),
                "mode": request.mode,
            },
            "issues": [summary],
            "files": [],
            "comparison": [],
            "snapshot": next(
                (
                    row["data"]
                    for row in records
                    if row["type"] == "repository_snapshot_created"
                ),
                {},
            ),
            "provenance": [row["data"] for row in records if row["type"] == "patch"],
            "repairs": list(
                {
                    row["data"]["number"]: row["data"]
                    for row in records
                    if row["type"] in {"repair_started", "repair_completed"}
                }.values()
            ),
            **{
                phase: [
                    row["data"]
                    for row in records
                    if row["type"] == "command" and row["data"]["phase"] == phase
                ]
                for phase in ("baseline", "post")
            },
        }
        save_report(store, expired, data, State.FAILED, summary)
    owner = uuid4().hex
    job_id = store.claim(owner)
    if job_id is None:
        return None
    job = store.get(job_id)
    assert job is not None
    stop = Event()

    def maintain_lease() -> None:
        while not stop.wait(10):
            if not store.heartbeat(job_id, owner):
                break

    thread = Thread(target=maintain_lease, daemon=True)
    thread.start()
    try:
        execution = Execution(
            store,
            settings,
            job_id,
            owner,
            Request.model_validate(job.request),
            sandbox or DockerSandbox(settings),
            provider
            or (
                CompatibleProvider(settings)
                if settings.llm_api_key and settings.llm_model
                else None
            ),
            monotonic() + settings.job_seconds,
        )
        execution.run()
    finally:
        stop.set()
        thread.join(timeout=1)
    return job_id
