import ast
import difflib
import hashlib
import re
from io import StringIO
from pathlib import Path
from uuid import uuid4

from .migrations.base import Plugin
from .models import Edit, Plan, Signal
from .security import SECRET_PATTERNS, Blocked, redact, safe_file


def sha(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def unified(path: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(
        StringIO(old).readlines(),
        StringIO(new).readlines(),
        f"a/{path}\t" if " " in path else f"a/{path}",
        f"b/{path}\t" if " " in path else f"b/{path}",
    )
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in lines
    )


def public_api(tree: ast.AST) -> set[str]:
    def exports(node: ast.AST, prefix: str = "") -> set[str]:
        result = set()
        for child in getattr(node, "body", []):
            if isinstance(child, ast.ClassDef) and not child.name.startswith("_"):
                name = prefix + child.name
                result.add(name)
                result.update(exports(child, name + "."))
            elif isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and not child.name.startswith("_"):
                result.add(prefix + child.name + "(" + ast.dump(child.args) + ")")
            elif (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
                and not child.target.id.startswith("_")
            ):
                result.add(prefix + child.target.id)
        return result

    return exports(tree)


def sensitive_nodes(tree: ast.AST) -> set[str]:
    banned_modules = {
        "os",
        "sys",
        "subprocess",
        "socket",
        "shutil",
        "ctypes",
        "requests",
        "httpx",
        "urllib",
        "pathlib",
        "pickle",
        "importlib",
        "builtins",
    }
    banned = {
        "exec",
        "eval",
        "compile",
        "open",
        "__import__",
        "__builtins__",
        "getattr",
        "setattr",
        "breakpoint",
        "skip",
        "skipif",
        "xfail",
        "filterwarnings",
        "system",
        "popen",
    }
    findings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [item.name.split(".")[0] for item in node.names]
                if isinstance(node, ast.Import)
                else [(node.module or "").split(".")[0]]
            )
            if banned_modules.intersection(names):
                findings.add(ast.dump(node))
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if name in banned:
                findings.add(ast.dump(node))
    return findings


def semantic_candidate(
    source: str, edit: Edit, signal: Signal, preserve_api: bool
) -> str:
    if (
        edit.path != signal.path
        or not signal.line <= edit.start <= edit.end <= signal.end
    ):
        raise Blocked(
            "Semantic proposal touches a range outside the reviewed migration evidence"
        )
    if edit.end - edit.start >= 50 or edit.replacement.count("\n") > 65:
        raise Blocked("Semantic edit exceeds the source-range budget")
    lines = StringIO(source, newline="").readlines()
    if "".join(lines[edit.start - 1 : edit.end]) != edit.expected:
        raise Blocked("Semantic proposal is stale or has incorrect source context")
    if any(re.search(pattern, edit.replacement) for pattern in SECRET_PATTERNS):
        raise Blocked("Semantic proposal contains possible credentials")
    candidate = (
        "".join(lines[: edit.start - 1]) + edit.replacement + "".join(lines[edit.end :])
    )
    try:
        before, after = ast.parse(source), ast.parse(candidate)
    except (SyntaxError, ValueError) as exc:
        raise Blocked("Semantic edit is not valid Python") from exc
    from collections import Counter

    original_assertions = Counter(
        ast.dump(node) for node in ast.walk(before) if isinstance(node, ast.Assert)
    )
    current_assertions = Counter(
        ast.dump(node) for node in ast.walk(after) if isinstance(node, ast.Assert)
    )
    if original_assertions - current_assertions:
        raise Blocked("Semantic edit removed or changed an assertion")
    removed_api = {
        name
        for name in public_api(before) - public_api(after)
        if ".Config" not in name and not (signal.rule == "validator" and "(" in name)
    }
    if preserve_api and removed_api:
        raise Blocked("Semantic edit changed a public function or field declaration")
    if sensitive_nodes(after) - sensitive_nodes(before):
        raise Blocked("Semantic edit introduced a restricted operation")
    return candidate


def apply(
    root: Path,
    plugin: Plugin,
    plan: Plan,
    path: str,
    before: str,
    after: str,
    evidence: list[Signal],
    classification: str,
    version: str,
    rationale: str,
) -> dict[str, object] | None:
    if before == after:
        return None
    if not plugin.permits(root, path, plan):
        raise Blocked("Patch touches an out-of-scope file")
    file = safe_file(root, path)
    if "\0" in before or "\0" in after or file.read_bytes() != before.encode():
        raise Blocked("Patch base is stale or binary")
    if len(after.encode()) > len(before.encode()) + 32_000:
        raise Blocked("Patch exceeds the change size limit")
    if path.endswith(".py"):
        try:
            ast.parse(after)
        except (ValueError, SyntaxError) as exc:
            raise Blocked("Patch failed Python syntax validation") from exc
    diff = unified(path, before, after)
    # Exact base verification plus checked replacement of an existing regular file is
    # equivalent to apply --check for this single-file replacement format.
    temp = file.with_name(f".patchops-{uuid4().hex}")
    try:
        temp.write_bytes(after.encode())
        temp.chmod(file.stat().st_mode & 0o777)
        temp.replace(file)
    finally:
        temp.unlink(missing_ok=True)
    return {
        "path": path,
        "classification": classification,
        "version": version,
        "rule_ids": sorted({item.rule for item in evidence}),
        "before_sha256": sha(before),
        "after_sha256": sha(after),
        "evidence": [item.model_dump() for item in evidence],
        "hunk_headers": [line for line in diff.split("\n") if line.startswith("@@ ")],
        "validation": plan.validation,
        "rationale": redact(rationale),
        "review_required": classification != "DETERMINISTIC_REWRITE",
    }


def combined_diff(
    original: Path, working: Path, plan: Plan
) -> tuple[str, list[dict[str, object]]]:
    chunks = []
    files = []
    for path in plan.files:
        old = safe_file(original, path).read_bytes().decode()
        new = safe_file(working, path).read_bytes().decode()
        if old != new:
            diff = unified(path, old, new)
            chunks.append(diff)
            lines = diff.split("\n")[2:]
            files.append(
                {
                    "path": path,
                    "additions": sum(line.startswith("+") for line in lines),
                    "deletions": sum(line.startswith("-") for line in lines),
                }
            )
    return "".join(chunks), files
