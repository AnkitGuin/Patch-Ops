import hashlib
import os
import re
import shutil
import stat
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile
from typing import IO
from urllib.parse import urlsplit

from .config import Settings
from .models import Local, Remote, Request, Zip
from .process import run
from .security import Blocked, excluded, isolated_env, relative, safe_file

MIB = 1024 * 1024
GIT_REF = re.compile(r"(?:HEAD|[A-Za-z0-9][A-Za-z0-9_./-]{0,199})\Z")
SHA = re.compile(r"[a-f0-9]{40}(?:[a-f0-9]{24})?\Z")


@dataclass
class Snapshot:
    root: Path
    commit: str | None
    digest: str
    branch: str | None
    skipped: int
    warnings: list[str]


def git_env() -> dict[str, str]:
    return {
        **isolated_env(),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_COUNT": "4",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": os.devnull,
        "GIT_CONFIG_KEY_1": "http.followRedirects",
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "credential.helper",
        "GIT_CONFIG_VALUE_2": "",
        "GIT_CONFIG_KEY_3": "core.attributesFile",
        "GIT_CONFIG_VALUE_3": os.devnull,
    }


def git(
    cwd: Path,
    args: list[str],
    checkpoint: Callable[[], None],
    output: IO[bytes] | None = None,
    limit: int = 1_000_000,
    stdin: IO[bytes] | None = None,
) -> str:
    try:
        result = run(
            ["git", *args],
            cwd=cwd,
            env=git_env(),
            seconds=90,
            checkpoint=checkpoint,
            output=output,
            stdin=stdin,
            limit=limit,
        )
    except FileNotFoundError as exc:
        raise Blocked("Git is unavailable") from exc
    if result.code or result.timed_out or result.truncated:
        raise Blocked("Git could not resolve or export the selected revision")
    try:
        return result.stdout.decode("utf-8").removesuffix("\n")
    except UnicodeDecodeError as exc:
        raise Blocked("Git tree contains a non-UTF-8 path") from exc


def valid_ref(ref: str | None) -> None:
    if ref and (
        not GIT_REF.fullmatch(ref)
        or ".." in ref
        or "//" in ref
        or ref.endswith(("/", ".lock"))
    ):
        raise Blocked("Invalid Git ref")


def validate_source(request: Request, settings: Settings) -> str:
    source = request.source
    if isinstance(source, Zip):
        return Path(source.label.replace("\\", "/")).name or "repository.zip"
    valid_ref(source.ref)
    if isinstance(source, Local):
        try:
            path = Path(source.path).resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise Blocked("Local repository is unavailable") from exc
        if not path.is_dir() or not any(
            path.is_relative_to(root.resolve()) for root in settings.allowed_roots
        ):
            raise Blocked("Local repository is outside the allowed roots")
        data_root = settings.data_root.resolve()
        if path.is_relative_to(data_root):
            raise Blocked("Select a repository outside the PatchOps data directory")
        source.path = str(path)
        return path.name
    try:
        url = urlsplit(source.url)
        allowed = (
            url.scheme == "https"
            and url.hostname in settings.git_hosts
            and url.netloc == url.hostname
        )
    except ValueError as exc:
        raise Blocked("Invalid Git URL") from exc
    if (
        not allowed
        or url.query
        or url.fragment
        or not re.fullmatch(r"/[\w.-]+(?:/[\w.-]+)+/?", url.path)
    ):
        raise Blocked(
            "Use a public HTTPS Git URL on an allowed host, without credentials"
        )
    if any(part in {".", ".."} for part in url.path.split("/")):
        raise Blocked("Invalid Git URL path")
    return url.path.rstrip("/").removesuffix(".git").strip("/")


class Extract:
    def __init__(self, root: Path, settings: Settings, checkpoint: Callable[[], None]):
        self.root = root
        self.settings = settings
        self.checkpoint = checkpoint
        self.count = self.total = self.skipped = 0
        self.names: dict[str, str] = {}
        self.members: set[str] = set()
        self.submodules: list[str] = []

    def member(
        self, name: str, size: int, directory: bool, stream: IO[bytes] | None
    ) -> None:
        self.checkpoint()
        path = relative(name.removesuffix("/") if directory else name)
        self.count += 1
        self.total += size
        if (
            self.count > self.settings.max_files
            or self.total > self.settings.max_repo_mb * MIB
        ):
            raise Blocked(
                "Repository exceeds the file count or uncompressed byte limit"
            )
        normalized = path.as_posix()
        if normalized in self.members:
            raise Blocked("Duplicate or case-conflicting archive path")
        self.members.add(normalized)
        for part in (path, *path.parents):
            key = part.as_posix()
            if self.names.setdefault(key.casefold(), key) != key:
                raise Blocked("Duplicate or case-conflicting archive path")
        if path.name == ".gitmodules":
            self.submodules.append(normalized)
        if excluded(path) or size > self.settings.max_file_mb * MIB:
            self.skipped += 1
            return
        if directory:
            return
        assert stream is not None
        data = stream.read(size + 1)
        if len(data) != size:
            raise Blocked("Archive member size does not match its header")
        destination = self.root.joinpath(*path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as target:
                target.write(data)
        except OSError as exc:
            raise Blocked("Conflicting archive paths") from exc


def extract_zip(path: Path, extract: Extract) -> None:
    if path.stat().st_size > extract.settings.max_repo_mb * MIB:
        raise Blocked("ZIP exceeds the compressed byte limit")
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                mode = stat.S_IFMT(member.external_attr >> 16)
                if mode not in (0, stat.S_IFDIR if member.is_dir() else stat.S_IFREG):
                    raise Blocked("ZIP contains a symlink or special file")
                if member.flag_bits & 1 or "\x00" in member.orig_filename:
                    raise Blocked("ZIP contains an encrypted or invalid entry")
                if member.is_dir():
                    extract.member(member.filename, 0, True, None)
                else:
                    with archive.open(member) as stream:
                        extract.member(member.filename, member.file_size, False, stream)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        if isinstance(exc, Blocked):
            raise
        raise Blocked("Invalid or unsupported ZIP archive") from exc


def extract_git(repository: Path, commit: str, extract: Extract) -> None:
    entries = git(
        repository,
        ["ls-tree", "-r", "-l", "-z", "--full-tree", commit],
        extract.checkpoint,
        limit=8_000_000,
    ).split("\0")
    blobs = []
    for entry in filter(None, entries):
        extract.checkpoint()
        header, _, name = entry.partition("\t")
        fields = header.split()
        if len(fields) != 4:
            raise Blocked("Git returned invalid tree metadata")
        mode, kind, oid, size_text = fields
        path = relative(name)
        if kind == "commit" and mode == "160000":
            extract.submodules.append(name)
            extract.member(name, 0, True, None)
            continue
        if kind != "blob" or mode not in {"100644", "100755"} or not SHA.fullmatch(oid):
            raise Blocked("Git revision contains a symlink or special file")
        size = int(size_text)
        if excluded(path) or size > extract.settings.max_file_mb * MIB:
            extract.member(name, size, False, None)
        else:
            blobs.append((name, mode, oid, size))
        if len(blobs) + extract.count > extract.settings.max_files:
            raise Blocked("Repository exceeds the file count limit")
    total = sum(size for _, _, _, size in blobs)
    if extract.total + total > extract.settings.max_repo_mb * MIB:
        raise Blocked("Repository exceeds the uncompressed byte limit")
    if not blobs:
        return
    with TemporaryFile() as instructions, TemporaryFile() as output:
        instructions.write("".join(oid + "\n" for _, _, oid, _ in blobs).encode())
        instructions.seek(0)
        git(
            repository,
            ["cat-file", "--batch"],
            extract.checkpoint,
            output,
            limit=total + 128 * len(blobs),
            stdin=instructions,
        )
        output.seek(0)
        for name, mode, oid, size in blobs:
            if output.readline(128) != f"{oid} blob {size}\n".encode():
                raise Blocked("Git blob did not match the fixed tree metadata")
            # The extractor needs a bounded stream because the next Git object follows this one.
            from io import BytesIO

            data = output.read(size)
            if output.read(1) != b"\n":
                raise Blocked("Git blob stream was truncated")
            extract.member(name, size, False, BytesIO(data))
            if mode == "100755":
                extract.root.joinpath(*relative(name).parts).chmod(0o755)


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        name = path.relative_to(root).as_posix()
        safe_file(root, name)
        digest.update(name.encode() + b"\0")
        digest.update(str(bool(path.stat().st_mode & 0o111)).encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def snapshot(
    request: Request, folder: Path, settings: Settings, checkpoint: Callable[[], None]
) -> Snapshot:
    validate_source(request, settings)
    root = folder / "snapshot"
    root.mkdir()
    extract = Extract(root, settings, checkpoint)
    source = request.source
    commit = branch = None
    if isinstance(source, Zip):
        extract_zip(folder / "input.zip", extract)
        children = list(root.iterdir())
        if (
            len(children) == 1
            and children[0].is_dir()
            and any(
                (children[0] / name).is_file()
                for name in (
                    "pyproject.toml",
                    "requirements.txt",
                    "setup.py",
                    "setup.cfg",
                )
            )
        ):
            wrapper = children[0]
            for item in wrapper.iterdir():
                item.rename(root / item.name)
            wrapper.rmdir()
    else:
        remote_repo = folder / "git-source"
        try:
            if isinstance(source, Remote):
                remote_repo.mkdir()
                git(remote_repo, ["init", "--bare", "--template="], checkpoint)
                branch = source.ref or "HEAD"

                def download_limit() -> None:
                    checkpoint()
                    objects = remote_repo / "objects"
                    total = 0
                    for path in objects.rglob("*"):
                        try:
                            if path.is_file():
                                total += path.stat().st_size
                        except FileNotFoundError:
                            # Git may rename a temporary pack while it is being counted.
                            continue
                    if total > settings.max_repo_mb * MIB:
                        raise Blocked("Git download exceeds the repository byte limit")

                git(
                    remote_repo,
                    [
                        "fetch",
                        "--depth=1",
                        "--no-tags",
                        "--no-recurse-submodules",
                        "--",
                        source.url,
                        branch,
                    ],
                    download_limit,
                )
                repository = remote_repo
                revision = "FETCH_HEAD"
            else:
                repository = Path(source.path)
                if (
                    Path(
                        git(repository, ["rev-parse", "--show-toplevel"], checkpoint)
                    ).resolve()
                    != repository
                ):
                    raise Blocked("Select the root of a Git repository")
                revision = source.ref or "HEAD"
                branch = source.ref
            commit = git(
                repository,
                ["rev-parse", "--verify", "--end-of-options", revision + "^{commit}"],
                checkpoint,
            )
            if not SHA.fullmatch(commit):
                raise Blocked("Git did not return a commit SHA")
            extract_git(repository, commit, extract)
        finally:
            shutil.rmtree(remote_repo, ignore_errors=True)
    if not any(path.is_file() for path in root.rglob("*")):
        raise Blocked("Repository snapshot is empty")
    warnings = []
    if extract.skipped:
        warnings.append(
            f"{extract.skipped} excluded or oversized entries omitted from snapshot"
        )
    if extract.submodules:
        warnings.append("Git submodules are omitted from validation and require review")
    return Snapshot(root, commit, fingerprint(root), branch, extract.skipped, warnings)
