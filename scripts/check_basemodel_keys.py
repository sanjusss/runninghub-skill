#!/usr/bin/env python3
"""Compare data/basemodel_map.json keys with the RunningHub base model enum.

Polls POST /api/resource/baseModels on www.runninghub.cn and www.runninghub.ai
(anonymous endpoint, no API key) and diffs the returned enum against the
top-level keys of data/basemodel_map.json (keys starting with "_" such as
_meta are skipped). When both sites are queried their enums are compared to
each other as well; the file diff uses the union of the queried enums, so a
model offered on either site is never reported as "removed" by mistake.

Python 3.9+ standard library only.

Exit codes (the scheduled GitHub workflow depends on this contract):
  0  all sets in sync — stdout: "keys in sync (N)"
  1  drift found — stdout: one paste-ready line per difference
  2  network or parse failure — error on stderr, nothing on stdout
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BASEMODEL_MAP_PATH = SCRIPT_DIR.parent / "data" / "basemodel_map.json"

ALL_HOSTS = ["www.runninghub.cn", "www.runninghub.ai"]
BASEMODELS_PATH = "/api/resource/baseModels"
REQUEST_TIMEOUT = 20


def fetch_base_models(host: str) -> list[str]:
    """POST {} to one site and return its base model enum."""
    url = f"https://{host}{BASEMODELS_PATH}"
    req = urllib.request.Request(
        url, method="POST", data=b"{}",
        headers={"Content-Type": "application/json",
                 "User-Agent": "runninghub-skill/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{host}: HTTP {e.code} from {url}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"{host}: network error: {e}") from e
    if status >= 400:
        raise RuntimeError(f"{host}: HTTP {status} from {url}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{host}: non-JSON response: {text[:300]!r}") from e
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{host}: unexpected envelope: {text[:300]!r}")
    if parsed.get("code") not in (0, "0"):
        raise RuntimeError(f'{host}: API error {parsed.get("code")!r}: '
                           f'{parsed.get("msg")}')
    data = parsed.get("data")
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise RuntimeError(f"{host}: unexpected data payload: {text[:300]!r}")
    return data


def load_file_keys() -> set[str]:
    """Read the map file and return its top-level keys, skipping "_"-prefixed ones."""
    try:
        raw = json.loads(BASEMODEL_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"cannot read {BASEMODEL_MAP_PATH}: {e}") from e
    if not isinstance(raw, dict):
        raise RuntimeError(f"{BASEMODEL_MAP_PATH}: top-level JSON value is not an object")
    return {key for key in raw if not key.startswith("_")}


def diff_lines(enums: dict[str, list[str]], file_keys: set[str]) -> list[str]:
    """Return one stdout line per difference; an empty list means in sync."""
    lines: list[str] = []
    platform: set[str] = set()
    for enum in enums.values():
        platform.update(enum)
    added = sorted(platform - file_keys)
    removed = sorted(file_keys - platform)
    if added:
        lines.append(f"added: {', '.join(added)}")
    if removed:
        lines.append(f"removed: {', '.join(removed)}")
    if len(enums) == 2:
        cn = set(enums["www.runninghub.cn"])
        ai = set(enums["www.runninghub.ai"])
        cn_only = sorted(cn - ai)
        ai_only = sorted(ai - cn)
        if cn_only or ai_only:
            lines.append(f"host mismatch: cn-only=[{', '.join(cn_only)}] "
                         f"ai-only=[{', '.join(ai_only)}]")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diff data/basemodel_map.json keys against the RunningHub "
                    "base model enum (/api/resource/baseModels).")
    parser.add_argument("--host", choices=ALL_HOSTS,
                        help="check only this site (default: both)")
    args = parser.parse_args()
    hosts = [args.host] if args.host else ALL_HOSTS

    try:
        enums = {host: fetch_base_models(host) for host in hosts}
        file_keys = load_file_keys()
    except Exception as e:  # noqa: BLE001 — any failure here means exit code 2
        print(f"check failed: {e}", file=sys.stderr)
        return 2

    lines = diff_lines(enums, file_keys)
    if not lines:
        print(f"keys in sync ({len(file_keys)})")
        return 0
    for line in lines:
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
