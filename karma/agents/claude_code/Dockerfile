# Claude Code agent image for docker-sandbox mode.
#
# Local sandbox mode uses the host `claude` CLI and does not build this image;
# docker mode runs the agent here. Auth: the in-container `claude` reads
# CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or ANTHROPIC_API_KEY from
# the environment -- KARMA forwards those from the host (sandbox.launch_agent).
# The kubeconfig pointing at KARMA's kubectl proxy is bind-mounted at
# /root/.kube/config and KUBECONFIG is set for it.
FROM node:20-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && ARCH="$(dpkg --print-architecture)" \
 && curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && npm install -g @anthropic-ai/claude-code \
 && apt-get purge -y --auto-remove curl \
 && rm -rf /var/lib/apt/lists/*

# KARMA mounts the stage dir (with prompt.txt) at /workspace and polls
# /workspace/submit.txt, so the agent works from there.
WORKDIR /workspace
COPY entrypoint.sh /agent/entrypoint.sh
RUN chmod +x /agent/entrypoint.sh
ENTRYPOINT ["/agent/entrypoint.sh"]
