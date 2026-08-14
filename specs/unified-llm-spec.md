# unified-llm-spec — RETIRED WORKING COPY

**This working copy has been retired.** The canonical vendored snapshot is
[`specs/canonical/unified-llm-spec-canonical.md`](canonical/unified-llm-spec-canonical.md). It is byte-identical to upstream
[strongdm/attractor](https://github.com/strongdm/attractor) @ `fb57a55` — read that file, not
this one.

## Why

This copy carried local edits that had drifted into **contradicting** the canonical snapshot after
`SYNC-1` re-synced `specs/canonical/*` to `fb57a55`. It described a spec this project does not
implement and upstream does not publish. Concrete contradictions at the time of retirement:

This copy was 16 lines short of upstream and carried undocumented local edits (see the `SYNC-1`
baseline note in `SPEC_CONFORMANCE.md`). Rather than reconstruct which edits were deliberate,
the canonical snapshot is now the single source: it is byte-identical to `fb57a55`, and every
intentional difference between this bundle and that spec is recorded in `specs/EXTENSIONS.md`
and dispositioned in `SPEC_CONFORMANCE.md` (`ULM-*` rows).

Keeping two spec files that disagree is worse than keeping one: a reader has no way to tell which
is normative, and a citation to `specs/unified-llm-spec.md:<line>` silently means nothing after the next upstream sync.

## Where things live now

| Need | Go to |
|------|-------|
| The normative upstream spec | `specs/canonical/unified-llm-spec-canonical.md` (byte-identical to `fb57a55`) |
| Where this bundle deliberately differs | `specs/EXTENSIONS.md` |
| Disposition of each known gap | `SPEC_CONFORMANCE.md` |

Retired 2026-08-14 (maintainer ruling, documentation/spec-alignment wave).
