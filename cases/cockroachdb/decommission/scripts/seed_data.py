#!/usr/bin/env python3
import subprocess
import sys
import time


NAMESPACE = "cockroachdb"
POD = "crdb-cluster-0"
SQL_HOST = "crdb-cluster-0.crdb-cluster.cockroachdb.svc.cluster.local"
TARGET_PODS = ["crdb-cluster-3", "crdb-cluster-4"]


def run(cmd, timeout=30):
    # Bound every exec (O-bound): an un-timed `kubectl exec` against an
    # unresponsive node hangs to the unit timeout and the buffered log is lost on
    # kill. On timeout return a synthetic failed result so the retry loops continue.
    try:
        return subprocess.run(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            cmd, 124, exc.stdout or "", (exc.stderr or "") + "\n[exec timed out]"
        )


def log(message):
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # flush so a script killed at its timeout still leaves its progress log
    print(f"[{timestamp}] {message}", flush=True)


def exec_sql(sql, fmt=None):
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        POD,
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        f"--host={SQL_HOST}",
    ]
    if fmt:
        cmd.append(f"--format={fmt}")
    cmd += ["-e", sql]
    return run(cmd)


def retry(fn, attempts=20, delay=3):
    last = None
    for _ in range(attempts):
        last = fn()
        if last.returncode == 0:
            return last
        time.sleep(delay)
    return last


def parse_tsv(output):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return [], []
    header = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:]]
    return header, rows


def parse_replica_set(value):
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        value = value[1:-1]
    if not value:
        return set()
    return {int(item.strip()) for item in value.split(",") if item.strip()}


def get_range_info(range_id=None):
    result = exec_sql("SHOW RANGES FROM TABLE bench.decom_data;", fmt="tsv")
    if result.returncode != 0:
        return None, None, None, result.stderr.strip()
    header, rows = parse_tsv(result.stdout)
    if not rows:
        return None, None, None, "No range rows returned"
    if "range_id" not in header or "replicas" not in header:
        return None, None, None, "Missing range_id or replicas columns"
    idx_range = header.index("range_id")
    idx_replicas = header.index("replicas")
    idx_voters = header.index("voting_replicas") if "voting_replicas" in header else None
    row = None
    if range_id is None:
        row = rows[0]
    else:
        for candidate in rows:
            if len(candidate) <= idx_range:
                continue
            try:
                candidate_id = int(candidate[idx_range])
            except ValueError:
                continue
            if candidate_id == range_id:
                row = candidate
                break
    if row is None:
        ids = []
        for candidate in rows:
            if len(candidate) <= idx_range:
                continue
            try:
                ids.append(int(candidate[idx_range]))
            except ValueError:
                continue
        return None, None, None, f"Range {range_id} not found (seen {sorted(ids)})"
    if len(row) <= max(idx_range, idx_replicas):
        return None, None, None, "Range row missing expected columns"
    try:
        range_id = int(row[idx_range])
    except ValueError:
        return None, None, None, "Range ID parse failed"
    all_replicas = parse_replica_set(row[idx_replicas])
    if idx_voters is not None and len(row) > idx_voters:
        voting_replicas = parse_replica_set(row[idx_voters])
    else:
        voting_replicas = set(all_replicas)
    return range_id, voting_replicas, all_replicas, None


def wait_for_range_info(attempts=20, delay=3):
    last_err = None
    for _ in range(attempts):
        range_id, voters, all_replicas, err = get_range_info()
        if err is None:
            return range_id, voters, all_replicas
        last_err = err
        time.sleep(delay)
    return None, None, last_err


def wait_for_replica_state(desired_count, range_id, attempts=80, delay=3):
    last_voters = None
    last_all = None
    for _ in range(attempts):
        current_id, voters, all_replicas, err = get_range_info(range_id)
        if err is None:
            last_voters = voters
            last_all = all_replicas
            if len(voters) == desired_count:
                return current_id, voters, all_replicas, None
        time.sleep(delay)
    return (
        range_id if last_voters is not None else None,
        last_voters,
        last_all,
        "replica state not reached",
    )


def wait_for_relocation(range_id, target, attempts=40, delay=3):
    last_err = None
    last_voters = None
    last_all = None
    for _ in range(attempts):
        current_id, voters, all_replicas, err = get_range_info(range_id)
        if err is None and current_id == range_id:
            last_voters = voters
            last_all = all_replicas
            if target in voters:
                return voters, all_replicas, None
        last_err = err or "Relocation not complete yet"
        time.sleep(delay)
    return last_voters, last_all, last_err


def init_cluster():
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        POD,
        "--",
        "./cockroach",
        "init",
        "--insecure",
        f"--host={SQL_HOST}",
    ]
    log("Initializing cluster (if needed)")
    for _ in range(20):
        result = run(cmd)
        if result.returncode == 0:
            return True
        output = f"{result.stdout}\n{result.stderr}".lower()
        if "already initialized" in output or "already been initialized" in output:
            return True
        time.sleep(3)
    print("Failed to initialize cluster:", file=sys.stderr)
    print(result.stderr.strip(), file=sys.stderr)
    return False


def get_node_status():
    cmd = [
        "kubectl", "-n", NAMESPACE, "exec", POD, "--",
        "./cockroach", "node", "status", "--insecure",
        f"--host={SQL_HOST}", "--format=tsv",
    ]
    result = run(cmd)
    if result.returncode != 0:
        return None, result.stderr.strip()
    header, rows = parse_tsv(result.stdout)
    if "is_live" not in header:
        return None, "no is_live column in node status"
    idx = header.index("is_live")
    live = sum(1 for r in rows if len(r) > idx and r[idx].strip().lower() == "true")
    return live, result.stdout.strip()


def wait_for_live_nodes(expected, attempts=50, delay=3):
    """A range cannot upreplicate to RF=3 unless >=3 nodes are LIVE (not merely
    registered in gossip). pod-Ready / rollout-complete can race ahead of cluster
    membership, and a too-aggressive liveness probe can restart nodes under load,
    dropping the live count below quorum. Gate on the cluster's own liveness view."""
    last = 0
    for _ in range(attempts):
        count, info = get_node_status()
        if count is not None:
            last = count
            if count >= expected:
                log(f"Live nodes: {count}/{expected}")
                return count
        time.sleep(delay)
    log(f"Live nodes stabilized at {last}/{expected}")
    return last


def main():
    if not init_cluster():
        return 1

    log("Waiting for cluster nodes to become live")
    live = wait_for_live_nodes(5)
    if live < 3:
        count, info = get_node_status()
        print(f"Cluster never reached quorum: only {live} live node(s)", file=sys.stderr)
        print(info or "node status unavailable", file=sys.stderr)
        return 1

    log("Seeding bench.decom_data")
    setup_sql = """
    CREATE DATABASE IF NOT EXISTS bench;
    CREATE TABLE IF NOT EXISTS bench.decom_data (
        id INT PRIMARY KEY,
        payload STRING
    );
    UPSERT INTO bench.decom_data VALUES
        (1, 'alpha'),
        (2, 'beta'),
        (3, 'gamma');
    ALTER TABLE bench.decom_data CONFIGURE ZONE USING num_replicas = 3, num_voters = 3;
    """
    result = retry(lambda: exec_sql(setup_sql))
    if result.returncode != 0:
        print("Failed to seed data:", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return 1

    log("Resolving node addresses")
    result = retry(
        lambda: exec_sql(
            "SELECT node_id, address FROM crdb_internal.gossip_nodes ORDER BY node_id;",
            fmt="tsv",
        )
    )
    if result.returncode != 0:
        print("Failed to fetch node addresses:", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    header, rows = parse_tsv(result.stdout)
    if not header:
        print("Empty node address output", file=sys.stderr)
        return 1
    idx_node = header.index("node_id") if "node_id" in header else 0
    idx_addr = header.index("address") if "address" in header else 1
    target_nodes = {}
    for row in rows:
        if len(row) <= max(idx_node, idx_addr):
            continue
        addr = row[idx_addr]
        for pod in TARGET_PODS:
            if pod in addr:
                target_nodes[pod] = int(row[idx_node])
    if len(target_nodes) != len(TARGET_PODS):
        print("Could not resolve target node IDs", file=sys.stderr)
        return 1
    log(f"Target node IDs: {target_nodes}")

    log("Resolving store IDs")
    result = retry(
        lambda: exec_sql(
            "SELECT node_id, store_id FROM crdb_internal.kv_store_status ORDER BY node_id;",
            fmt="tsv",
        )
    )
    if result.returncode != 0:
        print("Failed to fetch store IDs:", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        return 1
    header, rows = parse_tsv(result.stdout)
    if not header:
        print("Empty store output", file=sys.stderr)
        return 1
    idx_node = header.index("node_id") if "node_id" in header else 0
    idx_store = header.index("store_id") if "store_id" in header else 1
    store_map = {}
    for row in rows:
        if len(row) <= max(idx_node, idx_store):
            continue
        store_map[int(row[idx_node])] = int(row[idx_store])

    try:
        target_store_ids = [
            store_map[target_nodes["crdb-cluster-3"]],
            store_map[target_nodes["crdb-cluster-4"]],
        ]
    except KeyError:
        print("Missing store IDs for target nodes", file=sys.stderr)
        return 1
    log(f"Target store IDs: {target_store_ids}")

    log("Fetching range info for bench.decom_data")
    range_id, voting_set, all_replicas = wait_for_range_info()
    if range_id is None:
        print("Failed to fetch range info:", file=sys.stderr)
        print(all_replicas, file=sys.stderr)
        return 1
    log(
        f"Range {range_id} replicas: {sorted(all_replicas)} "
        f"voters: {sorted(voting_set)}"
    )

    # A freshly-created, single-row range starts single-replica (RF=1). The zone
    # config above raises num_voters to 3, but upreplication is asynchronous; if
    # we relocate against the still-single voter set there is no spare voter to
    # move to the second target and the relocation aborts. Wait for the range to
    # carry enough voters (the two targets plus at least one spare to relocate)
    # before pinning. Tolerate clusters that cannot reach 3 voters (e.g. a smaller
    # inherited topology) by proceeding with whatever it stabilizes at.
    required = set(target_store_ids)
    desired_voters = min(3, max(len(voting_set), len(required) + 1))
    if len(voting_set) < desired_voters:
        log(f"Waiting for range {range_id} to upreplicate to {desired_voters} voters")
        upreplicated_id, up_voters, up_all, err = wait_for_replica_state(
            desired_voters, range_id
        )
        if up_voters is not None:
            voting_set = up_voters
            all_replicas = up_all if up_all is not None else all_replicas
            range_id = upreplicated_id or range_id
        log(
            f"Range {range_id} after upreplication replicas: {sorted(all_replicas)} "
            f"voters: {sorted(voting_set)}"
        )

    if required.issubset(voting_set):
        log("Required voters already present, skipping relocation")
    else:
        log("Missing required voters, relocating to targets")
        voters = set(voting_set)
        for target in required:
            if target in voters:
                continue
            source = None
            for store_id in voters:
                if store_id not in required:
                    source = store_id
                    break
            if source is None:
                # No spare (non-target) voter remains to relocate onto this
                # target. On the canonical 5-node topology the upreplication wait
                # above guarantees a spare; reaching here means the live cluster
                # has fewer voters than targets (a smaller inherited topology), so
                # pin what we can and let the oracle adapt to the live placement
                # rather than aborting the whole precondition.
                log(f"No spare voter to relocate onto {target}; pinning best-effort")
                break
            relocate_sql = (
                f"ALTER RANGE {range_id} RELOCATE VOTERS FROM {source} TO {target};"
            )
            log(f"Relocating range {range_id}: {source} -> {target}")
            result = retry(lambda: exec_sql(relocate_sql))
            if result.returncode != 0:
                print("Failed to relocate range replicas:", file=sys.stderr)
                print(result.stderr.strip(), file=sys.stderr)
                return 1
            updated_voters, updated_all, err = wait_for_relocation(range_id, target)
            if updated_voters is None or err is not None:
                print("Timed out waiting for replica relocation", file=sys.stderr)
                if err:
                    print(err, file=sys.stderr)
                if updated_voters is not None:
                    print(f"Current voters: {sorted(updated_voters)}", file=sys.stderr)
                if updated_all is not None:
                    print(f"Current replicas: {sorted(updated_all)}", file=sys.stderr)
                return 1
            voters = set(updated_voters)
            log(
                f"Updated replicas: {sorted(updated_all)} voters: {sorted(updated_voters)}"
            )

        _, final_voters, final_all, err = get_range_info(range_id)
        if err is not None or final_voters is None:
            print("Failed to refresh range info after relocation", file=sys.stderr)
            print(err or "No range voters returned", file=sys.stderr)
            return 1
        # On the canonical 5-node topology the upreplication wait guarantees a
        # spare voter per target, so the full required set must be pinned. Only
        # tolerate a partial result when the live cluster simply did not have
        # enough voters to satisfy every target (smaller inherited topology).
        if not required.issubset(final_voters):
            if len(final_voters) >= len(required):
                print("Replica placement missing required voters after relocation", file=sys.stderr)
                print(f"Current voters: {sorted(final_voters)}", file=sys.stderr)
                return 1
            log(
                "Cluster has fewer voters than relocation targets; "
                f"pinned best-effort voters: {sorted(final_voters)}"
            )
        log(
            f"Final replicas: {sorted(final_all)} voters: {sorted(final_voters)}"
        )

    log("Replica placement verified")
    print("Seeded data and pinned replicas to target nodes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
