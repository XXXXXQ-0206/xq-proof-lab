# File Manifest

This manifest classifies project files without deleting local research data.
The exhaustive static-closeout inventory is retained in
`docs/MINIMAL_STATIC_CLOSURE.md`.

| Class | Paths | Policy |
| --- | --- | --- |
| Core source | `src/xiangqi_core/`, `src/xiangqi_solver/`, `src/xiangqi_evaluators/` | Project implementation. Keep with paired tests. |
| CLI and scripts | `tools/`, `scripts/` | Project entrypoints. External-engine launchers are diagnostic-only and must state their dependency boundary. |
| Tests | `tests/` | Project regression suite. Fake engines are test fixtures, not runtime assets. |
| Documentation | `README.md`, `docs/` | Product, maintenance, rule, evidence, and historical-plan documentation. |
| Example configuration | `configs/*.example.json`, corpus and diagnostic JSON manifests | Keep as versioned schemas or reproducibility inputs. Do not imply an external asset is bundled. |
| Reproducible local output | `artifacts/` | Ignored. Retain reports with source config and hashes; do not treat them as source files. |
| Local proof data | `database/*.sqlite*` | Ignored. SQLite proof/frontier stores are user data and can be regenerated only from their documented inputs. |
| Temporary/profile data | `tmp/`, `*.prof`, caches | Ignored. Never delete automatically. |
| External assets | Ignored Pikafish clone directories, `pikafish.nnue`, downloaded binaries | `external/README.md` is tracked; preserve upstream license/source notes and never vendor the ignored assets. |

`.gitignore` implements the generated/local-resource rules above. A new source,
test, tool, document, or example config belongs in version control; a generated
artifact belongs under an ignored data directory with its generation inputs
documented.
