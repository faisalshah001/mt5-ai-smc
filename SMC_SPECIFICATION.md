# Smart Money Concepts (SMC) Market Structure Engine — Specification

**Status:** DRAFT — first revision, not yet approved for implementation.
**Document version:** 0.1.0
**Applies to (intended canonical engine):** `app/analysis/state_machine.py` (`detect_structure_state`), built on `app/analysis/market_structure.py` (`detect_swing_points`, `classify_market_structure`), orchestrated by `app/analysis/analysis_engine.py` (`analyze_market`).

This document defines the exact trading rules the canonical SMC market-structure engine must follow. It is written **before** any implementation change, per project workflow. No Python file has been modified to produce this document.

## How to read this document

Every rule below is tagged with exactly one of the following labels:

- **`[CURRENT BEHAVIOUR]`** — what the existing code actually does today, cited with file and line numbers. This is a factual statement, not an endorsement.
- **`[PROPOSED SPEC]`** — a rule this document proposes as the go-forward specification, distinct from what the code currently does (or in addition to it), based directly on the existing architecture and standard ICT/SMC convention.
- **`[DECISION REQUIRED]`** — a point where the current implementation, ICT convention, or the project's own goals are ambiguous or contested. **No rule is asserted here.** This is a question for you, the project owner, to resolve before implementation. Per instruction, uncertain trading interpretations are never silently resolved.
- **`[APPROVED SPEC]`** — an architectural decision approved for implementation, even if code is still pending.
- **`[INVARIANT]`** — a behavioural or lifecycle guarantee that implementations must preserve.
- **`[IMPLEMENTATION STATUS]`** — indicates whether the approved specification has or has not yet been implemented.

Where `[CURRENT BEHAVIOUR]` and `[PROPOSED SPEC]` differ, the difference is called out explicitly so the gap is never implied.

---

## 1. Purpose and scope

This specification governs the **canonical Smart Money Concepts market-structure engine** — the single, authoritative rule set for:

1. Swing detection
2. HH / HL / LH / LL classification
3. Protected high and protected low tracking
4. BOS (Break of Structure)
5. MSS (Market Structure Shift)
6. CHoCH (Change of Character)
7. Liquidity (informative — interface contract only, see Appendix A)
8. Order Blocks (informative — interface contract only, see Appendix B)
9. Future downstream confluence engines (extension points only, see Appendix C)

Items 1–6 are specified normatively (exact trigger/confirmation/invalidation rules). Items 7–9 already exist as working, separately-reviewed modules (`liquidity.py`, `order_blocks.py`) that consume the structure engine's output; this document defines their **interface contract** with the structure engine, not new trading rules for them — re-specifying their internal behavior at BOS/MSS/CHoCH-level rigor was not requested and is out of scope here.

**Out of scope:** Fair Value Gaps, Breaker Blocks, and Premium/Discount/Equilibrium zones are named in `CLAUDE.md`'s "Trading Logic" section as concepts to respect, but **no code implementing them exists in this repository today**. This document does not invent rules for them. Internal Structure detection is named alongside them in `CLAUDE.md`, and no code implementing it exists today either — but unlike the other three, its canonical **architecture** is now approved (§9, Decision #5); its detailed trading rules (internal BOS/MSS/CHoCH, protected/candidate-level equivalents, and interaction with liquidity/order-block detection at the internal degree) remain unspecified pending further decisions before implementation.

This document does **not** authorize implementation. It does not modify, rename, or delete any file. It does not add API endpoints.

## 2. Terminology

| Term | Definition |
|---|---|
| Swing high / swing low | A pivot candle confirmed by a symmetric left/right lookback window (§4, §5). |
| HH / LH / HL / LL | Higher High, Lower High, Higher Low, Lower Low — classification of a confirmed swing relative to the previous confirmed swing of the same type (§7). |
| External trend | The **confirmed** directional bias of the market (`neutral`/`bullish`/`bearish`). Changes only on a confirmed CHoCH. |
| Structure state | The **working** state of the engine (`neutral`/`bullish`/`bearish`/`mss_bullish`/`mss_bearish`). Changes on MSS creation, MSS invalidation (§19, Decision #6), and CHoCH. |
| Protected high / protected low | The structural swing level whose break, in the given trend context, initiates an MSS (§10, §11). |
| Candidate level | The most recently confirmed opposite-type swing (LH during a bullish trend's building bearish case, HL during a bearish trend's building bullish case), held in reserve to become the next protected level (§12). |
| BOS (Break of Structure) | A close-confirmed break of the active continuation level, in the direction of the current confirmed trend. Does not change trend or state beyond re-arming the next BOS level. |
| MSS (Market Structure Shift) | A close-confirmed break of the protected level, **against** the current trend. Tentative — does not change `external_trend`. |
| CHoCH (Change of Character) | The **confirmed** reversal: an MSS followed by a specific opposite-direction swing sequence. Changes `external_trend`. |
| Displacement | Sharp directional price movement following a structural event, used by the Order Block engine (Appendix B) — not evaluated by the structure engine itself. |
| Liquidity sweep | A wick-based breach of a liquidity pool followed by a close-based reversion (§28, Appendix A) — evaluated independently of BOS/MSS/CHoCH.
| Provisional / confirmed | **Two independent, non-interchangeable axes share this vocabulary — never assume one implies the other:** (1) `OrderBlock.confirmation_status` (§28, Decision #12) — an Order Block's own MSS→CHoCH promotion lifecycle stage; (2) live-safe data status (§30, Decision #14) — whether a given swing or event's confirmation-lag window has elapsed under live-safe mode. §28 point 7 deliberately couples these two axes at one specific point (an Order Block cannot flip to `confirmation_status = "confirmed"` until its confirming CHoCH is itself no longer live-safe-provisional) — that coupling is a single named rule, not evidence the two axes are the same concept. |

## 3. Candle and price assumptions

- **`[CURRENT BEHAVIOUR]`** Candles originate from `app/mt5/market.py::get_candles`, which converts MT5 Unix timestamps to UTC (`market.py:71-75`) and requires `M1/M5/M15/M30/H1/H4/D1` timeframes.
- **`[CURRENT BEHAVIOUR]`** `app/analysis/analysis_engine.py::_prepare_candles` (lines 81–205) and `_validate_input` (lines 39-78) currently perform the strictest candle validation that exists in the codebase — DataFrame/column/emptiness checks, UTC coercion, numeric coercion of OHLC, stable chronological sort, duplicate-timestamp rejection, and OHLC relationship validation (`high >= open/close/low`, `low <= open/close/high`) — but exist only as private internals of `analysis_engine.py`. No other module or endpoint can call them independently; they are not a shared, reusable component today.
- **`[CURRENT BEHAVIOUR]`** Four live endpoints bypass this validation entirely, calling `get_candles` → `calculate_indicators` (or an equivalent) directly with no validation in between: `/candles/{symbol}/{timeframe}` (`main.py:129-130`), `/strategy/trend/{symbol}/{timeframe}` (`main.py:181-183`), `/strategy/multi-timeframe/{symbol}` (`main.py:210-217`, via injected `candle_loader`/`indicator_calculator`), and `/analysis/market-structure/{symbol}/{timeframe}` (`main.py:280-286`). This is a codebase-wide validation gap, not one specific to structure analysis.

The original Decision #1 has been split into two independent architectural decisions, per project direction (2026-07-28):

- **Decision A** — a single, pipeline-independent candle-validation entry point. **RESOLVED below.**
- **Decision B** — whether `/analysis/market-structure` should migrate from the legacy `market_structure.py` pipeline to the canonical `analyze_market()`/`state_machine.py` pipeline. **RESOLVED below.**

- **`[APPROVED SPEC — Decision A, resolved 2026-07-28]`** A single, standalone, pipeline-independent candle-validation component is approved as the sole entry point for candle-data hygiene across this codebase.

  **1. Architecture.** The component lives outside any single pipeline — it must not import from or depend on `state_machine.py`, `market_structure.py`, `liquidity.py`, `order_blocks.py`, or `analysis_engine.py`'s orchestration function (`analyze_market`). It is a leaf-level utility every pipeline calls into, never the reverse. `analyze_market()` calls it in place of its current private `_validate_input`/`_prepare_candles` implementation, rather than keeping a parallel copy (CLAUDE.md's "avoid duplicate logic").

  **2. Required behaviour — normalize (lossless, unambiguous canonicalization):**
  - Automatic UTC time coercion (numeric epoch-seconds or datetime-like values parsed into tz-aware UTC datetimes).
  - Numeric coercion for the required OHLC fields (`open`, `high`, `low`, `close`), including numeric-looking strings.
  - Stable chronological sorting by `time`.
  - Automatic `RangeIndex` reset (0..n-1) after sorting.
  - Preservation of all unrelated extra columns (e.g. `tick_volume`, `spread`, `real_volume`) unchanged — the component touches only `time`, the required OHLC fields, and `volume` if present.

  **3. Required behaviour — reject (no safe automatic resolution exists):**
  - Missing required columns (`time`, `open`, `high`, `low`, `close`).
  - Any value in a required numeric field that is not coercible to a finite number — this explicitly includes `NaN` **and** positive/negative infinity in `open`/`high`/`low`/`close`. Infinity rejection is a **new** requirement; nothing in the codebase checks for it today.
  - Unparseable timestamps.
  - Duplicate timestamps — no automatic resolution (which row is authoritative is ambiguous).
  - Invalid OHLC relationships (`high < max(open, close, low)`, or `low > min(open, close, high)`).
  - Empty DataFrames.

  **4. Explicitly out of scope.** Minimum candle-history checks remain engine-specific (e.g. `detect_swing_points`'s `left_bars + right_bars + 1` requirement, or any future indicator-warmup requirement) — these depend on caller-supplied parameters the validation component has no visibility into and must not be centralised here.

  **5. Error contract.** Every rejection raises the existing `ValueError` — **no new exception class is introduced.** This decision centralises validation; it does not redesign the exception hierarchy, and a dedicated exception type is not required to resolve it.

  **6. HTTP translation.** Every endpoint that calls this component translates a raised `ValueError` into an HTTP `400` response, consistent with the mapping already implemented identically across all four endpoints today (`main.py:132-136, 185-189, 219-223, 303-307`).

  **7. Required call sites.** All four candle-consuming endpoints — `/candles/{symbol}/{timeframe}`, `/strategy/trend/{symbol}/{timeframe}`, `/strategy/multi-timeframe/{symbol}`, `/analysis/market-structure/{symbol}/{timeframe}` — MUST call this component immediately after candle retrieval and before any indicator or structure computation. `analyze_market()` MUST call the same component rather than a private equivalent.

  **8. Compatibility.** No response-schema change on any endpoint — this is strictly a validation-layer addition. For well-formed candle data (the default case), no behavioural change. For malformed candle data (unsorted, duplicated, NaN, infinite, or OHLC-inconsistent), previously-silent `200 OK` responses carrying incorrect output become explicit `400 Bad Request` responses — a deliberate, beneficial, but visible behaviour change across all four endpoints, not only the structure-analysis endpoint.

  **9. Versioning.** The final `pipeline_version` release classification (MAJOR/MINOR/PATCH per §33) for this decision is **deferred to the final versioning audit** performed at specification freeze — not decided here.

  **`[IMPLEMENTATION STATUS]`** This approves the architecture only. No Python code has been changed.

- **`[APPROVED SPEC — Decision B, resolved 2026-07-28]`** Option C is approved: the legacy `/analysis/market-structure` endpoint is **not** replaced immediately. A new canonical endpoint (or an explicit API version of the existing path) is introduced to expose `analyze_market()`/`state_machine.py` output directly, and the legacy endpoint is retired only after a governed deprecation window — not through an adapter, and not through an abrupt, unannounced replacement.

  **1. Canonical endpoint.** A new endpoint (or explicit version identifier on the existing path) exposes `AnalysisResult` — the full canonical pipeline output (structure DataFrame, liquidity DataFrame, unified `MarketEvent` stream, `StructureSnapshot`, metadata) — directly, without reshaping it to resemble the legacy response.

  **2. Legacy endpoint, unchanged during the migration period.** `/analysis/market-structure` continues to run the existing `market_structure.py` pipeline (`detect_swing_points → classify_market_structure → detect_breaks_of_structure → detect_change_of_character`) and return its existing `swing_points`/`bos_events`/`choch_events` response contract exactly as today, for the full duration of Phase 1 and Phase 2 below. Decision A's shared candle-validation component still applies to this endpoint — that requirement is independent of, and unaffected by, this decision.

  **3. `[INVARIANT]` No adapter layer permitted.** No component may translate canonical `analyze_market()`/`state_machine.py` output back into the legacy response shape or legacy `bos`/`choch` semantics, at any point in this migration. The two pipelines run side by side, each producing its own genuine output — never a simulation of the other. This closes off the adapter-layer approach entirely; it is not an available implementation choice under Decision B.

  **4. Voluntary migration.** Consumers move from the legacy endpoint to the canonical endpoint at their own pace during the deprecation window. No consumer is forced to migrate before Phase 3.

  **5. Deprecation Lifecycle.**

  **Phase 1 — Introduction:**
  - Legacy endpoint remains available, unchanged.
  - Canonical endpoint is introduced, running `analyze_market()`/`state_machine.py` directly.
  - Both endpoints are documented as available, with the canonical endpoint clearly identified as the long-term interface.

  **Phase 2 — Deprecation notice:**
  - Legacy endpoint is officially marked deprecated (in documentation and, where technically feasible, in its own responses/headers).
  - Consumers are instructed to migrate to the canonical endpoint.
  - No new functionality is added to the legacy endpoint from this point forward — it continues to receive Decision A's validation component and nothing else.

  **Phase 3 — Removal:**
  - Legacy endpoint is removed.
  - The canonical endpoint becomes the sole supported interface for market-structure analysis.

  **6. Exit criteria for Phase 3 (all required, not any one alone):**
  - All first-party consumers (n8n workflows, LLM tool integrations, and any other internal caller of the legacy endpoint) have migrated to the canonical endpoint.
  - Implementation of the previously-approved architectural decisions targeting `state_machine.py`/`order_blocks.py` (Decisions #6, #10, #11, #12), and Decision #14's live-safe output mode where applicable, is complete — so the canonical endpoint being promoted to sole interface reflects the fully-resolved engine, not an interim state.
  - The compatibility notice period committed to in Phase 2 has fully elapsed.

  **7. Versioning.** The MAJOR classification (§33) applies specifically to **Phase 3 — legacy endpoint removal** — not to the introduction of the canonical endpoint. Phase 1 (canonical endpoint introduced) is additive: existing consumers continue using the legacy endpoint unchanged, and no existing API contract is broken. Phase 2 (deprecation notice) changes no runtime behaviour at all — the legacy endpoint remains fully functional; only its documented status changes. Phase 3 (legacy endpoint removed) is the breaking event: it is the point at which the legacy `swing_points`/`bos_events`/`choch_events` response contract stops being served, and it is this event — not the earlier introduction of the canonical endpoint — that requires the **MAJOR** `pipeline_version` increment. See §33 for the recorded entry.

  **`[IMPLEMENTATION STATUS]`** This approves the architecture and lifecycle only. No Python code has been changed, no endpoint has been added or removed.

- **`[CURRENT BEHAVIOUR]`** ATR14 (`app/indicators/technical.py`, Wilder smoothing, `alpha=1/14`) is a hard input dependency for every break-distance calculation in the structure engine. No alternative volatility measure is supported.

## 4. Swing-high definition

- **`[CURRENT BEHAVIOUR]`** (`market_structure.py::detect_swing_points`, lines 47–87) A candle at position `p` is a confirmed swing high if and only if:
  `high[p] > max(high[p-left_bars : p])` **and** `high[p] > max(high[p+1 : p+right_bars+1])`
  Both comparisons are **strict greater-than**. Defaults: `left_bars=3`, `right_bars=3`.
- **`[CURRENT BEHAVIOUR]`** Ties are excluded: if `high[p]` equals a neighboring high within either window, `p` is **not** a swing high (edge case, see §32).
- **`[CURRENT BEHAVIOUR]`** Minimum candle count enforced: `left_bars + right_bars + 1`, else `ValueError` (`market_structure.py:32-38`).

## 5. Swing-low definition

- **`[CURRENT BEHAVIOUR]`** (`market_structure.py::detect_swing_points`, lines 47–94) Mirror of §4 using strict less-than:
  `low[p] < min(low[p-left_bars : p])` **and** `low[p] < min(low[p+1 : p+right_bars+1])`

## 6. Swing confirmation delay and right-bar lookahead

- **`[CURRENT BEHAVIOUR]`** A swing point is *labeled* at its own pivot row index `p` (`detect_swing_points`, lines 80-94), but that label can only be computed once `right_bars` candles **after** `p` exist. This means the DataFrame column `swing_high`/`swing_low` at row `p` encodes information that would not be knowable in real time until `right_bars` candles later.
- **`[CURRENT BEHAVIOUR]`** `detect_structure_state` (and `classify_market_structure`) consume the `structure` column exactly as given, in row-index order, with no awareness of this confirmation lag. All downstream BOS/MSS/CHoCH timestamps are therefore "as of" a candle that would not have been actionable at that moment in a live feed.
- **`[PROPOSED SPEC]`** This lag must be documented as an explicit, load-bearing property of the pipeline (not an implementation detail), because it directly affects §30 (historical vs. live behaviour) and §31 (determinism/reproducibility framing).
- **`[APPROVED SPEC — Decision #2, resolved 2026-07-28, linked to Decision #14 / §30]`** The canonical engine MUST expose a "confirmed as of" boundary for live-trading consumers. This project targets MT5 and future live-trading use, so historical-only behaviour is not an acceptable end state. The full live-safe output model — including the distinct timestamps this boundary depends on (pivot candle time, swing confirmation time, event detection time) and the provisional/confirmed data distinction — is specified in §30. This entry approves the *requirement*, not the code; implementation is deferred to a later, separately-approved phase.

## 7. HH, HL, LH and LL classification

- **`[CURRENT BEHAVIOUR]`** (`market_structure.py::classify_market_structure`, lines 99-156) Each confirmed swing high is compared only to the **immediately preceding confirmed swing high** (`previous_high`), and each confirmed swing low only to the **immediately preceding confirmed swing low** (`previous_low`). These two reference variables are tracked globally across the **entire series from row 0** and are **never reset**, including across CHoCH/trend-reversal cycles.
- **`[CURRENT BEHAVIOUR]`** The very first confirmed swing high in the series receives **no label** (`structure` stays `pd.NA`) because `previous_high` starts as `None`; same for the first confirmed swing low. Labeling effectively begins on the second swing of each type.
- **`[CURRENT BEHAVIOUR]`** Comparison is strict: `current_high > previous_high → HH else LH` (line 138-141); `current_low > previous_low → HL else LL` (line 149-152). An **exact tie** is classified as `LH`/`LL`, not `HH`/`HL`.
- **`[APPROVED SPEC — Decision #3, resolved 2026-07-28]`** Option B is approved: the canonical engine's HH/HL/LH/LL comparison state resets at each completed trend cycle, replacing the global, never-reset `previous_high`/`previous_low` tracking described above **for the canonical engine specifically** — see point 7 below for the legacy pipeline's unchanged status during Decision B's migration window.

  **1. Cycle boundary.** A completed, confirmed CHoCH (§17/§18) ends the active trend cycle and begins the next one. MSS alone never ends a cycle: per Decision #6 (§19), an MSS that invalidates leaves the current cycle uninterrupted — only a *confirmed* CHoCH is a boundary.

  **2. Forward-only reset; confirming swing stays with its own cycle.** The comparison baseline resets only for swings *after* the CHoCH-confirming candle. The CHoCH-confirming swing itself remains classified under the baseline of the cycle it completes — it is never reclassified against the new cycle it causes to begin.

  **3. `[INVARIANT]` No retroactive relabeling.** The reset never re-derives or changes a classification already assigned to an earlier swing. Once a swing is labeled `HH`/`HL`/`LH`/`LL`, that label is permanent regardless of any later cycle boundary — preserving §31's determinism guarantee under a fixed candle window.

  **4. New-cycle baseline seeding.** The first qualifying swing high after a cycle boundary establishes that cycle's `previous_high` baseline; the first qualifying swing low establishes its `previous_low` baseline — mirroring, once per cycle, the existing whole-series behaviour already described above (the first swing of each type in the series has no label because no baseline exists yet). Each new cycle's first swing high and first swing low are therefore unlabeled in the same way, once per cycle rather than once per series.

  **5. `[INVARIANT]` Unified forward architecture required.** Classification and cycle/event-boundary detection MUST be computed through a single, causally forward pass. The per-cycle comparison baseline is architecturally the same kind of state as `protected_high`/`protected_low`/`candidate_high`/`candidate_low`, which `state_machine.py` already resets/promotes at the exact CHoCH-confirming row (§10-§12) — classification of the swing at row `T` MUST depend only on state established at rows `< T`, and cycle-boundary detection at row `T` MUST depend only on classification already computed at row `T` and earlier. Any design that requires knowing a cycle boundary before it has been reached is not permitted.

  **6. `[INVARIANT]` Two-pass bootstrap prohibited.** A design that first computes CHoCH boundaries under global classification and then reclassifies per-cycle using those boundaries is **not permitted** as the canonical architecture. This is not merely inefficient — it is unreliable: a swing that global classification forces to `LH`/`HL` because it remains below/above some cycle-irrelevant historical extreme can prevent a CHoCH from being detected under global rules at a candle where per-cycle rules would confirm one, producing a boundary set that does not match the per-cycle-correct one. The unified forward pass in point 5 is required instead.

  **7. Legacy/canonical relationship — temporary, not permanent.** The existing global classifier described above remains **unchanged**, serving *only* the legacy `/analysis/market-structure` pipeline, and *only* for the duration of Decision B's Phase 1 and Phase 2 (§3, Decision B). `[INVARIANT]` No adapter may simulate per-cycle classification output through the legacy response path, or simulate legacy (global) output through the canonical path — extending Decision B's existing no-adapter invariant to this layer.

  **8. Convergence at Decision B Phase 3.** When the legacy endpoint is removed (Decision B, Phase 3), the global classifier is removed at the same time if it has no remaining consumers. After Phase 3, only the canonical per-trend-cycle classification engine remains. This specification does not permit permanent duplication of classification logic — the two-engine state during Phase 1/2 is a bounded artifact of Decision B's already-approved migration window, not a requirement Decision #3 introduces independently.

  **9. Acceptance criteria.**
  1. A CHoCH-confirming swing retains the classification computed under the cycle it completes, not the cycle it begins.
  2. The comparison-baseline reset takes effect only for swings after the CHoCH-confirming candle, never at or before it.
  3. The first qualifying swing high and first qualifying swing low following a cycle boundary establish fresh `previous_high`/`previous_low` baselines for that cycle.
  4. No swing classified in a completed cycle is ever relabeled once a later cycle boundary is reached.
  5. Re-running the canonical engine against an identical, fixed candle history produces identical classification output every time (§31).
  6. The legacy endpoint's `structure` column output remains byte-identical throughout Decision B's Phase 1 and Phase 2.
  7. Exactly one per-trend-cycle classification engine remains once Decision B's Phase 3 completes.

  **`[IMPLEMENTATION STATUS]`** This approves the architecture and its acceptance criteria only. No Python code has been changed.
- **`[APPROVED SPEC — Decision #4, resolved 2026-07-28]`** Option A is approved: exact-tie swings continue folding into `LH`/`LL`, using the existing strict `>` comparison. No new classification value, no tolerance band, and no new column are introduced.

  **1. Verified behaviour.** Using the strict `>` comparison already specified above: an **Equal High** (`current_high == previous_high`) is classified **`LH`**; an **Equal Low** (`current_low == previous_low`) is classified **`LL`**. Both cases fall to the `else` branch of their respective comparison — no separate tie-detection logic is introduced.

  **2. Architectural rationale.** `HH`/`HL`/`LH`/`LL` exist to classify **trend continuation** — whether a swing confirmed progress relative to the prior swing of the same type, a binary question by construction. An exact tie is, by definition, not progress: it did not exceed the prior reference. Classifying it as `LH`/`LL` is therefore the structurally correct answer to the question this column exists to answer, not a fallback or an approximation of some other, missing answer.

  **3. No duplication of liquidity semantics.** The separate significance of an equal high/low as a liquidity target is already fully represented by the dedicated Liquidity engine (`liquidity.py`, §28, Appendix A), which independently detects EQH/EQL pools via tolerance-based clustering (`tolerance_pips`) across swings, regardless of how `classify_market_structure` labels them. That mechanism is purpose-built for liquidity-pool semantics; `classify_market_structure` is purpose-built for trend-continuation semantics. The `structure` column deliberately remains limited to `{HH, HL, LL, LH}` so these two concerns stay cleanly separated — introducing a fifth value here would duplicate a role the Liquidity engine already fills, using a coarser and more appropriate (tolerance-based, not exact-float) comparison for that specific purpose.

  **4. No new tolerance parameter.** Equality is exact floating-point equality, consistent with the existing strict `>` comparison this section already specifies. No ATR- or pip-based tolerance is introduced at this layer; tolerance-based near-equality remains the Liquidity engine's responsibility exclusively (Appendix A's `tolerance_pips`).

  **5. Interaction with Decision #3.** Once Decision #3's per-cycle classification is implemented, tie detection operates against the active cycle's baseline (§7, Decision #3, point 4) — a tie can only occur from the second qualifying swing of a given type onward within the current cycle, since a cycle's first swing of each type has no baseline to compare against.

  **`[IMPLEMENTATION STATUS]`** This approves the rule and its rationale only. No Python code has been changed.

## 8. Bullish, bearish and neutral structure states

- **`[CURRENT BEHAVIOUR]`** (`state_machine.py`, lines 4-12, 101-102) Two parallel state variables exist:
  - `current_trend ∈ {neutral, bullish, bearish}` — the **confirmed** trend. Changes only on CHoCH.
  - `current_state ∈ {neutral, bullish, bearish, mss_bullish, mss_bearish}` — the **working** state. Changes on both MSS and CHoCH.
- **`[CURRENT BEHAVIOUR]`** `neutral` persists until the engine sees the *second* classified swing overall (since the first swing of each type is unlabeled per §7), and only if that second swing is `HH`, `HL`, `LL`, or `LH` (any of the four can trigger the initial transition — see §27 for the asymmetric consequence of which one does).
- **`[PROPOSED SPEC]`** `current_trend` and `current_state` must always satisfy: `current_state ∈ {current_trend, "mss_" + current_trend}` for `current_trend ∈ {bullish, bearish}`, and `current_state == "neutral"` iff `current_trend == "neutral"`. (This already holds in the current implementation; stated here as an explicit invariant to protect during any future change.)

## 9. Internal structure versus external structure

- **`[CURRENT BEHAVIOUR]`** **No internal/external structure distinction exists anywhere in this codebase.** `detect_swing_points` runs a single pass with one `(left_bars, right_bars)` pair; `state_machine.py` operates on that single swing degree only. `CLAUDE.md` lists "Internal Structure" and "External Structure" as concepts to respect, but no module computes either — what the engine currently calls "structure" corresponds to what ICT terminology usually calls **external** (major swing) structure only, by virtue of whatever `left_bars`/`right_bars` the caller chooses.
- **`[APPROVED SPEC — Decision #5, resolved 2026-07-28]`** Internal Structure is approved, using a hierarchical architecture (Option C) — rejecting both candidate answers originally posed above: not a second, independent structure engine (the "parallel, lower-priority state machine" candidate), and not a single detection pass with post-hoc strength/degree labeling (the "single pass with a strength/degree classification" candidate). The approved architecture is stated as a set of implementation-neutral invariants:

  **1. One canonical swing-detection algorithm.** There is exactly one swing-detection algorithm in the canonical engine (§4/§5). It is not reimplemented, forked, or duplicated for Internal Structure.

  **2. Parameterizable by structural degree.** The same algorithm may be parameterized differently for each structural degree it computes — a coarser configuration for External Structure (unchanged from §4/§5's existing specification), a finer configuration for Internal Structure. Parameterization is not duplication: it is the mechanism §4/§5 already specifies for configuring swing sensitivity, applied to more than one degree rather than exactly one.

  **3. `[INVARIANT]` Internal and External Structure reuse the same swing-detection logic.** Whatever component computes External Structure's confirmed swing highs/lows MUST be the same component — not a separate, independently-maintained implementation — that computes Internal Structure's confirmed swing highs/lows, differing only in its degree parameterization.

  **4. `[INVARIANT]` The hierarchy is created by the classification/state layer, not by separate structure engines.** Internal Structure is not a second, independently-evolving state machine running in parallel with no defined relationship to External Structure. Internal swing classification and Internal BOS/MSS/CHoCH state are scoped to, and reset at, the boundaries of the currently active *External* trend cycle — using the same unified forward-pass, reset-at-CHoCH-row architecture already mandated for External Structure by Decision #3 (§7, points 5–6), applied as a nested, second scope of per-cycle state, computed within the same single forward traversal that already tracks the External degree. This is what distinguishes Internal Structure from an unrelated, independently-parameterized second analysis: its meaning is explicitly subordinate to, and bounded by, the current External cycle, not merely a smaller version of an otherwise-unrelated computation.

  **5. No new detection algorithm.** Determining which swings belong to Internal Structure versus External Structure is not performed by a promotion/demotion classifier applied to a single fine-grained swing set. Such a classifier would need to re-derive windowed significance from raw price data to be correct, duplicating the existing algorithm's function under a different name while adding a new, unproven mechanism. The canonical architecture does not introduce this.

  **6. Determinism.** Because the hierarchy is expressed entirely through cycle-scoped state resets — not through windowed or sliced detection input — Internal Structure inherits the same forward-only, non-retroactive determinism guarantees already established for External Structure (§7, Decision #3, point 3; §31).

  **7. Relationship to Decisions A, B, #3, #4.** Internal Structure is a canonical-pipeline capability only, consistent with Decision B's legacy-endpoint freeze — the legacy pipeline has no Internal Structure concept and is unaffected. It depends directly on Decision #3's already-approved cycle-boundary and unified-forward-pass architecture, which it extends rather than duplicates. Decision #4's rationale (structure classification serves trend-continuation; liquidity/tolerance concerns stay in the Liquidity engine) applies identically at the Internal degree once its own classification rules are specified. Decision A (validation) is unaffected — both structural degrees consume the same validated candle input.

  **8. Scope of this approval.** This resolves the *architecture* Internal Structure must follow if and when it is built — one algorithm, multiple degrees, hierarchy expressed at the state layer. It does not itself specify Internal Structure's detailed BOS/MSS/CHoCH rules, protected/candidate-level equivalents, or its interaction with liquidity/order-block detection at the internal degree; those remain to be specified, following this architecture, before implementation. Decision #15 (§10) subsequently specifies the protected-level lifecycle for the External degree only, for exactly this reason — an Internal-degree equivalent remains explicitly deferred here, not overlooked.

  **`[IMPLEMENTATION STATUS]`** This approves the architecture only. No Python code has been changed.

## 10. Protected high definition

- **`[CURRENT BEHAVIOUR]`** (`state_machine.py`) `protected_high` is the swing-high level whose break, while `current_state == "bearish"`, triggers a bullish MSS (§15).
- Set initially: on the first `LH` while transitioning `neutral → bearish` (line 333) or continuing `bearish` (line 336, ratcheted down on every subsequent `LH`).
- Cleared to `None`: on confirmed bullish CHoCH (line 243).
- Promoted from `candidate_high`: on confirmed bearish CHoCH (line 305) — the `LH` that built up during the `mss_bearish` phase becomes the new `protected_high` for the freshly-confirmed bearish trend.
- **`[CURRENT BEHAVIOUR — flagged in §26]`** Once broken to start an MSS, `protected_high` is **not** cleared or marked broken; it persists unchanged (and keeps being written to the DataFrame via `store_current_state`, lines 160-193) until CHoCH eventually replaces it.
- **`[APPROVED SPEC — see §19, §26, §27]`** In addition to the CHoCH-driven clearing/promotion above, `protected_high` MUST also be re-established when a pending bullish MSS is invalidated (§19, Decision #6) or when `neutral → bearish` initializes via a lone `LL` (§27, Decision #11): seeded from `latest_swing_high`, `protected_level_status = active`, `protected_level_source = latest_swing`, until superseded by the next confirmed `LH` (§26, Decision #10).

- **`[APPROVED SPEC — Decision #15, resolved 2026-07-28]`** The complete Protected High lifecycle (mirrored for Protected Low in §11) is defined as three distinct, non-overlapping lifecycle transitions — **Creation**, **Replacement**, and **Reseed** — plus one paired **Clearing** effect. These are deliberately not conflated: each has a different starting status, a different value source, and a different triggering event.

  **1. Creation — value transitions `None → active`.** Occurs only when no protected level currently exists for the relevant trend direction. Two sub-cases:
  - **Trend-initialization creation:** `neutral → bearish` via a direct `LH` seeds `protected_high` immediately from that swing. If initialization instead occurs via a lone `LL` (no `LH` yet confirmed), Decision #11's reseed rule seeds it from `latest_swing_high` instead (`status = active`, `source = latest_swing`).
  - **CHoCH-promotion creation:** a confirmed bearish CHoCH promotes `candidate_high` (accumulated during the preceding `mss_bearish` phase) into `protected_high` for the newly-confirmed bearish cycle. `protected_high` is `None` immediately beforehand in this case too — it was cleared by the Clearing effect (point 4, below) at the *previous* bullish CHoCH and never set during the intervening bullish cycle, since `protected_high` is not tracked while `current_state == "bullish"`.

  **2. Replacement — value transitions `active → active`.** Occurs only when a currently-*active* (non-broken) protected level is refreshed by a fresh, properly-classified same-type swing, with no invalidation involved. Two sub-cases:
  - **Continuation ratchet:** each new `LH` confirmed during an ongoing bearish trend replaces the prior `protected_high` value with the new swing's price (current behaviour, unchanged).
  - **Provisional-to-permanent upgrade:** if the currently-active value has `source = latest_swing` (i.e., it originated from a Reseed, point 3), the next properly-classified `LH` overwrites it, updating `source` to `hl`/`lh` (Decision #10, §26). This is a Replacement, not a second Reseed, because the level is already `active` when it happens.

  **3. Reseed — value transitions `broken → active`.** Occurs *only* as the resolution of an MSS invalidation (Decision #6, §19): when a pending bullish MSS invalidates, `protected_high` — currently `broken` since the MSS first fired against it — is re-established from `latest_swing_high` per Decision #11's reseed rule (`status = active`, `source = latest_swing`). Reseed never sources from a directly-classified `LH`; only Replacement (point 2) can later upgrade it to one.

  **4. Clearing — the paired, atomic counterpart of CHoCH-promotion creation.** A confirmed bullish CHoCH clears `protected_high` to `None` (current behaviour) in the same row that promotes `protected_low` for the new bullish cycle (§11's Creation). Clearing is not itself a Creation, Replacement, or Reseed of `protected_high` — it is the deterministic, same-event side effect that makes the *next* bearish cycle's eventual Creation event (`None → active`) well-defined rather than ambiguous.

  **5. `[INVARIANT]` Closed set of modifying transitions.** Only the four transitions above (Creation, Replacement, Reseed, and CHoCH's paired Clearing) may modify `protected_high` or `protected_low`. All other state transitions — including BOS triggering (Sections 3/4), MSS confirmation-flag bookkeeping (`bullish_mss_has_hl` etc.), the status-only change at MSS firing (which flips `status` to `broken` without invoking any of the four value-modifying transitions), and, once specified, Internal-degree transitions (Decision #5, §9) — are prohibited from modifying them. This replaces the previous, scattered set of negative statements ("BOS never writes...", "HH classification never...") with a single closed-world rule: modification is enumerated and exhaustive, not merely absent elsewhere.

  **6. Single-value invariant.** At any row, at most one `protected_high` and at most one `protected_low` exist as live state; every transition above replaces, never appends to, the prior value. No historical registry exists — a consumer needing history reads the row-by-row output, where each row already records the then-current value.

  **7. Determinism.** All four transitions are forward-only, depend only on state established at or before the current row, and never retroactively alter a previously-written value — consistent with, and requiring no exception to, Decision #3's determinism guarantees (§7 point 3; §31).

  **8. Interaction with Decisions #3–#5.** Decision #3 requires no change to this lifecycle — it was already correctly cycle-scoped before Decision #3 existed; only the upstream `LH`/`HL` labels this lifecycle reads are affected by Decision #3's per-cycle classification. Decision #4's tie handling flows through unchanged: a tied swing classified `LH`/`HL` is equally eligible to drive Creation or Replacement. Decision #5: this lifecycle is fully specified for the External degree only; an Internal-degree equivalent is not yet specifiable, since it depends on Internal MSS/CHoCH trigger rules Decision #5 explicitly deferred (§9 point 8) — inventing one now would be guessing undecided business logic.

  **`[IMPLEMENTATION STATUS]`** This approves the lifecycle specification only. No Python code has been changed.

## 11. Protected low definition

- **`[CURRENT BEHAVIOUR]`** Mirror of §10: `protected_low` triggers a bearish MSS when broken while `current_state == "bullish"`. Set on first `HL` while `neutral → bullish` (line 270) or continuing `bullish` (line 273, ratcheted up each `HL`). Cleared on bearish CHoCH (line 306). Promoted from `candidate_low` on bullish CHoCH (line 242). Same staleness behavior as §10 applies (§26).
- **`[APPROVED SPEC — see §19, §26, §27]`** In addition to the CHoCH-driven clearing/promotion above, `protected_low` MUST also be re-established when a pending bearish MSS is invalidated (§19, Decision #6) or when `neutral → bullish` initializes via a lone `HH` (§27, Decision #11): seeded from `latest_swing_low`, `protected_level_status = active`, `protected_level_source = latest_swing`, until superseded by the next confirmed `HL` (§26, Decision #10).

- **`[APPROVED SPEC — Decision #15, see §10]`** Mirror of §10's Decision #15: the complete Protected Low lifecycle is defined by the same four transitions — Creation (`None → active`, via a direct `HL` or Decision #11's fallback at trend-initialization, or via bullish-CHoCH promotion of `candidate_low`), Replacement (`active → active`, continuation ratchet or provisional-to-permanent upgrade), Reseed (`broken → active`, via bearish-MSS invalidation per Decision #6), and Clearing (bearish CHoCH clears `protected_low` to `None` in the same row that promotes `protected_high` for the new bearish cycle). The same closed-set-of-modifying-transitions invariant (§10, point 5) and single-value invariant (§10, point 6) apply identically to `protected_low`.

## 12. Candidate protected levels

- **`[CURRENT BEHAVIOUR]`** `candidate_high`/`candidate_low` hold the most recently confirmed `LH`/`HL` price respectively, updated **unconditionally on every occurrence of that structure type regardless of `current_state`** (lines 262-264, 325-327) — including during `neutral`, `bullish`/`bearish` continuation, and `mss_*` pending phases.
- **`[CURRENT BEHAVIOUR]`** Promoted to `protected_low`/`protected_high` specifically at CHoCH confirmation time (lines 242, 305).
- **`[CURRENT BEHAVIOUR — redundancy, not a defect]`** Also reassigned as a side effect of a same-direction BOS (`protected_low = candidate_low` on bullish BOS, line 421; mirror at line 472). In every traced code path this is a no-op, since `candidate_low`/`protected_low` are already kept synchronized by the `HL` continuation branch (line 273). Documented here as current behavior; not proposed for removal without your approval per CLAUDE.md rule 2/3.
- **`[APPROVED SPEC — Decision #15, see §10]`** Promotion of a candidate level to protected (second bullet above) is the CHoCH-promotion sub-case of Decision #15's Creation transition (§10, point 1). A candidate can disappear without ever becoming protected: if its pending MSS invalidates (Decision #6) rather than confirming into CHoCH, the accumulated candidate value is never promoted — it remains available for a future CHoCH, not lost, but that specific value was never "spent." The documented BOS side-effect (third bullet above) is explicitly excluded from Decision #15's closed set of protected-level-modifying transitions (§10, point 5) — it is a no-op precisely because it never actually changes an already-synchronized value, not because it is itself a recognized lifecycle transition.

## 13. Bullish BOS rules

| Field | Rule |
|---|---|
| Required previous state | `current_state == "bullish"` |
| Required protected level | Not required to trigger (BOS is independent of `protected_low`); `protected_low` may be updated as a **side effect** if `candidate_low` is set (line 420-421) |
| Exact trigger condition | `active_bullish_bos_level is not None` AND `broken_bullish_bos_level != active_bullish_bos_level` AND `close - active_bullish_bos_level >= ATR14 * minimum_break_atr` |
| Confirmation condition | None beyond the trigger — single-candle, close-based, immediate |
| State before event | `bullish` |
| State after event | `bullish` (unchanged) |
| Trend before event | `bullish` (unchanged) |
| Trend after event | `bullish` (unchanged) |
| Broken level | `active_bullish_bos_level` (price of the most recent confirmed `HH`) |
| Event direction | `bullish` |
| Duplicate-event guard | `broken_bullish_bos_level` records the last-broken level; re-arms automatically when a fresh `HH` updates `active_bullish_bos_level` to a new, not-yet-broken value |
| Invalidation condition | **`[CURRENT BEHAVIOUR]` None exists.** A confirmed BOS is never retroactively invalidated in the current implementation. |

**`[APPROVED SPEC — Decision #3, see §7]`** Once Decision #3 is implemented for the canonical engine, `active_bullish_bos_level` is set from the most recent `HH` **as classified under the per-trend-cycle rules in §7** — the same triggering mechanism described above, scoped per current cycle rather than compared against the whole series. This is a downstream consequence of Decision #3, not a new rule introduced here.

## 14. Bearish BOS rules

Mirror of §13 using `active_bearish_bos_level`/`broken_bearish_bos_level`, gated on `current_state == "bearish"` (`state_machine.py:451-472`). Trigger: `active_bearish_bos_level - close >= ATR14 * minimum_break_atr`. Per §13's note, `active_bearish_bos_level` is likewise sourced from `LL`-classified swings under per-trend-cycle rules once Decision #3 (§7) is implemented for the canonical engine.

## 15. Bullish MSS rules

| Field | Rule |
|---|---|
| Required previous state | `current_state == "bearish"` |
| Required protected level | `protected_high is not None` — **if it is `None` (see §27), bullish MSS can never fire, silently** |
| Exact trigger condition | `close - protected_high >= ATR14 * minimum_break_atr` AND `broken_bullish_mss_level != protected_high` |
| Confirmation condition | None beyond the trigger for the MSS event itself — it is recorded immediately. (The **reversal** it represents is not "confirmed" until CHoCH, §17.) |
| State before event | `bearish` |
| State after event | `mss_bullish` |
| Trend before event | `bearish` |
| Trend after event | `bearish` — **`[CURRENT BEHAVIOUR — by design per docstring]`** MSS never changes `current_trend` |
| Broken level | `protected_high` |
| Event direction | `bullish` |
| Duplicate-event guard | `broken_bullish_mss_level == protected_high` prevents re-trigger on the same level (largely moot in practice since `current_state` leaves `bearish` immediately upon firing) |
| Invalidation condition | **`[CURRENT BEHAVIOUR]` None exists in code.** Spec resolved — see §19, Decision #6; implementation pending. |

**`[APPROVED SPEC — Decision #3, see §7]`** `protected_high` (and therefore this event's trigger level) derives from `LH`-classified swings (§10), which are subject to per-trend-cycle classification once Decision #3 is implemented for the canonical engine — see §7.

## 16. Bearish MSS rules

Mirror of §15 using `protected_low`, gated on `current_state == "bullish"` (lines 379-397). Requires `protected_low is not None`. Invalidation condition: mirror of §15 — **`[CURRENT BEHAVIOUR]` None exists in code.** Spec resolved — see §19, Decision #6; implementation pending. Per §15's note, `protected_low` is likewise sourced from `HL`-classified swings under per-trend-cycle rules once Decision #3 (§7) is implemented for the canonical engine.

## 17. Bullish CHoCH confirmation rules

| Field | Rule |
|---|---|
| Required previous state | `current_state == "mss_bullish"` |
| Required protected level / flag | `bullish_mss_has_hl == True` — an `HL` must have already been confirmed at some point during the `mss_bullish` phase |
| Exact trigger condition | `structure_type == "HH"` while `current_state == "mss_bullish"` |
| Confirmation condition | The ordered two-swing sequence **HL, then HH**, with no requirement on how many bars elapse between them or on price behavior in between — assuming no intervening same-original-direction confirmed swing invalidates the pending MSS under §19, Decision #6 |
| State before event | `mss_bullish` |
| Trend before event | `bearish` (never changed by the MSS that preceded it) |
| State after event | `bullish` |
| Trend after event | `bullish` |
| Broken level | `mss_origin_level` — the **original** `protected_high` that triggered the MSS, **not** the new HH's price |
| Event direction | `bullish` |
| Duplicate-event guard | Leaving `mss_bullish` state immediately (all MSS bookkeeping — `bullish_mss_has_hl`, `broken_*_mss_level`, `mss_origin_level` — reset to `False`/`None`, lines 245-253) |
| Invalidation condition | **`[CURRENT BEHAVIOUR]` matches `[APPROVED SPEC]`.** CHoCH is permanent — Decision #7 (§20) formally approves this as the specification, not merely current behaviour; `external_trend` does not revert without a subsequent, independently-confirmed, opposite-direction CHoCH. |

**`[CURRENT BEHAVIOUR — ordering caveat]`** If the confirming `HH` arrives *before* any `HL` during the `mss_bullish` phase, `bullish_mss_has_hl` is still `False`, so that `HH` confirms nothing (lines 232-233). The engine keeps waiting, however long it takes, for an `HL` to eventually appear followed by a *subsequent* `HH`.

**`[APPROVED SPEC — Decision #3, see §7]`** The `HL`/`HH` swings that drive this confirmation are, once Decision #3 is implemented for the canonical engine, classified under per-trend-cycle rules — per §7 point 2, the confirming `HH` itself is classified under the baseline of the cycle it completes, not the new cycle it begins.

## 18. Bearish CHoCH confirmation rules

Mirror of §17: requires `mss_bearish` state and `bearish_mss_has_lh == True`, confirms on the next `LL` (lines 295-317). Per §17's note, the `LH`/`LL` swings driving this confirmation are likewise classified under per-trend-cycle rules once Decision #3 (§7) is implemented for the canonical engine.

## 19. MSS invalidation and failure rules

**`[CURRENT BEHAVIOUR]` No invalidation or failure path exists for a pending MSS. This is the most significant gap identified in the current implementation.**

Precise mechanism: sections 3–4 of `detect_structure_state` (BOS/MSS detection, lines 372-472) are gated strictly on `current_state == "bullish"` or `current_state == "bearish"`. While `current_state` is `mss_bullish` or `mss_bearish`, **neither branch runs at all** — nothing in the function re-evaluates or cancels the pending MSS.

Within the swing-classification branches (Step 1, lines 217-341), only two structure types are handled per pending state:
- `mss_bullish` handles `HH` (confirmation check, line 232) and `HL` (sets `bullish_mss_has_hl`, line 276-278).
- `mss_bearish` handles `LL` (confirmation check, line 295) and `LH` (sets `bearish_mss_has_lh`, line 339-341).

**There is no handling at all** for `LL`/`LH` occurring during `mss_bullish`, or `HH`/`HL` occurring during `mss_bearish` — i.e., a swing that would represent the *original* trend reasserting itself and the reversal attempt failing. Such a swing silently updates only the unconditional bookkeeping (`latest_swing_*`, `active_*_bos_level`/`candidate_*`) and has **zero effect** on `current_state`. The engine can remain in `mss_bullish`/`mss_bearish` indefinitely — through any number of further same-original-direction swings — until an `HL→HH` (or `LH→LL`) pair eventually appears, no matter how much later or how far price has moved in the meantime.

- **`[APPROVED SPEC — Decision #6, resolved 2026-07-28]`** Option A is approved: a confirmed same-original-direction swing immediately invalidates a pending MSS, as a **formal state transition** (not merely a flag reset). Specifically:

  - **Bullish trend, bearish MSS pending (`current_state == "mss_bearish"`):** a confirmed `HH` invalidates the MSS. `current_state` returns to `bullish`.
  - **Bearish trend, bullish MSS pending (`current_state == "mss_bullish"`):** a confirmed `LL` invalidates the MSS. `current_state` returns to `bearish`.

  On invalidation, the specification requires:
  - Clearing the pending MSS state (`current_state` reverted to the pre-MSS trend value; `current_trend` itself never changed during the pending phase, so this is a direct revert).
  - Clearing the MSS confirmation flag for that direction (`bullish_mss_has_hl` or `bearish_mss_has_lh`).
  - Clearing `mss_origin_level`.
  - Clearing the temporary MSS-tracking variable for that direction (`broken_bullish_mss_level` or `broken_bearish_mss_level`).
  - Restoring the previous trend state exactly (`current_trend` unchanged throughout; only `current_state` moves).
  - Ensuring no stale MSS data (flags, origin level, broken-level tracker) remains active or is written to subsequent rows.
  - Re-establishing the protected level on the invalidated side per the reseed rule in §26/§27 (`protected_low`/`protected_high` ← `latest_swing_low`/`latest_swing_high`), since the pre-MSS protected level is, by definition, already broken and cannot simply be left in place.
  - Emitting an **externally observable invalidation signal**, required so downstream engines (specifically the Order Block engine, §28, Decision #12) can react deterministically without re-deriving state-machine internals:
    - `structure_event = "MSS_INVALIDATED"` on the invalidation candle (a new value in the existing single-slot `structure_event` column). **`[CORRECTED — Decision #8, §22]`** This candle cannot simultaneously carry any other structural event — but not because `current_state` is still `mss_*` when Sections 3/4 would evaluate (it isn't: invalidation reverts `current_state` within the same row, before Sections 3/4 run). The true mechanism is `structure_event`'s `is None` guard (§22, point 3): `structure_event` is already set to `"MSS_INVALIDATED"` before Sections 3/4 evaluate, and that guard blocks them regardless of `current_state`'s post-invalidation value.
    - `event_direction` = the **reasserted** (original, pre-MSS) trend direction — the same convention `CHoCH` already uses for its resulting direction.
    - A new column, **`mss_invalidated_origin_index`** = the candle position of the original MSS-creation row. This is the join key: it lets a consumer reconstruct the exact `source_event_id` the Order Block engine already synthesizes for that MSS (`f"STR_MSS_{origin_index:05d}"`, §28) and look up every block sourced from it directly.
    - `broken_level` (existing column) continues to carry the invalidated protected-level price on this row, for transparency — no new field needed for that.
    - This requires `state_machine.py` to additionally track the MSS-creation candle's **position** (not only its price, which `mss_origin_level` already covers) — a new `mss_origin_index` variable, parallel to `mss_origin_level`, cleared at the same two points (CHoCH confirmation, MSS invalidation). **`[APPROVED SPEC — audit clarification]`** "Parallel to `mss_origin_level`" specifically means: `mss_origin_level` is an existing per-row output column, written every row via `store_current_state` throughout the pending-MSS phase (not only on the origin candle itself) — `mss_origin_index` MUST be written the same way, as a per-row output column for the duration of the pending phase, not held solely as an internal, unexposed loop variable that only surfaces once, indirectly, via `mss_invalidated_origin_index` on the eventual invalidation row.
    - On the invalidation candle specifically, `mss_invalidated_origin_index` is populated directly from the internal `mss_origin_index` variable's current value, **before** that variable is cleared as part of this same invalidation transition.
    - The corresponding canonical `MarketEvent` (built by `analysis_engine.py::_build_structure_events`) carries `metadata["mss_origin_index"]` (the join key above) and `metadata["mss_origin_event_id"]` (the original MSS occurrence's own `MarketEvent.event_id`) — the latter closes a pre-existing gap where the Order Block engine's synthesized `source_event_id` and the canonical event stream's `event_id` are independent, non-cross-referenced ID namespaces for the same underlying event.
    - `"MSS_INVALIDATED"` is added to the `EventType` literal (`models.py`) as an additive value — no existing value is removed or renamed (a MINOR-class change under §33).

  This makes MSS invalidation symmetric with MSS creation and CHoCH confirmation: all three are swing/close-driven, formal state transitions with fully-specified bookkeeping — no timers, no arbitrary price-distance constants. Options B and C are rejected: both introduce unjustified magic constants and/or decouple invalidation from the swing-driven evidence the rest of the engine relies on.

  **Note — not addressed by this decision:** an `LH` confirming during `mss_bullish`, or an `HL` confirming during `mss_bearish`, remain **undefined** (no invalidation behaviour specified). Only the same-original-direction swing types listed above (`HH`/`LL`) are covered. See the updated §21 table.

  **Dependency:** this decision depends on Decisions #10 (§26) and #11 (§27) for the protected-level reseed rule referenced above — both are resolved alongside this one.

  **`[IMPLEMENTATION STATUS]`** This approves the rule only. No Python code has been changed.

## 20. CHoCH invalidation rules

**`[CURRENT BEHAVIOUR]`** None exists — a confirmed CHoCH is final; `external_trend` only changes again via the next opposite-direction CHoCH.

**`[APPROVED SPEC — Decision #7, resolved 2026-07-28]`** No "failed CHoCH" concept is approved. A confirmed CHoCH remains permanent: `external_trend` does not revert except via a subsequent, independently-confirmed, opposite-direction CHoCH.

  **1. Rationale — the engine's canonical append-only, forward-only architecture already resolves this.** Every structural event this engine emits — BOS, MSS, CHoCH — is a permanent record of what was determined to be true at the row it was emitted, and the event stream itself is append-only: new market information is always represented by *emitting a new event*, never by mutating or retracting one already emitted. This is not a new principle invented for this decision — it is the exact pattern Decision #6 (§19) already established for MSS: when a pending MSS invalidates, the original `MSS` event is never deleted or rewritten; a separate `MSS_INVALIDATED` event is appended alongside it, and the historical record grows, it does not get corrected. CHoCH permanence is the same rule applied one level up, not a stricter or different one: a reversal-of-the-reversal is represented by *appending* a new MSS and, if it confirms, a new CHoCH — never by reaching back into the stream and un-committing the CHoCH that already fired. `external_trend` must be forward-only for the same reason `structure` (Decision #3) and the protected-level values (Decision #15) are forward-only: each is a value derived from an append-only event history, and none of them can remain correct unless that history itself is immutable.

  **2. A sharp reversal-of-the-reversal is represented by new events, exactly as point 1 requires.** Price reverting sharply back toward the old trend immediately after a CHoCH is not an unrepresentable scenario — it is modeled as a fresh MSS firing against the *new* trend, which then either invalidates (Decision #6, itself append-only per point 1) or confirms into a second, independent CHoCH that flips the trend back. Two real, dated CHoCH events in the append-only record — not one event retroactively erased — is the only representation consistent with this engine's architecture.

  **3. `[INVARIANT]` No retroactive relabeling of `external_trend`.** A "failed CHoCH" mechanism would require un-committing an already-appended, already-permanent event — directly violating the append-only, forward-only architecture and the no-retroactive-relabeling principle it implies, already established as load-bearing by Decision #3 (§7, point 3) and Decision #15 (§10, point 7). This specification does not carve out an exception to that architecture for CHoCH.

  **4. `[INVARIANT]` Closed set of transitions that modify `external_trend`.** Only a confirmed CHoCH may modify `external_trend` — §2's existing definition ("Changes only on a confirmed CHoCH"), now formalized as a closed set, mirroring Decision #15's §10 point 5 pattern. No other event — MSS creation, MSS invalidation, BOS, or a sharp price move that does not itself complete a new CHoCH sequence — may modify it.

  **5. Consistency with Decision #12.** This decision is required for, not merely compatible with, Decision #12's already-approved Order Block promotion invariant (§28, point 5: "Once confirmed, an Order Block can never revert to provisional"). That invariant assumes the CHoCH which triggers promotion is itself permanent; allowing CHoCH to later fail would orphan already-promoted Order Blocks whose confirming event no longer exists. Rejecting "failed CHoCH" is what keeps Decision #12 internally sound.

  **6. No new metadata or annotation is introduced.** A "this CHoCH was quickly followed by an opposing CHoCH" signal is not added as a new field — the append-only event stream already carries this information natively (two `CHoCH` events at nearby timestamps/positions), consistent with point 1's principle that new information is represented by new events, not new fields layered onto old ones.

  **`[IMPLEMENTATION STATUS]`** This approves the rule and its rationale only. No Python code has been changed.

## 21. State transition table

`[CURRENT BEHAVIOUR]`, consolidated from §8–§18. Cells marked **UNDEFINED** have no code path (§19).

| From state | Trigger swing/break | Additional condition | To state | `external_trend` change |
|---|---|---|---|---|
| `neutral` | `HH` confirmed | — | `bullish` | `neutral → bullish` |
| `neutral` | `HL` confirmed | — | `bullish` | `neutral → bullish` (see §27 — leaves `protected_high` unset) |
| `neutral` | `LL` confirmed | — | `bearish` | `neutral → bearish` |
| `neutral` | `LH` confirmed | — | `bearish` | `neutral → bearish` (see §27 — leaves `protected_low` unset) |
| `bullish` | close breaks `active_bullish_bos_level` by ATR | level not already broken | `bullish` | none (BOS) |
| `bullish` | close breaks `protected_low` by ATR | level not already broken | `mss_bearish` | none (MSS is tentative) |
| `bearish` | close breaks `active_bearish_bos_level` by ATR | level not already broken | `bearish` | none (BOS) |
| `bearish` | close breaks `protected_high` by ATR | level not already broken | `mss_bullish` | none (MSS is tentative) |
| `mss_bullish` | `HL` confirmed | — | `mss_bullish` (flag set only) | none |
| `mss_bullish` | `HH` confirmed | `bullish_mss_has_hl == True` | `bullish` | `bearish → bullish` (CHoCH) |
| `mss_bullish` | `HH` confirmed | `bullish_mss_has_hl == False` | `mss_bullish` (no-op) | none |
| `mss_bullish` | `LL` confirmed | any | `bearish` — **`[APPROVED SPEC, pending implementation — Decision #6]`** MSS invalidated | none (reverts to pre-MSS trend, already `bearish`) |
| `mss_bullish` | `LH` confirmed | any | **UNDEFINED — no code path** (§19); not addressed by Decision #6 | — |
| `mss_bearish` | `LH` confirmed | — | `mss_bearish` (flag set only) | none |
| `mss_bearish` | `LL` confirmed | `bearish_mss_has_lh == True` | `bearish` | `bullish → bearish` (CHoCH) |
| `mss_bearish` | `LL` confirmed | `bearish_mss_has_lh == False` | `mss_bearish` (no-op) | none |
| `mss_bearish` | `HH` confirmed | any | `bullish` — **`[APPROVED SPEC, pending implementation — Decision #6]`** MSS invalidated | none (reverts to pre-MSS trend, already `bullish`) |
| `mss_bearish` | `HL` confirmed | any | **UNDEFINED — no code path** (§19); not addressed by Decision #6 | — |

## 22. Same-candle event priority

- **`[CURRENT BEHAVIOUR]`** `event` is a single-slot variable per candle — **at most one structural event can be recorded per candle.**
- **`[CURRENT BEHAVIOUR]`** Precedence order, derived from execution order and the `event is None` guards: **CHoCH (Step 1, swing-classification-driven) > MSS (Step 3/4, close-driven) > BOS (Step 3/4, close-driven).** Concretely: if a candle both confirms a CHoCH via its swing label *and* would otherwise qualify for an MSS/BOS via its close price, the CHoCH is recorded and the close-based checks are skipped entirely for that candle, because `event` is already set to `"CHoCH"` before Step 3/4 runs (line 234 sets `event`; the guards at lines 376 and 427 both require `event is None`).
- **`[APPROVED SPEC — Decision #8, resolved 2026-07-28]`** The single-slot design is retained and formalized: **at most one `structure_event` may ever be recorded per candle**, governed by a complete, provable, closed-set ordering. This is not merely "current design, unchanged" — this decision establishes that no combination of conditions this engine can produce ever requires more than one, given `current_state`'s single-valuedness and the ordering below. This decision governs the ordering of *structural events* once a row's swing classification is already known; it treats that classification as a fixed input, however it was produced, and does not redefine or extend the swing-classification layer itself (§4/§5/§7, Decisions #3/#4).

  **1. Event-combination catalog.**
  - **Impossible by construction** (proven, not merely unobserved): two `structure_event`s of opposite trigger-type on the same row — e.g. bullish MSS creation and bearish MSS creation, or bullish CHoCH and bearish CHoCH — because each requires a specific, single value of `current_state`, and `current_state` is single-valued at every point in a row's processing. Likewise, a candle cannot both set `active_bullish_bos_level` (via its own new `HH`) and close-break that same, just-set level in the same row, since `close <= high` always holds for that candle (the level equals that candle's own high) — the analogous case for `protected_low`/bearish MSS holds by the same `close >= low` tautology.
  - **Structurally possible, already deterministically resolved:** BOS + MSS eligibility (same trend-context Section) — MSS is checked first, `event is None` gates BOS, so MSS wins; CHoCH + MSS/BOS eligibility — CHoCH is set in Step 1 before Steps 3/4 run, so the same guard means CHoCH wins; MSS_INVALIDATED + a fresh opposite-direction MSS — invalidation is recorded via the same guard before the post-invalidation `current_state` could otherwise qualify for a new MSS check.
  - **Structurally possible, causally identical rather than a true combination:** CHoCH + Swing, and MSS_INVALIDATED + Swing — both CHoCH and MSS_INVALIDATED *are* specific outcomes of swing classification (Step 1); they never occur independently of "a swing," so this is not two co-occurring events, it is one event with a swing-classification cause.
  - **Already fully specified elsewhere, not re-addressed here:** CHoCH + Protected-Level update — Decision #15's Clearing transition is already defined to occur atomically with CHoCH's Creation transition, in the same row, per §10 point 4.
  - **Explicitly out of scope:** whether a single row's swing classification can itself take more than one value is a swing-classification-layer question (§4/§5/§7, Decisions #3/#4), not a structural-event-ordering question, and is not addressed by this decision. This decision consumes whatever classification that layer assigns to a row as a given input. Also out of scope: the unified `MarketEvent` stream (§29), which also carries liquidity and Order Block events, is not single-slot and was never intended to be — multiple events from *different* engines at the same candle are already normal and already supported (e.g. a CHoCH candle that is also the candle an Order Block is promoted on, per Decision #12). This decision governs `structure_event` specifically.

  **2. Ordering, per row, with what is read/written at each step:**
  1. **Snapshot** `trend_before`/`state_before` (read-only, for output metadata; never re-read for control flow).
  2. **Swing-classification bookkeeping** (Step 1) — consumes the row's already-determined swing classification together with `swing_high`/`swing_low`/prices; writes `latest_swing_high`/`latest_swing_low`, `active_bullish_bos_level`/`active_bearish_bos_level`, `candidate_high`/`candidate_low` unconditionally. Required first because every later step depends on these values potentially being current as of this row.
  3. **Swing-classification state-transition effects** (Step 1) — reads confirmation flags (`bullish_mss_has_hl`, `bearish_mss_has_lh`) and `current_state` as they stood before this row's Step 1 began; may write `current_state`, `current_trend`, `mss_origin_level`, `protected_high`/`protected_low` (via Decision #15's transitions), and `structure_event` (CHoCH or MSS_INVALIDATED). Required after bookkeeping because confirmation depends on already-current levels.
  4. **Missing-data guard** — reads `close`, the ATR column; if either is unusable, steps 5–6 (the close/ATR-dependent MSS and BOS checks) are skipped for this row. **`[APPROVED SPEC — Decision #8, audit clarification]`** This guard's scope is limited strictly to steps 5–6: it must never suppress a `structure_event` (or `event_direction`/`broken_level`) already determined at step 3, which does not read `close` or ATR. A row whose swing classification alone produced a CHoCH or MSS_INVALIDATED at step 3 carries that event through to step 7's write regardless of whether `close`/ATR are usable on that same row.
  5. **Close-driven MSS-trigger check** — runs only if `current_state` (as it stands after step 3) is `bullish`/`bearish` and `structure_event is None`; reads `protected_low`/`protected_high`, `close`, ATR; may write `structure_event = "MSS"`, `current_state`, `mss_origin_level`. Required before BOS because MSS is a state-changing event and BOS is not (point 3's general principle).
  6. **Close-driven BOS-trigger check** — runs only if `structure_event` is still `None`; reads `active_*_bos_level`, `close`, ATR; may write `structure_event = "BOS"`, `broken_*_bos_level`.
  7. **Atomic write** — all row output (`structure_event`, `event_direction`, `broken_level`, `break_distance`, `required_break_distance`, `mss_confirmation_step`, `mss_invalidated_origin_index`, `trend_before/after`, `state_before/after`) is written once per row **regardless of whether step 4's guard skipped steps 5–6**, from whichever values were finalized by that point — no partial or repeated writes (Decision #15's single-value-invariant pattern, extended to the full row). This step must always execute, including on missing-data rows (point 4): the current implementation's early `continue` on a missing-data row (`state_machine.py`, lines 350-363) writes only `trend_before/after`, `state_before/after`, and `mss_confirmation_step`, bypassing this write entirely for `structure_event`/`event_direction`/`broken_level`/`break_distance`/`required_break_distance` — a conformant implementation must instead reach this step for every row, so that a step-3-determined event is never silently dropped.

  **3. `[INVARIANT]` Closed-set ordering.** `structure_event` may be written at exactly one of three points in the ordering above — step 3 (CHoCH or MSS_INVALIDATED), step 5 (MSS), or step 6 (BOS) — and only the first of these three that becomes eligible for a given row may write it; every later point in the ordering is unconditionally gated on `structure_event is None`. No other step, and no step outside this ordering, may write `structure_event`. This total order — **CHoCH/MSS_INVALIDATED > MSS > BOS** — is not an arbitrary tie-break; it is state-changing events (CHoCH changes `external_trend`; MSS changes `current_state`; MSS_INVALIDATED reverts `current_state`) preceding the one non-state-changing event (BOS, which changes neither). Any future implementation is conformant if and only if it can be shown to implement exactly this ordering, with no step reading a value this same row's later steps will write.

  **4. Interaction with previously frozen decisions — no contradictions.**
  - **Decision #3** (per-cycle structure): the ordering above is agnostic to whether `structure` is globally or per-cycle classified — it governs what happens *after* classification is known for the row, which Decision #3 already specifies is available before `detect_structure_state` processes it (§7, points 5–6).
  - **Decision #4** (tie classification): unaffected — a tied swing is classified exactly as any other `LH`/`LL` before this ordering ever runs.
  - **Decision #5** (hierarchy): this ordering governs the External degree; an Internal-degree equivalent remains deferred with Decision #5 and Decision #15 for the same, already-stated reason (§9, point 8).
  - **Decision #6** (MSS invalidation): step 3's inclusion of MSS_INVALIDATED, and its precedence over steps 5–6, is exactly what makes Decision #6's note in §19 about same-candle exclusivity precise rather than approximate — see the corrected note in §19.
  - **Decision #7** (CHoCH permanence): CHoCH's precedence at step 3 and its permanence (§20) are independent properties — this decision governs *which* event is recorded on a contested row, not whether a recorded CHoCH can later change.
  - **Decision #10 / #11** (protected-level status, reseed): read/written entirely within step 3, using the same pre-row snapshot rule as any other step-3 effect — no new interaction.
  - **Decision #12** (Order Blocks): unaffected — Order Block detection is a separate, later pipeline stage consuming the already-finalized `structure_event` column; the "explicitly out of scope" note in point 1 covers this directly.
  - **Decision #15** (protected-level lifecycle): this ordering is what makes Decision #15's four transitions well-defined within a row's processing — Creation/Replacement/Reseed/Clearing are all step-3 effects, subject to the same snapshot rule as confirmation flags.

  **5. Determinism.** The ordering is a fixed, row-local total order with no branch depending on any later step's output — forward-only and requiring no look-ahead. `structure_event` is written exactly once per row, at exactly one of three fixed points, consistent with the append-only architecture already established by Decision #3/#7/#15: a row's recorded event is never revisited by a later row, and a later row's processing never depends on anything but already-finalized prior-row state. Given an identical, fixed candle history, this ordering produces identical `structure_event` values on every run.

  **`[IMPLEMENTATION STATUS]`** This approves the ordering and its invariants only. No Python code has been changed.

## 23. Wick break versus candle-close break

- **`[CURRENT BEHAVIOUR]`** All BOS, MSS, and CHoCH-triggering comparisons in `state_machine.py` use **`close`**, never `high`/`low` wicks (lines 347, 380, 391, 406, 431, 457).
- **`[CURRENT BEHAVIOUR]`** This is consistent with `market_structure.py`'s simpler BOS model (also close-based).
- **`[CURRENT BEHAVIOUR — deliberate contrast]`** The Liquidity engine (`liquidity.py`) uses a **different, intentional** model: sweep detection is wick-triggered (`current_high > pool.level`) and close-confirmed (`current_close < pool.level`) — appropriate since a liquidity sweep is inherently about a wick piercing a level and price reverting. This divergence between structural breaks (close-only) and liquidity sweeps (wick+close) is existing, sensible design — not an inconsistency — and is documented here so it is not mistaken for a defect in a future review.

## 24. ATR break threshold behaviour

- **`[CURRENT BEHAVIOUR]`** `required_distance = ATR14 * minimum_break_atr`, default `minimum_break_atr = 0.10` (i.e., 10% of ATR14). Identical formula and default used by both `market_structure.py` and `state_machine.py`.
- **`[CURRENT BEHAVIOUR]`** Negative ATR raises `ValueError` (line 365-368). Missing/NaN `close` or ATR for a row skips event detection for that row only, but the state snapshot is still recorded (lines 350-363). **`[CORRECTED — Decision #8, §22, point 2, step 4]`** The current implementation's missing-data branch (lines 350-363) also silently drops any `structure_event` already determined by swing classification alone (CHoCH, MSS_INVALIDATED) on that same row, since it `continue`s before reaching the event-write step. This is not approved behaviour — §22, point 2, step 4/7 requires such an event to survive the guard.
- **`[APPROVED SPEC — Decision #9, resolved 2026-07-28]`** The current mechanism — a volatility-scaled minimum-distance filter, `required_distance = ATR14 * minimum_break_atr` — is retained unchanged as the canonical approach. The multiplier's default value (`0.10`) is retained as the canonical default. Neither the algorithm nor the default value is redesigned by this decision; this decision determines what *kind* of thing the multiplier is, and what governs changing it.

  **1. Current rule — where it is used.** `required_distance = ATR14 * minimum_break_atr` gates exactly two event types: BOS creation (bullish and bearish, §13/§14) and MSS creation (bullish and bearish, §15/§16), each via a close-versus-level distance check. It does **not** gate CHoCH confirmation (swing-sequence-driven, §17/§18), MSS invalidation (same-original-direction-swing-driven, §19, Decision #6), or any of Decision #15's four protected-level transitions (Creation/Replacement/Reseed are swing- or CHoCH/invalidation-driven, never ATR-driven directly). Its architectural role is a **structural-significance filter**: distinguishing a genuine, volatility-scaled close beyond a level from ordinary noise around it — a role every BOS/MSS trigger condition already depends on for its exact-threshold semantics, but which no *other* invariant in this specification depends on.

  **2. Is the multiplier architectural?** No — it is a **configuration default**, not an architectural invariant, and the codebase already reflects this correctly: `minimum_break_atr` is a function parameter with a default value, not a constant baked into the trigger formula's logic. What *is* architectural is the **formula's shape** — a volatility-scaled (ATR-relative), not fixed-pip, minimum-distance filter — which this decision does not revisit, per its own stated objective. The specific multiplier value is comparable to `left_bars`/`right_bars` (§4/§5) or Decision #12's `minimum_displacement_atr`: a caller-supplied parameter with a sensible default, not a hardcoded rule. ATR itself already normalizes for instrument and timeframe (ATR14 on a given instrument/timeframe automatically reflects that instrument/timeframe's own typical range), which is why a single multiplier default can reasonably serve as a starting point across instruments without the specification needing to assert it is *optimal* for all of them — that determination is empirical, not architectural, and is explicitly out of scope for this document.

  **3. Determinism.** Changing only the multiplier's value — never its role, never the formula — cannot violate any frozen decision, because in every case the invariants that could be implicated govern mechanisms the multiplier does not touch:
  - **Append-only / forward-only:** the multiplier is one input to a per-row, causally-forward boolean check; for any fixed value supplied for a run, the resulting event stream is computed by the same deterministic mechanism as today. No retroactive or look-ahead dependency is introduced by changing the number.
  - **Decision #3** (per-cycle classification), **Decision #6** (MSS invalidation), **Decision #7** (CHoCH permanence): all swing-classification- or swing-sequence-driven, with no ATR dependency in their own trigger conditions. The multiplier can only affect *how often* an MSS exists to be invalidated or confirmed into CHoCH — an indirect frequency effect, never a violation of the transition mechanics those decisions define.
  - **Decision #8** (event ordering): governs *which* event type wins when more than one is eligible on a row, entirely independent of the numeric threshold that determined eligibility in the first place. A different multiplier changes *whether* MSS or BOS become eligible, never their relative precedence once they are.
  - **Decision #10 / #11** (protected-level status, reseed) and **Decision #15** (protected-level lifecycle): the multiplier gates *when* an MSS reads and breaks an existing protected level, but never how that level was created, replaced, or reseeded — those transitions are swing- and CHoCH/invalidation-driven, not ATR-driven, per point 1.
  - **Decision #12** (Order Blocks): uses its own, independently-configured multiplier (`minimum_displacement_atr`) for a different purpose (source-candle displacement, not structural-break significance). The two parameters are deliberately independent (§5, below) and Order Block's MSS-sourcing lifecycle depends on whether an MSS/CHoCH occurred, not on which multiplier value produced it.

  **4. Calibration policy.** No mandatory empirical tuning is required as a precondition for this decision. The fixed `0.10` default is architecturally sufficient *as a default* — not because it is proven optimal (no evidence for its derivation exists, and this document does not invent one), but because the parameter is already correctly exposed as a caller-supplied override for exactly the cases where a different value is warranted. Requiring empirical derivation of "the correct" per-instrument or per-timeframe value would be guessing business logic this specification is not positioned to invent (CLAUDE.md: never guess business logic). The architectural policy is: **per-instrument or per-timeframe calibration is an explicitly anticipated, always-available configuration choice, not a gap requiring a future decision to unlock.** No tuning is mandatory; tuning is permitted and expected to vary by deployment, without that variation itself being a specification concern.

  **5. `[INVARIANT]` Configuration, not architecture.** `minimum_break_atr` and Decision #12's `minimum_displacement_atr` are independently-configurable numeric parameters, each with a documented default (`0.10` and `1.0` ATR respectively — see Appendix B), supplied per invocation. Changing either value: (a) never requires a specification change, since this decision defines the mechanism and its default, not a permanently-fixed number; (b) never alters the trigger formula's shape or any of the event-ordering, protected-level, or CHoCH-permanence invariants already frozen by Decisions #3/#6/#7/#8/#10/#11/#12/#15; (c) must be supplied as a single, fixed value for the full duration of one analysis run, exactly as `left_bars`/`right_bars` already must be — a run that varied its threshold mid-history would violate §31's fixed-input determinism precondition; (d) may legitimately differ between instruments, timeframes, or runs, entirely at the caller's discretion, without constituting a new architectural decision.

  **6. Future extensibility.** A future volatility-normalised or adaptive threshold model (e.g., a trailing-window-recalibrated multiplier, or a different base volatility measure entirely) could replace the current fixed-multiplier default **without violating any frozen decision**, provided it respects this interface boundary: it must (a) produce a single scalar `required_distance` value per row, fed into the same `close`-versus-`level` comparison already specified in §13–§16; (b) compute that value as a deterministic, forward-only function of already-known data as of that row — no look-ahead, no whole-dataset statistics computed non-causally; and (c) remain independent of Decision #12's `minimum_displacement_atr` unless a future decision explicitly links them. Any model satisfying this boundary is a swap of the threshold-computation step, not a new parallel system — consistent with extending existing architecture rather than duplicating it.

  **`[IMPLEMENTATION STATUS]`** This approves the classification of the multiplier as configuration, the calibration policy, and the configuration invariant only. The algorithm, its default value, and its use in the BOS/MSS trigger formula are unchanged. No Python code has been changed.

## 25. Duplicate-event prevention

- **`[CURRENT BEHAVIOUR]`** This is one of the more robust mechanisms in the file, and is called out positively rather than as a gap:
  - **BOS:** `active_*_bos_level` (the current target) vs. `broken_*_bos_level` (the last-broken target) — re-arms automatically whenever a fresh same-direction swing extreme updates `active_*_bos_level` to a new, unbroken value.
  - **MSS:** `broken_*_mss_level` compared directly against the live `protected_high`/`protected_low` — re-arms whenever the protected level itself moves (new `HL`/`LH` in a continuing trend resets the corresponding broken-tracker, lines 274, 337) or is replaced at CHoCH.
- No known defect in this mechanism from the current review.

## 26. Stale protected-level handling

- **`[CURRENT BEHAVIOUR]`** When MSS fires, the broken `protected_high`/`protected_low` value is **not** cleared, nulled, or flagged. `store_current_state` (lines 160-193) only overwrites a column when the corresponding variable is not `None` — so the **already-invalidated** level continues to be written to every row throughout the `mss_*` pending phase, until CHoCH eventually replaces it (or, per §19, potentially never, if no CHoCH ever confirms).
- **`[CURRENT BEHAVIOUR — consumer-facing risk]`** A consumer reading `protected_low` from a row where `structure_state == "mss_bearish"` sees a level that has already been broken, with no field indicating that fact.
- **`[APPROVED SPEC — Decision #10, resolved 2026-07-28]`** The canonical output adds two independent **output columns** per protected level — `protected_level_status` and `protected_level_source`, present in the output DataFrame wherever `protected_high`/`protected_low` are reported — deliberately separating **validity** from **provenance**:

  - **`protected_level_status`** ∈ `{active, broken}` — whether the currently-reported level is still structurally valid.
    - `active`: the level has not been broken since it was last set.
    - `broken`: the level has been broken (an MSS has fired against it) and has not yet been replaced.
  - **`protected_level_source`** ∈ `{hl, lh, latest_swing}` — where the current value originated.
    - `hl` / `lh`: set from a properly classified `HL`/`LH` swing (the normal continuation/initialization path, §10/§11).
    - `latest_swing`: seeded from `latest_swing_low`/`latest_swing_high` per the reseed rule defined in §27, because no classified `HL`/`LH` was available at the moment a valid level was needed.

  These two fields are **independent** — status describes lifecycle, source describes origin. A reseeded level (`source = latest_swing`) is a fully **active** level, not a third, weaker status; it becomes `broken` the same way any other active level does — by a same-side MSS firing against it — and is later legitimately replaced by a proper `hl`/`lh`-sourced value the next time one is confirmed (§10/§11's existing continuation logic).

  **Transitions:**
  - MSS fires against a `protected_low`/`protected_high` → `protected_level_status = broken` (the value itself is otherwise left as reported, for transparency/debugging, consistent with how `break_distance` is preserved elsewhere in this spec).
  - CHoCH confirms → the old level is discarded (unchanged from current behaviour, §10/§11) and the newly-promoted level (from `candidate_high`/`candidate_low`) is `active`, `source = lh`/`hl`.
  - MSS invalidates (§19, Decision #6) → the reseed rule defined in §27 applies: the invalidated side's protected level becomes `active`, `source = latest_swing` (or, if no swing low/high has ever been confirmed, remains unset — the last-resort edge case in §27).
  - A fresh `HL`/`LH` confirms while a level is `active` with `source = latest_swing` → the level is overwritten with the classified swing price, `source` updates to `hl`/`lh` (this overwrite already happens automatically via the existing continuation assignment, §10/§11 — only the new field bookkeeping is additive).

  **`[INVARIANT]`** A protected level seeded from `latest_swing_low` or `latest_swing_high` is a fully valid (`active`) protected level. It exists only as a temporary structural reference and MUST be automatically replaced by the next correctly-classified `HL` or `LH` protected level through the existing continuation logic, without any special migration step. This is a clarification of lifecycle semantics only — it does not change any behaviour already specified above.

  This does **not** null the broken level immediately — the raw value remains visible (status-flagged) for transparency, refining the original three candidate options into this two-field model.

  **`[IMPLEMENTATION STATUS]`** This approves the data model only. No Python code has been changed.

## 27. Initialization when only HH or LL exists

- **`[CURRENT BEHAVIOUR]`** Trend can initialize `neutral → bullish` via a lone `HH` (line 228-230) **without** `protected_low` ever being set, since only an `HL` sets it (line 270). Symmetrically, `neutral → bearish` via a lone `LL` (line 291-293) leaves `protected_high` unset.
- **`[CURRENT BEHAVIOUR — consequence]`** Bearish-MSS detection is hard-gated on `protected_low is not None` (line 379); bullish-MSS detection is hard-gated on `protected_high is not None` (line 430). **During the gap window — from trend initialization via a lone `HH`/`LL` until the first opposite-type swing (`HL`/`LH`) is confirmed — MSS detection is silently impossible.** A sharp reversal during this window produces no MSS and no CHoCH.
- **`[CURRENT BEHAVIOUR — no gap in the mirror case]`** If instead the trend initializes via a lone `HL` (line 266-270) or lone `LH` (line 329-333), the corresponding protected level **is** set immediately — no gap in that specific case.
- **`[APPROVED SPEC — Decision #11, resolved 2026-07-28]`** This section is the **authoritative definition of the reseed mechanism** referenced elsewhere in this document as "the reseed rule." It closes the initialization gap as follows: when `neutral → bullish` is entered via a lone `HH` (no `HL` yet confirmed), `protected_low` is immediately seeded from `latest_swing_low` if one has ever been confirmed (`protected_level_status = active`, `protected_level_source = latest_swing`). Symmetrically, `neutral → bearish` via a lone `LL` seeds `protected_high` from `latest_swing_high`.

  The resulting value is recorded using the `protected_level_status`/`protected_level_source` output-column model — **§26 (Decision #10) defines that model** and is the section that receives and represents the reseeded value; this section (§27) defines the reseed mechanism itself.

  This uses **no new tracking state**. `latest_swing_low`/`latest_swing_high` are already computed unconditionally by the existing swing-classification logic (§10/§11), regardless of `HL`/`LH`/`HH`/`LL` classification, and are already written to output every row. No running-extreme tracker or additional swing history is introduced.

  **Residual gap (accepted):** if trend initializes via a lone `HH`/`LL` and no swing low/high of *any* classification has ever been confirmed yet in the series (i.e., this is the very first swing seen at all), no seed value exists and the opposite protected level remains unset (`None`) until the first opposite-type swing naturally arrives. This is a genuine, unavoidable start-of-series edge case, not a design gap — accepted per the original third candidate option (document as a known limitation).

  This same rule is what Decision #6 (§19) relies on to re-establish a protected level after MSS invalidation — one reseed rule serves both situations.

  **`[IMPLEMENTATION STATUS]`** This approves the rule only. No Python code has been changed.

## 28. Liquidity sweep versus structural break

- **`[CURRENT BEHAVIOUR]`** The Liquidity engine (`liquidity.py::detect_liquidity_registry`) requires only `{time, high, low, close, structure, swing_high_price, swing_low_price}` (lines 50-58) — it does **not** require or read `structure_event`, `external_trend`, `structure_state`, `protected_high`, or `protected_low`. **Liquidity sweep/break detection runs entirely independently of BOS/MSS/CHoCH trend context.** EQH/EQL pool creation is a pure price-tolerance clustering operation on the `structure` (HH/HL/LH/LL) column, regardless of whether the market is currently `bullish`, `bearish`, or `mss_*`-pending.
- **`[APPROVED SPEC — Decision #3, see §7]`** Because EQH/EQL pool creation reads the `structure` column directly, it inherits Decision #3's classification scoping: for the canonical engine, `structure` values are per-trend-cycle once Decision #3 is implemented, so EQH/EQL pool formation is evaluated against per-cycle-scoped HH/HL/LH/LL labels; for the legacy engine, `structure` remains globally-scoped throughout Decision B's Phase 1/2. This is a downstream consequence of Decision #3's already-approved scope, not a new liquidity-engine rule.
- **`[APPROVED SPEC — Decision #4, see §7]`** The `structure` column's four-value set (`HH`/`HL`/`LL`/`LH`) is a deliberate design choice, not an omission: exact-tie swings fold into `LH`/`LL` (§7) precisely because equal-high/equal-low significance as a liquidity concept is already fully represented here, via tolerance-based EQH/EQL clustering — `classify_market_structure` does not duplicate this semantics with a separate exact-tie label.
- **`[CURRENT BEHAVIOUR]`** The Order Block engine (`order_blocks.py`), by contrast, **is** gated on the structure engine's output — but only partially. `SUPPORTED_STRUCTURE_EVENTS = {"BOS", "CHoCH"}` (line 12) is a **hard validation constraint**: `_normalise_event_types` (lines 31-50) raises `ValueError` for any value outside that set. **`MSS` is not a supported source event for Order Block creation, under any configuration.** This is not merely a default — it cannot be enabled without a code change.
- **`[APPROVED SPEC — Decision #12, resolved 2026-07-28]`** MSS becomes an approved Order Block source event, under a single deterministic lifecycle — **not** a configurable option. `SUPPORTED_STRUCTURE_EVENTS` extends to `{"BOS", "MSS", "CHoCH"}`.

  **1. Eligibility.** A confirmed MSS is eligible to create an Order Block through the same eligibility, body-ratio, source-candle-search, and displacement checks already applied to BOS/CHoCH (§28 current behaviour, `order_blocks.py:581-690`) — no new creation criteria.

  **2. Provisional creation.** An Order Block created from an MSS:
  - retains its existing `source_event_id` and `source_event_type` (`"MSS"`) exactly as synthesized today (`order_blocks.py:695-698, 716-718`) — no new identifier is required, since `source_event_id` already encodes the originating candle's position;
  - is linked to the MSS occurrence by that candle position/index — the same position `mss_invalidated_origin_index` (§19) uses, so the two engines address the same MSS occurrence via one shared coordinate;
  - is created with `confirmation_status = "provisional"` (new field, `OrderBlock.confirmation_status ∈ {provisional, confirmed}`, default `"confirmed"` for BOS/CHoCH-sourced blocks, since those are terminal by construction);
  - is marked provisional in **live-safe mode** for as long as its originating MSS remains pending; in **historical mode**, the field still records the fact factually (the block *was* provisional for however many candles), since historical mode may report the fully-resolved final lifecycle directly (§30).

  **3. Invalidation cascade (mirrors §19's `MSS_INVALIDATED` signal).** When `structure_event = "MSS_INVALIDATED"` fires at some later candle with `mss_invalidated_origin_index = N`:
  - the Order Block engine reconstructs `source_event_id = f"STR_MSS_{N:05d}"` and looks up every block with that `source_event_id` via the existing `OrderBlockRegistry.by_source_event_id()` (`order_block_registry.py:215-228`) — no new registry method required;
  - every **active** block found (`status == "active"`) transitions to `status = "invalidated"` immediately, via the existing `OrderBlock.mark_invalidated()` method (`models.py:453-469`), reused rather than replaced;
  - `mark_invalidated()` gains an optional keyword-only `reason` parameter (`"price_penetration"` default, `"mss_invalidated"` for this path), stored in a new `OrderBlock.invalidation_reason` field, so the cause is distinguishable from ordinary distal-level-breach invalidation;
  - a block already `mitigated`, `invalidated`, or `expired` by price action before its MSS invalidates is **not** re-invalidated by this cascade (status transitions are one-way and already terminal — the cascade only acts on still-`active` blocks);
  - Order Blocks sourced from `BOS` or `CHoCH` are never affected by this cascade, regardless of price proximity.

  **4. Promotion on confirmation (single canonical Order Block per institutional footprint).** When the originating MSS instead confirms into CHoCH:
  - the engine looks up the pending MSS's own Order Block via its known `source_event_id` (identity-first lookup, not a registry-wide scan by price/candle);
  - it then independently runs CHoCH's own source-candle search (unchanged, `order_blocks.py:616-621`) and compares the resulting `candle_index` to the existing MSS-sourced block's `candle_index`;
  - **if they match:** no duplicate Order Block is created. The existing block is promoted in place via a new, required **`OrderBlock.mark_confirmed()`** method — following the same lifecycle-method pattern already established by `mark_mitigated()`, `mark_invalidated()`, and `mark_expired()` — which performs the one-way `provisional → confirmed` transition: `confirmation_status: "provisional" → "confirmed"`, a new `confirming_event_id`/`confirming_event_type = "CHoCH"` pair is recorded, and `confirmed_time`/`confirmed_index` are set (mirroring the existing `mitigated_time`/`mitigated_index` pattern, `models.py:367-368`). `source_event_id`, `source_event_type`, `created_time`, and `created_index` are **never overwritten** — the block's original MSS provenance and creation timestamp are preserved permanently. The output DataFrame gains **`order_block_confirmed`** (boolean) and **`confirmed_order_block_id`** columns, populated on the candle where promotion occurs, following the same event-row pattern already used by `order_block_mitigated`/`mitigated_order_block_id` and `order_block_invalidated`/`invalidated_order_block_id`;
  - **if they differ** (different `candle_index`): the MSS successfully progressed into CHoCH, but CHoCH's search resolved to a different anchor. A separate CHoCH-sourced Order Block is created normally, and the original MSS-sourced block is left as an independent, already-confirmed block (its MSS succeeded, so it was never invalidated).
  - **Deduplication key:** `candle_index` alone. Given the existing source-candle-selection logic (`_find_order_block_candle`, `order_blocks.py:68-99`), a candle's colour strictly determines which event direction may select it, and its OHLC strictly determines the resulting price range — so `candle_index` equality already implies matching direction and matching high/low range; checking those independently is redundant and is not required. This equivalence depends on `_find_order_block_candle`'s current strict-opposite-colour matching rule remaining unchanged — a named invariant to re-examine if that matching logic is ever modified.
  - A new `MarketEvent` type, `"ORDER_BLOCK_CONFIRMED"`, is emitted on promotion, paralleling the existing `ORDER_BLOCK_CREATED`/`MITIGATED`/`INVALIDATED` triad.

  **5. `[INVARIANT]` Promotion is one-way and at most once.** `confirmation_status` transitions `provisional → confirmed` exactly one time per Order Block, for one Order Block's entire lifetime. Once `confirmed`, an Order Block can never revert to `provisional`, and cannot be promoted a second time. Subsequent BOS, CHoCH, or any other structural event — including one that happens to resolve to the same `candle_index` as an already-confirmed block — MUST NOT re-promote it or mutate its `confirming_event_id`/`confirmed_time`. The promotion rule in point 4 applies exclusively to the relationship between one specific pending MSS and its own confirming CHoCH; it is not a general "merge any two events sharing a candle" rule. A later BOS resolving to the same anchor candle as an already-confirmed block creates its own independent, separately-tracked Order Block (a distinct continuation footprint), never a mutation of the earlier one. This one-way guarantee is additionally underwritten by Decision #7 (§20): CHoCH itself is permanent, so the confirming event behind a promotion can never later be un-confirmed out from under it.

  **6. Mutual exclusivity.** Invalidation (point 3) and promotion (point 4) can never both apply to the same MSS occurrence: an MSS resolves exactly one way — invalidated (§19, Decision #6) or confirmed into CHoCH (§17/§18) — never both, by construction of the state machine (Sections 3/4 require `current_state == "bullish"/"bearish"`, never `mss_*`, so only one MSS per direction can ever be pending at a time). **`[APPROVED SPEC — audit clarification]`** A direct consequence: an Order Block invalidated via the cascade (point 3) while `confirmation_status` is still `"provisional"` has that field left permanently unchanged at `"provisional"` — the cascade sets `status = "invalidated"` only (point 3), never `confirmation_status`, since only promotion (point 4) may ever set `confirmation_status = "confirmed"`, and mutual exclusivity guarantees promotion never applies to an MSS that has already invalidated. Such a block therefore coexists indefinitely as `status = "invalidated"`, `confirmation_status = "provisional"` — both fields are simultaneously correct and this is not a contradiction requiring reconciliation.

  **7. Live-safe vs. historical consistency (§30).** Both modes compute the identical final lifecycle for a fixed candle history (§31). Historical mode may report a block's fully-resolved final state directly. Live-safe mode must expose `confirmation_status = "provisional"` while the MSS is genuinely still pending, and may only flip it to `"confirmed"` once the confirming CHoCH event **itself** is no longer provisional under §30's own pivot/confirmation/detection timestamp model — Order Block confirmation timing inherits CHoCH's existing confirmation timing rather than defining a competing one.

  **8. Backward compatibility — deliberate breaking change.** This is **not** configurable and does **not** preserve existing output compatibility: default behaviour changes for every existing caller of `detect_order_blocks` (MSS-sourced blocks now appear where none did before). Per §33, implementation of this decision requires a **MAJOR** `pipeline_version` increment, recorded here as a specification requirement — not deferred as an implementation detail.

  **`[IMPLEMENTATION STATUS]`** This approves the complete lifecycle only. No Python code has been changed.
- **`[CURRENT BEHAVIOUR]`** `order_blocks.py`'s optional `require_liquidity_sweep` flag (default `False`) can cross-reference the independently-computed liquidity sweep direction within the order block's setup leg (`_liquidity_sweep_confirmed`, lines 102-134) — this is the one place liquidity and order-block/structure concepts are connected, and it is opt-in.

## 29. Event fields and required metadata

- **`[CURRENT BEHAVIOUR]`** `MarketEvent` (`models.py`, lines 57-99) fields: `event_id`, `event_type`, `time`, `index`, `direction`, `price`, `broken_level`, `source_id`, `source_type`, `strength`, `description`, `metadata`.
- **`[CURRENT BEHAVIOUR — dead field]`** `strength: Optional[float]` is defined but **never populated anywhere in the codebase** — not by `analysis_engine.py`'s structure-event builder, not by `liquidity.py`, not by `order_blocks.py`. Every `MarketEvent` ever constructed has `strength=None`.
- **`[CURRENT BEHAVIOUR]`** For structure events specifically, `analysis_engine.py::_build_structure_events` (lines 270-403) populates `metadata` with `break_distance`, `required_break_distance`, `trend_before`, `trend_after`, `state_before`, `state_after`, `mss_confirmation_step`, `mss_origin_level` — but only when each source column is present and non-null (lines 352-371).
- **`[PROPOSED SPEC]`** The canonical engine's output contract, going forward, SHOULD guarantee the following fields are always present (not merely "present when available") for every structure event:

| Event type | Required fields |
|---|---|
| BOS (either direction) | `event_type`, `direction`, `time`, `index`, `broken_level`, `break_distance`, `required_break_distance`, `trend_before_event`, `trend_after_event`, `state_before_event`, `state_after_event`, `strength` |
| MSS (either direction) | Same as BOS, plus `mss_origin_level` (== `broken_level` at MSS time) |
| CHoCH (either direction) | Same as BOS, plus `mss_origin_level` (the level that started the MSS being confirmed) and `mss_confirmation_step` |

- **`[APPROVED SPEC — Decisions #6 and #12, resolved 2026-07-28]`** Two new `EventType` values are added (additive, MINOR-class per §33): `MSS_INVALIDATED` (§19 — carries `metadata["mss_origin_index"]` and `metadata["mss_origin_event_id"]`) and `ORDER_BLOCK_CONFIRMED` (§28 Appendix B — carries the promoted Order Block's `order_block_id`, `source_event_id`, and `confirming_event_id`). Neither replaces or renames an existing value.
- **`[APPROVED SPEC — Decision #13, resolved 2026-07-28]`** `strength` is defined as a normalized structural-break strength ratio:

  `strength = break_distance / required_break_distance`

  Rules:
  - A value of `1.0` means the event exactly met the required break threshold (the boundary case).
  - Values greater than `1.0` indicate a break stronger than the minimum required distance.
  - The value is **not clamped** — no upper bound is imposed unless a future specification decision explicitly requires one.
  - `break_distance` and `required_break_distance` MUST remain present in `metadata` alongside `strength`, for transparency and debugging — `strength` is a derived convenience field, not a replacement for its inputs.
  - **`strength` MUST be populated for:** BOS (either direction), MSS (either direction), CHoCH (either direction) — i.e., every structure event for which `break_distance` and `required_break_distance` are computed.
  - **`strength` MUST remain `None`** for any event where this ratio is not applicable or not computable — e.g., events sourced from `liquidity.py` or `order_blocks.py` (no ATR-based break-distance calculation exists for them), or a structure-event row where `break_distance`/`required_break_distance` could not be computed per §24's missing/NaN handling.
  - The `strength` field itself is **not removed** — its existing presence in the `MarketEvent` model is preserved to avoid breaking the current response contract (CLAUDE.md rule 9).
  - This definition does not, by itself, authorize populating `strength` in code — implementation follows a separate approval step per CLAUDE.md workflow (Phase 5/6).
  - **`[APPROVED SPEC — Decision #9, see §24]`** `strength`'s definition is deliberately threshold-value-agnostic: `1.0` always means "exactly met the required break threshold," regardless of what `minimum_break_atr` is configured to. Decision #9's classification of that multiplier as a configuration value, not an architectural constant, therefore has no effect on `strength`'s formula or meaning — only on the numeric `required_break_distance` it divides by.

## 30. Historical-analysis versus live-trading behaviour

- **`[CURRENT BEHAVIOUR]`** Because of the swing confirmation lag (§6), this pipeline's output is **not stable under incremental/live querying**. As new candles close and are appended, previously "final" rows near the tail of a prior response can retroactively gain swing labels (and therefore new BOS/MSS/CHoCH events) that were not visible when that same window was queried earlier — because the confirming `right_bars` candles did not exist yet at that time.
- **`[CURRENT BEHAVIOUR — operational implication]`** A consumer treating this API as an append-only event log would be wrong: re-querying is not guaranteed to only add new events at the end: it can also cause new events to appear near the previous tail.
- **`[APPROVED SPEC — Decision #14, resolved 2026-07-28, linked to Decision #2 / §6]`** This project is intended for MT5 and future live-trading use. Historical-only behaviour is **not** acceptable as the final architecture. The canonical engine's output contract MUST support a **live-safe output mode**, distinct from the existing historical/retrospective mode, defined as follows.

  **Required distinct timestamps per event/swing:**
  - **Pivot candle time** — the timestamp of the candle at row `p` that eventually becomes a confirmed swing high/low.
  - **Swing confirmation time** — the timestamp of the candle `right_bars` after the pivot, at which the swing label first becomes computable (§6).
  - **Event detection time** — the timestamp of the candle on which a BOS/MSS/CHoCH condition is actually evaluated true (i.e., the candle whose `close` triggers the break, or whose swing label completes a CHoCH sequence).

  **Provisional vs. confirmed data:**
  - Data is **provisional** if it depends on a pivot whose confirmation window (`right_bars`) has not yet fully elapsed as of the most recent candle in the query.
  - Data is **confirmed** once its confirmation window has fully elapsed and cannot change on a subsequent query of the same underlying candle history.
  - **`[APPROVED SPEC — invariant]`** No event may be reported with an event timestamp earlier than the candle on which all information required to detect that event became available. Concretely: an event's reported time MUST NOT precede its swing confirmation time (for swing-classification-driven events, e.g. CHoCH) or its own detection time (for close-driven events, e.g. BOS/MSS) — whichever inputs the event actually depends on.

  **Two explicit, non-conflated modes:**
  - **Live-safe mode:** every row/event is annotated with its provisional/confirmed status per the above; whether provisional tail rows are flagged or excluded is a configuration choice this document does not yet fix (implementation detail, later phase).
  - **Historical/retrospective mode:** the existing full-series behaviour (§6, §31) remains supported unchanged, for backtesting and analysis use cases where retrospectively-complete labeling is desired and the confirmation lag is acceptable.
  - These two modes MUST be explicitly and separately selectable — a consumer must never receive live-safe output while believing it is historical, or vice versa.

  **`[IMPLEMENTATION STATUS]`** This decision approves the *requirement* and its shape only. It does **not** authorize code changes. Concrete design (API shape, flagging vs. truncation, configuration surface, interaction with §33 versioning) is deferred to a later, separately-approved implementation phase.

## 31. Determinism requirements

- **`CLAUDE.md`'s explicit mandate:** *"The same candle history must always produce identical outputs. No randomness. No hidden state. No implicit assumptions. Every event must be reproducible."*
- **`[CURRENT BEHAVIOUR — compliant, internally]`** `detect_structure_state` itself contains no randomness, processes rows in strict index order, and derives all output solely from its own accumulated state plus the current row — given a fixed, already-computed `structure` column as input, it is deterministic and reproducible.
- **`[CURRENT BEHAVIOUR — caveat, transitively]`** The determinism guarantee is conditioned on a **fixed, complete, immutable candle window**. It does not hold under incremental append (§30) — this is not "hidden state" inside `detect_structure_state` itself, but it is an implicit assumption baked into the upstream `structure` column it consumes, and CLAUDE.md's "no implicit assumptions" clause is worth reading as applying to the pipeline as a whole, not just this one function.
- **`[CURRENT BEHAVIOUR — minor robustness note]`** Duplicate-event guards use float equality (`broken_bearish_mss_level != protected_low`, `broken_bullish_bos_level != active_bullish_bos_level`). This is safe today because both sides are always assigned from the same source value with no intervening recomputation — IEEE754 float equality is deterministic given identical computation paths — but it is fragile if a future change ever computes either side via a different rounding/derivation path. Flagged as a robustness note, not a current defect.
- **`[APPROVED SPEC — Decision #3, see §7]`** Decision #3's per-trend-cycle classification reset is explicitly forward-only and non-retroactive (§7, point 3) — it introduces no hidden state and no implicit assumption beyond the same fixed-candle-window precondition already required by this section: given an identical, complete candle history, per-trend-cycle classification produces identical output on every run, exactly as the existing global classifier already does.
- **`[APPROVED SPEC — Decision #15, see §10]`** The Protected High/Low lifecycle's four transitions (Creation, Replacement, Reseed, Clearing) are each forward-only and depend only on state established at or before the current row; none retroactively alters a previously-written value. The closed-set-of-modifying-transitions invariant (§10, point 5) additionally guarantees no other state transition can introduce hidden, undocumented modification of these values — determinism here is enumerable, not merely observed.
- **`[APPROVED SPEC — Decision #7, see §20]`** CHoCH permanence is the trend-level counterpart of the same principle: once written, `external_trend` is never retroactively altered by anything other than a subsequent, independently-confirmed CHoCH. A reversal-of-the-reversal produces two forward-only, appended events, never a retroactive edit to the first — extending the append-only, no-retroactive-relabeling architecture already established for classification (Decision #3) and protected levels (Decision #15) to trend itself.
- **`[APPROVED SPEC — Decision #8, see §22]`** The same-candle structural-event ordering is a fixed, row-local total order with no step depending on a later step's output within the same row — forward-only by construction. `structure_event` is written at exactly one of three fixed points (§22, point 3), determined solely by state already established before that point, never by a value a later step in the same row will write.
- **`[APPROVED SPEC — Decision #9, see §24]`** `minimum_break_atr` (and Decision #12's `minimum_displacement_atr`) must be supplied as a single, fixed value for the full duration of one analysis run, exactly as `left_bars`/`right_bars` already must be (§24, point 5) — this is a precondition of, not an exception to, the fixed-input determinism guarantee this section already requires. A run that varied either threshold mid-history would violate that precondition; a run using a different, but still fixed, value than another run simply produces a different, equally deterministic and reproducible result.

## 32. Edge cases

Consolidated master list. Each item is tagged `[CONFIRMED CURRENT BEHAVIOUR]` or points to its `[DECISION REQUIRED]` number.

1. Tie prices in swing detection are excluded from being swing points at all (strict `>`/`<`) — §4/§5. `[CONFIRMED]`
2. Tie prices in HH/LH classification default to `LH`/`LL` — §7. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #4.** Verified: Equal High → `LH`, Equal Low → `LL`, via the existing strict `>` comparison — no implementation change required.
3. The very first swing high/low ever detected has no HH/HL/LH/LL label — §7. `[CONFIRMED]`
4. Trend initializing via a lone `HH`/`LL` leaves the opposite protected level unset, disabling MSS detection until the first opposite-type swing — §27. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #11 — pending implementation.**
5. MSS has no invalidation/failure path — §19. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #6 — pending implementation.**
6. Protected level goes stale (remains reported) during the MSS-pending phase — §26. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #10 — pending implementation.**
7. CHoCH confirmation requires strict swing ordering (HL must precede HH); an HH arriving first is silently ignored for confirmation purposes — §17. `[CONFIRMED]`, see Decision #6/#7 (related).
8. At most one structural event per candle — §22. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #8** — now a closed-set, provable ordering rather than only current-behaviour observation. Behaviour matches current code; no implementation change required.
9. `classify_market_structure`'s global, never-reset high/low tracking vs. `state_machine.py`'s per-cycle tracking — §7. `[CONFIRMED]` as current code (legacy engine, and canonical engine until implemented); **spec RESOLVED, see Decision #3 — pending implementation.**
10. Liquidity engine ignores trend/BOS/MSS/CHoCH context entirely — §28. `[CONFIRMED]` — out of scope for Decision #12, unchanged.
11. Order Block engine hard-excludes MSS as a source event type — §28. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #12 — pending implementation.**
12. `MarketEvent.strength` is defined but never populated by any code path — §29. `[CONFIRMED]`, see Decision #13.
13. Output is unstable under incremental/live querying due to swing confirmation lag — §30. `[CONFIRMED]`, see Decision #2/#14.
14. The live `/analysis/market-structure` endpoint bypasses shared candle validation and does not call `state_machine.py` at all — §3. `[CONFIRMED]` as current code. **Both halves now spec-resolved:** validation, see Decision A — pending implementation; pipeline, see Decision B — the legacy endpoint is deliberately retained unchanged through Phase 1/Phase 2 of the approved deprecation lifecycle (§3), with a new canonical endpoint introduced alongside it and the legacy endpoint removed only at Phase 3.

## 33. Versioning rules

- **`[CURRENT BEHAVIOUR]`** `analysis_engine.py::analyze_market` stamps `metadata["pipeline_version"] = "2.0.0"` (line 817) as a bare string with no enforcement, no changelog, and no semver policy tied to it.
- **`[PROPOSED SPEC]`**
  - Any change to swing-detection parameters/logic, classification logic, or state-machine transition rules that can alter output for previously-valid input **must** increment `pipeline_version`.
  - Recommended scheme: **MAJOR** = event semantics or response shape change; **MINOR** = new event types/fields added additively; **PATCH** = defect fix that brings behavior into conformance with this specification without changing the specification itself.
  - This document (`SMC_SPECIFICATION.md`) should itself carry a version and changelog (see header). Future rule changes are proposed as diffs to this document **first**, approved, then implemented, then version-bumped together with `pipeline_version`.
- This entire section is a proposal — no versioning policy beyond the bare string currently exists.
- **`[APPROVED SPEC — recorded per Decision #12, resolved 2026-07-28]`** Concrete application of the MAJOR-bump rule above: implementing Decision #12 (§28) — extending `SUPPORTED_STRUCTURE_EVENTS` to include `MSS` — changes default Order Block output for existing callers of `detect_order_blocks` and therefore **requires a MAJOR `pipeline_version` increment** on implementation. This is recorded here as a specification requirement, not left as an implementation-time judgment call.
- **`[APPROVED SPEC — recorded per Decision B, resolved 2026-07-28]`** Decision B's deprecation lifecycle (§3) spans three phases with two distinct versioning outcomes, not one:
  - **Phase 1 (canonical endpoint introduced)** is additive and non-breaking: existing consumers continue using the legacy endpoint unchanged, the legacy response contract is untouched, and no existing API contract is broken. This does not trigger the MAJOR criterion.
  - **Phase 2 (deprecation notice)** changes no runtime behaviour at all — the legacy endpoint remains fully functional; only its documented status changes. No version impact.
  - **Phase 3 (legacy endpoint removed)** is the breaking event and is classified **MAJOR**, on two independent grounds per the scheme above: it is a full response-shape change (the canonical `AnalysisResult`-based output bears no structural resemblance to the legacy `swing_points`/`bos_events`/`choch_events` contract, which is no longer served at all once this phase completes) and an event-semantics change (the legacy `bos`/`choch` fields and the canonical `structure_event` values of the same names are triggered by, and mean, different things). The MAJOR classification applies to this retirement event specifically, not to the earlier introduction of the canonical endpoint. Recorded as a specification requirement, not an implementation-time judgment call.

## 34. Testing acceptance criteria

- **`[CURRENT BEHAVIOUR]`** No test suite exists for this project (confirmed in prior review — only third-party library tests exist under `venv_old`).
- **`[PROPOSED SPEC]`** Before any implementation change to `state_machine.py`, the following must exist:
  - Deterministic fixture-based tests for swing detection (tie handling, minimum-window enforcement, asymmetric `left_bars`/`right_bars`).
  - Classification tests (first-swing no-label behavior, HH/LH/HL/LL sequencing, tie-break behavior — Decision #4, §7: Equal High → `LH`, Equal Low → `LL`).
  - Per-trend-cycle classification tests (Decision #3, §7): the CHoCH-confirming swing retains its completing-cycle classification; the comparison-baseline reset takes effect only for swings after the CHoCH-confirming candle; the first new-cycle swing high and swing low seed fresh baselines; no earlier swing is ever retroactively relabeled; an identical candle history reproduces identical output on every run; the legacy engine's output is unaffected throughout Decision B's Phase 1 and Phase 2.
  - Per-event-type tests for BOS/MSS/CHoCH covering: trigger condition, confirmation condition, duplicate-event guard, and (once resolved) invalidation condition, for both directions.
  - Same-candle priority tests (§22).
  - ATR-threshold boundary tests (exactly-at-threshold vs. just-under).
  - Missing/NaN close or ATR handling tests.
  - A test encoding the stale-protected-level scenario (§26) and the initialization-gap scenario (§27), so their current behavior is pinned and any future fix is a deliberate, visible diff.
  - Protected-level lifecycle tests (Decision #15, §10): each of Creation, Replacement, Reseed, and Clearing fires under its correct precondition and nowhere else; at most one active `protected_high`/`protected_low` value exists at any row; no transition outside the closed set (§10, point 5) ever modifies either value, including BOS triggering and MSS confirmation-flag bookkeeping; a `latest_swing`-sourced (Reseed) value is correctly upgraded to `hl`/`lh`-sourced on the next properly-classified swing (Replacement, not a second Reseed).
  - CHoCH-permanence tests (Decision #7, §20): a confirmed CHoCH's `external_trend` value is never altered by any subsequent event other than a fully independent, later-confirmed opposite-direction CHoCH; a sharp reversal-of-the-reversal produces a second, distinct MSS/CHoCH sequence rather than mutating the first CHoCH's already-recorded output; no event outside a confirmed CHoCH modifies `external_trend`.
  - Same-candle ordering tests (Decision #8, §22): CHoCH/MSS_INVALIDATED takes precedence over MSS, which takes precedence over BOS, on any row where more than one is otherwise eligible; no row ever produces more than one `structure_event`; no step in the ordering reads a value a later step in the same row will write.
  - Golden-file regression tests comparing full-pipeline output against this specification's rules.
- **`[PROPOSED SPEC]`** Any future change to the rules in this document must add or update a test encoding the new rule before the corresponding code change is merged.

## 35. Open design decisions requiring approval

Master list of every `[DECISION REQUIRED]` item raised in this document. Items marked **RESOLVED** have an approved specification (see the referenced section); this approves the rule only — implementation still requires a separate, later approval per the project workflow (Phase 5/6). All other items remain open exactly as originally raised.

1. **(§3)** ~~Should `_prepare_candles`-equivalent validation become a hard precondition inside the structure engine itself, or remain enforced only via `analyze_market()` as the single entry point?~~ **Split into two independent decisions, per project direction (2026-07-28):**
   - **Decision A — Single validation entry point.** **RESOLVED — approved 2026-07-28.** A standalone, pipeline-independent candle-validation component, called by all four candle-consuming endpoints and by `analyze_market()`; see §3. Implementation pending; final versioning classification deferred to the final versioning audit.
   - **Decision B — Live endpoint pipeline migration.** ~~Should `/analysis/market-structure` migrate from the legacy `market_structure.py` pipeline to the canonical `analyze_market()`/`state_machine.py` pipeline?~~ **RESOLVED — approved 2026-07-28.** Option C: the legacy endpoint is retained unchanged and a new canonical endpoint is introduced alongside it; no adapter layer is permitted; migration follows a three-phase deprecation lifecycle (introduction → deprecation notice → removal) with explicit exit criteria; classified **MAJOR**; see §3 and §33. Implementation pending.
2. **(§6)** ~~Does the canonical engine need a "confirmed as of" boundary for live-trading consumers...~~ **RESOLVED — approved 2026-07-28.** A confirmed-as-of boundary is required; see §6 and §30 (linked to Decision #14).
3. **(§7)** ~~Should `classify_market_structure`'s HH/HL/LH/LL comparison reset per trend cycle (coupled to `state_machine.py`'s cycle boundaries) instead of tracking globally across the whole series?~~ **RESOLVED — approved 2026-07-28.** Option B: the canonical engine resets HH/HL/LH/LL comparison state at each confirmed CHoCH boundary via a unified forward architecture (no two-pass bootstrap permitted); the CHoCH-confirming swing stays classified under the cycle it completes; the legacy global classifier remains unchanged only through Decision B's Phase 1/2 and is retired alongside the legacy endpoint at Phase 3, after which exactly one per-cycle engine remains; see §7. Implementation pending.
4. **(§7)** ~~Should exact-tie swing prices receive their own classification, or continue folding into `LH`/`LL`?~~ **RESOLVED — approved 2026-07-28.** Option A: ties continue folding into `LH`/`LL` (Equal High → `LH`, Equal Low → `LL`) via the existing strict `>` comparison — no new classification value, no tolerance band, no new column. Rationale: HH/HL/LH/LL classify trend continuation; equal-high/equal-low significance as a liquidity concept is already represented by the dedicated Liquidity engine (§28, Appendix A), so the structure column deliberately does not duplicate that semantics; see §7.
5. **(§9)** ~~How (if at all) should Internal Structure be added alongside External Structure — second swing-detection pass, single-pass degree classification, or deferred entirely?~~ **RESOLVED — approved 2026-07-28.** Option C: Internal Structure is approved architecturally — one canonical, parameterizable swing-detection algorithm shared by both degrees; the internal/external hierarchy is expressed by the classification/state layer through nested cycle scoping (extending Decision #3's unified forward-pass architecture), not by a separate structure engine or a promotion/demotion classifier. Detailed internal-degree trading rules remain to be specified before implementation; see §9. Implementation pending.
6. **(§19)** ~~What should invalidate a pending MSS...~~ **RESOLVED — approved 2026-07-28.** Same-original-direction confirming swing (`HH` invalidates `mss_bearish`, `LL` invalidates `mss_bullish`) as a formal state transition; see §19. Depends on Decisions #10/#11. Implementation pending.
7. **(§20)** ~~Should a "failed CHoCH" concept be supported, or is a confirmed CHoCH permanent by design?~~ **RESOLVED — approved 2026-07-28.** No "failed CHoCH" concept is introduced; CHoCH remains permanent, grounded in the engine's canonical append-only, forward-only architecture (the same pattern already established for MSS invalidation, Decision #6; classification, Decision #3; and protected levels, Decision #15) and formalized as a closed-set invariant (only a confirmed CHoCH may modify `external_trend`). Required by Decision #12's one-way Order Block promotion invariant. A reversal-of-the-reversal is represented via a subsequent, independent, appended MSS/CHoCH sequence, never by undoing the first. See §20.
8. **(§22)** ~~Should more than one structural event ever be recordable on a single candle?~~ **RESOLVED — approved 2026-07-28.** No — the single-slot design is retained, now backed by a complete, provable closed-set ordering (CHoCH/MSS_INVALIDATED > MSS > BOS, state-changing events preceding the one non-state-changing event) rather than only current-behaviour observation. Governs structural-event ordering only; treats swing classification as a given input and does not extend the swing-classification layer. See §22 (primary), §19 (corrected cross-reference).
9. **(§24)** ~~Does the flat 10%-of-ATR break threshold need empirical, per-instrument/timeframe justification, or is the current constant acceptable as a configurable default?~~ **RESOLVED — approved 2026-07-28.** The algorithm and its `0.10` default are unchanged. The multiplier is classified as a configuration value, not an architectural invariant — no mandatory empirical derivation is required; per-instrument/per-timeframe tuning is an explicitly anticipated, always-available override, formalized by a configuration invariant separating it from Decision #12's independent `minimum_displacement_atr`. See §24. No implementation change required.
10. **(§26)** ~~Should a stale/broken protected level be explicitly flagged...~~ **RESOLVED — approved 2026-07-28.** Two independent fields: `protected_level_status ∈ {active, broken}` and `protected_level_source ∈ {hl, lh, latest_swing}`; see §26. Implementation pending.
11. **(§27)** ~~How should the HH/LL-only trend-initialization gap...~~ **RESOLVED — approved 2026-07-28.** Reseed from `latest_swing_low`/`latest_swing_high` (no new tracking state); see §27. Same rule also serves Decision #6's post-invalidation reseed. Implementation pending.
12. **(§28)** ~~Should Order Block creation be extended to optionally source from `MSS` events...~~ **RESOLVED — approved 2026-07-28.** MSS is an approved (non-configurable) source event under a single deterministic lifecycle: provisional creation, an `MSS_INVALIDATED`-driven invalidation cascade (§19), and one-way promotion into a confirmed CHoCH-backed block (never duplicated) when the same MSS confirms; see §28 and Appendix B. Deliberate breaking change — requires a MAJOR version bump (§33). Implementation pending.
13. **(§29)** ~~Should `MarketEvent.strength` be defined and populated, or removed as dead weight?~~ **RESOLVED — approved 2026-07-28.** Field is kept and defined as `break_distance / required_break_distance`; see §29.
14. **(§30)** ~~Does the project need a live-safe output mode...~~ **RESOLVED — approved 2026-07-28, linked to Decision #2.** A live-safe output mode is required as a distinct mode alongside historical/retrospective analysis; see §30. Implementation deferred to a later phase.
15. **(§10/§11)** Protected High / Protected Low lifecycle. **RESOLVED — approved 2026-07-28.** The complete lifecycle is defined as four transitions — Creation (`None → active`), Replacement (`active → active`), Reseed (`broken → active`, via Decision #6's MSS invalidation), and CHoCH's paired Clearing effect — with a closed-set invariant restricting all modification to exactly these transitions, and a single-value invariant (at most one active `protected_high`/`protected_low` at any row). Fully specified for the External degree; the Internal degree (Decision #5) remains explicitly deferred pending Internal MSS/CHoCH trigger rules. See §10 (primary), mirrored in §11. Implementation pending.

---

## Appendix A — Liquidity Engine Interface Contract (informative, not normative)

Documents `liquidity.py`'s current contract with the structure engine, for completeness of pipeline item 7. Not a re-specification of its internal trading rules.

- **Input dependency:** `{time, high, low, close, structure, swing_high_price, swing_low_price}` only — no dependency on `structure_event`/`external_trend`/protected levels (§28).
- **EQH/EQL creation:** two consecutive same-type swings (`HH`/`LH` for highs; `HL`/`LL` for lows) within `tolerance_pips` of each other create a BSL/SSL pool at their midpoint.
- **`[APPROVED SPEC — Decision #3, see §7]`** For the canonical engine, the `HH`/`LH`/`HL`/`LL` values feeding EQH/EQL creation are per-trend-cycle classified (§7) once Decision #3 is implemented; for the legacy engine (Decision B, Phase 1/2), they remain globally classified, unchanged.
- **`[APPROVED SPEC — Decision #4, see §7]`** Exact-tie swings in `classify_market_structure` fold into `LH`/`LL` specifically because this engine's tolerance-based EQH/EQL clustering already represents equal-high/equal-low significance — the two mechanisms are deliberately complementary, not overlapping in scope.
- **Sweep:** wick exceeds the pool level by `minimum_sweep_pips`, close reverts back across it.
- **Break:** close exceeds the pool level by `break_confirmation_pips`, without qualifying as a sweep first.
- **Expiry:** optional `maximum_age_bars`.

## Appendix B — Order Block Engine Interface Contract (informative, not normative)

Documents `order_blocks.py`'s contract, for completeness of pipeline item 8.

- **`[CURRENT BEHAVIOUR]` Input dependency:** requires `structure_event`, `event_direction`, `broken_level` from the structure engine's output — hard-restricted to `source_event_types ⊆ {"BOS", "CHoCH"}`.
- **`[CURRENT BEHAVIOUR]` Creation:** final opposite-colour candle within `lookback_bars` before a qualifying event, subject to a minimum body-ratio and minimum ATR displacement filter; optional liquidity-sweep confirmation.
- **`[CURRENT BEHAVIOUR]` Lifecycle:** mitigated when price re-enters the block's range; invalidated when price closes beyond its distal level.
- **`[APPROVED SPEC — Decision #12, resolved 2026-07-28, full rule in §28]`** `source_event_types` extends to `⊆ {"BOS", "MSS", "CHoCH"}`, non-configurably.
  - New `OrderBlock` fields: `confirmation_status ∈ {provisional, confirmed}` (default `confirmed`); `confirming_event_id`, `confirming_event_type`, `confirmed_time`, `confirmed_index` (populated only on promotion); `invalidation_reason ∈ {price_penetration, mss_invalidated}`.
  - MSS-sourced blocks are created `confirmation_status = "provisional"`.
  - An `MSS_INVALIDATED` signal (§19) referencing a given block's `source_event_id` invalidates every still-`active` block sourced from that MSS via the existing `mark_invalidated()` method, tagged `invalidation_reason = "mss_invalidated"`.
  - A confirming CHoCH resolving to the same `candle_index` as the pending MSS's own block promotes that block in place (`provisional → confirmed`) instead of creating a duplicate, via a new, required **`OrderBlock.mark_confirmed()`** method (following the same lifecycle-method pattern as `mark_mitigated()`/`mark_invalidated()`/`mark_expired()`); a different `candle_index` creates an independent CHoCH-sourced block and leaves the MSS-sourced block confirmed on its own.
  - Promotion is one-way and occurs at most once per Order Block (`[INVARIANT]`, §28); no later structural event may re-promote or revert an already-confirmed block.
  - Promotion adds **`order_block_confirmed`** (boolean) and **`confirmed_order_block_id`** output columns, following the same event-row pattern as `order_block_mitigated`/`mitigated_order_block_id` and `order_block_invalidated`/`invalidated_order_block_id`.
  - BOS- and CHoCH-sourced blocks are always `confirmation_status = "confirmed"` and are never subject to the MSS-invalidation cascade.
  - This is a deliberate breaking change to default output — see §28 point 8 and §33.
- **`[APPROVED SPEC — Decision #9, see §24]`** The minimum-ATR-displacement filter referenced above (`minimum_displacement_atr`, default `1.0`) is an independently-configured parameter, deliberately distinct from `state_machine.py`'s structural-break multiplier `minimum_break_atr` (default `0.10`). The two serve different purposes — displacement-worthiness for Order Block anchoring versus structural-break significance — and Decision #9's configuration invariant (§24, point 5) applies identically to both: each may be changed independently, per run, without a specification change and without affecting the other.

## Appendix C — Extension Points for Future Confluence Engines (informative)

For pipeline item 9. No specific future engine (Fair Value Gaps, Breaker Blocks, Premium/Discount/Equilibrium) is specified here — none currently exist in code, and inventing their rules is explicitly out of scope. The relevant architectural point, based on the existing pipeline shape (`analysis_engine.py::analyze_market`, `event_registry.py::EventRegistry`, `models.py::MarketEvent`), is that any future confluence engine should:

- Consume the structure engine's DataFrame output and/or `MarketEvent` stream as an input layer, the same way `liquidity.py` and `order_blocks.py` already do.
- Emit its own `MarketEvent` instances into the same unified event stream rather than a parallel one.
- Not modify `state_machine.py`'s BOS/MSS/CHoCH semantics to accommodate itself.

This is stated as an architectural constraint carried over from the prior architecture review, not a new rule invented for this document.
