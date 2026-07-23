#!/usr/bin/env python3
"""Phase E — discover web .feature files for file-level CI sharding.

Walks sample_feature_tests/ and emits an Azure DevOps matrix object: one entry per
shardable web feature file. Parallel sharding is WEB-ONLY — files carrying a
non-web platform tag (@appium/@mobile/@desktop/...) are excluded, because
mobile/desktop runs need a device/host per shard, not a stateless agent.

Usage:
    python scripts/list_features.py                 # matrix JSON (for Azure)
    python scripts/list_features.py --format list   # plain JSON array of paths

ponytail: tag scan is a line prefix check, not a Gherkin parser. A feature
file tagging itself @appium at the file level is enough to exclude it; that is
how the roadmap models platform tags. Swap in behave's parser only if tag
placement gets subtle.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# A feature file is excluded from web sharding if any of these tags appear:
# other platforms (need a device/host per shard) or network/manual demos that
# shouldn't run unattended in CI (@live hits a real site; @manual is opt-in).
NON_WEB_TAGS = {"appium", "mobile", "desktop", "native", "ios", "android",
                "windows", "mac", "live", "manual"}

_TAG = re.compile(r"@(\w+)")


def _tags_and_has_scenario(text):
    tags = set()
    has_scenario = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("@"):
            tags.update(_TAG.findall(s))
        elif s.startswith("Scenario"):  # Scenario / Scenario Outline
            has_scenario = True
    return tags, has_scenario


def is_web_shard(path):
    """True if this feature file has scenarios and no non-web platform tag."""
    tags, has_scenario = _tags_and_has_scenario(path.read_text(encoding="utf-8"))
    return has_scenario and tags.isdisjoint(NON_WEB_TAGS)


def discover(root="sample_feature_tests"):
    """Sorted list of web .feature file paths (posix, relative to cwd)."""
    return sorted(
        p.as_posix()
        for p in Path(root).rglob("*.feature")
        if is_web_shard(p)
    )


def to_matrix(paths):
    """Azure matrix object: unique safe key -> {featurePath: <file>}."""
    matrix, seen = {}, {}
    for p in paths:
        stem = re.sub(r"\W+", "_", Path(p).with_suffix("").as_posix()).strip("_")
        key = stem
        n = seen.get(stem, 0)
        if n:
            key = f"{stem}_{n}"
        seen[stem] = n + 1
        matrix[key] = {"featurePath": p}
    return matrix


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="sample_feature_tests")
    ap.add_argument("--format", choices=["matrix", "list"], default="matrix")
    args = ap.parse_args(argv)
    paths = discover(args.root)
    if not paths:
        sys.stderr.write(f"no web .feature files under {args.root!r}\n")
    out = paths if args.format == "list" else to_matrix(paths)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
