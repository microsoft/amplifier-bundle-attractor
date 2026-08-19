"""S2 — Leverage: cost-now toil already spent.

Leverage is toil ALREADY BURNED, not automatability (that is the Fit binary)
and not how many times the user pressed enter.

Three calibration decisions are load-bearing and each one is defended here:

* **`n_prompts` is DROPPED.** Measured separation 1.0x — literally zero
  signal. The user's real loops happen INSIDE one prompt, driven by the
  agent. Any design that proxies toil by counting human turns measures
  nothing.
* **Wall span is CAPPED at 7,200 s.** 10.6% of sessions exceed it and the raw
  max is 7,745,294 s — a 90-day abandoned open session that would otherwise
  become the highest-toil unit in the corpus.
* **Aggregation is MEDIAN within cluster, never p75.** At session level the
  corpus is violently skewed (p75/median = 12.8x) so p75 looks tempting; at
  CLUSTER level that ratio collapses to 1.22x — clustering already absorbs
  the skew, and p75 then just amplifies one long outlier session into a
  cluster-wide claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SPAN_CAP_S = 7_200.0
#: Span is divided by 60 so a minute of wall time weighs like one tool call.
SPAN_DIVISOR = 60.0
#: Errors are doubled: sparsest and most discriminating of the dense proxies.
ERROR_WEIGHT = 2.0


def median(values: list[float]) -> float:
    """Median. Never p75 — see the module docstring."""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def percentile(values: list[float], pct: float) -> float:
    """Linearly-interpolated percentile. A SPREAD indicator only.

    Interpolated rather than nearest-rank so p75 genuinely reflects the top
    quartile on small clusters. With nearest-rank, the ablation arm that
    REJECTS p75 would pass for the wrong reason: rounding would hide the very
    outlier the arm exists to expose.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = pct * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return float(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo))


def span_capped(rec: dict) -> float:
    """Capped wall span for one session, in seconds."""
    val = rec.get("span_capped_s")
    if val is None:
        val = rec.get("span_s")
    if val is None:
        return 0.0
    return min(max(float(val), 0.0), SPAN_CAP_S)


@dataclass
class LeverageProfile:
    """Cluster-level toil profile."""

    n_sessions: int
    med_tool_calls: float
    med_llm_cycles: float
    med_span_capped_s: float
    errors_per_session: float
    leverage: float
    #: Sparse boosters — reported, never in the base combination.
    med_err_recover: float = 0.0
    med_delegates: float = 0.0
    #: Spread indicators only.
    p75_tool_calls: float = 0.0
    p75_span_capped_s: float = 0.0
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "n_sessions": self.n_sessions,
            "med_tool_calls": self.med_tool_calls,
            "med_llm_cycles": self.med_llm_cycles,
            "med_span_capped_s": self.med_span_capped_s,
            "errors_per_session": self.errors_per_session,
            "leverage": self.leverage,
            "med_err_recover": self.med_err_recover,
            "med_delegates": self.med_delegates,
            "p75_tool_calls": self.p75_tool_calls,
            "p75_span_capped_s": self.p75_span_capped_s,
            **self.detail,
        }


def compute_leverage(
    members: list[dict],
    *,
    cap_span: bool = True,
    aggregate: str = "median",
    include_n_prompts: bool = False,
) -> LeverageProfile:
    """Cluster leverage.

        leverage = med_tool_calls + med_llm_cycles + med_span_capped/60
                   + 2 * (tool_errors / n_sessions)

    `cap_span=False`, `aggregate="p75"` and `include_n_prompts=True` exist
    ONLY as the Scenario-3 ablation arms. They are never production paths;
    each one is a measured-worse alternative kept runnable so the calibration
    stays falsifiable rather than folklore.
    """
    if aggregate not in ("median", "p75"):
        raise ValueError(f"unknown aggregate: {aggregate!r}")
    agg = median if aggregate == "median" else (lambda v: percentile(v, 0.75))

    n = len(members)
    if n == 0:
        return LeverageProfile(0, 0.0, 0.0, 0.0, 0.0, 0.0)

    tools = [float(m.get("n_tool_calls", 0) or 0) for m in members]
    llm = [float(m.get("n_llm_cycles", 0) or 0) for m in members]
    if cap_span:
        spans = [span_capped(m) for m in members]
    else:
        spans = [float(m.get("span_s") or 0.0) for m in members]
    errs = sum(float(m.get("n_tool_errors", 0) or 0) for m in members)

    med_tools = agg(tools)
    med_llm = agg(llm)
    med_span = agg(spans)
    err_rate = errs / n

    leverage = med_tools + med_llm + (med_span / SPAN_DIVISOR) + ERROR_WEIGHT * err_rate
    detail: dict = {}
    if include_n_prompts:
        # Ablation arm i: measured 1.0x separation. Kept runnable so the DROP
        # decision can be re-falsified, never wired into the shipped score.
        med_prompts = agg([float(m.get("n_prompts", 0) or 0) for m in members])
        detail["med_n_prompts"] = med_prompts
        leverage += med_prompts

    return LeverageProfile(
        n_sessions=n,
        med_tool_calls=med_tools,
        med_llm_cycles=med_llm,
        med_span_capped_s=med_span,
        errors_per_session=err_rate,
        leverage=leverage,
        med_err_recover=agg([float(m.get("n_err_recover", 0) or 0) for m in members]),
        med_delegates=agg([float(m.get("n_delegates", 0) or 0) for m in members]),
        p75_tool_calls=percentile(tools, 0.75),
        p75_span_capped_s=percentile(spans, 0.75),
        detail=detail,
    )


def span_term(members: list[dict], *, cap_span: bool = True) -> float:
    """The span contribution to leverage, in leverage units.

    With the cap on, this is a HARD INVARIANT: it can never exceed
    7200/60 = 120. Scenario 3 Arm ii machine-checks exactly that.
    """
    spans = [span_capped(m) if cap_span else float(m.get("span_s") or 0.0) for m in members]
    return median(spans) / SPAN_DIVISOR


def separation(high: list[dict], low: list[dict], proxy: str, **kwargs) -> float:
    """Cluster-median separation ratio between two populations for one proxy.

    Used by the Scenario-3 arms. Returns `inf` when the low population's
    median is 0 and the high's is not (a real, reportable separation), and
    0.0 when both are 0 (no signal) — never a silent divide-by-zero.
    """
    getters = {
        "tool_calls": lambda m: float(m.get("n_tool_calls", 0) or 0),
        "llm_cycles": lambda m: float(m.get("n_llm_cycles", 0) or 0),
        "span_capped": span_capped,
        "span_raw": lambda m: float(m.get("span_s") or 0.0),
        "tool_errors": lambda m: float(m.get("n_tool_errors", 0) or 0),
        "n_prompts": lambda m: float(m.get("n_prompts", 0) or 0),
        "leverage": None,
    }
    if proxy not in getters:
        raise ValueError(f"unknown proxy: {proxy!r}")
    if proxy == "leverage":
        hi = compute_leverage(high, **kwargs).leverage
        lo = compute_leverage(low, **kwargs).leverage
    else:
        getter = getters[proxy]
        assert getter is not None
        hi = median([getter(m) for m in high])
        lo = median([getter(m) for m in low])
    if lo == 0.0:
        return float("inf") if hi > 0.0 else 0.0
    return hi / lo
