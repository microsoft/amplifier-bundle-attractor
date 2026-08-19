"""S8 — AUTHOR classification (harness / human / mixed): the DETERMINISTIC PRIOR.

This is an **admission gate**, not a ranking dimension. Ranked purely by
frequency, the top two clusters in the calibration corpus were the machine
talking to itself (194-session liveness sentinels; 164-session single-shot
classifier calls) and 59.7% of clustered sessions were harness-authored.
Without this gate, Frequency ranks the machine's own noise above human work.

The prior is deliberately incomplete. It **over-calls human** (42 of 54
clusters vs a true 33) because it cannot see that templated autonomous "lane"
missions are harness-LAUNCHED but contain real engineering work. Recovering
that over-call is the job of the `general`-tier cluster adjudication that
sits above this module (Gate 2) — the prior's job is to be cheap, local, and
honest about its own resolution.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

HARNESS = "harness"
HUMAN = "human"
MIXED = "mixed"

#: Sentinel liveness probes: "Say OK", "hi", "reply with the sentinel", ...
SENTINEL_RE = re.compile(
    r"^\W*(say\s+(ok|hi|hello|ready|pong|[a-z0-9_-]{1,20})|ok|okay|hi|hey|hello|yo|ping|pong|test|testing|"
    r"are\s+you\s+(there|alive|up|ready)|status|reply\s+with\b.{0,60}|respond\s+with\b.{0,60}|"
    r"echo\b.{0,40}|just\s+say\b.{0,40}|sanity\s*check|smoke\s*test)\W*$",
    re.IGNORECASE,
)

#: Eval-harness / simulated-AI-user phrasing.
EVAL_PHRASE_RE = re.compile(
    r"you\s+are\s+(an?\s+)?(ai|simulated|synthetic|scripted)\s+user|"
    r"you\s+are\s+being\s+evaluated|simulate\s+(a|the)\s+user|act\s+as\s+the\s+user|"
    r"eval(uation)?\s+(harness|task|run|scenario|rubric|session)|"
    r"score\s+the\s+(following|session|trace|transcript)|"
    r"against\s+(the\s+)?(rubric|criteria)|emit\s+(only\s+)?(a\s+)?json|"
    r"respond\s+with\s+(only\s+)?json|"
    r"return\s+(only\s+)?(a\s+)?json\s+(object|verdict)|"
    r"\b0/1/2\b|verdict\s*:|grade\s+this|"
    r"do\s+not\s+ask\s+(any\s+)?clarif|"
    r"first-run\s+(experience|friction)|friction\s+log|"
    r"persona\s*:|as\s+the\s+persona|"
    r"\bprobe\b.{0,30}\bsuite\b|"
    r"acceptance\s+criteri",
    re.IGNORECASE,
)

#: Conversational human markers.
HUMAN_PHRASE_RE = re.compile(
    r"\b(please|let'?s|can you|could you|i want|i need|we need|i'?m |i've |we'?re |"
    r"thanks|thank you|hmm|actually|wait|oops|nope|nvm|never mind|"
    r"my |our |should we|what if|why is|why does|how do i|help me)\b",
    re.IGNORECASE,
)

#: Volatility masks for the templated-prompt fingerprint.
_NORM_SUBS = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE), "<hex>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]?[\d:.\-+Z]*"), "<ts>"),
    (re.compile(r"/[\w.\-/]{6,}"), "<path>"),
    (re.compile(r"\d+"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def normalize_for_fp(text: str) -> str:
    out = text.lower()
    for rx, rep in _NORM_SUBS:
        out = rx.sub(rep, out)
    return out.strip()


def fingerprint(text: str) -> str:
    return hashlib.sha1(normalize_for_fp(text)[:220].encode("utf-8")).hexdigest()[:16]


def _first_prompt(rec: dict) -> str:
    """First prompt text, tolerating both this library's and ci_mine_v2's records."""
    src = rec.get("first_prompt") or rec.get("_fp_src") or ""
    if not src:
        prompts = rec.get("prompts") or []
        src = prompts[0] if prompts else ""
    return src if isinstance(src, str) else ""


def classify_authors(records: list[dict]) -> list[dict]:
    """Assign a deterministic author PRIOR to every record, in place.

    Corpus-wide, because the load-bearing signal (templated first prompts
    repeated across sessions) is only visible across the whole selection.
    """
    exact: Counter[str] = Counter()
    near: Counter[str] = Counter()
    for rec in records:
        src = _first_prompt(rec)
        if not src:
            continue
        exact[normalize_for_fp(src)[:400]] += 1
        near[fingerprint(src)] += 1

    for rec in records:
        src = _first_prompt(rec)
        n_exact = exact.get(normalize_for_fp(src)[:400], 0) if src else 0
        n_near = near.get(fingerprint(src), 0) if src else 0
        harness_score, harness_sig = _harness_score(rec, src, n_exact, n_near)
        human_score, human_sig = _human_score(rec, src, n_exact, n_near)

        if harness_score >= 3 and human_score <= 1:
            author = HARNESS
        elif harness_score >= 3 or (harness_score >= 2 and human_score <= 1):
            author = MIXED
        else:
            author = HUMAN

        rec["author"] = author
        rec["author_signals"] = harness_sig[:4]
        rec["author_scores"] = {"harness": harness_score, "human": human_score}
        rec["author_human_signals"] = human_sig[:4]
        rec["fp_exact_n"] = n_exact
        rec["fp_near_n"] = n_near
    return records


def _harness_score(rec: dict, src: str, n_exact: int, n_near: int) -> tuple[int, list[str]]:
    score = 0
    sig: list[str] = []
    if src and SENTINEL_RE.match(src.strip()[:120]):
        score += 3
        sig.append("sentinel")
    if src and EVAL_PHRASE_RE.search(src):
        score += 3
        sig.append("eval-phrasing")
    if n_exact >= 5 and len(src) >= 200:
        score += 3
        sig.append(f"template-exact-x{n_exact}")
    elif n_exact >= 5:
        score += 1
        sig.append(f"repeat-exact-x{n_exact}")
    if n_near >= 10 and n_exact < 5:
        score += 1
        sig.append(f"template-near-x{n_near}")
    if rec.get("n_prompts", 0) <= 1:
        score += 1
        sig.append("single-shot")
    if rec.get("machine_launched"):
        score += 2
        sig.append("machine-launched")
    return score, sig


def _human_score(rec: dict, src: str, n_exact: int, n_near: int) -> tuple[int, list[str]]:
    score = 0
    sig: list[str] = []
    n_prompts = rec.get("n_prompts", 0)
    if n_prompts >= 4:
        score += 2
        sig.append(f"multi-turn-{n_prompts}")
    elif n_prompts >= 2:
        score += 1
        sig.append(f"turns-{n_prompts}")
    if src and HUMAN_PHRASE_RE.search(src):
        score += 1
        sig.append("conversational")
    if rec.get("loop_markers", 0) >= 1:
        score += 1
        sig.append("loop-markers")
    if n_exact <= 1 and n_near <= 2:
        score += 1
        sig.append("unique-prompt")
    return score, sig


def cluster_author_prior(members: list[dict]) -> dict:
    """Roll per-session priors up to a cluster-level prior.

    Returns the measured mix plus the majority label. This is explicitly the
    OVER-CALLING half of S8: the `general`-tier adjudication above it exists
    to correct it, and `ranking.apply_admission_gate` consumes whichever
    label is authoritative.
    """
    mix = Counter(m.get("author", HUMAN) for m in members)
    majority = mix.most_common(1)[0][0] if mix else HUMAN
    return {
        "author_prior": majority,
        "author_mix": dict(mix),
        "n_members": len(members),
    }
