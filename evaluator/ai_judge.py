"""Headless AI judge for cloud grading.

Replaces the judgment half of the deleted local `/grade` skill. One Claude
Messages API call per exercise turns an `ai_context.json` bundle (written by
`evaluator.evaluate`) into the `evaluation.json` contract the report renderer
(`evaluator.grade`) consumes. A second, much smaller call per full run writes
the report-level `## Overall` paragraph.

Design rules (carried over from the skill + .claude/conventions/grade-*):

- **Structured outputs** (`output_config.format` json_schema) guarantee the
  model's reply parses; the schemas live in `schemas/`.
- **Points arithmetic happens in Python**, never in the model:
  ``points = max(0, 10 - sum(points_deducted))``.
- **The verdict is derived from the hard gates in Python** — any failed gate
  in the bundle forces ``fail`` regardless of what the model said.
- **Deduction values come only from the rule files** (general rules + the
  task's notes.md); the prompt forbids inventing values and the schema makes
  every deduction cite its `rule_source`.
- **The rules block is prompt-cached** (`cache_control` on the last system
  block) so a full grading run pays for the rule text once, not per exercise.
- The judge model is `JUDGE_MODEL` (default `claude-sonnet-4-6` — the
  project's locked decision; do not silently upgrade it).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .config import REPO_ROOT

SCHEMAS_DIR = REPO_ROOT / "schemas"

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
OVERALL_MAX_TOKENS = 300

# USD per 1M tokens: (input, output). Cache writes bill at 1.25x input,
# cache reads at 0.10x input. Matched by model-id prefix; unknown models
# fall back to the Sonnet 4.6 rates so the estimate is never silently zero.
_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4": (5.00, 25.00),
}
_FALLBACK_PRICING = (3.00, 15.00)


class JudgeError(RuntimeError):
    """A Claude API problem worth surfacing verbatim on the failed job."""


@dataclass
class JudgeUsage:
    """Token usage accumulated across one or more judge calls.

    ``batch`` marks usage produced through the Message Batches API, which
    Anthropic bills at half the standard token rates — ``est_cost_usd``
    applies the 0.5 multiplier when it's set.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    calls: int = 0
    model: str = field(default=DEFAULT_JUDGE_MODEL)
    batch: bool = False

    def add_response_usage(self, usage: Any) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_creation_input_tokens += (
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )
        self.cache_read_input_tokens += (
            getattr(usage, "cache_read_input_tokens", 0) or 0
        )
        self.calls += 1

    def merge(self, other: "JudgeUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.calls += other.calls

    @property
    def est_cost_usd(self) -> float:
        in_rate, out_rate = _FALLBACK_PRICING
        for prefix, rates in _PRICING_PER_MTOK.items():
            if self.model.startswith(prefix):
                in_rate, out_rate = rates
                break
        cost = (
            self.input_tokens * in_rate
            + self.cache_creation_input_tokens * in_rate * 1.25
            + self.cache_read_input_tokens * in_rate * 0.10
            + self.output_tokens * out_rate
        ) / 1_000_000
        if self.batch:
            cost *= 0.5  # Batches API bills at 50% of standard token rates.
        return round(cost, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "calls": self.calls,
            "batch": self.batch,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "est_cost_usd": self.est_cost_usd,
        }


# The static half of the system prompt. Encodes the judging conventions that
# used to live in .claude/skills/grade/SKILL.md and .claude/conventions/
# grade-*.md. Keep byte-stable: it sits inside the cached prefix.
JUDGE_SYSTEM_INSTRUCTIONS = """\
You are the AI judge for a SnapLogic training-exercise evaluator. You receive
one exercise bundle: the exercise description, instructor notes, the solution
pipeline's SnapLogic JSON definition, the student pipeline's definition,
topologically-sorted flow summaries for both, the deterministic hard-gate
results, and (for triggered-task exercises) per-scenario response diffs.

Your job is to compare the student's pipeline to the solution and emit a JSON
evaluation. Follow these rules exactly:

VERDICT
- If any hard gate in `hard_gates` has `passed: false`, the verdict is "fail"
  (the orchestrator only routes output-mismatch failures to you; you are
  judging the pipeline structure for partial credit).
- If every hard gate passed, the verdict is "pass".

DEDUCTIONS
- Apply ONLY deductions whose point value is stated explicitly in the general
  evaluation rules (provided below) or the task's notes.md (provided in the
  bundle). Use the rule's value verbatim. NEVER invent a point value.
- Each `differences` entry must cite its governing rule in `rule_source`
  (e.g. "general_rules: filter before sort" or "notes.md: <rule name>").
- One rule, one deduction per exercise: if the same rule is violated in
  several places, deduct its value once and name every occurrence in the
  description.
- Anything that looks off but has no governing rule with an explicit value is
  a mention-only note: include it with `points_deducted: 0` and
  `rule_source: "none"`.
- A task's notes.md can override or extend any general rule; when they
  conflict, notes.md wins.
- Do NOT penalize stylistic differences, equivalent expressions, or
  structurally different snaps that achieve the same correct outcome. There
  is usually more than one correct way to solve an exercise.

EVIDENCE
- Use `solution_flow` / `student_flow` (topologically sorted from link_map)
  for any ordering judgment. The raw `snap_map` key order is NOT execution
  order — never reason from it.
- Claim only what the provided context shows. If you did not see evidence
  for a statement, do not assert it.

BONUS QUESTION
- If the exercise description contains a bonus question, look for the
  student's answer in `student_version_notes` (the canonical location), and
  secondarily anywhere visible in the pipeline definition (sticky notes,
  snap notes, info.notes).
- Answer present in the version notes and correct: no deduction; summarize
  the answer and its correctness in `bonus_question_answer`.
- Answer present but only somewhere other than the version notes: mention-only
  note about the placement, no deduction.
- Answer missing entirely or incorrect: deduct 2 points
  (rule_source: "general_rules: bonus question").
- No bonus question in the exercise: set `bonus_question_answer` to null.

OUTPUT
- `summary`: 1-3 factual sentences. No recommendations, no advice, no praise
  padding. Do not compute or mention point totals — points are computed
  outside the model from your per-difference deductions.
"""

OVERALL_SYSTEM_INSTRUCTIONS = """\
You write the `## Overall` paragraph for a per-student SnapLogic grading
report. Hard limits:
1. SHORT: 1-2 sentences total.
2. Lead with the headline: pass/fail/missing counts and the points total
   exactly as given to you.
3. GENERAL: never mention individual tasks, slugs, or task-specific issues.
   Recurring categories stated generally are fine ("points were lost to snaps
   left at default names").
4. DESCRIPTIVE ONLY: no recommendations, no suggestions for improvement, no
   "the area to focus on", no advice of any kind.
"""


def _load_schema(filename: str) -> dict[str, Any]:
    path = SCHEMAS_DIR / filename
    schema = json.loads(path.read_text(encoding="utf-8"))
    # $comment is documentation for humans; don't ship it to the API.
    schema.pop("$comment", None)
    return schema


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


class AIJudge:
    """One judge instance per grading run (so the prompt cache is shared)."""

    def __init__(
        self,
        client: Any | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        if client is None:
            import anthropic  # lazy: local /prep users don't need the SDK

            client = anthropic.Anthropic()
        self._client = client
        self.model = model or os.environ.get("JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_MODEL
        self.max_tokens = max_tokens or int(
            os.environ.get("JUDGE_MAX_TOKENS", "").strip() or DEFAULT_MAX_TOKENS
        )
        self._evaluation_schema = _load_schema("evaluation.schema.json")
        self._overall_schema = _load_schema("overall.schema.json")

    # ----- per-exercise request params (shared by sync + batch) -----

    def _judge_params(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Build the Messages API kwargs for judging one exercise.

        Byte-identical between the synchronous call (`judge_exercise`) and the
        batched call (`build_batch_requests`) so the cached rules prefix is
        shared: a full batch run pays for the rule text once and reads it at
        ~0.1x on every later request.
        """
        system = [
            {"type": "text", "text": JUDGE_SYSTEM_INSTRUCTIONS},
            {
                "type": "text",
                "text": (
                    "GENERAL EVALUATION RULES (the only universal source of "
                    "deduction values):\n\n" + (bundle.get("general_rules") or "")
                ),
                # Instructions + rules are byte-stable across the whole run;
                # caching here makes exercises 2..N read the prefix at ~0.1x.
                "cache_control": {"type": "ephemeral"},
            },
        ]
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": self._render_bundle(bundle)}],
            "output_config": {
                "format": {"type": "json_schema", "schema": self._evaluation_schema}
            },
        }

    # ----- per-exercise synchronous call -----

    def judge_exercise(self, bundle: dict[str, Any]) -> tuple[dict[str, Any], JudgeUsage]:
        """Run one Messages API call and return (evaluation.json dict, usage)."""
        response = self._create(**self._judge_params(bundle))
        usage = JudgeUsage(model=self.model)
        usage.add_response_usage(response.usage)
        raw = self._parse_json_response(response)
        return _finalize_evaluation(bundle, raw), usage

    # ----- per-exercise batched calls (Message Batches API, 50% cheaper) -----

    def build_batch_requests(
        self, bundles_by_id: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """One batch request per exercise, keyed by its slug (the custom_id).

        Slugs are the exercise folder names (<=64 chars, unique per run), which
        is exactly what the Batches API needs for a custom_id. Requests are
        plain dicts — the SDK accepts them and this avoids pinning a specific
        typed-params import path across anthropic SDK versions.
        """
        return [
            {"custom_id": slug, "params": self._judge_params(bundle)}
            for slug, bundle in bundles_by_id.items()
        ]

    def submit_batch(self, requests: list[dict[str, Any]]) -> str:
        """Submit a batch of judge requests; returns the batch id."""
        try:
            batch = self._client.messages.batches.create(requests=requests)
        except Exception as e:  # surface API problems as a clear job error
            raise JudgeError(f"Submitting the grading batch failed: {e}") from e
        return batch.id

    def retrieve_batch_status(self, batch_id: str) -> str:
        """Return the batch's processing_status ('in_progress' | 'ended' | ...)."""
        try:
            batch = self._client.messages.batches.retrieve(batch_id)
        except Exception as e:
            raise JudgeError(f"Retrieving batch {batch_id} failed: {e}") from e
        return getattr(batch, "processing_status", "") or ""

    def collect_batch(
        self, batch_id: str, bundles_by_id: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], JudgeUsage, dict[str, str]]:
        """Read a finished batch's results.

        Returns (evaluations_by_slug, usage, errors_by_slug). Results arrive in
        any order, so they're keyed by custom_id (the slug). A non-succeeded
        result becomes an entry in ``errors`` and simply has no evaluation —
        the report renderer already treats a missing evaluation as 0 points.
        """
        usage = JudgeUsage(model=self.model, batch=True)
        evaluations: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        try:
            results = self._client.messages.batches.results(batch_id)
        except Exception as e:
            raise JudgeError(f"Reading batch {batch_id} results failed: {e}") from e
        for result in results:
            slug = getattr(result, "custom_id", "")
            outcome = getattr(result, "result", None)
            kind = getattr(outcome, "type", "")
            if kind != "succeeded":
                detail = getattr(getattr(outcome, "error", None), "type", "") or kind
                errors[slug] = f"batch result {kind or 'unknown'} ({detail})"
                continue
            bundle = bundles_by_id.get(slug)
            if bundle is None:
                errors[slug] = "no bundle for this custom_id"
                continue
            message = outcome.message
            usage.add_response_usage(getattr(message, "usage", None))
            raw = self._parse_json_response(message)
            evaluations[slug] = _finalize_evaluation(bundle, raw)
        return evaluations, usage, errors

    # ----- report-level Overall -----

    def overall_summary(self, report: dict[str, Any]) -> tuple[str, JudgeUsage]:
        """One small call that writes the `## Overall` paragraph."""
        counts = report.get("counts") or {}
        task_facts = [
            {
                "verdict": t.get("verdict") or t.get("status"),
                "points": t.get("points"),
                "deduction_areas": [
                    d.get("rule_source") or d.get("area")
                    for d in (t.get("differences") or [])
                    if int(d.get("points_deducted") or 0) > 0
                ],
            }
            for t in (report.get("tasks") or [])
        ]
        user_text = (
            "Write the Overall paragraph for this submission.\n"
            f"Counts: {_dump(counts)}\n"
            f"Points total: {report.get('points_earned')}/{report.get('points_possible')}\n"
            f"Per-task facts (do not name tasks): {_dump(task_facts)}"
        )
        response = self._create(
            model=self.model,
            max_tokens=OVERALL_MAX_TOKENS,
            system=OVERALL_SYSTEM_INSTRUCTIONS,
            messages=[{"role": "user", "content": user_text}],
            output_config={
                "format": {"type": "json_schema", "schema": self._overall_schema}
            },
        )
        usage = JudgeUsage(model=self.model)
        usage.add_response_usage(response.usage)
        data = self._parse_json_response(response)
        return (data.get("overall_summary") or "").strip(), usage

    # ----- internals -----

    def _create(self, **kwargs: Any) -> Any:
        try:
            return self._client.messages.create(**kwargs)
        except Exception as e:  # surface API problems as a clear job error
            name = type(e).__name__
            if name in {
                "AuthenticationError",
                "PermissionDeniedError",
                "BadRequestError",
            }:
                raise JudgeError(
                    f"Claude API rejected the judge call ({name}): {e}. "
                    "If this mentions credit or billing, the prepaid wallet "
                    "is exhausted — top it up and re-run."
                ) from e
            raise

    @staticmethod
    def _parse_json_response(response: Any) -> dict[str, Any]:
        text = next(
            (b.text for b in response.content if getattr(b, "type", "") == "text"),
            None,
        )
        if not text:
            raise JudgeError(
                f"Judge response had no text block (stop_reason="
                f"{getattr(response, 'stop_reason', '?')})."
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:  # shouldn't happen with output_config
            raise JudgeError(f"Judge response was not valid JSON: {e}") from e

    @staticmethod
    def _render_bundle(bundle: dict[str, Any]) -> str:
        """Flatten the ai_context bundle into one user message.

        general_rules is deliberately excluded — it lives in the cached
        system block. Everything else varies per exercise anyway.
        """
        parts = [
            f"TASK SLUG: {bundle.get('task_slug')}",
            f"TASK TYPE: {bundle.get('task_type')}",
            "",
            "EXERCISE DESCRIPTION:",
            bundle.get("exercise_description") or "(none)",
            "",
            "TASK-SPECIFIC INSTRUCTOR NOTES (notes.md — may override general rules):",
            bundle.get("task_notes") or "(none)",
            "",
            f"HARD GATES: {_dump(bundle.get('hard_gates') or [])}",
            "",
            f"SOLUTION FLOW (topological order): {_dump(bundle.get('solution_flow') or [])}",
            f"STUDENT FLOW (topological order): {_dump(bundle.get('student_flow') or [])}",
            "",
            f"STUDENT VERSION NOTES (bonus-answer canonical home): "
            f"{_dump(bundle.get('student_version_notes') or [])}",
        ]
        if bundle.get("triggered_task_scenarios") is not None:
            parts += [
                "",
                f"TRIGGERED TASK NAME EXPECTED: {bundle.get('triggered_task_name_expected')}",
                f"PER-SCENARIO RESPONSES (expected vs student): "
                f"{_dump(bundle.get('triggered_task_scenarios'))}",
            ]
        parts += [
            "",
            f"SOLUTION PIPELINE DEFINITION: {_dump(bundle.get('solution_definition') or {})}",
            "",
            f"STUDENT PIPELINE DEFINITION: {_dump(bundle.get('student_definition') or {})}",
        ]
        return "\n".join(parts)


def _finalize_evaluation(
    bundle: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Recompute everything the model must not be trusted with.

    Verdict comes from the bundle's hard gates; points come from the
    deductions sum; failing_gate fields are copied from the first failed
    gate. The model's own verdict is ignored when it disagrees.
    """
    gates = list(bundle.get("hard_gates") or [])
    failed = [g for g in gates if not g.get("passed")]
    verdict = "fail" if failed else "pass"

    differences: list[dict[str, Any]] = []
    total_deducted = 0
    for d in raw.get("differences") or []:
        try:
            pts = int(d.get("points_deducted") or 0)
        except (TypeError, ValueError):
            pts = 0
        pts = max(0, min(pts, 10))
        total_deducted += pts
        differences.append(
            {
                "area": d.get("area") or "(unspecified)",
                "description": d.get("description") or "",
                "points_deducted": pts,
                "rule_source": d.get("rule_source") or "none",
                "reasoning": d.get("reasoning") or "",
            }
        )

    return {
        "verdict": verdict,
        "points": max(0, 10 - total_deducted),
        "summary": (raw.get("summary") or "").strip(),
        "differences": differences,
        "bonus_question_answer": raw.get("bonus_question_answer"),
        "failing_gate": failed[0].get("name") if failed else None,
        "failing_gate_detail": failed[0].get("detail") if failed else None,
        "hard_gates": gates,
    }
