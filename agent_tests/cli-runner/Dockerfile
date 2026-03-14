FROM debian:bookworm-slim AS wrapper-build

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY agent_tests/_common/kubectl_wrapper.c /src/kubectl_wrapper.c
RUN mkdir -p /out
RUN gcc -O2 -o /out/kubectl-wrapper /src/kubectl_wrapper.c

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV HOME=/home/agent

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash curl ca-certificates git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Allow global npm installs without root.
ENV NPM_CONFIG_PREFIX=/home/agent/.npm-global
ENV NPM_CONFIG_CACHE=/home/agent/.npm
ENV PATH=/home/agent/.npm-global/bin:$PATH

# Install real kubectl (root-only)
RUN mkdir -p /opt/real-kubectl \
    && curl -L -o /opt/real-kubectl/kubectl https://dl.k8s.io/release/v1.29.4/bin/linux/amd64/kubectl \
    && chmod 700 /opt/real-kubectl/kubectl

COPY --from=wrapper-build /out/kubectl-wrapper /usr/bin/kubectl
RUN chmod 4755 /usr/bin/kubectl

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code
# Install Codex
RUN npm i -g @openai/codex

WORKDIR /app
COPY agent_tests/_common/entrypoint_common.sh /opt/agent/entrypoint_common.sh
COPY agent_tests/cli-runner/entrypoint.sh /app/entrypoint.sh
COPY agent_tests/cli-runner/collect_token_usage.py /opt/agent/collect_token_usage.py
COPY agent_tests/cli-runner/system_prompt.txt /opt/agent/system_prompt.txt

RUN chmod +x /opt/agent/entrypoint_common.sh /app/entrypoint.sh /opt/agent/collect_token_usage.py

RUN useradd -m -u 1000 agent \
    && mkdir -p /home/agent/.npm-global \
    && mkdir -p /home/agent/.npm \
    && mkdir -p /home/agent/.codex \
    && chown -R agent:agent /home/agent

# Create .claude directory and config file for auth
RUN mkdir -p /home/agent/.claude \
    && echo '{}' > /home/agent/.claude.json \
    && chown -R agent:agent /home/agent/.claude /home/agent/.claude.json

USER agent

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["bash"]
