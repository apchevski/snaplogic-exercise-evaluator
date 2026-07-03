"""API Lambda tests: auth matrix, job creation/dedupe, reads. Moto-backed."""
from __future__ import annotations

import json
import os

from backend.src import api
from backend.src.common import dynamo_table

from .conftest import api_event


def _call(event):
    return api.handler(event, None)


def _body(resp) -> dict:
    return json.loads(resp["body"])


# ---------- auth matrix ----------


def test_no_jwt_claims_is_401(aws):
    resp = _call(api_event("GET", "/v1/students", groups=None))
    assert resp["statusCode"] == 401


def test_mentor_cannot_prep_403(aws):
    resp = _call(api_event("POST", "/v1/preps", groups=("mentor",), body={}))
    assert resp["statusCode"] == 403


def test_admin_can_prep_202(aws, evaluator_dirs):
    resp = _call(
        api_event("POST", "/v1/preps", groups=("admin",), email="a@x.io", body={})
    )
    assert resp["statusCode"] == 202
    job = _body(resp)
    assert job["job_type"] == "prep" and job["status"] == "queued"
    # SQS got the message
    messages = aws["sqs"].receive_message(QueueUrl=os.environ["QUEUE_URL"])["Messages"]
    payload = json.loads(messages[0]["Body"])
    assert payload["job_type"] == "prep" and payload["requested_by"] == "a@x.io"
    # JOB item exists
    item = dynamo_table().get_item(Key={"pk": f"JOB#{job['id']}", "sk": "META"})["Item"]
    assert item["status"] == "queued"


def test_source_ip_outside_allowlist_403(aws, monkeypatch):
    monkeypatch.setenv("ALLOWED_CIDRS", "192.168.0.0/24, 10.9.0.0/16")
    resp = _call(api_event("GET", "/v1/students", source_ip="8.8.8.8"))
    assert resp["statusCode"] == 403
    resp = _call(api_event("GET", "/v1/students", source_ip="10.9.1.5"))
    assert resp["statusCode"] == 200


# ---------- gradings ----------


def test_post_grading_then_duplicate_409(aws):
    body = {"student": "Jane Doe"}
    first = _call(api_event("POST", "/v1/gradings", body=body))
    assert first["statusCode"] == 202
    job = _body(first)
    assert job["target"] == "jane-doe"

    dup = _call(api_event("POST", "/v1/gradings", body=body))
    assert dup["statusCode"] == 409

    other = _call(api_event("POST", "/v1/gradings", body={"student": "John Roe"}))
    assert other["statusCode"] == 202


def test_post_grading_requires_student(aws):
    resp = _call(api_event("POST", "/v1/gradings", body={}))
    assert resp["statusCode"] == 400


def test_get_grading_status(aws):
    created = _body(_call(api_event("POST", "/v1/gradings", body={"student": "Stat Us"})))
    resp = _call(api_event("GET", f"/v1/gradings/{created['id']}"))
    assert resp["statusCode"] == 200
    job = _body(resp)
    assert job["status"] == "queued" and job["job_type"] == "grade"
    assert job["student"] == "Stat Us"


def test_get_unknown_job_404(aws):
    resp = _call(api_event("GET", "/v1/gradings/nope"))
    assert resp["statusCode"] == 404


# ---------- preps ----------


def test_prep_unknown_slug_400(aws, evaluator_dirs):
    resp = _call(
        api_event("POST", "/v1/preps", groups=("admin",), body={"slug": "no_such_folder"})
    )
    assert resp["statusCode"] == 400


def test_prep_known_slug_202(aws, evaluator_dirs):
    folder = evaluator_dirs["exercises"] / "api_prep_slug"
    folder.mkdir(exist_ok=True)
    (folder / "description.md").write_text("# API Prep Slug", encoding="utf-8")
    resp = _call(
        api_event("POST", "/v1/preps", groups=("admin",), body={"slug": "api_prep_slug"})
    )
    assert resp["statusCode"] == 202
    assert _body(resp)["target"] == "api_prep_slug"


# ---------- reads ----------


def test_list_students(aws):
    dynamo_table().put_item(
        Item={
            "pk": "STUDENT#jane-doe",
            "sk": "META",
            "entity": "student",
            "slug": "jane-doe",
            "display_name": "Jane Doe",
            "points_earned": 52,
            "points_possible": 90,
        }
    )
    resp = _call(api_event("GET", "/v1/students"))
    assert resp["statusCode"] == 200
    students = _body(resp)["students"]
    assert students[0]["display_name"] == "Jane Doe"
    assert students[0]["points_earned"] == 52
    assert "pk" not in students[0]


def test_get_student_with_latest_report(aws):
    aws["s3"].put_object(
        Bucket=os.environ["DATA_BUCKET"],
        Key="students/jane-doe/v1/report.json",
        Body=json.dumps({"points_earned": 52}).encode(),
    )
    dynamo_table().put_item(
        Item={
            "pk": "STUDENT#jane-doe",
            "sk": "META",
            "entity": "student",
            "slug": "jane-doe",
            "display_name": "Jane Doe",
            "report_json_key": "students/jane-doe/v1/report.json",
        }
    )
    resp = _call(api_event("GET", "/v1/students/jane-doe"))
    assert resp["statusCode"] == 200
    data = _body(resp)
    assert data["student"]["display_name"] == "Jane Doe"
    assert data["report"]["points_earned"] == 52


def test_list_exercises_merges_image_and_dynamo(aws, evaluator_dirs):
    folder = evaluator_dirs["exercises"] / "api_ex_merge"
    folder.mkdir(exist_ok=True)
    (folder / "description.md").write_text(
        "# Task Merge\n\n### Objective:\n\nBuild the merge pipeline.\n",
        encoding="utf-8",
    )
    dynamo_table().put_item(
        Item={
            "pk": "EXERCISE#api_ex_merge",
            "sk": "META",
            "entity": "exercise",
            "slug": "api_ex_merge",
            "prep_status": "ready",
        }
    )
    resp = _call(api_event("GET", "/v1/exercises"))
    assert resp["statusCode"] == 200
    exercises = {e["slug"]: e for e in _body(resp)["exercises"]}
    assert exercises["api_ex_merge"]["prep_status"] == "ready"
    assert exercises["api_ex_merge"]["title"] == "Task Merge"
    assert "Build the merge pipeline." in exercises["api_ex_merge"]["description"]


# ---------- exercise resources (student input files) ----------


def _make_exercise_with_resource(
    evaluator_dirs, slug: str, filename: str = "Input.zip", content: bytes = b"zip-bytes"
):
    folder = evaluator_dirs["exercises"] / slug
    (folder / "resources").mkdir(parents=True, exist_ok=True)
    (folder / "description.md").write_text(f"# {slug}", encoding="utf-8")
    (folder / "resources" / filename).write_bytes(content)
    return folder


def test_list_exercises_includes_resources(aws, evaluator_dirs):
    _make_exercise_with_resource(evaluator_dirs, "api_res_list", content=b"123456789")
    resp = _call(api_event("GET", "/v1/exercises"))
    exercises = {e["slug"]: e for e in _body(resp)["exercises"]}
    assert exercises["api_res_list"]["resources"] == [
        {"filename": "Input.zip", "size_bytes": 9}
    ]


def test_download_resource_presigns_and_mirrors_to_s3(aws, evaluator_dirs):
    _make_exercise_with_resource(evaluator_dirs, "api_res_dl", content=b"data-123")
    resp = _call(api_event("GET", "/v1/exercises/api_res_dl/resources/Input.zip"))
    assert resp["statusCode"] == 200
    data = _body(resp)
    assert data["filename"] == "Input.zip"
    assert "exercise-resources/api_res_dl/Input.zip" in data["url"]
    # The image copy got mirrored into S3 (that's what the URL points at).
    obj = aws["s3"].get_object(
        Bucket=os.environ["DATA_BUCKET"], Key="exercise-resources/api_res_dl/Input.zip"
    )
    assert obj["Body"].read() == b"data-123"


def test_download_resource_refreshes_stale_s3_copy(aws, evaluator_dirs):
    _make_exercise_with_resource(evaluator_dirs, "api_res_stale", content=b"new-content")
    aws["s3"].put_object(
        Bucket=os.environ["DATA_BUCKET"],
        Key="exercise-resources/api_res_stale/Input.zip",
        Body=b"old-content",
    )
    resp = _call(api_event("GET", "/v1/exercises/api_res_stale/resources/Input.zip"))
    assert resp["statusCode"] == 200
    obj = aws["s3"].get_object(
        Bucket=os.environ["DATA_BUCKET"], Key="exercise-resources/api_res_stale/Input.zip"
    )
    assert obj["Body"].read() == b"new-content"


def test_download_unknown_resource_404(aws, evaluator_dirs):
    _make_exercise_with_resource(evaluator_dirs, "api_res_404")
    resp = _call(api_event("GET", "/v1/exercises/api_res_404/resources/nope.csv"))
    assert resp["statusCode"] == 404
    resp = _call(api_event("GET", "/v1/exercises/no_such_task/resources/Input.zip"))
    assert resp["statusCode"] == 404


def test_download_resource_rejects_path_traversal(aws, evaluator_dirs):
    folder = _make_exercise_with_resource(evaluator_dirs, "api_res_trav")
    (folder / "task.json").write_text("{}", encoding="utf-8")
    from evaluator.tasks import exercise_resource_path

    assert exercise_resource_path("api_res_trav", "..") is None
    assert exercise_resource_path("api_res_trav", "../task.json") is None
    assert exercise_resource_path("api_res_trav", "") is None
    assert exercise_resource_path("../api_res_trav", "Input.zip") is None
