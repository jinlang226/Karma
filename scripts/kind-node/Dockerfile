ARG BASE_IMAGE=kindest/node:v1.32.1
FROM ${BASE_IMAGE}

# Preserve the only known delta from the old personal node image while
# keeping the base on the official Kind node image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends socat udev \
    && rm -rf /var/lib/apt/lists/*
