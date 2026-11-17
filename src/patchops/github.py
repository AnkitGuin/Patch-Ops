import base64
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .config import Settings
from .intake import SHA
from .models import Remote, Request, State
from .security import Blocked, safe_file
from .store import Job


class GitHub:
    def __init__(
        self, settings: Settings, transport: httpx.BaseTransport | None = None
    ):
        if settings.github_token is None:
            raise Blocked("GitHub credentials are not configured")
        self.client = httpx.Client(
            base_url="https://api.github.com",
            timeout=15,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            transport=transport,
        )

    def call(
        self,
        method: str,
        path: str,
        *,
        allowed: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> Any:
        try:
            with self.client.stream(method, path, **kwargs) as response:
                if response.status_code not in allowed:
                    raise Blocked(
                        f"GitHub request returned HTTP {response.status_code}; verify branch and token permissions"
                    )
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise Blocked("GitHub response exceeded the byte limit")
                return response.status_code, json.loads(data)
        except (httpx.HTTPError, ValueError) as exc:
            raise Blocked("GitHub request did not complete") from exc

    def create(self, job: Job, folder: Path, report: dict[str, Any]) -> str:
        request = Request.model_validate(job.request)
        if job.state != State.READY or not isinstance(request.source, Remote):
            raise Blocked("PR creation requires a validated GitHub migration")
        parsed = urlsplit(request.source.url)
        if parsed.hostname != "github.com" or not re.fullmatch(
            r"/[\w.-]+/[\w.-]+/?", parsed.path
        ):
            raise Blocked("PR creation supports public GitHub repository sources")
        repo = parsed.path.rstrip("/").removesuffix(".git").strip("/")
        owner = repo.split("/")[0]
        api = "/repos/" + repo
        _, metadata = self.call("GET", api)
        base = request.source.ref or metadata["default_branch"]
        if base == "HEAD":
            base = metadata["default_branch"]
        commit = report["snapshot"]["commit"]
        if not isinstance(commit, str) or not SHA.fullmatch(commit):
            raise Blocked("Snapshot has no verified GitHub commit")
        branch = "patchops/" + job.id
        _, existing = self.call(
            "GET",
            api + "/pulls",
            params={"state": "all", "head": f"{owner}:{branch}", "base": base},
        )
        if existing:
            return self.pr_url(existing[0], repo)
        _, current = self.call("GET", api + "/git/ref/heads/" + quote(base, safe="/"))
        if current["object"]["sha"] != commit:
            raise Blocked(
                "GitHub base branch has advanced. Rerun the migration against its current revision."
            )
        _, base_commit = self.call("GET", api + "/git/commits/" + commit)
        tree = []
        for change in report["files"]:
            path = change["path"]
            file = safe_file(folder / "working", path)
            data = file.read_bytes()
            from hashlib import sha256

            expected = next(
                item["after_sha256"]
                for item in reversed(report["provenance"])
                if item["path"] == path
            )
            if sha256(data).hexdigest() != expected:
                raise Blocked("Validated file content has changed")
            _, blob = self.call(
                "POST",
                api + "/git/blobs",
                allowed=(201,),
                json={"content": base64.b64encode(data).decode(), "encoding": "base64"},
            )
            tree.append(
                {
                    "path": path,
                    "mode": "100755" if file.stat().st_mode & 0o111 else "100644",
                    "type": "blob",
                    "sha": blob["sha"],
                }
            )
        _, updated_tree = self.call(
            "POST",
            api + "/git/trees",
            allowed=(201,),
            json={"base_tree": base_commit["tree"]["sha"], "tree": tree},
        )
        status, ref = self.call(
            "GET", api + "/git/ref/heads/" + branch, allowed=(200, 404)
        )
        if status == 200:
            _, prior = self.call("GET", api + "/git/commits/" + ref["object"]["sha"])
            if prior["tree"]["sha"] != updated_tree["sha"] or [
                item["sha"] for item in prior["parents"]
            ] != [commit]:
                raise Blocked(
                    "The existing migration branch does not match the validated patch"
                )
        else:
            _, revision = self.call(
                "POST",
                api + "/git/commits",
                allowed=(201,),
                json={
                    "message": "Migrate Pydantic v1 to v2 with PatchOps",
                    "tree": updated_tree["sha"],
                    "parents": [commit],
                },
            )
            self.call(
                "POST",
                api + "/git/refs",
                allowed=(201,),
                json={"ref": "refs/heads/" + branch, "sha": revision["sha"]},
            )
        checks = "\n".join(
            f"- {row['name']} — {row['status']}" for row in report["post"]
        )
        changes = "\n".join(f"- `{row['path']}`" for row in report["files"])
        counts: dict[str, int] = {}
        for item in report["provenance"]:
            counts[item["classification"]] = counts.get(item["classification"], 0) + 1
        baseline = "\n".join(
            f"- {row['name']} — {row['status']}" for row in report["baseline"]
        )
        scope = report["scope"]["migration"]
        body = (
            f"Pydantic {scope['source_version']} → {scope['target_version']}\n\n"
            f"## Changed files\n{changes}\n\nEdit counts {counts}\n\n"
            f"## Baseline\n{baseline}\n\n## Post-migration checks\n{checks}\n\n"
            f"PatchOps report ID `{job.id}`. Review the diff before merging."
        )
        status, pull = self.call(
            "POST",
            api + "/pulls",
            allowed=(201, 422),
            json={
                "title": "Migrate Pydantic v1 to v2",
                "head": branch,
                "base": base,
                "body": body,
            },
        )
        if status == 422:
            _, matching = self.call(
                "GET",
                api + "/pulls",
                params={"state": "all", "head": f"{owner}:{branch}", "base": base},
            )
            if not matching:
                raise Blocked("GitHub rejected pull request creation")
            pull = matching[0]
        return self.pr_url(pull, repo)

    @staticmethod
    def pr_url(pull: dict[str, Any], repo: str) -> str:
        url = pull.get("html_url", "")
        if not re.fullmatch(
            r"https://github\.com/" + re.escape(repo) + r"/pull/\d+", url, re.IGNORECASE
        ):
            raise Blocked("GitHub returned an unexpected pull request URL")
        return url
