"""Package entry point.

    python -m evaluator <task> --student <path>     # single-exercise hard gates
                                                    # (historical default, unchanged)
    python -m evaluator run <student> [--space S] [--task SLUG]
                                                    # full in-process grading run:
                                                    # hard gates → Claude judge →
                                                    # report. Needs ANTHROPIC_API_KEY.
                                                    # Local dev twin of the cloud
                                                    # worker (~$0.95 full run on
                                                    # Sonnet 4.6 — it spends money).
"""
import sys


def _run_subcommand(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="evaluator run",
        description=(
            "Grade a student end to end in-process (hard gates + Claude judge "
            "+ report). Local twin of the cloud worker; uses the Claude API "
            "and therefore costs real money per run."
        ),
    )
    parser.add_argument("student", help="Student name (project name within the project space).")
    parser.add_argument("--space", dest="project_space", default=None)
    parser.add_argument(
        "--project",
        dest="project",
        default=None,
        help="SnapLogic project holding the student's pipelines (defaults to the student name).",
    )
    parser.add_argument(
        "--task",
        dest="task_slug",
        default=None,
        help="Limit to a single exercise slug (≈$0.10 instead of a full run).",
    )
    args = parser.parse_args(argv)

    from .runner import GradeRunError, run_grade

    try:
        result = run_grade(
            args.student,
            project_space=args.project_space,
            project=args.project,
            task_slug=args.task_slug,
        )
    except GradeRunError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print()
    print("=" * 60)
    print(f"Report: {result.report_md_path}")
    counts = result.counts
    print(
        f"Pass {counts.get('pass', 0)} · Fail {counts.get('fail', 0)} · "
        f"Missing {counts.get('missing', 0)} · "
        f"Needs sync {counts.get('needs_sync', counts.get('needs_prep', 0))}"
    )
    print(f"Points: {result.points_earned}/{result.points_possible}")
    u = result.usage
    print(
        f"Judge usage: {u.calls} call(s), {u.input_tokens} in / {u.output_tokens} out "
        f"(cache write {u.cache_creation_input_tokens}, read {u.cache_read_input_tokens}) "
        f"≈ ${u.est_cost_usd:.4f}"
    )
    return 0


if len(sys.argv) > 1 and sys.argv[1] == "run":
    raise SystemExit(_run_subcommand(sys.argv[2:]))

from .evaluate import main  # noqa: E402  (historical single-exercise CLI)

raise SystemExit(main())
