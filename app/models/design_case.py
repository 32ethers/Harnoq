"""DesignCase model — records HOW and WHY a FEATURE block is solved.

A design case must record not just the chosen solution but the reasoning, the
rejected alternatives, and the risks. Confirmation (gate) requires the six
fields enumerated in REQUIRED_FOR_CONFIRM; the service enforces this.

DesignCase attaches only to a FEATURE (its block), never directly to an
INPUT-*: INPUT-* is now a holistic requirement document, not an atomic item,
so a formal per-design "which INPUT does this cover" link would be either
vacuous (every design in the project trivially "covers" the same document) or
force back the very REQ-item granularity this pipeline moved away from.
Traceability from a design back to the requirement text is prose (read the
INPUT-* material at the design stage, cite it in problem_statement), not a DB
relationship.

``mined_requirements`` is where that prose actually lands. The design stage
mines the requirement in the same conversation that settles the solution, and
no upstream object holds what it digs out — so this field, not INPUT-*, is the
only place a requirement detail elicited during design is stored. It used to
be part (1) of ``design_scope``, a markdown heading inside another field: the
most load-bearing content in the pipeline carried the weakest structural form,
which made it unaggregatable without parsing prose and easy to thin out under
a long solution. Its own field means the project-level requirement view reads
a column instead of guessing at headings, and ``design_scope`` goes back to
meaning only the boundary.
"""
import json

from peewee import CharField, DateTimeField, ForeignKeyField, IntegerField, TextField

from app.db.base import SoftDeleteModel, database_proxy
from app.models.feature import Feature
from app.models.project import Project

# Shared 3-state lifecycle (see app/services/lifecycle.py):
#   draft ⇄ needs_clarification → confirmed
# "不做了" (dropping a design) is deletion (recycle bin), not a status — there
# is no cancelled/rejected/superseded state. Superseding a design (e.g. a
# redo) is just reopening/rewriting it in place; there is nothing else that
# would move it to a "superseded" status.
DESIGN_CASE_STATUSES = (
    "draft",
    "needs_clarification",
    "confirmed",
)

IMPACT_AREAS = (
    "frontend",
    "backend",
    "database",
    "api",
    "auth",
    "infrastructure",
    "ci_cd",
    "testing",
    "security",
    "performance",
    "documentation",
)

# Fields that must be non-empty to confirm a design (P1.2 acceptance #4).
REQUIRED_FOR_CONFIRM = (
    "problem_statement",
    "selected_solution",
    "selection_reason",
    "risks",
    "test_strategy",
    "acceptance_criteria",
)


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (ValueError, TypeError):
        return default


class DesignCase(SoftDeleteModel):
    project = ForeignKeyField(Project, backref="design_cases", on_delete="CASCADE")
    design_number = CharField(null=True)  # external id, per-project e.g. DESIGN-12
    # Nullable at the DB level (matches the FK's SET NULL on feature purge),
    # but design_case_service.create() always requires a feature_id.
    feature = ForeignKeyField(Feature, null=True, backref="design_cases", on_delete="SET NULL")
    status = CharField(default="draft", choices=[(v, v) for v in DESIGN_CASE_STATUSES])
    problem_statement = TextField(default="")
    #: The requirement as mined at the design stage — see the module docstring.
    mined_requirements = TextField(default="")
    #: Which project-constitution nfr_baseline rows this design verifies, and
    #: how each is measured. Its own field, not a paragraph inside
    #: test_strategy, for the reason mined_requirements is its own field: a
    #: claim buried in prose cannot be aggregated, so nothing can show which
    #: NFR rows nobody verifies. See app/services/nfr_coverage_service.py.
    verifies_nfr = TextField(default="")
    design_scope = TextField(default="")
    non_goals = TextField(default="")
    assumptions = TextField(default="")
    constraints = TextField(default="")
    selected_solution = TextField(default="")
    selection_reason = TextField(default="")
    rejected_solutions = TextField(default="[]")  # JSON list of {name, description, reason}
    impact_areas = TextField(default="[]")  # JSON list of impact-area strings
    risks = TextField(default="")
    test_strategy = TextField(default="")
    acceptance_criteria = TextField(default="")
    open_questions = TextField(default="[]")  # JSON list of strings
    reviewer = CharField(null=True)
    confirmed_at = DateTimeField(null=True)

    class Meta:
        database = database_proxy
        table_name = "design_case"
        indexes = ((("project", "design_number"), True),)

    def to_dict(self, *, ctx=None):
        """Serialize. In a LIST, pass ``ctx`` (see design_case_service.dict_ctx)
        so the feature is read from a pre-fetched map instead of a query PER
        design (an N+1 in list rendering). A single call passes no ctx and
        self-queries as before."""
        if ctx is not None:
            _feat = ctx["feature_by_id"].get(self.feature_id)
            _feature_number = _feat[0] if _feat else None
            _feature_name = _feat[1] if _feat else None
        else:
            _feature_number = (
                self.feature.feature_number if self.feature_id and self.feature is not None else None
            )
            _feature_name = (
                self.feature.name if self.feature_id and self.feature is not None else None
            )
        return {
            "id": self.id,
            "design_number": self.design_number,
            "project_id": self.project_id,
            "feature_id": self.feature_id,
            "feature_number": _feature_number,
            "feature_name": _feature_name,
            "status": self.status,
            "problem_statement": self.problem_statement,
            "mined_requirements": self.mined_requirements,
            "verifies_nfr": self.verifies_nfr,
            "design_scope": self.design_scope,
            "non_goals": self.non_goals,
            "assumptions": self.assumptions,
            "constraints": self.constraints,
            "selected_solution": self.selected_solution,
            "selection_reason": self.selection_reason,
            "rejected_solutions": _loads(self.rejected_solutions, []),
            "impact_areas": _loads(self.impact_areas, []),
            "risks": self.risks,
            "test_strategy": self.test_strategy,
            "acceptance_criteria": self.acceptance_criteria,
            "open_questions": _loads(self.open_questions, []),
            "reviewer": self.reviewer,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }


# Content fields captured in each version snapshot. Status/confirmed_at are
# gate metadata, not editable content, so (like the other version models) they
# are deliberately excluded from the snapshot.
DESIGN_CASE_VERSIONED_FIELDS = (
    "problem_statement",
    "mined_requirements",
    "verifies_nfr",
    "design_scope",
    "non_goals",
    "assumptions",
    "constraints",
    "selected_solution",
    "selection_reason",
    "rejected_solutions",
    "impact_areas",
    "risks",
    "test_strategy",
    "acceptance_criteria",
    "open_questions",
    "reviewer",
)


class DesignCaseVersion(SoftDeleteModel):
    design_case = ForeignKeyField(DesignCase, backref="versions", on_delete="CASCADE")
    version_number = IntegerField()
    change_type = CharField()
    restored_from_version_number = IntegerField(null=True)
    problem_statement = TextField(default="")
    mined_requirements = TextField(default="")
    verifies_nfr = TextField(default="")
    design_scope = TextField(default="")
    non_goals = TextField(default="")
    assumptions = TextField(default="")
    constraints = TextField(default="")
    selected_solution = TextField(default="")
    selection_reason = TextField(default="")
    rejected_solutions = TextField(default="[]")
    impact_areas = TextField(default="[]")
    risks = TextField(default="")
    test_strategy = TextField(default="")
    acceptance_criteria = TextField(default="")
    open_questions = TextField(default="[]")
    reviewer = CharField(null=True)

    class Meta:
        database = database_proxy
        table_name = "design_case_version"
        indexes = ((("design_case", "version_number"), True),)

    def to_dict(self):
        return {
            "id": self.id,
            "design_case_id": self.design_case_id,
            "version_number": self.version_number,
            "change_type": self.change_type,
            "restored_from_version_number": self.restored_from_version_number,
            "problem_statement": self.problem_statement,
            "mined_requirements": self.mined_requirements,
            "verifies_nfr": self.verifies_nfr,
            "design_scope": self.design_scope,
            "non_goals": self.non_goals,
            "assumptions": self.assumptions,
            "constraints": self.constraints,
            "selected_solution": self.selected_solution,
            "selection_reason": self.selection_reason,
            "rejected_solutions": _loads(self.rejected_solutions, []),
            "impact_areas": _loads(self.impact_areas, []),
            "risks": self.risks,
            "test_strategy": self.test_strategy,
            "acceptance_criteria": self.acceptance_criteria,
            "open_questions": _loads(self.open_questions, []),
            "reviewer": self.reviewer,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
