# Planning method behavioral evaluations

[中文](../../cn/evals/README.md) · [English](README.md)

`app/api/stage_methods.py` is prompt-as-code: wording changes downstream behavior. String assertions in `tests/test_skills.py` establish that instructions were delivered, not that agents followed them. A single live conversation, changing model settings, and subjective inspection make prompt improvements difficult to distinguish from sampling variance. This evaluation scaffolding provides repeatable observations.

## What is measured

The checker examines mechanically recognizable outputs: material tables, conflict checks, interface tables, uses relationships, decision tables, and whether Round 5 fields leaked into Round 3. It does not judge architecture quality or the wisdom of a solution. Passing does not prove a good design; failures identify missing expected artifacts, subject to checker limitations.

## Run

From the repository root, use a fresh disposable database, never the application database:

```bash
export HARNOQ_SQLITE_PATH=/tmp/harnoq-eval.db
# Use a new path or remove only an expendable prior evaluation database.
uv run python evals/make_fixtures.py
uv run python evals/dump_context.py P1 > /tmp/ctx_P1.json
uv run python evals/dump_context.py P2 > /tmp/ctx_P2.json
# Give an evaluation agent the context and save its output as Markdown.
uv run python evals/checker.py /tmp/out_*.md
```

Evaluation-agent instruction (substitute the placeholders):

> Act as Harnoq's planning agent. The user has asked to begin designing the first feature. Read `<ctx.json>` completely; `active_stage_method` is binding for this stage. Produce the user-facing Round 3 output for `<FEATURE-*>`. Do not call write tools. Use Chinese Markdown and standard pipe tables, and save the full result to `<out.md>`.

The Chinese output requirement is part of the existing checker's protocol, not the UI language. Translating this guide does not change the evaluator's recognized labels.

## Fixtures

| | P1: scheduling | P2: log aggregation and alerts |
| --- | --- | --- |
| Product shape | Standalone desktop UI | Unattended service without a UI |
| Main pressure | Constraint solving and conflicts | Throughput, out-of-order arrival, deduplication |
| UI Foundation | Present | Absent; exercises the no-UI branch |

Neither fixture is a real user project. Distinct domains and product shapes reduce overfitting. Fixture content belongs in evaluations, never in generic stage methods or downstream templates.

## Acceptance protocol

Run both fixtures three times: six outputs in total. Each check should pass at least five of six; the no-Round-5-content check must pass all six. After changing a prompt, rerun the entire set rather than only a failed sample.

## Limitations

- Results apply to the model and reasoning configuration used. Repeating with other configurations is a useful comparison.
- Only final text is examined, not intermediate tools or reasoning.
- The checker expects Markdown pipe tables. Rendered box-drawing tables may require conversion if they are the actual saved output rather than merely a client's visual rendering.
