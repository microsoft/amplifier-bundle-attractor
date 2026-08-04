You are generating or refining a Python module.

This is attempt 3.

Task: write a module rate_limiter.py implementing a token-bucket rate limiter as a class named TokenBucket. Write it to rate_limiter.py in the working directory. Do not write tests or other files.

Prior critique history (engine-accumulated, most recent last -- use it to descend, not re-flip):
Iteration 1 critique: Plain text response: CRITIQUE: Fix rate_limiter.py: (1) rename constructor param to `refill_rate` (not `rate`); (2) rename `consume()`/`acquire()` to `allow(tokens=1)`; (3) add module docstring with runnable usage example
Iteration 2 critique: Plain text response: CRITIQUE: Fix rate_limiter.py: (1) add `if __name__ == '__main__':` block that calls `allow()` and prints results; (2) add module-level constant `DEFAULT_CAPACITY = 64` (int); (3) add `remaining()` me
Iteration 3 critique: Plain text response: CRITIQUE: Fix rate_limiter.py criterion 9: `remaining()` must return a `float`. Currently it returns `self._tokens` which may be an `int` if the bucket was initialized with integer arguments. Change l

Write or update rate_limiter.py now.