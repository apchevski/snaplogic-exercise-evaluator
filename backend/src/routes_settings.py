"""Routes: GET /v1/config, GET/PUT /v1/settings (own credentials + model)."""
from __future__ import annotations

import os
from typing import Any

from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
    ServiceError,
)

from evaluator.ai_judge import JUDGE_MODEL_CHOICES

from .auth import ROLE_ADMIN, ROLE_MENTOR, _email, _groups, _require_role
from .common import (
    dynamo_table,
    get_user_settings,
    load_secrets_into_env,
    shared_judge_model,
    to_dynamo,
    user_settings_pk,
    utc_now_iso,
)
from .resolver import app

@app.get("/v1/config")
def get_config() -> dict[str, Any]:
    """Non-secret SnapLogic settings the UI needs (e.g. to prefill the
    Add Student dialog's project space). Credentials never leave the server."""
    _require_role(ROLE_ADMIN, ROLE_MENTOR)
    load_secrets_into_env()

    def env(key: str) -> str | None:
        return os.environ.get(key, "").strip() or None

    return {
        "config": {
            "org_name": env("SNAPLOGIC_ORG_NAME"),
            "student_project_space": env("SNAPLOGIC_STUDENT_PROJECT_SPACE"),
            "solution_project_space": env("SNAPLOGIC_SOLUTION_PROJECT_SPACE"),
            "solution_project": env("SNAPLOGIC_SOLUTION_PROJECT"),
        }
    }




# ---------- per-user settings (own credentials + judge model) ----------

#: Judge models a user may pick in Settings. Defined once next to the
#: pricing table in evaluator.ai_judge (JUDGE_MODEL_CHOICES, imported at the
#: top of this file) so the picker, the prices, and the cost blurbs can't
#: drift apart across files.
ALLOWED_JUDGE_MODELS = JUDGE_MODEL_CHOICES

#: SETTINGS-row keys only an admin may write (mentors run grading against the
#: shared SnapLogic credentials; syncs are admin-only anyway).
_ADMIN_ONLY_SETTINGS_KEYS = ("snaplogic_username", "snaplogic_password")


def _masked_settings(email: str, row: dict[str, Any]) -> dict[str, Any]:
    """The caller-visible view of their SETTINGS row — secrets never leave
    the server; the UI only learns *that* a value is stored (plus a short
    tail of the API key so the owner can tell which key it is)."""
    api_key = str(row.get("anthropic_api_key") or "")
    return {
        "email": email,
        "snaplogic_username": str(row.get("snaplogic_username") or "") or None,
        "snaplogic_password_set": bool(str(row.get("snaplogic_password") or "").strip()),
        "anthropic_api_key_set": bool(api_key.strip()),
        "anthropic_api_key_hint": ("…" + api_key[-4:]) if len(api_key) >= 12 else None,
        "judge_model": str(row.get("judge_model") or "") or None,
        "default_model": shared_judge_model(),
        "allowed_models": [dict(m) for m in ALLOWED_JUDGE_MODELS],
        "updated_at": row.get("updated_at"),
    }


@app.get("/v1/settings")
def get_settings() -> dict[str, Any]:
    """The caller's own stored credentials (masked) + the model choices."""
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    email = _email(claims).strip().lower()
    return {"settings": _masked_settings(email, get_user_settings(email))}


@app.put("/v1/settings")
def put_settings() -> dict[str, Any]:
    """Partial update of the caller's own credentials and judge model.

    Accepted keys — only the ones present are applied; null or "" clears:
      snaplogic_username    admin only — personal SnapLogic login
      snaplogic_password    admin only — stored write-only, never returned
      anthropic_api_key     own Anthropic key for grading (admin or mentor)
      judge_model           model used when this user starts a grading; must
                            be one of ALLOWED_JUDGE_MODELS (null = default)

    Jobs started by this user (grade, sync, the registration project check)
    run under these values; anything unset falls back to the shared app
    secret. SnapLogic credentials only take effect as a complete
    username+password pair.
    """
    claims = _require_role(ROLE_ADMIN, ROLE_MENTOR)
    body = app.current_event.json_body or {}
    if not isinstance(body, dict):
        raise BadRequestError("Body must be a JSON object.")
    email = _email(claims).strip().lower()
    if not email or email == "unknown":
        raise BadRequestError(
            "Your token carries no email claim — settings need one to be stored."
        )
    is_admin = ROLE_ADMIN in _groups(claims)

    editable = (
        "snaplogic_username",
        "snaplogic_password",
        "anthropic_api_key",
        "judge_model",
    )
    unknown = sorted(set(body) - set(editable))
    if unknown:
        raise BadRequestError(f"Unknown settings key(s): {', '.join(unknown)}.")
    for key in _ADMIN_ONLY_SETTINGS_KEYS:
        if key in body and not is_admin:
            raise ServiceError(
                403, "Only admins may store personal SnapLogic credentials."
            )

    if "judge_model" in body and body.get("judge_model"):
        model = str(body["judge_model"]).strip()
        allowed = {m["id"] for m in ALLOWED_JUDGE_MODELS}
        if model not in allowed:
            raise BadRequestError(
                f"judge_model must be one of {sorted(allowed)} (or null for the default)."
            )

    row = get_user_settings(email)
    for key in editable:
        if key not in body:
            continue
        value = str(body.get(key) or "").strip()
        if value:
            row[key] = value
        else:
            row.pop(key, None)

    row.update(
        {
            "pk": user_settings_pk(email),
            "sk": "SETTINGS",
            "email": email,
            "updated_at": utc_now_iso(),
        }
    )
    dynamo_table().put_item(Item=to_dynamo(row))
    return {"settings": _masked_settings(email, row)}


