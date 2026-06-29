from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_path(var: str, default: Path) -> Path:
    """Resolve a directory from an env var, falling back to the repo layout.

    On AWS Lambda the image filesystem is read-only, so the worker points
    these at /tmp (e.g. EVALUATOR_EXERCISES_DIR=/tmp/evaluator/exercises)
    and `evaluator.store` materializes content there before a run. Local
    runs leave the env vars unset and keep the historical repo-relative
    layout.
    """
    raw = os.environ.get(var, "").strip()
    return Path(raw) if raw else default


EXERCISES_DIR = _env_path("EVALUATOR_EXERCISES_DIR", REPO_ROOT / "exercises")
TMP_DIR = _env_path("EVALUATOR_TMP_DIR", REPO_ROOT / ".tmp")
# Persistent grading output — only report.md per student lives here.
# Intermediate artifacts (manifest.json, ai_context.json, evaluation.json,
# student output files) live under .tmp/grades/<student>/ during a run and are
# deleted at the end of `evaluator.grade report`.
GRADES_DIR = _env_path("EVALUATOR_GRADES_DIR", REPO_ROOT / "grades")


@dataclass(frozen=True)
class Settings:
    base_url: str
    username: str
    password: str
    org_name: str
    project_space_name: str        # solution's project space
    project_name: str              # solution's project
    student_project_space_name: str  # default student project space


def load_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")

    def req(key: str) -> str:
        val = os.environ.get(key, "").strip()
        if not val:
            raise RuntimeError(f"Missing required env var: {key}")
        return val

    return Settings(
        base_url=req("SNAPLOGIC_BASE_URL").rstrip("/"),
        username=req("SNAPLOGIC_ADMIN_USERNAME"),
        password=req("SNAPLOGIC_ADMIN_PASSWORD"),
        org_name=req("SNAPLOGIC_ORG_NAME"),
        project_space_name=req("SNAPLOGIC_SOLUTION_PROJECT_SPACE"),
        project_name=req("SNAPLOGIC_SOLUTION_PROJECT"),
        student_project_space_name=os.environ.get(
            "SNAPLOGIC_STUDENT_PROJECT_SPACE", "IWC_Support"
        ).strip()
        or "IWC_Support",
    )
