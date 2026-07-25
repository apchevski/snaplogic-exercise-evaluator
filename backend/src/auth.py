"""AuthN/AuthZ helpers: JWT claims, role-matrix primitives, source-IP check.

The API Gateway JWT authorizer already rejected unauthenticated calls; these
helpers read the verified claims off the event and enforce *authorization*
(roles + student self-scoping). See api.py's module docstring for the matrix.
"""
from __future__ import annotations

import ipaddress
import os
from typing import Any

from aws_lambda_powertools.event_handler.exceptions import (
    ServiceError,
    UnauthorizedError,
)
from boto3.dynamodb.conditions import Key

from .common import query_all
from .resolver import app

ROLE_ADMIN = "admin"
ROLE_MENTOR = "mentor"
ROLE_STUDENT = "student"


# ---------- auth helpers ----------


def _claims() -> dict[str, Any]:
    try:
        claims = app.current_event.request_context.authorizer.jwt_claim
    except Exception:
        claims = None
    if not claims:
        raise UnauthorizedError("No JWT claims on the request.")
    return claims


def _groups(claims: dict[str, Any]) -> set[str]:
    raw = claims.get("cognito:groups") or []
    if isinstance(raw, str):
        # API Gateway stringifies list claims as "[admin mentor]".
        raw = raw.strip("[]").replace(",", " ").split()
    return {str(g).strip() for g in raw if str(g).strip()}


def _email(claims: dict[str, Any]) -> str:
    return str(
        claims.get("email")
        or claims.get("username")
        or claims.get("cognito:username")
        or "unknown"
    )


def _require_role(*allowed: str) -> dict[str, Any]:
    claims = _claims()
    groups = _groups(claims)
    if not groups.intersection(allowed):
        raise ServiceError(
            403, f"Requires one of roles {sorted(allowed)}; token has {sorted(groups)}."
        )
    return claims


def _is_student_only(claims: dict[str, Any]) -> bool:
    """True for a caller in the read-only `student` group and nothing more
    privileged. Such users are scoped to their own STUDENT card; an
    admin/mentor (even one who is also in `student`) sees everything."""
    groups = _groups(claims)
    return ROLE_STUDENT in groups and not groups.intersection({ROLE_ADMIN, ROLE_MENTOR})


def _own_student_slug(claims: dict[str, Any]) -> str | None:
    """The STUDENT card slug whose login email matches the caller, or None.

    A student login is created together with its card (POST /v1/students with
    an email), which stores that email lowercased on the card — so the email
    claim is the link between the Cognito identity and the dashboard row.
    """
    email = _email(claims).strip().lower()
    if not email or email == "unknown":
        return None
    from botocore.exceptions import ClientError

    try:
        # gsi2 (sparse, on email) resolves the caller's card in one read.
        items = query_all(
            IndexName="gsi2", KeyConditionExpression=Key("email").eq(email)
        )
    except ClientError:
        # gsi2 not deployed yet (infra rollout lags a code deploy) — fall
        # back to walking the roster like before.
        items = [
            i
            for i in query_all(
                IndexName="gsi1", KeyConditionExpression=Key("entity").eq("student")
            )
            if str(i.get("email") or "").strip().lower() == email
        ]
    for item in items:
        # USER#<email>/SETTINGS rows carry an email attribute too; only a
        # STUDENT card (entity == "student") identifies the caller's row.
        if str(item.get("entity") or "") == "student":
            return str(item.get("slug"))
    return None


def _require_own_card(claims: dict[str, Any], slug: str) -> None:
    """Confine a student to their own card. Admins and mentors may read any
    student's card; a student reading anyone else's slug gets a 403."""
    if _is_student_only(claims) and _own_student_slug(claims) != slug:
        raise ServiceError(403, "Students may only view their own grades.")


def _ip_allowed(source_ip: str) -> bool:
    cidrs = [c.strip() for c in os.environ.get("ALLOWED_CIDRS", "").split(",") if c.strip()]
    if not cidrs:
        return True  # allowlist disabled; CloudFront/API GW layer still applies
    try:
        ip = ipaddress.ip_address(source_ip)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(c, strict=False) for c in cidrs)


