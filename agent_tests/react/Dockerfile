FROM debian:bookworm-slim AS wrapper-build

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY agent_tests/_common/kubectl_wrapper.c /src/kubectl_wrapper.c
RUN mkdir -p /out
RUN gcc -O2 -o /out/kubectl-wrapper /src/kubectl_wrapper.c

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash curl ca-certificates iptables git \
    && rm -rf /var/lib/apt/lists/*

# Install real kubectl (root-only)
RUN mkdir -p /opt/real-kubectl \
    && curl -L -o /opt/real-kubectl/kubectl https://dl.k8s.io/release/v1.29.4/bin/linux/amd64/kubectl \
    && chmod 700 /opt/real-kubectl/kubectl

COPY --from=wrapper-build /out/kubectl-wrapper /usr/bin/kubectl
RUN chmod 4755 /usr/bin/kubectl

RUN pip install --no-cache-dir openai

WORKDIR /app
COPY agent_tests/_common/entrypoint_common.sh /opt/agent/entrypoint_common.sh
COPY agent_tests/react/entrypoint.sh /app/entrypoint.sh
COPY agent_tests/react/run_agent.py /app/run_agent.py

RUN chmod +x /opt/agent/entrypoint_common.sh /app/entrypoint.sh

RUN useradd -m -u 1000 agent
USER agent

ENTRYPOINT ["/app/entrypoint.sh"]
