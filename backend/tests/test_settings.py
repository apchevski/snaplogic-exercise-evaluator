"""Per-user settings tests: the /v1/settings routes (role gating, secret
masking, validation) and the env-override mechanics jobs run under."""
from __future__ import annotations

import json
import os

from backend.src import api, common
from backend.src.common import (
    apply_user_overrides,
    dynamo_table,
    user_settings_pk,
)

from .conftest import api_event


def _call(event):
    return api.handler(event, None)


def _body(resp) -> dict:
    return json.loads(resp["body"])


def _settings(resp) -> dict:
    return _body(resp)["settings"]


# ---------- role gating ----------


def test_student_cannot_read_or_write_settings(aws):
    resp = _call(api_event("GET", "/v1/settings", groups=("student",)))
    assert resp["statusCode"] == 403
    resp = _call(
        api_event("PUT", "/v1/settings", groups=("student",), body={"judge_model": None})
    )
    assert resp["statusCode"] == 403


def test_mentor_cannot_store_snaplogic_credentials(aws):
    for key in ("snaplogic_username", "snaplogic_password"):
        resp = _call(
            api_event("PUT", "/v1/settings", groups=("mentor",), body={key: "x"})
        )
        assert resp["statusCode"] == 403


# ---------- read/write + masking ----------


def test_get_settings_defaults_when_nothing_stored(aws):
    resp = _call(api_event("GET", "/v1/settings", groups=("mentor",)))
    assert resp["statusCode"] == 200
    s = _settings(resp)
    assert s["email"] == "mentor@example.com"
    assert s["snaplogic_username"] is None
    assert s["snaplogic_password_set"] is False
    assert s["anthropic_api_key_set"] is False
    assert s["judge_model"] is None
    assert s["default_model"] == "claude-sonnet-4-6"
    assert {m["id"] for m in s["allowed_models"]} >= {"claude-sonnet-4-6"}


def test_mentor_stores_key_and_model_masked(aws):
    resp = _call(
        api_event(
            "PUT",
            "/v1/settings",
            groups=("mentor",),
            body={
                "anthropic_api_key": "sk-ant-api03-verysecret-tail",
                "judge_model": "claude-opus-4-8",
            },
        )
    )
    assert resp["statusCode"] == 200
    s = _settings(resp)
    assert s["anthropic_api_key_set"] is True
    assert s["anthropic_api_key_hint"] == "…tail"
    assert s["judge_model"] == "claude-opus-4-8"
    # The key itself never appears anywhere in the response.
    assert "verysecret" not in resp["body"]

    # GET reflects the same masked state.
    s = _settings(_call(api_event("GET", "/v1/settings", groups=("mentor",))))
    assert s["anthropic_api_key_set"] is True and s["judge_model"] == "claude-opus-4-8"


def test_admin_stores_snaplogic_credentials_write_only(aws):
    resp = _call(
        api_event(
            "PUT",
            "/v1/settings",
            groups=("admin",),
            email="admin@x.io",
            body={"snaplogic_username": "me@corp.io", "snaplogic_password": "hunter22"},
        )
    )
    assert resp["statusCode"] == 200
    s = _settings(resp)
    assert s["snaplogic_username"] == "me@corp.io"
    assert s["snaplogic_password_set"] is True
    assert "hunter22" not in resp["body"]
    # Stored on the USER row (raw), keyed by lowercased email.
    item = dynamo_table().get_item(
        Key={"pk": user_settings_pk("admin@x.io"), "sk": "SETTINGS"}
    )["Item"]
    assert item["snaplogic_password"] == "hunter22"


def test_put_clears_with_null(aws):
    _call(
        api_event(
            "PUT",
            "/v1/settings",
            groups=("mentor",),
            body={"anthropic_api_key": "sk-ant-api03-something", "judge_model": "claude-haiku-4-5"},
        )
    )
    resp = _call(
        api_event(
            "PUT",
            "/v1/settings",
            groups=("mentor",),
            body={"anthropic_api_key": None, "judge_model": None},
        )
    )
    s = _settings(resp)
    assert s["anthropic_api_key_set"] is False
    assert s["judge_model"] is None


def test_put_rejects_unknown_key_and_bad_model(aws):
    resp = _call(
        api_event("PUT", "/v1/settings", groups=("mentor",), body={"nope": "x"})
    )
    assert resp["statusCode"] == 400
    resp = _call(
        api_event(
            "PUT", "/v1/settings", groups=("mentor",), body={"judge_model": "gpt-4"}
        )
    )
    assert resp["statusCode"] == 400


def test_settings_row_stays_out_of_listings(aws):
    _call(
        api_event(
            "PUT",
            "/v1/settings",
            groups=("mentor",),
            body={"anthropic_api_key": "sk-ant-api03-something"},
        )
    )
    listed = _body(_call(api_event("GET", "/v1/students")))["students"]
    assert listed == []


# ---------- env overrides (what jobs run under) ----------


def _store_settings(email: str, **fields) -> None:
    dynamo_table().put_item(
        Item={
            "pk": user_settings_pk(email),
            "sk": "SETTINGS",
            "email": email,
            **fields,
        }
    )


def test_apply_user_overrides_and_restore(aws, monkeypatch):
    # Shared (base) credentials from the deployment secret / ambient env.
    monkeypatch.setenv("SNAPLOGIC_ADMIN_USERNAME", "shared-admin")
    monkeypatch.setenv("SNAPLOGIC_ADMIN_PASSWORD", "shared-pw")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared")
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    common.reset_base_env_snapshot()

    _store_settings(
        "admin@x.io",
        snaplogic_username="own-admin",
        snaplogic_password="own-pw",
        anthropic_api_key="sk-own",
        judge_model="claude-opus-4-8",
    )

    apply_user_overrides("Admin@X.io")  # matching is case-insensitive
    assert os.environ["SNAPLOGIC_ADMIN_USERNAME"] == "own-admin"
    assert os.environ["SNAPLOGIC_ADMIN_PASSWORD"] == "own-pw"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-own"
    assert os.environ["JUDGE_MODEL"] == "claude-opus-4-8"

    # A user with no stored settings gets the shared values back — including
    # dropping JUDGE_MODEL, which had no base value (warm-container hygiene).
    apply_user_overrides("mentor@example.com")
    assert os.environ["SNAPLOGIC_ADMIN_USERNAME"] == "shared-admin"
    assert os.environ["SNAPLOGIC_ADMIN_PASSWORD"] == "shared-pw"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-shared"
    assert "JUDGE_MODEL" not in os.environ


def test_half_a_snaplogic_pair_is_ignored(aws, monkeypatch):
    monkeypatch.setenv("SNAPLOGIC_ADMIN_USERNAME", "shared-admin")
    monkeypatch.setenv("SNAPLOGIC_ADMIN_PASSWORD", "shared-pw")
    common.reset_base_env_snapshot()

    # Username without a password can't authenticate — the pair stays shared.
    _store_settings("admin@x.io", snaplogic_username="own-admin")
    apply_user_overrides("admin@x.io")
    assert os.environ["SNAPLOGIC_ADMIN_USERNAME"] == "shared-admin"
    assert os.environ["SNAPLOGIC_ADMIN_PASSWORD"] == "shared-pw"


def test_no_email_leaves_shared_credentials(aws, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared")
    common.reset_base_env_snapshot()
    apply_user_overrides(None)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-shared"


def test_grade_job_runs_under_requesters_credentials(aws, monkeypatch):
    """End-to-end through the worker: a grade job requested by a user with
    stored settings sees their key/model in the env while grading runs."""
    import evaluator.runner as runner_mod
    from types import SimpleNamespace

    from backend.src import worker

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-shared")
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    common.reset_base_env_snapshot()
    _store_settings(
        "mentor@example.com",
        anthropic_api_key="sk-mentors-own",
        judge_model="claude-haiku-4-5",
    )

    class _Store:
        def materialize_exercises(self):
            pass

        def materialize_report(self, student, keys):
            pass

        def upload_report(self, student, slug, version):
            return {
                "report_md_key": f"students/{slug}/{version}/report.md",
                "report_json_key": f"students/{slug}/{version}/report.json",
            }

    monkeypatch.setattr(worker, "_make_store", lambda: _Store())

    seen_env: dict[str, str | None] = {}

    def fake_run(student, **kw):
        seen_env["key"] = os.environ.get("ANTHROPIC_API_KEY")
        seen_env["model"] = os.environ.get("JUDGE_MODEL")
        usage = SimpleNamespace(to_dict=lambda: {"model": "claude-haiku-4-5", "calls": 1})
        return SimpleNamespace(
            student=student,
            counts={"pass": 1, "fail": 0, "missing": 0, "needs_sync": 0, "total": 1},
            points_earned=10,
            points_possible=10,
            judged_count=1,
            usage=usage,
            report={"overall_summary": "ok"},
        )

    monkeypatch.setattr(runner_mod, "run_grade", fake_run)

    job = {
        "job_id": "job-own-creds",
        "job_type": "grade",
        "target": "kid",
        "student": "Kid",
        "student_slug": "kid",
        "task": "task_01_orders",
        "requested_by": "mentor@example.com",
    }
    dynamo_table().put_item(
        Item={"pk": "JOB#job-own-creds", "sk": "META", "job_id": "job-own-creds",
              "status": "queued"}
    )
    worker.handler({"Records": [{"body": json.dumps(job)}]}, None)

    assert seen_env == {"key": "sk-mentors-own", "model": "claude-haiku-4-5"}
    item = dynamo_table().get_item(Key={"pk": "JOB#job-own-creds", "sk": "META"})["Item"]
    assert item["status"] == "succeeded"
