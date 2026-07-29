# Contributing

Status: process guide for changing this codebase, distilled from `CLAUDE.md` (the binding engineering-process rules for this repository) and the pattern actually followed across this project's 8 implementation phases plus its production-readiness audit and hardening pass (`IMPLEMENTATION_ROADMAP.md`). Where this document and `CLAUDE.md` disagree, `CLAUDE.md` wins — this is a practical companion, not a replacement.

## 1. The governing hierarchy

1. **`SMC_SPECIFICATION.md`** — the frozen specification. Every trading-logic decision traces to a numbered section (`§N`) and, where applicable, a `Decision #N` label. Nothing in code should implement SMC behaviour that isn't traceable to a spec section.
2. **`CLAUDE.md`** — process rules: never guess business logic, never delete working functionality without approval, never duplicate implementations, always plan before coding, always test after.
3. **`IMPLEMENTATION_ROADMAP.md`** — the historical record of how the spec became code, phase by phase. Useful for understanding *why* a piece of code looks the way it does, not itself a source of new rules.
4. **This `docs/` set** — how the *current* implementation works, not how it was built.

## 2. Coding standards

- PEP8, strongly typed (type hints throughout), docstrings on every non-trivial function, meaningful variable names, no magic numbers.
- No hidden side effects, no global mutable state, no large functions where a smaller decomposition is natural — but see §5: don't refactor working code just to satisfy this in isolation.
- Prefer vectorised pandas operations, but never at the cost of correctness or auditability in the state-machine-shaped code (`state_machine.py`, `order_blocks.py`, `liquidity.py`) — these are deliberately explicit row-by-row loops because the logic is a genuine sequential state machine, not a batch transformation; a "vectorised" rewrite would obscure the very state transitions the code exists to make auditable. This is a considered trade-off, not an oversight — see [ARCHITECTURE.md §2.2](ARCHITECTURE.md#22-single-causally-forward-pass).
- Every new function: type hints, docstring, input validation at system boundaries only (trust internal callers; validate user/external input — see `candle_validation.py` for the pattern), error handling for the specific failure modes that are actually reachable (don't add defensive code for scenarios that can't happen).

## 3. Architecture rules (non-negotiable, per CLAUDE.md)

- Never guess business logic. If `SMC_SPECIFICATION.md` doesn't unambiguously answer a question your change raises, stop and ask — do not pick a plausible interpretation and proceed.
- Never delete working functionality without approval, and never remove or rename a file without approval — even confirmed-dead code (this project's own hardening pass verified two orphaned modules had zero consumers, via exhaustive repository-wide search, *before* deleting them, and still flagged the one edge case found — a non-normative citation in the frozen spec — for explicit confirmation rather than assuming it didn't matter).
- Never create duplicate implementations. Search the whole project first; reuse existing classes/utilities/registries/APIs. (The one deliberate exception in this codebase: the legacy and canonical market-structure engines coexist by explicit, spec-approved design during Decision B's deprecation window — that is not duplication, it's a governed migration state. See [ARCHITECTURE.md §4](ARCHITECTURE.md#4-canonical-vs-legacy-engine).)
- Never break a public API response contract without an approved, spec-recorded versioning decision (§33). The legacy endpoint's response shape is explicitly frozen until Phase 3 removal.
- Preserve backward compatibility except where a decision explicitly, approvedly introduces a breaking change (Decision #12's Order Block default-output change is the one example in this codebase, and it was recorded with a MAJOR version bump per §33 — not applied silently).

## 4. Preserving determinism

Any change touching `state_machine.py`, `order_blocks.py`, `liquidity.py`, or `market_structure.py` must preserve:

- **No wall-clock reads, no randomness.** These functions receive all their inputs as arguments; they don't consult `datetime.now()`, environment state, or anything outside their own parameters.
- **Causally-forward-only computation.** A row's output may depend only on rows before it (and its own row), never on rows after it, except for the one explicitly-approved exception (`detect_swing_points`' `right_bars` look-ahead, which is a documented, load-bearing property, not a violation — see [ARCHITECTURE.md §2.4](ARCHITECTURE.md#24-historical-reproducibility-with-a-known-live-data-caveat)).
- **No retroactive mutation of already-computed output**, unless a decision explicitly requires it (none currently do in this codebase — Decision #3's per-cycle classification was designed specifically to avoid needing this, per its own `[INVARIANT]` "no two-pass bootstrap," §7 point 6).
- Verify with: full suite run twice, and once in reverse file order (see [TESTING.md §2](TESTING.md#2-determinism)).

## 5. Don't refactor beyond scope

Every phase in this project's history was explicitly bounded: implement only the approved decision(s), audit downstream consumers only to preserve compatibility, report anything else discovered as a *separate*, out-of-scope finding rather than fixing it inline. Follow the same discipline: a bug fix doesn't need surrounding cleanup; discovering a second, unrelated issue while fixing the first is a reason to report it, not to expand the current change's blast radius.

## 6. How to add a new Decision

1. **The decision itself is proposed and approved in `SMC_SPECIFICATION.md` first** — as a new `[APPROVED SPEC]` entry with a `Decision #N` label, before any code is written. This project does not write SMC trading logic and retrofit a spec entry to match it.
2. **Plan before coding**: identify every affected file, every downstream consumer, existing tests that protect current behaviour, new tests required, and the exact versioning implication per §33's rules (don't guess — if §33 has no explicit entry for your decision, that itself is a finding to report, not a default to assume).
3. **Implement only the approved decision.** If implementing it reveals the frozen spec text under-specifies something (a naming choice, an edge case), resolve it directly from other text already in the spec if possible; if genuine ambiguity remains, stop and ask rather than guess.
4. **Add tests before or alongside the change**, covering the decision's own acceptance criteria if the spec states them explicitly (Decision #3, §7 point 9 is the model example: 7 numbered, directly-testable acceptance criteria).
5. **Run the full regression suite**, classify every failure (expected vs. unexpected — see [TESTING.md §6](TESTING.md#6-how-to-regenerate-goldens-safely)), and only regenerate goldens for failures proven to be the decision's own, required consequence.
6. **Update `docs/` if the decision changes anything this documentation set describes** — a stale docstring or diagram is exactly the kind of drift a later hardening pass has to rediscover and fix (this project's own hardening phase corrected a docstring that had gone stale during an earlier phase's classification rewrite).

## 7. How to avoid regressions

- Read [DATA_FLOW.md](DATA_FLOW.md) before changing any pipeline stage's output columns — every downstream stage that copies the DataFrame forward inherits your change whether it reads the changed column or not (the RSI-fix-changing-`analyze_endpoint_response` golden, [TESTING.md §4](TESTING.md#4-goldens), is the concrete example of this exact risk materialising).
- A golden test staying green is not proof a code path was exercised — confirm what the real fixture actually contains before concluding "no behaviour change."
- If your change touches `state_machine.py`, re-run the Decision #8 same-row event-ordering tests explicitly — that invariant is cheap to violate accidentally when adding a new branch to an already-large function.

## 8. Review checklist

Before considering a change complete, confirm:

- [ ] Every touched file's change is traceable to an approved spec decision or an explicitly-scoped bug fix — nothing extra crept in.
- [ ] No duplicate logic was introduced; existing modules/registries/helpers were reused where they already exist.
- [ ] Determinism preserved (§4 above) — full suite passes twice and in reverse order.
- [ ] Every golden-file diff (if any) is explained field-by-field, not merely regenerated.
- [ ] `pipeline_version` changed only if §33 explicitly requires it for this change, and to the exact recorded value — never a guessed increment.
- [ ] No dead code, no stale comments/docstrings left behind describing the pre-change behaviour.
- [ ] `docs/` updated if the change affects anything described in this documentation set.
- [ ] Legacy engine untouched, unless the change is explicitly a Decision B Phase-3-scoped legacy change (extremely unlikely outside that phase).

## 9. Commit standards

- One logical change per commit; create a commit before starting major work and another after successful implementation (`CLAUDE.md`'s standing instruction).
- Never force-push, never skip hooks (`--no-verify`), never amend a commit that's already been shared, unless explicitly asked.
- Commit messages should state *why*, not narrate *what* the diff already shows.

## Cross-references

- Full architecture and design philosophy: [ARCHITECTURE.md](ARCHITECTURE.md)
- Test-suite mechanics referenced throughout this document: [TESTING.md](TESTING.md)
- Diagnosing something during a change: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
