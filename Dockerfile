# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# SnapLogic Exercise Evaluator — deterministic Python evaluator image.
#
# This image packages ONLY the deterministic half of the project: the
# `evaluator/` package (hard gates, /prep + /grade orchestrators, dashboard
# build). The AI-judgment step of /grade runs inside an interactive Claude
# Code session on the host — it is intentionally NOT in this image (the
# project's design is "no Anthropic API key, no per-evaluation cost").
#
# The two halves hand off through bind-mounted folders (.tmp/, grades/) — see
# docker-compose.yml and the README "Running in Docker" section.
# ---------------------------------------------------------------------------

# ---- Stage 1: build an isolated virtualenv with the runtime deps ----------
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN python -m venv /opt/venv

# Install dependencies first so this layer is cached until requirements change.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# ---- Stage 2: lean runtime image -----------------------------------------
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="snaplogic-exercise-evaluator" \
      org.opencontainers.image.description="Deterministic SnapLogic exercise evaluator: hard gates, /prep + /grade orchestrators, dashboard build." \
      org.opencontainers.image.licenses="MIT"

# UTF-8 everywhere: gate details and pipeline names carry en-dashes and
# accented characters; the slim image's default C locale would otherwise
# make non-grade entry points raise UnicodeEncodeError on print.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Bring the pre-built virtualenv across from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy the evaluator package and the committed exercise *source* (description,
# notes, input data, universal rules). Generated artifacts (task.json,
# solution.json, expected/, grades/, ui/, .tmp/) are excluded by .dockerignore
# and are produced at run time onto bind-mounted host folders.
COPY evaluator/ ./evaluator/
COPY exercises/ ./exercises/

# Run as an unprivileged user; pre-create the writable output dirs it needs.
# On Linux bind mounts inherit host ownership, so if writes are denied, run
# with `--user "$(id -u):$(id -g)"` (see docker-compose.yml note).
RUN useradd --create-home --uid 10001 evaluator \
    && mkdir -p /app/grades /app/ui /app/.tmp \
    && chown -R evaluator:evaluator /app
USER evaluator

# No long-running process — this is a CLI you invoke one command at a time.
# The default just prints help; override the command to run a subcommand, e.g.
#   docker run --rm --env-file .env <image> python -m evaluator.prep survey
CMD ["python", "-m", "evaluator", "--help"]
