import json
from typing import Any

from .models import State
from .store import Store


def markdown(report: dict[str, Any]) -> str:
    def section(title: str, value: Any) -> str:
        if isinstance(value, str):
            return f"## {title}\n\n{value}\n"
        return (
            f"## {title}\n\n"
            + "\n".join(
                f"    {line}"
                for line in json.dumps(value, ensure_ascii=False, indent=2).splitlines()
            )
            + "\n"
        )

    def checks(stage: str) -> str:
        results = report.get(stage, [])
        if not results:
            return "No checks were recorded for this stage."
        return "\n".join(
            f"- **{row['name']}** — {row['status']} ({row['duration_ms']} ms, exit {row['exit_code']})"
            for row in results
        )

    return "\n".join(
        [
            "# PatchOps migration report\n",
            section(
                "Executive summary",
                f"**{report['state']}**\n\n{report['objective']}\n\n{report['summary']}",
            ),
            section("Repository snapshot", report.get("snapshot", {})),
            section("Migration scope", report["scope"]),
            section("Repository findings", report.get("findings", {})),
            section("Migration plan", report.get("plan", {})),
            section(
                "Changes made",
                {
                    "files": report.get("files", []),
                    "provenance": report.get("provenance", []),
                },
            ),
            section(
                "Dependency changes",
                {
                    "edits": [
                        row
                        for row in report.get("provenance", [])
                        if "dependency" in row["rule_ids"]
                    ],
                    "install_evidence": {
                        stage: [
                            row
                            for row in report.get(stage, [])
                            if row["name"] in {"install", "resolved", "migration"}
                        ]
                        for stage in ("baseline", "post")
                    },
                },
            ),
            section("Baseline validation", checks("baseline")),
            section("Post-migration validation", checks("post")),
            section("Repair history", report.get("repairs", [])),
            section("Regression assessment", report.get("comparison", [])),
            section("Limitations and uncertainty", report.get("issues", [])),
            section(
                "Human review checklist",
                "- Review the diff and every semantic edit.\n- Verify validator, config and serialization behavior against production inputs.\n- Resolve remaining findings and rerun incomplete checks.",
            ),
            section(
                "Reproducibility",
                {
                    "job_id": report["job_id"],
                    "strategy": report["strategy"],
                    "artifacts": report["artifacts"],
                    "llm_tokens_charged": report.get("llm_tokens", 0),
                    "commands": {
                        stage: [
                            {k: row[k] for k in ("name", "argv", "environment")}
                            for row in report.get(stage, [])
                        ]
                        for stage in ("baseline", "post")
                    },
                },
            ),
        ]
    )


def save_report(
    store: Store, job_id: str, report: dict[str, Any], state: State, summary: str
) -> None:
    report["state"] = state
    report["summary"] = summary
    report["artifacts"] = [
        name
        for name in ("analysis.json", "plan.json", "migration.diff")
        if (store.folder(job_id) / name).is_file()
    ] + ["report.json", "report.md"]
    store.write(job_id, "report.json", report)
    store.write(job_id, "report.md", markdown(report))
