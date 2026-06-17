# "api" agent image for docker-sandbox mode.
#
# Local sandbox mode runs run_agent.py with the host python3 (stdlib only, no
# install needed); docker mode runs the agent here. Auth: the loop reads
# KARMA_API_KEY / DEEPSEEK_API_KEY (and KARMA_API_BASE_URL / KARMA_API_MODEL)
# from the environment -- KARMA forwards them from the host (karma/sandbox.py).
# The kubeconfig pointing at KARMA's kubectl proxy is bind-mounted at
# /root/.kube/config and KUBECONFIG is set for it.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && ARCH="$(dpkg --print-architecture)" \
 && curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/${ARCH}/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && apt-get purge -y --auto-remove curl \
 && rm -rf /var/lib/apt/lists/*

# KARMA mounts the stage dir (with prompt.txt) at /workspace and polls
# /workspace/submit.txt, so the agent works from there.
WORKDIR /workspace
COPY run_agent.py /agent/run_agent.py
COPY entrypoint.sh /agent/entrypoint.sh
RUN chmod +x /agent/entrypoint.sh
ENTRYPOINT ["/agent/entrypoint.sh"]
