"""Business numbers (留痕-friendly external identifiers).

Every spine entity (source requirement/unit, design case, task plan,
development task) carries a human/AI-facing number alongside its raw database
id. ``n`` comes from a per-prefix monotonic counter
(:class:`~app.models.number_sequence.NumberSequence`) that **only ever
increases** — so a number is issued exactly once for all time.

The number used to be ``instance.id``, but a hard purge of the highest row frees
its rowid (SQLite reuses it), which would re-issue a purged number to a new,
unrelated object. The counter avoids that: purging frees the rowid but never
rewinds the counter. Lookups resolve the stored ``*_number`` string column, so
the number no longer has to equal the primary key.

The raw id stays the internal/foreign-key currency; the number is the only
identifier accepted by the API and skill layers.

Every object except PROJECT itself belongs to a project, and its stored number
is the fully qualified ``<project_number>-<PREFIX>-<n>`` (e.g.
``P1-FEATURE-3``) — there is exactly one on-disk/on-screen spelling, no
separate "short" form assembled at display time. The qualifier is a single
path segment on purpose: these numbers ride inside URLs (e.g.
``/projects/P1/tasks/P1-TASK-7/claim``), and a ``/`` inside one would split
across two path segments. ``-`` was already the in-number separator, so a
qualified number has two dashes where a bare one has one — the qualifier is
recovered by checking a known prefix, not by any dash count. PROJECT's own
number (``P1``) has no qualifier since it *is* the qualifier, and it drops the
dash entirely (``P1``, not ``P-1``) so it stays a single alphanumeric token.
Callers may still type the bare ``FEATURE-3`` form (old habit, old records);
:func:`normalize_number` resolves that down to the one stored spelling before
any lookup.
"""


def allocate(prefix, project_id=None):
    """Hand out the next never-before-used number for ``(project_id, prefix)``
    and advance that counter. Monotonic: a value is returned at most once,
    ever, and each project's counter is independent (see docs/cn/design-evolution.md#storage-ui)."""
    from app.db.base import database_proxy
    from app.models.number_sequence import NumberSequence

    with database_proxy.atomic():
        sequence, _ = NumberSequence.get_or_create(
            prefix=prefix, project_id=project_id
        )
        value = sequence.next_value
        sequence.next_value = value + 1
        sequence.save()
    return value


def assign_number(instance, prefix, field_name, project_id=None):
    """Set ``field_name`` on ``instance`` to its fully qualified number and
    save. With a ``project_id`` the stored form is
    ``f"{project_number}-{prefix}-{n}"``; without one (PROJECT itself is the
    only caller that omits it) PROJECT gets its own bare, dash-less form
    ``f"{prefix}{n}"`` (``P1``, ``P2``, ...) since it has no project to
    qualify against and must stay a single token."""
    n = allocate(prefix, project_id)
    if project_id is not None:
        from app.repositories import project_repo

        project = project_repo.get(project_id)
        bare = f"{prefix}-{n}"
        full = f"{project.project_number}-{bare}" if project else bare
    else:
        full = f"{prefix}{n}"
    setattr(instance, field_name, full)
    instance.save()
    return instance


def normalize_number(project_id, number):
    """Resolve ``number`` to the one spelling stored on disk for objects
    belonging to ``project_id``.

    Accepts both the fully qualified ``P1-FEATURE-3`` and the bare
    ``FEATURE-3`` (older records and hand-typed references still use the bare
    form); both must resolve to the same row. The qualifier and the
    in-number separator are both ``-``, so "does it contain a dash" can't
    tell qualified from bare (bare numbers have one too) — the test is
    whether ``number`` already starts with *this project's own*
    ``f"{project_number}-"``."""
    if not number or not isinstance(number, str):
        return number
    from app.repositories import project_repo

    project = project_repo.get(project_id)
    if project is None or not project.project_number:
        return number
    qualifier = f"{project.project_number}-"
    if number.startswith(qualifier):
        return number
    return f"{qualifier}{number}"


def parse_number(number, prefix):
    """Extract the raw id from a ``f"{prefix}-{id}"`` number, whether it's
    bare or qualified as ``f"{project_number}-{prefix}-{id}"``. Both old
    (pre-refactor) and current stored spellings must keep working since
    migrations run this against historical data.

    Splits off the last ``-``-separated segment as the id and checks the
    remainder ends with ``prefix`` — bare ``FEATURE-12`` has remainder
    ``FEATURE`` (equal to prefix); qualified ``P1-FEATURE-12`` has remainder
    ``P1-FEATURE`` (ends with ``-{prefix}``). Either way the qualifier itself
    is never inspected, so this doesn't care what project it belongs to."""
    if not number or not isinstance(number, str):
        return None
    head, sep, tail = number.rpartition("-")
    if not sep:
        return None
    if head != prefix and not head.endswith(f"-{prefix}"):
        return None
    try:
        return int(tail)
    except ValueError:
        return None
