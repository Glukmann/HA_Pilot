# syntax=docker/dockerfile:1
#
# Pilot add-on image. The build context may be either the repository root
# (CI, local builds) or the add-on folder alone (Supervisor source
# installs) — both produce the full image with the workshop SPA, because
# the workshop (sources AND the committed dist/) lives inside the add-on
# folder (pilot-addon/workshop).
#
# The SPA is NOT rebuilt here: dist/ is committed to git and the CI job
# `workshop-bundle-check` fails when it drifts from the sources (regenerate
# with `cd pilot-addon/workshop && npm ci && npm run build && npm run
# dist:hash`). This keeps source installs hermetic — no npm registry access
# on the Home Assistant host, which matters on slow or flaky links.
#
# Local build from the repo root:
#   docker build -f pilot-addon/Dockerfile -t pilot-addon .
# or from this folder:
#   docker build -t pilot-addon .
#
# HA's builder supplies BUILD_FROM per arch (build.yaml); the default keeps
# plain `docker build` working.

ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.21

# --- Stage 1: stage the context and normalize paths -------------------------
# The workshop sits at pilot-addon/workshop from the repo root and at
# workshop/ from this folder; expose both as fixed paths for later stages.
# Fails loudly if the committed SPA dist is missing — the UI is not optional.
FROM alpine:3.21 AS ctx
WORKDIR /ctx
COPY . .
RUN set -eux; \
    if [ -d pilot-addon/rootfs ]; then \
        cp -r pilot-addon/rootfs /ctx/addon-rootfs; \
        cp -r pilot-addon/workshop /ctx/workshop-out; \
    else \
        cp -r rootfs /ctx/addon-rootfs; \
        cp -r workshop /ctx/workshop-out; \
    fi; \
    test -f /ctx/workshop-out/dist/index.html

# --- Stage 2: the add-on runtime ---------------------------------------------
FROM ${BUILD_FROM}

# OpenClaw runtime pin (build.yaml arg); the agent workspace arrives in Phase 5.
ARG OPENCLAW_VERSION
ENV OPENCLAW_VERSION=${OPENCLAW_VERSION}

RUN apk add --no-cache python3 py3-aiohttp py3-websockets

WORKDIR /app
COPY --from=ctx /ctx/addon-rootfs/app /app
# Committed workshop dist next to the package; workshop_static.py serves it at /.
COPY --from=ctx /ctx/workshop-out/dist /app/pilot_addon/workshop_dist

ENV PILOT_DATA=/data \
    PORT=8899

# s6 service starts python3 -m pilot.main via the run script.
COPY --from=ctx /ctx/addon-rootfs/etc /etc
