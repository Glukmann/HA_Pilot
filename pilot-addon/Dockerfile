FROM ghcr.io/home-assistant/amd64-base:3.21

# OpenClaw runtime pin (build.yaml arg); the agent workspace arrives in Phase 5.
ARG OPENCLAW_VERSION
ENV OPENCLAW_VERSION=${OPENCLAW_VERSION}

RUN apk add --no-cache python3 py3-aiohttp py3-websockets

WORKDIR /app
COPY rootfs/app /app

ENV PILOT_DATA=/data \
    PORT=8899

# s6 service starts python3 -m pilot.main via the run script.
COPY rootfs/etc /etc
