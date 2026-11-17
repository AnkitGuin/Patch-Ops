import re
import tomllib
from pathlib import Path

import libcst as cst
from libcst.metadata import (
    MetadataWrapper,
    PositionProvider,
    QualifiedNameProvider,
    ScopeProvider,
)
from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from ..models import Analysis, Plan, Request, Risk, Signal, Strategy
from ..security import excluded, relative, test_path

RULE_VERSION = "1.0"
RENAMES = {
    "dict": "model_dump",
    "json": "model_dump_json",
    "parse_obj": "model_validate",
    "parse_raw": "model_validate_json",
    "schema": "model_json_schema",
    "copy": "model_copy",
}
REQUIREMENTS = re.compile(r"requirements(?:[-_.][\w.-]+)?\.(?:txt|in)\Z")
MANUAL_IMPORTS = {
    "BaseSettings",
    "ConstrainedStr",
    "ConstrainedInt",
    "GenericModel",
    "parse_obj_as",
    "schema_of",
}
SEMANTIC_METHODS = {
    "from_orm",
    "update_forward_refs",
    "validate",
    "schema_json",
    "__fields__",
    "__root__",
}


def requirement(value: str) -> Requirement | None:
    try:
        parsed = Requirement(value.strip())
    except InvalidRequirement:
        return None
    return parsed if parsed.name.lower() == "pydantic" and not parsed.url else None


def v1(parsed: Requirement) -> bool:
    candidates = {Version(v) for v in ("1.0", "1.8", "1.9", "1.10", "1.10.26")}
    for item in parsed.specifier:
        try:
            boundary = Version(item.version.removesuffix(".*"))
        except InvalidVersion:
            continue
        if boundary.major == 1:
            candidates.add(boundary)
            candidates.add(Version(f"1.{boundary.minor}.{boundary.micro + 1}"))
    return any(parsed.specifier.contains(candidate) for candidate in candidates)


def v1_only(parsed: Requirement) -> bool:
    # Positive proof of a v1 ceiling is required before changing a declaration.
    # Sampling representative v2 releases can miss versions admitted by the range.
    for item in parsed.specifier:
        try:
            boundary = Version(item.version.removesuffix(".*"))
        except InvalidVersion:
            continue
        if (
            item.operator in {"==", "~=", "<="}
            and boundary.major == 1
            or item.operator == "<"
            and boundary <= Version("2")
        ):
            return v1(parsed)
    return False


def replace_requirement(value: str, target: str) -> str:
    match = re.match(r"(?i)(pydantic(?:\s*\[[^\]]+\])?)(\s*)", value)
    assert match is not None
    specifier = (
        ">=2,<3"
        if target == "2.x"
        else f"=={target}"
        if target.count(".") == 2
        else f"=={target}.*"
    )
    body, separator, marker = value.partition(";")
    trailing = body[len(body.rstrip()) :]
    return match[1] + match[2] + specifier + trailing + separator + marker


def dependency_edit(path: str, source: str, target: str) -> str:
    if REQUIREMENTS.fullmatch(Path(path).name):
        result = []
        for line in source.splitlines(keepends=True):
            content, comment, suffix = line.partition("#")
            parsed = requirement(content)
            if parsed and v1_only(parsed) and "\\" not in content:
                raw = content.strip()
                line = (
                    content.replace(raw, replace_requirement(raw, target), 1)
                    + comment
                    + suffix
                )
            result.append(line)
        return "".join(result)
    if path != "pyproject.toml":
        return source
    try:
        expected = tomllib.loads(source)
    except tomllib.TOMLDecodeError:
        return source
    project = expected.get("project", {})
    groups = [
        project.get("dependencies", []),
        *project.get("optional-dependencies", {}).values(),
    ]
    edits = {}
    for group in groups:
        if not isinstance(group, list):
            continue
        for index, entry in enumerate(group):
            parsed = requirement(entry) if isinstance(entry, str) else None
            if parsed and v1_only(parsed):
                edits[entry] = group[index] = replace_requirement(entry, target)
    candidate = source
    for old, new in edits.items():
        for quote in ('"', "'"):
            if quote not in old and quote not in new:
                candidate = candidate.replace(quote + old + quote, quote + new + quote)
    try:
        return candidate if tomllib.loads(candidate) == expected else source
    except tomllib.TOMLDecodeError:
        return source


def declared_versions(path: str, source: str) -> list[str]:
    if REQUIREMENTS.fullmatch(Path(path).name):
        return [
            line.partition("#")[0].strip()
            for line in source.splitlines()
            if re.match(r"(?i)\s*pydantic\b(?![-.])", line)
        ]
    if path == "pyproject.toml":
        try:
            metadata = tomllib.loads(source)
        except tomllib.TOMLDecodeError:
            return []
        project = metadata.get("project", {})
        values = [
            *project.get("dependencies", []),
            *(
                entry
                for group in project.get("optional-dependencies", {}).values()
                for entry in group
            ),
        ]
        poetry = metadata.get("tool", {}).get("poetry", {}).get("dependencies", {})
        if "pydantic" in poetry:
            values.append(f"pydantic (Poetry) {poetry['pydantic']}")
        return [
            str(entry)
            for entry in values
            if re.match(r"(?i)pydantic\b(?![-.])", str(entry))
        ]
    if Path(path).name in {"setup.cfg", "setup.py", "Pipfile"}:
        return [
            line.strip()
            for line in source.splitlines()
            if re.search(r"(?i)\bpydantic\b(?![-.])", line)
        ]
    return []


class MigrationVisitor(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider, QualifiedNameProvider, ScopeProvider)

    def __init__(self, path: str):
        self.path = path
        self.signals: list[Signal] = []
        self.models: dict[cst.CSTNode, frozenset[str]] = {}
        self.classes: list[bool] = []

    def note(
        self,
        node: cst.CSTNode,
        rule: str,
        evidence: str,
        strategy: Strategy,
        risk: Risk,
    ) -> None:
        pos = self.get_metadata(PositionProvider, node)
        self.signals.append(
            Signal(
                path=self.path,
                line=pos.start.line,
                end=pos.end.line,
                rule=rule,
                evidence=evidence,
                strategy=strategy,
                risk=risk,
            )
        )

    def names(self, node: cst.CSTNode) -> set[str]:
        return {
            name.name for name in self.get_metadata(QualifiedNameProvider, node, set())
        }

    def model_methods(self, node: cst.BaseExpression) -> frozenset[str] | None:
        if self.names(node) == {"pydantic.BaseModel"}:
            return frozenset()
        if not isinstance(node, cst.Name):
            return None
        scope = self.get_metadata(ScopeProvider, node, None)
        if scope is None:
            return None
        definitions = {
            getattr(assignment, "node", None) for assignment in scope[node.value]
        }
        if len(definitions) != 1:
            return None
        definition = next(iter(definitions))
        return (
            self.models.get(definition) if isinstance(definition, cst.CSTNode) else None
        )

    def known_model(self, node: cst.BaseExpression) -> bool:
        return self.model_methods(node) is not None

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        bases = [self.model_methods(base.value) for base in node.bases]
        model = any(base is not None for base in bases)
        if model:
            inherited = set().union(*(base for base in bases if base is not None))
            if any(base is None for base in bases):
                inherited.update(RENAMES)
                inherited.update(RENAMES.values())
            body = node.body.body
            names = {
                item.name.value for item in body if isinstance(item, cst.FunctionDef)
            }
            for item in body:
                if isinstance(
                    item, (cst.SimpleStatementLine, cst.SimpleStatementSuite)
                ):
                    for statement in item.body:
                        if isinstance(statement, cst.Assign):
                            names.update(
                                target.target.value
                                for target in statement.targets
                                if isinstance(target.target, cst.Name)
                            )
                        elif isinstance(statement, cst.AnnAssign) and isinstance(
                            statement.target, cst.Name
                        ):
                            names.add(statement.target.value)
            self.models[node] = frozenset(inherited | names)
            self.note(
                node.name,
                "base-model",
                "Pydantic BaseModel subclass",
                "deterministic",
                "low",
            )
        if node.name.value == "Config" and self.classes and self.classes[-1]:
            self.note(
                node,
                "model-config",
                "Inner Config requires ConfigDict and setting-by-setting review",
                "semantic",
                "medium",
            )
        self.classes.append(model)

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self.classes.pop()
        return updated_node

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if not self.classes or not self.classes[-1]:
            return
        for decorator in node.decorators:
            target = decorator.decorator
            names = self.names(target.func if isinstance(target, cst.Call) else target)
            if names & {"pydantic.validator", "pydantic.root_validator"}:
                start = self.get_metadata(PositionProvider, decorator).start.line
                self.note(
                    node,
                    "validator",
                    "Validator ordering, defaults and signature require review",
                    "semantic",
                    "high",
                )
                self.signals[-1].line = start
                break

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            module = cst.Module([]).code_for_node(alias.name)
            if module == "pydantic.v1" or module.startswith("pydantic.v1."):
                self.note(
                    node,
                    "v1-shim",
                    "Explicit v1 compatibility import needs manual migration",
                    "manual",
                    "high",
                )

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if node.module is None or node.relative:
            return
        module = cst.Module([]).code_for_node(node.module)
        if module == "pydantic.v1" or module.startswith("pydantic.v1."):
            self.note(
                node,
                "v1-shim",
                "Explicit v1 compatibility import needs manual migration",
                "manual",
                "high",
            )
        if (module == "pydantic" or module.startswith("pydantic.")) and not isinstance(
            node.names, cst.ImportStar
        ):
            for alias in node.names:
                if (
                    module == "pydantic"
                    and isinstance(alias.name, cst.Name)
                    and alias.name.value == "v1"
                ):
                    self.note(
                        node,
                        "v1-shim",
                        "Explicit v1 compatibility import needs manual migration",
                        "manual",
                        "high",
                    )
                if (
                    isinstance(alias.name, cst.Name)
                    and alias.name.value in MANUAL_IMPORTS
                ):
                    self.note(
                        node,
                        "removed-import",
                        f"Review {alias.name.value} and its dependency requirements",
                        "manual",
                        "high",
                    )

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        attr = original_node.func
        if not isinstance(attr, cst.Attribute):
            return updated_node
        method = attr.attr.value
        if method not in RENAMES and method not in SEMANTIC_METHODS:
            return updated_node
        receiver = attr.value
        direct = self.model_methods(receiver)
        instance = (
            self.model_methods(receiver.func)
            if isinstance(receiver, cst.Call)
            else None
        )
        if direct is None and instance is None:
            # Record unresolved calls in Pydantic modules for review without guessing their type.
            if self.models and method in RENAMES:
                self.note(
                    original_node,
                    "unresolved-receiver",
                    f"Could not resolve receiver of {method}() to a Pydantic model",
                    "manual",
                    "medium",
                )
            return updated_node
        methods = direct if direct is not None else instance
        assert methods is not None
        overridden = method in methods or RENAMES.get(method) in methods
        safe = not overridden and (
            (
                method == "dict"
                and instance is not None
                and all(
                    arg.keyword
                    and arg.keyword.value
                    in {
                        "include",
                        "exclude",
                        "by_alias",
                        "exclude_unset",
                        "exclude_defaults",
                        "exclude_none",
                    }
                    and not arg.star
                    for arg in original_node.args
                )
            )
            or (
                method == "parse_obj"
                and direct is not None
                and len(original_node.args) == 1
                and not original_node.args[0].keyword
                and not original_node.args[0].star
            )
        )
        if safe:
            self.note(
                original_node,
                f"api-{method}",
                f"Resolved model receiver for {method}()",
                "deterministic",
                "low",
            )
            assert isinstance(updated_node.func, cst.Attribute)
            return updated_node.with_changes(
                func=updated_node.func.with_changes(attr=cst.Name(RENAMES[method]))
            )
        if overridden:
            self.note(
                original_node,
                f"api-{method}",
                f"Model defines {method}() or its replacement; review dispatch",
                "manual",
                "high",
            )
            return updated_node
        replacement = RENAMES.get(method, "the corresponding v2 API")
        self.note(
            original_node,
            f"api-{method}",
            f"Review {method}() semantics before using {replacement}",
            "semantic",
            "medium",
        )
        return updated_node

    def visit_Attribute(self, node: cst.Attribute) -> None:
        if node.attr.value in {"__fields__", "__root__", "__fields_set__"} and (
            self.known_model(node.value) or self.classes and self.classes[-1]
        ):
            self.note(
                node,
                "model-internals",
                "Review model internals or root model representation",
                "manual",
                "high",
            )

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        if (
            isinstance(node.target, cst.Name)
            and node.target.value == "__root__"
            and self.classes
            and self.classes[-1]
        ):
            self.note(
                node,
                "root-model",
                "Review v2 RootModel representation",
                "manual",
                "high",
            )


def transform(path: str, source: str) -> tuple[str, list[Signal]]:
    visitor = MigrationVisitor(path)
    result = MetadataWrapper(cst.parse_module(source)).visit(visitor)
    return result.code, visitor.signals


class PydanticV2:
    migration_id = "pydantic-v1-to-v2"

    def inspect(self, path: str, source: str, request: Request) -> list[Signal]:
        if path.endswith(".py") and path not in {"setup.py"}:
            signals = transform(path, source)[1]
            if not test_path(relative(path)):
                return signals
            return [
                item.model_copy(update={"strategy": "manual", "risk": "medium"})
                for item in signals
                if item.rule != "base-model"
            ]
        versions = declared_versions(path, source)
        if not versions:
            return []
        rewritten = dependency_edit(path, source, request.migration.target_version)
        pattern = next(
            (
                idx
                for idx, line in enumerate(source.splitlines(), 1)
                if re.search(r"(?i)pydantic", line)
            ),
            1,
        )
        signals = []
        if rewritten != source:
            signals.append(
                Signal(
                    path=path,
                    line=pattern,
                    end=pattern,
                    rule="dependency",
                    evidence="Pydantic declaration",
                    strategy="deterministic",
                    risk="low",
                )
            )
            versions = declared_versions(path, rewritten)
        if any(
            (parsed := requirement(value)) is None or v1(parsed) for value in versions
        ):
            signals.append(
                Signal(
                    path=path,
                    line=pattern,
                    end=pattern,
                    rule="dependency",
                    evidence="Pydantic declaration still permits v1 or needs manual review",
                    strategy="manual",
                    risk="high",
                )
            )
        return signals

    def plan(self, analysis: Analysis, request: Request, checks: list[str]) -> Plan:
        tasks = []
        for signal in analysis.migration_signals:
            if signal.rule == "base-model":
                continue
            if (
                signal.rule == "dependency"
                and not request.constraints.allow_dependency_update
            ):
                signal = signal.model_copy(update={"strategy": "manual"})
            elif (
                signal.strategy == "deterministic"
                and signal.rule.startswith("api-")
                and request.strategy == "llm-only"
            ):
                signal = signal.model_copy(update={"strategy": "semantic"})
            tasks.append(signal)
        assumptions = [
            "Conservative receiver resolution covers same-module BaseModel classes and inline model construction.",
            "Behavior outside discovered checks requires human review.",
        ]
        if analysis.lockfiles:
            assumptions.append(
                "Review and regenerate lockfiles using the repository's lock command: "
                + ", ".join(analysis.lockfiles)
            )
        return Plan(
            migration=request.migration,
            summary="Update eligible Pydantic APIs and dependency declarations.",
            expected_behavior="Preserve public APIs, input validation, and serialization behavior.",
            files=sorted({task.path for task in tasks if task.strategy != "manual"}),
            tasks=tasks,
            validation=checks,
            assumptions=assumptions,
        )

    def rewrite(self, path: str, source: str, request: Request) -> str:
        if path.endswith(".py"):
            return (
                source if request.strategy == "llm-only" else transform(path, source)[0]
            )
        if request.constraints.allow_dependency_update:
            return dependency_edit(path, source, request.migration.target_version)
        return source

    def permits(self, root: Path, path: str, plan: Plan) -> bool:
        name = relative(path)
        return (
            path in plan.files
            and not excluded(name)
            and not test_path(name)
            and (root / path).is_file()
        )


def version_matches(version: str, target: str) -> bool:
    try:
        observed = Version(version)
    except InvalidVersion:
        return False
    if target.endswith(".x"):
        return observed.major == int(target.split(".")[0])
    requested = Version(target)
    return (
        observed == requested
        if target.count(".") == 2
        else observed.release[:2] == requested.release[:2]
    )
