from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class State(StrEnum):
    CREATED = "CREATED"
    INGESTING = "INGESTING"
    ANALYZING = "ANALYZING"
    BASELINE_VALIDATING = "BASELINE_VALIDATING"
    PLANNING = "PLANNING"
    PATCHING = "PATCHING"
    POST_VALIDATING = "POST_VALIDATING"
    REPAIRING = "REPAIRING"
    REPORTING = "REPORTING"
    READY = "READY_FOR_REVIEW"
    WARNINGS = "READY_FOR_REVIEW_WITH_WARNINGS"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {State.READY, State.WARNINGS, State.BLOCKED, State.FAILED, State.CANCELLED}
TRANSITIONS = {
    State.CREATED: {State.INGESTING},
    State.INGESTING: {State.ANALYZING},
    State.ANALYZING: {State.BASELINE_VALIDATING, State.PLANNING, State.REPORTING},
    State.BASELINE_VALIDATING: {State.PLANNING},
    State.PLANNING: {State.PATCHING, State.REPORTING},
    State.PATCHING: {State.POST_VALIDATING, State.REPORTING},
    State.POST_VALIDATING: {State.REPAIRING, State.REPORTING},
    State.REPAIRING: {State.POST_VALIDATING, State.REPORTING},
    State.REPORTING: {State.READY, State.WARNINGS},
}


class Local(Model):
    type: Literal["local"] = "local"
    path: str = Field(min_length=1, max_length=4096)
    ref: str | None = Field(None, max_length=200)


class Remote(Model):
    type: Literal["git_url"] = "git_url"
    url: str = Field(min_length=1, max_length=500)
    ref: str | None = Field(None, max_length=200)


class Zip(Model):
    type: Literal["zip_upload"] = "zip_upload"
    label: str = Field(default="repository.zip", max_length=150)


class Migration(Model):
    id: Literal["pydantic-v1-to-v2"] = "pydantic-v1-to-v2"
    source_version: str = Field(default="1.x", pattern=r"^1\.(?:x|\d+(?:\.\d+)?)$")
    target_version: str = Field(default="2.x", pattern=r"^2\.(?:x|\d+(?:\.\d+)?)$")


class Constraints(Model):
    do_not_modify_tests: Literal[True] = True
    avoid_unrelated_refactors: Literal[True] = True
    allow_dependency_update: bool = True
    preserve_public_api: bool = True
    allow_semantic_edits: bool = False


class Validation(Model):
    run_tests: bool = True
    run_ruff: bool | None = None
    run_mypy: bool | None = None
    extra_checks: list[Literal["pytest", "ruff", "mypy"]] = Field(
        default_factory=list, max_length=3
    )


class Request(Model):
    source: Annotated[Local | Remote | Zip, Field(discriminator="type")]
    migration: Migration = Field(default_factory=Migration)
    objective: str = Field(
        default="Migrate Pydantic v1 usage to v2 and preserve behavior.",
        max_length=1000,
    )
    constraints: Constraints = Field(default_factory=Constraints)
    validation: Validation = Field(default_factory=Validation)
    mode: Literal["scan", "plan", "migrate", "dry-run"] = "migrate"
    strategy: Literal["hybrid", "deterministic", "llm-only"] = "hybrid"


Strategy = Literal["deterministic", "semantic", "manual"]
Risk = Literal["low", "medium", "high"]


class Signal(Model):
    path: str
    line: int = Field(ge=1)
    end: int = Field(ge=1)
    rule: str
    evidence: str
    strategy: Strategy
    risk: Risk


class Analysis(Model):
    repository_id: str
    commit_sha: str | None
    language: Literal["python"] = "python"
    python_version: str | None = None
    files_scanned: int = 0
    files_skipped: int = 0
    inventory: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    lockfiles: list[str] = Field(default_factory=list)
    local_import_graph: dict[str, list[str]] = Field(default_factory=dict)
    symbols: dict[str, list[str]] = Field(default_factory=dict)
    test_inventory: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    migration_signals: list[Signal] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Plan(Model):
    migration: Migration
    summary: str
    expected_behavior: str
    files: list[str]
    tasks: list[Signal]
    validation: list[str]
    assumptions: list[str]


class Edit(Model):
    path: str = Field(max_length=400)
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    expected: str = Field(min_length=1, max_length=6000)
    replacement: str = Field(min_length=1, max_length=6000)
    rationale: str = Field(min_length=1, max_length=800)
    behavior: str = Field(min_length=1, max_length=500)
    uncertainty: str = Field(min_length=1, max_length=500)
    validation: str = Field(min_length=1, max_length=500)


CheckStatus = Literal[
    "PASSED", "FAILED", "TIMED_OUT", "NOT_CONFIGURED", "NOT_RUN", "INFRA_ERROR"
]


class Check(Model):
    name: str
    argv: list[str] = Field(default_factory=list)
    status: CheckStatus
    exit_code: int | None = None
    started_at: str
    finished_at: str
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    signatures: list[str] = Field(default_factory=list)
    outcomes: dict[str, str] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
