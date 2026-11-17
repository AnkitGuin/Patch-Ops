# PatchOps migration report

## Executive summary

**READY_FOR_REVIEW_WITH_WARNINGS**

Migrate Pydantic v1 usage to v2 and preserve behavior.

Review the patch and the recorded validation findings before merging.

## Repository snapshot

    {
      "label": "customer_catalog.zip",
      "commit": null,
      "sha256": "a717aafdaedf8b0d952bf494b7ef0e1029f5d526e26cef09f943bad445a8886d",
      "ref": null,
      "language": "Python",
      "captured_at": 1794570809.285999
    }

## Migration scope

    {
      "migration": {
        "id": "pydantic-v1-to-v2",
        "source_version": "1.x",
        "target_version": "2.x"
      },
      "constraints": {
        "do_not_modify_tests": true,
        "avoid_unrelated_refactors": true,
        "allow_dependency_update": true,
        "preserve_public_api": true,
        "allow_semantic_edits": true
      },
      "mode": "migrate"
    }

## Repository findings

    {
      "files_scanned": 3,
      "files_skipped": 0,
      "python": null,
      "dependencies": [
        "pydantic==1.10.22"
      ],
      "signals": 8,
      "tests": [
        "tests/test_catalog.py"
      ],
      "lockfiles": [],
      "warnings": []
    }

## Migration plan

    {
      "migration": {
        "id": "pydantic-v1-to-v2",
        "source_version": "1.x",
        "target_version": "2.x"
      },
      "summary": "Update eligible Pydantic APIs and dependency declarations.",
      "expected_behavior": "Preserve public APIs, input validation, and serialization behavior.",
      "files": [
        "models.py",
        "requirements.txt"
      ],
      "tasks": [
        {
          "path": "models.py",
          "line": 8,
          "end": 9,
          "rule": "model-config",
          "evidence": "Inner Config requires ConfigDict and setting-by-setting review",
          "strategy": "semantic",
          "risk": "medium"
        },
        {
          "path": "models.py",
          "line": 11,
          "end": 16,
          "rule": "validator",
          "evidence": "Validator ordering, defaults and signature require review",
          "strategy": "semantic",
          "risk": "high"
        },
        {
          "path": "models.py",
          "line": 23,
          "end": 27,
          "rule": "validator",
          "evidence": "Validator ordering, defaults and signature require review",
          "strategy": "semantic",
          "risk": "high"
        },
        {
          "path": "models.py",
          "line": 31,
          "end": 31,
          "rule": "api-parse_obj",
          "evidence": "Resolved model receiver for parse_obj()",
          "strategy": "deterministic",
          "risk": "low"
        },
        {
          "path": "models.py",
          "line": 35,
          "end": 35,
          "rule": "api-dict",
          "evidence": "Resolved model receiver for dict()",
          "strategy": "deterministic",
          "risk": "low"
        },
        {
          "path": "requirements.txt",
          "line": 1,
          "end": 1,
          "rule": "dependency",
          "evidence": "Pydantic declaration",
          "strategy": "deterministic",
          "risk": "low"
        }
      ],
      "validation": [
        "syntax",
        "pytest",
        "migration"
      ],
      "assumptions": [
        "Conservative receiver resolution covers same-module BaseModel classes and inline model construction.",
        "Behavior outside discovered checks requires human review."
      ]
    }

## Changes made

    {
      "files": [
        {
          "path": "models.py",
          "additions": 14,
          "deletions": 9
        },
        {
          "path": "requirements.txt",
          "additions": 1,
          "deletions": 1
        }
      ],
      "provenance": [
        {
          "path": "models.py",
          "classification": "DETERMINISTIC_REWRITE",
          "version": "1.0",
          "rule_ids": [
            "api-dict",
            "api-parse_obj"
          ],
          "before_sha256": "9e981cee7abc22b780e4ff45f7c4b43223791266e2c7a5e9ec6186b976ca5d37",
          "after_sha256": "32a7a4697b44f698ce9386b9fe22b7ec3fe4697d68ea2aedf46995ae8b1512ce",
          "evidence": [
            {
              "path": "models.py",
              "line": 31,
              "end": 31,
              "rule": "api-parse_obj",
              "evidence": "Resolved model receiver for parse_obj()",
              "strategy": "deterministic",
              "risk": "low"
            },
            {
              "path": "models.py",
              "line": 35,
              "end": 35,
              "rule": "api-dict",
              "evidence": "Resolved model receiver for dict()",
              "strategy": "deterministic",
              "risk": "low"
            }
          ],
          "hunk_headers": [
            "@@ -28,8 +28,8 @@"
          ],
          "validation": [
            "syntax",
            "pytest",
            "migration"
          ],
          "rationale": "Applied source-aware Pydantic migration rules.",
          "review_required": false
        },
        {
          "path": "requirements.txt",
          "classification": "DETERMINISTIC_REWRITE",
          "version": "1.0",
          "rule_ids": [
            "dependency"
          ],
          "before_sha256": "4a16bf8139d8893174c8f19b0c0ded462150fb9fbb1a2e1aaf2bad2b8807bd0e",
          "after_sha256": "3690b6094792bdd6ae8d659571822e914309f1df3e8577ad0419503755446f5a",
          "evidence": [
            {
              "path": "requirements.txt",
              "line": 1,
              "end": 1,
              "rule": "dependency",
              "evidence": "Pydantic declaration",
              "strategy": "deterministic",
              "risk": "low"
            }
          ],
          "hunk_headers": [
            "@@ -1 +1 @@"
          ],
          "validation": [
            "syntax",
            "pytest",
            "migration"
          ],
          "rationale": "Applied source-aware Pydantic migration rules.",
          "review_required": false
        },
        {
          "path": "models.py",
          "classification": "LLM_SEMANTIC_EDIT",
          "version": "semantic-v1/test-fake",
          "rule_ids": [
            "validator"
          ],
          "before_sha256": "32a7a4697b44f698ce9386b9fe22b7ec3fe4697d68ea2aedf46995ae8b1512ce",
          "after_sha256": "bdd5ac42ad4887d789627182eac4e8646e38d88ad2cf7f12c4ee44b3e940bbab",
          "evidence": [
            {
              "path": "models.py",
              "line": 23,
              "end": 27,
              "rule": "validator",
              "evidence": "Validator ordering, defaults and signature require review",
              "strategy": "semantic",
              "risk": "high"
            }
          ],
          "hunk_headers": [
            "@@ -20,11 +20,13 @@"
          ],
          "validation": [
            "syntax",
            "pytest",
            "migration"
          ],
          "rationale": "Use equivalent v2 API.",
          "review_required": true,
          "behavior": "Preserve the fixture contract.",
          "uncertainty": "Review errors for invalid input.",
          "suggested_validation": "Configured pytest checks."
        },
        {
          "path": "models.py",
          "classification": "LLM_SEMANTIC_EDIT",
          "version": "semantic-v1/test-fake",
          "rule_ids": [
            "validator"
          ],
          "before_sha256": "bdd5ac42ad4887d789627182eac4e8646e38d88ad2cf7f12c4ee44b3e940bbab",
          "after_sha256": "0a91a72d25aef39595af4031a6f1f37de12a0247444ee667d0221491d77759f9",
          "evidence": [
            {
              "path": "models.py",
              "line": 11,
              "end": 16,
              "rule": "validator",
              "evidence": "Validator ordering, defaults and signature require review",
              "strategy": "semantic",
              "risk": "high"
            }
          ],
          "hunk_headers": [
            "@@ -8,7 +8,10 @@"
          ],
          "validation": [
            "syntax",
            "pytest",
            "migration"
          ],
          "rationale": "Use equivalent v2 API.",
          "review_required": true,
          "behavior": "Preserve the fixture contract.",
          "uncertainty": "Review errors for invalid input.",
          "suggested_validation": "Configured pytest checks."
        },
        {
          "path": "models.py",
          "classification": "LLM_SEMANTIC_EDIT",
          "version": "semantic-v1/test-fake",
          "rule_ids": [
            "model-config"
          ],
          "before_sha256": "0a91a72d25aef39595af4031a6f1f37de12a0247444ee667d0221491d77759f9",
          "after_sha256": "e6ed6138506d45c80a49a1a40eaa7e29834495765d7315a4ba6e60172bef0da5",
          "evidence": [
            {
              "path": "models.py",
              "line": 8,
              "end": 9,
              "rule": "model-config",
              "evidence": "Inner Config requires ConfigDict and setting-by-setting review",
              "strategy": "semantic",
              "risk": "medium"
            }
          ],
          "hunk_headers": [
            "@@ -5,8 +5,8 @@"
          ],
          "validation": [
            "syntax",
            "pytest",
            "migration"
          ],
          "rationale": "Use equivalent v2 API.",
          "review_required": true,
          "behavior": "Preserve the fixture contract.",
          "uncertainty": "Review errors for invalid input.",
          "suggested_validation": "Configured pytest checks."
        }
      ]
    }

## Dependency changes

    {
      "edits": [
        {
          "path": "requirements.txt",
          "classification": "DETERMINISTIC_REWRITE",
          "version": "1.0",
          "rule_ids": [
            "dependency"
          ],
          "before_sha256": "4a16bf8139d8893174c8f19b0c0ded462150fb9fbb1a2e1aaf2bad2b8807bd0e",
          "after_sha256": "3690b6094792bdd6ae8d659571822e914309f1df3e8577ad0419503755446f5a",
          "evidence": [
            {
              "path": "requirements.txt",
              "line": 1,
              "end": 1,
              "rule": "dependency",
              "evidence": "Pydantic declaration",
              "strategy": "deterministic",
              "risk": "low"
            }
          ],
          "hunk_headers": [
            "@@ -1 +1 @@"
          ],
          "validation": [
            "syntax",
            "pytest",
            "migration"
          ],
          "rationale": "Applied source-aware Pydantic migration rules.",
          "review_required": false
        }
      ],
      "install_evidence": {
        "baseline": [
          {
            "name": "install",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-m",
              "pip",
              "--isolated",
              "install",
              "--disable-pip-version-check",
              "--no-index",
              "--find-links=/wheelhouse",
              "--no-build-isolation",
              "-r",
              "requirements.txt"
            ],
            "status": "PASSED",
            "exit_code": 0,
            "started_at": "2026-11-13T11:53:39.514090+00:00",
            "finished_at": "2026-11-13T11:53:40.079655+00:00",
            "duration_ms": 565,
            "stdout": "Looking in links: /wheelhouse\nProcessing /wheelhouse/pydantic-1.10.22-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (from -r requirements.txt (line 1))\nRequirement already satisfied: typing-extensions>=4.2.0 in /work/venv/lib/python3.12/site-packages (from pydantic==1.10.22->-r requirements.txt (line 1)) (4.16.0)\nInstalling collected packages: pydantic\nSuccessfully installed pydantic-1.10.22\n",
            "stderr": "",
            "signatures": [],
            "outcomes": {},
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "resolved",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-m",
              "pip",
              "list",
              "--format=freeze",
              "--disable-pip-version-check"
            ],
            "status": "PASSED",
            "exit_code": 0,
            "started_at": "2026-11-13T11:53:40.079781+00:00",
            "finished_at": "2026-11-13T11:53:40.344578+00:00",
            "duration_ms": 264,
            "stdout": "iniconfig==2.3.1\nmypy==1.18.2\nmypy_extensions==1.2.0\npackaging==26.3\npathspec==1.1.1\npip==25.0.1\npluggy==1.7.0\npydantic==1.10.22\nPygments==2.21.0\npytest==8.4.2\nruff==0.13.3\nsetuptools==80.9.0\ntyping_extensions==4.16.0\nwheel==0.45.1\n",
            "stderr": "",
            "signatures": [],
            "outcomes": {},
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "migration",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-c",
              "import importlib.metadata; print(importlib.metadata.version('pydantic'))"
            ],
            "status": "PASSED",
            "exit_code": 0,
            "started_at": "2026-11-13T11:53:40.724778+00:00",
            "finished_at": "2026-11-13T11:53:40.789278+00:00",
            "duration_ms": 64,
            "stdout": "1.10.22\n",
            "stderr": "",
            "signatures": [],
            "outcomes": {},
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          }
        ],
        "post": [
          {
            "name": "install",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-m",
              "pip",
              "--isolated",
              "install",
              "--disable-pip-version-check",
              "--no-index",
              "--find-links=/wheelhouse",
              "--no-build-isolation",
              "-r",
              "requirements.txt"
            ],
            "status": "PASSED",
            "exit_code": 0,
            "started_at": "2026-11-13T11:53:46.644128+00:00",
            "finished_at": "2026-11-13T11:53:47.310727+00:00",
            "duration_ms": 666,
            "stdout": "Looking in links: /wheelhouse\nProcessing /wheelhouse/pydantic-2.11.10-py3-none-any.whl (from -r requirements.txt (line 1))\nProcessing /wheelhouse/annotated_types-0.8.0-py3-none-any.whl (from pydantic<3,>=2->-r requirements.txt (line 1))\nProcessing /wheelhouse/pydantic_core-2.33.2-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (from pydantic<3,>=2->-r requirements.txt (line 1))\nRequirement already satisfied: typing-extensions>=4.12.2 in /work/venv/lib/python3.12/site-packages (from pydantic<3,>=2->-r requirements.txt (line 1)) (4.16.0)\nProcessing /wheelhouse/typing_inspection-0.4.5-py3-none-any.whl (from pydantic<3,>=2->-r requirements.txt (line 1))\nInstalling collected packages: typing-inspection, pydantic-core, annotated-types, pydantic\nSuccessfully installed annotated-types-0.8.0 pydantic-2.11.10 pydantic-core-2.33.2 typing-inspection-0.4.5\n",
            "stderr": "",
            "signatures": [],
            "outcomes": {},
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "resolved",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-m",
              "pip",
              "list",
              "--format=freeze",
              "--disable-pip-version-check"
            ],
            "status": "PASSED",
            "exit_code": 0,
            "started_at": "2026-11-13T11:53:47.310862+00:00",
            "finished_at": "2026-11-13T11:53:47.526126+00:00",
            "duration_ms": 215,
            "stdout": "annotated-types==0.8.0\niniconfig==2.3.1\nmypy==1.18.2\nmypy_extensions==1.2.0\npackaging==26.3\npathspec==1.1.1\npip==25.0.1\npluggy==1.7.0\npydantic==2.11.10\npydantic_core==2.33.2\nPygments==2.21.0\npytest==8.4.2\nruff==0.13.3\nsetuptools==80.9.0\ntyping_extensions==4.16.0\ntyping-inspection==0.4.5\nwheel==0.45.1\n",
            "stderr": "",
            "signatures": [],
            "outcomes": {},
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "migration",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-c",
              "import importlib.metadata; print(importlib.metadata.version('pydantic'))"
            ],
            "status": "PASSED",
            "exit_code": 0,
            "started_at": "2026-11-13T11:53:47.958333+00:00",
            "finished_at": "2026-11-13T11:53:48.023122+00:00",
            "duration_ms": 64,
            "stdout": "2.11.10\n",
            "stderr": "",
            "signatures": [],
            "outcomes": {},
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          }
        ]
      }
    }

## Baseline validation

- **install** — PASSED (565 ms, exit 0)
- **resolved** — PASSED (264 ms, exit 0)
- **syntax** — PASSED (64 ms, exit 0)
- **pytest** — PASSED (315 ms, exit 0)
- **migration** — PASSED (64 ms, exit 0)

## Post-migration validation

- **install** — PASSED (666 ms, exit 0)
- **resolved** — PASSED (215 ms, exit 0)
- **syntax** — PASSED (65 ms, exit 0)
- **pytest** — PASSED (366 ms, exit 0)
- **migration** — PASSED (64 ms, exit 0)

## Repair history

    []

## Regression assessment

    [
      {
        "check": "install",
        "test": "",
        "result": "STILL_PASSING"
      },
      {
        "check": "resolved",
        "test": "",
        "result": "STILL_PASSING"
      },
      {
        "check": "syntax",
        "test": "",
        "result": "STILL_PASSING"
      },
      {
        "check": "pytest",
        "test": "tests.test_catalog::test_customer_accepts_field_name",
        "result": "STILL_PASSING"
      },
      {
        "check": "pytest",
        "test": "tests.test_catalog::test_customer_payload",
        "result": "STILL_PASSING"
      },
      {
        "check": "pytest",
        "test": "tests.test_catalog::test_missing_bound_uses_field_error",
        "result": "STILL_PASSING"
      },
      {
        "check": "pytest",
        "test": "tests.test_catalog::test_reads_band",
        "result": "STILL_PASSING"
      },
      {
        "check": "pytest",
        "test": "tests.test_catalog::test_rejects_empty_customer_name",
        "result": "STILL_PASSING"
      },
      {
        "check": "pytest",
        "test": "tests.test_catalog::test_rejects_inverted_band",
        "result": "STILL_PASSING"
      },
      {
        "check": "migration",
        "test": "",
        "result": "STILL_PASSING"
      }
    ]

## Limitations and uncertainty

    [
      "Review semantic edit for validator in models.py:23",
      "Review semantic edit for validator in models.py:11",
      "Review semantic edit for model-config in models.py:8"
    ]

## Human review checklist

- Review the diff and every semantic edit.
- Verify validator, config and serialization behavior against production inputs.
- Resolve remaining findings and rerun incomplete checks.

## Reproducibility

    {
      "job_id": "a917a6099f754b4d923ff2c052a13f44",
      "strategy": "hybrid",
      "artifacts": [
        "analysis.json",
        "plan.json",
        "migration.diff",
        "report.json",
        "report.md"
      ],
      "llm_tokens_charged": 3,
      "commands": {
        "baseline": [
          {
            "name": "install",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-m",
              "pip",
              "--isolated",
              "install",
              "--disable-pip-version-check",
              "--no-index",
              "--find-links=/wheelhouse",
              "--no-build-isolation",
              "-r",
              "requirements.txt"
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "resolved",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-m",
              "pip",
              "list",
              "--format=freeze",
              "--disable-pip-version-check"
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "syntax",
            "argv": [
              "/work/venv/bin/python",
              "-m",
              "compileall",
              "-q",
              "."
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "pytest",
            "argv": [
              "/work/venv/bin/python",
              "-m",
              "pytest",
              "-q",
              "-p",
              "no:cacheprovider",
              "-o",
              "junit_family=xunit1",
              "--junitxml=/work/patchops-results.xml"
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "migration",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-c",
              "import importlib.metadata; print(importlib.metadata.version('pydantic'))"
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          }
        ],
        "post": [
          {
            "name": "install",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-m",
              "pip",
              "--isolated",
              "install",
              "--disable-pip-version-check",
              "--no-index",
              "--find-links=/wheelhouse",
              "--no-build-isolation",
              "-r",
              "requirements.txt"
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "resolved",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-m",
              "pip",
              "list",
              "--format=freeze",
              "--disable-pip-version-check"
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "syntax",
            "argv": [
              "/work/venv/bin/python",
              "-m",
              "compileall",
              "-q",
              "."
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "pytest",
            "argv": [
              "/work/venv/bin/python",
              "-m",
              "pytest",
              "-q",
              "-p",
              "no:cacheprovider",
              "-o",
              "junit_family=xunit1",
              "--junitxml=/work/patchops-results.xml"
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          },
          {
            "name": "migration",
            "argv": [
              "/work/venv/bin/python",
              "-I",
              "-c",
              "import importlib.metadata; print(importlib.metadata.version('pydantic'))"
            ],
            "environment": {
              "image": "isolated-linux-namespace-fixture-qa",
              "network": "none",
              "python": "3.12.14",
              "dependency_source": "offline wheelhouse"
            }
          }
        ]
      }
    }
