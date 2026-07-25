"""API Lambda: Powertools HTTP router behind the API Gateway JWT authorizer.

Defense layers (outer → inner):
1. CloudFront Function + WAF-free IP allowlist on the SPA (infra).
2. API Gateway JWT authorizer — no valid Cognito token, no Lambda invoke.
3. This handler re-checks the source IP against ALLOWED_CIDRS and enforces
   the role matrix per route (the UI hiding buttons is cosmetic only):

       | Action                            | admin | mentor | student |
       |-----------------------------------|-------|--------|---------|
       | GET  /v1/exercises / resources    |  ✅   |  ✅    |  ✅ |
       | GET  /v1/students (list)          |  ✅   |  ✅    |  ✅ all rows; others slimmed |
       | GET  /v1/students/{slug} (+ /reports) | ✅ | ✅    |  ✅ own card only (else 403) |
       | GET  /v1/config, /v1/exercises/{slug} (authored content incl. notes.md), job polling | ✅ | ✅ | ❌ 403 |
       | GET  /v1/jobs (activity log), /v1/analytics/exercises | ✅ | ✅ | ❌ 403 |
       | GET  /v1/students/{slug}/reports/{version} (report history) | ✅ | ✅ | ✅ own card only |
       | GET/PUT /v1/settings (own credentials + judge model) | ✅ | ✅ (no SnapLogic creds) | ❌ 403 |
       | GET  /v1/students/{slug}/report/edits (audit log) | ✅ | ✅ | ❌ 403 |
       | POST /v1/students                 |  ✅   |  ✅    |  ❌ 403 |
       | POST /v1/gradings                 |  ✅   |  ✅    |  ❌ 403 |
       | PATCH /v1/students/{slug}/report  |  ✅   |  ✅    |  ❌ 403 |
       | POST /v1/syncs                    |  ✅   |  ❌ 403|  ❌ 403 |
       | POST /v1/exercises                |  ✅   |  ❌ 403|  ❌ 403 |
       | PUT  /v1/exercises/{slug}         |  ✅   |  ❌ 403|  ❌ 403 |
       | DELETE /v1/students/{slug}        |  ✅   |  ❌ 403|  ❌ 403 |
       | DELETE /v1/exercises/{slug}       |  ✅   |  ❌ 403|  ❌ 403 |

   `student` is the read-only role: members are created by POST /v1/students
   when a registration carries an email (Cognito emails the temporary
   password; the hosted UI forces a change on first sign-in). A student sees
   the whole roster — every row of the dashboard table — but rows that are
   not their own are slimmed to the table's columns (STUDENT_ROSTER_KEYS: no
   email, no summary, no report or provenance fields), and the per-student
   detail reads 403 on anyone else's slug — another student's detailed
   evaluation stays private. They can also browse the exercise list
   (descriptions + input files) but start or change nothing, and never see
   instructor notes. The link between a Cognito login and its card is the
   email: it is stored (lowercased) on the STUDENT card created alongside
   the login, and matched against the caller's email claim here.

POSTs never do the work inline — they write a JOB item + an SQS message and
return 202; the worker Lambda owns execution. A conditional-put LOCK item
dedupes concurrent requests for the same target (409 on conflict).
"""
from __future__ import annotations

import json
from typing import Any

from .auth import _ip_allowed
from .resolver import app, logger

# Importing a route module registers its routes on the shared resolver --
# these imports are load-bearing, not dead (hence the noqa).
from . import routes_exercises  # noqa: E402,F401
from . import routes_jobs  # noqa: E402,F401
from . import routes_settings  # noqa: E402,F401
from . import routes_students  # noqa: E402,F401

__all__ = ["app", "handler", "logger"]


# ---------- entry point ----------


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    source_ip = (
        (event.get("requestContext") or {}).get("http", {}).get("sourceIp", "")
    )
    if not _ip_allowed(source_ip):
        return {
            "statusCode": 403,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": "Access denied. Your IP address is not whitelisted."}
            ),
        }
    return app.resolve(event, context)
