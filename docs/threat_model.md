# Threat model

| Asset or boundary | Attack | Control |
| --- | --- | --- |
| Local files and job workspaces | Arbitrary host path, ZIP traversal, links, path collisions, archive expansion | Allowed local roots, safe relative paths, strict ZIP and Git file modes, file and byte caps, fresh private working directories |
| Host Git process | Credential leakage, option injection, Git redirects and protocol helpers | HTTPS hostname allowlist, validated refs, argument arrays, isolated environment, disabled redirects and hooks, read-only tree export, bounded process time and output |
| Model provider | Prompt injection, secret leakage, arbitrary code or patch scope | Explicit opt-in, bounded redacted excerpts, structured edits, exact before text, range validation, regular-file and syntax checks, restricted operations and assertion checks, evidence for human review |
| Worker and validation container | Untrusted install or test code, exfiltration, denial of service | Offline ephemeral containers, non-root UID, read-only image root, dropped capabilities, no new privileges, bounded tmpfs, memory, CPU, processes and time, no host mounts or inherited credentials |
| Reports and downloads | Logs expose known tokens or paths, arbitrary file download | Configured secret and common token redaction, bounded logs, artifact name allowlist, download headers, no raw snapshot downloads |
| GitHub repository | Unexpected branch modification or repeated PR creation | Explicit API approval, matching job state and source, base SHA verification, content-hash checks, stable branch and PR lookup, create-only GitHub calls |

The trusted operator controls API access, allowed roots, Docker, settings, and wheelhouse contents. Loopback requests are trusted in token-free mode. Anyone who can use the dashboard can submit jobs through its configured service identity. Bind it to loopback or put both services behind authenticated infrastructure.

The Docker socket is privileged. Run the worker with a dedicated Docker daemon or VM for hostile workloads. Docker isolation still depends on the host kernel, runtime policy, and trusted image contents. A compromised target can falsify its own checks inside the sandbox. Check outputs and model proposals are evidence for a reviewer. Generic redaction cannot recognize all embedded secrets. Keep confidential source and artifacts inside the operator trust boundary and configure a provider only where sharing source is authorized.

A local Git repository is an operator-supplied input, with the local clone's Git metadata inside the trust boundary. Use ZIP or the public Git workflow for untrusted input instead of mounting its existing `.git` directory. Apply retention outside the application to finished jobs and failed worker workspaces. The built-in controls are intended for single-operator use and lack per-user isolation, quotas, SSO and encryption policy.
