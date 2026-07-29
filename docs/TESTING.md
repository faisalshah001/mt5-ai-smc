# Testing

Status: describes the test suite as it exists today — 25 files under `tests/`, run with `pytest` (`pytest==9.1.1`, `pytest.ini` sets `testpaths = tests`, `pythonpath = .`).

## 1. Layout

```
tests/
  conftest.py                 shared fixtures (eurusd_h4_candles)
  _generate_goldens.py        manual golden-regeneration script — NOT collected by pytest
  golden/                     committed JSON snapshots, one per golden-tested function
  fixtures/                   eurusd_h4_candles.csv, the one real-market fixture
  helpers/
    candles.py                 build_zigzag_candles(), load_eurusd_h4_fixture()
    dataframe_compare.py        cell() — read one DataFrame cell as None-safe Python
    golden.py                   save_golden(), load_golden(), assert_matches_golden()
    serialize.py                dataframe_to_records(), events_to_records(), objects_to_records()
  test_baseline_*.py           Phase 0: pins the codebase's pre-Phase-1 behaviour (golden files, no
                                spec decision touches these files' correctness — pure change detectors)
  test_phaseN_*.py             one file per implementation phase (1 through 8), each named for the
                                decision(s) it covers — see IMPLEMENTATION_ROADMAP.md for what each
                                phase was
  test_hardening_*.py          Post-Audit Hardening Phase: RSI fix, structured logging
```

Naming is historical, not aspirational — `test_baseline_*` and `test_phaseN_*` record *when and why* a test was written, matching this project's phase-gated implementation history (`IMPLEMENTATION_ROADMAP.md`). New tests unrelated to a specific historical phase should still follow this convention loosely (a short, descriptive `test_<topic>_*.py` name) rather than being forced into an existing phase file that isn't actually about that topic.

## 2. Determinism

Every pipeline function is asserted to be a pure function of its input (see [ARCHITECTURE.md §2.1](ARCHITECTURE.md#21-deterministic-processing)). This is enforced two ways, both standing regression gates, not one-off checks:

- **Run the full suite twice in the same process invocation sequence.** If any test's assertions differ between the two runs, something is reading mutable global/module-level state or wall-clock time it shouldn't.
- **Run the full suite in reverse file order** (`ls tests/test_*.py | sort -r | xargs pytest`). If any test only passes in one file order, a test is leaking state into a later test (e.g. a shared, mutated fixture) rather than being independent.

Both were run, and both passed, at the end of every implementation phase in this project's history — this is the expected standing practice for any future change too.

## 3. Fixtures

Two kinds, used for different purposes:

- **`build_zigzag_candles(waypoints, candles_per_leg=8, ...)`** (`tests/helpers/candles.py`) — a deterministic, hand-constructed candle-series builder that visits each given price in order, guaranteeing each waypoint is a strict local high/low over any window narrower than `candles_per_leg`. Used for every hand-worked scenario test (specific BOS/MSS/CHoCH sequences, specific protected-level transitions, specific classification edge cases) because the exact expected output can be reasoned about and independently verified.
- **`load_eurusd_h4_fixture()`** — loads the one committed real-market candle history (`tests/fixtures/eurusd_h4_candles.csv`), used exclusively for golden-file regression tests (§4). It is a fixed, immutable snapshot, never re-fetched from MT5, so it is exactly as deterministic as a synthetic fixture despite being real data.

**Practice established throughout this project's history:** every hand-built fixture's expected values were verified against actual runtime output before being written into an assertion, never hand-derived from OHLC/index arithmetic alone — that arithmetic has repeatedly proven error-prone in this codebase's own implementation history. Follow the same discipline for new hand-built scenarios: run the fixture through the real function first, read off the actual values, *then* write the assertion.

## 4. Goldens

A "golden" is a committed JSON snapshot (`tests/golden/*.json`) of one pipeline function's output over `eurusd_h4_candles`, captured via `tests/_generate_goldens.py`. `assert_matches_golden(name, data)` (`tests/helpers/golden.py`) compares freshly-computed output against the committed snapshot exactly; on a list mismatch it reports the first differing record instead of dumping the entire payload.

**16 goldens exist today**, one (or a few) per pipeline stage: `indicators_eurusd_h4`, `legacy_bos_choch_eurusd_h4`, `state_machine_eurusd_h4`, `liquidity_dataframe/registry/events_eurusd_h4`, `order_block_dataframe/registry/events_eurusd_h4`, `analysis_engine_structure/events/liquidity/order_blocks/snapshot/metadata_eurusd_h4`, `analyze_endpoint_response_eurusd_h4`.

**A golden mismatch means production behaviour changed.** Whether that's expected depends entirely on whether a deliberate, approved change explains it — never fix a failing golden test by regenerating without first proving the diff is required. See §6 for the safe procedure.

**Goldens can go stale in a subtle way:** a golden only detects a difference if the real fixture's data actually exercises the changed code path. `indicators_eurusd_h4` genuinely changed when the RSI zero-average-loss fix landed (Post-Audit Hardening) because the real fixture happens to contain 2 rows where `average_loss == 0`; `state_machine_eurusd_h4` never changed across the entire Decision #3 per-cycle-classification rewrite (Phase 7) because the real fixture never actually crosses a second trend cycle boundary in that window. **A passing golden test is not proof an entire code path is exercised — verify what the fixture actually contains before concluding "no behaviour change" from a golden staying green.**

## 5. Regression philosophy

Two complementary layers, deliberately not merged into one:

1. **Golden-file tests** — broad, low-maintenance change detectors over the one real fixture. Excellent at catching *any* unintended diff, terrible at explaining *why* something changed or proving a specific rule is correct (a golden matching doesn't tell you the rule it's checking was ever actually triggered — see §4's caveat above).
2. **Hand-worked scenario tests** (`build_zigzag_candles`-based) — narrow, deliberately engineered to exercise one exact rule or edge case, with assertions that state *which* spec decision/point they pin. These are what actually prove correctness; goldens only prove *stability*.

New behaviour needs both: a hand-worked test proving the new rule is correct in isolation, and (if the change touches a golden-tested function) confirmation that the relevant golden either stays the same (proving the real fixture doesn't exercise the new path) or is deliberately, explainedly regenerated (proving it does).

## 6. How to regenerate goldens safely

**Never** regenerate a golden merely to make a failing test pass. The procedure, as practiced throughout this project:

1. Run the full suite *before* touching any golden. Record which tests fail.
2. For each failure, classify it: (a) an expected, approved behaviour change — cite the exact decision/fix; (b) an unexpected regression — stop, fix the code, do not regenerate; (c) an obsolete test assumption; (d) a fixture defect.
3. Only for category (a): back up the current goldens, run `python tests/_generate_goldens.py`, then **diff every golden file, not just the ones you expect to change** — this project's own history caught a golden changing for a reason not originally anticipated (`analyze_endpoint_response_eurusd_h4` changed alongside `indicators_eurusd_h4` during the RSI fix, because it happens to serialize `rsi14` as a passthrough column even though no SMC decision reads it — a fact only discovered by diffing every file, not by assuming "the SMC pipeline doesn't consume RSI" was sufficient reasoning on its own).
4. For every regenerated golden, write down the field-level reason it changed. Confirm nothing else changed that you can't explain.
5. Re-run the full suite twice, and in reverse order, before considering the change complete.

## 7. How to add a test

- Pick the right fixture: `build_zigzag_candles` for a specific rule/scenario you can reason about exactly; `eurusd_h4_candles` only if you're extending golden coverage.
- If asserting on a hand-built fixture's output, run it interactively first (`python -c "..."` or a scratch script) and copy the *actual* verified values into the assertion — do not hand-derive them.
- Name the test after what it proves, not how it's implemented, and comment which `SMC_SPECIFICATION.md` section/decision it pins if applicable (the existing test files are full of this pattern — follow it).
- If the test crosses a module boundary this project treats as load-bearing (e.g. a column another module depends on), consider whether a downstream regression test is also warranted — see the "regression risk despite zero code changes" pattern noted in [ARCHITECTURE.md §2.2](ARCHITECTURE.md#22-single-causally-forward-pass)'s cited history (Decision #3 changing `structure` values silently changed `liquidity.py`'s behaviour with zero code changes to that file).

## 8. Running the suite

```
pytest                                  # full suite (uses testpaths from pytest.ini)
pytest tests/test_baseline_state_machine.py -v
pytest -k "mss_invalidation" -v         # by test-name substring
python tests/_generate_goldens.py       # regenerate ALL goldens — see §6 before running this
```

## Cross-references

- What each stage's output columns mean (needed to write meaningful assertions): [DATA_FLOW.md](DATA_FLOW.md)
- Coding/process standards for the surrounding change: [CONTRIBUTING.md](CONTRIBUTING.md)
- Diagnosing a specific test/golden failure: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
