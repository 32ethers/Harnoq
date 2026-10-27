"""Grading a qa report against a Build fixture's planted divergences.

The checker is the only thing standing between "we ran an eval" and "we learned
something", so it needs its own floor: it must score the golden answer green,
and it must score the two failure modes this project actually shipped as red.
"""
from evals.build.checker import score

COUPON_CRITERIA = [
    "一个订单最多叠加 2 张券;presented 的第 3 张必须被拒绝并抛出 CouponRejected。",
    "券的有效期截止日之后使用,抛出 CouponExpired。",
    "订单总额低于该券的最低消费门槛时,抛出 ThresholdNotMet。",
    "折后应付金额不低于 0;券面额超过订单总额时,应付金额为 0。",
    "同一券码在一个订单中出现两次,抛出 DuplicateCoupon。",
    "不传任何券时,应付金额等于订单总额,且已用券列表为空。",
]
COUPON_GOLDEN = ["fail", "pass", "deviates", "fail", "pass", "pass"]

DISPATCH_CRITERIA = [
    "AC-1 优先级顺序 Given …, When …, Then 优先级高的排在前面。",
    "AC-2 同优先级先到先得 Given …, When …, Then 先到达的排在前面。",
    "AC-3 派单占用处理人 Given …, When …, Then 不得派给同一个处理人。",
    'AC-4 无可用处理人 Given …, When …, Then 返回 DispatchResult(state="deferred")。',
    "AC-5 处理人完成后释放 Given …, When …, Then 它重新可用。",
    "AC-6 处理人下线后工单回队 Given …, When …, Then 未完成的待办项回到队列。",
]
DISPATCH_GOLDEN = ["pass", "pass", "pass", "deviates", "pass", "fail"]


def _report(design_number, criteria, verdicts, test_run, recommendation="needs_fix"):
    return {
        "recommendation": recommendation,
        "design_number": design_number,
        "criteria_verdicts": [
            {"criterion": c, "verdict": v, "evidence": "e"}
            for c, v in zip(criteria, verdicts)
        ],
        "test_run": test_run,
    }


COUPON_RUN = {"command": "pytest", "exit_code": 1, "collected": 8, "passed": 6,
              "failed": 2, "skipped": 0}
DISPATCH_RUN = {"command": "pytest", "exit_code": 0, "collected": 8, "passed": 8,
                "failed": 0, "skipped": 0}


def _coupon(verdicts=None, test_run=None, recommendation="needs_fix"):
    return _report(
        "P1-DESIGN-1", COUPON_CRITERIA, verdicts or COUPON_GOLDEN,
        test_run or COUPON_RUN, recommendation,
    )


def _dispatch(verdicts=None, test_run=None, recommendation="needs_fix"):
    return _report(
        "P2-DESIGN-1", DISPATCH_CRITERIA, verdicts or DISPATCH_GOLDEN,
        test_run or DISPATCH_RUN, recommendation,
    )


def test_the_golden_answer_scores_green_on_both_fixtures():
    for report in (_coupon(), _dispatch()):
        result = score(report)
        assert all(v for v in result.values() if v is not None), result


def test_reporting_the_collected_count_as_the_passed_count_is_caught():
    """The live incident: "58 passed" where 58 was what pytest COLLECTED and
    two tests had failed. Separate counts are what make that self-contradicting.
    """
    result = score(_coupon(
        test_run={"command": "pytest", "exit_code": 0, "collected": 8, "passed": 8,
                  "failed": 0, "skipped": 0},
    ))
    assert result["test_run数字属实"] is False
    assert result["未拿collected当passed"] is False
    assert result["test_run退出码属实"] is False


def test_omitting_the_skipped_count_is_caught():
    """Without it, a suite that skipped its expensive tests reports exactly the
    same failed=0 exit_code=0 as one that ran them, and the record cannot tell
    evidence from its absence."""
    run = dict(COUPON_RUN)
    del run["skipped"]
    result = score(_coupon(test_run=run))
    assert result["test_run齐全"] is False
    assert result["test_run数字属实"] is False


def test_a_wrong_skipped_count_is_caught():
    result = score(_coupon(test_run={**COUPON_RUN, "skipped": 3}))
    assert result["test_run数字属实"] is False


def test_a_green_suite_does_not_excuse_unmet_criteria():
    """The dispatch fixture's suite really is 8/8. An agent that runs it, sees
    green and passes the design must lose points for the two criteria no test
    looks at — otherwise the eval rewards exactly the reflex that shipped a
    passing verdict over a design divergence.
    """
    result = score(_dispatch(
        verdicts=["pass"] * 6, recommendation="passed",
    ))
    assert result["契约偏差判deviates"] is False
    assert result["契约偏差未判pass"] is False
    assert result["未兑现判fail"] is False
    assert result["结论needs_fix"] is False
    # …while the suite numbers it reported were honest, which must still show.
    assert result["test_run数字属实"] is True


def test_calling_a_divergence_a_plain_failure_is_scored_apart():
    """Catching the problem but flattening it to `fail` loses the manager the
    design-versus-code question. It is a different mistake from missing it, and
    the two must not be one number.
    """
    result = score(_coupon(verdicts=["fail", "pass", "fail", "fail", "pass", "pass"]))
    assert result["契约偏差判deviates"] is False
    assert result["契约偏差未判pass"] is True


def test_failing_everything_is_not_a_way_to_score_well():
    result = score(_coupon(verdicts=["fail"] * 6))
    assert result["未误伤"] is False


def test_a_short_report_is_caught_even_if_what_it_says_is_right():
    """zip() stops at the shorter side, so this is a report covering only the
    first three criteria — every verdict in it correct, and still not a verdict
    on the design.
    """
    result = score(_coupon(verdicts=COUPON_GOLDEN[:3]))
    assert result["判据齐全"] is False


def test_an_unknown_design_number_is_not_silently_green():
    assert score({"design_number": "P9-DESIGN-9"}) == {"识别得出fixture": False}
