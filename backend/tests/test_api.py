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


# ---------- student registration ----------


def test_register_student_then_duplicate_409(aws):
    # No SnapLogic creds in the test env → the project-existence check is
    # skipped and registration proceeds (credential-less local dev behavior).
    resp = _call(api_event("POST", "/v1/students", body={"student": "New Kid"}))
    assert resp["statusCode"] == 201
    student = _body(resp)["student"]
    assert student["slug"] == "new-kid"
    assert student["display_name"] == "New Kid"
    assert student["registered_by"] == "mentor@example.com"

    # Shows up on the list endpoint with no grades at all.
    listed = _body(_call(api_event("GET", "/v1/students")))["students"]
    assert [s["slug"] for s in listed] == ["new-kid"]
    assert "points_earned" not in listed[0]
    assert "graded_at" not in listed[0]

    dup = _call(api_event("POST", "/v1/students", body={"student": "New Kid"}))
    assert dup["statusCode"] == 409


def test_register_student_requires_name(aws):
    resp = _call(api_event("POST", "/v1/students", body={}))
    assert resp["statusCode"] == 400


def test_registered_student_detail_has_no_report(aws):
    _call(api_event("POST", "/v1/students", body={"student": "Report Less"}))
    resp = _call(api_event("GET", "/v1/students/report-less"))
    assert resp["statusCode"] == 200
    data = _body(resp)
    assert data["student"]["display_name"] == "Report Less"
    assert data["report"] is None
    # No SnapLogic env configured (fixture scrubs it) → path can't be built.
    assert data["student"]["student_project_path"] is None


def test_never_graded_student_detail_has_project_path(aws, monkeypatch):
    from evaluator.snaplogic_client import SnapLogicClient

    _snaplogic_env(monkeypatch)  # TestOrg / IWC_Support
    monkeypatch.setattr(
        SnapLogicClient, "list_assets", lambda self, org, ps, project: []
    )
    _call(api_event("POST", "/v1/students", body={"student": "Path Kid"}))
    resp = _call(api_event("GET", "/v1/students/path-kid"))
    assert resp["statusCode"] == 200
    data = _body(resp)
    assert data["report"] is None
    # Computed server-side so the detail view shows it before the first grade.
    assert (
        data["student"]["student_project_path"] == "TestOrg/IWC_Support/Path Kid"
    )


def _snaplogic_env(monkeypatch):
    """Fake SnapLogic creds so the register route's project check runs."""
    monkeypatch.setenv("SNAPLOGIC_BASE_URL", "https://example.snaplogic.test")
    monkeypatch.setenv("SNAPLOGIC_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("SNAPLOGIC_ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("SNAPLOGIC_ORG_NAME", "TestOrg")
    monkeypatch.setenv("SNAPLOGIC_STUDENT_PROJECT_SPACE", "IWC_Support")


def test_register_student_project_missing_400(aws, monkeypatch):
    import httpx

    from evaluator.snaplogic_client import SnapLogicClient

    _snaplogic_env(monkeypatch)
    seen = {}

    def missing(self, org, ps, project):
        seen["path"] = (org, ps, project)
        req = httpx.Request("GET", "https://example.snaplogic.test/x")
        raise httpx.HTTPStatusError(
            "404", request=req, response=httpx.Response(404, request=req)
        )

    monkeypatch.setattr(SnapLogicClient, "list_assets", missing)
    resp = _call(api_event("POST", "/v1/students", body={"student": "Ghost Kid"}))
    assert resp["statusCode"] == 400
    assert "No project named 'Ghost Kid'" in _body(resp)["message"]
    assert seen["path"] == ("TestOrg", "IWC_Support", "Ghost Kid")
    # Nothing was registered.
    assert _body(_call(api_event("GET", "/v1/students")))["students"] == []


def test_register_student_project_exists_201(aws, monkeypatch):
    from evaluator.snaplogic_client import SnapLogicClient

    _snaplogic_env(monkeypatch)
    monkeypatch.setattr(
        SnapLogicClient, "list_assets", lambda self, org, ps, project: []
    )
    resp = _call(api_event("POST", "/v1/students", body={"student": "Real Kid"}))
    assert resp["statusCode"] == 201


def test_register_student_stores_space_and_project(aws, monkeypatch):
    from evaluator.snaplogic_client import SnapLogicClient

    _snaplogic_env(monkeypatch)
    seen = {}

    def ok(self, org, ps, project):
        seen["path"] = (org, ps, project)
        return []

    monkeypatch.setattr(SnapLogicClient, "list_assets", ok)
    resp = _call(
        api_event(
            "POST",
            "/v1/students",
            body={
                "student": "Custom Kid",
                "space": "Other_Space",
                "project": "Custom Project",
            },
        )
    )
    assert resp["statusCode"] == 201
    student = _body(resp)["student"]
    assert student["space"] == "Other_Space"
    assert student["project"] == "Custom Project"
    # Verification probed the overridden location, not the defaults.
    assert seen["path"] == ("TestOrg", "Other_Space", "Custom Project")


def test_register_student_defaults_space_from_env(aws, monkeypatch):
    from evaluator.snaplogic_client import SnapLogicClient

    _snaplogic_env(monkeypatch)  # SNAPLOGIC_STUDENT_PROJECT_SPACE=IWC_Support
    monkeypatch.setattr(
        SnapLogicClient, "list_assets", lambda self, org, ps, project: []
    )
    resp = _call(api_event("POST", "/v1/students", body={"student": "Env Kid"}))
    assert resp["statusCode"] == 201
    student = _body(resp)["student"]
    # The resolved default is stored on the card so the dashboard column and
    # every later grading run agree on where this student lives.
    assert student["space"] == "IWC_Support"
    assert student["project"] is None


def test_register_student_snaplogic_unreachable_502(aws, monkeypatch):
    import httpx

    from evaluator.snaplogic_client import SnapLogicClient

    _snaplogic_env(monkeypatch)

    def down(self, org, ps, project):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(SnapLogicClient, "list_assets", down)
    resp = _call(api_event("POST", "/v1/students", body={"student": "Any Kid"}))
    assert resp["statusCode"] == 502
    assert _body(_call(api_event("GET", "/v1/students")))["students"] == []


# ---------- student logins (email on registration) + the student role ----------


def _user_pool(monkeypatch):
    """Moto-backed user pool + student group, wired into USER_POOL_ID."""
    import boto3

    cognito = boto3.client("cognito-idp")
    pool_id = cognito.create_user_pool(PoolName="evaluator-test-users")["UserPool"]["Id"]
    cognito.create_group(GroupName="student", UserPoolId=pool_id)
    monkeypatch.setenv("USER_POOL_ID", pool_id)
    return cognito, pool_id


def test_register_student_with_email_creates_login(aws, monkeypatch):
    cognito, pool_id = _user_pool(monkeypatch)
    resp = _call(
        api_event(
            "POST",
            "/v1/students",
            body={"student": "Login Kid", "email": "Login.Kid@Example.com"},
        )
    )
    assert resp["statusCode"] == 201
    assert _body(resp)["student"]["email"] == "login.kid@example.com"
    user = cognito.admin_get_user(
        UserPoolId=pool_id, Username="login.kid@example.com"
    )
    attrs = {a["Name"]: a["Value"] for a in user["UserAttributes"]}
    assert attrs["email"] == "login.kid@example.com"
    assert attrs["email_verified"] == "true"
    assert attrs["name"] == "Login Kid"
    groups = cognito.admin_list_groups_for_user(
        Username="login.kid@example.com", UserPoolId=pool_id
    )["Groups"]
    assert [g["GroupName"] for g in groups] == ["student"]
    # The card carries the email so delete_student can remove the login later.
    listed = _body(_call(api_event("GET", "/v1/students")))["students"]
    assert listed[0]["email"] == "login.kid@example.com"


def test_register_student_without_email_creates_no_login(aws, monkeypatch):
    cognito, pool_id = _user_pool(monkeypatch)
    resp = _call(api_event("POST", "/v1/students", body={"student": "Offline Kid"}))
    assert resp["statusCode"] == 201
    assert cognito.list_users(UserPoolId=pool_id)["Users"] == []


def test_register_student_invalid_email_400(aws, monkeypatch):
    _user_pool(monkeypatch)
    resp = _call(
        api_event(
            "POST", "/v1/students", body={"student": "Typo Kid", "email": "not-an-email"}
        )
    )
    assert resp["statusCode"] == 400
    assert _body(_call(api_event("GET", "/v1/students")))["students"] == []


def test_register_student_email_without_pool_503(aws, monkeypatch):
    monkeypatch.delenv("USER_POOL_ID", raising=False)
    resp = _call(
        api_event(
            "POST",
            "/v1/students",
            body={"student": "Pool Less", "email": "kid@example.com"},
        )
    )
    assert resp["statusCode"] == 503
    # The request fails as a unit — the card put was rolled back.
    assert _body(_call(api_event("GET", "/v1/students")))["students"] == []


def test_register_student_duplicate_login_409_rolls_back_card(aws, monkeypatch):
    cognito, pool_id = _user_pool(monkeypatch)
    cognito.admin_create_user(
        UserPoolId=pool_id,
        Username="taken@example.com",
        UserAttributes=[{"Name": "email", "Value": "taken@example.com"}],
    )
    resp = _call(
        api_event(
            "POST",
            "/v1/students",
            body={"student": "Second Owner", "email": "taken@example.com"},
        )
    )
    assert resp["statusCode"] == 409
    assert _body(_call(api_event("GET", "/v1/students")))["students"] == []


def test_delete_student_removes_login(aws, monkeypatch):
    cognito, pool_id = _user_pool(monkeypatch)
    _call(
        api_event(
            "POST",
            "/v1/students",
            body={"student": "Gone Kid", "email": "gone@example.com"},
        )
    )
    resp = _call(api_event("DELETE", "/v1/students/gone-kid", groups=("admin",)))
    assert resp["statusCode"] == 200
    assert _body(resp)["deleted"]["login"] is True
    assert cognito.list_users(UserPoolId=pool_id)["Users"] == []


def test_student_role_is_read_only(aws, evaluator_dirs):
    # The reads the student dashboard needs all answer.
    for path in ("/v1/students", "/v1/exercises"):
        resp = _call(api_event("GET", path, groups=("student",)))
        assert resp["statusCode"] == 200, path
    # Every action — and reads that carry instructor-only data — is 403.
    denied = [
        ("GET", "/v1/config", None),
        ("GET", "/v1/exercises/some-slug", None),  # authored content incl. notes.md
        ("GET", "/v1/gradings/some-id", None),
        ("POST", "/v1/students", {"student": "X"}),
        ("POST", "/v1/gradings", {"student": "X"}),
        ("POST", "/v1/preps", {}),
        ("POST", "/v1/exercises", {}),
        ("PATCH", "/v1/students/x/report", {"overall_summary": "hi"}),
        ("DELETE", "/v1/students/x", None),
        ("DELETE", "/v1/exercises/x", None),
    ]
    for method, path, body in denied:
        resp = _call(api_event(method, path, groups=("student",), body=body))
        assert resp["statusCode"] == 403, f"{method} {path}"


# ---------- config ----------


def test_get_config_returns_non_secret_settings(aws, monkeypatch):
    monkeypatch.setenv("SNAPLOGIC_ORG_NAME", "TestOrg")
    monkeypatch.setenv("SNAPLOGIC_STUDENT_PROJECT_SPACE", "Training_Space")
    resp = _call(api_event("GET", "/v1/config"))
    assert resp["statusCode"] == 200
    cfg = _body(resp)["config"]
    assert cfg["org_name"] == "TestOrg"
    assert cfg["student_project_space"] == "Training_Space"
    assert cfg["solution_project_space"] is None
    # Credentials never appear, not even as keys.
    assert "SNAPLOGIC_ADMIN_PASSWORD" not in json.dumps(cfg)
    assert "password" not in json.dumps(cfg).lower()


# ---------- gradings ----------


def test_post_grading_uses_registered_space_and_project(aws):
    dynamo_table().put_item(
        Item={
            "pk": "STUDENT#card-kid",
            "sk": "META",
            "entity": "student",
            "slug": "card-kid",
            "display_name": "Card Kid",
            "space": "Space_Y",
            "project": "Project_Y",
        }
    )
    resp = _call(api_event("POST", "/v1/gradings", body={"student": "Card Kid"}))
    assert resp["statusCode"] == 202
    messages = aws["sqs"].receive_message(QueueUrl=os.environ["QUEUE_URL"])["Messages"]
    payload = json.loads(messages[0]["Body"])
    assert payload["space"] == "Space_Y"
    assert payload["project"] == "Project_Y"


def test_post_grading_body_space_overrides_card(aws):
    dynamo_table().put_item(
        Item={
            "pk": "STUDENT#override-kid",
            "sk": "META",
            "entity": "student",
            "slug": "override-kid",
            "display_name": "Override Kid",
            "space": "Card_Space",
        }
    )
    resp = _call(
        api_event(
            "POST",
            "/v1/gradings",
            body={"student": "Override Kid", "space": "One_Off_Space"},
        )
    )
    assert resp["statusCode"] == 202
    messages = aws["sqs"].receive_message(QueueUrl=os.environ["QUEUE_URL"])["Messages"]
    payload = json.loads(messages[0]["Body"])
    assert payload["space"] == "One_Off_Space"


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


def test_post_grading_with_unknown_task_400(aws, evaluator_dirs):
    resp = _call(
        api_event(
            "POST", "/v1/gradings", body={"student": "Jane Doe", "task": "no_such_task"}
        )
    )
    assert resp["statusCode"] == 400
    assert "no_such_task" in _body(resp)["message"]


def test_post_grading_with_task_passes_it_to_the_queue(aws, evaluator_dirs):
    (evaluator_dirs["exercises"] / "api_grade_task").mkdir(exist_ok=True)

    resp = _call(
        api_event(
            "POST", "/v1/gradings", body={"student": "Task Ed", "task": "api_grade_task"}
        )
    )
    assert resp["statusCode"] == 202
    messages = aws["sqs"].receive_message(QueueUrl=os.environ["QUEUE_URL"])["Messages"]
    payload = json.loads(messages[0]["Body"])
    assert payload["task"] == "api_grade_task"
    assert payload["student"] == "Task Ed"


def test_post_grading_with_tasks_subset_dedupes_and_queues(aws, evaluator_dirs):
    for slug in ("api_sub_a", "api_sub_b"):
        (evaluator_dirs["exercises"] / slug).mkdir(exist_ok=True)

    resp = _call(
        api_event(
            "POST",
            "/v1/gradings",
            body={"student": "Sub Set", "tasks": ["api_sub_a", "api_sub_b", "api_sub_a"]},
        )
    )
    assert resp["statusCode"] == 202
    messages = aws["sqs"].receive_message(QueueUrl=os.environ["QUEUE_URL"])["Messages"]
    payload = json.loads(messages[0]["Body"])
    assert payload["tasks"] == ["api_sub_a", "api_sub_b"]
    assert payload["task"] is None


def test_post_grading_single_element_tasks_collapses_to_task(aws, evaluator_dirs):
    (evaluator_dirs["exercises"] / "api_sub_c").mkdir(exist_ok=True)

    resp = _call(
        api_event(
            "POST", "/v1/gradings", body={"student": "Sub One", "tasks": ["api_sub_c"]}
        )
    )
    assert resp["statusCode"] == 202
    messages = aws["sqs"].receive_message(QueueUrl=os.environ["QUEUE_URL"])["Messages"]
    payload = json.loads(messages[0]["Body"])
    assert payload["task"] == "api_sub_c"
    assert payload["tasks"] is None


def test_post_grading_tasks_with_unknown_slug_400(aws, evaluator_dirs):
    (evaluator_dirs["exercises"] / "api_sub_d").mkdir(exist_ok=True)

    resp = _call(
        api_event(
            "POST",
            "/v1/gradings",
            body={"student": "Sub Bad", "tasks": ["api_sub_d", "nope_task"]},
        )
    )
    assert resp["statusCode"] == 400
    assert "nope_task" in _body(resp)["message"]


def test_post_grading_task_and_tasks_together_400(aws, evaluator_dirs):
    (evaluator_dirs["exercises"] / "api_sub_e").mkdir(exist_ok=True)

    resp = _call(
        api_event(
            "POST",
            "/v1/gradings",
            body={"student": "Sub Both", "task": "api_sub_e", "tasks": ["api_sub_e"]},
        )
    )
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


def test_get_student_reads_legacy_report_json_attribute(aws):
    # Students graded before the store labels were fixed carry the report S3
    # key under "report_json" instead of "report_json_key".
    aws["s3"].put_object(
        Bucket=os.environ["DATA_BUCKET"],
        Key="students/old-grad/v1/report.json",
        Body=json.dumps({"points_earned": 40}).encode(),
    )
    dynamo_table().put_item(
        Item={
            "pk": "STUDENT#old-grad",
            "sk": "META",
            "entity": "student",
            "slug": "old-grad",
            "display_name": "Old Grad",
            "report_json": "students/old-grad/v1/report.json",
        }
    )
    resp = _call(api_event("GET", "/v1/students/old-grad"))
    assert resp["statusCode"] == 200
    assert _body(resp)["report"]["points_earned"] == 40


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


def test_download_resource_tolerates_head_403(aws, evaluator_dirs, monkeypatch):
    """Without s3:ListBucket, S3 masks HeadObject-on-missing-key as 403; the
    mirror must fall through to the upload instead of surfacing a 500."""
    from botocore.exceptions import ClientError

    _make_exercise_with_resource(evaluator_dirs, "api_res_403", content=b"data-403")
    real = api.s3_client()

    class HeadForbiddenS3:
        def head_object(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
            )

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(api, "s3_client", lambda: HeadForbiddenS3())
    resp = _call(api_event("GET", "/v1/exercises/api_res_403/resources/Input.zip"))
    assert resp["statusCode"] == 200
    obj = aws["s3"].get_object(
        Bucket=os.environ["DATA_BUCKET"], Key="exercise-resources/api_res_403/Input.zip"
    )
    assert obj["Body"].read() == b"data-403"


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


# ---------- creating exercises from the UI (S3-authored) ----------


def _create_exercise(slug: str, *, description=None, notes=None, resources=None):
    body = {
        "slug": slug,
        "description_md": description
        if description is not None
        else f"# Task 99 – {slug}\n\n### Objective:\n\nBuild it.\n",
    }
    if notes is not None:
        body["notes_md"] = notes
    if resources is not None:
        body["resources"] = resources
    return _call(api_event("POST", "/v1/exercises", groups=("admin",), body=body))


def test_mentor_cannot_create_exercise_403(aws):
    resp = _call(
        api_event("POST", "/v1/exercises", groups=("mentor",), body={"slug": "x"})
    )
    assert resp["statusCode"] == 403


def test_create_exercise_writes_s3_dynamo_and_presigns_uploads(aws, evaluator_dirs):
    resp = _create_exercise(
        "api_ui_create",
        description="# Task 42 – UI Made\n\nDo the thing.\n",
        notes="Judge gently.",
        resources=[{"filename": "Input.zip"}],
    )
    assert resp["statusCode"] == 201
    data = _body(resp)
    assert data["exercise"]["title"] == "Task 42 – UI Made"
    assert data["uploads"][0]["filename"] == "Input.zip"
    assert "exercises/api_ui_create/resources/Input.zip" in data["uploads"][0]["url"]

    bucket = os.environ["DATA_BUCKET"]
    desc = aws["s3"].get_object(Bucket=bucket, Key="exercises/api_ui_create/description.md")
    assert b"Do the thing." in desc["Body"].read()
    notes = aws["s3"].get_object(Bucket=bucket, Key="exercises/api_ui_create/notes.md")
    assert notes["Body"].read() == b"Judge gently."

    item = dynamo_table().get_item(
        Key={"pk": "EXERCISE#api_ui_create", "sk": "META"}
    )["Item"]
    assert item["prep_status"] == "never_prepped"
    assert item["authored_in"] == "s3"
    assert item["created_by"] == "mentor@example.com"


def test_create_exercise_validation_400(aws, evaluator_dirs):
    assert _create_exercise("Bad Slug!")["statusCode"] == 400
    assert _create_exercise("api_ui_val", description="   ")["statusCode"] == 400
    assert _create_exercise("api_ui_val", description="no heading here")["statusCode"] == 400
    assert (
        _create_exercise("api_ui_val", resources=[{"filename": "../evil.zip"}])["statusCode"]
        == 400
    )
    assert (
        _create_exercise(
            "api_ui_val", resources=[{"filename": "a.zip"}, {"filename": "a.zip"}]
        )["statusCode"]
        == 400
    )


def test_create_exercise_duplicate_slug_409(aws, evaluator_dirs):
    (evaluator_dirs["exercises"] / "api_ui_dup_image").mkdir(exist_ok=True)
    assert _create_exercise("api_ui_dup_image")["statusCode"] == 409

    assert _create_exercise("api_ui_dup_s3")["statusCode"] == 201
    assert _create_exercise("api_ui_dup_s3")["statusCode"] == 409


def test_list_exercises_includes_s3_authored(aws, evaluator_dirs):
    _create_exercise("api_ui_list", description="# Task 43 – From The UI\n\nSteps.\n")
    aws["s3"].put_object(
        Bucket=os.environ["DATA_BUCKET"],
        Key="exercises/api_ui_list/resources/Data.csv",
        Body=b"a,b\n1,2\n",
    )
    # Prep-generated artifacts in the same prefix must not create phantom rows.
    aws["s3"].put_object(
        Bucket=os.environ["DATA_BUCKET"],
        Key="exercises/api_ui_ghost/solution.json",
        Body=b"{}",
    )
    resp = _call(api_event("GET", "/v1/exercises"))
    exercises = {e["slug"]: e for e in _body(resp)["exercises"]}
    entry = exercises["api_ui_list"]
    assert entry["title"] == "Task 43 – From The UI"
    assert "Steps." in entry["description"]
    assert entry["resources"] == [{"filename": "Data.csv", "size_bytes": 8}]
    assert "missing_from_image" not in entry
    assert "api_ui_ghost" not in exercises


def test_download_s3_authored_resource(aws, evaluator_dirs):
    _create_exercise("api_ui_dl")
    aws["s3"].put_object(
        Bucket=os.environ["DATA_BUCKET"],
        Key="exercises/api_ui_dl/resources/Input.zip",
        Body=b"zip-bytes",
    )
    resp = _call(api_event("GET", "/v1/exercises/api_ui_dl/resources/Input.zip"))
    assert resp["statusCode"] == 200
    assert "exercises/api_ui_dl/resources/Input.zip" in _body(resp)["url"]

    resp = _call(api_event("GET", "/v1/exercises/api_ui_dl/resources/nope.csv"))
    assert resp["statusCode"] == 404


def test_list_exercises_merges_s3_authored_into_bare_image_folder(aws, evaluator_dirs):
    """UI-authored exercise whose task.json was later committed to git: the
    image folder holds only task.json, description/resources stay in S3."""
    _create_exercise("api_ui_hybrid", description="# Task 44 – Hybrid\n\nDetails.\n")
    aws["s3"].put_object(
        Bucket=os.environ["DATA_BUCKET"],
        Key="exercises/api_ui_hybrid/resources/Input.zip",
        Body=b"12345",
    )
    folder = evaluator_dirs["exercises"] / "api_ui_hybrid"
    folder.mkdir(exist_ok=True)
    (folder / "task.json").write_text("{}", encoding="utf-8")

    resp = _call(api_event("GET", "/v1/exercises"))
    exercises = {e["slug"]: e for e in _body(resp)["exercises"]}
    entry = exercises["api_ui_hybrid"]
    assert entry["title"] == "Task 44 – Hybrid"
    assert "Details." in entry["description"]
    assert entry["resources"] == [{"filename": "Input.zip", "size_bytes": 5}]

    dl = _call(api_event("GET", "/v1/exercises/api_ui_hybrid/resources/Input.zip"))
    assert dl["statusCode"] == 200
    assert "exercises/api_ui_hybrid/resources/Input.zip" in _body(dl)["url"]


def test_prep_and_grade_accept_s3_authored_slug(aws, evaluator_dirs):
    _create_exercise("api_ui_prep")
    resp = _call(
        api_event("POST", "/v1/preps", groups=("admin",), body={"slug": "api_ui_prep"})
    )
    assert resp["statusCode"] == 202
    resp = _call(
        api_event(
            "POST", "/v1/gradings", body={"student": "S3 Authored", "task": "api_ui_prep"}
        )
    )
    assert resp["statusCode"] == 202


# ---------- structured task config ----------


def test_create_exercise_stores_task_config(aws, evaluator_dirs):
    resp = _call(
        api_event(
            "POST",
            "/v1/exercises",
            groups=("admin",),
            body={
                "slug": "api_cfg_create",
                "description_md": "# Task 50 – Configured\n\nGo.\n",
                "task_config": {
                    "task_type": "triggered_task",
                    "triggered_task_name": "Task 50 – Configured Task",
                    "requests": [
                        {"name": "addition", "params": {"mathOperation": "3+5"}},
                        {"name": "subtraction", "params": {"mathOperation": "10-4"}},
                    ],
                },
            },
        )
    )
    assert resp["statusCode"] == 201
    item = dynamo_table().get_item(Key={"pk": "EXERCISE#api_cfg_create", "sk": "META"})[
        "Item"
    ]
    assert item["task_config"]["task_type"] == "triggered_task"
    assert [r["name"] for r in item["task_config"]["requests"]] == [
        "addition",
        "subtraction",
    ]


def test_create_exercise_task_config_validation_400(aws, evaluator_dirs):
    def create(cfg):
        return _call(
            api_event(
                "POST",
                "/v1/exercises",
                groups=("admin",),
                body={
                    "slug": "api_cfg_bad",
                    "description_md": "# Task 51 – Bad Config",
                    "task_config": cfg,
                },
            )
        )["statusCode"]

    assert create({"task_type": "mystery"}) == 400
    assert create({"task_type": "file_writer"}) == 400  # no output_filenames
    assert create({"task_type": "file_writer", "output_filenames": ["../x.csv"]}) == 400
    assert (
        create(
            {"task_type": "file_writer", "output_filenames": ["a.csv"], "output_match_mode": "fuzzy"}
        )
        == 400
    )
    assert create({"task_type": "triggered_task", "triggered_task_name": "T"}) == 400
    assert (
        create(
            {
                "task_type": "triggered_task",
                "triggered_task_name": "T",
                "requests": [{"name": "Bad Name!", "params": {}}],
            }
        )
        == 400
    )


# ---------- GET /v1/exercises/{slug} + PUT (edit / archive) ----------


def test_get_single_exercise_returns_authored_content(aws, evaluator_dirs):
    _create_exercise(
        "api_get_one",
        description="# Task 60 – Detail\n\nBody.\n",
        notes="Hints here.",
    )
    aws["s3"].put_object(
        Bucket=os.environ["DATA_BUCKET"],
        Key="exercises/api_get_one/resources/Data.csv",
        Body=b"a\n1\n",
    )
    resp = _call(api_event("GET", "/v1/exercises/api_get_one"))
    assert resp["statusCode"] == 200
    ex = _body(resp)["exercise"]
    assert ex["title"] == "Task 60 – Detail"
    assert "Body." in ex["description_md"]
    assert ex["notes_md"] == "Hints here."
    assert ex["resources"] == [{"filename": "Data.csv", "size_bytes": 4}]

    assert _call(api_event("GET", "/v1/exercises/no_such_slug"))["statusCode"] == 404


def test_mentor_cannot_edit_exercise_403(aws, evaluator_dirs):
    resp = _call(
        api_event("PUT", "/v1/exercises/anything", groups=("mentor",), body={})
    )
    assert resp["statusCode"] == 403


def test_put_exercise_updates_content_config_and_resources(aws, evaluator_dirs):
    _create_exercise("api_put_edit", description="# Task 61 – Before\n\nOld.\n")
    bucket = os.environ["DATA_BUCKET"]
    aws["s3"].put_object(
        Bucket=bucket, Key="exercises/api_put_edit/resources/Old.zip", Body=b"old"
    )

    resp = _call(
        api_event(
            "PUT",
            "/v1/exercises/api_put_edit",
            groups=("admin",),
            body={
                "description_md": "# Task 61 – After\n\nNew.\n",
                "notes_md": "New notes.",
                "task_config": {
                    "task_type": "file_writer",
                    "output_filenames": ["R1.csv", "R2.csv"],
                },
                "resources": [{"filename": "New.zip"}],
                "remove_resources": ["Old.zip"],
            },
        )
    )
    assert resp["statusCode"] == 200
    data = _body(resp)
    assert data["exercise"]["title"] == "Task 61 – After"
    assert data["exercise"]["task_config"]["output_filenames"] == ["R1.csv", "R2.csv"]
    assert "exercises/api_put_edit/resources/New.zip" in data["uploads"][0]["url"]

    desc = aws["s3"].get_object(Bucket=bucket, Key="exercises/api_put_edit/description.md")
    assert b"New." in desc["Body"].read()
    notes = aws["s3"].get_object(Bucket=bucket, Key="exercises/api_put_edit/notes.md")
    assert notes["Body"].read() == b"New notes."
    listed = aws["s3"].list_objects_v2(
        Bucket=bucket, Prefix="exercises/api_put_edit/resources/"
    )
    assert listed.get("KeyCount", 0) == 0  # Old.zip removed, New.zip not uploaded yet

    # Clearing the config (back to auto) removes the attribute.
    resp = _call(
        api_event(
            "PUT", "/v1/exercises/api_put_edit", groups=("admin",),
            body={"task_config": None},
        )
    )
    assert resp["statusCode"] == 200
    item = dynamo_table().get_item(Key={"pk": "EXERCISE#api_put_edit", "sk": "META"})[
        "Item"
    ]
    assert "task_config" not in item
    assert item["title"] == "Task 61 – After"

    assert (
        _call(api_event("PUT", "/v1/exercises/no_such_slug", groups=("admin",), body={}))[
            "statusCode"
        ]
        == 404
    )


def test_archived_exercise_blocks_prep_and_single_task_grade(aws, evaluator_dirs):
    _create_exercise("api_arch_guard")
    resp = _call(
        api_event(
            "PUT", "/v1/exercises/api_arch_guard", groups=("admin",),
            body={"archived": True},
        )
    )
    assert resp["statusCode"] == 200

    resp = _call(
        api_event(
            "POST", "/v1/preps", groups=("admin",), body={"slug": "api_arch_guard"}
        )
    )
    assert resp["statusCode"] == 400
    assert "archived" in _body(resp)["message"]
    resp = _call(
        api_event(
            "POST", "/v1/gradings", body={"student": "Arch", "task": "api_arch_guard"}
        )
    )
    assert resp["statusCode"] == 400

    # The listing still shows it, flagged.
    exercises = {
        e["slug"]: e
        for e in _body(_call(api_event("GET", "/v1/exercises")))["exercises"]
    }
    assert exercises["api_arch_guard"]["archived"] is True

    # Unarchive restores prep.
    _call(
        api_event(
            "PUT", "/v1/exercises/api_arch_guard", groups=("admin",),
            body={"archived": False},
        )
    )
    resp = _call(
        api_event(
            "POST", "/v1/preps", groups=("admin",), body={"slug": "api_arch_guard"}
        )
    )
    assert resp["statusCode"] == 202


# ---------- PATCH /v1/students/{slug}/report (edit AI-written text) ----------


def _seed_student_report(aws, slug="edit-me", name="Edit Me"):
    report = {
        "student": name,
        "counts": {"pass": 1, "fail": 0, "missing": 1, "needs_prep": 0, "total": 2},
        "points_earned": 10,
        "points_possible": 20,
        "overall_summary": "AI overall text.",
        "tasks": [
            {
                "slug": "task_a",
                "status": "evaluated",
                "verdict": "pass",
                "points": 10,
                "summary": "AI summary for task_a.",
            },
            {"slug": "task_b", "status": "missing", "verdict": None, "reason": "Not found."},
        ],
    }
    json_key = f"students/{slug}/v1/report.json"
    md_key = f"students/{slug}/v1/report.md"
    bucket = os.environ["DATA_BUCKET"]
    aws["s3"].put_object(Bucket=bucket, Key=json_key, Body=json.dumps(report).encode())
    aws["s3"].put_object(
        Bucket=bucket,
        Key=md_key,
        Body=(
            "# Grading Report\n\n## Overall\n\nAI overall text.\n\n---\n\n"
            "## task_a\n\nAI summary for task_a.\n"
        ).encode("utf-8"),
    )
    dynamo_table().put_item(
        Item={
            "pk": f"STUDENT#{slug}",
            "sk": "META",
            "entity": "student",
            "slug": slug,
            "display_name": name,
            "overall_summary": "AI overall text.",
            "report_json_key": json_key,
            "report_md_key": md_key,
        }
    )
    return slug, json_key, md_key


def test_mentor_edits_overall_summary(aws):
    slug, json_key, md_key = _seed_student_report(aws)
    resp = _call(
        api_event(
            "PATCH",
            f"/v1/students/{slug}/report",
            groups=("mentor",),
            email="m@x.io",
            body={"overall_summary": "Human-corrected overall."},
        )
    )
    assert resp["statusCode"] == 200
    data = _body(resp)
    assert data["report"]["overall_summary"] == "Human-corrected overall."
    assert data["student"]["overall_summary"] == "Human-corrected overall."

    bucket = os.environ["DATA_BUCKET"]
    stored = json.loads(aws["s3"].get_object(Bucket=bucket, Key=json_key)["Body"].read())
    assert stored["overall_summary"] == "Human-corrected overall."
    assert stored["overall_summary_edited_by"] == "m@x.io"
    # report.md's Overall paragraph is rewritten too; the task section is not.
    md = aws["s3"].get_object(Bucket=bucket, Key=md_key)["Body"].read().decode()
    assert "Human-corrected overall." in md
    assert "AI overall text." not in md
    assert "AI summary for task_a." in md
    # Denormalized student card refreshed + edit stamped.
    item = dynamo_table().get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"})["Item"]
    assert item["overall_summary"] == "Human-corrected overall."
    assert item["report_edited_by"] == "m@x.io"


def test_edit_task_summary_touches_only_that_task(aws):
    slug, json_key, _ = _seed_student_report(aws, slug="edit-task", name="Edit Task")
    resp = _call(
        api_event(
            "PATCH",
            f"/v1/students/{slug}/report",
            body={"task": "task_b", "summary": "Mentor explanation instead."},
        )
    )
    assert resp["statusCode"] == 200
    stored = json.loads(
        aws["s3"].get_object(Bucket=os.environ["DATA_BUCKET"], Key=json_key)["Body"].read()
    )
    by_slug = {t["slug"]: t for t in stored["tasks"]}
    assert by_slug["task_b"]["summary"] == "Mentor explanation instead."
    assert by_slug["task_b"]["summary_edited_by"] == "mentor@example.com"
    assert by_slug["task_a"]["summary"] == "AI summary for task_a."
    # Verdict/points untouched, overall untouched.
    assert by_slug["task_b"]["status"] == "missing"
    assert stored["overall_summary"] == "AI overall text."
    assert stored["points_earned"] == 10


def test_edit_report_validation(aws):
    slug, _, _ = _seed_student_report(aws, slug="edit-val", name="Edit Val")

    def patch(body, slug=slug):
        return _call(api_event("PATCH", f"/v1/students/{slug}/report", body=body))

    assert patch({})["statusCode"] == 400  # nothing to apply
    assert patch({"overall_summary": "   "})["statusCode"] == 400
    assert patch({"task": "task_a"})["statusCode"] == 400  # summary missing
    assert patch({"task": "", "summary": "x"})["statusCode"] == 400
    assert patch({"task": "no_such_task", "summary": "x"})["statusCode"] == 404
    assert patch({"overall_summary": "x"}, slug="no-such-student")["statusCode"] == 404


def test_edit_report_without_stored_report_400(aws):
    dynamo_table().put_item(
        Item={
            "pk": "STUDENT#no-report",
            "sk": "META",
            "entity": "student",
            "slug": "no-report",
            "display_name": "No Report",
        }
    )
    resp = _call(
        api_event(
            "PATCH", "/v1/students/no-report/report", body={"overall_summary": "x"}
        )
    )
    assert resp["statusCode"] == 400


def test_list_exercises_s3_description_wins_over_image_copy(aws, evaluator_dirs):
    """After a UI edit the S3 copy is canonical — a stale image copy of the
    same folder must not shadow it."""
    folder = evaluator_dirs["exercises"] / "api_s3_wins"
    folder.mkdir(exist_ok=True)
    (folder / "description.md").write_text("# Task 62 – Stale Image\n", encoding="utf-8")
    aws["s3"].put_object(
        Bucket=os.environ["DATA_BUCKET"],
        Key="exercises/api_s3_wins/description.md",
        Body="# Task 62 – Edited In UI\n\nFresh.\n".encode("utf-8"),
    )
    resp = _call(api_event("GET", "/v1/exercises"))
    exercises = {e["slug"]: e for e in _body(resp)["exercises"]}
    assert exercises["api_s3_wins"]["title"] == "Task 62 – Edited In UI"
    assert "Fresh." in exercises["api_s3_wins"]["description"]


# ---------- hard deletes (admin only) ----------


def _seed_student_jobs_and_lock(slug: str, *, expired: bool = True):
    """A grade JOB row + lock for the student, as a finished run leaves them."""
    import time

    dynamo_table().put_item(
        Item={
            "pk": "JOB#job-for-" + slug,
            "sk": "META",
            "entity": "job",
            "slug": "job-for-" + slug,
            "job_id": "job-for-" + slug,
            "job_type": "grade",
            "status": "succeeded",
            "target": slug,
        }
    )
    dynamo_table().put_item(
        Item={
            "pk": f"LOCK#grade#{slug}",
            "sk": "META",
            "job_id": "job-for-" + slug,
            "ttl": int(time.time()) + (-100 if expired else 600),
        }
    )


def test_mentor_cannot_delete_student_403(aws):
    _seed_student_report(aws, slug="del-403", name="Del Fourothree")
    resp = _call(api_event("DELETE", "/v1/students/del-403", groups=("mentor",)))
    assert resp["statusCode"] == 403
    # Nothing was touched.
    assert dynamo_table().get_item(Key={"pk": "STUDENT#del-403", "sk": "META"}).get("Item")


def test_delete_unknown_student_404(aws):
    resp = _call(api_event("DELETE", "/v1/students/nobody", groups=("admin",)))
    assert resp["statusCode"] == 404


def test_delete_student_purges_rows_jobs_and_s3(aws):
    slug, json_key, md_key = _seed_student_report(aws, slug="del-me", name="Del Me")
    dynamo_table().put_item(
        Item={
            "pk": f"STUDENT#{slug}",
            "sk": "REPORT#2026-01-01T00:00:00Z",
            "version": "2026-01-01T00:00:00Z",
        }
    )
    _seed_student_jobs_and_lock(slug)

    resp = _call(api_event("DELETE", f"/v1/students/{slug}", groups=("admin",)))
    assert resp["statusCode"] == 200
    deleted = _body(resp)["deleted"]
    assert deleted["student"] == slug
    assert deleted["rows"] == 2  # META + one REPORT row
    assert deleted["jobs"] == 1
    assert deleted["objects"] >= 2  # report.json + report.md (all versions)

    table = dynamo_table()
    assert "Item" not in table.get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"})
    assert "Item" not in table.get_item(
        Key={"pk": f"STUDENT#{slug}", "sk": "REPORT#2026-01-01T00:00:00Z"}
    )
    assert "Item" not in table.get_item(Key={"pk": "JOB#job-for-" + slug, "sk": "META"})
    assert "Item" not in table.get_item(Key={"pk": f"LOCK#grade#{slug}", "sk": "META"})
    bucket = os.environ["DATA_BUCKET"]
    listed = aws["s3"].list_object_versions(Bucket=bucket, Prefix=f"students/{slug}/")
    assert not listed.get("Versions") and not listed.get("DeleteMarkers")
    # Gone from the dashboard list too.
    assert _body(_call(api_event("GET", "/v1/students")))["students"] == []


def test_delete_student_with_active_grade_lock_409(aws):
    slug, *_ = _seed_student_report(aws, slug="del-busy", name="Del Busy")
    _seed_student_jobs_and_lock(slug, expired=False)
    resp = _call(api_event("DELETE", f"/v1/students/{slug}", groups=("admin",)))
    assert resp["statusCode"] == 409
    assert dynamo_table().get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"}).get("Item")


def test_mentor_cannot_delete_exercise_403(aws, evaluator_dirs):
    _create_exercise("api_del_role")
    resp = _call(api_event("DELETE", "/v1/exercises/api_del_role", groups=("mentor",)))
    assert resp["statusCode"] == 403


def test_delete_unknown_exercise_404(aws, evaluator_dirs):
    resp = _call(api_event("DELETE", "/v1/exercises/api_del_ghost", groups=("admin",)))
    assert resp["statusCode"] == 404


def test_delete_s3_authored_exercise_purges_everything(aws, evaluator_dirs):
    _create_exercise("api_del_s3")
    bucket = os.environ["DATA_BUCKET"]
    aws["s3"].put_object(
        Bucket=bucket, Key="exercises/api_del_s3/resources/Input.zip", Body=b"zip"
    )
    aws["s3"].put_object(
        Bucket=bucket, Key="exercises/api_del_s3/solution.json", Body=b"{}"
    )
    aws["s3"].put_object(
        Bucket=bucket, Key="exercise-resources/api_del_s3/Input.zip", Body=b"zip"
    )

    resp = _call(api_event("DELETE", "/v1/exercises/api_del_s3", groups=("admin",)))
    assert resp["statusCode"] == 200
    deleted = _body(resp)["deleted"]
    assert deleted["tombstoned"] is False

    # No row, no S3 objects, gone from the listing, 404 on direct GET.
    assert "Item" not in dynamo_table().get_item(
        Key={"pk": "EXERCISE#api_del_s3", "sk": "META"}
    )
    for prefix in ("exercises/api_del_s3/", "exercise-resources/api_del_s3/"):
        listed = aws["s3"].list_object_versions(Bucket=bucket, Prefix=prefix)
        assert not listed.get("Versions") and not listed.get("DeleteMarkers")
    slugs = {e["slug"] for e in _body(_call(api_event("GET", "/v1/exercises")))["exercises"]}
    assert "api_del_s3" not in slugs
    assert _call(api_event("GET", "/v1/exercises/api_del_s3"))["statusCode"] == 404


def test_delete_image_shipped_exercise_leaves_tombstone(aws, evaluator_dirs):
    folder = evaluator_dirs["exercises"] / "api_del_img"
    folder.mkdir(exist_ok=True)
    (folder / "description.md").write_text("# Task 77 – Baked In\n", encoding="utf-8")

    resp = _call(api_event("DELETE", "/v1/exercises/api_del_img", groups=("admin",)))
    assert resp["statusCode"] == 200
    assert _body(resp)["deleted"]["tombstoned"] is True

    item = dynamo_table().get_item(Key={"pk": "EXERCISE#api_del_img", "sk": "META"})["Item"]
    assert item["deleted"] is True

    # The image folder must not resurrect it anywhere.
    slugs = {e["slug"] for e in _body(_call(api_event("GET", "/v1/exercises")))["exercises"]}
    assert "api_del_img" not in slugs
    assert _call(api_event("GET", "/v1/exercises/api_del_img"))["statusCode"] == 404
    resp = _call(
        api_event("POST", "/v1/preps", groups=("admin",), body={"slug": "api_del_img"})
    )
    assert resp["statusCode"] == 400 and "deleted" in _body(resp)["message"]
    resp = _call(
        api_event("POST", "/v1/gradings", body={"student": "X", "task": "api_del_img"})
    )
    assert resp["statusCode"] == 400
    resp = _call(
        api_event(
            "PUT", "/v1/exercises/api_del_img", groups=("admin",),
            body={"archived": True},
        )
    )
    assert resp["statusCode"] == 404
    # Deleting it again is a 404, not a second purge.
    resp = _call(api_event("DELETE", "/v1/exercises/api_del_img", groups=("admin",)))
    assert resp["statusCode"] == 404

    # Re-creating the slug replaces the tombstone with a fresh exercise.
    resp = _create_exercise("api_del_img", description="# Task 77 – Reborn\n\nAgain.\n")
    assert resp["statusCode"] == 201
    item = dynamo_table().get_item(Key={"pk": "EXERCISE#api_del_img", "sk": "META"})["Item"]
    assert "deleted" not in item and item["title"] == "Task 77 – Reborn"


def test_delete_tombstoned_exercise_blocks_resource_download(aws, evaluator_dirs):
    folder = evaluator_dirs["exercises"] / "api_del_res"
    (folder / "resources").mkdir(parents=True, exist_ok=True)
    (folder / "description.md").write_text("# Task 78 – Res\n", encoding="utf-8")
    (folder / "resources" / "Input.zip").write_bytes(b"zip-bytes")

    _call(api_event("DELETE", "/v1/exercises/api_del_res", groups=("admin",)))
    resp = _call(api_event("GET", "/v1/exercises/api_del_res/resources/Input.zip"))
    assert resp["statusCode"] == 404
    # The lazy image-to-S3 mirror must not have run.
    listed = aws["s3"].list_object_versions(
        Bucket=os.environ["DATA_BUCKET"], Prefix="exercise-resources/api_del_res/"
    )
    assert not listed.get("Versions") and not listed.get("DeleteMarkers")


def test_delete_exercise_with_active_prep_lock_409(aws, evaluator_dirs):
    import time

    _create_exercise("api_del_lock")
    dynamo_table().put_item(
        Item={
            "pk": "LOCK#prep#all",
            "sk": "META",
            "job_id": "j1",
            "ttl": int(time.time()) + 600,
        }
    )
    resp = _call(api_event("DELETE", "/v1/exercises/api_del_lock", groups=("admin",)))
    assert resp["statusCode"] == 409


def test_delete_exercise_scrubs_student_reports(aws, evaluator_dirs):
    _create_exercise("task_a", description="# Task A\n\nDo A.\n")
    slug, json_key, md_key = _seed_student_report(aws, slug="scrub-me", name="Scrub Me")

    resp = _call(api_event("DELETE", "/v1/exercises/task_a", groups=("admin",)))
    assert resp["statusCode"] == 200
    assert _body(resp)["deleted"]["reports_scrubbed"] == 1

    bucket = os.environ["DATA_BUCKET"]
    report = json.loads(aws["s3"].get_object(Bucket=bucket, Key=json_key)["Body"].read())
    assert [t["slug"] for t in report["tasks"]] == ["task_b"]
    # task_a was a 10-point pass; task_b (missing) remains: 0/10.
    assert report["counts"] == {"pass": 0, "fail": 0, "missing": 1, "needs_prep": 0, "total": 1}
    assert report["points_earned"] == 0
    assert report["points_possible"] == 10
    # report.md lost the task's section but kept the head block.
    md = aws["s3"].get_object(Bucket=bucket, Key=md_key)["Body"].read().decode()
    assert "AI summary for task_a." not in md
    assert "## Overall" in md
    # The denormalized student card was refreshed to match.
    card = dynamo_table().get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"})["Item"]
    assert int(card["points_earned"]) == 0
    assert int(card["points_possible"]) == 10
    assert int(card["counts"]["missing"]) == 1


def test_delete_exercise_leaves_unrelated_reports_alone(aws, evaluator_dirs):
    _create_exercise("api_del_other")
    slug, json_key, _ = _seed_student_report(aws, slug="untouched", name="Untouched")
    before = aws["s3"].get_object(
        Bucket=os.environ["DATA_BUCKET"], Key=json_key
    )["Body"].read()

    resp = _call(api_event("DELETE", "/v1/exercises/api_del_other", groups=("admin",)))
    assert resp["statusCode"] == 200
    assert _body(resp)["deleted"]["reports_scrubbed"] == 0
    after = aws["s3"].get_object(
        Bucket=os.environ["DATA_BUCKET"], Key=json_key
    )["Body"].read()
    assert after == before
