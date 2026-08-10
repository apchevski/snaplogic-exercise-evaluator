# syntax=docker/dockerfile:1

# Single source of truth: runs in AWS Lambda AND locally via docker-compose.
#
# In AWS Lambda:
#   - api Lambda:   CMD = backend.src.api.handler (set below)
#   - worker Lambda: CMD = backend.src.worker.handler (overridden in infra module)
#
# Locally via docker-compose:
#   - api service:   runs the API Lambda via RIE on port 9000
#   - worker service: runs the worker Lambda via RIE on port 9001
#   - cli service:    entrypoint overridden to python; runs sync/run commands
#
# Contents: evaluator/ (hard gates + runners) + backend/ (API + worker handlers)
# + schemas/ (structured outputs) + exercises/general_evaluation_rules.md (the
# judge's universal rulebook). Everything else exercise-related — authored
# content (description.md, notes.md, resources/) and generated artifacts
# (task.json, solution.json, expected/, grades/) — is fetched at runtime from
# S3 (cloud) or bind mounts (local).
#
# The image sets NO EVALUATOR_* vars: evaluator.config's package-relative
# defaults resolve to the baked-in copies (correct for read-only consumers
# like the api Lambda, which only lists/reads authored exercises). Runtimes
# that WRITE declare their own /tmp (Lambda's only writable dir) redirects:
# infra/modules/sqs-worker for the cloud worker, docker-compose.yml for the
# local worker/cli services.

FROM public.ecr.aws/lambda/python:3.13

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONIOENCODING=utf-8

# Install dependencies first; this layer caches until requirements change.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy source + the judge's rulebook. Exercise folders are NOT copied: their
# authored content is created in the web UI and canonical in S3, and the
# worker overlays the whole exercises/ S3 prefix onto this tree before every
# job (S3Store.materialize_exercises). general_evaluation_rules.md is the one
# exception — nothing uploads it to S3 and no UI edits it, so git → image is
# its only delivery path.
COPY evaluator/ ${LAMBDA_TASK_ROOT}/evaluator/
COPY backend/ ${LAMBDA_TASK_ROOT}/backend/
COPY schemas/ ${LAMBDA_TASK_ROOT}/schemas/
COPY exercises/general_evaluation_rules.md ${LAMBDA_TASK_ROOT}/exercises/

# Default entry point: the API Lambda handler.
# Worker Lambda overrides this via Terraform's image_config.
CMD ["backend.src.api.handler"]
