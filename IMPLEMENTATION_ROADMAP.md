# SMC Engine — Implementation Roadmap

**Status:** Planning document. No code has been changed to produce this document.
**Maps:** `SMC_SPECIFICATION.md` (all decisions frozen, spec internally consistent and implementation-ready) → current codebase state.
**Scope:** This document does not modify `SMC_SPECIFICATION.md` and does not modify any Python file. It is a plan only.

---

## 0. How to read this document

Every gap below cites the exact current code location and the exact spec section/decision that governs its replacement. "Implementation status" mirrors the spec's own `[IMPLEMENTATION STATUS]` tags: every decision in `SMC_SPECIFICATION.md` is **approved, zero implemented** — this roadmap is what turns each one into code.

Two decisions are explicitly **not implementable yet**, independent of sequencing choices:

- **Decision #2 / #14 (live-safe mode)** — the spec itself defers "concrete design (API shape, flagging vs. truncation, configuration surface)" to a later, separately-approved phase (§30, `[IMPLEMENTATION STATUS]`). This roadmap treats it as a future phase gated on a design step that has not happened yet.
- **Decision #5 (Internal Structure)** — the spec approves the *architecture* only; detailed Internal BOS/MSS/CHoCH/protected-level rules remain unspecified (§9, point 8). Not implementable until those follow-up decisions exist.

Everything else below is implementable directly from the frozen spec.

---

## 1. Full implementation gap analysis

### Decision A — Standalone candle-validation component

| | |
|---|---|
| File | New: `app/analysis/candle_validation.py`. Modified: `app/analysis/analysis_engine.py`, `main.py` |
| Class/function | New standalone function (name TBD, e.g. `validate_and_normalize_candles`). Removes `analysis_engine.py::_validate_input` (lines 39-78) and `_prepare_candles` (lines 81-205) as private duplicates. |
| Affected variables | None (input/output is the candles DataFrame) |
| Affected outputs | No schema change on any endpoint response. Malformed input (NaN/±inf OHLC, duplicate timestamps, bad OHLC relationships, unparseable time) now raises `ValueError` → HTTP 400 on all four candle-consuming endpoints, where today it may silently succeed with bad data on three of the four (`/candles`, `/strategy/trend`, `/strategy/multi-timeframe`). |
| Spec section | §3, Decision A |
| Decision # | A |
| Implementation status | Not implemented |
| Classification | **Missing feature** (infinity rejection is new) + **Refactoring only** (extraction of existing `_validate_input`/`_prepare_candles` logic) |

### Decision B, Phase 1 — Canonical endpoint

| | |
|---|---|
| File | `main.py` |
| Class/function | New route function calling `app.analysis.analysis_engine.analyze_market` |
| Affected variables | None |
| Affected outputs | New endpoint only; no existing endpoint response changes |
| Spec section | §3, Decision B, points 1, 5 (Phase 1) |
| Decision # | B |
| Implementation status | Not implemented |
| Classification | **Missing feature** |

### Decision B, Phase 2 — Deprecation notice

| | |
|---|---|
| File | `main.py` (route metadata/docs only) |
| Affected outputs | Legacy endpoint's documented status only; **no runtime behaviour change** |
| Spec section | §3, Decision B, point 5 (Phase 2) |
| Classification | **Documentation only** |

### Decision B, Phase 3 — Legacy removal (future, gated)

| | |
|---|---|
| File | `main.py` (remove route), `app/analysis/market_structure.py` (remove `detect_breaks_of_structure`, `detect_change_of_character`, and the global `classify_market_structure` once it has no remaining consumers per §7 point 8) |
| Spec section | §3 Decision B point 5 (Phase 3), §7 Decision #3 point 8 |
| Classification | **Missing feature removal** — out of scope for this roadmap's near-term phases; gated on exit criteria (§3, point 6) that this roadmap must first satisfy |

### Decision #2 / #14 — Live-safe output mode

| | |
|---|---|
| File | Undetermined — design not yet specified |
| Spec section | §6 (Decision #2), §30 (Decision #14) |
| Implementation status | **Blocked.** Spec explicitly defers concrete design. Requires a follow-up design-approval step before any gap analysis is possible. |
| Classification | **Missing feature** (blocked) |

### Decision #3 — Per-cycle classification (canonical engine)

| | |
|---|---|
| File | `app/analysis/state_machine.py` (major rewrite), `app/analysis/analysis_engine.py` (pipeline restructure), `app/analysis/market_structure.py` (unaffected — legacy only) |
| Class/function | `state_machine.py::detect_structure_state` must absorb swing classification (currently `market_structure.py::classify_market_structure`, lines 99-156) into itself as one unified forward pass, since cycle boundaries (confirmed CHoCH) are only known *while* the state machine runs, and classification of a given swing must be cycle-aware. `analysis_engine.py::analyze_market`'s pipeline step 3 (`classify_market_structure` call, line 748) is removed from the canonical path; `detect_structure_state` receives swing points directly (from `detect_swing_points`) instead of a pre-classified `structure` column. |
| Affected variables | New cycle-scoped `previous_high`/`previous_low` baselines (replacing the whole-series-global ones in `market_structure.py`, which remain unchanged for the legacy engine only). New internal cycle-boundary tracking, reset at each confirmed CHoCH row. |
| Affected outputs | `structure` column values can differ from today's canonical-pipeline output for any swing whose global classification was forced to `LH`/`LL` by a cycle-irrelevant historical extreme (§7, point 6 — this is the exact scenario the two-pass-bootstrap prohibition exists to prevent). This is a genuine, deliberate output-changing decision for the canonical pipeline. |
| Spec section | §7, Decision #3 (points 1-9) |
| Decision # | #3 |
| Implementation status | Not implemented |
| Classification | **Behaviour mismatch** + **State mismatch** — the single largest, highest-risk item in this roadmap |

### Decision #4 — Tie classification

| | |
|---|---|
| File | None |
| Spec section | §7, Decision #4 |
| Implementation status | Current code already matches approved spec exactly (strict `>`/`<`, ties fold to `LH`/`LL`) |
| Classification | **Test only** — pin the behaviour with a regression test; no code change |

### Decision #5 — Internal Structure

| | |
|---|---|
| File | Undetermined — architecture approved, detailed rules not yet specified |
| Spec section | §9, Decision #5 |
| Implementation status | **Blocked** pending further decisions (§9, point 8) |
| Classification | **Missing feature** (blocked) |

### Decision #6 — MSS invalidation

| | |
|---|---|
| File | `app/analysis/state_machine.py`, `app/analysis/analysis_engine.py`, `app/analysis/models.py` |
| Class/function | `state_machine.py::detect_structure_state` — new branches for `LL` confirming while `current_state == "mss_bullish"` and `HH` confirming while `current_state == "mss_bearish"` (currently unhandled: Step 1's `elif current_state == "mss_bullish":`/`"mss_bearish":` blocks at lines 232, 276, 295, 339 only handle the *confirming*-direction swing type, never the *invalidating* one — a same-original-direction swing during a pending MSS is silently absorbed into unconditional bookkeeping only). `analysis_engine.py::_build_structure_events` — extend the accepted `event_type` set (line 310, currently `{"BOS", "MSS", "CHoCH"}`) to include `"MSS_INVALIDATED"`; add `mss_origin_index`/`mss_origin_event_id` metadata population, requiring a forward-built index→event_id map (an MSS event's `event_id` is always assigned before its eventual invalidation row is reached, since events are built in row order — no look-ahead needed). |
| Affected variables | New internal variable `mss_origin_index` (candle position of the MSS-creation row), tracked parallel to the existing `mss_origin_level`, cleared at the same two points (CHoCH confirmation, MSS invalidation). Per the audit clarification in §19, `mss_origin_index` must also become a **new per-row output column** (mirroring how `mss_origin_level` is already written every row via `store_current_state`), not merely an internal loop variable. |
| Affected outputs | New DataFrame columns: `mss_origin_index`, `mss_invalidated_origin_index`. New `structure_event` value: `"MSS_INVALIDATED"`. New `EventType` literal value (`models.py`). |
| Spec section | §19, Decision #6 |
| Decision # | #6 |
| Implementation status | Not implemented |
| Classification | **Missing feature** — described in the spec itself as "the most significant gap identified in the current implementation" (§19) |

### Decision #7 — CHoCH permanence

| | |
|---|---|
| File | None |
| Spec section | §20, Decision #7 |
| Implementation status | Current code already matches (no "failed CHoCH" mechanism exists) |
| Classification | **Test only** |

### Decision #8 — Same-candle event ordering + missing-data guard fix

| | |
|---|---|
| File | `app/analysis/state_machine.py` |
| Class/function | `detect_structure_state` — the missing-data branch (lines 350-363) currently `continue`s before reaching the event-write block (lines 478-498, "5. Store event information"). This means a `structure_event` already determined at Step 1 (CHoCH or, once Decision #6 ships, `MSS_INVALIDATED`) on a row where `close`/ATR is simultaneously NaN is **silently dropped from output today** — confirmed against the live source: Step 1 (lines 217-341) can set `event = "CHoCH"` before the `close`/ATR check at line 347, and the `continue` at line 363 skips straight past the block that would have written it. The state change itself (`current_trend`, `current_state`, protected levels via `store_current_state`) is still applied — only the discrete event marker is lost. |
| Affected variables | Control flow only — no new state variables. Requires restructuring so the missing-data guard skips only the close/ATR-dependent MSS/BOS checks (spec's Steps 5-6), while the row-output write (spec's Step 7) always executes using whatever `structure_event` value Step 3 already produced. |
| Affected outputs | `structure_event`, `event_direction`, `broken_level`, `break_distance`, `required_break_distance` — currently silently `NA` on any row where a swing-driven event coincides with missing close/ATR; must be correctly populated after the fix. |
| Spec section | §22, Decision #8, point 2 (steps 4 & 7); audit clarification |
| Decision # | #8 |
| Implementation status | Not implemented (this is a **bug fix**, not merely a formalization — it exists in the code today regardless of any other decision) |
| Classification | **Bug** |

### Decision #9 — ATR threshold configuration classification

| | |
|---|---|
| File | None |
| Spec section | §24, Decision #9 |
| Implementation status | Current code already treats `minimum_break_atr` as a caller-supplied parameter with a sensible default — matches spec |
| Classification | **Documentation only** |

### Decision #10 — Protected-level status/source fields

| | |
|---|---|
| File | `app/analysis/state_machine.py` |
| Class/function | `detect_structure_state`, `store_current_state` |
| Affected variables | New state: status/source tracking for each of `protected_high` and `protected_low` independently. **Naming ambiguity to resolve before coding** (flagged below, §9 of this roadmap) — recommended: `protected_high_status`, `protected_high_source`, `protected_low_status`, `protected_low_source` (four columns, mirroring the existing `protected_high`/`protected_low` column pair), since the spec's "two independent output columns per protected level" (§26) is written once and applied to both §10 and §11 symmetrically, and the two underlying value columns are already independent today. |
| Affected outputs | Four new DataFrame columns (see above). Values: `status ∈ {active, broken}`, `source ∈ {hl, lh, latest_swing}`. |
| Spec section | §26, Decision #10 |
| Decision # | #10 |
| Implementation status | Not implemented |
| Classification | **Missing feature** + **Output mismatch** |

### Decision #11 — Reseed on lone-HH/LL initialization

| | |
|---|---|
| File | `app/analysis/state_machine.py` |
| Class/function | `detect_structure_state`, Step 1's `neutral → bullish` (lone `HH`, lines 228-230) and `neutral → bearish` (lone `LL`, lines 291-293) branches |
| Affected variables | On entering these branches, seed `protected_low`/`protected_high` from `latest_swing_low`/`latest_swing_high` if one has ever been confirmed; set the new status/source fields (Decision #10) to `active`/`latest_swing`. Should be implemented as one shared internal helper reused by both this decision and Decision #6's post-invalidation reseed (spec: "one reseed rule serves both situations," §27) — a single function, not duplicated logic, per CLAUDE.md rule 4. |
| Affected outputs | `protected_low`/`protected_high` populated in more rows than today (closes the current silent MSS-detection gap during the initialization window, §27). |
| Spec section | §27, Decision #11 |
| Decision # | #11 |
| Implementation status | Not implemented |
| Classification | **Missing feature** |

### Decision #12 — MSS as Order Block source; provisional/confirmed lifecycle

| | |
|---|---|
| File | `app/analysis/order_blocks.py`, `app/analysis/models.py`. No change needed to `app/analysis/order_block_registry.py` — `by_source_event_id()` (lines 215-228) already exists and suffices per spec. |
| Class/function | `order_blocks.py`: `SUPPORTED_STRUCTURE_EVENTS` (line 12) extends to `{"BOS", "MSS", "CHoCH"}`; new invalidation-cascade branch triggered when a row has `structure_event == "MSS_INVALIDATED"`, reconstructing `source_event_id` from `mss_invalidated_origin_index` and invalidating every still-`active` block found via the existing registry lookup; new promotion branch on CHoCH confirmation, doing an identity-first lookup of the pending MSS's own block by `source_event_id`, comparing its `candle_index` against CHoCH's own independently-run source-candle search, and calling the new `OrderBlock.mark_confirmed()` on a match. `models.py::OrderBlock`: new `mark_confirmed()` method (mirrors `mark_mitigated`/`mark_invalidated`/`mark_expired`); `mark_invalidated()` gains an optional `reason` keyword argument (default `"price_penetration"`). |
| Affected variables | New `OrderBlock` fields: `confirmation_status` (default `"confirmed"`, set to `"provisional"` for MSS-sourced blocks at creation), `confirming_event_id`, `confirming_event_type`, `confirmed_time`, `confirmed_index`, `invalidation_reason`. |
| Affected outputs | New DataFrame columns: `order_block_confirmed`, `confirmed_order_block_id`. New `EventType` value: `"ORDER_BLOCK_CONFIRMED"`. **Default output changes for every existing caller of `detect_order_blocks`** — MSS-sourced blocks appear where none did before (spec explicitly calls this a deliberate breaking change, §28 point 8). |
| Spec section | §28, Decision #12; Appendix B |
| Decision # | #12 |
| Implementation status | Not implemented |
| Classification | **Missing feature** + **Output mismatch** (deliberate, spec-mandated) |
| Dependency | Requires Decision #6 implemented first — the invalidation cascade listens for `structure_event == "MSS_INVALIDATED"` and reads `mss_invalidated_origin_index`, neither of which exists until Decision #6 ships. |

### Decision #13 — `strength` field population

| | |
|---|---|
| File | `app/analysis/analysis_engine.py` |
| Class/function | `_build_structure_events` (lines 270-403) |
| Affected variables | None new — `break_distance`/`required_break_distance` already exist as columns |
| Affected outputs | `MarketEvent.strength` populated as `break_distance / required_break_distance` for BOS/MSS/CHoCH events only; stays `None` elsewhere (including, once implemented, `MSS_INVALIDATED` — it is swing-driven at Step 1, not a close-vs-level distance check, so it has no `break_distance`/`required_break_distance` in the relevant sense). |
| Spec section | §29, Decision #13 |
| Decision # | #13 |
| Implementation status | Not implemented (`strength` is currently a dead field — defined, never populated) |
| Classification | **Missing feature** — small, fully isolated |

### Decision #15 — Protected-level lifecycle closed set

| | |
|---|---|
| File | None beyond Decisions #6/#10/#11's own changes |
| Spec section | §10/§11, Decision #15 |
| Implementation status | Satisfied automatically once #6/#10/#11 are correctly implemented, **provided no other code path is introduced that writes `protected_high`/`protected_low` outside the four defined transitions.** This is a review/test obligation, not separate code. |
| Classification | **Test only** (invariant/property test: assert no other structural event, including BOS and MSS confirmation-flag bookkeeping, ever changes these two values) |

### Audit clarifications (from the completed consistency audit)

| Item | File | Classification |
|---|---|---|
| §2 terminology gap (structure state / MSS_INVALIDATED) | None | Documentation only — already fixed in spec |
| provisional/confirmed vocabulary disambiguation | None | Documentation only — already fixed in spec |
| missing-data guard drops step-3 events | `state_machine.py` | **Bug** — see Decision #8 above (same fix) |
| `mss_origin_index` must be a per-row output column | `state_machine.py` | Folded into Decision #6's implementation |
| `confirmation_status` stays `"provisional"` after cascade invalidation | `order_blocks.py` | Folded into Decision #12's implementation — a **"must not"** requirement (the cascade must only ever write `status`, never `confirmation_status`), verified by test, not by additional code |

---

## 2. File-by-file implementation plan

| File | Purpose | Required changes | Risk | Depends on | Estimated difficulty |
|---|---|---|---|---|---|
| `app/analysis/candle_validation.py` (new) | Single validation/normalization entry point (Decision A) | New module: UTC coercion, numeric coercion incl. new ±inf rejection, stable sort, RangeIndex reset, duplicate-timestamp rejection, OHLC relationship checks | Low — pure extraction + one new check, well-isolated | None | Small |
| `app/analysis/state_machine.py` | Core structure state machine | Decision #6 (MSS invalidation branches, `mss_origin_index`), Decision #8 fix (missing-data guard scope), Decision #10 (status/source fields), Decision #11 (reseed helper), later Decision #3 (unified per-cycle classification rewrite) | **Highest** — this file accumulates the most changes across the most decisions; Decision #3 alone is the single riskiest change in the roadmap | Decision #6 depends on #10/#11's reseed helper existing first (spec §19 dependency note) | Large (cumulative); Decision #3 alone is Very Large |
| `app/analysis/market_structure.py` | Legacy classification/BOS/CHoCH pipeline | **No changes** until Decision B Phase 3 (removal only, far future) | None in near term | Decision B Phase 3 | None (near term) |
| `app/analysis/analysis_engine.py` | Pipeline orchestration | Replace `_validate_input`/`_prepare_candles` with the new validation component (Decision A); extend `_build_structure_events` for `MSS_INVALIDATED` + `strength` (Decisions #6, #13); remove the `classify_market_structure` pipeline step once Decision #3 ships | Medium — orchestration changes are individually small but compound across phases | Decisions A, #6, #13, #3 | Medium |
| `app/analysis/order_blocks.py` | Order Block detection/lifecycle | Decision #12: MSS as source event, invalidation cascade, promotion logic, new output columns | Medium-High — the promotion/invalidation logic is intricate (identity-first lookup, dedup-by-`candle_index`) and must not regress existing BOS/CHoCH-sourced block behaviour | Decision #6 | Large |
| `app/analysis/order_block_registry.py` | Order Block storage/query | **No changes** — existing `by_source_event_id()` suffices | None | — | None |
| `app/analysis/liquidity.py` | Liquidity pool detection | **No changes** — consumes `structure` column generically; behaviour changes automatically (and correctly) once Decision #3 changes what values that column holds | Low (code) / Medium (regression surface, since output values shift downstream of Decision #3) | Decision #3 (indirect, no code coupling) | None (code); regression-test effort only |
| `app/analysis/models.py` | Dataclasses / literals | `EventType` += `MSS_INVALIDATED`, `ORDER_BLOCK_CONFIRMED`; `OrderBlock` += `confirmation_status`, `confirming_event_id`, `confirming_event_type`, `confirmed_time`, `confirmed_index`, `invalidation_reason`, `mark_confirmed()`; `mark_invalidated()` gains `reason` kwarg | Low — additive dataclass fields, backward compatible (existing fields untouched) | None | Small |
| `main.py` | FastAPI routes | Decision A: call validation at all 4 candle-consuming endpoints. Decision B Phase 1: new canonical endpoint. Decision B Phase 2: deprecation metadata on legacy endpoint. Decision B Phase 3 (future): remove legacy endpoint | Low-Medium — mechanical wiring, but the canonical endpoint's exact response shape needs a naming/shape decision (flagged §9) | Decision A; `analyze_market()` already exists | Small-Medium |
| `app/mt5/market.py` | MT5 candle retrieval | **No changes** — Decision A wraps its output, does not modify it | None | — | None |
| `app/indicators/technical.py` | ATR/EMA/RSI/MACD indicators | **No changes** — untouched by every decision in the spec | None | — | None |
| `tests/` (new) | Test suite | Currently **does not exist at all** (§34 confirms). Must be created before any behaviour-changing phase, per CLAUDE.md and the spec's own testing-acceptance-criteria section | High if skipped — every decision above claims correctness only against the spec's own listed test requirements; there's nothing today to run | None | Large (one-time bootstrap), then incremental per phase |

---

## 3. Dependency graph

```
Decision A (candle validation)  ──────────────┐
                                               ├──> Decision B Phase 1 (canonical endpoint)
Test infrastructure bootstrap ────────────────┘         │
        │                                                ├──> Decision B Phase 2 (deprecation notice)
        │                                                │            │
        │                                                │            └──> Decision B Phase 3 (legacy removal) [gated, future]
        │
        ├──> Decision #13 (strength)                [independent — no upstream dependency]
        │
        ├──> Decision #4 / #7 / #9 (test-only)      [independent — pin existing behaviour]
        │
        ├──> Decision #10 + #11 + #15 (status/source, reseed, closed set)
        │            │
        │            └──> Decision #6 (MSS invalidation)   [reuses #11's reseed helper]
        │                       │
        │                       └──> Decision #12 (Order Block MSS-sourcing)
        │                                  │
        │                                  └──> Decision B Phase 3 exit criterion satisfied (one of several)
        │
        └──> Decision #3 (per-cycle classification rewrite)   [large, isolated; recommended after #6/#10/#11/#12 — see §4 rationale]
                       │
                       └──> Decision B Phase 3 exit criterion satisfied (one of several)

Decision #2/#14 (live-safe mode) — BLOCKED, needs its own design-approval step before it enters this graph at all
Decision #5 (Internal Structure) — BLOCKED, needs its own follow-up decisions before it enters this graph at all
```

**Must be implemented first:** test infrastructure (nothing after it can be verified without it); Decision A (both the canonical endpoint and, indirectly, every other phase's HTTP-level testing benefit from centralized validation existing early).

**Must wait for another change:** Decision #6 waits for #10/#11 (shared reseed helper, status/source fields it also writes); Decision #12 waits for #6 (`MSS_INVALIDATED` signal); Decision B Phase 3 waits for #6/#10/#11/#12 and (per §3 Decision B exit criteria) Decision #14 "where applicable."

**Independent (parallelizable):** Decision #13 (strength), Decisions #4/#7/#9 (test-only), Decision B Phase 1 (once Decision A lands), Decision #3's rewrite is architecturally independent of #10/#11/#12's *logic* (per spec §10 point 8: "Decision #3 requires no change to this lifecycle") but touches the *same file* — see §4's sequencing rationale for why it is nonetheless scheduled last among the state-machine changes.

**Parallel work:** Decision #13 and the test-only decisions can run alongside any other phase without coordination. Decision B Phase 1 (endpoint wiring in `main.py`) can proceed in parallel with `state_machine.py` work, since it only calls `analyze_market()` as a black box.

---

## 4. Phase breakdown

Each phase below is scoped to leave the codebase fully working, independently mergeable, and independently regression-tested. No phase depends on a partially-implemented decision from a later phase.

### Phase 0 — Test infrastructure bootstrap
- Establish `tests/` with a test runner (pytest), deterministic candle-fixture builders (fixed, hand-constructed OHLC sequences producing known swing/BOS/MSS/CHoCH sequences), and golden-file comparison helpers.
- No production code changes.
- **Required before Phase 1** — CLAUDE.md and §34 both require tests to exist before any implementation change to `state_machine.py`, and Decision A/B also need endpoint-level test coverage.

### Phase 1 — Decision A: candle validation
- New `candle_validation.py`; `analysis_engine.py` refactored to call it; wired into all 4 `main.py` endpoints.
- Tests: valid-input passthrough, each reject case (NaN, ±inf, duplicate timestamp, bad OHLC relationship, unparseable time, empty frame), HTTP 400 translation at each of the 4 endpoints.
- Leaves codebase working: yes — purely additive validation layer; well-formed input behaviour is unchanged by construction.

### Phase 2 — Decision B Phase 1: canonical endpoint
- New `main.py` route exposing `analyze_market()` output directly.
- Tests: endpoint returns a well-formed `AnalysisResult`-shaped response; legacy endpoint untouched (regression: byte-identical legacy response for a fixed fixture, before and after this phase).
- Leaves codebase working: yes — purely additive.

### Phase 3 — Decisions #10 + #11 + #15: protected-level status/source, reseed, closed set
- `state_machine.py`: new status/source state and columns; shared reseed helper; lone-HH/LL initialization fix.
- Tests: each of the four Decision #15 transitions (Creation/Replacement/Reseed/Clearing) fires under its exact precondition and nowhere else; closed-set invariant test (no other code path — BOS triggering, MSS flag bookkeeping — ever modifies `protected_high`/`protected_low` or their new status/source fields); reseed-at-initialization scenario from §27; provisional-to-permanent upgrade scenario from §10 point 2.
- Leaves codebase working: yes — purely additive columns; existing `protected_high`/`protected_low` value semantics unchanged.

### Phase 4 — Decision #6 + Decision #8 fix: MSS invalidation, missing-data guard
- `state_machine.py`: invalidation branches, `mss_origin_index` tracking/column, `MSS_INVALIDATED` event; missing-data guard restructured so Step-3 events always reach output.
- `analysis_engine.py`: `_build_structure_events` accepts `MSS_INVALIDATED`, builds the origin-index → event-id map.
- `models.py`: `EventType += "MSS_INVALIDATED"`.
- Tests: same-original-direction swing invalidates a pending MSS in both directions; invalidation clears all pending-MSS bookkeeping and reseeds correctly (reusing Phase 3's helper); `mss_invalidated_origin_index`/`mss_origin_event_id` correctly join back to the originating MSS event; missing-data-row CHoCH/MSS_INVALIDATED survives the guard (regression test for the audit-identified bug, independent of whether it's triggered via CHoCH or via this phase's new invalidation path); the two still-`UNDEFINED` §21 table cells (`LH` during `mss_bullish`, `HL` during `mss_bearish`) remain explicitly untriggered/untested-as-out-of-scope, not silently assumed away.
- Leaves codebase working: yes — this closes a documented gap without touching any currently-firing code path (BOS/MSS/CHoCH creation and CHoCH confirmation logic are unchanged).

### Phase 5 — Decision #13: `strength` population
- `analysis_engine.py::_build_structure_events` only.
- Tests: `strength == break_distance / required_break_distance` for BOS/MSS/CHoCH, exactly `1.0` at threshold, `> 1.0` above threshold, `None` where distance fields are unavailable.
- Can run in parallel with Phases 3-4; listed here for sequencing clarity only.
- Leaves codebase working: yes — populates a previously-dead field; no consumer depends on it being `None` today (unenforceable to prove universally, but it is a new, additive value — no removal, no rename).

### Phase 6 — Decision #12: Order Block MSS-sourcing
- `order_blocks.py`, `models.py` (`OrderBlock` fields, `mark_confirmed()`), `analysis_engine.py` (`ORDER_BLOCK_CONFIRMED` if surfaced through structure-event metadata — actually sourced from `order_blocks.py` itself, per Appendix B).
- Tests: MSS-sourced block created `provisional`; invalidation cascade fires only on still-`active` blocks and never re-invalidates a terminal one; promotion fires exactly once, matches by `candle_index`, never re-promotes; mismatched-`candle_index` case creates an independent CHoCH-sourced block; BOS/CHoCH-sourced blocks are `confirmed` by default and never touched by the cascade; `confirmation_status` stays `"provisional"` forever on a cascade-invalidated block (audit clarification, explicit regression test); mutual exclusivity (a given MSS occurrence never both invalidates and promotes).
- **Depends on Phase 4.**
- Leaves codebase working: yes, but this is the phase with the **deliberate breaking output change** — must ship with the MAJOR version bump (§6 below) and cannot be silently merged as if it were additive.

### Phase 7 — Decision #3: per-cycle classification (canonical engine rewrite)
- `state_machine.py`: absorb classification into the unified forward pass; cycle-scoped `previous_high`/`previous_low`, reset at each confirmed CHoCH row; re-integrate Phases 3/4's status/source/invalidation/reseed logic into the new structure.
- `analysis_engine.py`: remove the standalone `classify_market_structure` pipeline step for the canonical path.
- `market_structure.py`: **unchanged** — continues serving the legacy pipeline only.
- Tests: all 7 acceptance criteria in §7 point 9 (CHoCH-confirming swing keeps its completing-cycle label; reset takes effect only after the confirming candle; fresh per-cycle baselines; no retroactive relabeling; determinism under a fixed candle window; **legacy engine's `structure` output is byte-identical throughout this phase** — critical regression gate; exactly one per-cycle engine survives once Decision B Phase 3 eventually completes, not testable until then). Full re-run of every Phase 3/4/6 test against the new architecture.
- **Recommended last** among the state-machine-touching phases — see rationale below.
- Leaves codebase working: yes, provided the byte-identical-legacy-output regression gate passes; this phase changes canonical-pipeline *output values* deliberately (see §6, versioning).

### Phase 8 — Decision B Phase 2: deprecation notice
- `main.py` metadata/header change only.
- Recommended after Phase 7, so consumers are being pointed at a canonical pipeline that is actually functionally complete, not a partial one. Can be moved earlier if the project prefers to signal direction sooner — this is a scheduling preference, not an architectural constraint.

### Future / blocked phases (out of this roadmap's concrete scope)
- Decision #2/#14 (live-safe mode) — needs its own design-approval step first.
- Decision #5 (Internal Structure) — needs its own follow-up decisions first.
- Decision B Phase 3 (legacy removal) — gated on all of the above plus first-party consumer migration; genuinely last.

### Sequencing rationale: why Decision #3 is scheduled after #6/#10/#11/#12, not before

Two valid orderings exist:

1. **(Recommended above)** Implement #10/#11/#6/#12 against the current, globally-classified `state_machine.py` first, then perform Decision #3's rewrite last, folding the already-tested lifecycle logic into the new unified pass.
   - Advantage: every phase before #3 stays small, independently reviewable, and testable against the *current*, well-understood architecture. The spec itself confirms this is safe: "Decision #3 requires no change to this lifecycle" (§10 point 8) — the invalidation/reseed/status mechanics are classification-scheme-agnostic; only the upstream swing *labels* change.
   - Disadvantage: `state_machine.py` is meaningfully rewritten twice — once to add #6/#10/#11/#12's logic, again to re-host it inside #3's unified pass.
2. **(Alternative)** Do Decision #3's rewrite first, then build #6/#10/#11/#12 directly on the new architecture.
   - Advantage: the lifecycle logic is written once, directly against its final home.
   - Disadvantage: the single riskiest, largest change in the roadmap (§8 below) would also be the *first* state-machine change, with no smaller phases having yet exercised the fixture/test infrastructure against real invalidation/reseed scenarios — harder to isolate a regression's cause.

This roadmap recommends ordering (1) for risk-minimization consistent with CLAUDE.md's "small, reviewable phases" mandate, but flags this explicitly as a sequencing preference open to revision, not a spec requirement — either ordering is spec-conformant.

---

## 5. Regression test matrix

| Decision | Unit tests | Integration tests | Regression tests | Edge / failure cases | Live-safe / Historical | Missing-data | Duplicate-event | Expected output |
|---|---|---|---|---|---|---|---|---|
| A (validation) | Each reject rule individually (NaN, ±inf, duplicate ts, bad OHLC, unparseable time, empty, missing columns) | Full `analyze_market()` run with a malformed fixture → `ValueError`; each of 4 endpoints → HTTP 400 | Well-formed fixture output byte-identical before/after refactor | Boundary OHLC (`high == open == close == low`), single-row frame, all-NaN frame | N/A | N/A | N/A | `ValueError` message content stable (existing message text preserved where unchanged) |
| B Phase 1 | New endpoint route logic | Canonical endpoint returns valid `AnalysisResult` JSON for a known fixture | Legacy endpoint response byte-identical pre/post | Empty candle set → same error path as `analyze_market()` today | N/A | N/A | N/A | Canonical response contains structure DataFrame, liquidity, events, snapshot, metadata |
| #3 | Cycle-boundary reset logic in isolation | Full canonical pipeline over a multi-cycle fixture | **Legacy pipeline output byte-identical** (critical gate); canonical pipeline diffs are expected and must be explained per-swing | First-swing-of-series no-label case (once per cycle, not once per series); CHoCH-confirming swing retains old-cycle label | Determinism: identical fixed history → identical output on repeated runs | N/A (classification doesn't read close/ATR) | N/A | Cycle boundary matches confirmed CHoCH row exactly; no earlier swing ever relabeled |
| #4 | Exact-tie fixture (`current_high == previous_high`) | End-to-end tie scenario feeding into liquidity EQH detection | Confirms current `LH`/`LL` behaviour unchanged | Tie at the very first swing of a cycle (no baseline — not a tie at all, must classify as unlabeled, not as a tie) | N/A | N/A | N/A | Equal High → `LH`, Equal Low → `LL` |
| #6 | Invalidation branch for both directions in isolation | Full pipeline: MSS fires → same-direction swing → `MSS_INVALIDATED` emitted, state reverted | Non-invalidating swings (opposite-direction confirmation) still behave exactly as before | Invalidation immediately followed by a fresh MSS on the reasserted trend (same-row precedence per Decision #8); invalidation with no prior `latest_swing_*` to reseed from (residual gap, §27) | Event timestamp must not precede the invalidating swing's own confirmation (§30 invariant, once relevant) | **Explicit test**: invalidating swing confirmed on a row where `close`/ATR is simultaneously NaN — event must still be recorded (Decision #8 fix) | Re-arming after invalidation: a second same-direction swing after reseed must not re-invalidate against a stale tracker | `mss_invalidated_origin_index` correctly joins to originating MSS `event_id`; `current_trend` unchanged throughout |
| #7 | N/A (no new code) | Sharp reversal-of-reversal produces two independent CHoCH events, never a mutated first one | `external_trend` history is append-only across a long multi-reversal fixture | Two CHoCHs on nearby but non-adjacent rows | N/A | N/A | N/A | Second CHoCH does not alter first CHoCH's already-recorded row |
| #8 (ordering + guard fix) | Per-step ordering assertions (CHoCH/MSS_INVALIDATED > MSS > BOS) on synthetic same-row-eligible fixtures | Full pipeline row where swing label and close-based trigger are simultaneously eligible | Existing precedence behaviour (CHoCH > MSS > BOS) unchanged for non-missing-data rows | Row eligible for MSS but already carries a Step-3 event | N/A | **Primary regression target**: CHoCH or `MSS_INVALIDATED` on a NaN-close/ATR row is no longer dropped | At most one `structure_event` per row, verified across full fixture | Exactly one of `{CHoCH, MSS_INVALIDATED, MSS, BOS}` per eligible row |
| #9 | N/A (no code change) | Custom `minimum_break_atr` value produces a proportionally different trigger threshold | Default `0.10` unchanged | Zero-value multiplier (should trigger on any positive distance); very large multiplier (should never trigger) | N/A | N/A | N/A | `required_distance` formula unchanged |
| #10 | Status/source transition logic per case (fresh `HL`/`LH`, MSS firing, reseed, CHoCH clearing) | Full pipeline exposing all 4 new columns correctly at every row | N/A (new columns, no prior behaviour to regress) | Level broken twice in a row without an intervening replacement (status stays `broken`, doesn't re-trigger) | N/A | Missing-data row: status/source columns must still reflect last-known state (via `store_current_state`, unaffected by the Decision #8 guard fix since these are Step-3 writes) | N/A | `source` correctly upgrades `latest_swing → hl/lh` on next real swing, never regresses |
| #11 | Reseed-at-initialization in isolation for both directions | Full pipeline: lone `HH` → immediate `protected_low` seeding when `latest_swing_low` pre-exists | Trend initializing via `HL`/`LH` directly (no gap) unaffected | No prior swing low/high exists at all (residual gap, accepted per spec — must remain `None`, not fabricated) | N/A | N/A | N/A | MSS detectable during the previously-silent gap window whenever a seed value exists |
| #12 | Cascade lookup, promotion match/mismatch logic in isolation | Full pipeline: MSS → Order Block (provisional) → CHoCH → promotion; MSS → Order Block (provisional) → invalidating swing → cascade invalidation | Existing BOS/CHoCH-sourced Order Block creation/mitigation/expiry entirely unchanged | Promotion candle_index mismatch (independent block created); a block already mitigated/expired before its MSS invalidates (cascade must not touch it) | Historical vs. live-safe `confirmation_status` timing per §28 point 7 (deferred until #14 exists — flag as not independently testable yet) | N/A | Promotion fires at most once per block (explicit test attempting a second CHoCH match) | `confirmation_status` transitions exactly `provisional → confirmed` once, or stays `provisional` forever on cascade-invalidated blocks |
| #13 | Formula correctness in isolation | Full pipeline `strength` values spot-checked against `break_distance`/`required_break_distance` | N/A (new field) | Exactly-at-threshold (`strength == 1.0`) | N/A | Row where `break_distance`/`required_break_distance` are `NA` → `strength` stays `None` | N/A | `strength` present for BOS/MSS/CHoCH only |
| #15 | N/A (invariant test) | Full-fixture property test: every row's `protected_high`/`protected_low` change is attributable to exactly one of the four named transitions | Regression against #10/#11/#6's own tests | Attempted mutation via BOS or MSS-flag bookkeeping paths — must assert **no** change | N/A | N/A | N/A | At most one active value per level per row (single-value invariant) |

Golden-file regression tests (full-pipeline output against fixed, hand-verified fixtures) should be established starting Phase 0 and extended, not replaced, at every subsequent phase — per §34's existing requirement.

---

## 6. Breaking changes

| Change | Changes outputs | Changes schema | Changes `EventType` | Changes `MarketEvent` | Changes CSV/DataFrame columns | Changes API contract | Version increment |
|---|---|---|---|---|---|---|---|
| Decision A (validation) | Only for malformed input (400 instead of silent success) | No | No | No | No | Yes — new 400 cases | **MINOR** (new, additive rejection behaviour on well-defined bad input; no change for well-formed input) — final classification deferred to versioning audit per spec §3 point 9 |
| Decision B Phase 1 (canonical endpoint) | No (new endpoint only) | New endpoint's own schema | No | No | No | Additive only | **None** (per §33 — Phase 1 is explicitly non-breaking) |
| Decision B Phase 2 (deprecation notice) | No | No | No | No | No | Documented-status only | **None** |
| Decision B Phase 3 (legacy removal) | Yes — legacy contract stops being served | Yes | N/A (legacy pipeline has no `EventType`) | N/A | N/A | Yes — breaking | **MAJOR** (recorded in spec §33) |
| Decision #3 (per-cycle classification) | **Yes, for the canonical pipeline** — different `HH`/`HL`/`LH`/`LL`, and therefore different downstream BOS/MSS/CHoCH, EQH/EQL, and Order Block output is possible for identical input, versus today's canonical output | No | No | No | No (same columns, different values) | Canonical endpoint's output values change | **Not yet explicitly recorded in §33** — under the spec's own general MAJOR criterion ("event semantics... change that can alter output for previously-valid input," §33) this is MAJOR. This roadmap recommends recording it as such at implementation time; it is not a new judgment call, it follows directly from the already-approved general rule. |
| Decision #6 (MSS invalidation) | Adds new event occurrences (`MSS_INVALIDATED`) where none existed; does not alter any existing `BOS`/`MSS`/`CHoCH` row | New columns (`mss_origin_index`, `mss_invalidated_origin_index`) | Yes — `MSS_INVALIDATED` added | Yes — new metadata keys | Yes — additive columns | Additive | **MINOR** (additive `EventType` value, per §33's own additive-value rule; no existing value removed/renamed) |
| Decision #10 (status/source fields) | No (new columns only) | New columns | No | No | Yes — additive | Additive | **MINOR** or **PATCH** depending on whether the project treats new DataFrame columns as schema-significant; recommend **MINOR** for consistency with #6 |
| Decision #11 (reseed) | Yes — fills previously-`None` cells during the initialization gap | No | No | No | No | New values in existing columns | **PATCH** (brings behaviour into closer conformance with spec without changing the spec) — arguably **MINOR** since it changes when MSS becomes detectable; recommend **MINOR** to be safe |
| Decision #12 (Order Block MSS-sourcing) | **Yes — deliberate, spec-mandated breaking change** (§28 point 8) | New columns, new `OrderBlock` fields | Yes — `ORDER_BLOCK_CONFIRMED` added | No | Yes | Yes — default output changes for every existing caller of `detect_order_blocks` | **MAJOR** (explicitly recorded in spec §33) |
| Decision #13 (strength) | Yes — previously-`None` field now populated | No | No | No (existing field) | No | New values in existing field | **MINOR** |

**Consolidated `pipeline_version` sequence** (assuming the Phase ordering in §4): Phase 1 (MINOR) → Phase 2 (none) → Phase 3 (MINOR) → Phase 4 (MINOR) → Phase 5 (MINOR) → Phase 6 (**MAJOR**) → Phase 7 (**MAJOR**) → Phase 8 (none) → Decision B Phase 3, far future (**MAJOR**).

---

## 7. Migration strategy

- **Existing consumers of `/analysis/market-structure` (legacy):** Zero impact through Phase 8 — the legacy endpoint's `swing_points`/`bos_events`/`choch_events` contract is explicitly frozen (§3 Decision B, point 2) and continues serving unchanged output through Phases 1-8. The only change reaching it at all before Decision B Phase 3 is Decision A's validation layer (malformed input now 400s instead of silently succeeding — a deliberate, spec-approved behaviour change affecting all four candle-consuming endpoints equally, not specific to this one).
- **Consumers of `analyze_market()` directly (if any exist today via internal callers):** Affected starting Phase 3 (classification value changes) and Phase 6 (Order Block default output changes) — these are internal-pipeline consumers, not the public legacy endpoint, and should be identified and enumerated before Phase 3/6 ship.
- **No adapter layer, by explicit invariant** (§3 Decision B point 3; §7 point 7) — migration is voluntary, side-by-side, never simulated. This roadmap does not propose any compatibility shim, and none should be built.
- **Feature flags:** Not recommended and not necessary. Every phase above is additive to the legacy path (which is frozen) and the canonical path has no external consumers yet at Phase 1-2 (it's newly introduced). A flag to "enable" the canonical endpoint is unnecessary since it is a new route, not a behaviour change to an existing one. A flag to revert Decision #3/#6/#12's canonical-pipeline changes would effectively be the prohibited adapter layer in disguise (simulating old canonical output alongside new) — not recommended.
- **Upgrade path:** Consumers migrate from the legacy endpoint to the canonical endpoint at their own pace during Decision B's Phase 1/2 window (§3 point 4), which per this roadmap's phase ordering only becomes a *complete* replacement once Phases 3, 4, 6 (and #14, once unblocked) have shipped — migrating earlier means consuming a canonical pipeline that is still missing MSS invalidation, protected-level status, and Order Block MSS-sourcing.
- **Deprecation path:** Formal deprecation notice (Phase 8) should not go out until the canonical pipeline is functionally at parity or better — recommended to hold Phase 8 until Phase 7 completes, per §4's stated scheduling preference (not a hard architectural requirement).

---

## 8. Implementation risks

- **Decision #3's unified-pass rewrite (highest risk in the roadmap).** Merging swing classification into the state machine, with a cycle-scoped reset, touches the most state, has the least prior test coverage (nothing like it exists today), and is explicitly the one change the spec's own two-pass-bootstrap prohibition (§7 point 6) warns can silently produce a *different, wrong* boundary set if implemented carelessly — not merely a crash risk, a **correctness** risk that could pass casual testing while still being wrong on specific historical-extreme scenarios. Mitigation: the byte-identical-legacy-output regression gate (unaffected code path) plus exhaustive per-cycle acceptance-criteria tests (§7 point 9) before this phase is considered done.
- **Hidden coupling inside `state_machine.py`'s single large function.** Every one of Decisions #6/#8/#10/#11/#3 modifies the same ~500-line function. The risk is not any one change in isolation but cumulative entanglement — e.g., Decision #6's invalidation branch and Decision #10's status-transition-on-MSS-fire both need to fire in the correct relative order within Step 3, and Decision #8's ordering invariant must continue to hold after every subsequent addition. Mitigation: re-run the full Decision #8 ordering test suite after every phase that touches this file, not only once at the end.
- **Ordering bugs specifically around the missing-data guard (Decision #8 fix).** This is a genuine bug fix to control flow that every other phase's Step-3 writes now depend on being correct (Decision #6's `MSS_INVALIDATED`, in particular, is created at Step 3 and is exactly the kind of event this fix protects). Getting the restructuring wrong in either direction (still dropping events, or now double-writing) is a determinism violation, not merely a missed edge case. Mitigation: this fix should ship in the *same* phase as Decision #6 (as scheduled, Phase 4) so the new event type is validated against the very code path that used to lose it.
- **Regression risk on `liquidity.py` and `order_blocks.py` from Decision #3, despite zero code changes to either file.** Both consume the `structure` column's *values*, not its computation method — Decision #3 changes those values for the canonical pipeline. This is a "silent regression" risk class: no diff will appear in either file's own code, yet their output can change. Mitigation: explicit downstream regression fixtures covering EQH/EQL clustering and Order Block anchoring before/after Phase 7, not just structure-engine-level tests.
- **State-persistence / re-entrancy assumptions.** `detect_structure_state` and its Decision #3 successor are pure functions over a full candle DataFrame with no external state — there is no live persistence layer today. This is a risk only in the sense that any future live-safe implementation (Decision #14, blocked) will need to either re-run the full history each time or introduce genuine incremental state; this roadmap does not resolve that, but flags it as the central open question the eventual live-safe design step must answer.
- **Race conditions:** None identified — the pipeline is single-threaded, synchronous, row-by-row, with no concurrent writers to any state variable. This risk category is not applicable to the current architecture.
- **Performance risk:** Decision #3's unified pass is not asymptotically worse than today's two-pass pipeline (still O(n) over candles), but merges two loops into one with more branching per row — worth a basic performance regression check on a large fixture (e.g., multi-year M1 data) given ATR/indicator calculations already run at that scale, but not expected to be a material risk.
- **Test-infrastructure risk (Phase 0).** Because no test suite exists today, every risk above is currently *unverifiable*. This is listed last only because it is procedurally first — every other risk in this list is actually a statement about what Phase 0's fixtures must be capable of catching.

---

## 9. Final readiness assessment

**Is the specification implementation-ready?** Yes, for every decision except #2/#14 and #5, which the spec itself explicitly and correctly defers rather than leaving ambiguous.

**Is any architectural ambiguity still blocking implementation?** No architectural ambiguity remains in the frozen decisions. Two **implementation-detail** (not architectural) naming questions are worth explicit sign-off before Phase 3/Phase 2 coding begins, since the spec's wording under-specifies them and this document should not silently guess:

1. **Decision #10's exact column names.** The spec says "two independent output columns per protected level" (§26) without stating whether that means one generic pair or four columns (two per level, matching the existing `protected_high`/`protected_low` structure). This roadmap recommends four columns (`protected_high_status`, `protected_high_source`, `protected_low_status`, `protected_low_source`) as the interpretation consistent with the existing schema, but this is a naming choice, not a re-opening of Decision #10's architecture.
2. **Decision B Phase 1's exact canonical endpoint path/name.** The spec approves "a new endpoint (or an explicit version identifier on the existing path)" (§3 point 1) without naming it. Needs a one-line decision (e.g., `/analysis/structure/{symbol}/{timeframe}` vs. a versioned variant of the existing path) before `main.py` work begins.

Neither of these blocks planning or sequencing — both are resolvable in minutes at the start of their respective phase, and are flagged here only so Phase 2/Phase 3 do not begin with an implicit, unrecorded guess.

**Readiness statement:** Implementation may begin at Phase 0 (test infrastructure) followed by Phase 1 (Decision A) once this roadmap is reviewed and approved. No further architectural decisions are required to start; the two items above are naming confirmations, not design work.
