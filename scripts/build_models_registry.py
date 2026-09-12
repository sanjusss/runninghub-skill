#!/usr/bin/env python3
"""Rebuild data/models.json from the official RunningHub API docs.

The RunningHub API reference is an Apifox project that publishes a plain-text
index (llms.txt) plus one Markdown page per endpoint containing an OpenAPI 3.0
YAML block. This script fetches the index, downloads every 标准模型 API page,
extracts each endpoint's path/params/defaults, and writes a compact registry.

Requirements: python3 + PyYAML (import yaml) + network access.
Run from anywhere:  python3 build_models_registry.py [--host www.runninghub.cn]

The registry is committed under data/ so that the skill itself never needs
PyYAML or network access to list models — rerun this script only when
RunningHub publishes new models.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    raise SystemExit("PyYAML is required: python3 -m pip install PyYAML") from None

DOC_INDEX = "/runninghub-api-doc-cn/llms.txt"

# Doc-tree sections that describe model (generation) endpoints. Everything
# else in the index (AI 应用, ComfyUI 工作流, 任务查询, 资源上传, 账户相关...)
# is a fixed platform API documented in references/api-reference.md.
MODEL_SECTIONS = ("模型API", "标准模型API")
ALLOWED_HOSTS = ("www.runninghub.cn", "www.runninghub.ai")
AUTH_QUERY_KEYS = {"rh-comfy-auth", "rh-identify", "q-ak", "q-signature"}
URL_RE = re.compile(r"https?://[^\s<>\"']+")

# The doc-tree breadcrumb (segment 3) is the authority for task naming; we
# only normalise case/merges here. Verify against llms.txt before editing:
#   grep -oP '^- (模型API|标准模型API) > [^[]+ \[' llms.txt
TASK_BY_BREADCRUMB = {
    "image-to-3D": "image-to-3d",
    "multi-image-to-3D": "multi-image-to-3d",
    "text-to-3D": "text-to-3d",
    "image-edit": "image-to-image",
    "image-to-image-flux": "image-to-image",
    "video-to-video": "video-edit",
    "speech": "text-to-speech",
    "music-generation": "text-to-music",
    "lyrics-generation": "text-to-lyrics",
    "song-extend": "audio-to-audio",
    "voice-clone": "audio-to-audio",
    "lip-sync": "lip-sync-video",
    "avatar-video": "digital-human",
    "upload-character": "video-tools",
    "short-play-video": "video-tools",
    "video-draft-enhance": "video-tools",
    "video-denoise": "video-tools",
    "video-fps-increase": "video-tools",
    "video-frame-interpolation": "video-tools",
    "video-subtitle-erase": "video-tools",
    "video-translate": "video-tools",
    "video-transition": "video-tools",
    "video-effects": "video-tools",
    "multi-image-to-world": "image-to-world",
}

# Endpoints the docs file under the catch-all "其他" (2-segment breadcrumb),
# classified by hand from their titles. New entries under 其他 stay "other"
# and should be added here after checking their doc page.
EXPLICIT_TASK_OVERRIDE = {
    "minimax/hailuo-h3/context-ir-image": "prompt-enhance",
    "minimax/hailuo-h3/context-ir-multimodal": "prompt-enhance",
    "minimax/hailuo-h3/context-ir-text": "prompt-enhance",
    "minimax/hailuo-h3/regeneration-image-to-video": "video-upscale",
    "minimax/hailuo-h3/regeneration-multimodal-to-video": "video-upscale",
    "minimax/hailuo-h3/regeneration-text-to-video": "video-upscale",
    "seedream-v5-pro/layer-decomposition": "image-layer-decomposition",
}

# Utility endpoints that are not generation models — dropped from the
# registry so `rh.py model <endpoint>` never offers them.
EXCLUDED_SUBTASKS = {"upload-file"}
EXCLUDED_ENDPOINTS = {"mureka-ai/files-upload"}

OUTPUT_TYPE_BY_TASK = {
    "text-to-video": "video", "image-to-video": "video", "start-end-to-video": "video",
    "reference-to-video": "video", "multimodal-video": "video", "video-edit": "video",
    "video-extend": "video", "video-upscale": "video", "video-tools": "video",
    "motion-control": "video", "digital-human": "video", "audio-to-video": "video",
    "lip-sync-video": "video", "text-to-world": "video", "image-to-world": "video",
    "video-to-world": "video",
    "text-to-image": "image", "image-to-image": "image", "image-upscale": "image",
    "image-tools": "image", "image-effects": "image", "reference-to-image": "image",
    "image-layer-decomposition": "image",
    "text-to-audio": "audio", "text-to-music": "audio", "audio-to-audio": "audio",
    "video-to-audio": "audio", "text-to-speech": "audio",
    "text-to-text": "text", "text-to-lyrics": "text", "prompt-enhance": "text",
    "image-to-text": "text", "video-to-text": "text",
    "text-to-3d": "3d", "image-to-3d": "3d", "multi-image-to-3d": "3d",
}


def http_get(url: str, timeout: int = 60, attempts: int = 3) -> bytes:
    """Retry only documentation GET requests, which have no external side effects."""
    req = urllib.request.Request(url, headers={"User-Agent": "runninghub-skill-builder/1.0"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and exc.code < 500:
                raise
            if attempt == attempts:
                raise
            time.sleep(attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts:
                raise
            time.sleep(attempt)
    raise RuntimeError("unreachable")


def strip_authenticated_query(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    keys = {k.lower() for k, _ in urllib.parse.parse_qsl(parsed.query,
                                                         keep_blank_values=True)}
    if keys & AUTH_QUERY_KEYS:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return url


def sanitize_text(value: str) -> str:
    return URL_RE.sub(lambda match: strip_authenticated_query(match.group(0)), value)


def sanitize_registry(value):
    """Remove unusable examples and authenticated query strings from registry data."""
    if isinstance(value, dict):
        cleaned = {k: sanitize_registry(v) for k, v in value.items()}
        if cleaned.get("type") in ("STRING", "FILE", "ARRAY"):
            cleaned.pop("default", None)
        return cleaned
    if isinstance(value, list):
        return [sanitize_registry(v) for v in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def parse_index(host: str) -> list[dict]:
    """Return [{path_in_doc, title, breadcrumb}] for model API pages."""
    text = http_get(f"https://{host}{DOC_INDEX}").decode("utf-8")
    entries = []
    for line in text.splitlines():
        m = re.match(r"^- (.+) \[([^\]]+)\]\((https://[^)]+?\.md)\)(?::\s*(.*))?$", line.strip())
        if not m:
            continue
        breadcrumb, title, url, blurb = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        if not any(breadcrumb.startswith(s) or breadcrumb.startswith(f"标准{s}") for s in MODEL_SECTIONS):
            continue
        crumbs = [c.strip() for c in breadcrumb.split(">")]
        subtask = crumbs[2] if len(crumbs) > 2 else ""
        if subtask in EXCLUDED_SUBTASKS:
            continue
        entries.append({
            "doc_path": url,
            "title": title.strip(),
            "breadcrumb": breadcrumb,
            "blurb": blurb.strip(),
        })
    return entries


def extract_yaml_spec(md_text: str) -> dict | None:
    m = re.search(r"```yaml\s*\n(.*?)\n```", md_text, re.S)
    if not m:
        return None
    try:
        return yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None


def parse_param(name: str, prop: dict, required: set[str]) -> dict:
    p = {"key": name}
    t = prop.get("type", "")
    if t == "integer" or t == "number":
        p["type"] = "NUMBER"
    elif prop.get("enum"):
        p["type"] = "LIST"
    elif t == "boolean":
        p["type"] = "BOOL"
    elif t == "array":
        p["type"] = "ARRAY"
    else:
        p["type"] = "STRING"
    if prop.get("format") == "binary":
        p["type"] = "FILE"
    if prop.get("enum"):
        p["options"] = [str(v) for v in prop["enum"]]
    if prop.get("items") and isinstance(prop["items"], dict) and prop["items"].get("type"):
        p["itemType"] = prop["items"]["type"]
    if p["type"] in ("LIST", "BOOL", "NUMBER") \
            and "default" in prop and prop["default"] is not None:
        p["default"] = prop["default"]
    if "minLength" in prop:
        p["minLength"] = prop["minLength"]
    if "maxLength" in prop:
        p["maxLength"] = prop["maxLength"]
    if prop.get("description"):
        p["description"] = sanitize_text(str(prop["description"])[:300])
    if name in required:
        p["required"] = True
    return p


def parse_endpoint(entry: dict) -> dict | None:
    md = http_get(entry["doc_path"]).decode("utf-8")
    spec = extract_yaml_spec(md)
    if not spec or not spec.get("paths"):
        return None
    path, ops = next(iter(spec["paths"].items()))
    op = next(iter(ops.values()))
    params = []
    schema = (op.get("requestBody", {}).get("content", {})
              .get("application/json", {}).get("schema", {}))
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not props:
        # ref-structured schema: resolve from components
        ref = schema.get("$ref", "")
        if not ref and schema.get("x-apifox-refs"):
            first = next(iter(schema["x-apifox-refs"].values()))
            ref = first.get("$ref", "")
        if ref.startswith("#/components/schemas/"):
            name = ref.split("/")[-1]
            comp = (spec.get("components", {}).get("schemas", {}) or {}).get(name, {})
            props = comp.get("properties") or {}
            required = set(comp.get("required") or [])
    for pname, prop in props.items():
        if pname in ("apiKey",):  # auth lives in the header, not the body
            continue
        params.append(parse_param(pname, prop, required))

    crumbs = [c.strip() for c in entry["breadcrumb"].split(">")]
    # e.g. "标准模型API > 视频生成与处理 > image-to-video > Vidu > [标题]"
    # or   "标准模型API > 其他 [标题]" (catch-all, only 2 segments)
    category = crumbs[1] if len(crumbs) > 1 else ""
    subtask = crumbs[2] if len(crumbs) > 2 else ""
    vendor = crumbs[3] if len(crumbs) > 3 else ""
    ep_id = path.removeprefix("/openapi/v2/")
    if ep_id in EXCLUDED_ENDPOINTS or subtask in EXCLUDED_SUBTASKS:
        return {}
    task = EXPLICIT_TASK_OVERRIDE.get(ep_id) or TASK_BY_BREADCRUMB.get(subtask, subtask or "other")
    return {
        "endpoint": ep_id,
        "title": entry["title"],
        "task": task,
        "outputType": OUTPUT_TYPE_BY_TASK.get(task, "other"),
        "category": category,
        "vendor": vendor,
        "description": sanitize_text(entry["blurb"] or str(op.get("description") or "")[:400]),
        "params": params,
        "docUrl": entry["doc_path"].removesuffix(".md"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="www.runninghub.cn", choices=ALLOWED_HOSTS)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data" / "models.json"))
    ap.add_argument("--workers", type=positive_int, default=16)
    ap.add_argument("--allow-partial", action="store_true",
                    help="write a registry even when some documentation pages fail")
    args = ap.parse_args()

    entries = parse_index(args.host)
    print(f"index: {len(entries)} model API pages", file=sys.stderr)

    endpoints, failures = [], []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(parse_endpoint, e): e for e in entries}
        for fut in cf.as_completed(futures):
            e = futures[fut]
            try:
                ep = fut.result()
                if ep is None:
                    failures.append(e["title"])
                elif ep:
                    endpoints.append(ep)
            except Exception as exc:  # noqa: BLE001
                failures.append(f'{e["title"]}: {exc}')

    endpoints.sort(key=lambda x: (x["task"], x["endpoint"]))
    if failures:
        print(f"failed pages: {len(failures)}", file=sys.stderr)
        for title in failures[:20]:
            print(f"  FAILED: {title}", file=sys.stderr)
        if not args.allow_partial:
            raise SystemExit("registry not replaced; rerun after the documentation fetch succeeds")
    if not endpoints:
        raise SystemExit("registry not replaced: no model endpoints were parsed")
    endpoint_ids = [e["endpoint"] for e in endpoints]
    if len(endpoint_ids) != len(set(endpoint_ids)):
        raise SystemExit("registry not replaced: duplicate model endpoints")
    registry = sanitize_registry({
        "version": 2,
        "builtAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": f"https://{args.host}{DOC_INDEX}",
        "total": len(endpoints),
        "endpoints": endpoints,
    })
    encoded = json.dumps(registry, ensure_ascii=False, indent=1) + "\n"
    if re.search(r"(?:Rh-Comfy-Auth|Rh-Identify|q-ak|q-signature)=", encoded, re.I):
        raise SystemExit("registry not replaced: authenticated URL remained after sanitizing")
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, out)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"wrote {args.out}: {len(endpoints)} endpoints, {len(failures)} failures", file=sys.stderr)


if __name__ == "__main__":
    main()
