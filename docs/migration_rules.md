# Pydantic migration rules

| Rule | Version | Automatic condition | Review trigger |
| --- | --- | --- | --- |
| `api-dict` | 1.0 | Inline construction of a resolved Pydantic model with only supported serialization keywords | A project-defined `dict` or `model_dump`, a dynamic receiver, or uncertain serialization options |
| `api-parse_obj` | 1.0 | Resolved model class with one positional argument | A project-defined `parse_obj` or `model_validate`, another call signature, or a dynamic receiver |
| `dependency` | 1.0 | A recognized v1-only requirement line or PEP 621 dependency with a verified TOML round trip | Ranges that can admit v1 and a later major version, other manifests, unsupported syntax, lockfiles, and policies that disable dependency edits |

The transformer uses LibCST positions, qualified names and scope assignments. It refuses automatic edits when a model binding is shadowed or when inheritance could override the relevant methods. It preserves source trivia for the supported call rewrites. Standard test files are protected by path and naming rules. Tests with custom discovery conventions need manual review.

Potential v2 replacements include `model_dump_json`, `model_validate_json`, `model_json_schema`, `ConfigDict`, `field_validator` and `model_validator`. Model validators can change signature, execution order and error location. Aliases, serialization flags, default validation and coercion need behavioral checks. Dynamic attributes, cross-module model types, vendor code, Pydantic settings moves and custom root models can require a human edit. The [Pydantic migration guide](https://docs.pydantic.dev/latest/migration/) describes the compatibility changes.
