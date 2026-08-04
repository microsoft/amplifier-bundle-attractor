#!/usr/bin/env python3
"""Mechanical criteria gate for the feedback-live run. Exit 0 = all pass."""
import inspect
import sys

failures = []

try:
    src = open("rate_limiter.py", encoding="utf-8").read()
except OSError as e:
    print(f"FAIL: rate_limiter.py unreadable: {e}")
    sys.exit(1)

# Load rate_limiter.py explicitly from the working directory: this checker
# lives OUTSIDE the cwd, so plain `import rate_limiter` would resolve against
# the checker's own directory (sys.path[0]), not the cwd.
import importlib.util

spec = importlib.util.spec_from_file_location("rate_limiter", "rate_limiter.py")
try:
    rate_limiter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rate_limiter)
except Exception as e:
    print(f"FAIL: loading rate_limiter.py: {e}")
    sys.exit(1)

TB = getattr(rate_limiter, "TokenBucket", None)
if TB is None:
    failures.append("class TokenBucket missing")
else:
    params = []
    try:
        params = list(inspect.signature(TB.__init__).parameters)
    except (TypeError, ValueError) as e:
        failures.append(f"cannot inspect TokenBucket.__init__: {e}")
    if "capacity" not in params:
        failures.append("criterion 1: constructor must take a capacity argument")
    if "refill_rate" not in params:
        failures.append("criterion 1: constructor must take a refill_rate argument (this exact name)")
    allow = getattr(TB, "allow", None)
    if allow is None:
        failures.append("criterion 2: TokenBucket must have an allow(tokens=1) method")
    else:
        try:
            tb = TB(capacity=2, refill_rate=1.0)
            r = tb.allow()
            if not isinstance(r, bool):
                failures.append("criterion 2: allow() must return a bool")
        except Exception as e:
            failures.append(f"criterion 2: TokenBucket(capacity=2, refill_rate=1.0).allow() raised: {e}")
        hints = getattr(allow, "__annotations__", {})
        if "return" not in hints:
            failures.append("criterion 6: allow() must carry type hints including a return annotation")

if "time.monotonic" not in src and "monotonic()" not in src:
    failures.append("criterion 3: refill must be computed from time.monotonic(), never wall-clock")
if "threading.Lock" not in src and "Lock()" not in src:
    failures.append("criterion 4: state mutation must be guarded by an explicit threading.Lock")
doc = rate_limiter.__doc__ or ""
if ">>>" not in doc and "Example" not in doc and "example" not in doc:
    failures.append("criterion 5: the module docstring must contain a runnable usage example")
if "__main__" not in src:
    failures.append("criterion 7: an if __name__ == '__main__' demo block must exercise allow() and print results")

DC = getattr(rate_limiter, "DEFAULT_CAPACITY", None)
if DC != 64:
    failures.append("criterion 8: the module must expose a constant DEFAULT_CAPACITY = 64 (int)")
if TB is not None:
    rem = getattr(TB, "remaining", None)
    if rem is None:
        failures.append("criterion 9: TokenBucket must provide a remaining() method returning the current token count as a float")
    else:
        try:
            v = TB(capacity=2, refill_rate=1.0).remaining()
            if not isinstance(v, float):
                failures.append("criterion 9: remaining() must return a float")
        except Exception as e:
            failures.append(f"criterion 9: remaining() raised: {e}")
    if "__repr__" not in vars(TB):
        failures.append("criterion 10: TokenBucket must define __repr__ (showing capacity and refill_rate)")

if failures:
    print("CRITERIA GATE: FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("CRITERIA GATE: PASS (all 10 criteria)")
