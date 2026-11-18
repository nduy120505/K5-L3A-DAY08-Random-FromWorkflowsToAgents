"""Regression test from docs/trace_case.md: seeded_regression C-002@11.

First wrong step = event #3 `model_override {"delay_days": 5}` (DB says 2). The ticket's
number then drives a 20.000đ refund on a claim that must be denied (FM-03 ticket_trust).
The graders must flag it on the buggy variant and stay quiet on `fixed`.
"""
import pytest

pytestmark = pytest.mark.student


def _grade(shape: str, claim: str = "C-002", seed: int = 11):
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9.graders import DbFactKept, RefundAmountCorrect
    from lab9.trace_case import investigate

    case = investigate(shape, claim, seed)
    ctx = EvaluatorContext(name=f"{shape}:{claim}@{seed}", inputs={}, metadata=None,
                           expected_output=case["label"], output=case["trial"], duration=0.0,
                           _span_tree=None, attributes={}, metrics={})
    return case["trial"], DbFactKept().evaluate(ctx).value, RefundAmountCorrect().evaluate(ctx).value


def test_lab9_trace_case_c002_first_wrong_step_is_override():
    trial, db_kept, amount_ok = _grade("seeded_regression")
    kinds = [e.get("kind") for e in trial["events"]]
    first_bad = kinds.index("model_override")
    assert kinds[:first_bad] == ["handoff", "investigator"]      # steps before it were correct
    assert trial["events"][first_bad]["payload"] == {"delay_days": 5}
    assert db_kept is False and amount_ok is False
    assert trial["snapshot"]["refund_vnd"] == 20000 and not trial["lab8_pass"]


def test_lab9_trace_case_c002_fixed_keeps_db_fact():
    trial, db_kept, amount_ok = _grade("fixed")
    assert "model_override" not in [e.get("kind") for e in trial["events"]]
    assert db_kept is True and amount_ok is True
    assert trial["snapshot"]["refund_vnd"] == 0 and trial["lab8_pass"]
