# SnapLogic Exercise Evaluator

Automated grading for SnapLogic training exercises. AI-driven judgment — designed
for exercises that admit many correct solutions, so judgment comes from a model
rather than a rubric.

> **Architecture (cloud-hosted):** mentors click a **Grade** button on a
> VPN-restricted web dashboard; grading (deterministic hard gates + Claude API
> judgment, Sonnet 5 by default) runs in AWS, with nothing installed locally.
> The platform (backend Lambdas, Terraform, React SPA, CI/CD) is deployed; see
> [Cloud grading platform](#cloud-grading-platform) for the one-time deploy
> steps. The local `/grade` Claude Code skill was removed; `/prep` (exercise
> maintenance) and `python -m evaluator run` (a local twin of the cloud grade
> job) remain as dev fallbacks.

## What it does

Mentors and admins log into a VPN-restricted web dashboard (styled after the
classic SnapLogic Dashboard: navy panel headers, sortable/paginated data
tables, and — like the old console's Designer / Manager / Dashboard header —
**Students / Exercises** tabs centered in the top bar; account and
grading settings live on the **Settings** page behind the top-right user menu):

- **Row selection**: both tables use SnapLogic-style square checkboxes in the
  leftmost column. Tick any number of rows; the checkbox in the column header
  selects/clears **every row on the current page** (so with *Entries per page*
  at 100 it selects all 100), and shows a dash when only some are ticked.
- **Icon-only toolbars**: like the classic SnapLogic Designer toolbar, the
  Students and Exercises toolbars show icons instead of labeled buttons, each
  on a tinted square so it stands out against the white toolbar.
  Hover an icon for its name and, for bulk-capable actions, the selection
  count (e.g. *Remove 3 selected students permanently*); the confirmation
  dialog then lists every target by name.
- **Grade** (mentor or admin): tick a student's row (the checkbox in
  the leftmost column) and click the **Grade** icon in the toolbar. Grading runs for
  **one student at a time** — the Grade icon is enabled only while exactly
  one row is ticked. A scope
  picker opens listing every active exercise by its human-readable name
  (e.g. *Task 01 – Generate CSV Report*), all preselected — keep them all for a
  full run, or check just the exercises you want. A job queues, a worker
  Lambda runs the deterministic hard gates against SnapLogic, sends each
  surviving exercise to Claude (Sonnet 5), renders the report, and the row
  refreshes with points and per-task detail. A subset run only replaces the
  selected exercises' results; every run — full or subset — also refreshes
  the AI Overall summary from the merged report.
  - **Full runs use the Batch API (~50% cheaper).** Grading *all* exercises
    submits the AI judging as one Anthropic Message **batch** billed at half
    the standard token rate. The batch is **asynchronous**, so results are not
    instant — usually a few minutes, occasionally up to an hour. The grade
    dialog says so before you confirm; the row shows a **Batch grading…**
    status and the report appears when the batch finishes (you can leave the
    page). A **subset** selection and the per-card **Regrade** stay on the
    instant **synchronous** path (normal cost).
- **Rank column** (everyone): the Students table's leftmost data column (no
  header) numbers the rows in their current order — the top row is always
  **1**, whatever column is sorted, so sorting by Total Points ranks by
  points, sorting by Fail ranks by failures, and so on. Ties in the sorted
  column break alphabetically by student name. The top three rows wear
  gold/silver/bronze medal circles; every other row wears a plain white
  circle.
- **Add a student** (mentor or admin): click the **+** (Add student) icon in
  the toolbar.
  The dialog takes the student's name plus the SnapLogic **project space**
  (prefilled with the configured default, `SNAPLOGIC_STUDENT_PROJECT_SPACE`)
  and optionally a **project** name for when the project isn't named exactly
  after the student. Both are stored on the student and dictate where every
  later grading run looks for their pipelines. The API first verifies the
  project exists at that location (a typo gets a clear "no project named …"
  error instead of a card that every grading run would fail on), then
  registers the student with zero exercises graded (and $0 spent) — grading
  starts later by selecting the row and clicking **Grade**. An optional **student email**
  additionally creates a read-only web login for the student: Cognito emails
  them a temporary password, they change it on first sign-in, and from then
  on they can watch their grades (see the `student` role below).
- **Edit a student** (admin only): tick exactly one student row and click the
  **Edit** (pencil) icon in the toolbar. The dialog takes the same four fields
  as *Add a student* — name, email, project space, project — prefilled with
  what's stored. Saving re-verifies that the SnapLogic project exists wherever
  you've pointed it (same clear "no project named …" error on a typo) and
  changes nothing about the student's grades: **a rename keeps their existing
  grades, report history and detail page**, because the student's internal id
  is fixed at registration. Editing the **email** manages their login — adding
  one invites them, a different address replaces the login (new temporary
  password by email), and clearing it takes the login away. Editing is blocked
  (with a clear message) while a grading for that student is running, since
  that run would overwrite the edit when it finishes.
- **Student sign-in** (read-only): a user in the `student` Cognito group
  lands on the **Students** table — the same roster staff see, but styled as
  a leaderboard: rank badge, name, points, and verdict counts only (the
  Project Space, Project, and Last Graded columns are hidden), with no
  selection checkboxes or toolbar actions — plus an **Exercises** tab.
  Only their own name in the table is a link: other students' detailed
  evaluations stay private (plain text in the table, 403 server-side).
  Clicking their own name opens their detail page (`/students/<their-slug>`)
  with their verdicts, points, overall summary, and per-exercise feedback;
  Exercises is a read-only catalog of the active (non-archived)
  exercises with descriptions and downloadable input files, without the
  staff sync-status columns or toolbar. Every action is gone (and 403s
  server-side): no grading, no registering, no report edits, no instructor
  notes, no exercise editing. The backend enforces the scope too —
  `GET /v1/students` slims every row that isn't the caller's own down to the
  table's columns (no email, no summary, no report fields), and any other
  student's detail/reports 403s. The link between the login and the card is
  the email stored at registration.
- **Regrade one exercise** (mentor or admin): on a student's detail page,
  every task card has a **Regrade** button that re-runs just that exercise
  (one Claude call instead of one per exercise — faster and cheaper than a
  full run). The result is merged into the student's existing report and the
  AI Overall summary is rewritten to match; all other task results are left
  untouched.
- **Not-graded visibility**: exercises a student has never been graded on
  (registered-only students, or exercises added after their last run) show
  as **not graded** cards on the detail page — each with its own **Grade**
  button — plus a **Not Graded** count column on the dashboard (next to
  Pass/Fail/Missing) and a badge in the grade summary.
- **Edit an evaluation** (mentor or admin): next to each task card's Regrade
  button — and beside the Overall summary — a pencil button opens an inline
  editor. Beside the Overall paragraph it rewrites that summary; on a task card
  it opens the **whole evaluation**: the summary, the list of deductions and
  notes (add/edit/remove each one's area, description, −points, rule source and
  reasoning), the bonus-question assessment, and the **points**. Editing the
  deductions recomputes that exercise's points the same way the AI judge does
  (`points = 10 − Σ deductions`, floored at 0) with a live preview; typing a
  **Points** value instead pins a **manual override** that deliberately bypasses
  that formula (human judgment wins) — it's labeled *manually adjusted* and can
  be reset back to the computed value. A points override is allowed on **any**
  task, even a MISSING or name-mismatch one, so a mentor can award partial
  credit; the pass/fail verdict (a hard-gate outcome) is still never changed.
  Edits are saved into the stored report in place ($0 — no re-grade) and refresh
  the student's total. Each task card shows whether it is still **Evaluated by
  AI** or was **Edited by** someone (with the date); every change is appended to
  an immutable **Edit history** (who changed what, when) in its own panel on the
  student's page. Regrading a task later replaces its edited evaluation — and
  clears any override — with fresh AI text; an edited Overall summary is
  likewise replaced on any grading run, since every run rewrites it.
- **Sync** (admin only): tick one or more exercise rows and click the **Sync**
  icon in the toolbar to refresh their solution caches + expected outputs from
  SnapLogic into S3 ($0 — no AI involved). Each ticked exercise syncs as its
  own background job; archived rows in the selection are skipped. There is no
  separate sync-all button: selecting **every** active exercise (header
  checkbox) makes Sync run the single sync-all job instead.
- **Remove a student** (admin only): tick one or more student rows and click
  the red **Remove** (trash) icon in the toolbar; a confirmation dialog lists them,
  then each student is permanently deleted from AWS — dashboard card, full report history (every S3 version),
  their grading-job records, and the web login their registration created
  (if any). Their SnapLogic projects are untouched.
- **Delete an exercise** (admin only): tick one or more exercise rows and
  click the red **Delete** (trash) icon in the toolbar (next to Archive); a
  confirmation dialog lists them, then each exercise is permanently deleted from AWS — authored content, sync artifacts and input files (every S3
  version) plus its DynamoDB and job records — and scrubs its result out of
  every student's live report (points, counts and totals are recalculated;
  older report versions keep their history). Archive remains the reversible
  alternative. Exercises that still ship in the container image keep a
  minimal tombstone row so the image copy can't resurrect them; re-creating
  the same folder name later replaces the tombstone.
- **Exercises** (mentor or admin): the exercise list's **Sync Status** column
  shows a green circled check when an exercise is synced and ready, a muted
  dash when it has never been synced (or is archived — archived exercises sort
  to the bottom of the list), and a diagnostic pill for the
  in-between/failure states; click a task name to expand its full description
  (rendered from the exercise's `description.md`). A **copy** icon at the
  right edge of each Exercise cell copies the task name to the clipboard —
  handy for naming the pipeline in SnapLogic exactly. Exercises that ship
  input data (zips, CSVs under `exercises/<slug>/resources/`) show a
  **Files** column — click a file to download it (served via a short-lived
  presigned S3 URL). A collapsible **Exercise Analytics** panel (staff), shown
  below the sync-status table, gives — per exercise across the whole cohort —
  the pass/fail/missing split, the average score, and the deduction rules that
  cost points most often.
- **Bulk grade** (mentor or admin): the **Grade** icon accepts a multi-row
  selection — one student opens the exercise picker as before; several queue a
  full "grade all exercises" run for each (the worker runs them one at a time),
  after a confirmation dialog that lists the students and notes the cost.
- **Export roster** (mentor or admin): a download icon in the Students toolbar
  exports the roster — as currently searched and sorted — to a CSV (rank, name,
  project, points, verdict counts, last graded).
- **Activity** (mentor or admin): an **Activity Logs** tab lists recent
  grade/sync jobs — who started each one, its status (including background
  failures), and a per-job result/cost summary. Scoped by role and enforced
  server-side: an **admin** sees every admin's and mentor's jobs (the
  deployment-wide audit trail); a **mentor** sees only the jobs they started
  themselves. Students have no Activity tab (403).
- **Gradings in progress** (mentor or admin): a **Gradings in Progress** panel
  at the top of the Students page shows every grading running right now —
  whoever started it, from whatever browser — with how long it has been going.
  It is read from the server, so it survives a browser refresh and is the same
  in every session. While a student has a run in flight their row shows a
  **Grading…** marker and the **Grade** button is disabled for them, on the
  student's detail page **Grade**/**Regrade** are disabled with a banner
  naming who started the run, and the pages refresh themselves when it
  finishes. (The backend already held one grade lock per student — a duplicate
  request 409s — so this makes that visible instead of letting you click into
  an error.) There is no honest estimate of time *remaining*: a full run is
  judged by Anthropic's batch API on their schedule, so elapsed time is shown.
- **Grading history** (everyone, own grades only for students): each student's
  detail page has a **Grading history** panel listing every grading run — its
  scope, who ran it, and the estimated **Claude cost** of that run (full runs
  are billed at the 50% batch rate; runs from before cost tracking, and
  all-deterministic runs, show a dash). Click **View** to open a read-only
  snapshot of that report exactly as it was then. The Scope column
  distinguishes a **Regrade** (the task card's Regrade button) from a
  **Grade** (a single exercise picked from the Students tab).

Exercise *authoring* stays in git (description.md, notes.md, rules); the
`/prep` Claude Code skill still works locally as a dev fallback:

```
/prep                          # reconcile every exercise folder against SnapLogic
python -m evaluator run <student>   # local twin of the cloud grade job
                                    # (needs ANTHROPIC_API_KEY; costs real money)
```

## Cloud grading platform

```
Browser (VPN/office IPs only)
  ├─► CloudFront ── CF Function (IP allowlist) ──► S3 (React SPA, frontend/)
  └─► API Gateway HTTP API /v1 ── JWT authorizer (Cognito) on every route
        ├─ GET  exercises / files                       (any role, students too)
        ├─ GET  students / detail / reports  (students: own card only; else 403)
        ├─ GET  /v1/students/{slug}/reports/{version} — history (students: own only)
        ├─ GET  /v1/config, job status, authored content       (mentor or admin)
        ├─ GET  /v1/jobs — activity log, /v1/analytics/exercises (mentor or admin)
        ├─ GET  /v1/students/{slug}/report/edits — audit log   (mentor or admin)
        ├─ GET/PUT /v1/settings — own credentials + judge model (mentor or admin;
        │        SnapLogic credentials admin-only; secrets are write-only)
        ├─ POST /v1/students {student, space?, project?, email?} — register, no
        │        grading; 400 unless the SnapLogic project exists; the stored
        │        space/project dictate later grading runs; an email creates a
        │        read-only Cognito login for the student      (mentor or admin)
        ├─ POST /v1/gradings {student, slug?, task?|tasks?}   (mentor or admin)
        │        (no task/tasks = full run → async 50%-off Batch API;
        │         subset/single = instant synchronous; `slug` addresses the
        │         card directly, which is what finds a renamed student)
        ├─ PUT /v1/students/{slug} {student?, space?, project?, email?}
        │        edit a registration; the slug (and so every grade) is kept,
        │        the SnapLogic project is re-verified, the login is added/
        │        replaced/removed; 409 mid-grading          (admin only)
        ├─ PATCH /v1/students/{slug}/report — edit evaluation (mentor or admin)
        │        (overall summary, or a task's summary/deductions/bonus/points;
        │        deductions recompute points unless a manual override pins them;
        │        every change is appended to the audit log)
        ├─ POST /v1/syncs {slug?}                             (admin only)
        ├─ POST/PUT /v1/exercises — create / edit / archive   (admin only)
        ├─ DELETE /v1/students/{slug} — purge everything      (admin only)
        └─ DELETE /v1/exercises/{slug} — purge + report scrub (admin only)
                  │ JOB item (DynamoDB) + SQS message
                  ▼
SQS ──► Worker Lambda (container image, 15-min cap, concurrency 1, DLQ no-retry)
          ├─ authored + generated exercise content from S3 (image = seed only)
          ├─ SnapLogic REST (GET-only; the requester's own stored credentials
          │         when set, else the shared Secrets Manager creds)
          ├─ grade: hard gates → Claude (requester's model choice, default
          │         Sonnet 5; requester's own API key when stored, else the
          │         shared key; structured outputs, prompt-cached rules)
          │         → report.md/.json → S3 + DynamoDB
          │         (full run: Message Batches API @ 50% off, async
          │          submit → self-redrive poll → collect; subset/single: sync)
          └─ sync:  evaluator.sync sync → artifacts to S3 ($0 AI)
```

| Piece | Where |
|---|---|
| Headless judge / runner / store | `evaluator/ai_judge.py`, `evaluator/runner.py`, `evaluator/store.py` |
| API + worker Lambdas | `backend/src/` (tests in `backend/tests/`, all moto/stub — $0) |
| Structured-outputs schemas | `schemas/` |
| Lambda container image | `Dockerfile` (one image, two CMDs) |
| Terraform (12 AWS services, ≈$0.50–0.70/mo) | `infra/` (bootstrap + environments/production + modules) |
| React SPA | `frontend/` (Vite + TS, Cognito Hosted UI + PKCE; unit tests via `npm test` — vitest) |
| CI/CD (GitHub OIDC, no stored keys) | `.github/workflows/` |

**Roles** (Cognito groups; the API enforces, the UI only hides buttons):
admins sync + grade + view; mentors grade + view; students view the
**roster table**, **their own detailed grades**, and a **read-only exercise
catalog** (no actions, no edits, no instructor notes) — a signed-in student
lands on the Students table, can open only their own detail page, and the
backend 403s any other student's card (and strips email/summary/report
fields from other rows in the list). Admin/mentor users are invite-only
(admin-created in the Cognito console — no self-signup); student logins are
created by the app itself when a registration includes an email — never add
someone to the `student` group by hand alongside an admin/mentor invite.
**MFA** is optional TOTP (authenticator app): the pool is `mfa_configuration =
"OPTIONAL"` with software-token MFA on. With OPTIONAL MFA the hosted UI does
**not** prompt anyone to enroll, and you can't pre-register someone's
authenticator from the console — so users enroll themselves from the in-app
**Settings page (top-right user menu → Settings) → Two-factor
authentication** (scan the QR, enter a code, done; next sign-in then asks
for a code). **Turn off** asks for confirmation first — disabling removes
the enrolled authenticator immediately, and re-enabling means starting over
with a new QR code. The Settings page also lets
users change their password (a wrong current password says exactly that,
rather than Cognito's misleading "Incorrect username or password") and set a
display name — the **Account** and
**Grading** panels sit side by side for staff, and each panel has a single
**Save** button in its bottom-right corner that applies every changed field
at once (MFA enrollment applies immediately and keeps its own buttons). The
page relies on the
`aws.cognito.signin.user.admin` scope granted to the SPA app client — after
deploying that scope, existing sessions must sign out and back in once before
the account sections work.

**Per-user grading credentials** (Settings page, admin/mentor): every
admin and mentor can store their own credentials — jobs *they* start run
under them, and anything left unset falls back to the shared deployment
secret. Three independent settings, stored on a `USER#<email>/SETTINGS`
DynamoDB row and applied per job via the `requested_by` email:

- **SnapLogic credentials** (admins only): a personal username + password
  used by the gradings, syncs, and registration project checks that admin
  starts, replacing the shared `SNAPLOGIC_ADMIN_*` login. Only takes effect
  as a complete pair. A stored password shows as dots in the field (the
  value itself is never returned by the API), and **Clear** — which drops
  both fields back to the shared credentials — asks for confirmation first.
- **Anthropic API key** (admin or mentor): gradings the user starts are
  billed to their own key instead of the shared `ANTHROPIC_API_KEY`.
- **AI judge model** (admin or mentor): the Claude model used for gradings
  the user starts, picked from a server-side allowlist — Sonnet 5
  (labelled **Recommended**; the default and preselected), Sonnet 4.6,
  Opus 4.8, Haiku 4.5. Each option shows a short cost/capability blurb
  (e.g. "Most thorough evaluations · ~1.7× the cost of Sonnet"). Priced
  per model in the job's cost estimate.

Secrets are write-only: `GET /v1/settings` only reports that one is stored
(plus the API key's last four characters), never the value. Set the pool to `"ON"` to require a second factor for everyone
(then the hosted UI drives enrollment at sign-in and the in-app flow isn't
needed). **Session length:** a sign-in lasts up to **12 hours** (the Cognito
`refresh_token_validity`; access/id tokens are 60 min and renew silently in the
background until then). When the session expires — or any API call is rejected
with `401` — the app clears it and returns you to the **Sign in** screen (at
`/login`) rather than leaving you on a page of "Unauthorized" errors. The
Sign-in screen lives at `/login`; every other path redirects there while signed
out, and Cognito returns to the dashboard (`/`) after a successful sign-in.

### Deploying (one-time, in order)

1. `infra/bootstrap`: `terraform init && terraform apply` once to create the TF
   state bucket.
2. `infra/environments/production`: `terraform init`, then apply ECR first
   (`-target=module.data -target=module.secrets -target=module.ecr`). The
   Lambdas are container images and need one to exist, so build + push once by
   hand: `docker build -f Dockerfile -t <ecr-url>:latest . && docker push …`,
   then run a full `terraform apply`.
3. Put the secret value (SnapLogic creds + Anthropic key) into Secrets
   Manager — the exact CLI command is in `infra/modules/secrets-manager/main.tf`.
4. Create users in the Cognito console and add them to `admin` / `mentor`
   (the third group, `student`, is populated by the app — see Roles above).
5. Fill the blank values in `.github/deploy.vars` from `terraform output` (the
   deploy workflows load that file — no GitHub Variables to set by hand; there
   are no CI secrets, auth is OIDC). Commit, then deploy: push to `main`
   (auto-deploys via path filters) or run a workflow manually from the Actions
   tab / `gh workflow run` against any branch. CI takes over (image → Lambdas,
   SPA → S3 + CloudFront).
6. On the Exercises page (admin), select every exercise with the header
   checkbox and click the **Sync** icon once — with all exercises selected it
   runs the single sync-all job. Besides generating artifacts,
   this seeds every image-shipped exercise's authored files
   (description/notes/resources) into S3 — the canonical exercise store —
   after which the UI owns exercise content end to end.

**One-time GitHub setup for gated infra applies:** `deploy-infra` runs
`terraform plan` on every PR/push and uploads the plan, but the `apply` job is
pinned to a `production` GitHub **Environment**. Create it once under
**Settings → Environments → New environment → `production`** and add yourself
under **Required reviewers** (leave *Prevent self-review* unchecked so a solo
operator can approve their own run). After that, every push to `main` that
touches `infra/**` plans automatically and then **pauses for approval** — open
the run, read the plan in the job summary, and click **Approve** to apply the
exact plan you reviewed (or **Reject** to cancel). If remote state drifted
between plan and approval, terraform refuses the stale plan — just re-run.

## Verdicts and points

Every exercise resolves to exactly one of three verdicts, with a 0–10
point score:

| Verdict   | Meaning                                              | Points  |
|-----------|------------------------------------------------------|---------|
| **PASS**  | Every hard gate passed (output matches the solution) | `10 − Σ deductions`, floor `0`. Verdict stays PASS even if deductions exceed 10. |
| **FAIL** (output-mismatch)  | `output_match` or `triggered_task_responses_match` failed — output is wrong | `10 − Σ deductions`, floor `0` — AI judges pipeline structure for partial credit |
| **FAIL** (procedural)       | Pipeline name wrong (deliverable is there but doesn't follow the naming convention) | `0` (AI not invoked) |
| **MISSING** | Student didn't submit a runnable deliverable: no matching pipeline, OR no output uploaded to SLDB (file_writer), OR no Triggered Task with the convention name (triggered_task) | `—` (not graded; counts as `0/10` toward the per-student total) |

**Why FAIL has two flavors**: a student whose pipeline is structurally
correct except for one misspelled string literal should not be ranked
alongside a student who submitted an empty pipeline. Output-mismatch
FAILs (`output_match`, `triggered_task_responses_match`) still go
to the AI for partial credit — the verdict stays FAIL because the
output is wrong, but points reflect how close the pipeline is to a
correct solution. Procedural FAILs (name mismatch) stay at 0 because
there's nothing partial to credit.

**Why "deliverable not submitted" is MISSING, not FAIL**: a submission
that doesn't include a runnable deliverable can't be graded at all —
the student didn't submit anything to evaluate. This covers both
file_writer (no output file in SLDB → student never ran it) and
triggered_task (no Triggered Task with the convention name → student
didn't create the artifact that lets the task be invoked). MISSING
exercises are not AI-judged (there's nothing to judge) but they still
count as `0/10` toward the per-student total — the denominator is
always `(total exercises) × 10`, regardless of how many were
actually graded, so a student who skipped half the exercises sees
that reflected in their total.

Deductions for every PASS or output-mismatch FAIL come from rules with
**explicit point values** written into
`exercises/general_evaluation_rules.md` (universal SnapLogic best
practices) and per-exercise `exercises/<slug>/notes.md` (task-specific
guidance). The AI judge applies the value the rule states (`-2`, `-1`,
or *mention only*) — it never invents a deduction value. This is what
guarantees the same mistake costs the same points for every student,
every time.

If the AI sees something off that no rule covers with explicit points,
it surfaces it under **Notes** in the report — no points deducted.

A mentor or admin may also **override an exercise's points directly** from the
web UI, pinning a 0–10 value that intentionally supersedes `10 − Σ deductions`
(human judgment wins over the formula). The override is labeled *manually
adjusted*, is allowed on any task — including MISSING and name-mismatch — and
every such change is written to the report's immutable audit log; a re-grade of
that task clears it. The verdict never changes.

### `/prep` — keep exercise folders in sync with SnapLogic

The `prep` skill walks `exercises/`, reads the canonical pipeline name from each
folder's `description.md` H1 heading, looks the pipeline up in the solution
project space, and reconciles local files against the live SnapLogic state:

- Auto-creates `task.json` for **single-output file_writer** exercises (the lone
  writer filename is derived from the binary-write snap).
- Asks the operator to hand-write `task.json` for **multi-output file_writer**
  exercises (lists every required output under `output_filenames`) and for
  **triggered_task** exercises (the script can't derive the Triggered Task name
  or scenarios).
- Detects pipeline renames, writer-filename renames, and stale solution caches;
  rewrites `solution.json`, `solution.cache.json`, and `expected/` to match.
- Prunes obsolete files in `expected/`, keeping only the current outputs.

Run `/prep` whenever you add a new exercise folder or edit a solution pipeline.
`/grade` refuses to grade folders that are not fully prepped.

`/prep --task <slug>` surveys and reconciles just one folder.

### How a grade runs (cloud worker)

> The local `/grade` Claude Code skill was **removed** in the June 2026
> pivot — grading now runs in the cloud from the **Grade** button (see
> [What it does](#what-it-does)). The steps below describe what the worker
> Lambda (`evaluator/runner.py` + `evaluator/ai_judge.py`) does per exercise;
> `python -m evaluator run <student>` is the local twin of the same code path
> (needs `ANTHROPIC_API_KEY`; costs real money).

1. Resolves the student's project location (body override → student card →
   env default: org + `SNAPLOGIC_STUDENT_PROJECT_SPACE` + student name).
2. Discovers every registered, non-archived exercise.
3. For each exercise, runs the deterministic Python evaluator which:
   - Fetches both the solution pipeline and the student's pipeline (GET-only).
   - Applies hard gates: pipeline name match (dash-tolerant) and **either**
     output file match (file_writer) **or** Triggered Task name match plus
     per-scenario JSON response match (triggered_task).
   - On hard-gate fail → writes a complete evaluation and stops.
   - On hard-gate pass → assembles the `ai_context` bundle (description,
     instructor notes, topologically-sorted snap flows, both raw pipeline
     JSONs, plus per-scenario request/response pairs for triggered_task).
4. The **headless Claude judge** (`ai_judge.py`, Sonnet 5, structured outputs,
   prompt-cached rules) turns each judgeable bundle into a scored evaluation;
   points arithmetic and the final verdict are recomputed in Python.
5. Composes `report.md` (human-readable) and `report.json` (structured mirror
   the React SPA renders), uploads them as an immutable S3 version, and writes
   the REPORT row + refreshed STUDENT card.

A **subset or single-exercise Regrade** re-grades only the selected tasks and
merges them into the stored report; `counts` / `points_earned` /
`points_possible` are recomputed from the merged task list, and every run —
full or scoped — refreshes the `## Overall` paragraph so it's never stale.
Full "grade all" runs judge through the **Message Batches API** (~50% cheaper,
asynchronous); subset/single runs stay on the instant synchronous path.

### Pipeline-name matching: dash-tolerant for pipelines, strict for Triggered Tasks

The SnapLogic Designer freely substitutes hyphen-minus (`-`), en dash (`–`),
and em dash (`—`) in pipeline names. The pipeline-name hard gate treats all
three glyphs as equal, so `Task 03 – Join Employee Records` (en dash) matches
`Task 03 - Join Employee Records` (hyphen).

Triggered Task names, by contrast, are matched **strictly** (byte-for-byte) —
the URL is computed from the exact string and any normalization there would
silently route to the wrong task.

## Project layout

```
.
├── README.md
├── SOLUTION_OVERVIEW.md        # one-read map of the whole solution (for new contributors, human or AI)
├── CHANGELOG.md
├── LICENSE
├── requirements.txt
├── docker-compose.yml          # local dev: api/worker (Lambda RIE) + cli services
├── .env.example                # template; copy to .env and fill in
├── .claude/
│   ├── CLAUDE.md               # operating rules (auto-loaded by Claude Code)
│   ├── architecture.md         # design notes
│   ├── project.md              # project framing
│   ├── snaplogic_api_findings.md  # REST API discoveries / gotchas
│   ├── settings.json           # Claude Code project settings
│   ├── conventions/            # one file per project-wide or skill-scoped rule
│   └── skills/
│       └── prep/SKILL.md       # the /prep slash command
├── exercises/
│   ├── general_evaluation_rules.md
│   ├── task_01_generate_csv_report/   # file_writer example
│   │   ├── task.json           # COMMITTED: intent (output_filename(s)); solution_pipeline_path auto-rewritten by /prep
│   │   ├── description.md      # the student-facing prompt (H1 = canonical pipeline name)
│   │   ├── notes.md            # instructor hints fed to the AI judge
│   │   ├── resources/          # student-facing input data (e.g. Task1.zip) — downloadable from the Exercises page
│   │   ├── solution.json       # cached solution pipeline JSON (gitignored; fetched by /prep)
│   │   ├── solution.cache.json # sidecar: signature + snode_id for cache invalidation (gitignored)
│   │   └── expected/           # golden output file(s) (gitignored; auto-fetched by /prep)
│   └── task_02_calculator/     # triggered_task example
│       ├── task.json           # COMMITTED: intent (triggered_task_name + requests[]); path auto-rewritten by /prep
│       ├── description.md
│       ├── notes.md
│       ├── solution.json
│       ├── solution.cache.json
│       └── expected/           # one <scenario>.json per request in task.json
├── grades/                     # persistent per-student report.md + report.json (local runs; cloud keeps them in S3)
├── evaluator/
│   ├── __init__.py
│   ├── __main__.py             # `python -m evaluator ...` (incl. the `run` subcommand)
│   ├── config.py               # env loading; EVALUATOR_*_DIR overrides for Lambda
│   ├── snaplogic_client.py     # GET-only SnapLogic REST client
│   ├── pipeline_fetch.py       # pipeline + SLDB file retrieval, topo sort, triggered-task probes
│   ├── name_match.py           # dash-tolerant pipeline-name comparison
│   ├── hard_gates.py           # name + output equality checks (CSV/XLSX or per-scenario JSON)
│   ├── tasks.py                # task.json discovery + TaskConfig (file_writer | triggered_task)
│   ├── evaluate.py             # per-task evaluator (no LLM call)
│   ├── sync.py                 # /prep skill orchestrator + CLI (also runs inside cloud sync jobs)
│   ├── grade.py                # plan/report orchestrator + CLI
│   ├── ai_judge.py             # headless Claude judge (Sonnet 5, structured outputs)
│   ├── runner.py               # in-process grade run: gates → judge → report → Overall
│   └── store.py                # LocalStore / S3Store artifact + report I/O
├── backend/
│   ├── src/                    # api.py (composition root) + resolver/auth/jobs/content + routes_*.py + worker.py (SQS consumer) + common.py
│   └── tests/                  # pytest: moto AWS + stubbed Claude — $0, run on every PR (deploy-backend `test` job)
├── schemas/                    # structured-outputs JSON schemas for the judge
├── Dockerfile                 # cloud image (api + worker share it; CMD differs)
├── infra/                      # Terraform: bootstrap (state bucket) + environments/production + modules/
├── frontend/                   # React SPA (Vite + TS): login, dashboard, student detail, exercises
├── .github/workflows/          # deploy-backend (test→build→deploy), deploy-frontend (test→build→deploy), deploy-infra (validate→plan→apply); gates run on PRs, deploy on main
└── .tmp/                       # scratch space during a grading run; cleaned out per student
```

## Setup

> The recommended path is **[Running in Docker](#running-in-docker-no-local-python)** —
> no Python install at all. The venv setup below is the optional escape hatch
> (and is still how `.env` gets created — Docker needs that step too).

```powershell
# from repo root

# 1. Credentials (required for BOTH Docker and venv)
Copy-Item .env.example .env
notepad .env   # set SNAPLOGIC_* values

# 2. Local Python (only if you are NOT using Docker)
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Required env vars (see `.env.example`):

| Variable                            | Purpose                                                              |
|-------------------------------------|----------------------------------------------------------------------|
| `SNAPLOGIC_BASE_URL`                | e.g. `https://elastic.snaplogic.com`                                 |
| `SNAPLOGIC_ADMIN_USERNAME`          | Admin user with read access to both project spaces                   |
| `SNAPLOGIC_ADMIN_PASSWORD`          | Admin password                                                       |
| `SNAPLOGIC_ORG_NAME`                | Top-level org (used as the first path segment in every lookup)       |
| `SNAPLOGIC_SOLUTION_PROJECT_SPACE`  | Project space holding the **solution** pipelines                     |
| `SNAPLOGIC_SOLUTION_PROJECT`        | Project (within the solution space) holding the solution pipelines   |
| `SNAPLOGIC_STUDENT_PROJECT_SPACE`   | **Default** project space for students (default `IWC_Support`) — prefills the Add Student dialog; the per-student space stored at registration wins |

> Migration note: `SNAPLOGIC_PROJECT_SPACE_NAME` / `SNAPLOGIC_PROJECT_NAME` were
> renamed to `SNAPLOGIC_SOLUTION_PROJECT_SPACE` / `SNAPLOGIC_SOLUTION_PROJECT`
> to make it explicit that they point at the solution, not the student.

## Running

**Primary entry point: the web dashboard.** Mentors and admins grade, sync,
register students, and author exercises from the browser — see
[What it does](#what-it-does). Nothing below is needed for normal use; it's
the local dev fallback for exercise maintenance and for reproducing a cloud
grade run on your own machine.

**Exercise maintenance — the `/prep` skill in Claude Code:**

```
/prep                                          # reconcile all exercise folders
/prep --task task_02_calculator                # reconcile one folder
```

**Reproduce a cloud grade run locally** (the twin of the worker's code path;
needs `ANTHROPIC_API_KEY`, **costs real money**):

```powershell
.\.venv\Scripts\Activate.ps1
python -m evaluator run "Gabriela Shurbeska"
```

**Lower-level — the deterministic evaluator for one exercise (no AI, $0):**

```powershell
python -m evaluator task_01_generate_csv_report `
  --student "Interworks-Partner/IWC_Support/Gabriela Shurbeska/Task 01 – Generate CSV Report"
```

The student name is auto-derived from the third segment of `--student`. On
hard-gate fail it writes `.tmp/grades/<student>/<task>/evaluation.json`; on
hard-gate pass it writes `.tmp/grades/<student>/<task>/ai_context.json` and
exits 0 with `READY_FOR_AI_REVIEW` — the AI judgment step (`evaluator.runner`
+ `evaluator.ai_judge`) picks up from there.

The solution pipeline JSON is cached at `exercises/<task>/solution.json`
(committed to the repo) with a sidecar `solution.cache.json` recording
the SnapLogic asset's modified-at timestamp. A run only refetches the
body when the timestamp changes — so back-to-back grading of multiple
students hits the cache. To force a refresh of a solution and its
expected outputs, run `/prep --task <slug>` (or call
`python -m evaluator.sync sync --slug <slug>`).

Flags:
- `--student-name <name>` — override the auto-derived student name
  (used in the output path).

The `/prep` orchestrator and the grade helpers are also exposed as
subcommands: `python -m evaluator.sync {survey,sync}` and
`python -m evaluator.grade {plan,report,sync-overall}` (`sync-overall` copies
the rendered `## Overall` paragraph from `report.md` into `overall_summary`
in `report.json`). These CLI entry points are what the cloud grading worker
reuses.

## Running in Docker (no local Python)

`docker-compose.yml` defines three services, all built from `Dockerfile` —
the same image that runs in AWS:

| Service | What it does | Port |
|---------|-------------|------|
| `api` | API Lambda via RIE — HTTP API proxy events | 9000 |
| `worker` | Worker Lambda via RIE — SQS-style invocations | 9001 |
| `cli` | evaluator CLI — `sync` / `run`; bind-mounts repo dirs | — |

### Prerequisites

- Docker (Desktop on Windows/macOS, Engine on Linux), running.
- A filled-in `.env` at the repo root (see [Setup](#setup)).
- Build the image once: `docker compose build` (re-run when `evaluator/`,
  `backend/`, `schemas/`, or `requirements.txt` changes).

### CLI (sync, local run)

The `cli` service overrides the Lambda entrypoint to run Python directly.
Bind mounts keep all writes in your workspace:

```powershell
docker compose run --rm -T cli python -m evaluator.sync survey
docker compose run --rm -T cli python -m evaluator.sync sync --slug task_02_calculator
docker compose run --rm -T cli python -m evaluator run "Gabriela Shurbeska"  # costs real money
```

> **Escape hatch (no Docker):** substitute `.venv/Scripts/python.exe` (or `python`)
> for `docker compose run --rm -T cli python` in any command above, after the
> venv [Setup](#setup).

### API / worker (local Lambda testing)

The RIE is bundled in the base image — no extra setup:

```powershell
# Start the API Lambda
docker compose up api

# Invoke it (from another shell; PowerShell needs backtick line-continuation)
curl -sX POST http://localhost:9000/2015-03-31/functions/function/invocations `
  -H "Content-Type: application/json" `
  -d '{"version":"2.0","routeKey":"GET /v1/health","rawPath":"/v1/health","headers":{},"requestContext":{"http":{"method":"GET","path":"/v1/health"}},"isBase64Encoded":false}'

# Run the worker Lambda once
docker compose run --rm worker
```

## Grade dashboard (browser UI)

The grade dashboard is the **React SPA** under `frontend/` — the same app
mentors use in the cloud (login, roster, student detail, exercises). It reads
live data from the API; there is no locally-generated HTML file. Build and
preview it with the usual Vite commands:

```powershell
cd frontend
npm install
npm run dev      # local dev server against VITE_API_URL
npm run build    # production bundle → frontend/dist/ (CI syncs this to S3)
```

> A self-contained `evaluator.ui` static-HTML dashboard existed in the
> local-first era; it was removed once the SPA became the single UI. Local
> grade runs now just write `grades/<student>/report.{md,json}`.

Exit codes:
- `0` — hard gates passed (AI step pending, or all gates passed)
- `1` — procedural hard gate failed (pipeline name mismatch)
- `2` — bad CLI args / missing required env var / unknown task slug
- `4` — deliverable not submitted (`output_present` 404 OR `triggered_task_exists` missing) — orchestrator treats as MISSING

## Managing exercises

**S3 is the source of truth for exercise content.** Exercises are created,
edited and archived from the web UI; the `exercises/` folders in this repo
are a *seed* — anything shipped there graduates into S3 on its next sync
(additively; an S3 copy is never overwritten by the image), and from then on
the UI owns it. Durability comes from the AWS side, not from git: bucket
versioning + DynamoDB point-in-time recovery + `prevent_destroy` guards.

### Creating and editing (web UI, admin only)

Click the **+** (Add new exercise) icon in the Exercises toolbar, or tick an
exercise row and click the **Edit** (pencil) icon (Edit needs exactly one row
ticked). The dialog takes:

- **Exercise Name** (required) — the human-readable pipeline name (e.g.
  *Task 07 – Router Basics*). Sync looks the solution pipeline up by it, and on
  create the exercise's folder id is derived from it automatically — there's no
  slug to type. Stored as the H1 heading of `description.md` behind the scenes.
- **Description** (required) — the student-facing prompt body (Markdown); no
  need to repeat the name as a heading, the dialog adds it for you.
- **AI Guidance** (optional) — instructor hints fed to the AI judge.
- **Task type** — this replaces the hand-written `task.json`:
  - *File writer, single output* (the default "auto"): nothing to fill in;
    sync detects the pipeline's lone writer snap and generates everything.
  - *File writer, multiple/custom outputs*: list the output filenames and
    pick the comparison mode (`exact` vs `columns_only` for
    non-deterministic outputs).
  - *Triggered task*: the Triggered Task name (defaults to
    `<pipeline name> Task`, the strict convention) and one row per request
    scenario — a snake_case name plus `param=value; param2=value2` pairs.
  The config is stored as structured data; the worker synthesizes
  `task.json` from it (plus the env-derived pipeline path) at sync time.
- **Input files** (optional) — uploaded browser → S3 via presigned URLs;
  students download them from the Exercises page. In edit mode, click an
  existing file to mark it for deletion.

Markdown lands in S3 under `exercises/<slug>/`; the worker overlays that
prefix onto its working tree before every job, so a UI-authored exercise is
indistinguishable from a seeded one. Finish by ticking the row and
clicking the **Sync** icon in the toolbar.

**Archive** (admin: tick one or more rows, then click the **Archive** icon in
the toolbar) soft-deletes exercises: they stop being
synced, graded and counted toward student totals. Archived rows render
greyed-out, sort to the bottom of the list whatever column is sorted, and show
a plain dash in **Sync Status** (their stored status is moot once they're out
of syncing). Nothing is removed from S3 — **Unarchive** restores them fully.
The icon archives or unarchives depending on the selection (mixing archived and
active rows disables it), and **both directions ask for confirmation** —
unarchiving puts the exercise back into every student's denominator.
**Delete** (admin, same toolbar) is the permanent alternative — see *Delete an
exercise* above.

### Fallback: authoring in git

If the UI is unavailable you can still drop a folder in the repo — the
pre-pivot flow. On its next sync the folder's authored files are seeded into
S3 and the UI takes over; **content edits in git do NOT propagate after
that** (the S3 copy wins), so treat git authoring as create-only.

1. Create `exercises/<slug>/description.md` — the student-facing prompt. The
   **first H1 heading** is the canonical pipeline name (e.g.
   `# Task 03 – Join Employee Records`); both the solution and the student's
   pipeline must use that name in SnapLogic.
2. Optionally create `exercises/<slug>/notes.md` (instructor hints — fed
   to the AI judge). Put only **task-specific** rules here; the universal
   best-practice rules in `exercises/general_evaluation_rules.md` apply
   automatically. Use `notes.md` to override a universal rule when the
   exercise legitimately requires it.
3. If the exercise hands the student input data (a zip, CSVs, …), put those
   files in `exercises/<slug>/resources/`. They appear automatically as
   download buttons on the web UI's Exercises page — no code or config
   needed. Skip the folder entirely when there are no input files.
4. Run `/prep`.

   - For **single-output file_writer** exercises, `/prep` auto-creates `task.json`
     (with `"output_filename": "<output>.csv"`) and fetches `solution.json` +
     `expected/<output>.csv`. By default the output gate compares the column-name
     set **and** the row multiset. **Column order and row order are both
     ignored** — the student just needs the same column names (same names, same
     count) and the same rows; a different column order is realigned by name
     before rows are compared, so it never fails. Only a missing/extra column or
     differing row data fails. For an exercise whose output is non-deterministic
     (e.g. it calls an API that returns random rows every run), add
     `"output_match_mode": "columns_only"` to `task.json` — the gate then
     compares only the column-name set (any order) and ignores rows, so a correct
     submission still passes. The header reader is format-aware (real `.xlsx`
     from the Excel Formatter, or CSV). Default is `"exact"`; see
     `exercises/task_04_born_on_friday/`.
   - For **multi-output file_writer** exercises (the pipeline writes several files
     and the student must reproduce **all** of them — e.g.
     `exercises/task_05_multiple_flows_one_pipeline/`), `/prep` can't guess which
     writers are deliverables, so you hand-write `task.json` with the full list
     under `output_filenames` and then run `/prep` to fetch every file:

     ```json
     {
       "task_type": "file_writer",
       "solution_pipeline_path": "Org/ProjectSpace/Project/Pipeline Name",
       "output_filenames": ["Report1.csv", "Report2.csv", "Report3.csv"]
     }
     ```

     The output gate compares **every** file (each header + row multiset); the
     exercise PASSes only when all match. `output_match_mode` applies to all of
     them. Use exactly one of `output_filename` / `output_filenames`.

     > **Back-compat:** the `file_writer` task type was originally named
     > `csv_writer`, with keys `output_csv_filename` / `output_csv_filenames`
     > (the first exercises all wrote CSVs). Those old names are still accepted
     > in `task.json` and normalize to the format-neutral ones; the
     > `sync --output-csv` flag is likewise a deprecated alias for
     > `--output-file`. Write new exercises with the `file_writer` /
     > `output_filename(s)` names.
   - For **triggered_task** exercises, `/prep` asks you to hand-write
     `task.json` because the script can't derive the Triggered Task name or
     scenarios. The schema is:

     ```json
     {
       "task_type": "triggered_task",
       "solution_pipeline_path": "Org/ProjectSpace/Project/Pipeline Name",
       "triggered_task_name": "Pipeline Name Task",
       "requests": [
         { "name": "addition",    "params": { "mathOperation": "3+5"  } },
         { "name": "subtraction", "params": { "mathOperation": "10-4" } }
       ]
     }
     ```

     `name` becomes the filename in `expected/` (`addition.json`, …) and the
     scenario label in `ai_context.json`.

No Python edits needed — both skills auto-discover any folder with a
`task.json`.

## Architecture & design notes

New to the project? Start with [SOLUTION_OVERVIEW.md](SOLUTION_OVERVIEW.md) —
a single-file map of the whole solution (architecture diagram, data model,
auth, key flows, CI/CD, and the invariants not to break), written so a new
contributor — human or AI — can onboard from one read.

See [.claude/architecture.md](.claude/architecture.md) and
[.claude/project.md](.claude/project.md) for the design rationale, plus
[.claude/conventions/](.claude/conventions/) for the running list of
project-wide and skill-scoped rules.

## Safety

The SnapLogic client is **GET-only** by construction — `SnapLogicClient`
exposes no `post`/`put`/`delete` method. If you ever need to mutate the
org (e.g., import a pipeline), it must be added explicitly and confirmed
with the project owner first.
