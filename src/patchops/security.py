import os
import re
from pathlib import Path, PurePosixPath


class Blocked(RuntimeError):
    """A safe operation could not be established."""


class Cancelled(RuntimeError):
    """Cancellation requested by the operator."""


SECRET_PATTERNS = [
    r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}",
    r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,}|AKIA[A-Z0-9]{16})\b",
    r"(?is)-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    r"(?im)\b(?:password|api[_-]?key|secret|token)\s*[:=]\s*[^\s,;]+",
    r"(?i)https?://[^\s/]+:[^\s/]+@",
]
EXCLUDED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
    "vendor",
    "vendored",
    ".svn",
    ".hg",
    ".ssh",
    ".aws",
    ".eggs",
    ".patchops",
}
SENSITIVE = {
    ".netrc",
    ".pypirc",
    ".npmrc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "secrets",
}


def redact(value: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        value = re.sub(pattern, "[REDACTED]", value)
    return value


def relative(name: str) -> PurePosixPath:
    parts = name.split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.endswith((".", " "))
        or part.partition(".")[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        for part in parts
    ) or re.search(r'[\\:\x00-\x1f\x7f<>|?*"]', name):
        raise Blocked("Unsafe repository-relative path")
    return PurePosixPath(name)


def excluded(path: PurePosixPath) -> bool:
    return any(
        part.lower() in EXCLUDED | SENSITIVE
        or part.lower().startswith((".env", ".gitmodules"))
        or part.lower().endswith((".egg-info", ".pem", ".key", ".p12", ".pfx"))
        for part in path.parts
    )


def test_path(path: PurePosixPath) -> bool:
    return (
        any(part.lower() in {"tests", "test", "testing"} for part in path.parts[:-1])
        or path.name in {"conftest.py", "test.py", "tests.py"}
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
    )


def safe_file(root: Path, name: str) -> Path:
    path = root
    for part in relative(name).parts:
        path = path / part
        if path.is_symlink():
            raise Blocked("Symlink in artifact or workspace path")
    if not path.is_file():
        raise Blocked("Expected an existing regular file")
    return path


def isolated_env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP")
    return {key: os.environ[key] for key in keep if key in os.environ}
