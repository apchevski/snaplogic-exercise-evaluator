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
#   - cli service:    entrypoint overridden to python; runs prep/run commands
#
# Contents: evaluator/ (hard gates + runners) + backend/ (API + worker handlers)
# + schemas/ (structured outputs) + exercises/ (authored content: descriptions,
# notes, task.json). Generated artifacts (solution.json, expected/, grades/)
# are gitignored and fetched at runtime from S3 (cloud) or bind mounts (local).
#
# Lambda's filesystem is read-only after startup; EVALUATOR_*_DIR env vars
# redirect writes to /tmp. See docker-compose.yml for local path overrides.

FROM public.ecr.aws/lambda/python:3.13

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONIOENCODING=utf-8 \
    # Lambda's read-only filesystem — evaluator.config and evaluator.store \
    # honor these env overrides and write to /tmp instead. \
    EVALUATOR_EXERCISES_DIR=/tmp/evaluator/exercises \
    EVALUATOR_TMP_DIR=/tmp/evaluator/scratch \
    EVALUATOR_GRADES_DIR=/tmp/evaluator/grades \
    # Cloud-only: disable UI rebuild (the cloud worker doesn't render dashboards). \
    # docker-compose.yml overrides this to "" for local cli service. \
    EVALUATOR_DISABLE_UI_REBUILD=1

# Install dependencies first; this layer caches until requirements change.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy source + authored exercise content. Generated artifacts (solution.json,
# solution.cache.json, expected/) are excluded by .dockerignore and fetched
# at runtime (S3 in cloud, bind mounts locally).
COPY evaluator/ ${LAMBDA_TASK_ROOT}/evaluator/
COPY backend/ ${LAMBDA_TASK_ROOT}/backend/
COPY schemas/ ${LAMBDA_TASK_ROOT}/schemas/
COPY exercises/ ${LAMBDA_TASK_ROOT}/exercises/

# Default entry point: the API Lambda handler.
# Worker Lambda overrides this via Terraform's image_config.
CMD ["backend.src.api.handler"]
