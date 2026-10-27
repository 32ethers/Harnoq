"""Split a DESIGN-*'s ``acceptance_criteria`` blob into individual criteria.

This exists for one caller: the qa-evidence gate in
``development_task_service.record_agent_run``. A qa run that claims a verdict
must submit one verdict per criterion, quoting each criterion's text — which is
only possible for an agent that actually holds the design. Splitting is
therefore not cosmetic: it decides how many verdicts are demanded and what they
must say.

Two things begin a criterion, and they are PEERS — neither takes precedence:

* a list marker — ``- ``/``* ``/``• `` bullets, or ``1.``/``1)``/``(1)``
  numbering;
* a markdown heading — ``#`` through ``######``.

Any other non-empty line continues whichever criterion started last (authors
wrap long Given/When/Then sentences). A blob with neither is one criterion per
non-empty line.

An earlier version made markers WIN over headings and dropped headings as
section labels. That rule was inferred from a design whose criterion count it
happened to preserve, and it was wrong: no design in live data uses headings
as labels over bullets — every heading is itself a criterion. In a design that
opens with a bulleted list and later appends heading blocks (criteria mined
after the first pass, which is the normal way this grows), the rule silently
swallowed every appended block: their bodies were glued onto the last bullet,
producing one unquotable concatenation and hiding five real criteria from the
gate. It survived review because the check applied was "did the criterion
COUNT change" — and a wrongness that preserves the count is invisible to that.
Check the split TEXT, not its length.

Storage carries the literal two-character ``\\n`` in several designs (see
app.core.agent_text): agents receive the repaired text, so they quote criteria
split on real breaks. Parsing applies the same repair first, otherwise a design
stored that way looks like a single criterion here while the qa agent saw five.
"""
import re

from app.core.agent_text import normalize_agent_text

#: Leading list markers to strip before comparing: "- ", "* ", "• ", "1. ",
#: "1) ", "(1) ". The text after the marker is the criterion itself.
_MARKER = re.compile(r"^\s*(?:[-*•]|\(?\d{1,3}[.)])\s*")
#: A markdown heading line: 1-6 "#" followed by whitespace and text.
_HEADING = re.compile(r"^\s*#{1,6}\s+")
_WHITESPACE = re.compile(r"[\s　]+")


def _is_item_start(line):
    return bool(_MARKER.match(line))


def _is_heading(line):
    return bool(_HEADING.match(line))


def _starts_criterion(line):
    return _is_item_start(line) or _is_heading(line)


def _strip_start(line):
    """Remove whichever leading marker or heading opened this criterion."""
    return _MARKER.sub("", _HEADING.sub("", line)).strip()


def split(text):
    """Return the criteria in ``text``, in order, marker/heading stripped.

    A list marker or a markdown heading begins a new criterion; any other line
    continues the criterion that started last, so a heading's body and a
    wrapped bullet are folded in the same way. Text before the first
    marker/heading, when the blob uses either, is dropped as a lead-in. A blob
    with neither is one criterion per non-empty line.
    """
    if not text or not text.strip():
        return []
    lines = [line for line in normalize_agent_text(text).splitlines() if line.strip()]
    if not any(_starts_criterion(line) for line in lines):
        return [line.strip() for line in lines]

    criteria = []
    for line in lines:
        if _starts_criterion(line):
            criteria.append(_strip_start(line))
        elif criteria:
            criteria[-1] = f"{criteria[-1]} {line.strip()}"
    return [criterion for criterion in criteria if criterion]


def normalize(text):
    """Collapse a criterion to its comparable form.

    Whitespace (including the full-width space that shows up in CJK prose) is
    collapsed and any leading list marker OR heading marker dropped, so a quote
    that survived a round trip through JSON still matches. Everything else is
    kept: matching is meant to require the agent to have the actual sentence,
    not a paraphrase of it.

    Dropping the heading marker here is not cosmetic — it is what makes
    heading-block designs verifiable at all. ``split`` strips ``###`` from the
    criteria it returns, while the qa agent is told to quote each criterion AS
    WRITTEN, which includes the ``###`` it can see in the blob. Without the
    same strip on this side, every quote from such a design missed by exactly
    those characters and no qa run against it could ever be recorded. Both
    spellings must land on the same key; a heading alone still does not, since
    it is not the whole criterion.
    """
    stripped = _HEADING.sub("", normalize_agent_text(text or ""))
    return _WHITESPACE.sub(" ", _MARKER.sub("", stripped)).strip()


def count(text):
    """How many criteria ``text`` holds."""
    return len(split(text))
