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
APP_SECRET = os.environ.get("BENCH_PARAM_APP_SECRET_NAME", "app-user-password")
READONLY_SECRET = os.environ.get("BENCH_PARAM_READONLY_SECRET_NAME", "readonly-user-password")
ADMIN_USER = os.environ.get("BENCH_PARAM_ADMIN_USERNAME", "admin-user")
APP_USER = os.environ.get("BENCH_PARAM_APP_USERNAME", "app-user")
READONLY_USER = os.environ.get("BENCH_PARAM_READONLY_USERNAME", "readonly-user")
APP_DB = os.environ.get("BENCH_PARAM_APP_DATABASE", "appdb")
REPORTS_COLLECTION = os.environ.get("BENCH_PARAM_REPORTS_COLLECTION", "reports")
REPORTING_ROLE = os.environ.get("BENCH_PARAM_REPORTING_ROLE_NAME", "reportingRole")
SEED_DOCS = int(os.environ.get("BENCH_PARAM_SEED_DOCS", "3"))


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


def role_has_privileges(role):
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


def user_has_role(user, role_name, role_db=None):
    for role in user.get("roles", []):
        if role.get("role") != role_name:
            continue
        if role_db is None or role.get("db") == role_db:
            return True
    return False


def creds(errors):
    admin_pw = get_secret_value(ADMIN_SECRET, "password", errors)
    app_pw = get_secret_value(APP_SECRET, "password", errors)
    ro_pw = get_secret_value(READONLY_SECRET, "password", errors)
    if errors:
        return None
    return {
        "admin_uri": f"mongodb://{ADMIN_USER}:{admin_pw}@localhost:27017/admin",
        "app_uri": f"mongodb://{APP_USER}:{app_pw}@localhost:27017/{APP_DB}?authSource=admin",
        "ro_uri": f"mongodb://{READONLY_USER}:{ro_pw}@localhost:27017/{APP_DB}?authSource=admin",
    }


def check_role():
    errors = []
    c = creds(errors)
    if errors:
        return fail("User management role check failed:", errors)

    role = load_json(
        c["admin_uri"],
        f'JSON.stringify(db.getSiblingDB("admin").getRole("{REPORTING_ROLE}",{{showPrivileges:true}}))',
        f"{REPORTING_ROLE} (admin)",
        errors,
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

    if role is None:
        errors.append(f"{REPORTING_ROLE} not found")
    elif not role_has_privileges(role):
        errors.append(f"{REPORTING_ROLE} missing find on {APP_DB}.{REPORTS_COLLECTION}")

    if errors:
        return fail("User management role check failed:", errors)
    print(role_db)
    return 0


def check_bindings():
    errors = []
    c = creds(errors)
    if errors:
        return fail("User management bindings check failed:", errors)

    role = load_json(
        c["admin_uri"],
        f'JSON.stringify(db.getSiblingDB("admin").getRole("{REPORTING_ROLE}",{{showPrivileges:true}}))',
        f"{REPORTING_ROLE} (admin)",
        [],
    )
    role_db = "admin" if role is not None else APP_DB

    app_user = load_json(c["admin_uri"], f'JSON.stringify(db.getUser("{APP_USER}"))', APP_USER, errors)
    ro_user = load_json(c["admin_uri"], f'JSON.stringify(db.getUser("{READONLY_USER}"))', READONLY_USER, errors)

    if isinstance(app_user, dict):
      if not user_has_role(app_user, "readWrite", APP_DB):
          errors.append(f"{APP_USER} missing readWrite on {APP_DB}")
    if isinstance(ro_user, dict):
      if not user_has_role(ro_user, "read", APP_DB):
          errors.append(f"{READONLY_USER} missing read on {APP_DB}")
      if not user_has_role(ro_user, REPORTING_ROLE, role_db):
          errors.append(f"{READONLY_USER} missing {REPORTING_ROLE}")

    return fail("User management bindings check failed:", errors)


def check_access():
    errors = []
    c = creds(errors)
    if errors:
        return fail("User management access check failed:", errors)

    count = load_json(
        c["admin_uri"],
        f'JSON.stringify(db.getSiblingDB("{APP_DB}").{REPORTS_COLLECTION}.countDocuments({{}}))',
        "seed count",
        errors,
    )
    if isinstance(count, int):
        if count < SEED_DOCS:
            errors.append(f"seed docs missing from {APP_DB}.{REPORTS_COLLECTION}")
    elif isinstance(count, str) and count.isdigit():
        if int(count) < SEED_DOCS:
            errors.append(f"seed docs missing from {APP_DB}.{REPORTS_COLLECTION}")
    else:
        errors.append("unable to verify seed count")

    app_insert = run_mongo(c["app_uri"], f'db.{REPORTS_COLLECTION}.insertOne({{ok:"app-write"}}, {{writeConcern:{{w:1}}}})')
    if app_insert.returncode != 0:
        errors.append(f"{APP_USER} insert failed: {app_insert.stderr.strip() or app_insert.stdout.strip()}")

    ro_read = run_mongo(c["ro_uri"], f'db.{REPORTS_COLLECTION}.findOne({{}})')
    if ro_read.returncode != 0:
        errors.append(f"{READONLY_USER} read failed: {ro_read.stderr.strip() or ro_read.stdout.strip()}")

    ro_agg = run_mongo(c["ro_uri"], f'db.{REPORTS_COLLECTION}.aggregate([{{$match:{{}}}}]).toArray().length')
    if ro_agg.returncode != 0:
        errors.append(f"{READONLY_USER} aggregate failed: {ro_agg.stderr.strip() or ro_agg.stdout.strip()}")

    ro_write = run_mongo(c["ro_uri"], f'db.{REPORTS_COLLECTION}.insertOne({{bad:"write"}})')
    ro_combined = (ro_write.stdout + ro_write.stderr).lower()
    if ro_write.returncode == 0 and "not authorized" not in ro_combined:
        errors.append(f"{READONLY_USER} insert unexpectedly succeeded")

    return fail("User management access check failed:", errors)


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
    print("User management verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
