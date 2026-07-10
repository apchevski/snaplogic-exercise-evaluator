"""Runner tests with injected plan/report fakes — no SnapLogic, no AWS."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluator.ai_judge import AIJudge
from evaluator.config import GRADES_DIR, TMP_DIR
from evaluator.runner import (
    GradeRunError,
    collect_grade_batch,
    run_grade,
    submit_grade_batch,
)

OVERALL_PLACEHOLDER = (
    "<!-- TODO Claude: SHORT (1-2 sentence) GENERAL synthesis. -->"
)


class StubClient:
    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._payloads.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(payload))],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
            stop_reason="end_turn",
        )


def _write_fixtures(student: str, *, ready: bool = True) -> dict:
    """Write a manifest + ai_context bundle the way cmd_plan would."""
    student_dir = TMP_DIR / "grades" / student
    task_dir = student_dir / "task_x"
    task_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = task_dir / "ai_context.json"
    eval_path = task_dir / "evaluation.json"
    bundle = {
        "task_slug": "task_x",
        "task_type": "file_writer",
        "exercise_description": "desc",
        "general_rules": "rules",
        "task_notes": "",
        "solution_flow": [],
        "student_flow": [],
        "solution_definition": {},
        "student_definition": {},
        "student_version_notes": [],
        "hard_gates": [{"name": "output_match", "passed": True, "detail": "ok"}],
    }
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    entries = []
    if ready:
        entries.append(
            {
                "slug": "task_x",
                "status": "ready_for_ai",
                "student_pipeline_name": "Task X",
                "ai_context_path": str(bundle_path),
                "evaluation_path": str(eval_path),
            }
        )
    manifest = {
        "student": student,
        "org": "o",
        "project_space": "ps",
        "student_project_path": f"o/ps/{student}",
        "generated_at": "2026-06-12",
        "entries": entries,
    }
    (student_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return {"eval_path": eval_path, "manifest": manifest}


def _fake_report_fn(student: str) -> None:
    report_dir = GRADES_DIR / student
    report_dir.mkdir(parents=True, exist_ok=True)
    md = (
        f"# Grade report — {student}\n\n"
        "- **Total**: 8/10 points\n\n"
        f"## Overall\n\n{OVERALL_PLACEHOLDER}\n\n---\n\n"
        "## task_x — ✓ PASS\n\n**Points**: 8/10\n"
    )
    (report_dir / "report.md").write_text(md, encoding="utf-8")
    payload = {
        "student": student,
        "counts": {"pass": 1, "fail": 0, "missing": 0, "needs_sync": 0, "total": 1},
        "points_earned": 8,
        "points_possible": 10,
        "overall_summary": None,
        "tasks": [{"slug": "task_x", "verdict": "pass", "points": 8, "differences": []}],
    }
    (report_dir / "report.json").write_text(json.dumps(payload), encoding="utf-8")


def _judge_payloads():
    return [
        {
            "verdict": "pass",
            "summary": "fine",
            "differences": [
                {
                    "area": "a",
                    "description": "d",
                    "points_deducted": 2,
                    "rule_source": "general_rules: x",
                    "reasoning": "r",
                }
            ],
            "bonus_question_answer": None,
        },
        {"overall_summary": "1 of 1 passed with 8/10 points."},
    ]


def test_full_run_judges_renders_and_fills_overall(evaluator_dirs):
    student = "Runner Test Student"
    fixtures = _write_fixtures(student)
    stub = StubClient(_judge_payloads())

    result = run_grade(
        student,
        judge=AIJudge(client=stub),
        plan_fn=lambda s, ps, t: 0,
        report_fn=lambda s, ps, t: (_fake_report_fn(s), 0)[1],
    )

    evaluation = json.loads(Path(fixtures["eval_path"]).read_text(encoding="utf-8"))
    assert evaluation["verdict"] == "pass"
    assert evaluation["points"] == 8

    assert result.judged_count == 1
    assert result.usage.calls == 2  # one judge call + one overall call
    assert result.points_earned == 8

    md = result.report_md_path.read_text(encoding="utf-8")
    assert "1 of 1 passed with 8/10 points." in md
    assert "TODO Claude" not in md
    assert result.report["overall_summary"] == "1 of 1 passed with 8/10 points."
    on_disk = json.loads(result.report_json_path.read_text(encoding="utf-8"))
    assert on_disk["overall_summary"] == "1 of 1 passed with 8/10 points."


def test_single_task_run_skips_overall(evaluator_dirs):
    student = "Runner Single Task"
    _write_fixtures(student)
    stub = StubClient(_judge_payloads()[:1])

    result = run_grade(
        student,
        task_slug="task_x",
        judge=AIJudge(client=stub),
        plan_fn=lambda s, ps, t: 0,
        report_fn=lambda s, ps, t: (_fake_report_fn(s), 0)[1],
    )
    assert result.usage.calls == 1
    md = result.report_md_path.read_text(encoding="utf-8")
    assert OVERALL_PLACEHOLDER in md  # untouched in single-task mode


def test_plan_failure_raises(evaluator_dirs):
    with pytest.raises(GradeRunError):
        run_grade(
            "Whoever",
            judge=AIJudge(client=StubClient([])),
            plan_fn=lambda s, ps, t: 2,
            report_fn=lambda s, ps, t: 0,
        )


# ----- batch full-run path (submit / collect) -----


class StubBatchClient:
    """Serves both the batch API (per-exercise) and messages.create (overall)."""

    def __init__(self, batch_results=(), create_payloads=()):
        self._batch_results = list(batch_results)
        self._create_payloads = list(create_payloads)
        self.created_requests = None
        self.messages = SimpleNamespace(
            create=self._create,
            batches=SimpleNamespace(
                create=self._batch_create,
                retrieve=lambda batch_id: SimpleNamespace(processing_status="ended"),
                results=lambda batch_id: iter(self._batch_results),
            ),
        )

    def _create(self, **kwargs):
        payload = self._create_payloads.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(payload))],
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5,
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
            ),
            stop_reason="end_turn",
        )

    def _batch_create(self, requests):
        self.created_requests = requests
        return SimpleNamespace(id="batch_1")


def _batch_result(custom_id, raw):
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(
            type="succeeded",
            message=SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(raw))],
                usage=SimpleNamespace(
                    input_tokens=100, output_tokens=50,
                    cache_creation_input_tokens=0, cache_read_input_tokens=0,
                ),
                stop_reason="end_turn",
            ),
        ),
    )


def test_submit_grade_batch_no_ready_finalizes_sync(evaluator_dirs, monkeypatch):
    student = "Runner No Ready"
    _write_fixtures(student, ready=False)  # manifest with nothing to judge
    import evaluator.grade as grade_mod

    monkeypatch.setattr(grade_mod, "cmd_plan", lambda s, ps, t, project=None: 0)
    monkeypatch.setattr(grade_mod, "cmd_report", lambda s, ps, t: (_fake_report_fn(s), 0)[1])

    judge = AIJudge(client=StubBatchClient(create_payloads=[{"overall_summary": "none judged."}]))
    store = SimpleNamespace(upload_scratch=lambda *a: 0)

    out = submit_grade_batch(
        student, project_space=None, project=None, judge=judge, store=store, job_id="job1"
    )
    assert out["done"] is True
    assert out["result"].points_earned == 8  # rendered synchronously, no batch


def test_submit_grade_batch_submits_and_stashes(evaluator_dirs, monkeypatch):
    student = "Runner Batch Submit"
    _write_fixtures(student, ready=True)
    import evaluator.grade as grade_mod

    monkeypatch.setattr(grade_mod, "cmd_plan", lambda s, ps, t, project=None: 0)

    judge = AIJudge(client=StubBatchClient())
    stashed = []
    store = SimpleNamespace(upload_scratch=lambda job_id, s: stashed.append((job_id, s)))

    out = submit_grade_batch(
        student, project_space=None, project=None, judge=judge, store=store, job_id="job2"
    )
    assert out["done"] is False
    assert out["batch_id"] == "batch_1"
    assert out["judged_count"] == 1
    assert stashed == [("job2", student)]
    # custom_id is the exercise slug.
    assert judge._client.created_requests[0]["custom_id"] == "task_x"


def test_collect_grade_batch_writes_evals_and_renders(evaluator_dirs, monkeypatch):
    student = "Runner Batch Collect"
    fixtures = _write_fixtures(student, ready=True)
    import evaluator.grade as grade_mod

    monkeypatch.setattr(grade_mod, "cmd_report", lambda s, ps, t: (_fake_report_fn(s), 0)[1])

    raw = {
        "verdict": "pass",
        "summary": "ok",
        "differences": [
            {"area": "a", "description": "d", "points_deducted": 2,
             "rule_source": "general_rules: x", "reasoning": "r"}
        ],
        "bonus_question_answer": None,
    }
    judge = AIJudge(
        client=StubBatchClient(
            batch_results=[_batch_result("task_x", raw)],
            create_payloads=[{"overall_summary": "1 of 1 passed."}],
        )
    )
    downloaded = []
    store = SimpleNamespace(download_scratch=lambda job_id, s: downloaded.append((job_id, s)))

    result = collect_grade_batch(
        student, project_space=None, batch_id="batch_1", judge=judge, store=store, job_id="job3"
    )

    assert downloaded == [("job3", student)]
    evaluation = json.loads(Path(fixtures["eval_path"]).read_text(encoding="utf-8"))
    assert evaluation["verdict"] == "pass" and evaluation["points"] == 8
    assert result.judged_count == 1
    # per-exercise usage was batched (discounted); the Overall call was not.
    assert result.usage.batch is True
    assert result.overall_usage is not None and result.overall_usage.batch is False
    assert result.report["overall_summary"] == "1 of 1 passed."
