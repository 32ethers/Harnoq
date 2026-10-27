"""Which project-constitution NFR rows anybody actually verifies.

The constitution's ``nfr_baseline`` binds the whole project, and it was the one
document in the pipeline with no verification surface at all. Acceptance
criteria get a verdict each — a qa run must return one entry per criterion,
quoting it — while an NFR row was stated at the constitution stage, sometimes
echoed in a design's ``test_strategy``, and checked by nobody. A live project
reached six merged tasks with a stated 500,000-row capacity target and a table
carrying no index on either column its own browse query sorts by, and nothing
in the pipeline was positioned to notice, because nothing was ever asked.

The fix is ownership, applied the way scope ownership already is: a design
states which NFR rows it verifies, in its own ``verifies_nfr`` field, and this
module DERIVES the coverage. Nothing here is persisted — a second copy would
drift from the designs, and the whole point is a view that cannot disagree
with them.

Only COMMITMENT rows are asked for an owner; see ``nfr_baseline`` for that
distinction and why it had to become a column. A direction is still listed,
and a design may still claim one, but a direction with no claimant is not a
hole — there is nothing to measure, so an owner would own nothing.

Two holes are reported, and they are different failures:

* a commitment no design claims — the constitution asked for something and no
  design took responsibility for proving it;
* a claim naming no NFR row — a design believes it is verifying a project
  commitment that the constitution does not contain, so either the wording
  drifted or the commitment was never consolidated.

Matching is by the metric name as the constitution writes it, normalised for
whitespace and case. That is deliberately literal: a fuzzy match would let a
design claim coverage it does not have, which is the failure this exists to
surface, not one to reproduce.
"""
from app.services import (
    design_case_service,
    development_task_service,
    project_constitution_service,
)
from app.services.nfr_baseline import (  # re-exported: the parsing lives there
    COMMITMENT,
    DIRECTION,
    extract_claims,
    extract_nfr_rows,
    norm as _norm,
)

__all__ = [
    "COMMITMENT", "DIRECTION", "build", "extract_claims", "extract_nfr_rows",
]


def build(project_id):
    """Coverage of the project's NFR baseline by its designs.

    Returns a dict with the NFR rows (each carrying the designs that claim it),
    the claims that match no row, and the counts a reader needs first.
    """
    constitution = project_constitution_service.require_for_project(project_id)
    rows = extract_nfr_rows(constitution.nfr_baseline)
    designs = design_case_service.list_by_project(project_id)

    # One query for every task, then group in memory: a per-design task lookup
    # here is an N+1 that only shows up once a project has many designs.
    tasks_by_design = {}
    for task in development_task_service.list_by_project(project_id):
        tasks_by_design.setdefault(task.design_case_id, []).append(task)

    claims_by_design = [
        (design, extract_claims(design.verifies_nfr)) for design in designs
    ]

    unmatched_claims = []
    for design, claims in claims_by_design:
        for claim in claims:
            if not any(_norm(row["metric"]) in _norm(claim) for row in rows):
                unmatched_claims.append({
                    "design_number": design.design_number,
                    "claim": claim,
                })

    out_rows = []
    for row in rows:
        metric = _norm(row["metric"])
        verified_by = []
        for design, claims in claims_by_design:
            matched = [claim for claim in claims if metric in _norm(claim)]
            if not matched:
                continue
            verified_by.append({
                "design_id": design.id,
                "design_number": design.design_number,
                "design_status": design.status,
                "how": matched,
                "tasks": [
                    {"task_number": t.task_number, "status": t.status}
                    for t in tasks_by_design.get(design.id, [])
                ],
            })
        # A direction is never "unverified": nothing about it is measurable, so
        # an unclaimed one is the expected state rather than a missing owner.
        out_rows.append({
            **row,
            "verified_by": verified_by,
            "unverified": row["strength"] == COMMITMENT and not verified_by,
        })

    unverified = [r["metric"] for r in out_rows if r["unverified"]]
    return {
        "project_id": project_id,
        "constitution_status": constitution.status,
        "total": len(out_rows),
        "commitment_count": sum(
            1 for r in out_rows if r["strength"] == COMMITMENT
        ),
        "direction_count": sum(1 for r in out_rows if r["strength"] == DIRECTION),
        "unverified_count": len(unverified),
        "unverified_metrics": unverified,
        "rows": out_rows,
        "unmatched_claims": unmatched_claims,
    }
