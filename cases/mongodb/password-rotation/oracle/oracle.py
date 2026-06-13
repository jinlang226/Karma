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
APP_OLD_SECRET = os.environ.get("BENCH_PARAM_APP_OLD_SECRET_NAME", "app-user-password-old")
APP_NEXT_SECRET = os.environ.get("BENCH_PARAM_APP_NEXT_SECRET_NAME", "app-user-password-next")
REPORTING_SECRET = os.environ.get("BENCH_PARAM_REPORTING_SECRET_NAME", "reporting-user-password")
ADMIN_USER = os.environ.get("BENCH_PARAM_ADMIN_USERNAME", "admin-user")
APP_USER = os.environ.get("BENCH_PARAM_APP_USERNAME", "app-user")
REPORTING_USER = os.environ.get("BENCH_PARAM_REPORTING_USERNAME", "reporting-user")
APP_DB = os.environ.get("BENCH_PARAM_APP_DATABASE", "appdb")
APP_COLLECTION = os.environ.get("BENCH_PARAM_APP_COLLECTION", "testdata")
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


def credentials(errors):
    admin_pw = get_secret(ADMIN_SECRET, errors)
    app_pw = get_secret(APP_SECRET, errors)
    app_old_pw = get_secret(APP_OLD_SECRET, errors)
    app_next_pw = get_secret(APP_NEXT_SECRET, errors)
    rep_pw = get_secret(REPORTING_SECRET, errors)
    if errors:
        return None
    return {
        "admin_pw": admin_pw,
        "app_pw": app_pw,
        "app_old_pw": app_old_pw,
        "app_next_pw": app_next_pw,
        "rep_pw": rep_pw,
        "admin_uri": f"mongodb://{ADMIN_USER}:{admin_pw}@localhost:27017/admin",
        "app_new_uri": f"mongodb://{APP_USER}:{app_next_pw}@localhost:27017/{APP_DB}?authSource=admin",
        "app_old_uri": f"mongodb://{APP_USER}:{app_old_pw}@localhost:27017/{APP_DB}?authSource=admin",
        "rep_uri": f"mongodb://{REPORTING_USER}:{rep_pw}@localhost:27017/{APP_DB}?authSource=admin",
    }


def check_secret():
    errors = []
    c = credentials(errors)
    if c is None:
        return fail("Password rotation secret check failed:", errors)
    if c["app_pw"] != c["app_next_pw"]:
        errors.append(f"{APP_SECRET} does not match {APP_NEXT_SECRET}")
    if c["app_pw"] == c["app_old_pw"]:
        errors.append(f"{APP_SECRET} still equals {APP_OLD_SECRET}")
    return fail("Password rotation secret check failed:", errors)


def check_auth():
    errors = []
    c = credentials(errors)
    if c is None:
        return fail("Password rotation auth check failed:", errors)

    old_try = run_mongo(c["app_old_uri"], "db.runCommand({connectionStatus:1}).ok")
    old_combined = (old_try.stdout + old_try.stderr).lower()
    if old_try.returncode == 0 and "not authorized" not in old_combined:
        errors.append("old app-user password still works")

    new_try = run_mongo(c["app_new_uri"], "db.runCommand({connectionStatus:1}).ok")
    if new_try.returncode != 0:
        errors.append(f"new app-user password failed: {new_try.stderr.strip() or new_try.stdout.strip()}")

    admin_try = run_mongo(c["admin_uri"], "db.runCommand({connectionStatus:1}).ok")
    if admin_try.returncode != 0:
        errors.append("admin authentication failed")

    return fail("Password rotation auth check failed:", errors)


def check_integrity():
    errors = []
    c = credentials(errors)
    if c is None:
        return fail("Password rotation integrity check failed:", errors)

    count = load_json(
        c["app_new_uri"],
        f'JSON.stringify(db.getSiblingDB("{APP_DB}").{APP_COLLECTION}.countDocuments({{}}))',
        "data count",
        errors,
    )
    if isinstance(count, int):
        if count < SEED_DOCS:
            errors.append(f"seed data missing from {APP_DB}.{APP_COLLECTION}")
    elif isinstance(count, str) and count.isdigit():
        if int(count) < SEED_DOCS:
            errors.append(f"seed data missing from {APP_DB}.{APP_COLLECTION}")
    else:
        errors.append("unable to verify data count")

    app_write = run_mongo(c["app_new_uri"], f'db.{APP_COLLECTION}.insertOne({{ok:"after-rotation"}}, {{writeConcern:{{w:1}}}})')
    if app_write.returncode != 0:
        errors.append(f"app-user write failed: {app_write.stderr.strip() or app_write.stdout.strip()}")

    rep_read = run_mongo(c["rep_uri"], f'db.{APP_COLLECTION}.findOne({{}})')
    if rep_read.returncode != 0:
        errors.append(f"reporting-user read failed: {rep_read.stderr.strip() or rep_read.stdout.strip()}")

    rep_write = run_mongo(c["rep_uri"], f'db.{APP_COLLECTION}.insertOne({{bad:"write"}})')
    rep_combined = (rep_write.stdout + rep_write.stderr).lower()
    if rep_write.returncode == 0 and "not authorized" not in rep_combined:
        errors.append("reporting-user write unexpectedly succeeded")

    return fail("Password rotation integrity check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", default="all", choices=["all", "secret", "auth", "integrity"])
    args = parser.parse_args()

    if args.check == "secret":
        return check_secret()
    if args.check == "auth":
        return check_auth()
    if args.check == "integrity":
        return check_integrity()

    for fn in (check_secret, check_auth, check_integrity):
        rc = fn()
        if rc != 0:
            return rc
    print("Password rotation verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
