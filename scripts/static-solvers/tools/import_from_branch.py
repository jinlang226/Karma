#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from _shared import STATIC_ROOT, VENDOR_BRANCH, VENDOR_ROOT, ensure_parent, git_ls_tree, git_show, now_utc_iso


RAW_COPY_PREFIXES = (
    "scripts/resource-solvers/",
    "resources/cockroachdb/",
    "resources/elasticsearch/",
    "resources/mongodb/",
    "resources/nginx-ingress/",
    "resources/rabbitmq-experiments/",
    "resources/ray/",
    "resources/spark/",
)


def _copy_repo_file(repo_path: str) -> None:
    dest = VENDOR_ROOT / repo_path
    ensure_parent(dest)
    dest.write_text(git_show(VENDOR_BRANCH, repo_path))


def main() -> int:
    copied = 0
    for prefix in RAW_COPY_PREFIXES:
        for repo_path in git_ls_tree(VENDOR_BRANCH, prefix):
            _copy_repo_file(repo_path)
            copied += 1

    stamp = VENDOR_ROOT / ".import-meta.txt"
    ensure_parent(stamp)
    stamp.write_text(
        f"source_branch={VENDOR_BRANCH}\n"
        f"imported_at={now_utc_iso()}\n"
        f"copied_files={copied}\n"
    )
    readme = STATIC_ROOT / "vendor" / "README.md"
    ensure_parent(readme)
    readme.write_text(
        "This directory contains raw copied reference assets from the\n"
        "`import-improve-resources` branch. Files under this subtree are vendored\n"
        "unchanged for provenance and should not be edited in place.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
