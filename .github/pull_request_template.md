## Summary

<!-- What changed, and why? -->

## Validation

- [ ] `python -m compileall -q src tools tests`
- [ ] `python -m unittest discover -s tests -v`
- [ ] `python .\tools\perft.py --depth 1`
- [ ] `git diff --check`

## Evidence and Compatibility

- [ ] This change does not commit credentials, local paths, databases,
      artifacts, external binaries, or NNUE files.
- [ ] Report/schema changes are documented.
- [ ] Known platform or timing limitations are stated.
- [ ] Any external input remains diagnostic and is not presented as local proof.
