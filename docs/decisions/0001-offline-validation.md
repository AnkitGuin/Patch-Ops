# Offline validation and bounded edits

The worker runs both dependency installation and checks in a Docker network namespace without external interfaces. The operator prepares a trusted wheelhouse at image build time. This keeps dependency-install hooks inside the same network isolation boundary as tests. It requires extending the wheelhouse for target dependencies unavailable in the default image.

Semantic edits use exact before/after source ranges. The worker can verify path, range, content, syntax and file metadata before a replacement. The final unified diff can be applied independently by a reviewer. Validators that require coupled edits across distant ranges are flagged for human review.
