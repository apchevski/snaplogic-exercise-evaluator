"""S3Store tests against moto S3."""
from __future__ import annotations

import os

from evaluator.config import EXERCISES_DIR, GRADES_DIR
from evaluator.store import S3Store


def _bucket() -> str:
    return os.environ["DATA_BUCKET"]


def test_materialize_merges_image_and_s3_with_s3_winning(aws, evaluator_dirs, tmp_path):
    s3 = aws["s3"]
    # Authored content baked into the "image".
    image_dir = tmp_path / "image-exercises"
    slug_dir = image_dir / "store_slug_a"
    slug_dir.mkdir(parents=True)
    (slug_dir / "description.md").write_text("# Task A", encoding="utf-8")
    (slug_dir / "task.json").write_text('{"from": "image"}', encoding="utf-8")

    # Generated artifacts in S3 — including a reconciled task.json.
    s3.put_object(Bucket=_bucket(), Key="exercises/store_slug_a/solution.json", Body=b"{}")
    s3.put_object(
        Bucket=_bucket(), Key="exercises/store_slug_a/task.json", Body=b'{"from": "s3"}'
    )
    s3.put_object(
        Bucket=_bucket(), Key="exercises/store_slug_a/expected/out.csv", Body=b"a,b\n1,2\n"
    )

    store = S3Store(_bucket(), image_exercises_dir=image_dir)
    dest = store.materialize_exercises()

    assert dest == EXERCISES_DIR
    assert (dest / "store_slug_a" / "description.md").read_text(encoding="utf-8") == "# Task A"
    assert (dest / "store_slug_a" / "solution.json").exists()
    assert (dest / "store_slug_a" / "expected" / "out.csv").exists()
    # The prep-reconciled task.json from S3 overrides the committed one.
    assert '"from": "s3"' in (dest / "store_slug_a" / "task.json").read_text(encoding="utf-8")


def test_upload_exercise_artifacts(aws, evaluator_dirs, tmp_path):
    slug = "store_slug_b"
    slug_dir = EXERCISES_DIR / slug
    (slug_dir / "expected").mkdir(parents=True, exist_ok=True)
    (slug_dir / "task.json").write_text("{}", encoding="utf-8")
    (slug_dir / "solution.json").write_text("{}", encoding="utf-8")
    (slug_dir / "solution.cache.json").write_text("{}", encoding="utf-8")
    (slug_dir / "expected" / "out.csv").write_text("a\n1\n", encoding="utf-8")
    (slug_dir / "description.md").write_text("# B", encoding="utf-8")  # authored: not uploaded

    store = S3Store(_bucket(), image_exercises_dir=tmp_path / "nonexistent")
    keys = store.upload_exercise_artifacts(slug)

    assert sorted(keys) == [
        f"exercises/{slug}/expected/out.csv",
        f"exercises/{slug}/solution.cache.json",
        f"exercises/{slug}/solution.json",
        f"exercises/{slug}/task.json",
    ]
    listed = aws["s3"].list_objects_v2(Bucket=_bucket(), Prefix=f"exercises/{slug}/")
    assert {o["Key"] for o in listed["Contents"]} == set(keys)


def test_upload_report_versions(aws, evaluator_dirs, tmp_path):
    student = "Store Report Student"
    report_dir = GRADES_DIR / student
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.md").write_text("# report", encoding="utf-8")
    (report_dir / "report.json").write_text("{}", encoding="utf-8")

    store = S3Store(_bucket(), image_exercises_dir=tmp_path / "nonexistent")
    keys = store.upload_report(student, "store-report-student", "2026-06-12T10:00:00Z")

    assert keys == {
        "report_md_key": "students/store-report-student/2026-06-12T10:00:00Z/report.md",
        "report_json_key": "students/store-report-student/2026-06-12T10:00:00Z/report.json",
    }
    body = (
        aws["s3"]
        .get_object(Bucket=_bucket(), Key=keys["report_md_key"])["Body"]
        .read()
        .decode("utf-8")
    )
    assert body == "# report"
