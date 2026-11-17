# Evaluation

Run the manifest against the same input snapshots for deterministic, LLM-only and hybrid strategies.

```bash
uv run patchops evaluate --benchmark benchmarks/manifest.json --dry-run
uv run patchops evaluate --benchmark benchmarks/manifest.json
```

The 23-case manifest combines the catalog fixture and generated Python scenarios with requirements and PEP 621 metadata. Cases include aliases, integer coercion, legacy JSON and schema calls, unrelated dictionary operations and missing tests. Held-out-tagged scenarios are generated behavioral variants. Their tests are available to the validation runner. The current implementation passes bounded source context to the model and does not provide test source. The tag does not guarantee that all information about a held-out test stays hidden during repair.

The deterministic strategy disables model proposals. LLM-only asks the provider about discovered code edits and shares the deterministic dependency preparation and verification infrastructure. Hybrid applies deterministic code rules before proposing semantic edits. All three share scope gates, snapshots, validation configuration and job budgets.

| Metric | Calculation |
| --- | --- |
| Validated completion | Jobs in `READY_FOR_REVIEW` divided by all jobs in a validation run. Dry runs emit `null`. |
| Functional regression | Newly failing tests among baseline-passing tests with comparable post-migration outcomes |
| Held-out pass rate | Passed tests divided by executed, non-skipped tests in held-out-tagged cases |
| Validation coverage | Completed baseline and post checks divided by recorded applicable checks |
| Repair success | Attempts recorded as validated divided by recorded attempts |
| Changed lines | Added plus deleted diff lines |
| Latency | Median and nearest-rank p95 wall-clock duration per strategy |
| Provider usage | Sum of reported or conservatively charged model tokens |

Unavailable denominators are emitted as null. Dry-run execution cannot establish functional migration, regression, validation coverage, or repair performance. Changed lines are reported as a churn proxy. Human editing effort, monetary cost, and expert judgments about unrelated churn require additional data.

The bundled [dry-run result](../examples/benchmark-dry-run.json) records 23 cases per strategy. No live model was configured. It shows patch generation and the provenance/reporting path, with 70 changed lines each for deterministic and hybrid and 46 for LLM-only from dependency preparation. There was no live Docker benchmark, external repository trial, or full provider comparison in this environment.
