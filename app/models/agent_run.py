"""AgentRun — a single execution record of one of the two development agents.

The development-loop state machine in ``development_task_service`` consumes
terminal AgentRun results (passed/failed/needs_fix) to drive a DevelopmentTask
from ``coding`` toward ``ready_for_pr``. Non-terminal statuses
(pending/running/cancelled) are recorded but do not transition the task.

``blocked`` is the outcome for "the run never got to judge", as distinct from
"the run judged and found problems". A qa subagent that cannot reach its
design — the Harnoq MCP surface was down when its session started, so the
harnoq_* tools were never injected — has no verdict to give. Reporting that as
``needs_fix`` sent the task back to coder with nothing to fix and burned a qa
round as if verification had happened (TASK-1, 2026-07-21). ``blocked`` records
it as itself and leaves the task where it stands.

``design_number``/``criteria_verdicts`` carry a qa verdict's evidence. Honesty
about missing context is a property of the model that happened to be running,
not of the system; a qa agent without the design can write a confident PASS
that is indistinguishable from a real one. Quoting every criterion of the
design is something only an agent holding that design can do, which is what
makes the gate in ``record_agent_run`` structural rather than advisory.

A per-criterion verdict is not only ``pass``/``fail``: ``deviates`` records
"this behaves, but not as the design says" — a different return shape, a
different error channel, a condition the design describes differently from
what the code does. Before it existed, a qa agent that found exactly this had
nowhere to put it: ``fail`` overstates a working feature and ``pass`` erases
the divergence, so real divergences got written into the evidence prose and
then filed as ``pass`` anyway, because prose is not read by the gate. The
fix is structural, not a nicer word: ``deviates`` is refused under
``status="passed"`` the same way ``fail`` is (see ``_qa_evidence`` in
``development_task_service``), so a divergence can no longer ride along
inside a passing verdict — it forces ``needs_fix``, which forces the manager
to actually decide whether the code should change to match the design or the
design itself was wrong, instead of the question being silently pre-answered
by a passing verdict that never surfaced it.

Each entry's own ``evidence`` sub-field carries either shape a qa agent can
report (see ``_qa_evidence_value`` in ``development_task_service``): a plain
string, the historical free-text form, kept exactly as written; or a
structured ``{tests, code, note}`` object naming which tests cover the
criterion and where in the code it is satisfied. Re-verifying an unchanged
design used to cost most of its time in relocating, per criterion, an answer
qa had already written down as prose the previous round and could not query
back. An empty ``tests`` list in the structured form is a real answer
("nothing automated watches this"), not a gap to reject — it is what
``development_task_service.uncovered_criteria`` reads to name criteria with
no test coverage after the task's latest qa run. A string entry is left out
of that view entirely rather than counted as uncovered: prose cannot be split
into a tests/code breakdown without inventing data nobody wrote.

``test_run`` carries the qa run's own test-suite execution as separate
integer fields, not a sentence, because a sentence was the failure mode this
replaces: a qa run once reported "58 passed" when the suite actually ran 2
failed / 56 passed on that exact commit — the agent had read pytest's
COLLECTED count (58) and reported it as the PASSED count, and Harnoq stored
the sentence verbatim, gating nothing on it. ``collected`` and ``passed``
being distinct fields does not stop that specific lie from being told again,
but it does stop it from being invisible: a run that reports the collection
count as the pass count now has to also report ``failed``, and
``record_agent_run`` rejects ``status="passed"`` outright when ``failed`` is
non-zero or ``exit_code`` is non-zero. Making a false claim structurally
impossible is not the goal here — that is not achievable from a record the
agent itself writes — only making a false claim visible in the record rather
than absorbed by it.

``skipped`` joined them later, for the failure the other four cannot express:
a suite that skipped a test reports the same ``failed=0 exit_code=0`` as one
that ran it, so a record without the count cannot distinguish evidence from
its absence. It is required but never refused — skipping is legitimate, and
whether a skip leaves a specific criterion unproven is that criterion's
verdict to state.
"""
import json

from peewee import CharField, DateTimeField, ForeignKeyField, TextField

from app.core.time import utcnow
from app.db.base import BaseModel, database_proxy
from app.models.development_task import DevelopmentTask

AGENT_ROLES = ("coder", "qa")

#: An AgentRun is born terminal: the agent reports what its run concluded,
#: after the fact. ``pending``/``running``/``cancelled`` were written by
#: nothing — they
#: describe a run being WATCHED, and Harnoq does not watch agents, it records
#: what they report. Same reason the git-host-observer values left
#: PullRequestRecord.
AGENT_RUN_STATUSES = (
    "passed",
    "failed",
    "needs_fix",
    "blocked",
)

#: Verdict values inside ``criteria_verdicts``. ``deviates`` sits between
#: ``pass`` and ``fail``: the behaviour is present but does not match what the
#: design states. It is refused under an agent-run ``status="passed"`` (see
#: ``_qa_evidence``) — riding along inside a pass is what made the earlier
#: two-value enum a hole, not the missing word by itself.
CRITERION_VERDICTS = ("pass", "fail", "deviates")


class AgentRun(BaseModel):
    task = ForeignKeyField(DevelopmentTask, backref="agent_runs", on_delete="CASCADE")
    role = CharField(choices=[(v, v) for v in AGENT_ROLES])
    status = CharField(choices=[(v, v) for v in AGENT_RUN_STATUSES])
    summary = TextField(default="")
    #: DESIGN-* this run validated against. Set on qa verdicts only.
    design_number = CharField(null=True)
    #: JSON list of {criterion, verdict, evidence}, one per criterion of that
    #: design. Empty for coder runs and for qa runs that reached no verdict.
    #: ``evidence`` is either a free-text string (legacy) or a structured
    #: {tests, code, note} object — see the module docstring and
    #: development_task_service._qa_evidence_value.
    criteria_verdicts = TextField(default="")
    #: JSON {command, exit_code, collected, passed, failed, skipped} — qa's own
    #: test-suite execution. Separate integer fields, not a sentence: see the
    #: module docstring for the incident (collected count reported as passed
    #: count) that separate fields make self-contradictory instead of
    #: unfalsifiable. Empty for coder runs, blocked runs, and runs recorded
    #: before this field existed.
    test_run = TextField(default="")
    started_at = DateTimeField(null=True)
    ended_at = DateTimeField(null=True)
    created_at = DateTimeField(default=utcnow)

    class Meta:
        database = database_proxy
        table_name = "agent_run"

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "role": self.role,
            "status": self.status,
            "summary": self.summary,
            "design_number": self.design_number,
            "criteria_verdicts": json.loads(self.criteria_verdicts or "[]"),
            "test_run": json.loads(self.test_run) if self.test_run else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
