# attractor-spec — RETIRED WORKING COPY

**This working copy has been retired.** The canonical vendored snapshot is
[`specs/canonical/attractor-spec-canonical.md`](canonical/attractor-spec-canonical.md). It is byte-identical to upstream
[strongdm/attractor](https://github.com/strongdm/attractor) @ `fb57a55` — read that file, not
this one.

## Why

This copy carried local edits that had drifted into **contradicting** the canonical snapshot after
`SYNC-1` re-synced `specs/canonical/*` to `fb57a55`. It described a spec this project does not
implement and upstream does not publish. Concrete contradictions at the time of retirement:

- **Five-phase lifecycle** (`:319`: `PARSE -> VALIDATE -> INITIALIZE -> EXECUTE -> FINALIZE`).
  Canonical §3.1 (`:322-326`) specifies **six** phases, with an explicit `TRANSFORM` between
  `PARSE` and `VALIDATE` — which is what this engine actually runs (see `specs/EXTENSIONS.md` §5).
- **`k_of_n` / `quorum` join policies** (`:844`, `:846`) and the whole **error-policy table**
  (`:848-854`). Upstream removed all of them at `fb57a55`; canonical §4.8 (`:848-851`) lists only
  `wait_all` and `first_success`. They survive here as bundle extensions (`specs/EXTENSIONS.md` §18),
  not as spec.
- **`preferred_next_label`** (`:2056`, `:2070`). The field is named `preferred_label` in canonical
  and in every code path in this repo.
- **`default_max_retry`** singular as the primary attribute name (`:138`, `:479`, `:1989`).
  Canonical (`:139`, `:485`, `:1993`) names it `default_max_retries` with the singular retained
  only as a legacy alias.

Keeping two spec files that disagree is worse than keeping one: a reader has no way to tell which
is normative, and a citation to `specs/attractor-spec.md:<line>` silently means nothing after the next upstream sync.

## Where things live now

| Need | Go to |
|------|-------|
| The normative upstream spec | `specs/canonical/attractor-spec-canonical.md` (byte-identical to `fb57a55`) |
| Where this bundle deliberately differs | `specs/EXTENSIONS.md` |
| Disposition of each known gap | `SPEC_CONFORMANCE.md` |

Retired 2026-08-14 (maintainer ruling, documentation/spec-alignment wave).
