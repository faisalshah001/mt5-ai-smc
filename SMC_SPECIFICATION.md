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

**Out of scope:** Fair Value Gaps, Breaker Blocks, Premium/Discount/Equilibrium zones, and Internal Structure detection are named in `CLAUDE.md`'s "Trading Logic" section as concepts to respect, but **no code implementing them exists in this repository today**. This document does not invent rules for them. See §9 for the internal/external structure gap specifically, since it was requested explicitly.

This document does **not** authorize implementation. It does not modify, rename, or delete any file. It does not add API endpoints.

## 2. Terminology

| Term | Definition |
|---|---|
| Swing high / swing low | A pivot candle confirmed by a symmetric left/right lookback window (§4, §5). |
| HH / LH / HL / LL | Higher High, Lower High, Higher Low, Lower Low — classification of a confirmed swing relative to the previous confirmed swing of the same type (§7). |
| External trend | The **confirmed** directional bias of the market (`neutral`/`bullish`/`bearish`). Changes only on a confirmed CHoCH. |
| Structure state | The **working** state of the engine (`neutral`/`bullish`/`bearish`/`mss_bullish`/`mss_bearish`). Changes on both MSS and CHoCH. |
| Protected high / protected low | The structural swing level whose break, in the given trend context, initiates an MSS (§10, §11). |
| Candidate level | The most recently confirmed opposite-type swing (LH during a bullish trend's building bearish case, HL during a bearish trend's building bullish case), held in reserve to become the next protected level (§12). |
| BOS (Break of Structure) | A close-confirmed break of the active continuation level, in the direction of the current confirmed trend. Does not change trend or state beyond re-arming the next BOS level. |
| MSS (Market Structure Shift) | A close-confirmed break of the protected level, **against** the current trend. Tentative — does not change `external_trend`. |
| CHoCH (Change of Character) | The **confirmed** reversal: an MSS followed by a specific opposite-direction swing sequence. Changes `external_trend`. |
| Displacement | Sharp directional price movement following a structural event, used by the Order Block engine (Appendix B) — not evaluated by the structure engine itself. |
| Liquidity sweep | A wick-based breach of a liquidity pool followed by a close-based reversion (§28, Appendix A) — evaluated independently of BOS/MSS/CHoCH.

## 3. Candle and price assumptions

- **`[CURRENT BEHAVIOUR]`** Candles originate from `app/mt5/market.py::get_candles`, which converts MT5 Unix timestamps to UTC (`market.py:71-75`) and requires `M1/M5/M15/M30/H1/H4/D1` timeframes.
- **`[CURRENT BEHAVIOUR]`** `app/analysis/analysis_engine.py::_prepare_candles` (lines 81–205) is the strictest validation gate that exists in the codebase: UTC coercion, numeric coercion of OHLC, stable chronological sort, duplicate-timestamp rejection, and OHLC relationship validation (`high >= open/close/low`, `low <= open/close/high`). This gate runs **only** inside `analyze_market()`.
- **`[CURRENT BEHAVIOUR]`** The live API path (`main.py`'s `/analysis/market-structure/{symbol}/{timeframe}`) does **not** call `_prepare_candles`. It calls `get_candles` → `calculate_indicators` → `detect_swing_points` directly (`main.py:280-294`). The strict OHLC/UTC/duplicate validation gate is bypassed on the currently-live endpoint.
- **`[PROPOSED SPEC]`** The canonical structure engine (`detect_structure_state`) MUST only ever receive candles that have already passed `_prepare_candles`. Every call site — live API or otherwise — must route through this gate.
- **`[DECISION REQUIRED — #1]`** Should `_prepare_candles` become a hard precondition enforced *inside* `detect_swing_points`/`detect_structure_state` (defensive, redundant validation), or is it acceptable to rely on callers always routing through `analyze_market()` first (single point of enforcement, per CLAUDE.md's "avoid duplicate logic")?
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
- **`[DECISION REQUIRED — #3]`** Is `classify_market_structure`'s global, never-reset `previous_high`/`previous_low` tracking the intended behavior, or should it be scoped to the current trend cycle (reset at each confirmed CHoCH) so that "HH" always means "higher than the highest point of the *current* trend leg," matching how `state_machine.py`'s own protected/candidate levels are scoped per cycle? This is a structural coupling between the two files that has not been reconciled.
- **`[DECISION REQUIRED — #4]`** Should an exact-tie swing get its own classification (e.g., treated as an equal-high/equal-low structural event, distinct from — but related to — the separately-implemented EQH/EQL liquidity tolerance concept in `liquidity.py`), or is silently folding ties into `LH`/`LL` acceptable?

## 8. Bullish, bearish and neutral structure states

- **`[CURRENT BEHAVIOUR]`** (`state_machine.py`, lines 4-12, 101-102) Two parallel state variables exist:
  - `current_trend ∈ {neutral, bullish, bearish}` — the **confirmed** trend. Changes only on CHoCH.
  - `current_state ∈ {neutral, bullish, bearish, mss_bullish, mss_bearish}` — the **working** state. Changes on both MSS and CHoCH.
- **`[CURRENT BEHAVIOUR]`** `neutral` persists until the engine sees the *second* classified swing overall (since the first swing of each type is unlabeled per §7), and only if that second swing is `HH`, `HL`, `LL`, or `LH` (any of the four can trigger the initial transition — see §27 for the asymmetric consequence of which one does).
- **`[PROPOSED SPEC]`** `current_trend` and `current_state` must always satisfy: `current_state ∈ {current_trend, "mss_" + current_trend}` for `current_trend ∈ {bullish, bearish}`, and `current_state == "neutral"` iff `current_trend == "neutral"`. (This already holds in the current implementation; stated here as an explicit invariant to protect during any future change.)

## 9. Internal structure versus external structure

- **`[CURRENT BEHAVIOUR]`** **No internal/external structure distinction exists anywhere in this codebase.** `detect_swing_points` runs a single pass with one `(left_bars, right_bars)` pair; `state_machine.py` operates on that single swing degree only. `CLAUDE.md` lists "Internal Structure" and "External Structure" as concepts to respect, but no module computes either — what the engine currently calls "structure" corresponds to what ICT terminology usually calls **external** (major swing) structure only, by virtue of whatever `left_bars`/`right_bars` the caller chooses.
- **`[DECISION REQUIRED — #5]`** Should internal structure be added as a second, smaller-degree swing pass (e.g., a second `detect_swing_points`/`detect_structure_state` invocation with smaller `left_bars`/`right_bars`) feeding a parallel, lower-priority state machine? Or should it be a single pass with a strength/degree classification per swing? This specification does not invent an answer — it flags the gap as real and currently unaddressed.

## 10. Protected high definition

- **`[CURRENT BEHAVIOUR]`** (`state_machine.py`) `protected_high` is the swing-high level whose break, while `current_state == "bearish"`, triggers a bullish MSS (§15).
- Set initially: on the first `LH` while transitioning `neutral → bearish` (line 333) or continuing `bearish` (line 336, ratcheted down on every subsequent `LH`).
- Cleared to `None`: on confirmed bullish CHoCH (line 243).
- Promoted from `candidate_high`: on confirmed bearish CHoCH (line 305) — the `LH` that built up during the `mss_bearish` phase becomes the new `protected_high` for the freshly-confirmed bearish trend.
- **`[CURRENT BEHAVIOUR — flagged in §26]`** Once broken to start an MSS, `protected_high` is **not** cleared or marked broken; it persists unchanged (and keeps being written to the DataFrame via `store_current_state`, lines 160-193) until CHoCH eventually replaces it.
- **`[APPROVED SPEC — see §19, §26, §27]`** In addition to the CHoCH-driven clearing/promotion above, `protected_high` MUST also be re-established when a pending bullish MSS is invalidated (§19, Decision #6) or when `neutral → bearish` initializes via a lone `LL` (§27, Decision #11): seeded from `latest_swing_high`, `protected_level_status = active`, `protected_level_source = latest_swing`, until superseded by the next confirmed `LH` (§26, Decision #10).

## 11. Protected low definition

- **`[CURRENT BEHAVIOUR]`** Mirror of §10: `protected_low` triggers a bearish MSS when broken while `current_state == "bullish"`. Set on first `HL` while `neutral → bullish` (line 270) or continuing `bullish` (line 273, ratcheted up each `HL`). Cleared on bearish CHoCH (line 306). Promoted from `candidate_low` on bullish CHoCH (line 242). Same staleness behavior as §10 applies (§26).
- **`[APPROVED SPEC — see §19, §26, §27]`** In addition to the CHoCH-driven clearing/promotion above, `protected_low` MUST also be re-established when a pending bearish MSS is invalidated (§19, Decision #6) or when `neutral → bullish` initializes via a lone `HH` (§27, Decision #11): seeded from `latest_swing_low`, `protected_level_status = active`, `protected_level_source = latest_swing`, until superseded by the next confirmed `HL` (§26, Decision #10).

## 12. Candidate protected levels

- **`[CURRENT BEHAVIOUR]`** `candidate_high`/`candidate_low` hold the most recently confirmed `LH`/`HL` price respectively, updated **unconditionally on every occurrence of that structure type regardless of `current_state`** (lines 262-264, 325-327) — including during `neutral`, `bullish`/`bearish` continuation, and `mss_*` pending phases.
- **`[CURRENT BEHAVIOUR]`** Promoted to `protected_low`/`protected_high` specifically at CHoCH confirmation time (lines 242, 305).
- **`[CURRENT BEHAVIOUR — redundancy, not a defect]`** Also reassigned as a side effect of a same-direction BOS (`protected_low = candidate_low` on bullish BOS, line 421; mirror at line 472). In every traced code path this is a no-op, since `candidate_low`/`protected_low` are already kept synchronized by the `HL` continuation branch (line 273). Documented here as current behavior; not proposed for removal without your approval per CLAUDE.md rule 2/3.

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

## 14. Bearish BOS rules

Mirror of §13 using `active_bearish_bos_level`/`broken_bearish_bos_level`, gated on `current_state == "bearish"` (`state_machine.py:451-472`). Trigger: `active_bearish_bos_level - close >= ATR14 * minimum_break_atr`.

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

## 16. Bearish MSS rules

Mirror of §15 using `protected_low`, gated on `current_state == "bullish"` (lines 379-397). Requires `protected_low is not None`. Invalidation condition: mirror of §15 — **`[CURRENT BEHAVIOUR]` None exists in code.** Spec resolved — see §19, Decision #6; implementation pending.

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
| Invalidation condition | **`[CURRENT BEHAVIOUR]` None exists.** Once confirmed, CHoCH is permanent in the current model — `external_trend` does not revert without a subsequent opposite CHoCH. (Unlike the MSS gap in §19, this is arguably correct by standard ICT convention — see §20.) |

**`[CURRENT BEHAVIOUR — ordering caveat]`** If the confirming `HH` arrives *before* any `HL` during the `mss_bullish` phase, `bullish_mss_has_hl` is still `False`, so that `HH` confirms nothing (lines 232-233). The engine keeps waiting, however long it takes, for an `HL` to eventually appear followed by a *subsequent* `HH`.

## 18. Bearish CHoCH confirmation rules

Mirror of §17: requires `mss_bearish` state and `bearish_mss_has_lh == True`, confirms on the next `LL` (lines 295-317).

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
    - `structure_event = "MSS_INVALIDATED"` on the invalidation candle (a new value in the existing single-slot `structure_event` column, per §22 — this candle cannot simultaneously carry any other structural event, since Sections 3/4 do not run while `current_state` is `mss_*`).
    - `event_direction` = the **reasserted** (original, pre-MSS) trend direction — the same convention `CHoCH` already uses for its resulting direction.
    - A new column, **`mss_invalidated_origin_index`** = the candle position of the original MSS-creation row. This is the join key: it lets a consumer reconstruct the exact `source_event_id` the Order Block engine already synthesizes for that MSS (`f"STR_MSS_{origin_index:05d}"`, §28) and look up every block sourced from it directly.
    - `broken_level` (existing column) continues to carry the invalidated protected-level price on this row, for transparency — no new field needed for that.
    - This requires `state_machine.py` to additionally track the MSS-creation candle's **position** (not only its price, which `mss_origin_level` already covers) — a new `mss_origin_index` variable, parallel to `mss_origin_level`, cleared at the same two points (CHoCH confirmation, MSS invalidation).
    - On the invalidation candle specifically, `mss_invalidated_origin_index` is populated directly from the internal `mss_origin_index` variable's current value, **before** that variable is cleared as part of this same invalidation transition.
    - The corresponding canonical `MarketEvent` (built by `analysis_engine.py::_build_structure_events`) carries `metadata["mss_origin_index"]` (the join key above) and `metadata["mss_origin_event_id"]` (the original MSS occurrence's own `MarketEvent.event_id`) — the latter closes a pre-existing gap where the Order Block engine's synthesized `source_event_id` and the canonical event stream's `event_id` are independent, non-cross-referenced ID namespaces for the same underlying event.
    - `"MSS_INVALIDATED"` is added to the `EventType` literal (`models.py`) as an additive value — no existing value is removed or renamed (a MINOR-class change under §33).

  This makes MSS invalidation symmetric with MSS creation and CHoCH confirmation: all three are swing/close-driven, formal state transitions with fully-specified bookkeeping — no timers, no arbitrary price-distance constants. Options B and C are rejected: both introduce unjustified magic constants and/or decouple invalidation from the swing-driven evidence the rest of the engine relies on.

  **Note — not addressed by this decision:** an `LH` confirming during `mss_bullish`, or an `HL` confirming during `mss_bearish`, remain **undefined** (no invalidation behaviour specified). Only the same-original-direction swing types listed above (`HH`/`LL`) are covered. See the updated §21 table.

  **Dependency:** this decision depends on Decisions #10 (§26) and #11 (§27) for the protected-level reseed rule referenced above — both are resolved alongside this one.

  **`[IMPLEMENTATION STATUS]`** This approves the rule only. No Python code has been changed.

## 20. CHoCH invalidation rules

**`[CURRENT BEHAVIOUR]`** None exists — a confirmed CHoCH is final; `external_trend` only changes again via the next opposite-direction CHoCH.

**`[PROPOSED SPEC — reasoning, not a rule change]`** Unlike the MSS gap in §19, this is plausibly correct by standard ICT convention: CHoCH *is* defined as the confirmed reversal, so nothing should "un-confirm" it after the fact — the market simply continues from there under the new trend.

**`[DECISION REQUIRED — #7]`** Should the engine nonetheless support a "failed CHoCH" concept (e.g., an immediate, sharp continuation of the *old* trend right after a fresh CHoCH, which some SMC frameworks treat as invalidating the just-confirmed reversal)? This document takes no position — flagged only because some ICT-adjacent communities do use this concept and it should be a deliberate inclusion/exclusion decision.

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
- **`[DECISION REQUIRED — #8]`** Should a single candle ever be able to record more than one structural event (e.g., a CHoCH immediately followed by a fresh continuation BOS on the same candle, in a fast, single-candle move)? Current design: no, by construction.

## 23. Wick break versus candle-close break

- **`[CURRENT BEHAVIOUR]`** All BOS, MSS, and CHoCH-triggering comparisons in `state_machine.py` use **`close`**, never `high`/`low` wicks (lines 347, 380, 391, 406, 431, 457).
- **`[CURRENT BEHAVIOUR]`** This is consistent with `market_structure.py`'s simpler BOS model (also close-based).
- **`[CURRENT BEHAVIOUR — deliberate contrast]`** The Liquidity engine (`liquidity.py`) uses a **different, intentional** model: sweep detection is wick-triggered (`current_high > pool.level`) and close-confirmed (`current_close < pool.level`) — appropriate since a liquidity sweep is inherently about a wick piercing a level and price reverting. This divergence between structural breaks (close-only) and liquidity sweeps (wick+close) is existing, sensible design — not an inconsistency — and is documented here so it is not mistaken for a defect in a future review.

## 24. ATR break threshold behaviour

- **`[CURRENT BEHAVIOUR]`** `required_distance = ATR14 * minimum_break_atr`, default `minimum_break_atr = 0.10` (i.e., 10% of ATR14). Identical formula and default used by both `market_structure.py` and `state_machine.py`.
- **`[CURRENT BEHAVIOUR]`** Negative ATR raises `ValueError` (line 365-368). Missing/NaN `close` or ATR for a row skips event detection for that row only, but the state snapshot is still recorded (lines 350-363).
- **`[DECISION REQUIRED — #9]`** Is a flat 10%-of-ATR threshold appropriate across all instruments/timeframes/sessions, or does it need empirical, per-symbol/per-timeframe justification? No evidence for the current default's derivation exists in the codebase — it is a bare constant.

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

  **5. `[INVARIANT]` Promotion is one-way and at most once.** `confirmation_status` transitions `provisional → confirmed` exactly one time per Order Block, for one Order Block's entire lifetime. Once `confirmed`, an Order Block can never revert to `provisional`, and cannot be promoted a second time. Subsequent BOS, CHoCH, or any other structural event — including one that happens to resolve to the same `candle_index` as an already-confirmed block — MUST NOT re-promote it or mutate its `confirming_event_id`/`confirmed_time`. The promotion rule in point 4 applies exclusively to the relationship between one specific pending MSS and its own confirming CHoCH; it is not a general "merge any two events sharing a candle" rule. A later BOS resolving to the same anchor candle as an already-confirmed block creates its own independent, separately-tracked Order Block (a distinct continuation footprint), never a mutation of the earlier one.

  **6. Mutual exclusivity.** Invalidation (point 3) and promotion (point 4) can never both apply to the same MSS occurrence: an MSS resolves exactly one way — invalidated (§19, Decision #6) or confirmed into CHoCH (§17/§18) — never both, by construction of the state machine (Sections 3/4 require `current_state == "bullish"/"bearish"`, never `mss_*`, so only one MSS per direction can ever be pending at a time).

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

## 32. Edge cases

Consolidated master list. Each item is tagged `[CONFIRMED CURRENT BEHAVIOUR]` or points to its `[DECISION REQUIRED]` number.

1. Tie prices in swing detection are excluded from being swing points at all (strict `>`/`<`) — §4/§5. `[CONFIRMED]`
2. Tie prices in HH/LH classification default to `LH`/`LL` — §7. `[CONFIRMED]`, see Decision #4.
3. The very first swing high/low ever detected has no HH/HL/LH/LL label — §7. `[CONFIRMED]`
4. Trend initializing via a lone `HH`/`LL` leaves the opposite protected level unset, disabling MSS detection until the first opposite-type swing — §27. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #11 — pending implementation.**
5. MSS has no invalidation/failure path — §19. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #6 — pending implementation.**
6. Protected level goes stale (remains reported) during the MSS-pending phase — §26. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #10 — pending implementation.**
7. CHoCH confirmation requires strict swing ordering (HL must precede HH); an HH arriving first is silently ignored for confirmation purposes — §17. `[CONFIRMED]`, see Decision #6/#7 (related).
8. At most one structural event per candle — §22. `[CONFIRMED]`, see Decision #8.
9. `classify_market_structure`'s global, never-reset high/low tracking vs. `state_machine.py`'s per-cycle tracking — §7. `[CONFIRMED]`, see Decision #3.
10. Liquidity engine ignores trend/BOS/MSS/CHoCH context entirely — §28. `[CONFIRMED]` — out of scope for Decision #12, unchanged.
11. Order Block engine hard-excludes MSS as a source event type — §28. `[CONFIRMED]` as current code; **spec RESOLVED, see Decision #12 — pending implementation.**
12. `MarketEvent.strength` is defined but never populated by any code path — §29. `[CONFIRMED]`, see Decision #13.
13. Output is unstable under incremental/live querying due to swing confirmation lag — §30. `[CONFIRMED]`, see Decision #2/#14.
14. The live `/analysis/market-structure` endpoint bypasses `_prepare_candles` validation entirely — §3. `[CONFIRMED]`, see Decision #1. (Note: this endpoint does not currently call `state_machine.py` at all — see §3, where the live endpoint's pipeline is documented — but the gap will matter the moment it is wired up.)

## 33. Versioning rules

- **`[CURRENT BEHAVIOUR]`** `analysis_engine.py::analyze_market` stamps `metadata["pipeline_version"] = "2.0.0"` (line 817) as a bare string with no enforcement, no changelog, and no semver policy tied to it.
- **`[PROPOSED SPEC]`**
  - Any change to swing-detection parameters/logic, classification logic, or state-machine transition rules that can alter output for previously-valid input **must** increment `pipeline_version`.
  - Recommended scheme: **MAJOR** = event semantics or response shape change; **MINOR** = new event types/fields added additively; **PATCH** = defect fix that brings behavior into conformance with this specification without changing the specification itself.
  - This document (`SMC_SPECIFICATION.md`) should itself carry a version and changelog (see header). Future rule changes are proposed as diffs to this document **first**, approved, then implemented, then version-bumped together with `pipeline_version`.
- This entire section is a proposal — no versioning policy beyond the bare string currently exists.
- **`[APPROVED SPEC — recorded per Decision #12, resolved 2026-07-28]`** Concrete application of the MAJOR-bump rule above: implementing Decision #12 (§28) — extending `SUPPORTED_STRUCTURE_EVENTS` to include `MSS` — changes default Order Block output for existing callers of `detect_order_blocks` and therefore **requires a MAJOR `pipeline_version` increment** on implementation. This is recorded here as a specification requirement, not left as an implementation-time judgment call.

## 34. Testing acceptance criteria

- **`[CURRENT BEHAVIOUR]`** No test suite exists for this project (confirmed in prior review — only third-party library tests exist under `venv_old`).
- **`[PROPOSED SPEC]`** Before any implementation change to `state_machine.py`, the following must exist:
  - Deterministic fixture-based tests for swing detection (tie handling, minimum-window enforcement, asymmetric `left_bars`/`right_bars`).
  - Classification tests (first-swing no-label behavior, HH/LH/HL/LL sequencing, tie-break behavior).
  - Per-event-type tests for BOS/MSS/CHoCH covering: trigger condition, confirmation condition, duplicate-event guard, and (once resolved) invalidation condition, for both directions.
  - Same-candle priority tests (§22).
  - ATR-threshold boundary tests (exactly-at-threshold vs. just-under).
  - Missing/NaN close or ATR handling tests.
  - A test encoding the stale-protected-level scenario (§26) and the initialization-gap scenario (§27), so their current behavior is pinned and any future fix is a deliberate, visible diff.
  - Golden-file regression tests comparing full-pipeline output against this specification's rules.
- **`[PROPOSED SPEC]`** Any future change to the rules in this document must add or update a test encoding the new rule before the corresponding code change is merged.

## 35. Open design decisions requiring approval

Master list of every `[DECISION REQUIRED]` item raised in this document. Items marked **RESOLVED** have an approved specification (see the referenced section); this approves the rule only — implementation still requires a separate, later approval per the project workflow (Phase 5/6). All other items remain open exactly as originally raised.

1. **(§3)** Should `_prepare_candles`-equivalent validation become a hard precondition inside the structure engine itself, or remain enforced only via `analyze_market()` as the single entry point?
2. **(§6)** ~~Does the canonical engine need a "confirmed as of" boundary for live-trading consumers...~~ **RESOLVED — approved 2026-07-28.** A confirmed-as-of boundary is required; see §6 and §30 (linked to Decision #14).
3. **(§7)** Should `classify_market_structure`'s HH/HL/LH/LL comparison reset per trend cycle (coupled to `state_machine.py`'s cycle boundaries) instead of tracking globally across the whole series?
4. **(§7)** Should exact-tie swing prices receive their own classification, or continue folding into `LH`/`LL`?
5. **(§9)** How (if at all) should Internal Structure be added alongside External Structure — second swing-detection pass, single-pass degree classification, or deferred entirely?
6. **(§19)** ~~What should invalidate a pending MSS...~~ **RESOLVED — approved 2026-07-28.** Same-original-direction confirming swing (`HH` invalidates `mss_bearish`, `LL` invalidates `mss_bullish`) as a formal state transition; see §19. Depends on Decisions #10/#11. Implementation pending.
7. **(§20)** Should a "failed CHoCH" concept be supported, or is a confirmed CHoCH permanent by design (current behavior, and arguably correct ICT convention)?
8. **(§22)** Should more than one structural event ever be recordable on a single candle?
9. **(§24)** Does the flat 10%-of-ATR break threshold need empirical, per-instrument/timeframe justification, or is the current constant acceptable as a configurable default?
10. **(§26)** ~~Should a stale/broken protected level be explicitly flagged...~~ **RESOLVED — approved 2026-07-28.** Two independent fields: `protected_level_status ∈ {active, broken}` and `protected_level_source ∈ {hl, lh, latest_swing}`; see §26. Implementation pending.
11. **(§27)** ~~How should the HH/LL-only trend-initialization gap...~~ **RESOLVED — approved 2026-07-28.** Reseed from `latest_swing_low`/`latest_swing_high` (no new tracking state); see §27. Same rule also serves Decision #6's post-invalidation reseed. Implementation pending.
12. **(§28)** ~~Should Order Block creation be extended to optionally source from `MSS` events...~~ **RESOLVED — approved 2026-07-28.** MSS is an approved (non-configurable) source event under a single deterministic lifecycle: provisional creation, an `MSS_INVALIDATED`-driven invalidation cascade (§19), and one-way promotion into a confirmed CHoCH-backed block (never duplicated) when the same MSS confirms; see §28 and Appendix B. Deliberate breaking change — requires a MAJOR version bump (§33). Implementation pending.
13. **(§29)** ~~Should `MarketEvent.strength` be defined and populated, or removed as dead weight?~~ **RESOLVED — approved 2026-07-28.** Field is kept and defined as `break_distance / required_break_distance`; see §29.
14. **(§30)** ~~Does the project need a live-safe output mode...~~ **RESOLVED — approved 2026-07-28, linked to Decision #2.** A live-safe output mode is required as a distinct mode alongside historical/retrospective analysis; see §30. Implementation deferred to a later phase.

---

## Appendix A — Liquidity Engine Interface Contract (informative, not normative)

Documents `liquidity.py`'s current contract with the structure engine, for completeness of pipeline item 7. Not a re-specification of its internal trading rules.

- **Input dependency:** `{time, high, low, close, structure, swing_high_price, swing_low_price}` only — no dependency on `structure_event`/`external_trend`/protected levels (§28).
- **EQH/EQL creation:** two consecutive same-type swings (`HH`/`LH` for highs; `HL`/`LL` for lows) within `tolerance_pips` of each other create a BSL/SSL pool at their midpoint.
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

## Appendix C — Extension Points for Future Confluence Engines (informative)

For pipeline item 9. No specific future engine (Fair Value Gaps, Breaker Blocks, Premium/Discount/Equilibrium) is specified here — none currently exist in code, and inventing their rules is explicitly out of scope. The relevant architectural point, based on the existing pipeline shape (`analysis_engine.py::analyze_market`, `event_registry.py::EventRegistry`, `models.py::MarketEvent`), is that any future confluence engine should:

- Consume the structure engine's DataFrame output and/or `MarketEvent` stream as an input layer, the same way `liquidity.py` and `order_blocks.py` already do.
- Emit its own `MarketEvent` instances into the same unified event stream rather than a parallel one.
- Not modify `state_machine.py`'s BOS/MSS/CHoCH semantics to accommodate itself.

This is stated as an architectural constraint carried over from the prior architecture review, not a new rule invented for this document.
