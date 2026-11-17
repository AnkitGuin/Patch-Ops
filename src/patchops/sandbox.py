import json
import re
import tarfile
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryFile
from time import monotonic
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from .config import Settings
from .models import Check, CheckStatus
from .process import run
from .security import Blocked, redact, safe_file
from .store import iso, now
from .validation import pytest_results, signature


def unavailable(name: str, reason: str, status: CheckStatus = "NOT_RUN") -> Check:
    timestamp = iso(now())
    return Check(
        name=name,
        status=status,
        started_at=timestamp,
        finished_at=timestamp,
        stderr=reason,
    )


class Sandbox(Protocol):
    def checks(
        self,
        root: Path,
        names: list[str],
        seconds: float,
        checkpoint: Callable[[], None],
        focus: list[str] | None = None,
    ) -> list[Check]: ...


class DockerSandbox:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.secrets = tuple(
            secret.get_secret_value()
            for secret in (
                settings.api_token,
                settings.llm_api_key,
                settings.github_token,
            )
            if secret
        )

    def checks(
        self,
        root: Path,
        names: list[str],
        seconds: float,
        checkpoint: Callable[[], None],
        focus: list[str] | None = None,
    ) -> list[Check]:
        started = monotonic()
        reason = ""
        if self.settings.install_network != "none":
            return [
                unavailable(
                    "install",
                    "Build a wheelhouse image for dependencies. Runtime network must be none.",
                    "INFRA_ERROR",
                )
            ]
        try:
            image = run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    self.settings.sandbox_image,
                ],
                seconds=min(12, seconds),
                checkpoint=checkpoint,
            )
        except FileNotFoundError:
            return [
                unavailable(
                    "install", "Docker executable is unavailable", "INFRA_ERROR"
                )
            ]
        image_id = image.stdout.decode(errors="replace").strip()
        if image.code or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            return [
                unavailable(
                    "install",
                    "Docker daemon or configured local sandbox image is unavailable",
                    "INFRA_ERROR",
                )
            ]
        name = "patchops-" + uuid4().hex
        settings = self.settings
        # Docker CLI arguments are operator settings, with the image resolved to an immutable local ID.
        argv = [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "-i",
            "--name",
            name,
            "--label",
            "io.patchops.sandbox=true",
            "--user",
            "10001:10001",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--network=none",
            "--init",
            f"--pids-limit={settings.sandbox_pids}",
            f"--cpus={settings.sandbox_cpus}",
            f"--memory={settings.sandbox_memory}",
            f"--memory-swap={settings.sandbox_memory}",
            "--log-driver=none",
            "--ulimit",
            "nofile=1024:1024",
            "--tmpfs",
            f"/work:rw,exec,nosuid,nodev,size={settings.sandbox_disk},uid=10001,gid=10001",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=48m,mode=1777",
            image_id,
            json.dumps(names),
            str(settings.command_seconds),
            json.dumps(focus or []),
        ]
        try:
            with TemporaryFile() as stream:
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    for file in sorted(root.rglob("*")):
                        checkpoint()
                        if file.is_symlink() or not (file.is_file() or file.is_dir()):
                            raise Blocked(
                                "Snapshot contains an unsafe filesystem entry"
                            )
                        arcname = file.relative_to(root).as_posix()
                        if file.is_file():
                            safe_file(root, arcname)
                        archive.add(file, arcname=arcname, recursive=False)
                stream.seek(0)
                result = run(
                    argv,
                    seconds=max(0.05, seconds - (monotonic() - started)),
                    stdin=stream,
                    checkpoint=checkpoint,
                    limit=900_000,
                )
        finally:
            run(["docker", "rm", "-f", name], seconds=8)
        records: list[Check] = []
        environment = {
            "image": image_id,
            "python": "3.12",
            "network": "none",
            "dependency_source": "image wheelhouse",
        }
        try:
            for line in result.stdout.splitlines():
                data = json.loads(line)
                xml = data.pop("junit", "").encode()
                for field in ("stdout", "stderr"):
                    data[field] = redact(data[field], self.secrets)
                data["argv"] = [redact(arg, self.secrets) for arg in data["argv"]]
                if data["name"] == "pytest":
                    data["outcomes"], diagnostics = pytest_results(xml)
                    data["outcomes"] = {
                        redact(key, self.secrets): value
                        for key, value in data["outcomes"].items()
                    }
                    data["signatures"] = [
                        redact(item, self.secrets) for item in diagnostics
                    ]
                if not data.get("signatures") and data["status"] == "FAILED":
                    combined = data["stdout"] + "\n" + data["stderr"]
                    data["signatures"] = [
                        signature(line)
                        for line in combined.splitlines()
                        if re.search(r"error|fail|assert", line, re.IGNORECASE)
                    ][:30]
                record = Check.model_validate({**data, "environment": environment})
                expected = ["install", "resolved", *names]
                if (
                    len(records) >= len(expected)
                    or record.name != expected[len(records)]
                ):
                    raise ValueError("Unexpected command result")
                records.append(record)
        except (ValueError, TypeError, KeyError, ValidationError):
            reason = "Docker runner returned invalid or incomplete evidence"
        if result.timed_out:
            reason = "Docker validation exceeded the job deadline"
        elif result.code or result.truncated:
            reason = "Docker runner failed or exceeded its evidence limit"
        elif (
            records and records[0].status == "PASSED" and len(records) != 2 + len(names)
        ):
            reason = "Docker runner omitted required command evidence"
        if reason and not records:
            records.append(
                unavailable(
                    "install",
                    reason,
                    "TIMED_OUT" if result.timed_out else "INFRA_ERROR",
                )
            )
        completed = {record.name for record in records}
        if reason:
            records.append(
                unavailable(
                    "runner", reason, "TIMED_OUT" if result.timed_out else "INFRA_ERROR"
                )
            )
        elif "install" not in completed:
            records.append(
                unavailable(
                    "install", "Docker produced no install evidence", "INFRA_ERROR"
                )
            )
        records.extend(
            unavailable(
                check,
                reason
                or "Dependency installation or preceding execution did not complete",
            )
            for check in names
            if check not in completed
        )
        return records
