"""Parsing the constitution's ``nfr_baseline``, and one distinction it carries.

Pure text handling, no repositories and no other services — the same shape as
``acceptance_criteria``. It sits below ``nfr_coverage_service`` (which derives
coverage) and below ``design_case_service`` (which requires an answer at
confirmation), so both can read the baseline without importing each other.

THE DISTINCTION
---------------

A baseline row can be either of two things, and the failure this module exists
to fix is that they used to look identical:

* a COMMITMENT — a number the project promises to hit. Some design must take
  responsibility for proving it, and proving it usually costs something real:
  a fixture at the stated scale, an instrumented run, a measurement.
* a DIRECTION — a stance that constrains how designs are chosen without
  asserting any number. Nothing can be measured against it, so demanding an
  owner for it produces a fake claim, and demanding a test produces a fake
  test.

In a live project the baseline stated a capacity figure the user later
described as an offhand number, in a table cell that looked exactly like a
hard promise. Three separate designs each copied it into a MUST acceptance
criterion, and each one then needed a fixture at that scale to discharge it.
The same table already held a row reading "no fixed target" — the author was
already making this distinction, in prose, because no field carried it.

Strength is read from a ``strength`` column when the table has one. Anything
unrecognised, and every row of a table without the column, is a COMMITMENT:
the failure worth avoiding is a promise nobody owns, so the safe default is
the one that keeps asking. A mistyped value is visible rather than silent —
the row simply goes on demanding an owner in the coverage view.
"""
import re

COMMITMENT = "commitment"
DIRECTION = "direction"

_WHITESPACE = re.compile(r"[\s　]+")
#: A markdown table separator row: | --- | :--: | etc.
_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

#: Header cells, by the column they name. A row whose non-empty cells are all
#: header words labels the table rather than stating an NFR.
_HEADERS = {
    "metric": {"metric", "指标"},
    "target": {"target", "目标"},
    "strength": {"strength", "性质", "分档", "约束力"},
    "applies_to": {"applies to", "applies_to", "适用于", "适用范围"},
}
_HEADER_WORDS = frozenset().union(*_HEADERS.values())

#: Spellings of DIRECTION. Closed and matched whole-cell, case-folded. Kept
#: short on purpose: a value nobody recognises falls back to COMMITMENT, which
#: is the answer that keeps the question open.
_DIRECTION_WORDS = frozenset({
    "direction", "方向", "导向", "aspiration", "guidance", "no fixed target",
    "无硬指标", "非承诺",
})

#: Spellings of "this design verifies no NFR row". Confirming a design demands
#: an answer once the constitution states a commitment, so the negative answer
#: needs somewhere to be written; without this it would land in
#: ``unmatched_claims`` and read as a design claiming something the
#: constitution does not contain — the opposite of what it says.
_NO_CLAIM = frozenset({"none", "n/a", "na", "-", "无", "不涉及", "不验证"})

#: A negative answer, optionally going on to explain itself. Requiring an
#: answer produced prose, and prose explaining WHICH rows it does not prove
#: names those rows — which the substring matching below then read as claims.
#: A live design wrote "无——本设计不证明 nfr_baseline 中的任何指标(A、B、C、D
#: 均由后端设计验证)" and was recorded as the owner of four commitments, taking
#: the coverage view from four honest holes to none. So the token is matched at
#: the START of the line and must be followed by end-of-line or a separator: a
#: denial stays a denial however much it explains, while a line that merely
#: begins with one of these words as part of a longer phrase is still a claim.
_NEGATIVE = re.compile(
    r"^(?:%s)(?:\s*[—\-–:：,，。.、]|\s*$)" % "|".join(
        re.escape(word) for word in sorted(_NO_CLAIM, key=len, reverse=True)
    ),
    re.IGNORECASE,
)


def norm(text):
    return _WHITESPACE.sub(" ", (text or "")).strip().casefold()


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_header(cells):
    return bool(cells) and all(norm(c) in _HEADER_WORDS for c in cells if c)


def _column_map(cells):
    """Header cells -> {column name: index}, for the columns it names."""
    found = {}
    for index, cell in enumerate(cells):
        for column, spellings in _HEADERS.items():
            if norm(cell) in spellings and column not in found:
                found[column] = index
    return found


#: Where each column sits when the table has no recognisable header row. This
#: is the shape every constitution written before ``strength`` existed uses.
_POSITIONAL = {"metric": 0, "target": 1, "applies_to": 2}


def _strength(value):
    return DIRECTION if norm(value) in _DIRECTION_WORDS else COMMITMENT


def extract_nfr_rows(nfr_baseline):
    """Return the NFR rows in a constitution's ``nfr_baseline``.

    Real constitutions write this as a markdown table (metric | target |
    applies to, optionally a strength column). A blob with no table falls back
    to one row per non-empty line, whose whole text is the metric — the same
    choice ``acceptance_criteria`` makes, and for the same reason: guessing at
    an unanticipated shape drops rows silently, and a coverage view that omits
    rows is worse than none.

    Column positions come from the header row when it names them, so adding
    ``strength`` to an existing table does not depend on where it is put. A
    table with no recognisable header keeps the historical fixed order.
    """
    text = nfr_baseline or ""
    if not text.strip():
        return []

    lines = [line for line in text.splitlines() if line.strip()]
    table = [line for line in lines if line.strip().startswith("|")]
    if not table:
        return [
            {"metric": line.strip(), "target": "", "applies_to": "",
             "strength": COMMITMENT}
            for line in lines
        ]

    columns = _POSITIONAL
    rows = []
    for line in table:
        if _SEPARATOR.match(line):
            continue
        cells = _cells(line)
        if not cells or not cells[0]:
            continue
        if _is_header(cells):
            named = _column_map(cells)
            if "metric" in named:
                columns = named
            continue

        def cell(column):
            index = columns.get(column)
            return cells[index] if index is not None and index < len(cells) else ""

        rows.append({
            "metric": cell("metric"),
            "target": cell("target"),
            "applies_to": cell("applies_to"),
            "strength": _strength(cell("strength")),
        })
    return rows


def has_commitment(nfr_baseline):
    """Whether the baseline promises anything a design could be asked to prove."""
    return any(r["strength"] == COMMITMENT for r in extract_nfr_rows(nfr_baseline))


def extract_claims(verifies_nfr):
    """Return the non-empty lines of a design's ``verifies_nfr``, marker stripped.

    An explicit "none" yields no claims — same as blank, and it stays a denial
    however much it goes on to explain itself (see ``_NEGATIVE``). The
    difference between "none" and blank is enforced at confirmation, not here:
    this module reports what a design covers, and a design that verifies
    nothing covers nothing either way.
    """
    claims = []
    for raw in (verifies_nfr or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\(?\d{1,3}[.)])\s*", "", raw).strip()
        if line and not _NEGATIVE.match(norm(line)):
            claims.append(line)
    return claims
