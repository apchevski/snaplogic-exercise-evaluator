"""AIJudge unit tests with a stubbed Anthropic client ($0)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from evaluator.ai_judge import (
    DEFAULT_JUDGE_MODEL,
    AIJudge,
    JudgeError,
    JudgeUsage,
    _load_schema,
)


class StubClient:
    """Records messages.create kwargs and replays canned model output."""

    def __init__(self, payloads: list[dict]):
        self._payloads = list(payloads)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._payloads.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(payload))],
            usage=SimpleNamespace(
                input_tokens=1_000,
                output_tokens=200,
                cache_creation_input_tokens=500,
                cache_read_input_tokens=300,
            ),
            stop_reason="end_turn",
        )


def _bundle(*, gates_pass: bool = True) -> dict:
    return {
        "task_slug": "task_01_generate_csv_report",
        "task_type": "file_writer",
        "exercise_description": "# Task 01\nDo the thing.",
        "general_rules": "RULES " * 50,
        "task_notes": "note",
        "solution_flow": [{"class_fqid": "csv-read", "label": "Read"}],
        "student_flow": [{"class_fqid": "csv-read", "label": "Read"}],
        "solution_definition": {"snap_map": {}},
        "student_definition": {"snap_map": {}},
        "student_version_notes": [],
        "hard_gates": [
            {"name": "pipeline_name_match", "passed": True, "detail": "ok"},
            {
                "name": "output_match",
                "passed": gates_pass,
                "detail": "ok" if gates_pass else "row 3 differs",
            },
        ],
    }


def _raw(verdict="pass", deductions=(), bonus=None) -> dict:
    return {
        "verdict": verdict,
        "summary": "Pipeline matches the solution.",
        "differences": [
            {
                "area": f"area-{i}",
                "description": "desc",
                "points_deducted": p,
                "rule_source": "general_rules: test rule",
                "reasoning": "because",
            }
            for i, p in enumerate(deductions)
        ],
        "bonus_question_answer": bonus,
    }


def test_points_arithmetic_in_python():
    judge = AIJudge(client=StubClient([_raw(deductions=[2, 1])]))
    evaluation, usage = judge.judge_exercise(_bundle())
    assert evaluation["verdict"] == "pass"
    assert evaluation["points"] == 7
    assert usage.calls == 1


def test_points_floor_at_zero_keeps_pass_verdict():
    judge = AIJudge(client=StubClient([_raw(deductions=[5, 5, 2])]))
    evaluation, _ = judge.judge_exercise(_bundle())
    assert evaluation["verdict"] == "pass"
    assert evaluation["points"] == 0


def test_verdict_forced_fail_when_gate_failed_even_if_model_says_pass():
    judge = AIJudge(client=StubClient([_raw(verdict="pass", deductions=[1])]))
    evaluation, _ = judge.judge_exercise(_bundle(gates_pass=False))
    assert evaluation["verdict"] == "fail"
    assert evaluation["failing_gate"] == "output_match"
    assert evaluation["failing_gate_detail"] == "row 3 differs"
    assert evaluation["points"] == 9


def test_invalid_deduction_values_are_clamped():
    judge = AIJudge(client=StubClient([_raw(deductions=[-3, 12])]))
    evaluation, _ = judge.judge_exercise(_bundle())
    # -3 -> 0, 12 -> 10 → points = max(0, 10-10) = 0
    assert [d["points_deducted"] for d in evaluation["differences"]] == [0, 10]
    assert evaluation["points"] == 0


def test_request_shape_schema_model_and_cache_control():
    stub = StubClient([_raw()])
    judge = AIJudge(client=stub)
    judge.judge_exercise(_bundle())
    call = stub.calls[0]
    assert call["model"] == DEFAULT_JUDGE_MODEL == "claude-sonnet-4-6"
    expected_schema = _load_schema("evaluation.schema.json")
    assert call["output_config"]["format"] == {
        "type": "json_schema",
        "schema": expected_schema,
    }
    system = call["system"]
    assert isinstance(system, list) and len(system) == 2
    assert "general evaluation rules" in system[1]["text"].lower()
    assert system[1]["cache_control"] == {"type": "ephemeral"}
    # rules live in the cached system block, not the per-exercise user turn
    assert "RULES RULES" not in call["messages"][0]["content"]


def test_judge_model_env_override(monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "claude-haiku-4-5")
    stub = StubClient([_raw()])
    AIJudge(client=stub).judge_exercise(_bundle())
    assert stub.calls[0]["model"] == "claude-haiku-4-5"


def test_usage_cost_estimate_sonnet_rates():
    usage = JudgeUsage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_creation_input_tokens=200_000,
        cache_read_input_tokens=400_000,
        model="claude-sonnet-4-6",
    )
    # 1M*3 + 0.1M*15 + 0.2M*3*1.25 + 0.4M*3*0.10 = 3 + 1.5 + 0.75 + 0.12
    assert usage.est_cost_usd == pytest.approx(5.37)


def test_overall_summary_call():
    stub = StubClient([{"overall_summary": "5 of 6 passed with 52/60 points."}])
    judge = AIJudge(client=stub)
    report = {
        "counts": {"pass": 5, "fail": 1, "missing": 0, "needs_sync": 0, "total": 6},
        "points_earned": 52,
        "points_possible": 60,
        "tasks": [{"verdict": "pass", "points": 10, "differences": []}],
    }
    overall, usage = judge.overall_summary(report)
    assert overall == "5 of 6 passed with 52/60 points."
    assert usage.calls == 1
    assert stub.calls[0]["max_tokens"] == 300


def test_non_text_response_raises_judge_error():
    class EmptyClient:
        def __init__(self):
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs):
            return SimpleNamespace(content=[], usage=None, stop_reason="refusal")

    judge = AIJudge(client=EmptyClient())
    with pytest.raises(JudgeError):
        judge.judge_exercise(_bundle())
