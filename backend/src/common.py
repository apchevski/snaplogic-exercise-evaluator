"""Shared helpers for the API and worker Lambdas.

Single DynamoDB table (see infra/modules/data). Item shapes:

    STUDENT#<slug>  / META            entity="student", slug — student card
    STUDENT#<slug>  / REPORT#<ver>    one row per grading run (history)
    STUDENT#<slug>  / AUDIT#<ts>#<r>  one immutable row per manual report edit
    JOB#<id>        / META            entity="job", slug=<id> — job lifecycle
    LOCK#<key>      / META            conditional-put dedupe lock, ttl 30 min
    EXERCISE#<slug> / META            entity="exercise", slug — sync status
    USER#<email>    / SETTINGS        per-user credentials + judge model

GSI ``gsi1`` is sparse on (entity, slug): only items that carry both
attributes are listable, which keeps REPORT/AUDIT/LOCK rows out of list
queries.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import unicodedata
from decimal import Decimal
from functools import lru_cache
from typing import Any

LOCK_TTL_SECONDS = 30 * 60

# Secret keys copied into the process env (SnapLogic creds + Anthropic key).
SECRET_ENV_KEYS = (
    "SNAPLOGIC_BASE_URL",
    "SNAPLOGIC_ADMIN_USERNAME",
    "SNAPLOGIC_ADMIN_PASSWORD",
    "SNAPLOGIC_ORG_NAME",
    "SNAPLOGIC_SOLUTION_PROJECT_SPACE",
    "SNAPLOGIC_SOLUTION_PROJECT",
    "SNAPLOGIC_STUDENT_PROJECT_SPACE",
    "ANTHROPIC_API_KEY",
)


@lru_cache(maxsize=1)
def load_secrets_into_env() -> bool:
    """Fetch the app secret once per container and export the keys."""
    secret_arn = os.environ.get("SECRET_ARN", "").strip()
    if not secret_arn:
        return False  # local/dev: rely on the ambient environment (.env)
    import boto3

    resp = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    data = json.loads(resp["SecretString"])
    for key in SECRET_ENV_KEYS:
        value = str(data.get(key, "")).strip()
        if value:
            os.environ[key] = value
    return True


# ---------- per-user credentials ----------
#
# Admins and mentors may store their own credentials (USER#<email>/SETTINGS):
# SnapLogic username/password (admins), an Anthropic API key, and a judge
# model choice. Jobs run under the requester's credentials when present and
# fall back to the shared app secret otherwise. The row deliberately carries
# no entity/slug attributes, so it never appears in the sparse-GSI listings.

#: Env keys a user's stored settings may override for one request/job.
USER_OVERRIDE_ENV_KEYS = (
    "SNAPLOGIC_ADMIN_USERNAME",
    "SNAPLOGIC_ADMIN_PASSWORD",
    "ANTHROPIC_API_KEY",
    "JUDGE_MODEL",
)

# Snapshot of the shared (base) values, taken the first time overrides are
# applied — warm Lambda containers reuse the process env across invocations,
# so the previous job's per-user values must be scrubbed before each job.
_base_env: dict[str, str] | None = None


def user_settings_pk(email: str) -> str:
    return f"USER#{email.strip().lower()}"


def get_user_settings(email: str) -> dict[str, Any]:
    """The stored USER settings row for an email (raw, secrets included)."""
    if not email or email.strip().lower() == "unknown":
        return {}
    item = (
        dynamo_table()
        .get_item(Key={"pk": user_settings_pk(email), "sk": "SETTINGS"})
        .get("Item")
    )
    return from_dynamo(item) if item else {}


def reset_base_env_snapshot() -> None:
    """Test hook: forget the shared-credentials snapshot."""
    global _base_env
    _base_env = None


def apply_user_overrides(email: str | None) -> None:
    """Overlay the requesting user's own credentials onto the process env.

    Always restores the shared values first (the container may still carry a
    previous requester's overrides), then applies whatever the user stored:
    SnapLogic credentials only as a complete username+password pair, the
    Anthropic key and judge model independently. Call after
    load_secrets_into_env() and before any evaluator/SDK client is built.
    """
    global _base_env
    load_secrets_into_env()
    if _base_env is None:
        _base_env = {k: os.environ.get(k, "") for k in USER_OVERRIDE_ENV_KEYS}
    for key, value in _base_env.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    settings = get_user_settings(email or "")
    if not settings:
        return
    sl_user = str(settings.get("snaplogic_username") or "").strip()
    sl_pass = str(settings.get("snaplogic_password") or "").strip()
    if sl_user and sl_pass:  # half a credential pair is unusable — ignore it
        os.environ["SNAPLOGIC_ADMIN_USERNAME"] = sl_user
        os.environ["SNAPLOGIC_ADMIN_PASSWORD"] = sl_pass
    api_key = str(settings.get("anthropic_api_key") or "").strip()
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    model = str(settings.get("judge_model") or "").strip()
    if model:
        os.environ["JUDGE_MODEL"] = model


def shared_judge_model() -> str:
    """The deployment's default judge model — the JUDGE_MODEL env value the
    Lambda was configured with (Terraform), ignoring any per-user override a
    warm container may still carry. The code constant is only the last-resort
    fallback; no model is otherwise hardcoded."""
    from evaluator.ai_judge import DEFAULT_JUDGE_MODEL

    if _base_env is not None:
        base = _base_env.get("JUDGE_MODEL", "")
    else:
        base = os.environ.get("JUDGE_MODEL", "")
    return base.strip() or DEFAULT_JUDGE_MODEL


def table_name() -> str:
    return os.environ["TABLE_NAME"]


def data_bucket() -> str:
    return os.environ["DATA_BUCKET"]


@lru_cache(maxsize=1)
def dynamo_table():
    import boto3

    return boto3.resource("dynamodb").Table(table_name())


@lru_cache(maxsize=1)
def s3_client():
    import boto3

    return boto3.client("s3")


@lru_cache(maxsize=1)
def sqs_client():
    import boto3

    return boto3.client("sqs")


@lru_cache(maxsize=1)
def cognito_client():
    import boto3

    return boto3.client("cognito-idp")


def reset_cached_clients() -> None:
    """Test hook: drop cached boto3 resources so moto fixtures apply."""
    dynamo_table.cache_clear()
    s3_client.cache_clear()
    sqs_client.cache_clear()
    cognito_client.cache_clear()


def utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()


def epoch_in(seconds: int) -> int:
    return int(_dt.datetime.now(tz=_dt.timezone.utc).timestamp()) + seconds


def slugify(name: str) -> str:
    """Stable URL/key-safe slug for student display names and similar."""
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return norm or "unnamed"


def lock_key(job_type: str, target: str) -> str:
    return f"LOCK#{job_type}#{target}"


def to_dynamo(obj: Any) -> Any:
    """Make a JSON-shaped object storable (floats → Decimal)."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def from_dynamo(obj: Any) -> Any:
    """Make a DynamoDB item JSON-serializable (Decimal → int/float)."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [from_dynamo(v) for v in obj]
    return obj


def public_item(item: dict[str, Any]) -> dict[str, Any]:
    """Strip table internals before returning an item over the API."""
    cleaned = {k: v for k, v in item.items() if k not in {"pk", "sk", "ttl"}}
    return from_dynamo(cleaned)
