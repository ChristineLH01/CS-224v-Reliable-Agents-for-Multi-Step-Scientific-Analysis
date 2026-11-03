# Sandbox image: one container per attempt. The agent CLI runs *inside* it, so every
# command the agent issues sees only this filesystem plus the attempt's /workspace mount.
ARG BASE_IMAGE=condaforge/miniforge3:26.7.2-0
FROM ${BASE_IMAGE}

ARG AGENT_UID=1000
ARG AGENT_GID=1000
ARG CLAUDE_CODE_VERSION=2.1.310

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl wget git unzip zip bzip2 xz-utils pigz zstd \
      procps psmisc less file jq tree nano vim-tiny build-essential \
 && rm -rf /var/lib/apt/lists/*

# Non-root user whose UID/GID match the host user, so files written into the
# bind-mounted /workspace stay owned by you. (Claude Code also refuses
# bypassPermissions as root.) The base image ships `ubuntu` as UID 1000, which
# would collide with the usual host UID on Linux.
RUN set -eux; \
    uid="${AGENT_UID}"; gid="${AGENT_GID}"; \
    if [ "$uid" = "0" ]; then uid=1000; fi; \
    if [ "$gid" = "0" ]; then gid=1000; fi; \
    if existing="$(getent passwd "$uid" | cut -d: -f1)" && [ -n "$existing" ]; then userdel -r "$existing" 2>/dev/null || userdel "$existing"; fi; \
    if ! getent group "$gid" >/dev/null; then groupadd -g "$gid" agent; fi; \
    useradd -m -u "$uid" -g "$gid" -s /bin/bash agent; \
    mkdir -p /workspace /opt/sandbox; \
    chown -R agent /opt/conda /workspace /opt/sandbox

USER agent
WORKDIR /home/agent

# Analysis environment, owned by the agent so `mamba install` / `pip install` work.
COPY --chown=agent environment.yml /opt/sandbox/environment.yml
RUN mamba env create -y -f /opt/sandbox/environment.yml \
 && mamba env export -n bio > /opt/sandbox/conda-env.lock.yml \
 && mamba clean -a -y

# Claude Code, pinned. Native installer -> ~/.local/bin/claude.
RUN curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_CODE_VERSION}" \
 && /home/agent/.local/bin/claude --version

# Codex CLI, pinned: the release's full Linux package (codex plus its tool host,
# exec server, rg and the bwrap its sandbox uses), checksum-verified -> ~/.local/bin/codex.
ARG CODEX_VERSION=0.166.0
RUN set -eux; arch="$(uname -m)"; \
    pkg="codex-package-${arch}-unknown-linux-musl.tar.gz"; \
    url="https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}"; \
    dest=/home/agent/.local/share/codex; mkdir -p "$dest"; cd /tmp; \
    curl -fsSLO "$url/$pkg"; curl -fsSL "$url/codex-package_SHA256SUMS" | grep "  $pkg\$" | sha256sum -c -; \
    tar -xzf "$pkg" -C "$dest"; rm -f "$pkg"; \
    ln -sf "$dest/bin/codex" /home/agent/.local/bin/codex; \
    ln -sf "$dest/bin/codex-code-mode-host" /home/agent/.local/bin/codex-code-mode-host; \
    /home/agent/.local/bin/codex --version

COPY --chown=agent TOOLS.md /opt/sandbox/TOOLS.md

# "bio" is the active env for every process (docker exec included), without
# relying on shell init files.
ENV CONDA_DEFAULT_ENV=bio \
    CONDA_PREFIX=/opt/conda/envs/bio \
    PATH=/home/agent/.local/bin:/opt/conda/envs/bio/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    HOME=/home/agent \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

RUN echo 'export PATH="/home/agent/.local/bin:/opt/conda/envs/bio/bin:/opt/conda/bin:$PATH"' >> /home/agent/.bashrc

LABEL org.bmbench.role=sandbox
WORKDIR /workspace
# The base image's ENTRYPOINT is tini, so PID 1 reaps orphans; the harness runs
# this as root and execs the agent as `agent`, so `pkill -u agent` never hits PID 1.
CMD ["sleep", "infinity"]
