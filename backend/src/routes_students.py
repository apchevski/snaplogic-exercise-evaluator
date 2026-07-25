"""Routes: student roster, detail, registration, report edits, deletion."""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

from aws_lambda_powertools.event_handler import Response
from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
    NotFoundError,
    ServiceError,
)
from boto3.dynamodb.conditions import Key

from .auth import (
    ROLE_ADMIN,
    ROLE_MENTOR,
    ROLE_STUDENT,
    _email,
    _is_student_only,
    _require_own_card,
    _require_role,
)
from .common import (
    apply_user_overrides,
    cognito_client,
    data_bucket,
    dynamo_table,
    load_secrets_into_env,
    lock_key,
    public_item,
    query_all,
    s3_client,
    slugify,
    to_dynamo,
    utc_now_iso,
)
from .content import _purge_s3_prefix, _s3_text
from .jobs import _delete_jobs_for_target, _reject_active_job
from .resolver import app

# The roster columns the dashboard table shows — the only fields another
# student's row carries when a student-only caller lists the roster.
# Everything else (email, overall summary, report keys, registration and
# edit provenance) stays between that student and the staff.
STUDENT_ROSTER_KEYS = {
    "slug",
    "display_name",
    "space",
    "project",
    "points_earned",
    "points_possible",
    "counts",
    "graded_at",
}


@app.get("/v1/students")
def list_students() -> dict[str, Any]:
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT)
    items = query_all(
        IndexName="gsi1", KeyConditionExpression=Key("entity").eq("student")
    )
    students = sorted(
        (public_item(i) for i in items),
        key=lambda s: str(s.get("display_name", "")).lower(),
    )
    if _is_student_only(claims):
        # A student sees the whole roster (scores are cohort-visible), but
        # every row that isn't their own is slimmed to the table's columns.
        # Their own card (matched by email) stays complete — its email is how
        # the SPA finds "my" row, and the per-slug detail endpoints still 403
        # on anyone else's slug.
        email = _email(claims).strip().lower()
        students = [
            s
            if str(s.get("email") or "").strip().lower() == email
            else {k: v for k, v in s.items() if k in STUDENT_ROSTER_KEYS}
            for s in students
        ]
    return {"students": students}


@app.get("/v1/students/<slug>")
def get_student(slug: str) -> dict[str, Any]:
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT)
    _require_own_card(claims, slug)
    resp = dynamo_table().get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"})
    item = resp.get("Item")
    if not item:
        raise NotFoundError(f"No graded student {slug!r}.")
    meta = public_item(item)
    # So the detail view can show the project path even before the first grade.
    meta["student_project_path"] = _student_project_path(meta)
    report = None
    # "report_json" is the legacy attribute name written before the store
    # labels were fixed; keep reading it so old gradings stay viewable.
    key = meta.get("report_json_key") or meta.get("report_json")
    if key:
        obj = s3_client().get_object(Bucket=data_bucket(), Key=key)
        report = json.loads(obj["Body"].read().decode("utf-8"))
    return {"student": meta, "report": report}


@app.get("/v1/students/<slug>/reports")
def list_student_reports(slug: str) -> dict[str, Any]:
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT)
    _require_own_card(claims, slug)
    items = query_all(
        KeyConditionExpression=Key("pk").eq(f"STUDENT#{slug}")
        & Key("sk").begins_with("REPORT#"),
        ScanIndexForward=False,
    )
    return {"reports": [public_item(i) for i in items]}


@app.get("/v1/students/<slug>/reports/<version>")
def get_student_report_version(slug: str, version: str) -> dict[str, Any]:
    """One historical report version's full report.json (immutable S3 copy).

    Powers the version picker on the student detail page: `list_student_reports`
    gives the index (versions + counts), this returns the rendered report for a
    chosen one. Same self-scoping as the other student reads — a student may
    only read their own card's history.
    """
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR, ROLE_STUDENT)
    _require_own_card(claims, slug)
    row = dynamo_table().get_item(
        Key={"pk": f"STUDENT#{slug}", "sk": f"REPORT#{version}"}
    ).get("Item")
    if not row:
        raise NotFoundError(f"No report version {version!r} for {slug!r}.")
    meta = public_item(row)
    # "report_json" is the legacy attribute name (see get_student).
    key = meta.get("report_json_key") or meta.get("report_json")
    report = None
    if key:
        obj = s3_client().get_object(Bucket=data_bucket(), Key=key)
        report = json.loads(obj["Body"].read().decode("utf-8"))
    return {"version": version, "meta": meta, "report": report}




def _default_student_space() -> str | None:
    """The configured default student project space, if any."""
    load_secrets_into_env()
    return os.environ.get("SNAPLOGIC_STUDENT_PROJECT_SPACE", "").strip() or None


def _student_project_path(meta: dict[str, Any]) -> str | None:
    """The SnapLogic path to the student's project — ``org/space/project`` —
    mirroring the manifest grade.py writes. Lets the UI show the project path
    even for students who've never been graded (no report to read it from).
    Returns None when org/space can't be resolved (e.g. credential-less env)."""
    load_secrets_into_env()
    org = os.environ.get("SNAPLOGIC_ORG_NAME", "").strip()
    space = (meta.get("space") or "").strip() or _default_student_space()
    # The project defaults to the student name (same rule as grade.py).
    project = (meta.get("project") or "").strip() or meta.get("display_name")
    if not (org and space and project):
        return None
    return f"{org}/{space}/{project}"


def _opt_str(body: dict[str, Any], key: str) -> str | None:
    """Trimmed optional string from the request body (empty → None)."""
    raw = body.get(key)
    if raw is None:
        return None
    return str(raw).strip() or None


def _verify_student_project(project: str, space: str | None, requester: str) -> None:
    """Reject registration when SnapLogic has no project with that name.

    The project (by default named exactly after the student) must exist in
    the student project space, or every subsequent grading run would fail.
    One GET (asset list) settles it: 404 → clear 400 back to the UI.
    Credentials are the requester's own stored SnapLogic login when set,
    otherwise the app secret (deployed) or the ambient env (local dev); when
    none are configured at all the check is skipped so registration keeps
    working in credential-less environments (e.g. tests).
    """
    import httpx

    apply_user_overrides(requester)
    base_url = os.environ.get("SNAPLOGIC_BASE_URL", "").strip().rstrip("/")
    username = os.environ.get("SNAPLOGIC_ADMIN_USERNAME", "").strip()
    password = os.environ.get("SNAPLOGIC_ADMIN_PASSWORD", "").strip()
    org = os.environ.get("SNAPLOGIC_ORG_NAME", "").strip()
    if not (base_url and username and password and org):
        return
    ps = space or _default_student_space() or "IWC_Support"

    from evaluator.config import Settings
    from evaluator.snaplogic_client import SnapLogicClient

    settings = Settings(
        base_url=base_url,
        username=username,
        password=password,
        org_name=org,
        project_space_name="",
        project_name="",
        student_project_space_name=ps,
    )
    # Stay well under the 29 s API Gateway ceiling.
    with SnapLogicClient(settings, timeout_s=10.0) as client:
        try:
            client.list_assets(org, ps, project)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise BadRequestError(
                    f"No project named {project!r} exists in the {ps!r} "
                    "project space — check the project space and project "
                    "name (the project defaults to the student name)."
                )
            raise ServiceError(
                502,
                "Could not verify the SnapLogic project "
                f"(SnapLogic answered HTTP {e.response.status_code}). Try again.",
            )
        except httpx.HTTPError as e:
            raise ServiceError(
                502, f"Could not reach SnapLogic to verify the project: {e}"
            )



_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _create_student_login(student: str, email: str) -> None:
    """Invite the student into Cognito's read-only `student` group.

    AdminCreateUser with EMAIL delivery makes Cognito send the invitation
    itself (username + temporary password); the hosted UI then forces a
    password change on first sign-in — the API never sees or stores a
    password. The email doubles as the username (the pool signs in by
    email), and is pre-verified so account recovery works immediately.
    """
    from botocore.exceptions import ClientError

    pool_id = os.environ.get("USER_POOL_ID", "").strip()
    if not pool_id:
        raise ServiceError(
            503,
            "Student logins are not configured on this deployment "
            "(USER_POOL_ID is unset). Register without an email, or deploy "
            "the Cognito wiring first.",
        )
    cognito = cognito_client()
    try:
        cognito.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": student},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "UsernameExistsException":
            raise ServiceError(409, f"A login for {email!r} already exists.")
        if code == "InvalidParameterException":
            raise BadRequestError(
                f"Cognito rejected the login for {email!r}: "
                f"{e.response['Error'].get('Message', 'invalid parameter')}"
            )
        raise ServiceError(502, f"Creating the student login failed: {e}")
    try:
        cognito.admin_add_user_to_group(
            UserPoolId=pool_id, Username=email, GroupName=ROLE_STUDENT
        )
    except ClientError as e:
        # Without the group the login can't pass any role check — remove it
        # so a retry doesn't hit UsernameExistsException on a broken account.
        cognito.admin_delete_user(UserPoolId=pool_id, Username=email)
        raise ServiceError(502, f"Assigning the student role failed: {e}")


def _delete_student_login(email: str) -> bool:
    """Best-effort removal of the login a registration created (see
    delete_student: a hard delete leaves no trace, and an orphaned login
    could still sign in and read every grade)."""
    from botocore.exceptions import ClientError

    pool_id = os.environ.get("USER_POOL_ID", "").strip()
    if not pool_id:
        return False
    try:
        cognito_client().admin_delete_user(UserPoolId=pool_id, Username=email)
    except ClientError as e:
        if e.response["Error"]["Code"] == "UserNotFoundException":
            return False
        raise
    return True


@app.post("/v1/students")
def post_student() -> Response:
    """Register a student without grading anything (admin or mentor).

    Creates the STUDENT card so the student shows up on the dashboard with
    every exercise still ungraded; a full or per-exercise grading can then
    be started later. Optional body keys 'space' (project space; defaults to
    SNAPLOGIC_STUDENT_PROJECT_SPACE) and 'project' (SnapLogic project name;
    defaults to the student name) are stored on the card and dictate where
    every later grading run looks for this student's pipelines. Optional
    'email' additionally creates a read-only web login for the student
    (Cognito `student` group; Cognito emails the temporary password). 400 if
    no matching SnapLogic project exists; 409 if the student already exists
    (registered or graded) — nothing about an existing student is
    overwritten.
    """
    from botocore.exceptions import ClientError

    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    body = app.current_event.json_body or {}
    student = str(body.get("student") or "").strip()
    if not student:
        raise BadRequestError("Body must include a non-empty 'student'.")
    email = (_opt_str(body, "email") or "").lower() or None
    if email and not _EMAIL_RE.match(email):
        raise BadRequestError(f"{email!r} does not look like an email address.")
    slug = slugify(student)
    # Resolve the space at registration time so the card (and the dashboard
    # column) always carries the value grading will actually use.
    space = _opt_str(body, "space") or _default_student_space()
    project = _opt_str(body, "project")
    _verify_student_project(project or student, space, _email(claims))
    row = {
        "pk": f"STUDENT#{slug}",
        "sk": "META",
        "entity": "student",
        "slug": slug,
        "display_name": student,
        "space": space,
        "project": project,
        "registered_by": _email(claims),
        "registered_at": utc_now_iso(),
    }
    if email:
        # Present exactly when a login was created — delete_student uses it
        # to know there is a Cognito user to remove. Omitted otherwise:
        # email keys the sparse gsi2, and DynamoDB rejects a NULL index key.
        row["email"] = email
    try:
        dynamo_table().put_item(
            Item=to_dynamo(row), ConditionExpression="attribute_not_exists(pk)"
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ServiceError(409, f"Student {student!r} is already on the list.")
        raise
    if email:
        # Card first (the duplicate check must win over login creation), then
        # the login; if Cognito refuses, roll the card back so the request
        # fails as a unit and can simply be retried.
        try:
            _create_student_login(student, email)
        except Exception:
            dynamo_table().delete_item(Key={"pk": row["pk"], "sk": "META"})
            raise
    return Response(
        status_code=201,
        content_type="application/json",
        body=json.dumps({"student": public_item(row)}),
    )




# Hard-gate failures that still route to the AI judge for partial credit
# (points = 10 − Σ deductions). Mirrors evaluator.evaluate._OUTPUT_MISMATCH_GATES;
# kept here so the API doesn't import the SnapLogic-touching evaluate module.
_OUTPUT_MISMATCH_GATES = frozenset({"output_match", "triggered_task_responses_match"})


def _task_is_ai_judged(task: dict[str, Any]) -> bool:
    """True when the task's score came from the AI judge — so its deductions
    and bonus can be edited and points safely recomputed as 10 − Σ deductions.

    False for a MISSING / NEEDS-SYNC task (never reached the AI) and for a
    *procedural* FAIL such as a pipeline-name mismatch, which is fixed at 0
    points with no AI call and an empty deduction list: recomputing 10 − Σ
    there would wrongly hand it full marks.
    """
    if task.get("status") != "evaluated":
        return False
    if task.get("verdict") == "fail":
        gate = task.get("failing_gate")
        if gate and gate not in _OUTPUT_MISMATCH_GATES:
            return False
    return True


def _clean_difference(d: Any) -> dict[str, Any]:
    """Coerce a mentor-supplied difference into the canonical report shape.

    Mirrors evaluator.ai_judge._finalize_evaluation so a hand-edited
    deduction is indistinguishable from an AI-produced one: five known keys,
    points_deducted an int clamped to [0, MAX_POINTS_PER_EXERCISE], and the
    two "source"/"reasoning" fields defaulted rather than left blank.
    """
    from evaluator.grade import MAX_POINTS_PER_EXERCISE

    if not isinstance(d, dict):
        raise BadRequestError("Each difference must be an object.")
    try:
        pts = int(d.get("points_deducted") or 0)
    except (TypeError, ValueError):
        pts = 0
    pts = max(0, min(pts, MAX_POINTS_PER_EXERCISE))
    description = str(d.get("description") or "").strip()
    if not description:
        raise BadRequestError("Each difference needs a non-empty 'description'.")
    return {
        "area": (str(d.get("area") or "").strip() or "(unspecified)"),
        "description": description,
        "points_deducted": pts,
        "rule_source": (str(d.get("rule_source") or "").strip() or "none"),
        "reasoning": str(d.get("reasoning") or "").strip(),
    }


def _derived_points(task: dict[str, Any]) -> int | None:
    """Points a task carries with no manual override in force.

    AI-judged → 10 − Σ deductions (the judge's invariant); a procedural FAIL →
    0; a MISSING / needs-sync task → None (unscored). Used when a mentor clears
    a manual override to fall back to the computed value.
    """
    from evaluator.grade import MAX_POINTS_PER_EXERCISE

    if _task_is_ai_judged(task):
        total = sum(int(d.get("points_deducted") or 0) for d in (task.get("differences") or []))
        return max(0, MAX_POINTS_PER_EXERCISE - total)
    if task.get("verdict") == "fail":
        return 0
    return None


def _audit_text(val: Any, limit: int = 160) -> str | None:
    """A compact snapshot of a text field for the audit log (trimmed)."""
    if val is None:
        return None
    s = str(val).strip()
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s or None


def _append_report_audit(
    slug: str, editor: str, when: str, target: str, changes: list[dict[str, Any]]
) -> None:
    """Append one immutable audit row for a report edit; no-op if nothing
    actually changed.

    Rows live under the student's partition as ``AUDIT#<ts>#<rand>`` (sortable
    by time, unique per edit). They deliberately omit the ``entity``/``slug``
    GSI keys so they stay out of the student/exercise list queries — the same
    trick REPORT# rows use (see common.py). None values inside ``changes`` are
    fine here: they are plain attributes, not GSI keys.
    """
    if not changes:
        return
    dynamo_table().put_item(
        Item=to_dynamo(
            {
                "pk": f"STUDENT#{slug}",
                "sk": f"AUDIT#{when}#{uuid.uuid4().hex[:8]}",
                "edited_by": editor,
                "edited_at": when,
                "target": target,
                "changes": changes,
            }
        )
    )


@app.patch("/v1/students/<slug>/report")
def patch_student_report(slug: str) -> dict[str, Any]:
    """Edit a graded report in place (admin or mentor) — no re-grade, no AI cost.

    Report-level key:
      overall_summary          replacement text for the Overall paragraph

    Task-level keys (all need 'task' = the exercise slug; only the ones
    present are applied):
      summary                  replacement summary text
      differences              full replacement list of deductions + notes;
                               each item is {area, description, points_deducted,
                               rule_source, reasoning}. Unless a manual points
                               override is in force, the task's points are
                               recomputed as max(0, 10 - Σ points_deducted) —
                               the same invariant the AI judge uses — and the
                               student's points_earned total is refreshed.
      bonus_question_answer    replacement bonus text, or null/"" to clear it
      points                   direct points OVERRIDE (int 0..10): pins the
                               score, flags the task points_manual=True, and
                               deliberately bypasses 10 − Σ (human judgment
                               wins). null clears the override and falls back to
                               the computed value. Allowed on ANY task — even a
                               MISSING or name-mismatch one — so a mentor can
                               award partial credit; the verdict/status (a
                               hard-gate outcome) is still never changed.

    'differences' and 'bonus_question_answer' apply only to an AI-judged task
    (see _task_is_ai_judged): a MISSING / NEEDS-SYNC task or a procedural FAIL
    (e.g. name mismatch) has a fixed score and no deductions to edit — but its
    points can still be overridden directly.
    Rewrites the latest stored report.json at its existing S3 key (and the
    report.md Overall section for an overall edit); verdicts are never changed.
    Every applied change is appended to an immutable audit log (AUDIT# rows,
    read back via GET .../report/edits). Any of these edits is overwritten by
    the next re-grade of that task, which is the intended semantics: new
    grading, new evaluation.
    """
    from evaluator.grade import MAX_POINTS_PER_EXERCISE, _sum_points

    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    body = app.current_event.json_body or {}

    new_overall: str | None = None
    if "overall_summary" in body:
        new_overall = str(body.get("overall_summary") or "").strip()
        if not new_overall:
            raise BadRequestError("overall_summary must not be empty.")

    task_slug = str(body.get("task") or "").strip()
    edit_summary = "summary" in body
    edit_diffs = "differences" in body
    edit_bonus = "bonus_question_answer" in body
    edit_points = "points" in body
    if (edit_summary or edit_diffs or edit_bonus or edit_points) and not task_slug:
        raise BadRequestError("Editing a task needs a non-empty 'task' slug.")

    new_summary: str | None = None
    if edit_summary:
        new_summary = str(body.get("summary") or "").strip()
        if not new_summary:
            raise BadRequestError("summary must not be empty.")

    new_diffs: list[dict[str, Any]] | None = None
    if edit_diffs:
        raw_diffs = body.get("differences")
        if not isinstance(raw_diffs, list):
            raise BadRequestError("'differences' must be a list.")
        new_diffs = [_clean_difference(d) for d in raw_diffs]

    new_bonus: str | None = None
    if edit_bonus:
        raw_bonus = body.get("bonus_question_answer")
        # null or "" clears the bonus answer; any other value is stored as text.
        new_bonus = None if raw_bonus is None else (str(raw_bonus).strip() or None)

    # A manual points override: an int pins the score (10 − Σ is bypassed on
    # purpose), null clears the override and falls back to the computed value.
    override_points: int | None = None
    clear_override = False
    if edit_points:
        raw_points = body.get("points")
        if raw_points is None:
            clear_override = True
        else:
            try:
                override_points = int(raw_points)
            except (TypeError, ValueError):
                raise BadRequestError(
                    "points must be an integer 0..10, or null to clear the override."
                )
            override_points = max(0, min(override_points, MAX_POINTS_PER_EXERCISE))

    if new_overall is None and not task_slug:
        raise BadRequestError(
            "Body must include 'overall_summary' and/or a task edit."
        )
    if task_slug and not (edit_summary or edit_diffs or edit_bonus or edit_points):
        raise BadRequestError(
            "A task edit needs 'summary', 'differences', 'bonus_question_answer', "
            "and/or 'points'."
        )

    item = dynamo_table().get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"}).get("Item")
    if not item:
        raise NotFoundError(f"No graded student {slug!r}.")
    meta = public_item(item)
    # "report_json" is the legacy attribute name (see get_student).
    report_key = meta.get("report_json_key") or meta.get("report_json")
    if not report_key:
        raise BadRequestError("This student has no stored report to edit.")

    s3 = s3_client()
    obj = s3.get_object(Bucket=data_bucket(), Key=str(report_key))
    report = json.loads(obj["Body"].read().decode("utf-8"))
    editor = _email(claims)
    now = utc_now_iso()
    points_changed = False

    if task_slug:
        task = next(
            (t for t in report.get("tasks") or [] if t.get("slug") == task_slug), None
        )
        if task is None:
            raise NotFoundError(f"No task {task_slug!r} in the stored report.")
        # Deductions and the bonus answer only exist for an AI-judged task. A
        # MISSING / NEEDS-SYNC one has no verdict, and a procedural FAIL is
        # fixed at 0 points with no AI call — neither has deductions to edit or
        # a 10 − Σ score to recompute. A direct points OVERRIDE, by contrast, is
        # allowed on any task: human judgment may award partial credit even for
        # a missing or name-mismatch submission.
        if (edit_diffs or edit_bonus) and not _task_is_ai_judged(task):
            raise BadRequestError(
                "Only an AI-judged exercise has deductions or a bonus answer to "
                "edit. A missing or name-mismatch result has a fixed score "
                "(you can still override its points directly)."
            )
        # Snapshot pre-edit values for the audit log.
        before_summary = task.get("summary")
        before_bonus = task.get("bonus_question_answer")
        before_points = task.get("points")
        before_diffs = [dict(d) for d in (task.get("differences") or [])]

        if new_summary is not None:
            task["summary"] = new_summary
            task["summary_edited_by"] = editor
            task["summary_edited_at"] = now
        if edit_diffs:
            task["differences"] = new_diffs
        if edit_bonus:
            task["bonus_question_answer"] = new_bonus

        # Points resolution, in precedence order:
        #   explicit override  → pin the score, flag it manual (10 − Σ bypassed)
        #   clear override     → drop the flag, fall back to the computed value
        #   deductions changed & not manual → recompute 10 − Σ
        #   (deductions changed while manual → points stay pinned)
        if override_points is not None:
            task["points"] = override_points
            task["points_manual"] = True
            points_changed = True
        elif clear_override:
            task.pop("points_manual", None)
            task["points"] = _derived_points(task)
            points_changed = True
        elif edit_diffs and not task.get("points_manual"):
            task["points"] = _derived_points(task)
            points_changed = True

        task["edited_by"] = editor
        task["edited_at"] = now

        # Record only what actually changed (empty → no audit row written).
        task_changes: list[dict[str, Any]] = []
        if new_summary is not None and new_summary != (before_summary or ""):
            task_changes.append(
                {"field": "summary", "from": _audit_text(before_summary),
                 "to": _audit_text(new_summary)}
            )
        if edit_diffs and new_diffs != before_diffs:
            before_ded = sum(int(d.get("points_deducted") or 0) for d in before_diffs)
            after_ded = sum(int(d["points_deducted"]) for d in new_diffs or [])
            task_changes.append(
                {"field": "deductions", "from": f"−{before_ded}", "to": f"−{after_ded}"}
            )
        if edit_bonus and (new_bonus or None) != (before_bonus or None):
            task_changes.append(
                {"field": "bonus", "from": _audit_text(before_bonus),
                 "to": _audit_text(new_bonus)}
            )
        if points_changed and task.get("points") != before_points:
            task_changes.append(
                {"field": "points", "from": before_points, "to": task.get("points")}
            )
        _append_report_audit(slug, editor, now, f"task:{task_slug}", task_changes)

    if new_overall is not None:
        before_overall = report.get("overall_summary")
        report["overall_summary"] = new_overall
        report["overall_summary_edited_by"] = editor
        report["overall_summary_edited_at"] = now
        if new_overall != (before_overall or ""):
            _append_report_audit(
                slug, editor, now, "overall",
                [{"field": "overall_summary", "from": _audit_text(before_overall),
                  "to": _audit_text(new_overall)}],
            )

    if points_changed:
        report["points_earned"] = _sum_points(report.get("tasks") or [])

    s3.put_object(
        Bucket=data_bucket(),
        Key=str(report_key),
        Body=json.dumps(report, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )

    # Keep the human-readable report.md's Overall paragraph in sync (task
    # sections are left as rendered — the web UI only ever shows report.json).
    if new_overall is not None:
        md_key = meta.get("report_md_key")
        md_text = _s3_text(str(md_key)) if md_key else None
        if md_text is not None:
            from evaluator.runner import _replace_overall_in_md

            s3.put_object(
                Bucket=data_bucket(),
                Key=str(md_key),
                Body=_replace_overall_in_md(md_text, new_overall).encode("utf-8"),
                ContentType="text/markdown; charset=utf-8",
            )

    # Refresh the denormalized student card + stamp the edit.
    update_expr = "SET report_edited_by = :e, report_edited_at = :t"
    values: dict[str, Any] = {":e": editor, ":t": now}
    if new_overall is not None:
        update_expr += ", overall_summary = :s"
        values[":s"] = new_overall
    if points_changed:
        update_expr += ", points_earned = :pe"
        values[":pe"] = report["points_earned"]
    dynamo_table().update_item(
        Key={"pk": f"STUDENT#{slug}", "sk": "META"},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=values,
    )
    meta["report_edited_by"] = editor
    meta["report_edited_at"] = now
    if new_overall is not None:
        meta["overall_summary"] = new_overall
    if points_changed:
        meta["points_earned"] = report["points_earned"]
    # Same shape as GET /v1/students/{slug} so the UI can swap state directly.
    return {"student": meta, "report": report}


@app.get("/v1/students/<slug>/report/edits")
def list_report_edits(slug: str) -> dict[str, Any]:
    """Immutable audit log of every manual edit to a student's report — who
    changed what, when (admin or mentor). Newest first.

    Students never see it: provenance is a mentor/admin concern, and a
    student's own view stays purely their grades (they didn't get the
    'edited by' line either).
    """
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    items = query_all(
        KeyConditionExpression=Key("pk").eq(f"STUDENT#{slug}")
        & Key("sk").begins_with("AUDIT#"),
        ScanIndexForward=False,
    )
    return {"edits": [public_item(i) for i in items]}




@app.delete("/v1/students/<slug>")
def delete_student(slug: str) -> dict[str, Any]:
    """Hard-delete a student and every trace of them (admin only).

    Removes the student card, all REPORT history rows, their grade-job rows,
    the grade lock, every stored report object under students/<slug>/
    (all S3 versions), and — when the registration created one — the
    student's Cognito login. 409 while a grading for them is queued or
    running.
    """
    _require_role(ROLE_ADMIN)
    table = dynamo_table()
    card = table.get_item(Key={"pk": f"STUDENT#{slug}", "sk": "META"}).get("Item")
    if not card:
        raise NotFoundError(f"No student {slug!r}.")
    _reject_active_job("grade", slug)

    rows = query_all(KeyConditionExpression=Key("pk").eq(f"STUDENT#{slug}"))
    for item in rows:
        table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    jobs = _delete_jobs_for_target("grade", slug)
    table.delete_item(Key={"pk": lock_key("grade", slug), "sk": "META"})
    objects = _purge_s3_prefix(f"students/{slug}/")
    # An orphaned login could still sign in and read every grade — remove it.
    email = str(card.get("email") or "").strip()
    login_deleted = _delete_student_login(email) if email else False
    return {
        "deleted": {
            "student": slug,
            "rows": len(rows),
            "jobs": jobs,
            "objects": objects,
            "login": login_deleted,
        }
    }


