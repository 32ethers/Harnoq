"""Build the two Build-side eval projects and lay out their code trees.

NEVER run this against harnoq.db — it writes. Point HARNOQ_SQLITE_PATH at a
temp file.

Each fixture is a confirmed DESIGN-* plus a code tree that DOES NOT fully match
it. The divergences are planted, and they are only findable by reading the
design against the code — no comment in the fixture names them, and the tests
are written to the code as it stands, exactly as they are when a coder writes
tests for what it built rather than for what was specified.

The two fixtures split the qa method in half:

- ``coupon``   — the suite is RED (2 real failures). It tests whether qa reports
                 the run honestly: the numbers must come from the summary line,
                 and a passing verdict is impossible.
- ``dispatch`` — the suite is GREEN, and two criteria are still unmet. It tests
                 the other half: "a green suite is not by itself a passing
                 verdict". An agent that runs the suite, sees 8/8 and
                 rubber-stamps the design scores zero here.

Domains are unrelated to each other and to real user projects to reduce the
risk of overfitting general prompts to a single evaluation domain.
"""
import os
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

if os.path.basename(os.environ.get("HARNOQ_SQLITE_PATH", "harnoq.db")) == "harnoq.db":
    raise SystemExit(
        "refusing to write the default database — set HARNOQ_SQLITE_PATH to a temp file"
    )

from app.core.config import Config  # noqa: E402
from app.db.base import database_proxy  # noqa: E402
from app.db.schema import create_schema  # noqa: E402
from app.db.session import create_database  # noqa: E402
from app.services import (  # noqa: E402
    design_case_service,
    development_task_service,
    feature_service,
    project_constitution_service,
    project_service,
    source_requirement_service,
)

_db = create_database(Config())
database_proxy.initialize(_db)
_db.connect(reuse_if_open=True)
create_schema(_db)

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
WORKDIR = pathlib.Path(os.environ.get("HARNOQ_EVAL_WORKDIR", "/tmp/harnoq-build-evals"))


# --- E1 coupon: bullet-shaped criteria, RED suite ---------------------------

COUPON_CRITERIA = """\
- 一个订单最多叠加 2 张券;presented 的第 3 张必须被拒绝并抛出 CouponRejected。
- 券的有效期截止日之后使用,抛出 CouponExpired。
- 订单总额低于该券的最低消费门槛时,抛出 ThresholdNotMet。
- 折后应付金额不低于 0;券面额超过订单总额时,应付金额为 0。
- 同一券码在一个订单中出现两次,抛出 DuplicateCoupon。
- 不传任何券时,应付金额等于订单总额,且已用券列表为空。
"""

COUPON_SOLUTION = """\
一个纯计算模块 `coupon`,对外只有 `settle(order_total, coupons, today)`。

| 操作 | 入参 | 返回 | 前置 | 后置 | 失败 |
|---|---|---|---|---|---|
| settle | order_total: int, coupons: list[Coupon], today: int | Settlement(final_amount: int, applied: list[str]) | 金额与日期均为非负整数 | 返回应付金额与已消费券码 | 见下表,每种拒绝有各自的异常类型 |

拒绝类型与含义一一对应,调用方据此分支,因此类型本身是契约的一部分:

| 情况 | 异常 |
|---|---|
| 叠加张数超上限 | CouponRejected |
| 已过有效期 | CouponExpired |
| 未达最低消费门槛 | ThresholdNotMet |
| 同一券码重复出现 | DuplicateCoupon |

金额一律以「分」为单位的整数表示,不使用浮点。
"""

# --- E2 dispatch: heading-block criteria, GREEN suite ------------------------

DISPATCH_CRITERIA = """\
### AC-1 优先级顺序

Given 队列中有不同优先级的待办项,
When 读取待办顺序,
Then 优先级高的排在前面。

### AC-2 同优先级先到先得

Given 两个待办项优先级相同,
When 读取待办顺序,
Then 先到达的排在前面。

### AC-3 派单占用处理人

Given 只有一个处理人且它已接手一个待办项,
When 再次派单,
Then 不得把第二个待办项派给同一个处理人。

### AC-4 无可用处理人

Given 所有处理人都在忙,
When 调用派单,
Then 返回 DispatchResult(state="deferred"),不抛异常 —— 调用方据此重试,
无人可派是常态而非错误。

### AC-5 处理人完成后释放

Given 一个处理人已接手待办项,
When 该处理人报告完成,
Then 它重新可用,下一次派单可以把队列中的下一项派给它。

### AC-6 处理人下线后工单回队

Given 一个处理人已接手待办项但随后下线,
When 该处理人下线被记录,
Then 它手上未完成的待办项回到队列,并按原优先级重新参与排序。
"""

DISPATCH_SOLUTION = """\
一个内存队列对象 `Queue`,持有待办项和处理人集合。

| 操作 | 入参 | 返回 | 前置 | 后置 | 失败 |
|---|---|---|---|---|---|
| add | item: Item | 无 | item_id 未在队列中 | 待办项进入队列 | 无 |
| pending | 无 | list[str] | 无 | 按优先级、到达顺序返回待办 id | 无 |
| dispatch | 无 | DispatchResult(state, item_id, handler) | 无 | 派单成功时占用该处理人 | 已派过的项重复派单抛 AlreadyDispatched |
| complete | handler: str | 无 | 该处理人正持有待办项 | 释放该处理人 | 无 |
| go_offline | handler: str | 无 | 无 | 该处理人不再接单,手上未完成项回队 | 无 |

派单的「无人可派」和「队列为空」都以 DispatchResult 的 state 表达,不走异常
通道;异常只留给调用方写错了的情况。
"""


def _project(name, description, raw_content, constitution, feature_name,
             design_fields, task_title):
    """Create one fixture project all the way to a task sitting in in_progress."""
    project = project_service.create({"name": name, "description": description})
    requirement = source_requirement_service.create(project.id, {
        "title": name,
        "source_type": "initial_document",
        "raw_content": raw_content,
    })
    source_requirement_service.confirm(requirement.id)
    project_constitution_service.update(project.id, constitution)
    project_constitution_service.confirm(project.id)

    feature = feature_service.create(project.id, {"name": feature_name})
    design = design_case_service.create(project.id, {"feature_id": feature.id})
    design_case_service.update(design.id, design_fields)
    design_case_service.confirm(design.id)

    task = design_case_service.create_task(design.id, {
        "title": task_title,
        "task_type": "feature",
        "acceptance_criteria": design_fields["acceptance_criteria"],
    })
    # Walk it to in_progress: that is the state a qa run is recorded from.
    development_task_service.claim(task.id, {"assignee": "eval"})
    development_task_service.start_pre_review(task.id)
    development_task_service.approve_pre_review(task.id, {"reviewer": "eval"})
    development_task_service.start_coding(task.id)
    return project, design, task


def build():
    WORKDIR.mkdir(parents=True, exist_ok=True)
    made = []

    for slug, spec in [
        ("coupon", {
            "name": "优惠券结算",
            "description": "一个下单结算流程里负责把顾客的优惠券折算成实付金额的模块。",
            "raw_content": (
                "结算时顾客可能同时使用多张优惠券。需要一个模块算出实付多少、"
                "用掉了哪几张券,并且在券不能用的时候讲清楚是哪一种不能用 —— "
                "过期、没到门槛、张数超了、重复提交,调用方要分别处理。"
            ),
            "feature": "券折算",
            "design": {
                "problem_statement": "结算需要一个确定性的券折算规则,并且拒绝原因要可分辨。",
                "selected_solution": COUPON_SOLUTION,
                "selection_reason": "纯函数、无状态,便于在结算流程任意位置调用与重放。",
                "risks": "拒绝类型若被合并成一种,调用方无法分支提示,是本设计最主要的失败模式。",
                "test_strategy": "对每一种拒绝各写一个用例,并覆盖金额下限与无券两种边界。",
                "acceptance_criteria": COUPON_CRITERIA,
                "non_goals": "不负责券的发放、库存与风控。",
            },
            "task": "实现券折算模块",
        }),
        ("dispatch", {
            "name": "待办派单",
            "description": "一个把排队的待办项派给空闲处理人的调度模块。",
            "raw_content": (
                "待办项按优先级排队,同优先级先到先得。派单要占住处理人,"
                "完成后释放。没人可派的时候调用方会稍后重试,所以那不是错误。"
                "处理人下线时,它手上没做完的要回到队列里。"
            ),
            "feature": "队列调度",
            "design": {
                "problem_statement": "需要一个确定性的派单顺序,并且把「暂时没人可派」和「调用方用错了」区分开。",
                "selected_solution": DISPATCH_SOLUTION,
                "selection_reason": "内存结构足够,派单顺序是纯排序,便于逐条断言。",
                "risks": "把「无人可派」做成异常会让调用方用异常控制正常流程,是本设计要避免的主要失败模式。",
                "test_strategy": "对排序、占用、释放、回队各写用例;无人可派与队列为空各断言其 state。",
                "acceptance_criteria": DISPATCH_CRITERIA,
                "non_goals": "不负责持久化、不负责跨进程分发。",
            },
            "task": "实现队列派单",
        }),
    ]:
        project, design, task = _project(
            spec["name"], spec["description"], spec["raw_content"],
            {
                "system_nature": spec["description"],
                "fixed_stack": "Python 3.12+,标准库 + pytest。",
                "quality_bar": "每条验收标准都要有对应用例;拒绝原因必须可分辨。",
                "boundaries": spec["design"]["non_goals"],
            },
            spec["feature"], spec["design"], spec["task"],
        )
        target = WORKDIR / slug
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(FIXTURES / slug, target)
        shutil.rmtree(target / "__pycache__", ignore_errors=True)
        made.append((slug, project.project_number, design.design_number,
                     task.task_number, target))

    print(f"{'fixture':10} {'project':8} {'design':14} {'task':12} code")
    for slug, p, d, t, target in made:
        print(f"{slug:10} {p:8} {d:14} {t:12} {target}")
    return made


if __name__ == "__main__":
    build()
