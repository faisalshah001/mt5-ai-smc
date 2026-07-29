## Summary

<!-- What does this change do, and why? Link the SMC_SPECIFICATION.md
     section/Decision # if this touches trading logic. -->

## Type of change

- [ ] Bug fix
- [ ] New feature (non-trading-logic)
- [ ] Trading-logic change (cite the approved `SMC_SPECIFICATION.md` Decision #)
- [ ] Documentation
- [ ] CI / tooling / release engineering
- [ ] Other (describe above)

## Checklist

<!-- See docs/CONTRIBUTING.md's full review checklist -- this is the
     short version for the PR description itself. -->

- [ ] Change is traceable to an approved spec Decision, or is a clearly-scoped bug fix / non-trading-logic change
- [ ] No duplicate logic introduced; existing modules/registries/helpers reused where they already exist
- [ ] Full test suite passes locally (`make test` / `pytest`)
- [ ] Determinism preserved: suite passes twice and in reverse file order (`docs/TESTING.md#2-determinism`)
- [ ] Any golden-file diff is explained field-by-field, not merely regenerated (`docs/TESTING.md#6-how-to-regenerate-goldens-safely`)
- [ ] `pipeline_version` changed only if `SMC_SPECIFICATION.md` §33 explicitly requires it, to the exact recorded value
- [ ] No dead code, no stale comments/docstrings left describing the pre-change behaviour
- [ ] `docs/` updated if this change affects anything described there
- [ ] Legacy endpoint (`/analysis/market-structure`) untouched, unless this PR is explicitly a Decision B Phase 3 change

## Testing performed

<!-- Commands run and their output/summary, not just "tests pass". -->

## Related issues

<!-- Closes #... / Relates to #... -->
