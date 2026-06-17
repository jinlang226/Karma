# GitHub Copilot CLI agent image for docker-sandbox mode.
#
# Local sandbox mode uses the host `copilot` CLI and does not build this image;
# docker mode runs the agent here. Auth: the in-container `copilot` reads
# GITHUB_TOKEN from the environment -- KARMA forwards it from the host
# (karma/sandbox.py). The kubeconfig pointing at KARMA's kubectl proxy is
# bind-mounted at /root/.kube/config and KUBECONFIG is set for it.
FROM node:20-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates git \
 && ARCH="$(dpkg --print-architecture)" \
 && curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && npm install -g @github/copilot \
 && apt-get purge -y --auto-remove curl \
 && rm -rf /var/lib/apt/lists/*

# KARMA mounts the stage dir (with prompt.txt) at /workspace and polls
# /workspace/submit.txt, so the agent works from there.
WORKDIR /workspace
COPY entrypoint.sh /agent/entrypoint.sh
RUN chmod +x /agent/entrypoint.sh
ENTRYPOINT ["/agent/entrypoint.sh"]
