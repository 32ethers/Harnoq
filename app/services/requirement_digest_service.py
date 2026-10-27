"""Project-level view of the requirement, derived from the designs.

The requirement is mined at the design stage and stored per design in
``mined_requirements`` (see app/models/design_case.py). That is the right home
-- it keeps the requirement next to the solution it justifies -- but it leaves
no place to ask "what has the user asked for across this project", which needs
reading every design.

This module answers that by DERIVING the view rather than storing a second
copy: nothing here is persisted, so it can never drift from the designs the
way a duplicated record would. A requirement detail is edited in exactly one
place and this view follows.

Extraction keys off the MARK, not the container. ``mined_requirements`` is
free text and real projects write it as a bullet list, a two-column table, or
the three-column forcing table from the draft round -- all of which carry the
same 用户确认 / 我补的(已认可) / 待确认 marks. Keying off the mark reads all
three; keying off the container would silently drop whichever shape was not
anticipated, and a requirement view that quietly omits rows is worse than none.
"""
import re

from app.services import design_case_service, feature_service, lifecycle

CONFIRMED = "用户确认"
SUPPLIED = "我补的(已认可)"
PENDING = "待确认"
UNMARKED = "未标记"

MARKS = (CONFIRMED, SUPPLIED, PENDING)

#: Half- and full-width parentheses both occur in real content, and the mark is
#: usually bold. Matched in this order so the longer 我补的(已认可) wins over a
#: bare 我补的 prefix of it.
_MARK_PATTERNS = (
    (SUPPLIED, re.compile(r"\**\s*Agent-supplied\s*\(accepted\)\s*\**", re.IGNORECASE)),
    (CONFIRMED, re.compile(r"\**\s*User-confirmed\b\s*\**", re.IGNORECASE)),
    (PENDING, re.compile(r"\**\s*Pending confirmation\b\s*\**", re.IGNORECASE)),
    (SUPPLIED, re.compile(r"\**\s*我补的\s*[（(]\s*已认可\s*[)）]\s*\**")),
    (CONFIRMED, re.compile(r"\**\s*用户确认\s*\**")),
    (PENDING, re.compile(r"\**\s*待确认\s*\**")),
)

#: A markdown table delimiter row (|---|---|) carries no content.
_DELIMITER_ROW = re.compile(r"^\s*\|[\s|:-]+\|\s*$")


def _detect_mark(line):
    """Return (mark, pattern) for the mark that appears earliest in ``line``."""
    best = None
    for mark, pattern in _MARK_PATTERNS:
        match = pattern.search(line)
        if match is None:
            continue
        if best is None or match.start() < best[1]:
            best = (mark, match.start(), pattern)
    if best is None:
        return None, None
    return best[0], best[2]


def _clean_cell(text, pattern):
    text = pattern.sub("", text) if pattern is not None else text
    # Bullet markers and the leading label some formats prefix onto the cell.
    text = re.sub(r"^\s*[-*+]\s*", "", text)
    return text.strip(" \t|。;；")


def _extract_text(line, pattern):
    """The requirement text itself, from a bullet line or a table row."""
    if "|" in line:
        cells = [c for c in line.split("|") if c.strip()]
        for cell in cells:
            cleaned = _clean_cell(cell, pattern)
            if cleaned:
                return cleaned
        return ""
    return _clean_cell(line, pattern)


def extract_lines(mined_requirements):
    """Every marked requirement line, in document order.

    Lines carrying no mark are reported with ``UNMARKED`` so a design that
    skipped the marking is visible here instead of silently contributing
    nothing -- an empty section and an unmarked section are different problems.
    """
    results = []
    for raw in (mined_requirements or "").split("\n"):
        line = raw.strip()
        if not line or _DELIMITER_ROW.match(line):
            continue
        mark, pattern = _detect_mark(line)
        if mark is None:
            # Only content-bearing lines count: headings and table headers are
            # structure, not requirements.
            if line.startswith("#") or not re.search(r"[一-鿿A-Za-z0-9]", line):
                continue
            text = _extract_text(line, None)
            if not text or _is_table_header(line):
                continue
            results.append({"text": text, "mark": UNMARKED})
            continue
        text = _extract_text(line, pattern)
        if not text:
            continue
        results.append({"text": text, "mark": mark})
    return results


_HEADER_WORDS = ("材料", "来源", "强迫出的结构", "我打算这么做", "需求材料", "Material", "Source", "Required structure", "Proposed approach", "Requirement material")

#: Row labels the design method itself mandates (the forcing table's required
#: final row). Structure of the artifact, not a requirement someone stated --
#: these are Harnoq's own vocabulary, not any project's domain terms.
_STRUCTURAL_LABELS = ("冲突检查", "Conflict check")


def _is_table_header(line):
    if "|" not in line:
        return False
    cells = [c.strip() for c in line.split("|") if c.strip()]
    if not cells:
        return False
    if cells[0] in _STRUCTURAL_LABELS:
        return True
    return all(c in _HEADER_WORDS for c in cells)


def build(project_id):
    """The whole project's requirement, grouped by FEATURE-* then DESIGN-*."""
    features = feature_service.list_by_project(project_id)
    designs = design_case_service.list_by_project(project_id)

    by_feature = {}
    for design in designs:
        by_feature.setdefault(design.feature_id, []).append(design)

    counts = {CONFIRMED: 0, SUPPLIED: 0, PENDING: 0, UNMARKED: 0}

    # A CONFIRMED design with nothing recorded is what makes a zero ambiguous:
    # "nobody asked for anything here" and "somebody asked and it was never
    # written down" read identically on the page. Naming them separates the
    # two. This is reported rather than gated on purpose -- confirm-time
    # validation could only require the field to be NON-EMPTY, which buys a
    # line written to pass the check, not a requirement anyone recorded (the
    # design method warns against exactly that padding). A hole that is visible
    # where someone is already looking at requirements beats one that cannot be
    # reached but can be filled with anything.
    unrecorded = []

    def _design_rows(items):
        rows = []
        for design in items:
            lines = extract_lines(design.mined_requirements)
            for line in lines:
                counts[line["mark"]] = counts.get(line["mark"], 0) + 1
            row = {
                "design_id": design.id,
                "design_number": design.design_number,
                "status": design.status,
                "requirements": lines,
                "empty": not lines,
            }
            if row["empty"] and design.status == lifecycle.CONFIRMED:
                row["unrecorded"] = True
                unrecorded.append(design.design_number)
            rows.append(row)
        return rows

    feature_rows = []
    for feature in features:
        rows = _design_rows(by_feature.get(feature.id, []))
        feature_rows.append({
            "feature_number": feature.feature_number,
            "feature_name": feature.name,
            "orientation": feature.orientation,
            "designs": rows,
            "total": sum(len(row["requirements"]) for row in rows),
        })

    # A purged FEATURE-* leaves its designs with feature_id NULL (the FK is SET
    # NULL). Grouping only by live features would drop those designs and the
    # requirements inside them from a view whose whole claim is completeness --
    # the silent omission this view exists to prevent. Show them orphaned.
    orphans = _design_rows(by_feature.get(None, []))
    if orphans:
        feature_rows.append({
            "feature_number": None,
            "feature_name": "未归属功能",
            "orientation": None,
            "designs": orphans,
            "total": sum(len(row["requirements"]) for row in orphans),
        })

    return {
        "project_id": project_id,
        "counts": counts,
        "total": sum(counts.values()),
        "features": feature_rows,
        "unrecorded_design_numbers": unrecorded,
    }
