from __future__ import annotations

import pytest

from tests.helpers.candles import load_eurusd_h4_fixture


@pytest.fixture
def eurusd_h4_candles():
    """
    Real, fixed H4 EURUSD candle history used for full-pipeline
    golden-file regression tests. Loaded fresh per test to guarantee
    no cross-test mutation leaks between tests.
    """

    return load_eurusd_h4_fixture()
