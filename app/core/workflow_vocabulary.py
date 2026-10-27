"""Harnoq-owned workflow terms exposed to every relevant agent.

The project glossary belongs to the downstream project's domain.  These terms
belong to Harnoq itself and have the same meaning in every project, so storing
them as editable per-project GlossaryTerm rows would duplicate and weaken the
contract.  Agent-facing context surfaces import this one definition instead.
"""

DELIVERY_WORKFLOW_VOCABULARY = {
    "scope": (
        "These are Harnoq workflow terms, not project-domain glossary entries. "
        "Interpret INTEG-* and SYS-* kinds by these definitions in every project."
    ),
    "terms": {
        "INTEG-*": (
            "One indivisible missing obligation found during inventory: one exact "
            "assembly edge, one unsatisfied declared seam, or one proven convergence "
            "responsibility. Several unreachable parts blocked by the same missing edge "
            "share that edge's INTEG-*; different edges never do. It must be covered by "
            "executable QA evidence or explicitly waived before delivery."
        ),
        "SYS-*": (
            "One system-stage user journey or non-functional obligation. It is "
            "owned directly by the active system stage run and closed only by "
            "executable evidence or an explicit user waiver."
        ),
        "integration inventory manifest": (
            "The structured A-G evidence, entry, part, seam, candidate and proposed-"
            "item rows submitted to Harnoq's Round-1 validation gate. It is a "
            "pre-review dataset, not a prose PASS claim. A valid manifest stores only "
            "a creation-binding receipt, never INTEG-* or workflow-plan data."
        ),
        "production entry point": (
            "A root that actually operates delivered product behavior in its intended "
            "environment and reaches an observable boundary. Build, bundle, compile, "
            "package, code-generation, test, dev or preview commands may discover an "
            "entry candidate but are not entries themselves. A browser bundle needs a "
            "verified served/deployed load path; a function or file merely named main, "
            "start or run is also insufficient."
        ),
        "production-reachable": (
            "Reachable by a fully wired transitive path starting at a verified "
            "production entry point. A node inside an unreachable subtree is still "
            "unreachable even when its internal imports or routes are correct."
        ),
        "assembly": (
            "An assembly GAP is one exact missing producer -> consumer edge that keeps "
            "one or more declared parts off every production path. The matrix still has "
            "one row per part; INTEG-* cardinality follows unique missing edges, not the "
            "number of downstream parts made unreachable by one edge."
        ),
        "assembly matrix": (
            "The exhaustive one-row-per-declared-part reachability check. It is "
            "evidence, not the INTEG-* batch: unreachable rows reconcile through their "
            "exact missing-edge gap keys."
        ),
        "seam": (
            "A directional producer -> consumer adjacency declared in capability "
            "DESIGN-* design_scope. Every declaration is inventoried, but only an "
            "unwired seam or one lacking a passing test that directly exercises that "
            "producer -> consumer becomes an INTEG-*."
        ),
        "seam graph": (
            "The exhaustive directed graph mechanically extracted from confirmed "
            "capability DESIGN-* design_scope producer -> consumer declarations. It is "
            "evidence: satisfied rows do not become work."
        ),
        "duplicate": (
            "The same contract or responsibility implemented more than once, with "
            "the same required caller semantics, one legitimate canonical owner, and "
            "a real defect if implementations diverge. Same name, body, fields or data "
            "shape alone is only a candidate; when any ownership or change reason "
            "differs, retain the separate implementations."
        ),
        "exact-name collision": (
            "The complete identifier is exactly equal in at least two modules. "
            "A shared prefix, suffix, base type or category does not qualify."
        ),
        "source skeleton": (
            "All source types, function/method signatures and data fields with "
            "implementation bodies omitted, read with callers to compare ownership."
        ),
        "convergence": (
            "Consolidating confirmed duplicate responsibilities while preserving "
            "every caller's behavior, then enforcing one implementation with a "
            "structural fitness test."
        ),
        "journey": (
            "An ordered user-visible scenario from INPUT-* that no single DESIGN-* "
            "naturally owns; system verification drives it end to end."
        ),
        "nfr": (
            "A Project Constitution NFR commitment that must be exercised at the "
            "system boundary rather than assumed from component tests."
        ),
        "covered": (
            "Closed by a completed owning TASK-* plus one exact executable test "
            "from its latest passing QA evidence."
        ),
        "waived": (
            "Closed only because the user explicitly chose not to require it and "
            "the reason was recorded; implemented or apparently working is not waived."
        ),
    },
}
