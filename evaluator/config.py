from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
EXERCISES_DIR = REPO_ROOT / "exercises"
TMP_DIR = REPO_ROOT / ".tmp"
# Persistent grading output — only report.md per student lives here.
# Intermediate artifacts (manifest.json, ai_context.json, evaluation.json,
# student CSV) live under .tmp/grades/<student>/ during a run and are
# deleted at the end of `evaluator.grade report`.
GRADES_DIR = REPO_ROOT / "grades"


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
