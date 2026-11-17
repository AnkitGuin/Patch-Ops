import ast
import importlib.util
import re
import tomllib
from collections.abc import Callable

import libcst as cst

from .intake import Snapshot
from .migrations.base import Plugin
from .migrations.pydantic_v2 import REQUIREMENTS
from .models import Analysis, Request
from .security import excluded, redact, relative, test_path

CONFIG = {
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "pytest.ini",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".mypy.ini",
    "Pipfile",
    ".gitlab-ci.yml",
}
LOCKS = {"poetry.lock", "uv.lock", "pdm.lock", "Pipfile.lock", "requirements.lock"}


def inspect_repo(
    snapshot: Snapshot, request: Request, plugin: Plugin, checkpoint: Callable[[], None]
) -> Analysis:
    result = Analysis(
        repository_id=snapshot.digest,
        commit_sha=snapshot.commit,
        files_skipped=snapshot.skipped,
        warnings=snapshot.warnings.copy(),
    )
    imports: dict[str, set[str]] = {}
    modules = {}
    for file in sorted(snapshot.root.rglob("*")):
        if not file.is_file():
            continue
        checkpoint()
        path = file.relative_to(snapshot.root).as_posix()
        name = relative(path)
        result.inventory.append(path)
        if excluded(name):
            result.files_skipped += 1
            continue
        if file.name in LOCKS:
            result.lockfiles.append(path)
        if (
            file.name in CONFIG
            or REQUIREMENTS.fullmatch(file.name)
            or path.startswith(".github/workflows/")
        ):
            result.config_files.append(path)
        if test_path(name):
            result.test_inventory.append(path)
        if not (
            file.suffix == ".py"
            or file.name in CONFIG
            or REQUIREMENTS.fullmatch(file.name)
        ):
            continue
        try:
            source = file.read_bytes().decode("utf-8")
            if "\x00" in source:
                raise UnicodeError()
        except UnicodeError:
            result.files_skipped += 1
            result.warnings.append(f"Cannot analyze binary or non-UTF-8 file {path}")
            continue
        result.files_scanned += 1
        try:
            result.migration_signals.extend(plugin.inspect(path, source, request))
            if file.suffix == ".py":
                tree = ast.parse(source)
                parts = list(name.with_suffix("").parts)
                if parts[0] == "src":
                    parts.pop(0)
                is_package = parts[-1:] == ["__init__"]
                if is_package:
                    parts.pop()
                module = ".".join(parts)
                modules[module] = path
                package = module if is_package else module.rpartition(".")[0]
                found: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        found.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        base = node.module or ""
                        if node.level:
                            try:
                                base = importlib.util.resolve_name(
                                    "." * node.level + base, package
                                )
                            except ImportError:
                                continue
                        found.add(base)
                        found.update(f"{base}.{alias.name}" for alias in node.names)
                imports[path] = found
                result.symbols[path] = [
                    f"{type(node).__name__} {node.name}:{node.lineno}"
                    for node in ast.walk(tree)
                    if isinstance(
                        node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                    )
                ]
            if REQUIREMENTS.fullmatch(file.name):
                result.dependencies.extend(
                    redact(line.strip())
                    for line in source.splitlines()
                    if line.strip() and not line.lstrip().startswith(("#", "-"))
                )
            if path == "pyproject.toml":
                meta = tomllib.loads(source)
                result.python_version = meta.get("project", {}).get("requires-python")
                result.dependencies.extend(
                    redact(str(item))
                    for item in meta.get("project", {}).get("dependencies", [])
                )
                result.dependencies.extend(
                    redact(f"{key} {value}")
                    for key, value in meta.get("tool", {})
                    .get("poetry", {})
                    .get("dependencies", {})
                    .items()
                )
            elif path in {"setup.cfg", "setup.py"}:
                python = re.search(r"(?m)python_requires\s*=\s*([^\n]+)", source)
                if python and result.python_version is None:
                    result.python_version = redact(python[1].strip())
        except (
            SyntaxError,
            cst.ParserSyntaxError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            result.warnings.append(f"Could not parse {path} ({type(exc).__name__})")
    result.local_import_graph = {
        path: sorted(
            {
                modules[entry]
                for entry in entries
                if entry in modules and modules[entry] != path
            }
        )
        for path, entries in imports.items()
    }
    result.dependencies = sorted(set(result.dependencies))
    return result
