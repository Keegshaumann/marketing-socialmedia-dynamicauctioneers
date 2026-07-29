"""Login brute-force throttle (M8 security hardening).

The platform is internet-facing, so ``/login`` needs more than a slow bcrypt
hash to resist automated password guessing. This is a small in-process throttle
keyed on the **account email**: after ``max_fails`` failed attempts for one
account inside ``window`` seconds, further attempts for that account are refused
for ``lockout`` seconds. A successful login clears the account's counter.

Why key on the account, not the client IP: behind the Caddy reverse proxy the
TCP peer is the proxy (useless), and the ``X-Forwarded-For`` header is
attacker-controlled (so an IP key is both spoofable to evade the limit and
spoofable to lock out a legitimate office IP). The account key cannot be spoofed
and directly caps guesses against a real account.

Tradeoff, accepted and documented: an attacker who knows an account email can
keep it locked by failing on purpose (a nuisance, not a breach). The window is
short and self-clearing, and gate approvals still work through the signed
email-token links, so a lockout never blocks the actual workflow.

In-process and best-effort by design (the deployment is a single uvicorn
process). Thread-safe because sync routes run in a threadpool.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import Request


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "")))
    except (TypeError, ValueError):
        return default


class LoginThrottle:
    """Sliding-window failure counter with a lockout, keyed by an opaque string."""

    def __init__(self, max_fails: int, window: int, lockout: int, clock=time.monotonic):
        self._max_fails = max_fails
        self._window = window
        self._lockout = lockout
        self._clock = clock
        self._fails: Dict[str, Deque[float]] = defaultdict(deque)
        self._locked_until: Dict[str, float] = {}
        self._mu = threading.Lock()

    def retry_after(self, key: str) -> int:
        """Seconds the caller must wait before another attempt, or 0 if allowed."""
        now = self._clock()
        with self._mu:
            until = self._locked_until.get(key)
            if until is not None:
                if now < until:
                    return int(until - now) + 1
                # Lock elapsed: clear it so the account is usable again.
                self._locked_until.pop(key, None)
                self._fails.pop(key, None)
            return 0

    def record_failure(self, key: str) -> None:
        """Record a failed attempt; lock the key once it crosses the threshold."""
        now = self._clock()
        with self._mu:
            dq = self._fails[key]
            dq.append(now)
            while dq and now - dq[0] > self._window:
                dq.popleft()
            if len(dq) >= self._max_fails:
                self._locked_until[key] = now + self._lockout
                dq.clear()

    def record_success(self, key: str) -> None:
        """Clear a key's failure history (called on a successful login)."""
        with self._mu:
            self._fails.pop(key, None)
            self._locked_until.pop(key, None)

    def reset(self) -> None:
        """Drop all state (used by tests to stay isolated)."""
        with self._mu:
            self._fails.clear()
            self._locked_until.clear()


# Defaults: 8 failures within 5 minutes -> locked for 15 minutes. Tunable via env
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
        return xff.split(",")[0].strip() or "unknown"
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"
