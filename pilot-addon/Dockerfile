# syntax=docker/dockerfile:1
#
# Pilot add-on image. The build context may be either the repository root
# (CI, local builds) or the add-on folder alone (Supervisor source
# installs) — both produce the full image with the workshop SPA, because
# the workshop sources live inside the add-on folder (pilot-addon/workshop).
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
# The workshop sources sit at pilot-addon/workshop from the repo root and at
# workshop/ from this folder; expose both as fixed paths for later stages.
# Fails loudly if the workshop sources are missing — the UI is not optional.
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
    test -f /ctx/workshop-out/package.json

# --- Stage 2: build the workshop SPA -----------------------------------------
FROM node:24-alpine AS workshop
WORKDIR /w
COPY --from=ctx /ctx/workshop-out/ ./
# Retry fetches: slow/unstable links to the npm registry must not fail the build.
RUN npm ci --fetch-retries=5 --fetch-retry-mintimeout=15000 --fetch-retry-maxtimeout=120000 --fetch-timeout=300000 \
    && npm run build

# --- Stage 3: the add-on runtime ---------------------------------------------
FROM ${BUILD_FROM}

# OpenClaw runtime pin (build.yaml arg); the agent workspace arrives in Phase 5.
ARG OPENCLAW_VERSION
ENV OPENCLAW_VERSION=${OPENCLAW_VERSION}

RUN apk add --no-cache python3 py3-aiohttp py3-websockets

WORKDIR /app
COPY --from=ctx /ctx/addon-rootfs/app /app
# Workshop dist next to the package; workshop_static.py serves it at /.
COPY --from=workshop /w/dist /app/pilot_addon/workshop_dist

ENV PILOT_DATA=/data \
    PORT=8899

# s6 service starts python3 -m pilot.main via the run script.
COPY --from=ctx /ctx/addon-rootfs/etc /etc
