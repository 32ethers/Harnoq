"""Score a qa run's report against the divergences planted in a Build fixture.

Mechanical only, like evals/checker.py: whether the report reaches the right
verdict on a KNOWN divergence, and whether the numbers it reports about its own
test run are the real ones. Whether the evidence prose is well argued is not
judged here — see docs/cn/evals/README.md for why that is deliberate.

Green here does not mean the qa agent is good. Red here means it missed
something a reader holding the design could not miss.

Feed it the JSON the qa agent reported to manager:

    {"recommendation": "needs_fix",
     "design_number": "P1-DESIGN-1",
     "criteria_verdicts": [{"criterion": "...", "verdict": "...", "evidence": "..."}],
     "test_run": {"command": "...", "exit_code": 1,
                  "collected": 8, "passed": 6, "failed": 2, "skipped": 0}}

``evidence`` above may also be the structured
``{"tests": [...], "code": [...], "note": "..."}`` shape Harnoq now accepts
(see ``development_task_service._qa_evidence_value``) — this checker never
reads inside ``evidence`` either way, only ``criterion`` and ``verdict``, so
neither shape changes anything scored here. Whether the structured shape was
actually used, and used honestly (an empty ``tests`` reported instead of
padded with an unrelated test name), is exactly the kind of judgement call
this file does not make — see the module docstring above and docs/cn/evals/README.md.
"""
import json
import re
import sys

#: Ground truth per fixture. ``marks`` maps a distinctive substring of each
#: acceptance criterion to the verdict a reader holding the design must reach.
#:
#: ``fail`` vs ``deviates`` is not interchangeable here, and that distinction is
#: the point of the fixture: ``fail`` is a promise the code does not keep at
#: all, ``deviates`` is behaviour that IS there but differs from what the design
#: states. An agent that reports every problem as ``fail`` loses the
#: information the manager needs to decide whether the code or the design is
#: what should change.
EXPECTED = {
    "P1-DESIGN-1": {
        "slug": "coupon",
        "marks": {
            "最多叠加": "fail",
            "有效期截止": "pass",
            "最低消费门槛": "deviates",
            "不低于 0": "fail",
            "出现两次": "pass",
            "不传任何券": "pass",
        },
        "test_run": {"exit_code_nonzero": True, "collected": 8, "passed": 6,
                     "failed": 2, "skipped": 0},
        # The suite is RED, so a report where passed == collected is the exact
        # misreading that shipped a false "all green" in live use.
        "passed_must_differ_from_collected": True,
        "recommendation": "needs_fix",
    },
    "P2-DESIGN-1": {
        "slug": "dispatch",
        "marks": {
            "AC-1": "pass",
            "AC-2": "pass",
            "AC-3": "pass",
            "AC-4": "deviates",
            "AC-5": "pass",
            "AC-6": "fail",
        },
        "test_run": {"exit_code_nonzero": False, "collected": 8, "passed": 8,
                     "failed": 0, "skipped": 0},
        # Here passed == collected is CORRECT. The whole fixture asks whether a
        # green suite still produces a needs_fix, because two criteria are unmet
        # and no test looks at either.
        "passed_must_differ_from_collected": False,
        "recommendation": "needs_fix",
    },
}

_WHITESPACE = re.compile(r"[\s　]+")


def _norm(text):
    return _WHITESPACE.sub(" ", text or "").strip()


def score(report):
    """Return {check_name: bool | None}. None means the check does not apply.

    Args:
        report: the parsed qa report dict.

    Returns:
        dict: check name -> outcome.
    """
    design_number = (report.get("design_number") or "").strip()
    truth = EXPECTED.get(design_number)
    if truth is None:
        return {"识别得出fixture": False}

    entries = report.get("criteria_verdicts") or []
    verdict_by_mark = {}
    for mark in truth["marks"]:
        hits = [e for e in entries if mark in _norm(e.get("criterion"))]
        verdict_by_mark[mark] = hits[0].get("verdict") if len(hits) == 1 else None

    r = {}
    r["判据齐全"] = len(entries) == len(truth["marks"]) and all(
        verdict_by_mark[m] is not None for m in truth["marks"]
    )

    wanted_deviates = [m for m, v in truth["marks"].items() if v == "deviates"]
    wanted_fail = [m for m, v in truth["marks"].items() if v == "fail"]
    wanted_pass = [m for m, v in truth["marks"].items() if v == "pass"]

    # The headline check: a divergence that IS implemented, just not as
    # specified, must be reported as such and not rounded to either neighbour.
    r["契约偏差判deviates"] = all(
        verdict_by_mark[m] == "deviates" for m in wanted_deviates
    )
    # A softer companion: at minimum it must not read as satisfied. Scored
    # separately so a run that caught the problem but mislabelled it is
    # distinguishable from one that never saw it.
    r["契约偏差未判pass"] = all(verdict_by_mark[m] != "pass" for m in wanted_deviates)
    r["未兑现判fail"] = all(verdict_by_mark[m] == "fail" for m in wanted_fail)
    # Guards the degenerate strategy of failing everything to be safe.
    r["未误伤"] = all(verdict_by_mark[m] == "pass" for m in wanted_pass)

    run = report.get("test_run") or {}
    expected_run = truth["test_run"]
    r["test_run齐全"] = all(
        key in run
        for key in ("command", "exit_code", "collected", "passed", "failed", "skipped")
    )
    r["test_run数字属实"] = (
        run.get("collected") == expected_run["collected"]
        and run.get("passed") == expected_run["passed"]
        and run.get("failed") == expected_run["failed"]
        and run.get("skipped") == expected_run["skipped"]
    )
    r["test_run退出码属实"] = (
        bool(run.get("exit_code")) is expected_run["exit_code_nonzero"]
    )
    r["未拿collected当passed"] = (
        run.get("passed") != run.get("collected")
        if truth["passed_must_differ_from_collected"]
        else None
    )
    r["结论needs_fix"] = (report.get("recommendation") or "").strip() == truth[
        "recommendation"
    ]
    return r


def main(paths):
    names, rows = [], []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            result = score(json.load(handle))
        rows.append((path, result))
        for key in result:
            if key not in names:
                names.append(key)

    width = max(len(n) for n in names) + 2
    for path, result in rows:
        print(path)
        for name in names:
            outcome = result.get(name)
            mark = "n/a " if outcome is None else ("PASS" if outcome else "FAIL")
            print(f"  {name:<{width}} {mark}")
        applicable = [v for v in result.values() if v is not None]
        print(f"  {'合计':<{width}} {sum(applicable)}/{len(applicable)}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: checker.py <qa-report.json> [...]")
    main(sys.argv[1:])
