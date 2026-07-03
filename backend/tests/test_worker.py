"""Worker Lambda tests: job lifecycle against moto, runner/store stubbed."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import boto3

from backend.src import worker
from backend.src.common import dynamo_table, lock_key, utc_now_iso


class StubStore:
    def __init__(self):
        self.uploaded_reports = []
        self.uploaded_artifacts = []
        self.materialized = False
        self.materialized_reports = []
        self.seeded = []

    def materialize_exercises(self):
        self.materialized = True

    def materialize_report(self, student, report_keys):
        self.materialized_reports.append((student, report_keys))

    def upload_report(self, student, student_slug, version):
        self.uploaded_reports.append((student, student_slug, version))
        return {
            "report_md_key": f"students/{student_slug}/{version}/report.md",
            "report_json_key": f"students/{student_slug}/{version}/report.json",
        }

    def upload_exercise_artifacts(self, slug):
        self.uploaded_artifacts.append(slug)
        return [f"exercises/{slug}/solution.json"]

    def seed_authored_files(self, slug):
        self.seeded.append(slug)
        return []


def _fake_run_result(student: str):
    usage = SimpleNamespace(
        to_dict=lambda: {
            "model": "claude-sonnet-4-6",
            "calls": 7,
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "est_cost_usd": 0.95,
        }
    )
    return SimpleNamespace(
        student=student,
        counts={"pass": 5, "fail": 1, "missing": 0, "needs_prep": 0, "total": 6},
        points_earned=52,
        points_possible=60,
        judged_count=6,
        usage=usage,
        report={"overall_summary": "5 of 6 passed."},
    )


def _seed_job(job_id: str, job_type: str, target: str, **payload) -> dict:
    now = utc_now_iso()
    dynamo_table().put_item(
        Item={
            "pk": f"JOB#{job_id}",
            "sk": "META",
            "entity": "job",
            "slug": job_id,
            "job_id": job_id,
            "job_type": job_type,
            "status": "queued",
            "target": target,
            "created_at": now,
            "updated_at": now,
        }
    )
    dynamo_table().put_item(
        Item={"pk": lock_key(job_type, target), "sk": "META", "job_id": job_id}
    )
    return {"job_id": job_id, "job_type": job_type, "target": target, **payload}


def _get_job(job_id: str) -> dict:
    return dynamo_table().get_item(Key={"pk": f"JOB#{job_id}", "sk": "META"})["Item"]


def test_grade_job_success(aws, monkeypatch):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    import evaluator.runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "run_grade", lambda student, **kw: _fake_run_result(student)
    )

    job = _seed_job(
        "job-grade-1", "grade", "jane-doe",
        student="Jane Doe", student_slug="jane-doe", requested_by="m@x.io",
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    item = _get_job("job-grade-1")
    assert item["status"] == "succeeded"
    assert item["result"]["points_earned"] == 52
    assert float(item["result"]["usage"]["est_cost_usd"]) == 0.95
    assert store.materialized and len(store.uploaded_reports) == 1

    # STUDENT card refreshed
    meta = dynamo_table().get_item(Key={"pk": "STUDENT#jane-doe", "sk": "META"})["Item"]
    assert meta["display_name"] == "Jane Doe"
    assert meta["points_earned"] == 52
    assert meta["overall_summary"] == "5 of 6 passed."
    assert meta["requested_by"] == "m@x.io"

    # REPORT history row written
    version = item["result"]["version"]
    row = dynamo_table().get_item(
        Key={"pk": "STUDENT#jane-doe", "sk": f"REPORT#{version}"}
    )["Item"]
    assert row["points_earned"] == 52

    # lock released
    assert "Item" not in dynamo_table().get_item(
        Key={"pk": lock_key("grade", "jane-doe"), "sk": "META"}
    )
    # full runs never pull a previous report
    assert store.materialized_reports == []


def test_single_task_grade_pulls_previous_report(aws, monkeypatch):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    import evaluator.runner as runner_mod

    seen_kwargs = {}

    def fake_run(student, **kw):
        seen_kwargs.update(kw)
        return _fake_run_result(student)

    monkeypatch.setattr(runner_mod, "run_grade", fake_run)

    # A previous full grading left report pointers on the STUDENT card.
    dynamo_table().put_item(
        Item={
            "pk": "STUDENT#jane-doe",
            "sk": "META",
            "entity": "student",
            "slug": "jane-doe",
            "display_name": "Jane Doe",
            "report_md_key": "students/jane-doe/v1/report.md",
            "report_json_key": "students/jane-doe/v1/report.json",
        }
    )

    job = _seed_job(
        "job-grade-single", "grade", "jane-doe",
        student="Jane Doe", student_slug="jane-doe", task="task_02_currency",
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    assert _get_job("job-grade-single")["status"] == "succeeded"
    assert seen_kwargs["task_slug"] == "task_02_currency"
    assert store.materialized_reports == [
        (
            "Jane Doe",
            {
                "report_md_key": "students/jane-doe/v1/report.md",
                "report_json_key": "students/jane-doe/v1/report.json",
            },
        )
    ]


def test_single_task_grade_without_previous_meta_still_runs(aws, monkeypatch):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    import evaluator.runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "run_grade", lambda student, **kw: _fake_run_result(student)
    )

    job = _seed_job(
        "job-grade-first-single", "grade", "new-kid",
        student="New Kid", student_slug="new-kid", task="task_01_orders",
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    assert _get_job("job-grade-first-single")["status"] == "succeeded"
    # No STUDENT card yet — materialize gets empty keys and downloads nothing.
    assert store.materialized_reports == [
        ("New Kid", {"report_md_key": None, "report_json_key": None})
    ]


def test_grade_job_failure_marks_failed_and_releases_lock(aws, monkeypatch):
    monkeypatch.setattr(worker, "_make_store", lambda: StubStore())
    import evaluator.runner as runner_mod

    def boom(student, **kw):
        raise RuntimeError("SnapLogic credentials rejected (401).")

    monkeypatch.setattr(runner_mod, "run_grade", boom)

    job = _seed_job(
        "job-grade-2", "grade", "bad-student",
        student="Bad Student", student_slug="bad-student",
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    item = _get_job("job-grade-2")
    assert item["status"] == "failed"
    assert "SnapLogic credentials rejected" in item["error"]
    assert "Item" not in dynamo_table().get_item(
        Key={"pk": lock_key("grade", "bad-student"), "sk": "META"}
    )


def test_prep_job_success(aws, monkeypatch, evaluator_dirs):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)

    folder = evaluator_dirs["exercises"] / "worker_prep_slug"
    folder.mkdir(exist_ok=True)
    (folder / "description.md").write_text("# Worker Prep", encoding="utf-8")

    import evaluator.prep as prep_mod

    monkeypatch.setattr(prep_mod, "cmd_sync", lambda slug, ofile: 0)
    monkeypatch.setattr(
        prep_mod,
        "_classify_folder",
        lambda folder, client, settings: SimpleNamespace(
            status="ready", reason="fresh", task_type="file_writer"
        ),
    )

    import evaluator.config as config_mod

    monkeypatch.setattr(config_mod, "load_settings", lambda: SimpleNamespace())

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    import evaluator.snaplogic_client as sl_mod

    monkeypatch.setattr(sl_mod, "SnapLogicClient", lambda settings: FakeClient())

    job = _seed_job(
        "job-prep-1", "prep", "worker_prep_slug", exercise_slug="worker_prep_slug"
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    item = _get_job("job-prep-1")
    assert item["status"] == "succeeded"
    assert item["result"]["exercises"][0]["status"] == "ready"
    assert store.uploaded_artifacts == ["worker_prep_slug"]

    ex = dynamo_table().get_item(
        Key={"pk": "EXERCISE#worker_prep_slug", "sk": "META"}
    )["Item"]
    assert ex["prep_status"] == "ready"
    assert ex["title"] == "Worker Prep"


def _stub_prep_pipeline(monkeypatch, sync_hook=None):
    """Stub the deterministic prep machinery (no SnapLogic, no network)."""
    import evaluator.config as config_mod
    import evaluator.prep as prep_mod
    import evaluator.snaplogic_client as sl_mod

    def fake_sync(slug, ofile):
        if sync_hook:
            sync_hook(slug)
        return 0

    monkeypatch.setattr(prep_mod, "cmd_sync", fake_sync)
    monkeypatch.setattr(
        prep_mod,
        "_classify_folder",
        lambda folder, client, settings: SimpleNamespace(
            status="ready", reason="fresh", task_type=None
        ),
    )
    monkeypatch.setattr(
        config_mod,
        "load_settings",
        lambda: SimpleNamespace(
            org_name="Org", project_space_name="Solutions", project_name="Training"
        ),
    )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(sl_mod, "SnapLogicClient", lambda settings: FakeClient())


def test_prep_synthesizes_task_json_from_config_and_preserves_row(
    aws, monkeypatch, evaluator_dirs
):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)

    folder = evaluator_dirs["exercises"] / "worker_cfg_slug"
    folder.mkdir(exist_ok=True)
    (folder / "description.md").write_text("# Task 77 – Config", encoding="utf-8")
    # Stale task.json from a previous overlay — the stored config must win.
    (folder / "task.json").write_text('{"task_type": "stale"}', encoding="utf-8")

    dynamo_table().put_item(
        Item={
            "pk": "EXERCISE#worker_cfg_slug",
            "sk": "META",
            "entity": "exercise",
            "slug": "worker_cfg_slug",
            "authored_in": "s3",
            "created_by": "admin@x.io",
            "task_config": {
                "task_type": "triggered_task",
                "triggered_task_name": "Task 77 – Config Task",
                "requests": [{"name": "addition", "params": {"mathOperation": "3+5"}}],
            },
        }
    )

    seen_at_sync = {}

    def capture(slug):
        seen_at_sync["task_json"] = json.loads(
            (folder / "task.json").read_text(encoding="utf-8")
        )

    _stub_prep_pipeline(monkeypatch, sync_hook=capture)

    job = _seed_job(
        "job-prep-cfg", "prep", "worker_cfg_slug", exercise_slug="worker_cfg_slug"
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    assert _get_job("job-prep-cfg")["status"] == "succeeded"
    # Synthesized before sync ran, from config + env-derived pipeline path.
    tj = seen_at_sync["task_json"]
    assert tj["task_type"] == "triggered_task"
    assert tj["solution_pipeline_path"] == "Org/Solutions/Training/Task 77 – Config"
    assert tj["triggered_task_name"] == "Task 77 – Config Task"
    assert tj["requests"] == [{"name": "addition", "params": {"mathOperation": "3+5"}}]
    # Authored files got their additive S3 seed pass.
    assert store.seeded == ["worker_cfg_slug"]

    # The survey rewrite preserved the authored row fields.
    ex = dynamo_table().get_item(
        Key={"pk": "EXERCISE#worker_cfg_slug", "sk": "META"}
    )["Item"]
    assert ex["prep_status"] == "ready"
    assert ex["created_by"] == "admin@x.io"
    assert ex["authored_in"] == "s3"
    assert ex["task_config"]["triggered_task_name"] == "Task 77 – Config Task"


def test_archived_exercise_pruned_from_working_tree(aws, monkeypatch, evaluator_dirs):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    import evaluator.runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "run_grade", lambda student, **kw: _fake_run_result(student)
    )

    folder = evaluator_dirs["exercises"] / "worker_arch_slug"
    folder.mkdir(exist_ok=True)
    (folder / "description.md").write_text("# Archived Task", encoding="utf-8")
    dynamo_table().put_item(
        Item={
            "pk": "EXERCISE#worker_arch_slug",
            "sk": "META",
            "entity": "exercise",
            "slug": "worker_arch_slug",
            "archived": True,
        }
    )

    job = _seed_job(
        "job-grade-arch", "grade", "arch-student",
        student="Arch Student", student_slug="arch-student",
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    assert _get_job("job-grade-arch")["status"] == "succeeded"
    # Pruned from the merged tree (so it can't be graded/counted) …
    assert not folder.exists()
    # … but nothing in S3 was touched (StubStore has no delete surface at all).


def test_unknown_job_type_fails_cleanly(aws, monkeypatch):
    monkeypatch.setattr(worker, "_make_store", lambda: StubStore())
    job = _seed_job("job-x-1", "mystery", "whatever")
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)
    item = _get_job("job-x-1")
    assert item["status"] == "failed"
    assert "Unknown job_type" in item["error"]


def test_secrets_loaded_into_env(aws, monkeypatch):
    sm = boto3.client("secretsmanager")
    secret = sm.create_secret(
        Name="test-app-secrets",
        SecretString=json.dumps(
            {"SNAPLOGIC_BASE_URL": "https://example.snaplogic.test",
             "ANTHROPIC_API_KEY": "sk-ant-test"}
        ),
    )
    monkeypatch.setenv("SECRET_ARN", secret["ARN"])
    monkeypatch.delenv("SNAPLOGIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    worker._load_secrets_into_env.cache_clear()
    assert worker._load_secrets_into_env() is True
    assert os.environ["SNAPLOGIC_BASE_URL"] == "https://example.snaplogic.test"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test"
    worker._load_secrets_into_env.cache_clear()
