"""
Environment provider registry and selection.

The registry decouples ``runtime.*`` from any concrete provider
implementation. ``runtime.service`` calls :func:`get_environment` and
receives an initialized provider object without importing
``environments.k8s`` directly.
"""

from __future__ import annotations

from typing import Any

from .k8s import K8sEnvironment

_REGISTRY: dict[str, type] = {
    "kubernetes": K8sEnvironment,
    "k8s": K8sEnvironment,
}

_DEFAULT_PROVIDER = "kubernetes"


def get_environment(
    provider: str | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> Any:
    """Return an initialized environment instance for the named provider.

    Uses the default provider (``"kubernetes"``) when *provider* is
    ``None``.

    Parameters
    ----------
    provider:
        Provider name. Accepted values: ``"kubernetes"``, ``"k8s"``.
    config:
        Provider-specific configuration dict forwarded to the constructor.

    Raises
    ------
    ValueError
        When *provider* is not registered.
    """
    key = (provider or _DEFAULT_PROVIDER).lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f"unknown environment provider: {provider!r}. "
            f"Available: {', '.join(sorted(_REGISTRY))}"
        )
    return cls(config=config or {})


def list_providers() -> list[str]:
    """Return the sorted list of registered provider names."""
    return sorted(_REGISTRY)
