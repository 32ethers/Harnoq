"""The eval checker is an instrument; an instrument that rots reports nonsense.

A live run once scored a false FAIL because the draft wrote "uses relation" in
English and the pattern only matched the Chinese wording — the checker was
wrong, not the agent. These cases pin both directions so that never silently
happens again.
"""
from evals.checker import score, tables

_COMPLETE = """
## 草案
| 材料 | 强迫出的结构 | 我打算这么做 |
|---|---|---|
| 用户说的:甲 | 必须把乙分离出去 | 建立丙边界 |
| 我补的:丁 | 调度必须有界 | 上限设为 2 |
| 冲突检查 | 交叉检查以上各行 | 无冲突 |

### 接口表
| 操作 | 入参 | 返回 | 前置 | 后置 | 失败 |
|---|---|---|---|---|---|
| A.open | 路径 | Handle | 未打开 | 已打开 | 抛 OSError |
| A.close | 无 | 无 | 已打开 | 已关闭 | 抛错 |
| B.load | 无 | Snapshot | A 已就绪 | 不改变状态 | 抛错 |
| B.save | Snapshot | 新 Snapshot | 非空 | 原子替换 | 回滚 |
| C.submit | 参数 | Handle | 已启动 | 进入 queued | 拒绝 |

### uses relation
- A 使用 B;禁止 B 使用 A

### 决策表
| 决定 | 翻过来要重建什么 | 归属 |
|---|---|---|
| 采用甲方案 | 所有模块接入方式与测试基座 | 上收 fixed_stack |
| 并发上限 2 | 调度策略与并发测试 | 本设计 |
"""

_EMPTY_CELL = _COMPLETE.replace("| A.close | 无 | 无 |", "| A.close |  | 无 |")

_BARE = """
| 材料 | 强迫出的结构 | 我打算这么做 |
|---|---|---|
| 甲 | 乙 | 丙 |

### 模块
- A:隐藏甲。接口:read(request) -> result

### 验收标准
- Given 甲 When 乙 Then 丙
"""


def test_a_complete_draft_passes_every_check():
    assert all(score(_COMPLETE).values())


def test_english_uses_relation_is_recognised():
    # The false-FAIL that motivated this file.
    assert score(_COMPLETE)["uses关系"]
    assert score(_COMPLETE.replace("### uses relation", "### 调用方向"))["uses关系"]


def test_a_bare_draft_fails_the_checks_that_matter():
    s = score(_BARE)
    for missing in ("来源标记", "冲突检查末行", "接口六列表", "决策三列表",
                    "归属含上收", "uses关系", "无Round5内容"):
        assert not s[missing], missing


def test_an_empty_interface_cell_is_caught():
    # 无 counts as filled; blank does not — that distinction is the whole point
    # of the fixed columns.
    assert not score(_EMPTY_CELL)["接口无空格"]
    assert score(_COMPLETE)["接口无空格"]


def test_conflict_check_must_be_a_row_not_a_paragraph():
    moved = _COMPLETE.replace("| 冲突检查 | 交叉检查以上各行 | 无冲突 |", "")
    moved += "\n### 冲突检查\n交叉检查以上各行:无冲突。\n"
    assert not score(moved)["冲突检查末行"]


def test_table_parser_reads_header_and_rows():
    found = tables(_COMPLETE)
    headers = [" ".join(h) for h, _ in found]
    assert any("材料" in h for h in headers)
    assert any("操作" in h and "失败" in h for h in headers)


def test_an_unusable_input_is_rejected_not_scored(tmp_path, capsys):
    """An empty file scores 1/11 — which reads as an agent catastrophe and is not.

    A real run scored a codex draft at 1/11 and the conclusion looked like
    "codex ignores the whole method". The file was zero bytes: 无Round5内容
    passes vacuously when there is nothing to violate. A number that looks like
    a verdict must not be printed for an input that cannot carry one.
    """
    from evals.checker import main

    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    assert main([str(empty)]) == 1
    assert "INVALID" in capsys.readouterr().out


def test_terminal_rendered_tables_are_rejected(tmp_path, capsys):
    from evals.checker import main

    rendered = tmp_path / "rendered.md"
    rendered.write_text(
        "## 草案\n  材料      强迫出的结构      我打算这么做\n"
        + "  " + "━" * 40 + "\n" + "详细内容" * 400,
        encoding="utf-8",
    )
    assert main([str(rendered)]) == 1
    assert "画框" in capsys.readouterr().out


_NUMBERED = _COMPLETE.replace(
    "| 材料 | 强迫出的结构 | 我打算这么做 |", "| # | 材料 | 强迫出的结构 | 我打算这么做 |"
).replace("|---|---|---|\n| 用户说的", "|---|---|---|---|\n| 1 | 用户说的"
).replace("| 我补的:丁", "| 2 | 我补的:丁"
).replace("| 冲突检查", "| 3 | 冲突检查")


def test_a_leading_row_number_column_does_not_hide_the_conflict_row():
    """A draft numbered its rows; the positional check called that a miss.

    The 冲突检查 row was present and well-formed, but sat in column 2 behind a
    row-number column, and the checker only looked at column 1 — it reported a
    regression that did not exist. The column is found by name now.
    """
    s = score(_NUMBERED)
    assert s["材料三列表"]
    assert s["冲突检查末行"]


_NUMBERED_UNNUMBERED_CONFLICT = _NUMBERED.replace(
    "| 3 | 冲突检查", "| 冲突检查"
)


def test_conflict_row_found_whether_or_not_it_carries_the_row_number():
    """Two live drafts numbered their rows and placed the label differently.

    One put 冲突检查 in the 材料 column behind the number; the other omitted the
    number on that row, putting the label back in column 1. Each position broke
    the check that only looked at the other, and each time it looked like a
    regression in the prompt. Both are accepted; a conflict pass written as a
    paragraph still fails, because it produces no row at all.
    """
    assert score(_NUMBERED)["冲突检查末行"]
    assert score(_NUMBERED_UNNUMBERED_CONFLICT)["冲突检查末行"]
    paragraph = _NUMBERED.replace("| 3 | 冲突检查 | 交叉检查以上各行 | 无冲突 |", "")
    paragraph += "\n### 冲突检查\n交叉检查:无冲突。\n"
    assert not score(paragraph)["冲突检查末行"]
