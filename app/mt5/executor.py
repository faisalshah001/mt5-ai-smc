"""
Single-threaded MetaTrader5 call execution (Production Readiness
Certification, Tasks 1 and 2).

The MetaTrader5 package wraps one native IPC channel to a single
running terminal and is not documented as thread-safe. FastAPI runs
this project's sync `def` route handlers in a thread pool, so without
this module, concurrent requests could call mt5.* functions from
multiple threads at the same time.

run_mt5() is the single execution path every mt5.* call in this
codebase is routed through: it guarantees no two mt5.* calls ever run
concurrently (all work is submitted to one dedicated worker thread),
and it enforces a timeout so a frozen/unresponsive terminal cannot
block a request indefinitely.

Note: if a call times out, the underlying native call may still be
running on the dedicated worker thread -- Python cannot forcibly
interrupt a running native call. Subsequent calls queue behind it
until it returns; the caller of the timed-out call still receives a
prompt MT5TimeoutError rather than hanging.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_TIMEOUT_SECONDS = 10.0


def _resolve_default_timeout() -> float:
    """Read the default MT5 call timeout from the environment, once."""

    raw_value = os.environ.get("MT5_CALL_TIMEOUT_SECONDS")

    if raw_value is None or not raw_value.strip():
        return _DEFAULT_TIMEOUT_SECONDS

    try:
        value = float(raw_value)
    except ValueError:
        logger.warning(
            "Ignoring invalid MT5_CALL_TIMEOUT_SECONDS=%r; using "
            "default of %.1fs.",
            raw_value,
            _DEFAULT_TIMEOUT_SECONDS,
        )
        return _DEFAULT_TIMEOUT_SECONDS

    if value <= 0:
        logger.warning(
            "Ignoring non-positive MT5_CALL_TIMEOUT_SECONDS=%r; using "
            "default of %.1fs.",
            raw_value,
            _DEFAULT_TIMEOUT_SECONDS,
        )
        return _DEFAULT_TIMEOUT_SECONDS

    return value


DEFAULT_MT5_TIMEOUT_SECONDS: float = _resolve_default_timeout()

# A single dedicated worker thread -- every mt5.* call in this codebase
# is submitted here, one at a time, in submission order.
_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="mt5-worker",
)


class MT5TimeoutError(RuntimeError):
    """
    Raised when a call routed through run_mt5() exceeds its timeout.

    Deliberately a RuntimeError subclass: every existing `except
    RuntimeError` clause already maps MT5 failures to HTTP 500, so
    callers that add a more specific `except MT5TimeoutError` clause
    (mapping to HTTP 503) upgrade their handling without needing any
    other exception-handling change.
    """


def run_mt5(
    func: Callable[..., T],
    *args: object,
    timeout: float | None = None,
    **kwargs: object,
) -> T:
    """
    Run one MetaTrader5 call on the single dedicated MT5 worker thread.

    Parameters
    ----------
    func:
        The mt5.* callable to invoke (e.g. mt5.copy_rates_from_pos).
    *args, **kwargs:
        Arguments forwarded to func.
    timeout:
        Seconds to wait before raising MT5TimeoutError. Defaults to
        DEFAULT_MT5_TIMEOUT_SECONDS (itself overridable via the
        MT5_CALL_TIMEOUT_SECONDS environment variable).
    """

    effective_timeout = (
        timeout if timeout is not None else DEFAULT_MT5_TIMEOUT_SECONDS
    )

    future = _executor.submit(func, *args, **kwargs)

    try:
        return future.result(timeout=effective_timeout)
    except FutureTimeoutError as error:
        function_name = getattr(func, "__name__", repr(func))

        logger.error(
            "MT5 call %s timed out after %.1fs.",
            function_name,
            effective_timeout,
        )

        raise MT5TimeoutError(
            f"MetaTrader5 call {function_name!r} timed out after "
            f"{effective_timeout:.1f}s."
        ) from error
