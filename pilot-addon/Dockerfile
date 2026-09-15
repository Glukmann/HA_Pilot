# syntax=docker/dockerfile:1
#
# Pilot add-on image. The build context may be either the repository root
# (CI, local builds — the workshop SPA is then built from workshop/ sources)
# or the add-on folder alone (Supervisor source installs — the workshop
# stage is skipped gracefully and the UI answers 404 while the API works).
#
# Local build from the repo root:
#   docker build -f pilot-addon/Dockerfile -t pilot-addon .
#
# HA's builder supplies BUILD_FROM per arch (build.yaml); the default keeps
# plain `docker build` working.

ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.21

# --- Stage 1: stage the context and normalize paths -------------------------
# Exposes /ctx/addon-rootfs (always) and /ctx/workshop-out (only when the
# context contains workshop/ sources) so later stages can COPY blindly.
FROM alpine:3.21 AS ctx
WORKDIR /ctx
COPY . .
RUN set -eux; \
    if [ -d pilot-addon/rootfs ]; then \
        cp -r pilot-addon/rootfs /ctx/addon-rootfs; \
    else \
        cp -r rootfs /ctx/addon-rootfs; \
    fi; \
    if [ -f workshop/package.json ]; then \
        cp -r workshop /ctx/workshop-out; \
    else \
        mkdir /ctx/workshop-out; \
        echo "workshop sources not in build context — SPA will be skipped"; \
    fi

# --- Stage 2: build the workshop SPA -----------------------------------------
FROM node:24-alpine AS workshop
WORKDIR /w
COPY --from=ctx /ctx/workshop-out/ ./
RUN if [ -f package.json ]; then \
        npm ci && npm run build; \
    else \
        mkdir dist; \
    fi

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
