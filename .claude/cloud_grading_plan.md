# Cloud Grading Plan (v4) — APPROVED BLUEPRINT

> Canonical implementation plan, finalized 2026-06-12. When the user says
> "implement the cloud grading plan" (or similar), THIS is the document.
>
> **Progress (2026-06-12, branch `feature/cloud-grading`): phases 1–6 are
> code-complete** — evaluator refactor (ai_judge/runner/store), backend
> Lambdas + 33 passing $0 tests, cloud prep, all Terraform (validates
> clean), the React SPA (builds clean), and the four GitHub workflows.
> **Still outstanding:** the deploy/verification steps that need live AWS
> (bootstrap apply, first image push, secret value, Cognito users, the
> curl-verified auth matrix, prep byte-equivalence check, browser E2E),
> **Phase 7** (judge-quality tuning against baseline reports — real Claude
> spend, needs user approval), and **Phase 8** (delete ui/ + evaluator/ui.py,
> retire local /prep — deliberately deferred until the cloud paths are
> verified). See CHANGELOG [Unreleased] for the full inventory.
>
> **Folder renames applied during implementation (the blueprint body below
> still uses the original names):** `web/` → `frontend/` (matches `backend/`)
> and `infra/envs/prod/` → `infra/environments/production/`. Everything else
> matches the names written below.

# Cloud-Hosted Click-to-Prep / Click-to-Grade Platform (v4 — headless, Claude API, Cognito login)

## Context

The evaluator pivots from "mentor grades locally with their own AI" to **fully cloud-hosted operations**: an **admin** or **mentor** logs into a VPN-restricted web dashboard; mentors (and admins) click **Grade** on a student; admins additionally click **Prep** on an exercise. Grading runs in AWS — deterministic hard gates against SnapLogic + AI judgment via the Claude API (Sonnet 4.6). Prep runs in AWS too (deterministic only, $0 AI). Nobody installs anything locally.

**User decisions (locked):** judge model **`claude-sonnet-4-6`** (~$0.95/full grading; $5 prepaid wallet) · **login required for everything** (Cognito; roles `admin` + `mentor`) · **IP allowlist kept as outer layer** (defense in depth, free) · SnapLogic creds + Anthropic key in **Secrets Manager** · stack = Docker + Terraform + AWS + GitHub Actions · local `/grade` deleted (done 2026-06-12); local `/prep` kept as dev fallback until cloud prep is verified · **build everything first, judge-quality tuning last** (user's explicit ordering choice).

**Already done (2026-06-12):** deleted `AGENTS.md`, `.github/copilot-instructions.md`, `.claude/skills/grade/`; patched README (transition note)/architecture.md/.dockerignore/CHANGELOG.

**Why cloud Prep is structurally necessary (not just convenient):** `solution.json`, `solution.cache.json`, and `expected/` are gitignored generated artifacts — CI can never bake them into the image. Something must run prep against SnapLogic; the Prep button is that something, writing artifacts to S3.

## Architecture

```
Browser (VPN/office IPs only)
  ├─► CloudFront ─ CF Function (IP allowlist) ─► S3 (React SPA + login page)
  └─► API Gateway HTTP API /v1
        │   JWT authorizer (Cognito) on every route + Lambda re-checks sourceIp
        ├─ GET  students / reports / gradings/{id} / exercises / preps/{id}   (any logged-in user)
        ├─ POST /v1/gradings {student, space?, task?}    (mentor or admin)
        └─ POST /v1/preps {slug? | all}                  (admin only)
                       │ both write a JOB item + SQS message (job_type: grade | prep)
                       ▼
SQS queue ──► Worker Lambda (Docker container image from ECR, 15-min timeout,
              reserved concurrency 1, DLQ no-retry)
                ├─ authored content from the image: description.md, notes.md, rules
                ├─ generated artifacts from S3:    solution.json, expected/, task.json
                ├─ SnapLogic REST API (GET-only; creds from Secrets Manager)
                ├─ [grade jobs] Claude API: Sonnet 4.6, structured outputs,
                │               prompt-cached rules, max_tokens capped
                ├─ [prep jobs]  fetch solution + expected outputs → write to S3 ($0 AI)
                └─ reports → S3 + DynamoDB; job → succeeded/failed (+ tokens/cost)
```

**Exercise storage split:** instructor-authored files stay in git and ship in the image (CI rebuild on push); machine-generated artifacts live in S3 under `exercises/<slug>/`, written only by prep jobs. A grade job for an exercise with no S3 artifacts reports `needs_prep` (existing semantic, unchanged).

## Auth (Cognito)

- One user pool, **admin-created users only** (invite-based; no self-signup). Hosted UI + Authorization Code + PKCE for the SPA (`react-oidc-context`).
- Two groups → `cognito:groups` claim: **`admin`** (prep + grade + view) and **`mentor`** (grade + view). Viewing requires any authenticated account. A view-only group can be added later without rework.
- API Gateway **JWT authorizer** validates tokens on every route; the Lambda enforces the role matrix per route and stamps `requested_by` (email claim) onto every job and report version.
- UI hides buttons by role (cosmetic); the API is the real enforcement.
- IP allowlist remains in front of everything (CF Function + Lambda sourceIp check).

| Action | admin | mentor |
|---|---|---|
| View dashboard/reports/exercises | ✅ | ✅ |
| POST /v1/gradings | ✅ | ✅ |
| POST /v1/preps | ✅ | ❌ 403 |

## New/changed code

```
evaluator/ai_judge.py     # NEW — headless judge (structured outputs; points arithmetic in Python)
evaluator/runner.py       # NEW — grade run in-process: hard gates → judge → render report
evaluator/store.py        # NEW — exercise-artifact + report I/O abstraction (local FS for dev, S3 on Lambda)
evaluator/__main__.py     # CHANGED — `run` subcommand for local dev
backend/src/api.py        # NEW — Powertools router: IP check, JWT role enforcement, job create/dedupe, reads
backend/src/worker.py     # NEW — SQS handler dispatching job_type: grade | prep
backend/tests/            # NEW — pytest: moto (AWS) + mocked anthropic client
schemas/evaluation.schema.json  # NEW — structured-outputs schema
Dockerfile.lambda         # NEW — cloud image (evaluator/ + backend/ + authored exercises/ + schemas/)
web/                      # NEW — Vite + React + TS SPA with Cognito login
infra/                    # NEW — Terraform: envs/prod + modules (incl. cognito)
.github/workflows/        # NEW — ci, deploy-infra, deploy-backend, deploy-web
```

### `ai_judge.py` essentials
One Messages API call per exercise (model from `JUDGE_MODEL` env, default `claude-sonnet-4-6`); `output_config.format` json_schema guarantees valid `evaluation.json`; deductions only from rule files; `points = max(0, 10 − Σ deductions)` recomputed in Python; Overall = 1–2 sentences, general, no recommendations (conventions from `.claude/conventions/grade-*.md` move into the prompt); rules block prompt-cached across the run; usage tokens recorded per job; billing-exhausted → job failed with clear message.

### Cloud prep (`worker.py` job_type=prep)
Reuses `evaluator/prep.py` sync logic; reads authored files from the image, writes `solution.json` + `solution.cache.json` + `expected/` + reconciled `task.json` to S3 via `store.py`. Survey state (ok / needs_prep / pipeline_renamed per exercise) saved to DynamoDB → powers the Exercises page. $0 AI cost.

## Data model (DynamoDB single table, PAY_PER_REQUEST, PITR)

| PK | SK | Contents |
|---|---|---|
| `STUDENT#<slug>` | `META` | display_name, space, counts, points, graded_at, latest_version, last `requested_by` |
| `STUDENT#<slug>` | `REPORT#<iso-ts>` | s3 keys, counts, points, tokens_used, est_cost_usd, requested_by |
| `JOB#<id>` | `META` | job_type grade/prep, status, target, error, requested_by, timestamps, usage |
| `LOCK#<key>` | `META` | conditional-put lock (per student for grades, per slug for preps), TTL 30 min |
| `EXERCISE#<slug>` | `META` | title, task_type, prep status, last_prepped_at, max_points |

GSI `gsi1` (entity, slug) for list queries. Reports in S3 `students/<slug>/<version>/`; exercise artifacts in S3 `exercises/<slug>/`. History kept — re-grades create new versions.

## Frontend (`web/`)

- **/login** — Cognito Hosted UI redirect (PKCE); tokens in memory/session storage; everything else route-guarded.
- **/ Dashboard** — student cards (port of the old static `ui/index.html` design — it is the spec), **Grade** button per card + "Grade new student" input; live status pill polling `GET /v1/gradings/{id}` (queued → grading → done/failed + cost).
- **/students/:slug** — full report (accordion, deductions/notes chips, failing-gate detail); version history later.
- **/exercises** — per-exercise prep status; **Prep** button (admins only) with same polling pattern via `GET /v1/preps/{id}`.

## Terraform (`infra/`, prod-only, S3 state + native lockfile)

- **cognito**: user pool (admin-create-only), domain, groups `admin`/`mentor`, SPA app client (code+PKCE, callback = CloudFront URL).
- **data**: DynamoDB (+GSI, PITR, deletion protection, TTL) · reports/artifacts S3 bucket.
- **secrets**: one Secrets Manager secret (SnapLogic creds + Anthropic key), ~$0.40/mo.
- **ecr + worker**: ECR repo · SQS + DLQ (maxReceiveCount 1 — never auto-retry a paid job) · worker Lambda from image (1024 MB, 900 s, reserved concurrency 1) · least-privilege IAM.
- **api**: HTTP API + **JWT authorizer** + routes + API Lambda (same image, api CMD) · CORS = CloudFront origin · 7-day logs.
- **web_hosting**: SPA bucket + CloudFront + OAC + CF Function IP allowlist (from `allowed_cidrs` tfvar) + SPA fallback. (CloudFront URL feeds Cognito callbacks + CORS — wire outputs in envs/prod.)
- **budget**: free $10 AWS Budget email alert.
- GitHub OIDC provider + deploy role (no stored AWS keys in CI).

**12 AWS services**: S3, DynamoDB, Lambda, API Gateway, SQS, ECR, CloudFront (+Function), Cognito ($0 ≤10k MAU), Secrets Manager ($0.40), CloudWatch Logs, Budgets, IAM. Total ≈ **$0.50–0.70/month**.

## CI/CD (GitHub Actions, OIDC)

- **ci.yml**: pytest (anthropic mocked, moto — $0), ruff, vite build, terraform fmt/validate.
- **deploy-backend.yml**: changes to `evaluator/ backend/ exercises/ schemas/ Dockerfile.lambda` → build image → ECR → update both Lambdas. Authored exercise edits ship by git push; admin clicks Prep afterward to refresh artifacts.
- **deploy-infra.yml** (plan on PR, apply on main) · **deploy-web.yml** (vite build with pool/client IDs + API URL from repo variables → s3 sync → invalidation).

## Phases (build everything first; judge-quality tuning is the final loop — user's explicit choice)

1. **Terraform foundation (1.5–2 d).** State bootstrap, then the dependency-free modules: data (DynamoDB + S3), secrets, ECR.
2. **Evaluator refactor + backend + deploy (3–4 d).** `ai_judge.py` + `runner.py` + `store.py` + `backend/` + `Dockerfile.lambda`; **unit tests with a mocked Claude client** (schema validity, points arithmetic, job lifecycle — $0, catches plumbing bugs before any paid call); push image manually to ECR → apply worker + api modules; **$0 import of existing `grades/*/report.json`**; one curl-triggered single-task run to prove the pipe (≈ $0.10).
3. **Cognito (1–1.5 d).** Pool/groups/client (placeholder localhost callback for token testing); JWT authorizer on; verify auth matrix via curl (no token → 401, mentor → /preps 403).
4. **Cloud prep (1.5–2 d).** Prep job type + `/v1/preps` + survey state in DynamoDB; verify Prep → S3 artifacts byte-equivalent to local `/prep` output. $0 AI.
5. **Web app (4–5 d).** Login + route guards + Dashboard + StudentDetail + Exercises page + role-gated Prep/Grade buttons + polling; wire real CloudFront URL into Cognito callbacks + CORS. Browser E2E both roles; 403 off-VPN.
6. **CI/CD (1–2 d).** Four workflows + OIDC role — this enables the tuning loop in Phase 7 (edit files → push → image auto-ships).
7. **Judge-quality validation & tuning (1–2 d + as needed).** Grade students with existing local reports as the baseline; compare verdicts/points/deduction sources; where the judge is off, edit `general_evaluation_rules.md` / `notes.md` / judge prompt → git push → CI rebuilds → re-grade. ~$0.10 per single-task iteration, ~$0.95 per full-run check.
8. **Polish (1–1.5 d).** Delete `ui/` + `evaluator/ui.py`; retire local `/prep` skill; cost-per-run display; README rewrite + CHANGELOG.

**Total ≈ 14–18 dev-days. Budget reality:** data import $0 · prep $0 AI · CI $0 (Claude mocked) · real spend concentrated in Phase 7 tuning: ~$1.50 if quality lands quickly, ~$3 with several iterations. AWS ≈ $0.50–0.70/mo.

## Risks / notes

- **Cognito callback ordering**: CloudFront URL must land in Cognito client callbacks + API CORS — two-step apply wrinkle, solved by wiring module outputs in `envs/prod/main.tf`.
- **Cognito email invites** (~50/day cap, may hit spam) — fine at this scale; SES is the escape hatch.
- **Lambda 15-min cap**: full run ≈ 2–5 min today; split to per-exercise SQS messages if exercise count grows past ~25.
- **Judge drift from the deleted skill**: rubric text lives on in `exercises/general_evaluation_rules.md` + `notes.md` + conventions; Phase 7 baseline comparison is the guard.
- **Two prep paths during transition** (local skill + cloud): cloud is canonical once verified; Phase 8 retires or demotes the local one.
- Work on a feature branch off `develop`.

## Verification

- **Phase 2**: unit tests green (mocked Claude: schema validity, points arithmetic); job lifecycle in DynamoDB; duplicate POST → 409; bad student → MISSING-heavy report, not a crash; bad SnapLogic creds → failed job with message; `terraform plan` clean on re-run.
- **Phase 3**: auth matrix — no token → 401, mentor → /preps 403, admin → 200.
- **Phase 4**: Prep produces S3 artifacts byte-equivalent to local `/prep` output for the same exercise.
- **Phase 5**: full browser E2E both roles; hotspot → 403 before the login page even loads.
- **Phase 7**: cloud-judge vs. baseline report diff (verdict/points/rule-sources match or differ explainably).
- **Cost**: job items show ≈ $0.10/task, ≈ $1/full run; Anthropic console matches.
