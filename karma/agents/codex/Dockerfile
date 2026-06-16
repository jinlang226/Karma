# Codex agent image for docker-sandbox mode.
#
# Auth (two options):
#   - Mount your Codex credentials file:
#       --agent-auth-path ~/.codex/auth.json --agent-auth-dest /root/.codex/auth.json
#   - or set OPENAI_API_KEY (KARMA forwards it from the host).
# The kubeconfig pointing at KARMA's kubectl proxy is bind-mounted at
# /root/.kube/config and KUBECONFIG is set for it.
FROM node:20-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates git \
 && ARCH="$(dpkg --print-architecture)" \
 && curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && npm install -g @openai/codex \
 && apt-get purge -y --auto-remove curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY entrypoint.sh /agent/entrypoint.sh
RUN chmod +x /agent/entrypoint.sh
ENTRYPOINT ["/agent/entrypoint.sh"]
