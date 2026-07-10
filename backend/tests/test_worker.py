"""Worker Lambda tests: job lifecycle against moto, runner/store stubbed."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import boto3

from backend.src import worker
from backend.src.common import (
    dynamo_table,
    load_secrets_into_env,
    lock_key,
    utc_now_iso,
)


class StubStore:
    def __init__(self):
        self.uploaded_reports = []
        self.uploaded_artifacts = []
        self.materialized = False
        self.materialized_reports = []
        self.seeded = []
        self.uploaded_scratch = []
        self.downloaded_scratch = []
        self.deleted_scratch = []

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

    def upload_scratch(self, job_id, student):
        self.uploaded_scratch.append((job_id, student))
        return 0

    def download_scratch(self, job_id, student):
        self.downloaded_scratch.append((job_id, student))
        return 0

    def delete_scratch(self, job_id):
        self.deleted_scratch.append(job_id)


def _stub_batch_submit(monkeypatch, submit_impl):
    """Route a full-run grade through the batch submit, with the real AIJudge
    (which needs an API key) replaced by a dummy and submit_grade_batch stubbed.
    """
    import evaluator.ai_judge as ai_judge_mod
    import evaluator.runner as runner_mod

    monkeypatch.setattr(ai_judge_mod, "AIJudge", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(runner_mod, "submit_grade_batch", submit_impl)


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
        counts={"pass": 5, "fail": 1, "missing": 0, "needs_sync": 0, "total": 6},
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


def test_grade_full_run_with_nothing_to_judge_finalizes_synchronously(aws, monkeypatch):
    # A full run whose plan found no AI-ready exercise renders the report
    # without a batch — submit_grade_batch returns done=True and no delayed
    # poll message is enqueued.
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    enqueued: list = []
    monkeypatch.setattr(worker, "_enqueue_delayed", lambda *a, **k: enqueued.append(a))
    _stub_batch_submit(
        monkeypatch,
        lambda student, **kw: {"done": True, "result": _fake_run_result(student)},
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
    assert enqueued == []  # no batch → no follow-up poll

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


def test_multi_task_grade_runs_each_slug_and_merges_usage(aws, monkeypatch):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    import evaluator.runner as runner_mod

    seen_slugs = []

    def fake_run(student, **kw):
        seen_slugs.append(kw["task_slug"])
        return _fake_run_result(student)

    monkeypatch.setattr(runner_mod, "run_grade", fake_run)

    job = _seed_job(
        "job-grade-multi", "grade", "jane-doe",
        student="Jane Doe", student_slug="jane-doe",
        tasks=["task_01_orders", "task_02_currency"],
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    item = _get_job("job-grade-multi")
    assert item["status"] == "succeeded"
    assert seen_slugs == ["task_01_orders", "task_02_currency"]
    # One previous-report pull for the whole job, not one per task.
    assert len(store.materialized_reports) == 1
    # Usage and judged_count are summed across the per-task runs.
    assert item["result"]["usage"]["calls"] == 14
    assert float(item["result"]["usage"]["est_cost_usd"]) == 1.9
    assert item["result"]["usage"]["model"] == "claude-sonnet-4-6"
    assert item["result"]["judged_count"] == 12
    # The REPORT history row records which exercises the run covered.
    version = item["result"]["version"]
    row = dynamo_table().get_item(
        Key={"pk": "STUDENT#jane-doe", "sk": f"REPORT#{version}"}
    )["Item"]
    assert row["tasks_scope"] == ["task_01_orders", "task_02_currency"]


def test_grade_preserves_registration_fields_on_student_card(aws, monkeypatch):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    monkeypatch.setattr(worker, "_enqueue_delayed", lambda *a, **k: None)

    seen_kwargs = {}

    def fake_submit(student, **kw):
        seen_kwargs.update(kw)
        return {"done": True, "result": _fake_run_result(student)}

    _stub_batch_submit(monkeypatch, fake_submit)

    # Registered from the UI without grading (POST /v1/students).
    dynamo_table().put_item(
        Item={
            "pk": "STUDENT#reg-kid",
            "sk": "META",
            "entity": "student",
            "slug": "reg-kid",
            "display_name": "Reg Kid",
            "space": "Space X",
            "project": "Project X",
            "registered_by": "mentor@x.io",
            "registered_at": "2026-07-01T00:00:00+00:00",
        }
    )

    job = _seed_job(
        "job-grade-reg", "grade", "reg-kid",
        student="Reg Kid", student_slug="reg-kid",
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    assert _get_job("job-grade-reg")["status"] == "succeeded"
    # The registered space/project drive where the run looks for pipelines.
    assert seen_kwargs["project_space"] == "Space X"
    assert seen_kwargs["project"] == "Project X"
    meta = dynamo_table().get_item(Key={"pk": "STUDENT#reg-kid", "sk": "META"})["Item"]
    assert meta["points_earned"] == 52
    assert meta["registered_by"] == "mentor@x.io"
    assert meta["registered_at"] == "2026-07-01T00:00:00+00:00"
    # The job carried no space/project, so the registered ones survive.
    assert meta["space"] == "Space X"
    assert meta["project"] == "Project X"


def test_grade_job_failure_marks_failed_and_releases_lock(aws, monkeypatch):
    monkeypatch.setattr(worker, "_make_store", lambda: StubStore())

    def boom(student, **kw):
        raise RuntimeError("SnapLogic credentials rejected (401).")

    _stub_batch_submit(monkeypatch, boom)

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


def test_full_grade_submits_batch_and_enqueues_collect(aws, monkeypatch):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    enqueued: list = []
    monkeypatch.setattr(worker, "_enqueue_delayed", lambda body, **k: enqueued.append(body))
    _stub_batch_submit(
        monkeypatch,
        lambda student, **kw: {"done": False, "batch_id": "batch_xyz", "judged_count": 3},
    )

    job = _seed_job(
        "job-grade-batch", "grade", "jane-doe",
        student="Jane Doe", student_slug="jane-doe", requested_by="m@x.io",
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    item = _get_job("job-grade-batch")
    assert item["status"] == "batch_processing"
    assert item["batch_id"] == "batch_xyz"
    assert int(item["poll_attempts"]) == 0
    assert int(item["judged_count"]) == 3
    # The scratch tree was stashed for the collect step (real store does this;
    # here submit_grade_batch is stubbed, so we just assert the handoff shape).
    # Lock is NOT released — the collect step owns it now.
    assert "Item" in dynamo_table().get_item(
        Key={"pk": lock_key("grade", "jane-doe"), "sk": "META"}
    )
    # One delayed "check the batch" message enqueued.
    assert len(enqueued) == 1
    msg = enqueued[0]
    assert msg["phase"] == "collect"
    assert msg["batch_id"] == "batch_xyz"
    assert msg["poll_attempts"] == 0
    assert msg["job_id"] == "job-grade-batch"


def test_grade_collect_reenqueues_while_batch_processing(aws, monkeypatch):
    monkeypatch.setattr(worker, "_make_store", lambda: StubStore())
    enqueued: list = []
    monkeypatch.setattr(worker, "_enqueue_delayed", lambda body, **k: enqueued.append(body))
    import evaluator.ai_judge as ai_judge_mod
    import evaluator.runner as runner_mod

    monkeypatch.setattr(ai_judge_mod, "AIJudge", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(runner_mod, "batch_status", lambda batch_id, **kw: "in_progress")

    collect_msg = _seed_job(
        "job-grade-collect-1", "grade", "jane-doe",
        phase="collect", batch_id="batch_1",
        student="Jane Doe", student_slug="jane-doe", poll_attempts=0,
    )
    worker.handler({"Records": [{"body": json.dumps(collect_msg)}]}, None)

    item = _get_job("job-grade-collect-1")
    # Still processing → not terminal, counter bumped, poll re-enqueued.
    assert item["status"] not in ("succeeded", "failed")
    assert int(item["poll_attempts"]) == 1
    assert len(enqueued) == 1 and enqueued[0]["poll_attempts"] == 1
    # Lock kept alive across the wait.
    assert "Item" in dynamo_table().get_item(
        Key={"pk": lock_key("grade", "jane-doe"), "sk": "META"}
    )


def test_grade_collect_finalizes_when_batch_ended(aws, monkeypatch):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    monkeypatch.setattr(worker, "_enqueue_delayed", lambda *a, **k: None)
    import evaluator.ai_judge as ai_judge_mod
    import evaluator.runner as runner_mod

    monkeypatch.setattr(ai_judge_mod, "AIJudge", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(runner_mod, "batch_status", lambda batch_id, **kw: "ended")
    monkeypatch.setattr(
        runner_mod, "collect_grade_batch",
        lambda student, **kw: _fake_run_result(student),
    )

    collect_msg = _seed_job(
        "job-grade-collect-2", "grade", "jane-doe",
        phase="collect", batch_id="batch_2",
        student="Jane Doe", student_slug="jane-doe", poll_attempts=2,
        requested_by="m@x.io",
    )
    worker.handler({"Records": [{"body": json.dumps(collect_msg)}]}, None)

    item = _get_job("job-grade-collect-2")
    assert item["status"] == "succeeded"
    assert item["result"]["points_earned"] == 52
    # STUDENT card + REPORT row written by the shared finalize path.
    meta = dynamo_table().get_item(Key={"pk": "STUDENT#jane-doe", "sk": "META"})["Item"]
    assert meta["points_earned"] == 52
    version = item["result"]["version"]
    assert "Item" in dynamo_table().get_item(
        Key={"pk": "STUDENT#jane-doe", "sk": f"REPORT#{version}"}
    )
    # Lock released and the S3 scratch cleaned up.
    assert "Item" not in dynamo_table().get_item(
        Key={"pk": lock_key("grade", "jane-doe"), "sk": "META"}
    )
    assert store.deleted_scratch == ["job-grade-collect-2"]


def test_sync_job_success(aws, monkeypatch, evaluator_dirs):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)

    folder = evaluator_dirs["exercises"] / "worker_sync_slug"
    folder.mkdir(exist_ok=True)
    (folder / "description.md").write_text("# Worker Prep", encoding="utf-8")

    import evaluator.sync as sync_mod

    monkeypatch.setattr(sync_mod, "cmd_sync", lambda slug, ofile: 0)
    monkeypatch.setattr(
        sync_mod,
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
        "job-sync-1", "sync", "worker_sync_slug", exercise_slug="worker_sync_slug"
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    item = _get_job("job-sync-1")
    assert item["status"] == "succeeded"
    assert item["result"]["exercises"][0]["status"] == "ready"
    assert store.uploaded_artifacts == ["worker_sync_slug"]

    ex = dynamo_table().get_item(
        Key={"pk": "EXERCISE#worker_sync_slug", "sk": "META"}
    )["Item"]
    assert ex["sync_status"] == "ready"
    assert ex["title"] == "Worker Prep"


def _stub_sync_pipeline(monkeypatch, sync_hook=None):
    """Stub the deterministic sync machinery (no SnapLogic, no network)."""
    import evaluator.config as config_mod
    import evaluator.sync as sync_mod
    import evaluator.snaplogic_client as sl_mod

    def fake_sync(slug, ofile):
        if sync_hook:
            sync_hook(slug)
        return 0

    monkeypatch.setattr(sync_mod, "cmd_sync", fake_sync)
    monkeypatch.setattr(
        sync_mod,
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


def test_sync_synthesizes_task_json_from_config_and_preserves_row(
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

    _stub_sync_pipeline(monkeypatch, sync_hook=capture)

    job = _seed_job(
        "job-sync-cfg", "sync", "worker_cfg_slug", exercise_slug="worker_cfg_slug"
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    assert _get_job("job-sync-cfg")["status"] == "succeeded"
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
    assert ex["sync_status"] == "ready"
    assert ex["created_by"] == "admin@x.io"
    assert ex["authored_in"] == "s3"
    assert ex["task_config"]["triggered_task_name"] == "Task 77 – Config Task"


def test_archived_exercise_pruned_from_working_tree(aws, monkeypatch, evaluator_dirs):
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    monkeypatch.setattr(worker, "_enqueue_delayed", lambda *a, **k: None)
    _stub_batch_submit(
        monkeypatch,
        lambda student, **kw: {"done": True, "result": _fake_run_result(student)},
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


def test_deleted_exercise_pruned_from_working_tree(aws, monkeypatch, evaluator_dirs):
    """A hard-deleted exercise's tombstone row keeps its image folder out of
    every job's working tree — sync must never re-seed it into S3."""
    store = StubStore()
    monkeypatch.setattr(worker, "_make_store", lambda: store)
    monkeypatch.setattr(worker, "_enqueue_delayed", lambda *a, **k: None)
    _stub_batch_submit(
        monkeypatch,
        lambda student, **kw: {"done": True, "result": _fake_run_result(student)},
    )

    folder = evaluator_dirs["exercises"] / "worker_del_slug"
    folder.mkdir(exist_ok=True)
    (folder / "description.md").write_text("# Deleted Task", encoding="utf-8")
    dynamo_table().put_item(
        Item={
            "pk": "EXERCISE#worker_del_slug",
            "sk": "META",
            "entity": "exercise",
            "slug": "worker_del_slug",
            "deleted": True,
        }
    )

    job = _seed_job(
        "job-grade-del", "grade", "del-student",
        student="Del Student", student_slug="del-student",
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    assert _get_job("job-grade-del")["status"] == "succeeded"
    assert not folder.exists()


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
    load_secrets_into_env.cache_clear()
    assert load_secrets_into_env() is True
    assert os.environ["SNAPLOGIC_BASE_URL"] == "https://example.snaplogic.test"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test"
    load_secrets_into_env.cache_clear()
