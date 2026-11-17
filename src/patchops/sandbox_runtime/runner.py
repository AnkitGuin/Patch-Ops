"""Trusted image entrypoint. All repository subprocesses run inside the container."""

import json
import os
import signal
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from time import monotonic

WORK = Path("/work")
REPO = WORK / "repo"
PY = str(WORK / "venv/bin/python")
COMMANDS = {
    "syntax": [PY, "-m", "compileall", "-q", "."],
    "pytest": [
        PY,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit1",
        "--junitxml=/work/patchops-results.xml",
    ],
    "ruff": ["/work/venv/bin/ruff", "check", "--no-cache", "."],
    "format": ["/work/venv/bin/ruff", "format", "--check", "--no-cache", "."],
    "mypy": ["/work/venv/bin/mypy", "--cache-dir=/work/mypy-cache", "."],
    "migration": [
        PY,
        "-I",
        "-c",
        "import importlib.metadata; print(importlib.metadata.version('pydantic'))",
    ],
    "resolved": [
        PY,
        "-I",
        "-m",
        "pip",
        "list",
        "--format=freeze",
        "--disable-pip-version-check",
    ],
}


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def execute(name: str, argv: list[str], seconds: int) -> dict[str, object]:
    started, start = timestamp(), monotonic()
    buffers: list[bytearray] = [bytearray(), bytearray()]

    def drain(stream, buffer: bytearray) -> None:
        while chunk := stream.read1(65536):
            buffer.extend(chunk[: max(0, 12_000 - len(buffer))])

    with subprocess.Popen(
        argv,
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=sys.platform != "win32",
    ) as proc:
        threads = [
            Thread(target=drain, args=(stream, buffer), daemon=True)
            for stream, buffer in zip((proc.stdout, proc.stderr), buffers, strict=True)
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                if sys.platform != "win32":
                    os.killpg(proc.pid, signal.SIGKILL)
                elif proc.poll() is None:
                    proc.kill()
            except ProcessLookupError:
                pass
            proc.wait()
            for thread in threads:
                thread.join(timeout=1)
    result: dict[str, object] = {
        "name": name,
        "argv": argv,
        "status": "TIMED_OUT"
        if timed_out
        else "PASSED"
        if proc.returncode == 0
        else "FAILED",
        "exit_code": proc.returncode,
        "started_at": started,
        "finished_at": timestamp(),
        "duration_ms": int((monotonic() - start) * 1000),
        "stdout": buffers[0].decode(errors="replace"),
        "stderr": buffers[1].decode(errors="replace"),
    }
    xml = WORK / "patchops-results.xml"
    if (
        name == "pytest"
        and xml.is_file()
        and not xml.is_symlink()
        and xml.stat().st_size <= 500_000
    ):
        result["junit"] = xml.read_text(errors="replace")
    return result


def installable() -> bool:
    if (REPO / "setup.py").is_file():
        return True
    pyproject = REPO / "pyproject.toml"
    if not pyproject.is_file():
        return False
    import tomllib

    with pyproject.open("rb") as stream:
        data = tomllib.load(stream)
    return (
        "project" in data or "build-system" in data or "poetry" in data.get("tool", {})
    )


def main() -> None:
    checks = json.loads(sys.argv[1])
    seconds = int(sys.argv[2])
    focus = json.loads(sys.argv[3])
    REPO.mkdir()
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
        archive.extractall(REPO, filter="data")
    os.environ["HOME"] = "/work"
    # Offline resolution is enforced by network isolation independently of pip flags.
    install = [
        PY,
        "-I",
        "-m",
        "pip",
        "--isolated",
        "install",
        "--disable-pip-version-check",
        "--no-index",
        "--find-links=/wheelhouse",
        "--no-build-isolation",
    ]
    tool_install = [*install, "-r", "/opt/patchops/tools.txt"]
    requirements = REPO / "requirements.txt"
    if requirements.is_file():
        install.extend(["-r", "requirements.txt"])
    if installable():
        install.append(".")
    has_requirements = requirements.is_file() or install[-1] == "."
    init = execute(
        "install", [sys.executable, "-I", "-m", "venv", "/work/venv"], seconds
    )
    if init["status"] == "PASSED":
        init = execute("install", tool_install, seconds)
    if init["status"] == "PASSED" and has_requirements:
        init = execute("install", install, seconds)
    print(json.dumps(init), flush=True)
    if init["status"] != "PASSED":
        return
    print(json.dumps(execute("resolved", COMMANDS["resolved"], seconds)), flush=True)
    os.environ["PYTHONPATH"] = "/work/repo:/work/repo/src"
    for check in checks:
        command = COMMANDS[check]
        if check == "pytest" and focus:
            command = [*command, "--", *focus]
        print(json.dumps(execute(check, command, seconds)), flush=True)


if __name__ == "__main__":
    main()
