#!/usr/bin/env python3
import argparse
import base64
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongodb-replica")
POD = f"{CLUSTER_PREFIX}-0"
ADMIN_SECRET = os.environ.get("BENCH_PARAM_ADMIN_SECRET_NAME", "admin-user-password")
REPORTING_SECRET = os.environ.get("BENCH_PARAM_REPORTING_SECRET_NAME", "reporting-user-password")
ADMIN_USER = os.environ.get("BENCH_PARAM_ADMIN_USERNAME", "admin-user")
REPORTING_USER = os.environ.get("BENCH_PARAM_REPORTING_USERNAME", "reporting-user")
APP_DB = os.environ.get("BENCH_PARAM_APP_DATABASE", "appdb")
REPORTS_COLLECTION = os.environ.get("BENCH_PARAM_REPORTS_COLLECTION", "reports")
RAW_COLLECTION = os.environ.get("BENCH_PARAM_RAW_COLLECTION", "raw")
BAD_ROLE = os.environ.get("BENCH_PARAM_BAD_ROLE_NAME", "rawRead")
REPORTING_ROLE = os.environ.get("BENCH_PARAM_REPORTING_ROLE_NAME", "reportingRole")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fail(prefix, errors):
    if errors:
        print(prefix, file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def get_secret(secret_name, errors):
    res = run([
        "kubectl", "-n", NAMESPACE, "get", "secret", secret_name, "-o", "jsonpath={.data.password}"
    ])
    if res.returncode != 0:
        errors.append(f"Failed to read secret {secret_name}: {res.stderr.strip() or res.stdout.strip()}")
        return None
    try:
        return base64.b64decode((res.stdout or "").strip()).decode("utf-8")
    except Exception:
        errors.append(f"Failed to decode secret {secret_name}.password")
        return None


def run_mongo(uri, eval_str):
    return run([
        "kubectl", "-n", NAMESPACE, "exec", POD, "--", "mongosh", "--quiet", uri, "--eval", eval_str
    ])


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


def role_has_reports_privileges(role):
    for priv in role.get("privileges", []):
        resource = priv.get("resource", {})
        if resource.get("db") != APP_DB or resource.get("collection") != REPORTS_COLLECTION:
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


def credentials(errors):
    admin_pw = get_secret(ADMIN_SECRET, errors)
    reporting_pw = get_secret(REPORTING_SECRET, errors)
    if errors:
        return None
    return {
        "admin_uri": f"mongodb://{ADMIN_USER}:{admin_pw}@localhost:27017/admin",
        "reporting_uri": f"mongodb://{REPORTING_USER}:{reporting_pw}@localhost:27017/{APP_DB}?authSource=admin",
    }


def check_role():
    errors = []
    c = credentials(errors)
    if c is None:
        return fail("Custom roles role check failed:", errors)

    role = load_json(
        c["admin_uri"],
        f'JSON.stringify(db.getSiblingDB("admin").getRole("{REPORTING_ROLE}",{{showPrivileges:true}}))',
        f"{REPORTING_ROLE} (admin)",
        errors,
    )
    if role is None:
        role = load_json(
            c["admin_uri"],
            f'JSON.stringify(db.getSiblingDB("{APP_DB}").getRole("{REPORTING_ROLE}",{{showPrivileges:true}}))',
            f"{REPORTING_ROLE} ({APP_DB})",
            errors,
        )

    if role is None:
        errors.append(f"{REPORTING_ROLE} not found")
    else:
        if not role_has_reports_privileges(role):
            errors.append(f"{REPORTING_ROLE} missing find on {APP_DB}.{REPORTS_COLLECTION}")
        if role_touches_raw(role):
            errors.append(f"{REPORTING_ROLE} must not grant access to {APP_DB}.{RAW_COLLECTION}")

    return fail("Custom roles role check failed:", errors)


def check_bindings():
    errors = []
    c = credentials(errors)
    if c is None:
        return fail("Custom roles bindings check failed:", errors)

    user = load_json(c["admin_uri"], f'JSON.stringify(db.getUser("{REPORTING_USER}"))', REPORTING_USER, errors)
    if isinstance(user, dict):
        if not user_has_role(user, REPORTING_ROLE):
            errors.append(f"{REPORTING_USER} missing {REPORTING_ROLE}")
        if user_has_role(user, BAD_ROLE, APP_DB):
            errors.append(f"{REPORTING_USER} still has deprecated role {BAD_ROLE}")

    return fail("Custom roles bindings check failed:", errors)


def check_access():
    errors = []
    c = credentials(errors)
    if c is None:
        return fail("Custom roles access check failed:", errors)

    reports_read = run_mongo(c["reporting_uri"], f'db.{REPORTS_COLLECTION}.findOne({{}})')
    if reports_read.returncode != 0:
        errors.append(f"{REPORTING_USER} read on reports failed: {reports_read.stderr.strip() or reports_read.stdout.strip()}")

    reports_agg = run_mongo(c["reporting_uri"], f'db.{REPORTS_COLLECTION}.aggregate([{{$match:{{}}}}]).toArray().length')
    if reports_agg.returncode != 0:
        errors.append(f"{REPORTING_USER} aggregate on reports failed: {reports_agg.stderr.strip() or reports_agg.stdout.strip()}")

    raw_read = run_mongo(c["reporting_uri"], f'db.{RAW_COLLECTION}.findOne({{}})')
    raw_combined = (raw_read.stdout + raw_read.stderr).lower()
    if raw_read.returncode == 0 and "not authorized" not in raw_combined:
        errors.append(f"{REPORTING_USER} can read {RAW_COLLECTION} unexpectedly")

    reports_write = run_mongo(c["reporting_uri"], f'db.{REPORTS_COLLECTION}.insertOne({{bad:"write"}})')
    write_combined = (reports_write.stdout + reports_write.stderr).lower()
    if reports_write.returncode == 0 and "not authorized" not in write_combined:
        errors.append(f"{REPORTING_USER} can write {REPORTS_COLLECTION} unexpectedly")

    return fail("Custom roles access check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", default="all", choices=["all", "role", "bindings", "access"])
    args = parser.parse_args()

    if args.check == "role":
        return check_role()
    if args.check == "bindings":
        return check_bindings()
    if args.check == "access":
        return check_access()

    for fn in (check_role, check_bindings, check_access):
        rc = fn()
        if rc != 0:
            return rc
    print("Custom roles verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
