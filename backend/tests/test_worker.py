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

    def materialize_exercises(self):
        self.materialized = True

    def upload_report(self, student, student_slug, version):
        self.uploaded_reports.append((student, student_slug, version))
        return {
            "report_md": f"students/{student_slug}/{version}/report.md",
            "report_json": f"students/{student_slug}/{version}/report.json",
        }

    def upload_exercise_artifacts(self, slug):
        self.uploaded_artifacts.append(slug)
        return [f"exercises/{slug}/solution.json"]


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
