#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongo-rs")
SERVICE_NAME = os.environ.get("BENCH_PARAM_SERVICE_NAME", "mongo")
REPLICA_SET_NAME = os.environ.get("BENCH_PARAM_REPLICA_SET_NAME", "rs0")
CLIENT_POD_NAME = os.environ.get("BENCH_PARAM_CLIENT_POD_NAME", "mongo-client")
EXTERNAL_HOST_PREFIX = os.environ.get("BENCH_PARAM_EXTERNAL_HOST_PREFIX", "domain-rs")
NODEPORT_START = int(os.environ.get("BENCH_PARAM_NODEPORT_START", "31181"))


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


_TLS_FLAGS_CACHE = None


def _mongo_tls_flags(probe_pod=None):
    """mongosh transport flags that adapt to the cluster's LIVE TLS mode.

    The environment PERSISTS across workflow stages, so an earlier stage
    (mongodb/tls-setup or mongodb/certificate-rotation) may have turned TLS on,
    after which a plain mongosh connection is refused. Detect TLS by probing the
    running mongo pod for a CA cert mounted at the paths the TLS stages use; if
    present, connect with --tls --tlsCAFile (and a client cert for mutual TLS
    when one is mounted), else connect plain. Standalone (no CA mounted) this
    returns [] -> identical plain behaviour. It only adds transport flags; every
    real check still runs and still fails when its condition is unmet.
    """
    global _TLS_FLAGS_CACHE
    if _TLS_FLAGS_CACHE is not None:
        return list(_TLS_FLAGS_CACHE)
    flags = []
    pod = probe_pod or f"{CLUSTER_PREFIX}-0"
    ca_path = None
    for cand in (
        "/etc/tls/ca.crt",
        "/etc/mongo-ca/ca.crt",
        "/etc/mongodb/tls/ca.crt",
        "/etc/ssl/mongodb/ca.crt",
    ):
        probe = run(["kubectl", "-n", NAMESPACE, "exec", pod, "--", "/bin/sh", "-c", "test -f " + cand])
        if probe.returncode == 0:
            ca_path = cand
            break
    if ca_path:
        flags = ["--tls", "--tlsAllowInvalidHostnames", "--tlsAllowInvalidCertificates", "--tlsCAFile", ca_path]
        for client_pem in ("/etc/tls/client.pem", "/etc/mongo-ca/client.pem"):
            cprobe = run(["kubectl", "-n", NAMESPACE, "exec", pod, "--", "/bin/sh", "-c", "test -f " + client_pem])
            if cprobe.returncode == 0:
                flags += ["--tlsCertificateKeyFile", client_pem]
                break
    _TLS_FLAGS_CACHE = flags
    return list(flags)


def _mongo_tls_uri_params():
    """The detected TLS settings as mongodb:// URI query params (tls=true&...).

    A connection-string URI defaults to tls=false, and that default OVERRIDES a
    command-line --tls flag -- so when we connect via a URI (the directConnection
    rs.conf read), the TLS options must live IN the URI, or mongosh connects plain
    and a persisted requireTLS server drops it ("connection <monitor> ... closed").
    Translate the same options _mongo_tls_flags() detects into URI-param form.
    """
    flags = _mongo_tls_flags()
    if not flags:
        return ""
    params = ["tls=true"]
    i = 0
    while i < len(flags):
        f = flags[i]
        if f == "--tlsAllowInvalidHostnames":
            params.append("tlsAllowInvalidHostnames=true")
        elif f == "--tlsAllowInvalidCertificates":
            params.append("tlsAllowInvalidCertificates=true")
        elif f == "--tlsCAFile":
            i += 1
            params.append("tlsCAFile=" + flags[i])
        elif f == "--tlsCertificateKeyFile":
            i += 1
            params.append("tlsCertificateKeyFile=" + flags[i])
        i += 1
    return "&".join(params)


def _resolve_expected_replicas():
    """Topology size to enforce.

    The environment PERSISTS across workflow stages, so an earlier
    replica-scaling stage may have grown the replica set past the standalone
    default of 3. Resolve the expected count from (in priority order): an
    explicit ``expected_replicas``/``target_replicas`` param override, else the
    LIVE StatefulSet (ready, else spec'd replicas), else the standalone default
    of 3. This adapts the topology/count check to whatever the workflow
    accumulated without loosening it -- a non-solving agent that drops or fails
    a member still mismatches the live ready/spec count.
    """
    for key in ("BENCH_PARAM_EXPECTED_REPLICAS", "BENCH_PARAM_TARGET_REPLICAS"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            try:
                return int(val)
            except ValueError:
                pass
    res = run(["kubectl", "-n", NAMESPACE, "get", "sts", CLUSTER_PREFIX, "-o", "json"])
    if res.returncode == 0:
        try:
            sts = json.loads(res.stdout)
            status = sts.get("status", {}) or {}
            spec = sts.get("spec", {}) or {}
            live = status.get("readyReplicas")
            if not isinstance(live, int) or live <= 0:
                live = spec.get("replicas")
            if isinstance(live, int) and live > 0:
                return live
        except (json.JSONDecodeError, AttributeError):
            pass
    return 3


EXPECTED_REPLICAS = _resolve_expected_replicas()


def fail(prefix, errors):
    if errors:
        print(prefix, file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def mongo_json(pod, eval_str, label, errors, uri=None):
    if uri:
        # Fold TLS into the URI: a connection-string's default (tls=false) overrides
        # a command-line --tls flag, so the detected TLS options must go in the URI.
        tls_q = _mongo_tls_uri_params()
        if tls_q:
            uri += ("&" if "?" in uri else "?") + tls_q
        cmd = ["kubectl", "-n", NAMESPACE, "exec", pod, "--", "mongosh", "--quiet", uri, "--eval", eval_str]
    else:
        cmd = ["kubectl", "-n", NAMESPACE, "exec", pod, "--", "mongosh", "--quiet",
               *_mongo_tls_flags(), "--eval", eval_str]
    res = run(cmd)
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"{label} failed: {detail}")
        return None
    raw = (res.stdout or "").strip()
    if not raw:
        errors.append(f"{label} returned empty output")
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        errors.append(f"Unable to parse {label} JSON output")
        return None


def check_topology():
    errors = []
    pod = f"{CLUSTER_PREFIX}-0"
    # Read rs.conf() via the member's own FQDN with directConnection=true to skip
    # SDAM topology monitoring, which a bare localhost connection would start and
    # which fails under a persisted requireTLS mode. Only the connection method
    # changes; the horizon assertions below are unchanged.
    local_uri = (f"mongodb://{pod}.{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:27017/"
                 "?directConnection=true&serverSelectionTimeoutMS=4000&connectTimeoutMS=4000")
    conf = mongo_json(pod, "JSON.stringify(rs.conf())", "rs.conf()", errors, uri=local_uri)
    if isinstance(conf, dict):
        members = conf.get("members", [])
        if len(members) != EXPECTED_REPLICAS:
            errors.append(f"Expected {EXPECTED_REPLICAS} members in rs.conf(), got {len(members)}")

        expected_hosts = {
            f"{CLUSTER_PREFIX}-{idx}.{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:27017": idx
            for idx in range(EXPECTED_REPLICAS)
        }
        for member in members:
            host = member.get("host")
            if host not in expected_hosts:
                errors.append(f"Unexpected member host: {host}")
                continue
            idx = expected_hosts[host]
            expected_horizon = f"{EXTERNAL_HOST_PREFIX}-{idx + 1}:{NODEPORT_START + idx}"
            horizons = member.get("horizons") or {}
            # Split-horizon: the horizon KEY name is client-chosen and arbitrary
            # (the prompt never dictates one); mongod selects a horizon by the
            # incoming connection's hostname, not by the label. So verify the
            # expected external endpoint is advertised under SOME horizon,
            # regardless of its key name.
            if expected_horizon not in horizons.values():
                errors.append(f"{host} expected horizon {expected_horizon}, got {dict(horizons)}")

    return fail("External access horizons topology check failed:", errors)


def check_services():
    errors = []
    for idx in range(EXPECTED_REPLICAS):
        svc_name = f"mongo-external-{idx}"
        res = run(["kubectl", "-n", NAMESPACE, "get", "svc", svc_name, "-o", "json"])
        if res.returncode != 0:
            detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
            errors.append(f"Failed to read service/{svc_name}: {detail}")
            continue
        try:
            svc = json.loads(res.stdout)
        except json.JSONDecodeError:
            errors.append(f"Failed to parse service/{svc_name} JSON")
            continue

        ports = svc.get("spec", {}).get("ports", [])
        node_port = ports[0].get("nodePort") if ports else None
        expected_node_port = NODEPORT_START + idx
        if node_port != expected_node_port:
            errors.append(f"{svc_name} nodePort expected {expected_node_port}, got {node_port}")

        selector = svc.get("spec", {}).get("selector", {})
        expected_selector = {"statefulset.kubernetes.io/pod-name": f"{CLUSTER_PREFIX}-{idx}"}
        if selector != expected_selector:
            errors.append(f"{svc_name} selector mismatch: expected={expected_selector} actual={selector}")

    return fail("External access horizons service check failed:", errors)


def check_connectivity():
    errors = []
    hosts = ",".join(
        f"{EXTERNAL_HOST_PREFIX}-{idx + 1}:{NODEPORT_START + idx}" for idx in range(EXPECTED_REPLICAS)
    )
    uri = f"mongodb://{hosts}/admin?replicaSet={REPLICA_SET_NAME}"

    res = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            CLIENT_POD_NAME,
            "--",
            "mongosh",
            "--quiet",
            *_mongo_tls_flags(),
            uri,
            "--eval",
            "JSON.stringify(db.hello())",
        ]
    )
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"mongo-client connectivity failed: {detail}")
        return fail("External access horizons connectivity check failed:", errors)

    try:
        hello = json.loads((res.stdout or "").strip())
    except json.JSONDecodeError:
        errors.append("Unable to parse mongo-client db.hello() output")
        return fail("External access horizons connectivity check failed:", errors)

    if hello.get("ok") != 1:
        errors.append("db.hello().ok != 1")
    if hello.get("setName") != REPLICA_SET_NAME:
        errors.append(f"Connected replica set expected {REPLICA_SET_NAME}, got {hello.get('setName')}")

    return fail("External access horizons connectivity check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", default="all", choices=["all", "topology", "services", "connectivity"])
    args = parser.parse_args()

    if args.check == "topology":
        return check_topology()
    if args.check == "services":
        return check_services()
    if args.check == "connectivity":
        return check_connectivity()

    for fn in (check_topology, check_services, check_connectivity):
        rc = fn()
        if rc != 0:
            return rc
    print("External access horizons verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
