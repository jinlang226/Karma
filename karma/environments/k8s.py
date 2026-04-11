"""
Kubernetes execution context and namespace lifecycle management.

This module is the only place in KARMA that issues kubectl commands for
environment management. Agent kubectl calls are intercepted separately by
``transport.k8s.proxy``.

Import this module only through ``environments.registry``; never import
it directly from ``runtime.*``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class K8sEnvironment:
    """Kubernetes environment provider.

    Manages the cluster-side lifecycle of a stage run: namespace creation,
    manifest rendering, decoy deployment, and cleanup.

    Typical usage::

        env = K8sEnvironment(config)
        bindings = env.bind_namespace_roles(roles, run_id)
        env.ensure_namespaces(bindings, run_dir=stage_dir)
        env.plant_decoys(decoys, bindings, resources_dir=..., run_dir=...)
        # ... run the agent ...
        env.cleanup_namespaces(bindings, run_dir=stage_dir)
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the provider.

        Parameters
        ----------
        config:
            Optional overrides. Recognized keys: ``kubeconfig`` (path),
            ``namespace_prefix`` (str), ``force_delete_timeout_sec`` (int).
        """
        self._config = config or {}

    def bind_namespace_roles(
        self,
        roles: list[str],
        run_id: str,
    ) -> dict[str, str]:
        """Map logical namespace roles to physical namespace names.

        Physical names are derived deterministically from *run_id* and the
        role name so they are unique per run. This method does not create
        namespaces in the cluster; call :meth:`ensure_namespaces` for that.

        Returns
        -------
        dict
            Map of role name to physical namespace name.
        """
        ...

    def ensure_namespaces(
        self,
        role_bindings: dict[str, str],
        *,
        run_dir: Path,
    ) -> None:
        """Create and label the physical namespaces for a stage run.

        Idempotent: existing namespaces are labeled but not recreated.

        Raises
        ------
        RuntimeError
            When namespace creation fails.
        """
        ...

    def cleanup_namespaces(
        self,
        role_bindings: dict[str, str],
        *,
        run_dir: Path,
        force: bool = False,
    ) -> None:
        """Delete the physical namespaces for a stage run.

        Missing namespaces are silently skipped. When *force* is ``True``,
        ``--grace-period=0 --force`` flags are passed to handle stuck
        namespaces.

        Raises
        ------
        RuntimeError
            When deletion fails and *force* is ``False``.
        """
        ...

    def render_manifest(
        self,
        template_path: Path,
        role_bindings: dict[str, str],
    ) -> str:
        """Render a manifest template by substituting namespace placeholders.

        Replaces ``{{namespace.<role>}}`` tokens with the physical namespace
        names from *role_bindings*.

        Raises
        ------
        ValueError
            When the template references a role absent from *role_bindings*.
        """
        ...

    def plant_decoys(
        self,
        decoy_configs: list[dict[str, Any]],
        role_bindings: dict[str, str],
        *,
        resources_dir: Path,
        run_dir: Path,
    ) -> None:
        """Deploy decoy resources into the cluster for a stage run.

        Renders each decoy manifest with *role_bindings* and applies it via
        ``kubectl apply``.

        Raises
        ------
        RuntimeError
            When any decoy application fails.
        """
        ...

    def build_env_vars(
        self,
        role_bindings: dict[str, str],
        *,
        proxy_port: int,
    ) -> dict[str, str]:
        """Return environment variables to inject into the agent process.

        Includes ``KARMA_NS_<ROLE>=<physical_name>`` entries for each role
        and ``KARMA_KUBECTL_PROXY_PORT=<port>`` for the proxy address.
        """
        ...
