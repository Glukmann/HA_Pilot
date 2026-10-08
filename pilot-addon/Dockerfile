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
# The OpenClaw core is COPIED from the official ghcr.io image as a build
# stage (same registry as the base images). Its Node is glibc-linked, so
# the runtime base is Debian — an Alpine/musl base would not run it.
#
# Local build from the repo root:
#   docker build -f pilot-addon/Dockerfile -t pilot-addon .
# or from this folder:
#   docker build -t pilot-addon .
#
# HA's builder supplies BUILD_FROM per arch (build.yaml); the default keeps
# plain `docker build` working.

ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
ARG OPENCLAW_VERSION=2026.9.8

# --- Stage 1: stage the context and normalize paths -------------------------
# Uses ${BUILD_FROM} (ghcr.io) rather than a Docker Hub image: hosts behind
# flaky links to docker.io must still build (the Hub is the usual timeout).
# The workshop sits at pilot-addon/workshop from the repo root and at
# workshop/ from this folder; expose both as fixed paths for later stages.
# Fails loudly if the committed SPA dist is missing — the UI is not optional.
FROM ${BUILD_FROM} AS ctx
WORKDIR /ctx
COPY . .
RUN set -eux; \
    if [ -d pilot-addon/rootfs ]; then \
        cp -r pilot-addon/rootfs /ctx/addon-rootfs; \
        cp -r pilot-addon/workshop /ctx/workshop-out; \
        cp -r pilot-addon/openclaw-plugin /ctx/plugin; \
    else \
        cp -r rootfs /ctx/addon-rootfs; \
        cp -r workshop /ctx/workshop-out; \
        cp -r openclaw-plugin /ctx/plugin; \
    fi; \
    test -f /ctx/workshop-out/dist/index.html; \
    test -f /ctx/plugin/dist/index.js

# --- Stage 1b: the OpenClaw core ---------------------------------------------
# Copied, not installed: the official image already bundles the right Node
# (glibc-linked) and the full package with node_modules. Same registry
# (ghcr.io) as the base images — the build stays hermetic for hosts with
# flaky links. Pinned via OPENCLAW_VERSION (build.yaml arg).
FROM ghcr.io/openclaw/openclaw:${OPENCLAW_VERSION}-slim AS openclaw-core

# --- Stage 2: the add-on runtime (Debian base: the core's Node needs glibc) --
FROM ${BUILD_FROM}

# Re-declared: a global (pre-FROM) ARG is usable in FROM lines only; inside
# a stage it must be declared again to expand in ENV/RUN.
ARG OPENCLAW_VERSION

ENV OPENCLAW_VERSION=${OPENCLAW_VERSION} \
    NODE_ENV=production

# libstdc++6 — runtime dependency of the copied node binary (verified: ldd).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-aiohttp libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# The core: node + the whole /app package (entry: node /opt/openclaw/openclaw.mjs).
COPY --from=openclaw-core /usr/local/bin/node /usr/local/bin/node
COPY --from=openclaw-core /app /opt/openclaw

# The Pilot tool plugin (prebuilt, committed — hermetic installs on the host).
COPY --from=ctx /ctx/plugin /opt/openclaw-plugins/pilot-home
# Bare imports ("openclaw/plugin-sdk/*", "typebox") resolve from the plugin
# dir: openclaw via symlink to the core package, typebox copied (cp -L: the
# core's node_modules are pnpm symlinks).
RUN set -eux; \
    mkdir -p /opt/openclaw-plugins/pilot-home/node_modules; \
    ln -sfn /opt/openclaw /opt/openclaw-plugins/pilot-home/node_modules/openclaw; \
    cp -rL /opt/openclaw/node_modules/typebox /opt/openclaw-plugins/pilot-home/node_modules/; \
    node -e "import('/opt/openclaw-plugins/pilot-home/dist/index.js').then((m)=>{if(!m.default||m.default.id!=='pilot-home')process.exit(1)})"

# The `openclaw` CLI on PATH: the add-on's core bridge drives the gateway
# config through it (`openclaw config set`, `openclaw automations`).
RUN printf '#!/bin/sh\nexec node /opt/openclaw/openclaw.mjs "$@"\n' \
        > /usr/local/bin/openclaw \
    && chmod +x /usr/local/bin/openclaw \
    && openclaw --version

WORKDIR /app
COPY --from=ctx /ctx/addon-rootfs/app /app
# Committed workshop dist next to the package; workshop_static.py serves it at /.
COPY --from=ctx /ctx/workshop-out/dist /app/pilot_addon/workshop_dist

ENV PILOT_DATA=/data \
    PORT=8899

# s6 services start python3 -m pilot_addon.main and the OpenClaw gateway
# via the run scripts under /etc/services.d/.
COPY --from=ctx /ctx/addon-rootfs/etc /etc
