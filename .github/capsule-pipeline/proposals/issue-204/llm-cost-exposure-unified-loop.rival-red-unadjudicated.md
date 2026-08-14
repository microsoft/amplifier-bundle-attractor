# Finding: RIVAL RED -- a gate-blind minimal implementation of the criteria did NOT green this gate (machine-proven FACT, unadjudicated here)

A minimal implementation of the maintainer criteria -- authored gate-blind, from the criteria and the pinned tree alone, BEFORE any gate existed -- was applied at the pinned base and DEFINITION.verify.sh still did not exit 0 (rival rc=1; a base control re-run confirmed the harness healthy at rc=1). For a FEATURE gate this is the strong direction of the rival lane's evidence: if that implementation is a DEFENSIBLE build of the stated criteria, this gate demands something beyond them -- an OVER-SPECIFIED gate that would stay RED at a correct different build -- and only the critic may convert this fact into a blocking objection (.ai/critique.md). If no critique verdict exists beside this finding, NOBODY has adjudicated it. An rc>=2 here is the same evidence arriving through the infrastructure door.

rival-touched top-level paths: modules/ (numstat added/removed: 343/4)
The rival diff itself deliberately does NOT ride this finding (a brief names the surface, never the diff): it lives outside the authoring workspace, as rival.patch beside the packaged capsule, where the critic and the human reviewer read it.

--- gate output under the rival patch (tail of .ai/verify-rival.log) ---
AC-6 PASS [guard]: existing Usage arithmetic and provider:response token fields unchanged
  PASS: AC-6


========================================
GATE SUMMARY
========================================
  PASS:       4
  FAIL:       1
  INFRA-FAIL: 1
========================================
Census:
AC-1: MET
AC-2: UNMET
AC-3: UNMET
AC-4: MET
AC-5: MET
AC-6: MET
========================================
GATE RESULT: FAIL (exit 1)
