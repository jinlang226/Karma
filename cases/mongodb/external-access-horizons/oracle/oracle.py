#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongo-rs")
SERVICE_NAME = os.environ.get("BENCH_PARAM_SERVICE_NAME", "mongo")
REPLICA_SET_NAME = os.environ.get("BENCH_PARAM_REPLICA_SET_NAME", "rs0")
CLIENT_POD_NAME = os.environ.get("BENCH_PARAM_CLIENT_POD_NAME", "mongo-client")
EXTERNAL_HOST_PREFIX = os.environ.get("BENCH_PARAM_EXTERNAL_HOST_PREFIX", "domain-rs")
NODEPORT_START = int(os.environ.get("BENCH_PARAM_NODEPORT_START", "31181"))


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


_TLS_FLAGS_CACHE = {}


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
    # Cache PER POD: different pods mount different certs. The mongo-rs members
    # carry /etc/mongo-cert/server.pem (the client cert the cluster accepts), but
    # the mongo-client pod does NOT -- reusing the members' cached flags for the
    # connectivity check (which execs in mongo-client) yields
    # "ENOENT ... /etc/mongo-cert/server.pem". Probe each pod for its OWN mounts.
    pod = probe_pod or f"{CLUSTER_PREFIX}-0"
    if pod in _TLS_FLAGS_CACHE:
        return list(_TLS_FLAGS_CACHE[pod])
    flags = []
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
        # Mutual TLS: when cert-rotation/tls-setup leaves the cluster in requireTLS
        # with client-cert verification, mongosh MUST present a client keypair or the
        # server drops the monitor connection ("connection <monitor> ... closed"). The
        # agent's proven working command presents /etc/mongo-cert/server.pem (the
        # keypair mounted into the mongo pods), so probe it FIRST -- it is the cert the
        # live cluster actually accepts. Fall back to the dedicated client.pem paths a
        # different setup might mount. Gated by test -f, so standalone (no cert) is
        # untouched and the check stays workflow-agnostic.
        for client_pem in ("/etc/mongo-cert/server.pem", "/etc/tls/client.pem", "/etc/mongo-ca/client.pem"):
            cprobe = run(["kubectl", "-n", NAMESPACE, "exec", pod, "--", "/bin/sh", "-c", "test -f " + client_pem])
            if cprobe.returncode == 0:
                flags += ["--tlsCertificateKeyFile", client_pem]
                break
    _TLS_FLAGS_CACHE[pod] = flags
    return list(flags)


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
    # TLS goes as CLI flags -- mongosh honors --tls/--tlsCAFile/--tlsCertificateKeyFile
    # alongside a URI positional, but IGNORES file-path TLS options passed as URI query
    # params, so a cluster left in mutual TLS by cert-rotation drops a URI-folded
    # connection. Mirror the agent's working command: CLI TLS flags (incl. the client
    # cert) + a URI carrying only directConnection/timeouts.
    cmd = ["kubectl", "-n", NAMESPACE, "exec", pod, "--", "mongosh", "--quiet", *_mongo_tls_flags(pod)]
    if uri:
        cmd.append(uri)
    cmd.extend(["--eval", eval_str])
    # Single attempt. The retry/failover lives in the caller (check_topology),
    # which tries DIFFERENT members -- a same-pod retry can't recover a wedged
    # monitor connection.
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
    # Read rs.conf() from the FIRST member that answers. rs.conf() is REPLICATED
    # (identical on every member), so when one member's local mongosh monitor
    # connection is wedged -- the intermittent "connection <monitor> to
    # 127.0.0.1:27017 closed" seen on a deep requireTLS cluster, which SAME-pod
    # retries cannot recover because the wedge persists for the whole oracle window
    # -- another member answers. Try each member across a few rounds and use the
    # first valid read. The command mirrors the agent's proven one (NO connection
    # URI, CLI TLS flags only): a previous `directConnection=true` URI
    # deterministically wedged the single-node monitor once split-horizon was set.
    # Workflow-agnostic: the same replicated config is read regardless of how the
    # cluster got here, and standalone the first member answers immediately.
    conf = None
    last_errors = ["rs.conf() unreadable from any replica-set member"]
    member_pods = [f"{CLUSTER_PREFIX}-{i}" for i in range(EXPECTED_REPLICAS)]
    for _round in range(3):
        for member_pod in member_pods:
            attempt_errors = []
            c = mongo_json(member_pod, "JSON.stringify(rs.conf())", "rs.conf()", attempt_errors)
            if isinstance(c, dict):
                conf = c
                break
            last_errors = attempt_errors
        if conf is not None:
            break
        time.sleep(3)
    if conf is None:
        errors.extend(last_errors)
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


def _stage_client_tls_flags():
    """TLS flags for the external connectivity check from the mongo-client pod.

    The mongo-client fixture pod is a bare image with NO cert mounts. When a
    prior stage (tls-setup/certificate-rotation) leaves the cluster in mutual
    requireTLS, a certless plain connection is dropped by the server even with
    --tlsAllowInvalidCertificates (§2.4). The members DO hold the CA + a client
    keypair the server accepts; copy those out of a member pod and into the
    mongo-client pod, then return flags pointing at the staged paths so the
    oracle presents a valid client identity. Standalone (no TLS) the member has
    no CA mount -> returns [] -> identical plain behaviour.
    """
    member = f"{CLUSTER_PREFIX}-0"
    member_flags = _mongo_tls_flags(member)
    if not member_flags:
        return []  # cluster is plain; nothing to stage
    # Pull the CA path and (optional) client cert path the member uses.
    ca_src = None
    cert_src = None
    for i, tok in enumerate(member_flags):
        if tok == "--tlsCAFile":
            ca_src = member_flags[i + 1]
        elif tok == "--tlsCertificateKeyFile":
            cert_src = member_flags[i + 1]
    staged = ["--tls", "--tlsAllowInvalidHostnames", "--tlsAllowInvalidCertificates"]

    def _copy(src, dest):
        # Stream the file out of the member and into the client pod (no kubectl cp
        # dependency on tar). Returns True on success.
        cat = run(["kubectl", "-n", NAMESPACE, "exec", member, "--", "/bin/sh", "-c", "cat " + src])
        if cat.returncode != 0 or not cat.stdout:
            return False
        proc = subprocess.run(
            ["kubectl", "-n", NAMESPACE, "exec", "-i", CLIENT_POD_NAME, "--", "/bin/sh", "-c", "cat > " + dest],
            input=cat.stdout, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return proc.returncode == 0

    if ca_src and _copy(ca_src, "/tmp/oracle-ca.crt"):
        staged += ["--tlsCAFile", "/tmp/oracle-ca.crt"]
    if cert_src and _copy(cert_src, "/tmp/oracle-client.pem"):
        staged += ["--tlsCertificateKeyFile", "/tmp/oracle-client.pem"]
    return staged


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
            *_stage_client_tls_flags(),
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
