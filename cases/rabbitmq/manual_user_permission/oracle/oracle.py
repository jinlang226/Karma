import base64
import json
import subprocess
import sys
import os

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")


def run(cmd):
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()


def run_json(cmd):
    return json.loads(run(cmd))


def get_secret_value(name, key):
    data = run_json([
        "kubectl", "-n", NAMESPACE, "get", "secret", name, "-o", "json"
    ])
    raw = data.get("data", {}).get(key)
    if not raw:
        raise ValueError(f"Missing secret data: {name}/{key}")
    return base64.b64decode(raw).decode().strip()


def curl_api(path, user, password):
    return run([
        "kubectl", "-n", NAMESPACE, "exec", "oracle-client", "--",
        "curl", "-s", "-u", f"{user}:{password}",
        f"http://{CLUSTER_PREFIX}:15672{path}",
    ])


def main():
    errors = []

    # Pods ready
    try:
        pods = run_json([
            "kubectl", "-n", NAMESPACE, "get", "pods", "-l", f"app={CLUSTER_PREFIX}", "-o", "json"
        ])
    except subprocess.CalledProcessError as exc:
        errors.append(f"Failed to list RabbitMQ pods: {exc.output.decode().strip()}")
        pods = {"items": []}

    ready_pods = 0
    for item in pods.get("items", []):
        name = item.get("metadata", {}).get("name", "unknown")
        phase = item.get("status", {}).get("phase")
        statuses = item.get("status", {}).get("containerStatuses", [])
        if phase != "Running" or not statuses or not all(s.get("ready") for s in statuses):
            errors.append(f"Pod not ready: {name}")
        else:
            ready_pods += 1

    if ready_pods < 3:
        errors.append(f"Expected 3 RabbitMQ pods ready, got {ready_pods}")

    # Clients ready
    for dep in ("app-client", "ops-client"):
        try:
            dep_obj = run_json([
                "kubectl", "-n", NAMESPACE, "get", "deployment", dep, "-o", "json"
            ])
            ready = dep_obj.get("status", {}).get("readyReplicas", 0)
            if ready < 1:
                errors.append(f"{dep} is not Ready")
        except subprocess.CalledProcessError as exc:
            errors.append(f"Failed to read {dep}: {exc.output.decode().strip()}")

    # Admin creds
    try:
        admin_user = get_secret_value(f"{CLUSTER_PREFIX}-admin", "username")
        admin_pass = get_secret_value(f"{CLUSTER_PREFIX}-admin", "password")
    except Exception as exc:
        errors.append(f"Failed to read admin credentials: {exc}")
        admin_user = None
        admin_pass = None

    if admin_user and admin_pass:
        # vhosts
        try:
            vhosts = json.loads(curl_api("/api/vhosts", admin_user, admin_pass))
            names = {v.get("name") for v in vhosts}
            if "/app" not in names:
                errors.append("/app vhost missing")
            if "/ops" not in names:
                errors.append("/ops vhost missing")
        except Exception:
            errors.append("Failed to query vhosts")

        # permissions
        def check_perm(vhost, user, require=True):
            try:
                perm = json.loads(curl_api(f"/api/permissions/{vhost}/{user}", admin_user, admin_pass))
            except Exception:
                return None
            # When permissions are missing, the API returns an error JSON without user/vhost fields.
            if "user" not in perm or "vhost" not in perm:
                return None
            configure = perm.get("configure", "")
            write = perm.get("write", "")
            read = perm.get("read", "")
            if require:
                if not configure or not write or not read:
                    return False
                return True
            return False

        app_ok = check_perm("%2Fapp", "app-user", require=True)
        if app_ok is not True:
            errors.append("app-user does not have full permissions on /app")

        ops_ok = check_perm("%2Fops", "ops-user", require=True)
        if ops_ok is not True:
            errors.append("ops-user does not have full permissions on /ops")

        # deny cross-vhost
        app_on_ops = check_perm("%2Fops", "app-user", require=False)
        if app_on_ops is not None:
            errors.append("app-user should not have permissions on /ops")

        ops_on_app = check_perm("%2Fapp", "ops-user", require=False)
        if ops_on_app is not None:
            errors.append("ops-user should not have permissions on /app")

    # Queue checks
    try:
        queue_out = run([
            "kubectl", "-n", NAMESPACE, "exec", f"{CLUSTER_PREFIX}-0", "--",
            "rabbitmqctl", "-q", "list_queues", "-p", "/app", "name", "type", "messages"
        ])
        found = False
        for line in queue_out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 3 and parts[0] == "app-queue":
                found = True
                if parts[1] != "classic":
                    errors.append("app-queue is not classic")
                try:
                    messages = int(parts[2])
                except ValueError:
                    messages = 0
                if messages < 1:
                    errors.append("app-queue has no messages")
                break
        if not found:
            errors.append("app-queue not found in /app")
    except subprocess.CalledProcessError as exc:
        errors.append(f"Failed to list queues in /app: {exc.output.decode().strip()}")

    # Drift source check
    try:
        cron = run_json([
            "kubectl", "-n", NAMESPACE, "get", "cronjob", "perm-reloader", "-o", "json"
        ])
        # cronjob exists -> configmap must not reapply wrong perms
        cm = run_json([
            "kubectl", "-n", NAMESPACE, "get", "configmap", "perm-reloader", "-o", "json"
        ])
        script = cm.get("data", {}).get("apply.sh", "")
        if '"configure":""' in script:
            errors.append("perm-reloader still enforces wrong permissions")
    except subprocess.CalledProcessError:
        # cronjob not found is acceptable
        pass

    if errors:
        print("Manual user permission verification failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("Manual user permission verified.")


if __name__ == "__main__":
    main()
