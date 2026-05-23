# PRINCIPLES.md — amplifier-bundle-attractor

Design context to consider before proposing or making changes here. Read this before planning a change.

This file is JIT-loaded — it is *not* part of the always-on conventions in `AGENTS.md`. Read it when you are about to design or modify behavior; ignore it when you are just running tests or following a known runbook.

---

## Upstream contract

This repo implements the **attractor nlspec** — DOT-graph-as-program execution where nodes are computation, edges are dispatch, and clusters are subgraphs.

The canonical specification lives upstream at [`strongdm/attractor`](https://github.com/strongdm/attractor). The nlspec under that repo is authoritative. Our `specs/` directory holds the canonical reference and our extensions to it; the relationship is one-way — upstream defines the language, we add to it for our use cases.

This repo **extends** the upstream spec; it does not contradict it.

---

## Walk upstream first

When fixing bugs, resolving doc-vs-code discrepancies, or considering new features here, **check the upstream first** to see what is *supposed* to be the case.

- Many "bugs" here are divergences from spec that should be corrected toward upstream, not patched away from it.
- Many "missing features" are already specified upstream — implement to spec rather than inventing a parallel.
- When extending beyond what is specified, the extension itself is the change worth deliberating. The in-spec parts are not negotiable.

This is the single highest-leverage check before proposing a change here. Skipping it means re-deriving decisions someone has already made carefully, or worse, breaking interoperability with community-provided `.dot` files in subtle ways the test suite will not catch.

If you find yourself "fixing" something that is spec-conformant — stop. Read the spec. Confirm. Then decide whether the change is a *fix* (back toward spec), an *extension* (documented delta), or a *mistake* (revert and reconsider).

---

## Intentional deltas from upstream

Document accepted deviations from the canonical spec here. Each delta should record:

- **What we changed** — which spec section, what upstream defines, what we do instead.
- **Why** — the use case that justified the deviation; what was insufficient about the upstream behavior.
- **Link** — the PR or decision that landed it.

Empty until the next change author captures theirs. If a delta exists in the codebase today and is not listed here, this is the place to add it.

---

## When you are extending the spec

Extensions to the canonical spec — new node shapes, new dispatch semantics, new event contracts — live in `specs/`. Add or update a spec extension document in the same PR that lands the implementation. Implementation without a corresponding spec note is debt; the next contributor cannot tell whether the behavior is intentional or accidental.

If the extension could plausibly live upstream — i.e., it is general enough that other consumers of the nlspec would want it — consider contributing it back to `strongdm/attractor` instead of forking the behavior here. The cost of upstream contribution is paid once; the cost of an undocumented local fork is paid by every future contributor who has to figure out why our behavior differs from spec.

---

## Pointers

- `specs/` — our spec extensions and the canonical attractor reference.
- `AGENTS.md` — runtime conventions for this repo (test commands, common engine pitfalls, the verification gradient).
- `examples/pipelines/` — canonical pipeline patterns; live test fixtures when verifying engine changes.
- [`strongdm/attractor`](https://github.com/strongdm/attractor) — the canonical upstream spec and nlspec.
