"""
Token-bucket rate limiter implementation.

A TokenBucket allows bursts up to `capacity` tokens and refills at a steady
`refill_rate` tokens per second.  Callers check availability by calling
`allow()`; the method returns True when the requested number of tokens is
available and False (without blocking) when the bucket does not have enough.

Runnable usage example::

    import time
    from rate_limiter import TokenBucket

    # Allow 5 requests per second, burst up to 10
    bucket = TokenBucket(refill_rate=5, capacity=10)

    for i in range(12):
        if bucket.allow():
            print(f"Request {i}: allowed")
        else:
            print(f"Request {i}: rate limited")
        time.sleep(0.1)
"""

import threading
import time

DEFAULT_CAPACITY = 64  # default maximum bucket capacity (tokens)


class TokenBucket:
    """Thread-safe token-bucket rate limiter.

    Parameters
    ----------
    refill_rate : float
        Token refill rate in tokens per second.  Must be positive.
    capacity : float
        Maximum number of tokens the bucket can hold (burst ceiling).
        Must be positive.  Defaults to `refill_rate` when not supplied,
        giving a bucket that allows no burst beyond one second's worth
        of tokens.

    Usage
    -----
    bucket = TokenBucket(refill_rate=10, capacity=20)
    if bucket.allow():
        # request is within rate limit
        ...
    else:
        # rate limit exceeded
        ...
    """

    def __init__(self, refill_rate: float, capacity: float = None) -> None:
        if refill_rate <= 0:
            raise ValueError(f"refill_rate must be positive, got {refill_rate!r}")
        if capacity is None:
            capacity = refill_rate
        if capacity <= 0:
            raise ValueError(f"capacity must be positive, got {capacity!r}")

        self._refill_rate: float = float(refill_rate)
        self._capacity: float = float(capacity)
        self._tokens: float = float(capacity)   # start full
        self._last_refill: float = time.monotonic()
        self._lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def refill_rate(self) -> float:
        """Token refill rate (tokens / second)."""
        return self._refill_rate

    @property
    def capacity(self) -> float:
        """Maximum bucket capacity (tokens)."""
        return self._capacity

    @property
    def tokens(self) -> float:
        """Current token count (snapshot; may be stale by the time you read it)."""
        with self._lock:
            self._refill()
            return float(self._tokens)

    def allow(self, tokens: float = 1) -> bool:
        """Check whether *tokens* are available and, if so, consume them.

        Parameters
        ----------
        tokens : float
            Number of tokens to consume.  Defaults to 1.

        Returns
        -------
        bool
            True  — tokens were available and have been deducted.
            False — not enough tokens; the bucket is unchanged.

        Raises
        ------
        ValueError
            If *tokens* is not positive or exceeds the bucket capacity.
        """
        if tokens <= 0:
            raise ValueError(f"tokens must be positive, got {tokens!r}")
        if tokens > self._capacity:
            raise ValueError(
                f"tokens ({tokens}) exceeds bucket capacity ({self._capacity})"
            )

        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def remaining(self) -> float:
        """Return the current number of available tokens.

        The value is a snapshot taken under the lock after applying any
        elapsed refill; it may be stale by the time the caller acts on it.

        Returns
        -------
        float
            Number of tokens currently in the bucket (0.0 – capacity).
        """
        with self._lock:
            self._refill()
            return float(self._tokens)

    def reset(self) -> None:
        """Refill the bucket to capacity immediately."""
        with self._lock:
            self._tokens = self._capacity
            self._last_refill = time.monotonic()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        """Add tokens proportional to elapsed time since last refill.

        Must be called while *self._lock* is held.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                self._capacity,
                self._tokens + elapsed * self._refill_rate,
            )
            self._last_refill = now

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"TokenBucket(refill_rate={self._refill_rate!r}, capacity={self._capacity!r}, "
            f"tokens={self._tokens:.4f})"
        )


if __name__ == "__main__":
    import time as _time

    print("Token-bucket demo — 5 tokens/sec, capacity 10\n")
    bucket = TokenBucket(refill_rate=5, capacity=10)

    for i in range(15):
        result = bucket.allow()
        status = "allowed" if result else "rate limited"
        print(f"  Request {i + 1:>2}: {status}  (remaining: {bucket.remaining():.2f})")
        _time.sleep(0.1)

    print("\nWaiting 1 second to let the bucket refill …")
    _time.sleep(1.0)
    print(f"Remaining after refill: {bucket.remaining():.2f}")
    print(f"allow(): {bucket.allow()}")
