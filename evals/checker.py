"""Score a Round-3 draft against the artifacts the design method mandates.

Presence and shape only — see docs/cn/evals/README.md for why quality is deliberately
out of scope. Import ``score`` for use in tests, or run this file over one or
more .md files.
"""
import json
import re
import sys


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def tables(text):
    """Return [(header_cells, [row_cells, ...]), ...] for markdown pipe tables."""
    out, lines = [], text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and re.match(
            r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]
        ):
            header, rows, i = _cells(lines[i]), [], i + 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append((header, rows))
        else:
            i += 1
    return out


def score(text):
    """Return {check_name: bool}. All True means every required artifact is present."""
    found = tables(text)

    def with_header(*needles):
        return [t for t in found if all(n in " ".join(t[0]) for n in needles)]

    r = {}

    material = with_header("材料", "强迫", "我打算")
    r["材料三列表"] = bool(material)
    r["来源标记"] = any(
        "用户说的" in " ".join(row) or "我补的" in " ".join(row)
        for t in material for row in t[1]
    )
    # The conflict pass must BE a row of that table. As a paragraph beside it,
    # it went missing in live runs even while the table itself landed.
    # Where the label sits varies legitimately between drafts: some number
    # their rows (材料 lands in column 2), and a numbered table's conflict row
    # often omits the number (材料 lands back in column 1). Checking only one
    # position reported a regression that did not exist — twice, in opposite
    # directions. Accept either, which still fails a conflict pass written as a
    # paragraph, since that produces no row at all.
    def _label_cells(header, row):
        idx = next((i for i, h in enumerate(header) if "材料" in h), None)
        cells = row[:1]
        if idx is not None and idx < len(row):
            cells = cells + [row[idx]]
        return cells

    r["冲突检查末行"] = any(
        any("冲突检查" in c for c in _label_cells(t[0], row))
        for t in material for row in t[1]
    )

    iface = with_header("操作", "入参", "返回", "前置", "后置", "失败")
    r["接口六列表"] = bool(iface)
    r["接口行数>=5"] = sum(len(t[1]) for t in iface) >= 5
    # An empty cell is the failure mode the fixed columns exist to expose; the
    # method says to write 无 rather than leave one blank.
    r["接口无空格"] = bool(iface) and all(
        all(c.strip() for c in row) for t in iface for row in t[1]
    )

    decision = with_header("决定", "归属")
    r["决策三列表"] = bool(decision)
    r["决策带代价"] = any(
        len(row) >= 2 and len(row[1]) > 4 for t in decision for row in t[1]
    )
    r["归属含上收"] = any(
        "上收" in " ".join(row) for t in decision for row in t[1]
    )

    r["uses关系"] = bool(re.search(
        r"使用关系|调用关系|调用方向|uses[\s_-]*(relation|关系)", text, re.I))
    # acceptance_criteria / risks / test_strategy are Round-5 fields; drafting
    # them in Round 3 doubles a document the user is asked to strike through.
    r["无Round5内容"] = re.search(
        r"^#+\s*.*(验收标准|测试策略|风险与)", text, re.M) is None
    return r


#: Below this a file cannot be a Round-3 draft; scoring it produces a number
#: that looks like a verdict on the agent when it is a verdict on the file.
#: An empty file scores 1/11 — 无Round5内容 passes vacuously — which reads as a
#: catastrophic agent failure and is not one.
MIN_CHARS = 500


def main(paths):
    worst = 0
    for path in paths:
        text = open(path, encoding="utf-8").read()
        if len(text.strip()) < MIN_CHARS:
            print(f"INVALID     {path}")
            print(f"      输入只有 {len(text.strip())} 字符,不可能是一份草案 —— "
                  "检查是否保存成功、是否存错路径。未打分。")
            worst = max(worst, 1)
            continue
        if "|" not in text and re.search(r"[━─│┃]", text):
            print(f"INVALID     {path}")
            print("      看起来是终端渲染后的画框表格,不是原始 markdown。"
                  "请保存 agent 的原始输出(管道表格)。未打分。")
            worst = max(worst, 1)
            continue
        s = score(text)
        ok = sum(s.values())
        worst = max(worst, len(s) - ok)
        missing = [k for k, v in s.items() if not v]
        print(f"{'PASS' if not missing else 'FAIL'} {ok}/{len(s)}  {path}")
        if missing:
            print("      缺:", ", ".join(missing))
    return 1 if worst else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit("usage: checker.py <draft.md> [draft.md ...]")
    if args[0] == "--json":
        print(json.dumps(score(open(args[1], encoding="utf-8").read()),
                         ensure_ascii=False, indent=1))
        raise SystemExit(0)
    raise SystemExit(main(args))
