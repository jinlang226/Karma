"""
KARMA — Kubernetes microservice agent benchmark framework.

Subpackages
-----------
definitions
    Static data loading and validation for cases, workflows, prompts,
    and adversary scenarios.
environments
    Kubernetes execution context and environment provider registry.
agents
    Agent registry and per-agent container assets.
runtime
    Unified execution core consumed by both CLI and HTTP adapters.
transport
    Kubectl proxy daemon and the K8s transport API.
oracle
    Oracle execution and final regression sweep helpers.
sandbox
    Local and Docker agent launch and container lifecycle management.
protocol
    Run-directory layout, artifact path helpers, and file contracts.
evidence
    Snapshot collection, usage normalization, and metric dispatch.
metrics
    Leaf metric plugins and the metric registry.
judge
    LLM-as-Judge evaluation pipeline.
interfaces
    CLI and HTTP adapter layers.
"""
