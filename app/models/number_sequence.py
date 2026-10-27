"""Monotonic business-number counters (one row per prefix).

Business numbers used to be derived from the row's own primary key
(``f"{PREFIX}-{id}"``). That was unique *while rows lived*, but SQLite (and any
non-AUTOINCREMENT rowid table) **reuses** the freed rowid after a hard purge of
the highest row, so a purged ``PLAN-6`` could be handed to a brand-new, unrelated
plan — a 留痕 hazard where one number points at two different objects over time.

This table decouples the number from the rowid: each prefix keeps a counter that
**only ever increases**. Purging a row frees its rowid but never rewinds the
counter, so a number is issued exactly once for all time. Lookups already resolve
the stored ``*_number`` string column (never by parsing the number back to an id),
so the number no longer has to equal the primary key.

Counters are scoped ``(project_id, prefix)``: each project hands out its own
INPUT-1, DESIGN-1, TASK-1, ... independently (see docs/cn/design-evolution.md#storage-ui).
``project_id`` is ``NULL`` for global entities such as projects and users;
each global prefix keeps its own counter.
"""
from peewee import CharField, IntegerField

from app.db.base import BaseModel, database_proxy


class NumberSequence(BaseModel):
    project_id = IntegerField(null=True)  # NULL for global prefixes, e.g. PROJECT and USER
    prefix = CharField()  # 'PROJECT', 'INPUT', 'FEATURE', 'DESIGN', 'TASK', ...
    next_value = IntegerField(default=1)  # the next number to hand out for prefix

    class Meta:
        database = database_proxy
        table_name = "number_sequence"
        indexes = ((("project_id", "prefix"), True),)
