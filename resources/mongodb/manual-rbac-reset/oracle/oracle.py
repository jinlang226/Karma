#!/usr/bin/env python3
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongodb-replica")
POD = f"{CLUSTER_PREFIX}-0"
ADMIN_SECRET = os.environ.get("BENCH_PARAM_ADMIN_SECRET_NAME", "admin-user-password")
APP_SECRET = os.environ.get("BENCH_PARAM_APP_SECRET_NAME", "app-user-password")
REPORTING_SECRET = os.environ.get("BENCH_PARAM_REPORTING_SECRET_NAME", "reporting-user-password")
ADMIN_USER = os.environ.get("BENCH_PARAM_ADMIN_USERNAME", "admin-user")
APP_USER = os.environ.get("BENCH_PARAM_APP_USERNAME", "app-user")
REPORTING_USER = os.environ.get("BENCH_PARAM_REPORTING_USERNAME", "reporting-user")
APP_DB = os.environ.get("BENCH_PARAM_APP_DATABASE", "appdb")
REPORTS_COLLECTION = os.environ.get("BENCH_PARAM_REPORTS_COLLECTION", "reports")
RAW_COLLECTION = os.environ.get("BENCH_PARAM_RAW_COLLECTION", "raw")
BAD_ROLE = os.environ.get("BENCH_PARAM_BAD_ROLE_NAME", "rawRead")
REPORTING_ROLE = os.environ.get("BENCH_PARAM_REPORTING_ROLE_NAME", "reportingRole")
SCRIPT_CM = os.environ.get("BENCH_PARAM_RESET_SCRIPT_CONFIGMAP_NAME", "mongodb-rbac-reset-script")
SCRIPT_KEY = os.environ.get("BENCH_PARAM_RESET_SCRIPT_KEY", "reset_rbac.sh")
ORACLE_SANDBOX_NS_FILE = os.environ.get("ORACLE_SANDBOX_NS_FILE", "oracle_sandbox_ns.txt")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fail(prefix, errors):
    if errors:
        print(prefix, file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def get_secret_value(secret_name, key, errors):
    res = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "secret",
            secret_name,
            "-o",
            f"jsonpath={{.data.{key}}}",
        ]
    )
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"Failed to read secret {secret_name}: {detail}")
        return None
    try:
        return base64.b64decode((res.stdout or "").strip()).decode("utf-8")
    except Exception:
        errors.append(f"Failed to decode secret {secret_name}.{key}")
        return None


def run_mongo(uri, eval_str):
    return run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            POD,
            "--",
            "mongosh",
            "--quiet",
            uri,
            "--eval",
            eval_str,
        ]
    )


def load_json(uri, eval_str, label, errors):
    res = run_mongo(uri, eval_str)
    if res.returncode != 0:
        errors.append(f"{label} failed: {res.stderr.strip() or res.stdout.strip()}")
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


def credentials(errors):
    admin_pw = get_secret_value(ADMIN_SECRET, "password", errors)
    app_pw = get_secret_value(APP_SECRET, "password", errors)
    reporting_pw = get_secret_value(REPORTING_SECRET, "password", errors)
    if errors:
        return None
    return {
        "admin_uri": f"mongodb://{ADMIN_USER}:{admin_pw}@localhost:27017/admin",
        "app_uri": f"mongodb://{APP_USER}:{app_pw}@localhost:27017/{APP_DB}?authSource=admin",
        "reporting_uri": f"mongodb://{REPORTING_USER}:{reporting_pw}@localhost:27017/{APP_DB}?authSource=admin",
    }


def load_reporting_role(c, errors):
    role = load_json(
        c["admin_uri"],
        f'JSON.stringify(db.getSiblingDB("admin").getRole("{REPORTING_ROLE}",{{showPrivileges:true}}))',
        f"{REPORTING_ROLE} (admin)",
        [],
    )
    role_db = "admin"
    if role is None:
        role = load_json(
            c["admin_uri"],
            f'JSON.stringify(db.getSiblingDB("{APP_DB}").getRole("{REPORTING_ROLE}",{{showPrivileges:true}}))',
            f"{REPORTING_ROLE} ({APP_DB})",
            errors,
        )
        role_db = APP_DB
    return role, role_db


def role_has_reports_privileges(role):
    for priv in role.get("privileges", []):
        resource = priv.get("resource", {})
        if resource.get("db") != APP_DB:
            continue
        if resource.get("collection") != REPORTS_COLLECTION:
            continue
        actions = set(priv.get("actions", []))
        if "find" in actions:
            return True
    return False


def role_touches_raw(role):
    for priv in role.get("privileges", []):
        resource = priv.get("resource", {})
        if resource.get("db") == APP_DB and resource.get("collection") == RAW_COLLECTION:
            return True
    return False


def user_has_role(user, role_name, role_db=None):
    for role in user.get("roles", []):
        if role.get("role") != role_name:
            continue
        if role_db is None or role.get("db") == role_db:
            return True
    return False


def get_script_text(errors):
    res = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "configmap",
            SCRIPT_CM,
            "-o",
            "json",
        ]
    )
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"Failed to read ConfigMap {SCRIPT_CM}/{SCRIPT_KEY}: {detail}")
        return None
    try:
        payload = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        errors.append(f"Failed to parse ConfigMap {SCRIPT_CM} JSON payload")
        return None
    text = ((payload.get("data") or {}).get(SCRIPT_KEY)) or ""
    if not text.strip():
        errors.append(f"ConfigMap {SCRIPT_CM} key {SCRIPT_KEY} is empty")
        return None
    return text


def _contains_any(text, needles):
    lowered = text.lower()
    return any(n.lower() in lowered for n in needles)


def _detail_from_result(result):
    parts = []
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if out:
        parts.append(f"stdout={out}")
    if err:
        parts.append(f"stderr={err}")
    return "; ".join(parts) if parts else f"exit {result.returncode}"


def get_sandbox_namespace(errors):
    try:
        text = open(ORACLE_SANDBOX_NS_FILE, "r", encoding="utf-8").read().strip()
    except OSError as exc:
        errors.append(f"Failed to read sandbox namespace file {ORACLE_SANDBOX_NS_FILE}: {exc}")
        return None
    if not text:
        errors.append(f"Sandbox namespace file {ORACLE_SANDBOX_NS_FILE} is empty")
        return None
    check = run(["kubectl", "get", "ns", text, "-o", "name"])
    if check.returncode != 0:
        errors.append(f"Sandbox namespace {text} missing: {_detail_from_result(check)}")
        return None
    return text


def run_script_in_sandbox(mode, errors):
    text = get_script_text(errors)
    if text is None:
        return
    sandbox_ns = get_sandbox_namespace(errors)
    if sandbox_ns is None:
        return

    script_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="oracle-rbac-script-",
            suffix=".sh",
            delete=False,
        ) as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
            script_path = handle.name

        os.chmod(script_path, 0o755)
        first_line = text.splitlines()[0] if text.splitlines() else ""
        syntax_shell = "bash" if "bash" in first_line else "sh"
        syntax = run([syntax_shell, "-n", script_path])
        if syntax.returncode != 0:
            errors.append(f"reset script syntax check failed: {_detail_from_result(syntax)}")
            return

        env = os.environ.copy()
        env["BENCH_NAMESPACE"] = sandbox_ns
        env["NAMESPACE"] = sandbox_ns
        execute = subprocess.run(
            [script_path, "--mode", mode],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        if execute.returncode != 0:
            errors.append(
                f"reset script execution failed for --mode {mode}: {_detail_from_result(execute)}"
            )
            return
    finally:
        if script_path:
            try:
                os.remove(script_path)
            except OSError:
                pass


def check_script_presence():
    errors = []
    _ = get_script_text(errors)
    return fail("RBAC reset script presence check failed:", errors)


def check_script_contract():
    errors = []
    text = get_script_text(errors)
    if text is None:
        return fail("RBAC reset script contract check failed:", errors)

    lines = text.strip().splitlines()
    if not lines or not lines[0].startswith("#!"):
        errors.append("reset script must include a shebang line")
    elif "sh" not in lines[0] and "bash" not in lines[0]:
        errors.append("reset script shebang must target sh/bash")

    lowered = text.lower()
    for mode in ("all", "app", "reporting"):
        if mode not in lowered:
            errors.append(f"reset script must handle mode '{mode}'")

    if "--mode" not in text and "mode=" not in lowered and "mode " not in lowered:
        errors.append("reset script must parse a mode argument")

    if not _contains_any(text, ["mongosh"]):
        errors.append("reset script must use mongosh to apply RBAC changes")

    if not _contains_any(text, ["createRole", "updateRole"]):
        errors.append("reset script must reconcile reporting role definition")

    if not _contains_any(text, ["createUser", "updateUser"]):
        errors.append("reset script must reconcile MongoDB users")

    if REPORTING_ROLE.lower() not in lowered:
        errors.append(f"reset script must reference reporting role '{REPORTING_ROLE}'")

    return fail("RBAC reset script contract check failed:", errors)


def check_script_executable():
    errors = []
    run_script_in_sandbox("app", errors)
    return fail("RBAC reset script executable check failed:", errors)


def check_script_apply_all():
    errors = []
    run_script_in_sandbox("all", errors)
    return fail("RBAC reset script apply-all check failed:", errors)


def check_role():
    errors = []
    c = credentials(errors)
    if c is None:
        return fail("RBAC reset role check failed:", errors)

    role, _role_db = load_reporting_role(c, errors)
    if role is None:
        errors.append(f"{REPORTING_ROLE} not found")
    else:
        if not role_has_reports_privileges(role):
            errors.append(
                f"{REPORTING_ROLE} missing find on {APP_DB}.{REPORTS_COLLECTION}"
            )
        if role_touches_raw(role):
            errors.append(
                f"{REPORTING_ROLE} must not grant access to {APP_DB}.{RAW_COLLECTION}"
            )

    return fail("RBAC reset role check failed:", errors)


def check_bindings():
    errors = []
    c = credentials(errors)
    if c is None:
        return fail("RBAC reset bindings check failed:", errors)

    _role, role_db = load_reporting_role(c, [])
    app_user = load_json(c["admin_uri"], f'JSON.stringify(db.getUser("{APP_USER}"))', APP_USER, errors)
    reporting_user = load_json(
        c["admin_uri"],
        f'JSON.stringify(db.getUser("{REPORTING_USER}"))',
        REPORTING_USER,
        errors,
    )

    if isinstance(app_user, dict):
        if not user_has_role(app_user, "readWrite", APP_DB):
            errors.append(f"{APP_USER} missing readWrite on {APP_DB}")

    if isinstance(reporting_user, dict):
        if not user_has_role(reporting_user, REPORTING_ROLE, role_db):
            errors.append(f"{REPORTING_USER} missing {REPORTING_ROLE}")
        if user_has_role(reporting_user, BAD_ROLE, APP_DB):
            errors.append(f"{REPORTING_USER} still has deprecated role {BAD_ROLE}")

    return fail("RBAC reset bindings check failed:", errors)


def check_access():
    errors = []
    c = credentials(errors)
    if c is None:
        return fail("RBAC reset access check failed:", errors)

    admin_ping = run_mongo(c["admin_uri"], 'db.adminCommand({ping:1}).ok')
    if admin_ping.returncode != 0:
        errors.append(f"admin ping failed: {admin_ping.stderr.strip() or admin_ping.stdout.strip()}")

    app_write = run_mongo(c["app_uri"], f'db.{REPORTS_COLLECTION}.insertOne({{ok:"app-write"}}, {{writeConcern:{{w:1}}}})')
    if app_write.returncode != 0:
        errors.append(f"{APP_USER} write failed: {app_write.stderr.strip() or app_write.stdout.strip()}")

    reporting_read = run_mongo(c["reporting_uri"], f'db.{REPORTS_COLLECTION}.findOne({{}})')
    if reporting_read.returncode != 0:
        errors.append(
            f"{REPORTING_USER} read on reports failed: {reporting_read.stderr.strip() or reporting_read.stdout.strip()}"
        )

    reporting_agg = run_mongo(c["reporting_uri"], f'db.{REPORTS_COLLECTION}.aggregate([{{$match:{{}}}}]).toArray().length')
    if reporting_agg.returncode != 0:
        errors.append(
            f"{REPORTING_USER} aggregate on reports failed: {reporting_agg.stderr.strip() or reporting_agg.stdout.strip()}"
        )

    reporting_raw_read = run_mongo(c["reporting_uri"], f'db.{RAW_COLLECTION}.findOne({{}})')
    raw_read_combined = (reporting_raw_read.stdout + reporting_raw_read.stderr).lower()
    if reporting_raw_read.returncode == 0 and "not authorized" not in raw_read_combined:
        errors.append(f"{REPORTING_USER} can read {RAW_COLLECTION} unexpectedly")

    reporting_write = run_mongo(c["reporting_uri"], f'db.{REPORTS_COLLECTION}.insertOne({{bad:"write"}})')
    write_combined = (reporting_write.stdout + reporting_write.stderr).lower()
    if reporting_write.returncode == 0 and "not authorized" not in write_combined:
        errors.append(f"{REPORTING_USER} can write {REPORTS_COLLECTION} unexpectedly")

    return fail("RBAC reset access check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        default="all",
        choices=[
            "all",
            "script_presence",
            "script_contract",
            "script_executable",
            "script_apply_all",
            "role",
            "bindings",
            "access",
        ],
    )
    args = parser.parse_args()

    if args.check == "script_presence":
        return check_script_presence()
    if args.check == "script_contract":
        return check_script_contract()
    if args.check == "script_executable":
        return check_script_executable()
    if args.check == "script_apply_all":
        return check_script_apply_all()
    if args.check == "role":
        return check_role()
    if args.check == "bindings":
        return check_bindings()
    if args.check == "access":
        return check_access()

    for fn in (
        check_script_presence,
        check_script_contract,
        check_script_executable,
        check_script_apply_all,
        check_role,
        check_bindings,
        check_access,
    ):
        rc = fn()
        if rc != 0:
            return rc
    print("Manual RBAC reset verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
