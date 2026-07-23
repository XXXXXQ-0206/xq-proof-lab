# Third-Party and Evidence Boundary

## Dependencies

The supported local toolkit uses CPython and the standard library. SQLite is
accessed through Python's standard `sqlite3` module. No third-party engine,
NNUE network, online service, or downloaded data is required for the local
rules/proof/verifier/UCI workflows.

## Pikafish and NNUE

Pikafish is an external GPL-3.0 project. This repository does not vendor its
source, executable, or NNUE weights. A locally downloaded binary or NNUE file
may be used only by explicitly requested compatibility diagnostics. The root
`pikafish.nnue`, reference clones, and downloaded releases are ignored user
data and must not be committed as project source.

When an external command is supplied to a diagnostic runner, report schema 11
records local command-token file paths, sizes, and SHA-256 hashes where they
can be resolved. This is provenance, not an endorsement or redistribution of
the external asset. Obtain and comply with the upstream license and release
terms independently.

## Network Sources

ChessDB and bootstrap/download scripts are opt-in integrations. They are not
used by lightweight validation. A response from a network service, external
search engine, evaluation, PV, or bestmove never proves a local proof node.

## Evidence Classes

- Local proof evidence: an artifact whose hash and legal tree are accepted by
  this project's independent verifier.
- Local analysis evidence: deterministic `self_fallback` or other local
  analysis output. It can choose a move but is not proof.
- Diagnostic evidence: any report involving external engines, NNUE, network
  data, unverified telemetry, emergency moves, unknown rules, or incomplete
  games. It is retained for debugging and compatibility only.

Match report telemetry that claims `source=proof` or `source=proof_store` must
include a well-formed proof-artifact SHA-256. The report records the claim and
the raw UCI line; independent certificate verification remains the authority
for the artifact itself.
