"""
Kubernetes execution context and namespace lifecycle management.

This module is the only place in KARMA that issues kubectl commands for
environment management. Agent kubectl calls are intercepted separately by
``transport.k8s.proxy``.

Import this module only through ``environments.registry``; never import
it directly from ``runtime.*``.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .._warn import warn

_PLACEHOLDER_RE = re.compile(r"\{\{namespace\.([A-Za-z0-9_-]+)\}\}")
_NAMESPACE_PREFIX = "karma"
_FORCE_DELETE_TIMEOUT_SEC = 120
# Never deleted by the case-created-namespace sweep.
_PROTECTED_NAMESPACES = frozenset({
    "default", "kube-system", "kube-public", "kube-node-lease",
    "local-path-storage",
})


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
        self._prefix = str(self._config.get("namespace_prefix") or _NAMESPACE_PREFIX)
        self._force_timeout = int(
            self._config.get("force_delete_timeout_sec") or _FORCE_DELETE_TIMEOUT_SEC
        )
        kc = self._config.get("kubeconfig")
        self._kubeconfig: str | None = str(kc) if kc else None

    def _kubectl(self, args: list[str], *, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
        """Run a kubectl command with optional kubeconfig override."""
        import os
        env = dict(os.environ)
        if self._kubeconfig:
            env["KUBECONFIG"] = self._kubeconfig
        cmd = ["kubectl"] + args
        return subprocess.run(
            cmd, env=env, capture_output=True, text=True,
            check=check, timeout=timeout,
        )

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
        _safe = re.sub(r"[^a-z0-9-]", "-", run_id.lower())
        if len(_safe) > 40:
            # Plain [:40] truncation drops the run_id's unique timestamp, so a long
            # workflow name yields the SAME namespace for every run/attempt. A stale
            # Terminating namespace from a prior attempt then collides (ensure sees
            # AlreadyExists and proceeds against a dying namespace). Append a hash of
            # the full run_id to stay unique while bounded (<=40 chars).
            _h = hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:8]
            safe_run = _safe[:31].rstrip("-") + "-" + _h
        else:
            safe_run = _safe.rstrip("-")
        bindings: dict[str, str] = {}
        for role in (roles or []):
            safe_role = re.sub(r"[^a-z0-9-]", "-", str(role).lower()).strip("-")
            if safe_role == "default":
                bindings[role] = f"{self._prefix}-{safe_run}"
            else:
                bindings[role] = f"{self._prefix}-{safe_run}-{safe_role}"
        return bindings

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
        for role, ns_name in role_bindings.items():
            try:
                result = self._kubectl(["create", "namespace", ns_name], check=False)
                if result.returncode != 0 and "AlreadyExists" not in result.stderr:
                    for _retry in range(2):
                        time.sleep(0.5)
                        result = self._kubectl(
                            ["create", "namespace", ns_name], check=False
                        )
                        if result.returncode == 0 or "AlreadyExists" in result.stderr:
                            break
                    else:
                        # Retries exhausted and the namespace still does not exist.
                        raise RuntimeError(
                            f"kubectl create namespace failed after retries: "
                            f"{result.stderr.strip() or result.returncode}"
                        )
                self._kubectl([
                    "label", "namespace", ns_name,
                    "karma/role=" + role,
                    "karma/run-dir=" + str(run_dir.name),
                    "--overwrite",
                ], check=False)
            except Exception as exc:
                raise RuntimeError(
                    f"failed to ensure namespace '{ns_name}' for role '{role}': {exc}"
                ) from exc

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
        for role, ns_name in role_bindings.items():
            args = ["delete", "namespace", ns_name, "--ignore-not-found"]
            if force:
                args += ["--grace-period=0", "--force"]
            try:
                self._kubectl(args, check=not force, timeout=self._force_timeout)
            except Exception as exc:
                if not force:
                    raise RuntimeError(
                        f"failed to delete namespace '{ns_name}' for role '{role}': {exc}"
                    ) from exc

    def list_namespaces(self) -> set[str]:
        """Return the set of namespace names currently in the cluster."""
        try:
            out = self._kubectl(
                ["get", "namespaces", "-o", "name"], check=False, timeout=30
            ).stdout
        except Exception as exc:
            warn(f"failed to list namespaces: {exc}")
            return set()
        return {ln.split("/", 1)[-1].strip() for ln in out.splitlines() if ln.strip()}

    def cleanup_created_namespaces(
        self, baseline: set[str], *, run_dir: Path, force: bool = False
    ) -> list[str]:
        """Delete namespaces created since *baseline*.

        Cases that manage their own literal namespaces (e.g. ``mongodb``,
        ``cockroachdb``) create them in preconditions; the per-role teardown
        (:meth:`cleanup_namespaces`) only removes the ``karma-*`` role
        namespaces, so those literal namespaces would otherwise leak across
        runs. This removes every namespace not present in *baseline* and not a
        protected system namespace. Returns the names deleted.
        """
        created = sorted(self.list_namespaces() - set(baseline) - _PROTECTED_NAMESPACES)
        for ns_name in created:
            args = ["delete", "namespace", ns_name, "--ignore-not-found", "--wait=false"]
            if force:
                args += ["--grace-period=0", "--force"]
            try:
                self._kubectl(args, check=False, timeout=self._force_timeout)
            except Exception as exc:
                warn(f"failed to delete namespace {ns_name}: {exc}")
        return created

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
        template = template_path.read_text()

        def _replace(m: re.Match) -> str:
            role = m.group(1)
            if role not in role_bindings:
                raise ValueError(
                    f"manifest template references unknown role '{role}'; "
                    f"available: {list(role_bindings)}"
                )
            return role_bindings[role]

        return _PLACEHOLDER_RE.sub(_replace, template)

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
        for decoy in decoy_configs or []:
            path = resources_dir / str(decoy.get("path") or "")
            if not path.exists():
                raise RuntimeError(f"decoy manifest not found: {path}")
            rendered = self.render_manifest(path, role_bindings)
            ns = str(decoy.get("namespace") or "").strip()
            apply_args = ["apply", "-f", "-"]
            if ns:
                apply_args += ["-n", ns]
            try:
                import os
                env = dict(os.environ)
                if self._kubeconfig:
                    env["KUBECONFIG"] = self._kubeconfig
                subprocess.run(
                    ["kubectl"] + apply_args,
                    input=rendered, env=env, capture_output=True, text=True,
                    check=True, timeout=60,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"failed to apply decoy manifest '{path}': {exc}"
                ) from exc

    def build_namespace_env_vars(
        self,
        role_bindings: dict[str, str],
    ) -> dict[str, str]:
        """Return namespace environment variables for commands and prompts.

        Emits, for each role, ``KARMA_NS_<ROLE>``, ``BENCH_NS_<ROLE>``, and
        ``NS_<role>`` set to the physical namespace name, plus a single
        ``BENCH_NAMESPACE`` pointing at the default role's namespace (or the
        first bound namespace when no ``default`` role exists). The
        ``BENCH_*`` names are the ones case and scenario commands reference
        via ``${BENCH_NAMESPACE}`` / ``$BENCH_NS_<ROLE>``.
        """
        env: dict[str, str] = {}
        default_ns = role_bindings.get("default") or next(iter(role_bindings.values()), "")
        if default_ns:
            env["BENCH_NAMESPACE"] = default_ns
        for role, ns_name in role_bindings.items():
            role_key = re.sub(r"[^A-Z0-9]", "_", role.upper())
            env["KARMA_NS_" + role_key] = ns_name
            env["BENCH_NS_" + role_key] = ns_name
            env["NS_" + role] = ns_name
        return env

    def build_env_vars(
        self,
        role_bindings: dict[str, str],
        *,
        proxy_port: int,
    ) -> dict[str, str]:
        """Return environment variables to inject into the agent process.

        Includes the namespace variables from :meth:`build_namespace_env_vars`
        and ``KARMA_KUBECTL_PROXY_PORT=<port>`` for the proxy address.
        """
        env = self.build_namespace_env_vars(role_bindings)
        env["KARMA_KUBECTL_PROXY_PORT"] = str(proxy_port)
        return env
