import json
import os

import httpx
import streamlit as st

API = os.getenv("PATCHOPS_API_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("PATCHOPS_API_TOKEN", "")
PREFIX = "/api/v1/jobs"
TERMINAL = {
    "READY_FOR_REVIEW",
    "READY_FOR_REVIEW_WITH_WARNINGS",
    "FAILED",
    "BLOCKED",
    "CANCELLED",
}


def call(method: str, path: str, quiet: bool = False, **kwargs):
    headers = kwargs.pop("headers", {})
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    try:
        response = httpx.request(
            method, API + path, headers=headers, timeout=60, trust_env=False, **kwargs
        )
        if quiet and response.status_code == 404:
            return None
        response.raise_for_status()
        if path.find("/artifacts/") != -1:
            return response.content
        return response.json()
    except httpx.HTTPStatusError as exc:
        try:
            st.error(str(exc.response.json().get("detail", "Request failed")))
        except ValueError:
            st.error(f"API returned HTTP {exc.response.status_code}.")
    except httpx.RequestError:
        st.error("The PatchOps API is unavailable. Check the API process and address.")
    return None


def new_job():
    st.header("New migration")
    kind = st.radio(
        "Repository source",
        ["local", "git_url", "zip_upload"],
        format_func={
            "local": "Local Git",
            "git_url": "Public Git URL",
            "zip_upload": "ZIP archive",
        }.get,
        horizontal=True,
    )
    with st.form("migration"):
        value = (
            st.text_input("Repository path" if kind == "local" else "Git HTTPS URL")
            if kind != "zip_upload"
            else ""
        )
        ref = (
            st.text_input("Git ref", placeholder="HEAD") if kind != "zip_upload" else ""
        )
        archive = (
            st.file_uploader("Repository archive", type=["zip"])
            if kind == "zip_upload"
            else None
        )
        st.selectbox("Migration family", ["Pydantic v1 → v2"])
        left, right = st.columns(2)
        source_version = left.text_input("Source version", value="1.x")
        target_version = right.text_input("Target version", value="2.x")
        objective = st.text_area(
            "Objective", value="Migrate Pydantic v1 usage to v2 and preserve behavior."
        )
        mode = st.selectbox("Workflow", ["migrate", "plan", "scan", "dry-run"])
        dep = st.checkbox("Update Pydantic dependency declarations", value=True)
        semantic = st.checkbox(
            "Authorize scoped model-assisted edits and source context sharing"
        )
        tests = st.checkbox("Run discovered tests", value=True)
        ruff = st.selectbox("Ruff", ["Discover", "Enable", "Disable"])
        mypy = st.selectbox("mypy", ["Discover", "Enable", "Disable"])
        st.caption(
            "Target code runs in an isolated Docker container. Dry runs produce a patch with validation skipped."
        )
        if st.form_submit_button("Queue migration", type="primary"):
            if kind == "zip_upload" and archive is None:
                st.error("Choose a ZIP archive.")
                return
            if kind != "zip_upload" and not value:
                st.error("Enter a repository location.")
                return
            source: dict[str, str | None] = {"type": kind}
            if archive:
                source["label"] = archive.name
            else:
                source["path" if kind == "local" else "url"] = value
                source["ref"] = ref or None
            flags = {"Discover": None, "Enable": True, "Disable": False}
            body = {
                "source": source,
                "migration": {
                    "source_version": source_version,
                    "target_version": target_version,
                },
                "objective": objective,
                "mode": mode,
                "constraints": {
                    "allow_dependency_update": dep,
                    "allow_semantic_edits": semantic,
                },
                "validation": {
                    "run_tests": tests,
                    "run_ruff": flags[ruff],
                    "run_mypy": flags[mypy],
                },
            }
            created = (
                call(
                    "POST",
                    PREFIX + "/zip",
                    content=archive.getvalue(),
                    headers={
                        "Content-Type": "application/zip",
                        "X-PatchOps-Request": json.dumps(body),
                    },
                )
                if archive
                else call("POST", PREFIX, json=body)
            )
            if created:
                st.success(f"Queued job {created['id']}")


def review(job):
    path = PREFIX + "/" + job["id"]
    st.subheader(job["label"])
    st.caption(job["id"])
    st.write(job["state"].replace("_", " ").title())
    if job["state"] not in TERMINAL and st.button(
        "Request cancellation", disabled=job["cancel_requested"]
    ):
        call("POST", path + "/cancel")
        st.rerun()
    if st.button("Refresh job"):
        st.rerun()
    events, repo, diff, validation, report = st.tabs(
        ["Timeline", "Repository & plan", "Diff", "Validation", "Report"]
    )
    with events:
        rows = call("GET", path + "/events?limit=500")
        if rows:
            st.dataframe(
                [
                    {
                        "Time": row["time"],
                        "Stage": row["stage"],
                        "Event": row["type"],
                        "Detail": row["data"].get(
                            "message", row["data"].get("phase", "")
                        ),
                    }
                    for row in rows
                ],
                width="stretch",
                hide_index=True,
            )
    with repo:
        analysis = call("GET", path + "/analysis", quiet=True)
        plan = call("GET", path + "/plan", quiet=True)
        if analysis:
            st.write(
                f"Revision `{analysis['commit_sha'] or analysis['repository_id']}`"
            )
            st.write(
                f"{analysis['files_scanned']} files scanned · {len(analysis['test_inventory'])} test files"
            )
            for warning in analysis["warnings"]:
                st.warning(warning)
        if plan:
            st.write(plan["summary"])
            st.dataframe(plan["tasks"], width="stretch", hide_index=True)
            st.write(plan["assumptions"])
        if not analysis and not plan:
            st.info("Repository evidence will appear after analysis.")
    with diff:
        patch = call("GET", path + "/diff", quiet=True)
        if patch:
            st.dataframe(patch["files"], width="stretch", hide_index=True)
            st.code(patch["diff"] or "No changed files", language="diff")
            for item in patch["provenance"]:
                with st.expander(f"{item['path']} · {item['classification']}"):
                    st.json(item)
        else:
            st.info("A diff is available after patch generation.")
    data = call("GET", path + "/report", quiet=True)
    with validation:
        if data:
            for phase in ("baseline", "post"):
                st.markdown(f"**{phase.title()} checks**")
                for check in data[phase]:
                    with st.expander(
                        f"{check['name']} · {check['status']} · {check['duration_ms']} ms"
                    ):
                        st.json(check)
            if data["comparison"]:
                st.dataframe(data["comparison"], width="stretch", hide_index=True)
            for attempt in data["repairs"]:
                with st.expander(f"Repair {attempt['number']} · {attempt['status']}"):
                    st.json(attempt)
            if (
                not data["baseline"]
                or not data["post"]
                or any(
                    item["status"] != "PASSED"
                    for phase in ("baseline", "post")
                    for item in data[phase]
                )
                or any(
                    row["result"] in {"NOT_RUN", "NOT_COMPARABLE"}
                    for row in data["comparison"]
                )
            ):
                st.warning("Validation was skipped, incomplete, or has failing checks.")
        else:
            st.info("Validation evidence will appear in the final report.")
    with report:
        if data:
            st.write(data["summary"])
            for issue in data["issues"]:
                st.warning(issue)
            for name in data["artifacts"]:
                content = call("GET", path + "/artifacts/" + name, quiet=True)
                if content is not None:
                    st.download_button(
                        "Download " + name, content, file_name=name, key=name
                    )
            if job["pr_url"]:
                st.link_button("Open pull request", job["pr_url"])
            else:
                approved = st.checkbox(
                    "I reviewed the patch and want to create a GitHub PR",
                    disabled=not job["pr_enabled"],
                )
                if st.button(
                    "Create GitHub PR", disabled=not approved or not job["pr_enabled"]
                ):
                    result = call(
                        "POST", path + "/pull-request", json={"approved": True}
                    )
                    if result:
                        st.rerun()
        else:
            st.info("The final report is pending.")


def main():
    st.set_page_config(page_title="PatchOps", page_icon="⚙️", layout="wide")
    st.title("PatchOps")
    page = st.sidebar.radio("Workspace", ["Overview", "New migration", "Review job"])
    if page == "New migration":
        new_job()
        return
    offset = st.sidebar.number_input("Job list offset", min_value=0, step=30, value=0)
    jobs = call("GET", f"{PREFIX}?limit=30&offset={offset}")
    if page == "Overview" and jobs is not None:
        st.header("Migration jobs")
        columns = st.columns(5)
        categories = [
            ("Jobs shown", lambda state: True),
            ("Running", lambda state: state not in TERMINAL and state != "CREATED"),
            ("Ready", lambda state: state.startswith("READY")),
            ("Blocked", lambda state: state == "BLOCKED"),
            ("Failed", lambda state: state == "FAILED"),
        ]
        for column, (label, matches) in zip(columns, categories, strict=True):
            column.metric(label, sum(matches(job["state"]) for job in jobs))
        st.dataframe(
            jobs,
            column_order=["id", "label", "state", "created", "finished"],
            width="stretch",
            hide_index=True,
        )
    if page == "Review job":
        picked = st.selectbox(
            "Recent jobs",
            jobs or [],
            format_func=lambda job: f"{job['label']} · {job['id'][:8]}",
        )
        manual = st.text_input("Or enter job ID")
        if manual:
            picked = call("GET", PREFIX + "/" + manual)
        if picked:
            review(picked)


main()
