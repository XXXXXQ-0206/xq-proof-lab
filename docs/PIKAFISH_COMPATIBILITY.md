# Pikafish Compatibility Notes

Pikafish is an optional external reference for local protocol and rule
diagnostics. It is not required to use XQ Proof Lab, and this
repository makes no playing-strength comparison claim against it.

## Supported Diagnostic Uses

- Compare local move generation with external `go perft` output.
- Check that an external `bestmove` or PV root move is locally legal.
- Exercise the UCI subprocess adapter against a real engine.
- Preserve a historical configuration for a reproducible compatibility probe.

`tools/compare_perft.py`, `tools/uci_search_probe.py`, and the match runners
all label external commands as diagnostics. Their output must not be used as a
proof artifact or as a release acceptance claim.

## Local Assets

`configs/pikafish_baseline.json` and the scripts under `scripts/` describe a
historical frozen reference release. They do not download or bundle it by
default. `external/pikafish-official-2026-01-02/`, its executable, and
`pikafish.nnue` are ignored user-local assets. Their hashes and options may be
recorded in a diagnostic report when present locally.

The project does not redistribute Pikafish or NNUE files. Pikafish is an
external GPL-3.0 project; users must obtain the relevant asset and comply with
its upstream license and release terms themselves.

## Proof Boundary

Pikafish output, NNUE values, WDL estimates, cloud-book responses, bestmoves,
and PVs can at most guide an explicit diagnostic or candidate-ordering path.
They do not prove a Xiangqi position. Proof status requires a local artifact
that is independently replayed by `ProofVerifier`.

## Historical Material

The old qualification/match configurations and ignored reports are retained
for audit and reproduction only. Some historical A/B timing reports combine
setup, proof-store work, and `go` work; they are not fair performance evidence.
See `docs/MAINTENANCE.md` and `docs/PROJECT_STATUS.md` before resuming any
research activity.
