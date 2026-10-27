# evals/build — Build 侧提示词的行为评测

[中文](README.md) · [English](../../../en/evals/build/README.md)

上一级的 `evals/` 测的是 **Planning 侧**(`app/api/stage_methods.py`,规划 agent 的
分阶段方法)。这一层测的是 **Build 侧**(`app/services/agent_template_builder.py`,
下发给 coder/qa/manager 的角色提示词)。两者共用一个信念:

> **送达 ≠ 被执行。** `tests/test_agent_templates.py` 里的字符串断言只证明某句话
> 在提示词里,不证明 agent 照做了。

这一层存在的直接原因是一次真实事故:qa 跑完套件,报告「58 passed, 0 failed」——
58 是 pytest **collected** 的数,那棵树实跑 2 failed。同一次运行里,它还发现了两处
设计与实现不一致,老实写进了 evidence 散文,判定照样填 `pass`。两个都不是判断力
问题,是机械动作做错了,而机械动作恰好是可以机械评的。

## 它测什么 / 不测什么

**测**:判定档位放得对不对、报的测试数字是不是真的。
每个 fixture 里都埋了**已知**的偏差,埋在代码和设计之间,不在注释里 ——
只有把设计和代码对着读才找得到。

**不测**:evidence 写得好不好、理由讲得清不清楚。那不可机械判,也不该由写提示词
的人自评(自评是这条流水线里最不可靠的动作)。

换句话说:**这里绿了不代表 qa 好;这里红了一定是它漏了一件拿着设计的人漏不掉的事。**

## 两个 fixture 各测一半

| | E1 `coupon` 优惠券结算 | E2 `dispatch` 待办派单 |
|---|---|---|
| 判据形状 | `- ` 列表 | `### AC-N` 标题块 |
| 套件状态 | **红**(8 收集 / 6 通过 / 2 失败) | **全绿**(8/8) |
| 埋的偏差 | 门槛未达抛 `CouponRejected`,设计写的是 `ThresholdNotMet` | 无人可派抛 `NoHandlerAvailable`,设计写的是返回 `state="deferred"` |
| 另有未兑现 | 叠加上限差一、折后金额没有下限(两条都有失败用例) | 处理人下线不回队(**没有任何用例覆盖**) |
| 它在逼问 | 报出来的数字是不是真的 | 绿套件敢不敢盖章 |

E2 是关键的那个:套件真的是 8/8,而两条判据确实没兑现。一个跑完套件、看见全绿
就通过的 agent,在 E1 上可能侥幸过关,在 E2 上必然归零。

两个 fixture 的领域彼此无关,也和真实用户项目无关,用于降低通用提示词对单一
测试领域过拟合的风险。

## 怎么用

```bash
# 1. 造 fixture(写进临时库和临时工作目录,绝不碰 harnoq.db)
export HARNOQ_SQLITE_PATH=/tmp/harnoq-build-eval.db
export HARNOQ_EVAL_WORKDIR=/tmp/harnoq-build-evals
rm -f $HARNOQ_SQLITE_PATH
uv run python evals/build/make_fixtures.py

# 2. 导出 agent 实际收到的 qa 提示词(不要照抄 README 里的版本,会漂)
uv run python evals/build/dump_qa_prompt.py P1 > /tmp/qa_prompt.md

# 3. 让一个 agent 扮演 qa,按那份提示词验一个 fixture,产出 JSON 报告
#    (任何 agent 都行。指令见下)

# 4. 打分
uv run python evals/build/checker.py /tmp/qa_*.json
```

给 agent 的指令(照抄,`<...>` 替换):

> 你是 qa。完整读 `/tmp/qa_prompt.md`,那是你的角色约束,逐条照做。
> 待验的代码在 `<工作目录>/<fixture>`,对应的设计是 `<DESIGN-*>`(用
> `harnoq_task_get` 或直接读库取)。按提示词要求做验证,然后把结论写成 JSON
> 到 `<out.json>`,形状是:
> `{"recommendation": "...", "design_number": "...", "criteria_verdicts":
> [{"criterion": "...", "verdict": "...", "evidence": {"tests": [...],
> "code": [...], "note": "..."}}], "test_run": {"command": "...",
> "exit_code": ..., "collected": ..., "passed": ..., "failed": ...,
> "skipped": ...}}`。`evidence` 里 `tests`/`code` 可以是空列表 —— 提示词里那句
> 反锚定条款照做即可,不要为了不留空而硬塞一个不相干的测试或代码位置。
> 不要调用任何写工具,不要修改被验的代码。

## 验收线

2 个 fixture × 3 次 = 6 轮:

- 每项 ≥ 5/6
- `契约偏差判deviates`、`test_run数字属实`、`结论needs_fix` 必须 **6/6**

达不到就改提示词,**改完重跑全部 6 轮**,不允许只重跑失败那次。
(`契约偏差未判pass` 是 `契约偏差判deviates` 的软化版:两者一起看,能区分
「没看见」和「看见了但档位放错」——后者要改的提示词句子不一样。)

## 已知局限

1. **fixture 会泄。** 埋的偏差写在这个仓库里。让一个能读到本仓库的 agent 来跑,
   等于开卷。跑评测的 agent 只应拿到工作目录和设计,不应拿到 `evals/build/`。
2. **跑什么模型,测的就是什么模型。** 一份只在某个模型上成立的提示词本身就是脆的,
   所以换模型重跑是有意义的对照,不是噪声。
3. **只看最终 JSON**,不看 agent 中途的工具调用和推理。一个瞎猜猜对的和一个真跑
   了套件的,在这里同分。
4. **偏差只有两类**(错误通道不符、完全未实现)。真实事故里还有别的形状 ——
   比如替身把「不适用」当成功返回、能跑的例程没接进产品入口 —— 提示词里为它们
   专门写了一段,这里还没有对应的 fixture。
5. **结构化 evidence 和反锚定条款都没有对应的 fixture。** `checker.py` 只读
   `criterion`/`verdict`,从不看 `evidence` 里面装的是什么,所以它测不出
   qa 有没有交出 `{tests, code, note}` 形状,也测不出反锚定那条 —— 「上一次的
   证据是起点不是答案」—— 有没有被遵守。要测反锚定,fixture 需要带着一份
   *上一轮*就有、且现在会误导人的旧证据(比如指向一段本身没坏、但新加了一层
   挡在它前面的老代码),这两个 fixture 目前都没有这个设定。
6. **`skipped` 只测送达,没测判断。** 两个 fixture 的套件都不跳过任何用例,所以
   `test_run齐全`/`test_run数字属实` 只能验出 agent 有没有报这个数,验不出提示词
   里那句「唯一覆盖它的用例被跳过时不得据此判 pass」。真实事故正是这个形状:
   套件报 371 收集 / 369 通过 / 0 失败,两个性能用例被跳过,而其中一条判据的
   判定就靠它们。要测它得再造一个 fixture:某条判据的唯一覆盖用例默认 skip,
   且代码其实不满足它 —— 正解是 `fail`,失分动作是看见"0 失败"就判 `pass`。
