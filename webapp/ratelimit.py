"""Login brute-force throttle (M8 security hardening).

The platform is internet-facing, so ``/login`` needs more than a slow bcrypt
hash to resist automated password guessing. This is a small in-process throttle
keyed on the **account email**: once an account accrues ``max_fails`` attempts
inside ``window`` seconds it is refused for ``lockout`` seconds. A successful
login clears the account's counter.

Why key on the account, not the client IP: behind the reverse proxy the TCP peer
is the proxy (useless), and the ``X-Forwarded-For`` header is attacker-controlled
(so an IP key is both spoofable to evade the limit and spoofable to lock out a
legitimate office IP). The account key cannot be spoofed and directly caps
guesses against a real account.

Tradeoff, accepted and documented: an attacker who knows an account email can
keep it locked by failing on purpose (a nuisance, not a breach). The window is
short and self-clearing, and gate approvals still work through the signed
email-token links, so a lockout never blocks the actual workflow.

Two properties matter for correctness on an internet-facing box:

- **Atomic** -- ``hit()`` counts the attempt AND decides allow/deny under one
  lock, *before* the ~200ms bcrypt runs. A concurrent burst of same-account
  POSTs is therefore serialised through the counter, so it cannot overrun the
  cap the way a separate check-then-record pair could (a TOCTOU race).
- **Bounded** -- state is swept of elapsed windows and capped, so a flood of
  unique account keys cannot grow memory without bound.

In-process by design (the deployment is a single uvicorn process). Thread-safe
because sync routes run in a threadpool.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import Request


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "")))
    except (TypeError, ValueError):
        return default


class LoginThrottle:
    """Sliding-window failure counter with a lockout, keyed by an opaque string."""

    def __init__(self, max_fails: int, window: int, lockout: int, max_keys: int = 50000,
                 clock=time.monotonic):
        self._max_fails = max_fails
        self._window = window
        self._lockout = lockout
        self._max_keys = max_keys
        self._clock = clock
        self._fails: Dict[str, Deque[float]] = defaultdict(deque)
        self._locked_until: Dict[str, float] = {}
        self._mu = threading.Lock()

    def hit(self, key: str) -> int:
        """Atomically register an attempt for ``key``.

        Returns 0 if the attempt is allowed to proceed, or the number of seconds
        to wait if the account is (now) locked. Counting and the allow/deny
        decision happen under one lock, ahead of the caller's password check, so
        a concurrent burst cannot slip more than ``max_fails`` attempts through in
        one window. A successful login must still call ``record_success`` to clear
        the counter.
        """
        now = self._clock()
        with self._mu:
            until = self._locked_until.get(key)
            if until is not None:
                if now < until:
                    return int(until - now) + 1
                # Lock elapsed: clear it so the account is usable again.
                self._locked_until.pop(key, None)
                self._fails.pop(key, None)
            dq = self._fails[key]
            while dq and now - dq[0] > self._window:
                dq.popleft()
            dq.append(now)
            self._maybe_sweep(now)
            if len(dq) > self._max_fails:
                self._locked_until[key] = now + self._lockout
                dq.clear()
                return self._lockout
            return 0

    def record_success(self, key: str) -> None:
        """Clear a key's failure history (called on a successful login)."""
        with self._mu:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)

    def _maybe_sweep(self, now: float) -> None:
        """Bound memory: when the map grows large, drop keys whose window has
        fully elapsed and are not locked, and drop expired locks. Called under
        the lock; cheap and only kicks in past the cap."""
        if len(self._fails) < self._max_keys:
            return
        for k in list(self._locked_until):
            if self._locked_until[k] <= now:
                self._locked_until.pop(k, None)
                self._fails.pop(k, None)
        for k in list(self._fails):
            dq = self._fails.get(k)
            if (not dq or now - dq[-1] > self._window) and self._locked_until.get(k, 0) <= now:
                self._fails.pop(k, None)

    def reset(self) -> None:
        """Drop all state (used by tests to stay isolated)."""
        with self._mu:
            self._fails.clear()
            self._locked_until.clear()


# Defaults: 8 attempts within 5 minutes -> locked for 15 minutes. Tunable via env
# so a deployment can tighten or loosen without a code change.
throttle = LoginThrottle(
    max_fails=_int_env("LOGIN_THROTTLE_MAX_FAILS", 8),
    window=_int_env("LOGIN_THROTTLE_WINDOW", 300),
    lockout=_int_env("LOGIN_THROTTLE_LOCKOUT", 900),
)


def account_key(email: str) -> str:
    """The throttle key for an account (normalised like the user table)."""
    return "acct:" + (email or "").strip().lower()


def client_ip(request: Request) -> str:
    """Best-effort client IP for LOG lines only (never a throttle key).

    Prefers the first ``X-Forwarded-For`` hop set by the proxy; falls back to the
    TCP peer. Used purely for forensics in the failed-login log, so its
    spoofability does not affect the throttle.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return safe_log(xff.split(",")[0].strip() or "unknown")
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def safe_log(value: object, limit: int = 120) -> str:
    """Neutralise an attacker-controlled string for a single log line.

    Strips CR/LF (so a submitted email or header cannot forge extra log records)
    and caps the length. Used on the email/IP that reach the failed-login logs.
    """
    s = str(value if value is not None else "")
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return s[:limit]
