ARG BASE_IMAGE=kindest/node:v1.32.1
FROM ${BASE_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends socat udev \
    && rm -rf /var/lib/apt/lists/*
