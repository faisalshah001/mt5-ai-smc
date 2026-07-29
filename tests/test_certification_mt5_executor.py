"""
Production Readiness Certification, Tasks 1 and 2: serialized,
timeout-protected MetaTrader5 execution (app/mt5/executor.py).

These tests exercise run_mt5() directly with plain Python callables --
no MetaTrader5 import or live terminal is required, matching this
project's existing pattern of testing the glue layer without a real
MT5 connection.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.mt5.executor import MT5TimeoutError, run_mt5


def test_run_mt5_returns_the_wrapped_calls_result():
    assert run_mt5(lambda: 42) == 42


def test_run_mt5_forwards_args_and_kwargs():
    def add(a, b, *, c):
        return a + b + c

    assert run_mt5(add, 1, 2, c=3) == 6


def test_run_mt5_propagates_the_wrapped_calls_exception():
    def boom():
        raise KeyError("boom")

    with pytest.raises(KeyError):
        run_mt5(boom)


def test_run_mt5_raises_mt5_timeout_error_on_slow_call():
    def slow():
        time.sleep(1.0)
        return "too late"

    with pytest.raises(MT5TimeoutError):
        run_mt5(slow, timeout=0.05)


def test_mt5_timeout_error_is_a_runtime_error():
    # Every existing `except RuntimeError` clause in this codebase
    # already maps MT5 failures to HTTP 500 -- a new, more specific
    # `except MT5TimeoutError` clause (mapping to 503) only works
    # correctly, and safely, if it subclasses RuntimeError.
    assert issubclass(MT5TimeoutError, RuntimeError)


def test_run_mt5_never_executes_two_calls_concurrently():
    lock = threading.Lock()
    active = 0
    max_active = 0

    def tracked_call():
        nonlocal active, max_active

        with lock:
            active += 1
            max_active = max(max_active, active)

        time.sleep(0.05)

        with lock:
            active -= 1

        return True

    caller_threads = [
        threading.Thread(target=run_mt5, args=(tracked_call,))
        for _ in range(5)
    ]

    for thread in caller_threads:
        thread.start()

    for thread in caller_threads:
        thread.join()

    assert max_active == 1
