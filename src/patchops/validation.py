import hashlib
import re
from xml.etree import ElementTree

from .models import Analysis, Check, Validation

COMMANDS = {
    "syntax": ["python", "-m", "compileall", "-q", "."],
    "pytest": [
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit1",
        "--junitxml=/work/patchops-results.xml",
    ],
    "ruff": ["ruff", "check", "--no-cache", "."],
    "format": ["ruff", "format", "--check", "--no-cache", "."],
    "mypy": ["mypy", "--cache-dir=/work/mypy-cache", "."],
    "migration": [
        "python",
        "-c",
        "import importlib.metadata; print(importlib.metadata.version('pydantic'))",
    ],
}


def configured(analysis: Analysis, policy: Validation, pyproject: str) -> list[str]:
    files = set(analysis.config_files)
    tools = ["syntax"]
    if policy.run_tests and (
        analysis.test_inventory or "pytest.ini" in files or "[tool.pytest" in pyproject
    ):
        tools.append("pytest")
    ruff = (
        policy.run_ruff
        if policy.run_ruff is not None
        else bool({"ruff.toml", ".ruff.toml"} & files or "[tool.ruff" in pyproject)
    )
    if ruff:
        tools.extend(["ruff", "format"])
    mypy = (
        policy.run_mypy
        if policy.run_mypy is not None
        else bool({"mypy.ini", ".mypy.ini"} & files or "[tool.mypy" in pyproject)
    )
    if mypy:
        tools.append("mypy")
    return list(dict.fromkeys([*tools, *policy.extra_checks, "migration"]))


def signature(text: str) -> str:
    text = re.sub(r":\d+(?::\d+)?", ":#", text.strip())
    text = re.sub(r"0x[0-9a-f]+", "0x#", text)
    text = re.sub(r"/work/(?:repo|\.venv)/", "", text)
    return text[:500]


def pytest_results(data: bytes) -> tuple[dict[str, str], list[str]]:
    if not data or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        return {}, []
    try:
        tree = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return {}, []
    outcomes = {}
    signatures = []
    for case in tree.iter("testcase"):
        node = case.get("classname", "") + "::" + case.get("name", "")
        node = re.sub(r"[^\x20-\x7e]", "?", node)[:300]
        failures = case.findall("failure") + case.findall("error")
        if failures:
            detail = "; ".join(
                signature(failure.get("message", "")) for failure in failures
            )
            outcome = "FAILED:" + hashlib.sha256(detail.encode()).hexdigest()[:16]
            signatures.append(f"{node}: {detail}")
        else:
            outcome = "SKIPPED" if case.find("skipped") is not None else "PASSED"
        if node not in outcomes or outcome.startswith("FAILED"):
            outcomes[node] = outcome
    return outcomes, signatures


def compare(baseline: list[Check], post: list[Check]) -> list[dict[str, str]]:
    before = {item.name: item for item in baseline}
    after = {item.name: item for item in post}
    rows = []
    skipped = {"NOT_RUN", "NOT_CONFIGURED", "TIMED_OUT", "INFRA_ERROR"}
    for name in dict.fromkeys([*before, *after]):
        old, new = before.get(name), after.get(name)
        if not old or not new or old.status in skipped or new.status in skipped:
            result = "NOT_RUN"
        elif any(
            old.environment.get(key) != new.environment.get(key)
            for key in ("image", "python", "network")
        ):
            result = "NOT_COMPARABLE"
        elif name == "pytest" and old.outcomes and new.outcomes:
            for node in sorted(old.outcomes.keys() | new.outcomes.keys()):
                left, right = old.outcomes.get(node), new.outcomes.get(node)
                if left is None or right is None or "SKIPPED" in {left, right}:
                    change = "NOT_COMPARABLE"
                elif right == "PASSED":
                    change = (
                        "STILL_PASSING"
                        if left == right
                        else "RESOLVED_BASELINE_FAILURE"
                    )
                else:
                    change = "PRE_EXISTING_FAILURE" if left == right else "NEW_FAILURE"
                rows.append({"check": name, "test": node, "result": change})
            prior_failure = any(
                value.startswith("FAILED") for value in old.outcomes.values()
            )
            current_failure = any(
                value.startswith("FAILED") for value in new.outcomes.values()
            )
            if (old.status == "FAILED" and not prior_failure) or (
                new.status == "FAILED" and not current_failure
            ):
                result = "NOT_COMPARABLE"
            else:
                continue
        elif name == "pytest" and new.status == "PASSED":
            result = "NOT_COMPARABLE"
        elif old.status == new.status == "PASSED":
            result = "STILL_PASSING"
        elif new.status == "PASSED":
            result = "RESOLVED_BASELINE_FAILURE"
        elif old.status == "PASSED" or set(new.signatures) - set(old.signatures):
            result = "NEW_FAILURE"
        elif old.signatures and set(old.signatures) == set(new.signatures):
            result = "PRE_EXISTING_FAILURE"
        else:
            result = "NOT_COMPARABLE"
        rows.append({"check": name, "test": "", "result": result})
    return rows
