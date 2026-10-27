# evals — 阶段方法的行为评测

[中文](README.md) · [English](../../en/evals/README.md)

`app/api/stage_methods.py` 是提示词即代码:改一个字,下游 agent 的行为就变。
但它没有测试——`tests/test_skills.py` 里那些字符串断言**只验证规则被送达**,
而这个项目反复付出代价学到的一课是:**送达 ≠ 被执行**。

于是每次改提示词都只能靠"跑一次真实会话 + 人读产出"来判断好坏。样本量 1、
模型配置还在变、读的人越严格越能挑出毛病——结果是连续多轮"改好了又改坏了",
分不清是改动的功劳还是掷骰子。

这个目录是那件事的替代品。

## 它测什么 / 不测什么

**测**:方法要求的**产物在不在**。三列材料表、冲突检查行、六列接口表、
uses 关系、三列决策表、没有把 Round-5 的字段提前写进 Round-3……这些是
机械可判的,而且恰好是实际反复丢失的东西。

**不测**:设计得好不好。接口划得对不对、方案选得聪明不聪明——不可机械判,
也不该由写提示词的人自己打分(自评正是这套流水线里最不可靠的动作)。那部分
仍然靠人看。

换句话说:**这里绿了不代表设计是好的;这里红了一定是提示词漏了东西。**

## 怎么用

```bash
# 1. 造 fixture(写进临时库,绝不碰 harnoq.db)
export HARNOQ_SQLITE_PATH=/tmp/harnoq-eval.db
rm -f $HARNOQ_SQLITE_PATH
uv run python evals/make_fixtures.py

# 2. 导出 agent 实际会看到的 payload
uv run python evals/dump_context.py P1 > /tmp/ctx_P1.json   # 排课系统
uv run python evals/dump_context.py P2 > /tmp/ctx_P2.json   # 日志聚合与告警

# 3. 让一个 agent 扮演规划侧,读 payload,产出 Round 3
#    (任何 agent 都行:subagent、codex、OpenWebUI。提示词见下)
#    产出存成 .md

# 4. 打分
uv run python evals/checker.py /tmp/out_*.md
```

给 agent 的指令(照抄):

> 你扮演 Harnoq 的规划 agent。用户刚说「开始设计第一个 Feature」。
> 完整读 `<ctx.json>`,其中 `active_stage_method` 是你当前阶段的约束性方法,
> 必须逐条照做。按其中 **Round 3** 的要求,为 `<FEATURE-*>` 产出这一轮该
> 发给用户的内容。不要调用任何写工具。用中文 markdown,表格用标准管道表格。
> 把完整内容写到 `<out.md>`。

## 两个 fixture

领域和形状都不重叠,单个 fixture 上调好的提示词很容易在另一个上露馅:

| | P1 排课系统 | P2 日志聚合与告警 |
|---|---|---|
| 形态 | 单机桌面、有界面 | 无人值守服务、**没有界面** |
| 核心压力 | 约束求解、冲突判定 | 吞吐、乱序到达、去重 |
| 有无 UI Foundation | 有 | **无**(检验方法在无页面项目上的分支) |

两个都**不是**用户的真实项目。采用不同领域与形态的 fixture,是为了降低通用
提示词对单一测试领域过拟合的风险。

## 验收线

2 个 fixture × 3 次 = 6 轮:

- 每项 ≥ 5/6
- `无Round5内容` 必须 6/6

达不到就改提示词,**改完重跑全部 6 轮**,不允许只重跑失败那次。

## 已知局限

1. **跑什么模型,测的就是什么模型。** 一份只在某个模型/某个 effort 上成立的
   提示词本身就是脆的,所以换模型重跑是有意义的对照,而不是噪声。
2. **只看最终文本**,不看 agent 中途的工具调用和推理。
3. **核对器认的是 markdown 管道表格**。有些客户端(如 codex TUI)会把表格
   渲染成画框字符——那是渲染,原始输出仍是管道表格;但若某个 agent 真的输出
   画框字符,核对器会误判,需要先转换。
