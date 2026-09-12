#!/usr/bin/env python3
"""Unified CLI for every RunningHub open API (www.runninghub.cn / www.runninghub.ai).

Python 3.9+ standard library only — no pip installs required.

Coverage
  Platform   : account / api-keys / queue status / public ComfyUI model list
  Tasks      : v2 query, v1 status & outputs, cancel, wait and optional download
  Workflows  : run (simple/advanced nodeInfoList), fetch API-format JSON
  AI apps    : run, fetch API call demo (nodeInfoList template)
  Models     : search registry, show params, run any 标准模型 API endpoint
  Uploads    : media (v2 binary), LoRA (md5 + presigned PUT)
  Civitai    : LoRA search/info/download, one-shot find, sync to RunningHub
  Webhooks   : event detail, retry

API key resolution: $RUNNINGHUB_API_KEY
Host resolution   : --host flag > $RUNNINGHUB_HOST > www.runninghub.cn
  (use --host www.runninghub.ai for the international site)

Exit codes: 0 ok, 1 API error, 2 usage/input error, 3 poll timeout.
Full endpoint docs: references/ in the skill directory.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import http.client
import json
import mimetypes
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = SCRIPT_DIR.parent / "data" / "models.json"
BASEMODEL_MAP_PATH = SCRIPT_DIR.parent / "data" / "basemodel_map.json"

DEFAULT_HOST = "www.runninghub.cn"
ALLOWED_HOSTS = {"www.runninghub.cn", "www.runninghub.ai"}
TERMINAL_STATES = {"SUCCESS", "FAILED"}
POLL_INTERVAL = 3.0
QUERY_RETRIES = 3
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SAFE_EXTENSION_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")

CIVITAI_HOST = "civitai.com"
# Cloudflare 403s the default urllib UA — every civitai.com request needs a browser UA.
CIVITAI_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
CIVITAI_PROXY_HINT = ("中国大陆访问 Civitai 通常需要代理，"
                      "可设置 HTTPS_PROXY 环境变量")
CIVITAI_LINK_RE = re.compile(
    r"civitai\.com/models/(\d+)(?:/[^\s\"'?<>#&]*)?"
    r"(?:[?#&][^\s\"'<>]*?modelVersionId=(\d+))?", re.IGNORECASE)
# Model types whose files RHLoraLoader can load (checkpoint/embedding/etc. cannot).
CIVITAI_LORA_TYPES = {"LORA", "LoCon", "DoRA"}
CIVITAI_SORTS = ["Highest Rated", "Most Downloaded", "Newest", "Most Liked",
                 "Most Discussed", "Most Collected", "Most Images",
                 "Highest Weighted", "Random"]
CIVITAI_RETRY_CAP = 5        # retries per request on 429/5xx/network errors
CIVITAI_BACKOFF_MAX = 30.0   # seconds between retries


# ---------------------------------------------------------------------------
# HTTP core
# ---------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, message: str, code=None, raw=None, *, status=None, retryable=False):
        super().__init__(message)
        self.code = code
        self.raw = raw
        self.status = status
        self.retryable = retryable


def resolve_key(args) -> str:
    key = getattr(args, "key", None) or os.environ.get("RUNNINGHUB_API_KEY", "")
    key = key.strip()
    if not key:
        print("No API key. Export RUNNINGHUB_API_KEY. "
              "Create keys at https://www.runninghub.cn/enterprise-api/consumerApi",
              file=sys.stderr)
        sys.exit(2)
    return key


def resolve_host(args) -> str:
    host = getattr(args, "host", None) or os.environ.get("RUNNINGHUB_HOST", "") or DEFAULT_HOST
    host = host.strip().removeprefix("https://").removeprefix("http://").rstrip("/")
    if host not in ALLOWED_HOSTS:
        print(f"Unsupported host: {host}. Choose www.runninghub.cn or www.runninghub.ai.",
              file=sys.stderr)
        sys.exit(2)
    return host


def display_url(url: str) -> str:
    """Remove query parameters and fragments before including a URL in an error."""
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def http_request(method: str, url: str, *, headers=None, body=None, timeout=120,
                 allow_empty: bool = False) -> dict:
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("User-Agent", "runninghub-skill/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            payload = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        payload = e.read()
    except urllib.error.URLError as e:
        raise ApiError(f"network error: {e.reason}", retryable=True) from e
    if not payload and allow_empty and 200 <= status < 300:
        return {}
    text = payload.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        kind = "empty" if not payload else "non-JSON"
        raise ApiError(f"{kind} response from {display_url(url)} (HTTP {status}): {text[:300]}",
                       status=status, retryable=status == 429 or status >= 500) from None
    if status >= 400:
        message = (parsed.get("msg") or parsed.get("errorMessage")
                   if isinstance(parsed, dict) else None)
        raise ApiError(str(message or f"HTTP {status} from {display_url(url)}"),
                       raw=parsed, status=status,
                       retryable=status == 429 or status >= 500)
    return parsed


def api_v1(key: str, host: str, path: str, payload: dict | None = None,
           method: str = "POST", key_field: str = "apiKey") -> dict:
    """Legacy /task /api /uc endpoints: apiKey in body/query + Bearer header."""
    body = dict(payload or {})
    body.setdefault(key_field, key)
    if method == "GET":
        qs = urllib.parse.urlencode({k: v for k, v in body.items() if v is not None})
        url = f"https://{host}{path}?{qs}"
        return http_request("GET", url, headers={"Authorization": f"Bearer {key}"})
    url = f"https://{host}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return http_request("POST", url, headers={
        "Authorization": f"Bearer {key}",
        "Host": host,
        "Content-Type": "application/json",
    }, body=data)


def api_v2(key: str, host: str, path: str, payload: dict | None = None, method: str = "POST") -> dict:
    """Modern /openapi/v2 endpoints: Bearer header only, no apiKey in body."""
    url = f"https://{host}{path}"
    headers = {"Authorization": f"Bearer {key}"}
    if method == "GET":
        return http_request("GET", url, headers=headers)
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    headers["Content-Type"] = "application/json"
    return http_request("POST", url, headers=headers, body=data)


def check(resp: dict, *, v2: bool = False) -> dict:
    """Raise on error envelopes; return resp for v2 / passthrough for v1 data."""
    if v2:
        # v2 model/query endpoints return {taskId,status,errorCode,errorMessage,...}
        # with empty errorCode on success; key-management endpoints use {code,msg}.
        code = resp.get("code")
        if code not in (None, 0, "0"):
            raise ApiError(f'{resp.get("msg") or resp.get("errorMessage")}', code=code, raw=resp)
        if resp.get("errorCode") not in (None, "", 0):
            raise ApiError(f'{resp.get("errorMessage")}', code=resp.get("errorCode"), raw=resp)
        return resp
    if resp.get("code") not in (0, "0"):
        raise ApiError(str(resp.get("msg")), code=resp.get("code"), raw=resp)
    return resp


def emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# nodeInfoList parsing:  --node "12:text=prompt words"  /  --node-file list.json
# ---------------------------------------------------------------------------

def parse_node_args(node_list: list[str] | None, node_file: str | None) -> list[dict] | None:
    nodes: list[dict] = []
    if node_file:
        try:
            loaded = json.loads(Path(node_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read --node-file {node_file}: {e}", file=sys.stderr); sys.exit(2)
        if isinstance(loaded, dict) and "nodeInfoList" in loaded:
            loaded = loaded["nodeInfoList"]
        if not isinstance(loaded, list):
            print("--node-file must contain a JSON array of {nodeId,fieldName,fieldValue}", file=sys.stderr); sys.exit(2)
        nodes.extend(loaded)
    for spec in node_list or []:
        try:
            node_id, rest = spec.split(":", 1)
            field, value = rest.split("=", 1)
        except ValueError:
            print(f'bad --node "{spec}" — expected nodeId:fieldName=value', file=sys.stderr); sys.exit(2)
        node_id, field = node_id.strip(), field.strip()
        if value.startswith("@"):
            try:
                value = Path(value[1:]).read_text(encoding="utf-8")
            except OSError as e:
                print(f'bad --node "{spec}" — cannot read {value[1:]}: {e}', file=sys.stderr); sys.exit(2)
        elif value.startswith("json:"):
            try:
                value = json.loads(value[5:])
            except json.JSONDecodeError as e:
                print(f'bad --node "{spec}" — invalid json: value: {e}', file=sys.stderr); sys.exit(2)
        nodes.append({"nodeId": node_id, "fieldName": field, "fieldValue": value})
    return nodes or None


# ---------------------------------------------------------------------------
# Upload / download helpers
# ---------------------------------------------------------------------------

def file_chunks(path: Path, *, prefix: bytes = b"", suffix: bytes = b""):
    """Yield a file-backed HTTP body without loading the full file into memory."""
    if prefix:
        yield prefix
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            yield chunk
    if suffix:
        yield suffix


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def upload_media(key: str, host: str, file_path: str) -> dict:
    path = Path(file_path)
    if not path.is_file():
        print(f"file not found: {file_path}", file=sys.stderr); sys.exit(2)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    boundary = "----rh-skill-" + secrets.token_hex(16)
    safe_name = path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    prefix = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
    ])
    suffix = f"\r\n--{boundary}--\r\n".encode()
    resp = http_request(
        "POST", f"https://{host}/openapi/v2/media/upload/binary",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(prefix) + path.stat().st_size + len(suffix)),
        }, body=file_chunks(path, prefix=prefix, suffix=suffix), timeout=600)
    check(resp, v2=True)
    return resp["data"]


def upload_lora(key: str, host: str, file_path: str, lora_name: str | None) -> dict:
    """3-step LoRA flow: md5 → presigned URL (v1 API) → PUT to COS."""
    path = Path(file_path)
    if not path.is_file():
        print(f"file not found: {file_path}", file=sys.stderr); sys.exit(2)
    md5 = file_md5(path)
    name = lora_name or path.stem
    resp = check(api_v1(key, host, "/api/openapi/getLoraUploadUrl",
                        {"loraName": name, "md5Hex": md5}))
    data = resp["data"]
    if not isinstance(data, dict):
        raise ApiError("invalid LoRA upload response", raw=resp)
    if not data.get("url"):
        if data.get("fileName"):
            return {"fileName": data["fileName"], "md5Hex": md5,
                    "reused": True, "hint": "use fileName in the RHLoraLoader node"}
        raise ApiError("LoRA upload response contains neither url nor fileName", raw=resp)
    http_request("PUT", data["url"],
                 headers={"Content-Type": "application/octet-stream",
                          "Content-Length": str(path.stat().st_size)},
                 body=file_chunks(path), timeout=1800, allow_empty=True)
    return {"fileName": data.get("fileName"), "md5Hex": md5,
            "hint": f"use fileName in the RHLoraLoader node"}


def download(url: str, out: str | None, *, overwrite: bool = False) -> str:
    """Stream a result URL to disk; returns the local path."""
    if not out:
        name = os.path.basename(urllib.parse.urlparse(url).path) or "download"
        out = str(Path.cwd() / name)
    target = Path(out).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise FileExistsError(f"output already exists: {target}; pass --overwrite to replace it")
    partial = target.with_name(f".{target.name}.{os.getpid()}.part")
    req = urllib.request.Request(url, headers={"User-Agent": "runninghub-skill/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp, partial.open("xb") as f:
            while chunk := resp.read(1 << 16):
                f.write(chunk)
        if target.exists() and not overwrite:
            raise FileExistsError(f"output already exists: {target}; pass --overwrite to replace it")
        os.replace(partial, target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return str(target)


def result_extension(result: dict, url: str) -> str:
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1]
    if SAFE_EXTENSION_RE.fullmatch(ext):
        return ext.lower()
    output_type = str(result.get("outputType") or result.get("fileType") or "bin")
    output_type = re.sub(r"[^A-Za-z0-9]", "", output_type)[:10] or "bin"
    return f".{output_type.lower()}"


def download_results(results: list, outdir: str, task_id: str,
                     *, overwrite: bool = False) -> list[dict]:
    """Download every file result; annotate each entry with its local path."""
    saved = []
    outdir_path = Path(outdir).expanduser().resolve()
    outdir_path.mkdir(parents=True, exist_ok=True)
    safe_task_id = str(task_id)
    if not SAFE_COMPONENT_RE.fullmatch(safe_task_id):
        safe_task_id = re.sub(r"[^A-Za-z0-9_-]", "_", safe_task_id).strip("_") or "task"
    for i, r in enumerate(results or []):
        entry = dict(r)
        url = r.get("url") or r.get("fileUrl")
        if url:
            out = str(outdir_path / f"{safe_task_id}_{i}{result_extension(r, url)}")
            try:
                entry["localPath"] = download(url, out, overwrite=overwrite)
            except Exception as exc:  # noqa: BLE001
                entry["downloadError"] = str(exc)
        saved.append(entry)
    return saved


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------

def submit_task(key: str, host: str, path: str, payload: dict) -> str:
    resp = check(api_v1(key, host, path, payload))
    data = resp.get("data") or {}
    task_id = str(data.get("taskId") or "")
    if not task_id:
        raise ApiError(f"no taskId in response: {json.dumps(resp, ensure_ascii=False)[:300]}")
    tips = data.get("promptTips")
    if tips:
        try:
            pt = json.loads(tips) if isinstance(tips, str) else tips
            if pt.get("result") is False:
                print(f"WARNING promptTips: {json.dumps(pt, ensure_ascii=False)[:500]}", file=sys.stderr)
        except (json.JSONDecodeError, AttributeError):
            print(f"WARNING promptTips: {str(tips)[:300]}", file=sys.stderr)
    return task_id


def query_v2(key: str, host: str, task_id: str) -> dict:
    return api_v2(key, host, "/openapi/v2/query", {"taskId": str(task_id)})


def wait_task(key: str, host: str, task_id: str, timeout: float, quiet: bool) -> dict:
    """Poll /openapi/v2/query until SUCCESS/FAILED or timeout."""
    deadline = time.monotonic() + timeout
    failures = 0
    while True:
        try:
            resp = query_v2(key, host, task_id)
            failures = 0
        except ApiError as exc:
            failures += 1
            if not exc.retryable or failures > QUERY_RETRIES:
                raise
            if not quiet:
                print(f"query failed ({failures}/{QUERY_RETRIES}), retrying: {exc}",
                      file=sys.stderr)
            if time.monotonic() + POLL_INTERVAL > deadline:
                raise
            time.sleep(POLL_INTERVAL)
            continue
        status = resp.get("status") or ""
        err = resp.get("errorCode") or ""
        if status in TERMINAL_STATES or err not in ("", None):
            return resp
        if not quiet:
            print(f"[{time.strftime('%H:%M:%S')}] {task_id}: {status or 'PENDING'}", file=sys.stderr)
        if time.monotonic() + POLL_INTERVAL > deadline:
            print(f"timeout after {timeout:.0f}s (task still {status}); "
                  f"re-run: rh.py task-wait {task_id}", file=sys.stderr)
            emit(resp)
            sys.exit(3)
        time.sleep(POLL_INTERVAL)


def run_and_deliver(key: str, host: str, task_id: str, args) -> None:
    if not getattr(args, "wait", True):
        emit({"taskId": task_id, "hint": "poll later: rh.py task-wait <taskId>"})
        return
    final = wait_task(key, host, task_id, args.timeout, args.quiet)
    out = {"taskId": final.get("taskId"), "status": final.get("status"),
           "usage": final.get("usage"), "promptTips": final.get("promptTips")}
    if final.get("status") == "SUCCESS":
        results = final.get("results") or []
        out["results"] = (download_results(results, args.outdir, task_id,
                                            overwrite=args.overwrite)
                          if args.outdir else results)
    else:
        out["errorCode"] = final.get("errorCode")
        out["errorMessage"] = final.get("errorMessage")
        out["failedReason"] = final.get("failedReason")
    emit(out)
    if final.get("status") != "SUCCESS" or any(
            r.get("downloadError") for r in out.get("results", [])):
        sys.exit(1)


# ---------------------------------------------------------------------------
# Models registry
# ---------------------------------------------------------------------------

def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        print(f"models registry missing: {REGISTRY_PATH}\n"
              "rebuild with: python3 scripts/build_models_registry.py", file=sys.stderr); sys.exit(2)
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read models registry {REGISTRY_PATH}: {exc}", file=sys.stderr)
        sys.exit(2)


def registry_matches(reg: dict, kw: str | None, task: str | None) -> list[dict]:
    hits = reg["endpoints"]
    if task:
        hits = [e for e in hits if e["task"] == task]
    if kw:
        kws = kw.lower().split()
        def hit(e):
            hay = f'{e["endpoint"]} {e["title"]} {e.get("vendor","")} {e.get("description","")}'.lower()
            return all(k in hay for k in kws)
        hits = [e for e in hits if hit(e)]
    return hits


# ---------------------------------------------------------------------------
# Civitai LoRA pipeline: search / info / download / sync / manifest
# ---------------------------------------------------------------------------

def civitai_key(required: bool) -> str:
    key = os.environ.get("CIVITAI_API_KEY", "").strip()
    if not key and required:
        print("No Civitai API key. Export CIVITAI_API_KEY. "
              "Create one on civitai.com under user settings → API Keys.",
              file=sys.stderr)
        sys.exit(2)
    return key


def retry_wait(base_delay: float, headers) -> float:
    """Backoff delay for 429/5xx: Retry-After header (capped) when present."""
    retry_after = headers.get("Retry-After") if headers else None
    if retry_after:
        try:
            return min(float(retry_after), CIVITAI_BACKOFF_MAX)
        except ValueError:
            pass
    return base_delay


def civitai_request(path: str, params: dict | None = None, *, token: str = "") -> dict:
    """GET https://civitai.com/api/v1/... as JSON.

    Browser UA (Cloudflare 403s the urllib default); Bearer header only when a
    key exists (skips the edge cache); exponential backoff 1s→30s on
    429/5xx/network errors (Retry-After honored, capped), max CIVITAI_RETRY_CAP retries.
    """
    qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"https://{CIVITAI_HOST}{path}"
    if qs:
        url = f"{url}?{qs}"
    headers = {"User-Agent": CIVITAI_UA, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    delay = 1.0
    status, text, resp_headers = 0, "", None
    for attempt in range(CIVITAI_RETRY_CAP + 1):
        final = attempt >= CIVITAI_RETRY_CAP
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                        timeout=60) as resp:
                status = getattr(resp, "status", 200)
                text = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status = e.code
            text = e.read().decode("utf-8", "replace")
            resp_headers = e.headers
        except urllib.error.URLError as e:
            if final:
                raise ApiError(f"cannot reach civitai.com: {e.reason}; {CIVITAI_PROXY_HINT}",
                               retryable=True) from e
            time.sleep(delay)
            delay = min(delay * 2, CIVITAI_BACKOFF_MAX)
            continue
        if status < 400:
            break
        if (status == 429 or status >= 500) and not final:
            time.sleep(retry_wait(delay, resp_headers))
            delay = min(delay * 2, CIVITAI_BACKOFF_MAX)
            continue
        break  # non-retryable error → report below
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise ApiError(f"non-JSON response from civitai.com{path} (HTTP {status}): "
                       f"{text[:200]}", status=status) from None
    if status >= 400:
        message = (parsed.get("error") or parsed.get("message")
                   if isinstance(parsed, dict) else None)
        raise ApiError(f"civitai.com{path} HTTP {status}: {message or display_url(url)}",
                       status=status, raw=parsed if isinstance(parsed, dict) else None,
                       retryable=status == 429 or status >= 500)
    return parsed


def load_basemodel_map() -> dict:
    try:
        raw = json.loads(BASEMODEL_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {BASEMODEL_MAP_PATH}: {exc}", file=sys.stderr)
        sys.exit(2)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def civitai_allow(base: str) -> list[str]:
    """Civitai baseModel strings allowed for a RunningHub base-model name."""
    mapping = load_basemodel_map()
    entry = mapping.get(base)
    if not isinstance(entry, dict):
        names = ", ".join(sorted(mapping))
        print(f'unknown --base "{base}" — pick one of: {names}', file=sys.stderr)
        sys.exit(2)
    return list(entry.get("allow") or [])


def base_model_allowed(value, allowed: list[str]) -> bool:
    if not allowed:
        return False  # empty allow set (e.g. HunyuanVideo1.5): nothing on civitai matches
    return str(value or "").strip().lower() in {a.strip().lower() for a in allowed}


def lora_home() -> Path:
    home = os.environ.get("RH_LORA_HOME", "").strip()
    return Path(home).expanduser() if home else Path.home() / ".runninghub" / "loras"


def load_manifest() -> dict:
    path = lora_home() / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"loras": []}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"LoRA manifest is damaged ({exc}); continuing with an empty manifest — "
              f"restore {path} before the next sync or the history is overwritten",
              file=sys.stderr)
        return {"loras": []}
    if isinstance(data, dict) and isinstance(data.get("loras"), list):
        return data
    return {"loras": []}


def save_manifest(manifest: dict) -> None:
    home = lora_home()
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / f".manifest.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, home / "manifest.json")


def manifest_entry(manifest: dict, version_id) -> dict | None:
    return next((e for e in manifest["loras"]
                 if str(e.get("versionId")) == str(version_id)), None)


def upsert_manifest(manifest: dict, entry: dict) -> None:
    existing = manifest_entry(manifest, entry.get("versionId"))
    if existing is not None:
        existing.update(entry)
    else:
        manifest["loras"].append(entry)
    save_manifest(manifest)


def license_fields(model: dict) -> dict:
    return {k: model.get(k) for k in
            ("allowNoCredit", "allowCommercialUse", "allowDerivatives",
             "allowDifferentLicense", "allowSellImage")}


def tag_names(tags) -> list[str]:
    """Civitai tags come back as [{name:...}] or plain strings, depending on endpoint."""
    names = []
    for t in tags or []:
        name = t.get("name") if isinstance(t, dict) else t
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def pick_primary_file(files: list | None) -> dict | None:
    files = files or []
    primary = [f for f in files if f.get("primary")]
    safetensors = [f for f in primary
                   if (f.get("metadata") or {}).get("format") == "SafeTensor"]
    return (safetensors or primary or files or [None])[0]


def is_safetensor_file(f: dict) -> bool:
    """SafeTensor by declared format, with the file extension as a fallback."""
    fmt = str(((f.get("metadata") or {}).get("format") or "")).lower()
    return fmt == "safetensor" or str(f.get("name") or "").lower().endswith(".safetensors")


def summarize_civitai_model(model: dict, allowed: list[str]) -> dict | None:
    """One search row per model: the first version matching the allow set."""
    versions = model.get("modelVersions") or []
    if allowed is not None:
        version = next((v for v in versions
                        if base_model_allowed(v.get("baseModel"), allowed)), None)
    else:
        version = versions[0] if versions else None
    if not version:
        return None
    stats = model.get("stats") or {}
    file = pick_primary_file(version.get("files"))
    primary_file = None
    if file:
        primary_file = {"fileId": file.get("id"), "fileName": file.get("name"),
                        "sizeKB": file.get("sizeKB"),
                        "format": (file.get("metadata") or {}).get("format")}
    return {
        "modelId": model.get("id"),
        "name": model.get("name"),
        "versionId": version.get("id"),
        "versionName": version.get("name"),
        "baseModel": version.get("baseModel"),
        "trainedWords": version.get("trainedWords") or [],
        "tags": tag_names(model.get("tags"))[:10],
        "downloadCount": stats.get("downloadCount"),
        "thumbsUpCount": stats.get("thumbsUpCount"),
        "nsfw": model.get("nsfw"),
        "license": license_fields(model),
        "primaryFile": primary_file,
    }


def civitai_search(query: str, *, allowed: list[str] | None = None, base: str | None = None,
                   sort: str | None = None, period: str | None = None,
                   limit: int = 20, nsfw: bool = False) -> list[dict]:
    """Search LoRA models, locally filtering modelVersions[].baseModel.

    Cursor pagination only (page+cursor together 400 on /models); pages may
    come back under-filled because baseModel filtering happens here, so the
    loop stops on "collected enough" or "no nextCursor" — never on "full page".
    """
    if allowed is not None and not allowed:
        print(f'--base "{base}": this RunningHub base model has no compatible LoRA '
              "family on civitai.com (empty allow set in data/basemodel_map.json); "
              "skipping the civitai search", file=sys.stderr)
        return []
    token = civitai_key(required=False)
    page_size = max(1, min(100, limit))
    results: list[dict] = []
    cursor = None
    for _ in range(50):
        params = {"query": query, "types": "LORA", "limit": page_size}
        if sort:
            params["sort"] = sort
        if period:
            params["period"] = period
        if nsfw:
            params["nsfw"] = "true"
        if cursor:
            params["cursor"] = cursor
        page = civitai_request("/api/v1/models", params, token=token)
        for model in page.get("items") or []:
            row = summarize_civitai_model(model, allowed)
            if row:
                results.append(row)
                if len(results) >= limit:
                    return results
        cursor = (page.get("metadata") or {}).get("nextCursor")
        if not cursor:
            break
    return results


def civitai_mini(version_id, token: str) -> dict | None:
    try:
        return civitai_request(f"/api/v1/model-versions/mini/{version_id}", token=token)
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise


def summarize_civitai_version(version: dict, mini: dict | None) -> dict:
    out = {
        "versionId": version.get("id"),
        "name": version.get("name"),
        "baseModel": version.get("baseModel"),
        "publishedAt": version.get("publishedAt"),
        "status": version.get("status"),
        "trainedWords": version.get("trainedWords") or [],
        "air": version.get("air"),
        "files": [{
            "fileId": f.get("id"),
            "fileName": f.get("name"),
            "sizeKB": f.get("sizeKB"),
            "primary": bool(f.get("primary")),
            "format": (f.get("metadata") or {}).get("format"),
            "sha256": (f.get("hashes") or {}).get("SHA256"),
        } for f in version.get("files") or []],
    }
    if mini:
        out["availability"] = mini.get("availability")
        out["requireAuth"] = mini.get("requireAuth")
        out["checkPermission"] = mini.get("checkPermission")
        out["earlyAccessEndsAt"] = mini.get("earlyAccessEndsAt")
        out["air"] = out.get("air") or mini.get("air")
    return out


def parse_iso_timestamp(value) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def content_disposition_name(headers) -> str | None:
    raw = headers.get("Content-Disposition") if headers else None
    if not raw:
        return None
    m = (re.search(r"filename\*=(?:UTF-8|utf-8)''([^;]+)", raw)
         or re.search(r'filename="([^"]+)"', raw)
         or re.search(r"filename=([^;]+)", raw))
    if not m:
        return None
    name = urllib.parse.unquote(m.group(1).strip().strip('"'))
    return os.path.basename(name) or None


def safe_filename(name) -> str:
    name = os.path.basename(str(name or "").strip())
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip()
    if not name:
        return "lora.safetensors"
    if len(name) > 180:
        stem, ext = os.path.splitext(name)
        if len(ext) >= 180:  # pathological: the extension alone exceeds the cap
            return name[:180]
        name = stem[:180 - len(ext)] + ext
    return name


def civitai_download_error(e: urllib.error.HTTPError, text: str) -> ApiError:
    if e.code == 401:
        return ApiError("civitai.com returned 401 — the download requires an account: "
                        "export CIVITAI_API_KEY", status=401)
    if e.code == 403:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {}
        deadline = payload.get("deadline") if isinstance(payload, dict) else None
        if deadline:
            return ApiError(f"civitai.com returned 403 — Early Access until {deadline}; "
                            "wait for the deadline or pick another version",
                            status=403, raw=payload)
        return ApiError("civitai.com returned 403 — the file is not downloadable",
                        status=403, raw=payload)
    if e.code == 410:
        return ApiError("civitai.com returned 410 — the version is archived; "
                        "pick another version", status=410)
    if e.code == 404:
        return ApiError("civitai.com returned 404 — no such version/file", status=404)
    return ApiError(f"civitai.com download HTTP {e.code}: {text[:200]}", status=e.code)


def model_id_from_air(air) -> int | None:
    m = re.search(r"civitai:(\d+)@", str(air or ""))
    return int(m.group(1)) if m else None


def query_param(url: str, name: str) -> str | None:
    """Single query parameter of a URL (exact key match, no substring pitfalls)."""
    query = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query,
                                   keep_blank_values=True)
    return next((v for k, v in query if k == name), None)


def civitai_download(version_id: int, file_id: int | None, *, token: str) -> dict:
    """Precheck via /mini, stream to <lora home>/files/{modelId}-{versionId}-{name}
    with Range resume, verify SHA256, then record the download in the manifest."""
    mini = civitai_request(f"/api/v1/model-versions/mini/{version_id}", token=token)
    # /mini lacks modelId/trainedWords/per-file hashes — the full version endpoint
    # fills the manifest and pins SHA256 to the requested file (mini's top-level
    # hashes describe the primary file only).
    try:
        detail = civitai_request(f"/api/v1/model-versions/{version_id}", token=token)
    except ApiError:
        detail = {}
    early = parse_iso_timestamp(mini.get("earlyAccessEndsAt"))
    if early is not None and early.tzinfo is None:
        early = early.replace(tzinfo=datetime.timezone.utc)
    if early and early > datetime.datetime.now(datetime.timezone.utc):
        raise ApiError(f"version {version_id} is Early Access until "
                       f"{early.isoformat()}; pick another version or wait")
    if mini.get("requireAuth") and not token:
        civitai_key(required=True)  # prints the export hint and exits 2

    detail_files = detail.get("files") or []
    model_type = str((detail.get("model") or {}).get("type") or "").strip()
    if model_type and model_type not in CIVITAI_LORA_TYPES:
        raise ApiError(f"version {version_id} belongs to a {model_type} model, not a "
                       f"LoRA (allowed types: {', '.join(sorted(CIVITAI_LORA_TYPES))}); "
                       "RHLoraLoader cannot load it")
    safetensors_files = [f for f in detail_files if is_safetensor_file(f)]
    if file_id:
        file_entry = next((f for f in detail_files if f.get("id") == file_id), None)
        if file_entry is None:
            raise ApiError(f"version {version_id} has no file with id {file_id}; "
                           f"list them: rh.py civitai-info {version_id}")
        if not is_safetensor_file(file_entry):
            raise ApiError(f'file {file_id} ("{file_entry.get("name")}") is not a '
                           "SafeTensor LoRA file; RHLoraLoader cannot load it")
        expected = (file_entry.get("hashes") or {}).get("SHA256")
    elif not safetensors_files:
        listing = ", ".join(f'{f.get("name")} [{(f.get("metadata") or {}).get("format")}]'
                            for f in detail_files) or "no files listed"
        raise ApiError(f"version {version_id} has no SafeTensor file "
                       f"({listing}); RHLoraLoader cannot load zip/ckpt files")
    else:
        file_entry = next((f for f in safetensors_files if f.get("primary")), None)
        if file_entry is None:
            file_entry = safetensors_files[0]
            primary_name = next((f.get("name") for f in detail_files if f.get("primary")),
                                None)
            print(f'primary file "{primary_name}" is not SafeTensor; using '
                  f'"{file_entry.get("name")}" instead', file=sys.stderr)
        expected = (file_entry.get("hashes") or {}).get("SHA256")
        if file_entry.get("primary"):
            expected = expected or (mini.get("hashes") or {}).get("SHA256")
    if not expected:
        print("civitai lists no SHA256 for this file; skipping the integrity check",
              file=sys.stderr)

    # The 307 to the signed CDN URL strips custom headers, so auth must ride
    # the query string (?token=) instead of the Authorization header.
    urls = [u for u in (mini.get("downloadUrls") or []) if isinstance(u, str) and u]
    url = None
    if urls:
        if file_id:
            url = next((u for u in urls if query_param(u, "fileId") == str(file_id)), None)
        url = url or urls[0]
    url = urllib.parse.urljoin(f"https://{CIVITAI_HOST}/",
                               url or f"/api/download/models/{version_id}")
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if k not in ("fileId", "token")]
    if file_id:
        query.append(("fileId", str(file_id)))
    # The token must never be sent to a host other than civitai.com.
    netloc = parts.netloc.lower()
    if netloc == CIVITAI_HOST or netloc.endswith(f".{CIVITAI_HOST}"):
        if token:
            query.append(("token", token))
    elif token:
        print(f"download URL is not on civitai.com ({parts.netloc}); "
              "requesting it without the API token", file=sys.stderr)
    url = parts._replace(query=urllib.parse.urlencode(query)).geturl()

    model_id = detail.get("modelId") or model_id_from_air(mini.get("air"))
    model_tag = model_id or "model"
    files_dir = lora_home() / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    part_id = file_id or (file_entry or {}).get("id")
    part = (files_dir / f"{model_tag}-{version_id}-{part_id}.part" if part_id is not None
            else files_dir / f"{model_tag}-{version_id}.part")
    final_name = None
    delay = 1.0
    for attempt in range(CIVITAI_RETRY_CAP + 1):
        final = attempt >= CIVITAI_RETRY_CAP
        offset = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": CIVITAI_UA}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                status = getattr(resp, "status", 200)
                if status == 206:
                    mode = "ab"  # resume accepted
                else:
                    mode, offset = "wb", 0  # server ignored Range → start over
                final_name = content_disposition_name(resp.headers) or final_name
                length = resp.headers.get("Content-Length")
                total = int(length) + offset if length and length.isdigit() else None
                done, next_note = offset, offset + (64 << 20)
                print(f"downloading version {version_id} → {part.name}", file=sys.stderr)
                with part.open(mode) as f:
                    while chunk := resp.read(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if total and done >= next_note:
                            next_note = done + (64 << 20)
                            print(f"  {done >> 20} / {total >> 20} MiB", file=sys.stderr)
                if total is not None and done < total:
                    raise ConnectionError(f"short read: {done} of {total} bytes")
            break
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code == 416 and offset:
                # Range start beyond EOF → the .part file already holds the
                # whole file; verify it instead of restarting the download.
                print("server says the resume range is already satisfied — "
                      "verifying the existing .part file", file=sys.stderr)
                break
            if (e.code == 429 or e.code >= 500) and not final:
                wait = retry_wait(delay, e.headers)
                print(f"download HTTP {e.code}; retrying in {wait:.0f}s "
                      f"(resume from byte {offset})", file=sys.stderr)
                time.sleep(wait)
                delay = min(delay * 2, CIVITAI_BACKOFF_MAX)
                continue
            raise civitai_download_error(e, text) from None
        except urllib.error.URLError as e:
            if final:
                raise ApiError(f"download failed: {e.reason}; {CIVITAI_PROXY_HINT}") from e
            resume_at = part.stat().st_size if part.exists() else 0
            print(f"download failed ({e.reason}); retrying in {delay:.0f}s "
                  f"(resume from byte {resume_at})", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, CIVITAI_BACKOFF_MAX)
        except (OSError, http.client.HTTPException) as e:
            # Mid-stream failures (ConnectionResetError, IncompleteRead, socket
            # timeout, ...) — the next attempt resumes from the .part file.
            if final:
                raise ApiError(f"download interrupted: {type(e).__name__}; "
                               f"{CIVITAI_PROXY_HINT}") from e
            resume_at = part.stat().st_size if part.exists() else 0
            print(f"download interrupted ({type(e).__name__}); retrying in "
                  f"{delay:.0f}s (resume from byte {resume_at})", file=sys.stderr)
            time.sleep(delay)
            delay = min(delay * 2, CIVITAI_BACKOFF_MAX)
    sha256 = file_sha256(part)
    if expected and sha256.lower() != str(expected).lower():
        part.unlink(missing_ok=True)
        raise ApiError(
            f"SHA256 mismatch for version {version_id}: downloaded {sha256}, "
            f"civitai lists {expected}; the .part file was discarded. Note: "
            "civitai's database is known to carry wrong SHA256 values (upstream "
            "issue) — check the model page manually before retrying.")
    name = safe_filename(final_name or (file_entry or {}).get("name")
                         or mini.get("fileName") or "lora.safetensors")
    target = files_dir / f"{model_tag}-{version_id}-{name}"
    os.replace(part, target)
    version_name = detail.get("name") or mini.get("versionName") or str(version_id)
    entry = {
        "modelId": model_id,
        "versionId": version_id,
        "air": mini.get("air"),
        "name": f'{mini.get("modelName") or ""} {version_name}'.strip(),
        "baseModel": mini.get("baseModel") or detail.get("baseModel"),
        "trainedWords": detail.get("trainedWords") or [],
        "sha256": sha256,
        "md5": file_md5(target),
        "localPath": str(target),
        "rhHost": None,
        "rhFileName": None,
        "syncedAt": None,
        "status": "downloaded",
    }
    manifest = load_manifest()
    upsert_manifest(manifest, entry)
    return {**entry, "fileId": part_id, "fileName": name,
            "sizeMB": round(target.stat().st_size / (1 << 20), 1)}


def rh_resource_records(key: str, host: str, payload: dict, max_pages: int = 4) -> list[dict]:
    """Page through /openapi/v2/resource/list (size caps at 50; rate limit 20/min)."""
    records = []
    for page in range(1, max_pages + 1):
        resp = check(api_v2(key, host, "/openapi/v2/resource/list",
                            dict(payload, current=page, size=50)), v2=True)
        data = resp.get("data") or {}
        batch = data.get("records") or []
        records.extend(batch)
        if len(batch) < 50:
            break
    return records


def civitai_source(desc: str) -> dict | None:
    """modelId/versionId extracted from a civitai.com/models/... link in desc."""
    m = CIVITAI_LINK_RE.search(desc or "")
    if not m:
        return None
    out = {"modelId": int(m.group(1))}
    if m.group(2):
        out["versionId"] = int(m.group(2))
    return out


def summarize_rh_lora(record: dict) -> dict:
    versions = record.get("versions") or []
    words: list = []
    for v in versions:
        raw = v.get("triggerWords")
        # RunningHub returns triggerWords as a plain string (or a list on some records)
        items = raw if isinstance(raw, list) else [raw]
        for w in items:
            w = str(w or "").strip()
            if w and w not in words:
                words.append(w)
    return {
        "nodeModelName": record.get("nodeModelName"),
        "desc": record.get("desc"),
        "civitaiSource": civitai_source(record.get("desc") or ""),
        "baseModels": [v.get("baseModel") for v in versions if v.get("baseModel")],
        "triggerWords": words,
    }


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_account(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v1(key, host, "/uc/openapi/accountStatus", key_field="apikey")))


def cmd_apikeys(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v2(key, host, "/openapi/v2/api-key/list", method="GET"), v2=True))


def cmd_queue(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v2(key, host, "/openapi/v2/queue/status", method="GET"), v2=True))


def cmd_resources(args):
    key, host = resolve_key(args), resolve_host(args)
    payload = {"resourceType": args.type, "resourceName": args.kw or "",
               "current": args.page, "size": args.size}
    if args.base_models:
        payload["baseModels"] = args.base_models.split(",")
    emit(check(api_v2(key, host, "/openapi/v2/resource/list", payload), v2=True))


def cmd_upload(args):
    key, host = resolve_key(args), resolve_host(args)
    data = upload_media(key, host, args.file)
    emit(data)
    print("fileName → ComfyUI LoadImage/LoadAudio/LoadVideo nodes; "
          "download_url (valid ~1 day) → 标准模型 API image/audio params", file=sys.stderr)


def cmd_upload_lora(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(upload_lora(key, host, args.file, args.name))


def cmd_workflow_run(args):
    key, host = resolve_key(args), resolve_host(args)
    payload = {"workflowId": str(args.workflow_id)}
    nodes = parse_node_args(args.node, args.node_file)
    if nodes:
        payload["nodeInfoList"] = nodes
    if args.workflow_json:
        try:
            wf = Path(args.workflow_json).read_text(encoding="utf-8")
            json.loads(wf)  # validate
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read --workflow-json {args.workflow_json}: {exc}", file=sys.stderr)
            sys.exit(2)
        payload["workflow"] = wf
    if args.webhook:
        payload["webhookUrl"] = args.webhook
    if args.instance:
        payload["instanceType"] = args.instance
    if args.retain is not None:
        payload["retainSeconds"] = args.retain
    if args.personal_queue:
        payload["usePersonalQueue"] = True
    if args.no_metadata:
        payload["addMetadata"] = False
    if args.password:
        payload["accessPassword"] = args.password
    task_id = submit_task(key, host, "/task/openapi/create", payload)
    run_and_deliver(key, host, task_id, args)


def cmd_workflow_json(args):
    key, host = resolve_key(args), resolve_host(args)
    resp = check(api_v1(key, host, "/api/openapi/getJsonApiFormat",
                        {"workflowId": str(args.workflow_id)}))
    prompt = (resp.get("data") or {}).get("prompt")
    try:
        parsed = json.loads(prompt) if isinstance(prompt, str) else prompt
    except json.JSONDecodeError:
        parsed = prompt
    out = Path(args.out or f"workflow-{args.workflow_id}.api.json")
    out.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    emit({"file": str(out), "nodeIds": sorted(parsed.keys()) if isinstance(parsed, dict) else None})


def cmd_app_run(args):
    key, host = resolve_key(args), resolve_host(args)
    try:
        webapp_id = int(args.webapp_id)  # OpenAPI schema: int64 — send a number, not a string
    except ValueError:
        webapp_id = args.webapp_id
    payload = {"webappId": webapp_id}
    nodes = parse_node_args(args.node, args.node_file)
    payload["nodeInfoList"] = nodes or []
    if args.webhook:
        payload["webhookUrl"] = args.webhook
    if args.instance:
        payload["instanceType"] = args.instance
    if args.password:
        payload["accessPassword"] = args.password
    task_id = submit_task(key, host, "/task/openapi/ai-app/run", payload)
    run_and_deliver(key, host, task_id, args)


def cmd_app_demo(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v1(key, host, "/api/webapp/apiCallDemo",
                      {"webappId": str(args.webapp_id)}, method="GET")))


def cmd_models(args):
    reg = load_registry()
    hits = registry_matches(reg, args.kw, args.task)
    if args.kw or args.task:
        for e in hits:
            print(f'{e["endpoint"]}\t[{e["task"]}] {e["title"]}')
        print(f"-- {len(hits)} of {reg['total']} endpoints "
              f"(detail: rh.py models --info ENDPOINT)", file=sys.stderr)
    else:
        import collections
        by_task = collections.Counter(e["task"] for e in reg["endpoints"])
        print(f"registry: {reg['total']} endpoints, built {reg['builtAt']}")
        for t, n in by_task.most_common():
            print(f"  {t}: {n}")
        print("filter: rh.py models --task text-to-video | --kw kling", file=sys.stderr)


def cmd_models_info(args):
    reg = load_registry()
    ep = next((e for e in reg["endpoints"] if e["endpoint"] == args.endpoint), None)
    if not ep:
        print(f"unknown endpoint {args.endpoint}; search: rh.py models --kw <word>", file=sys.stderr); sys.exit(2)
    emit(ep)


# Registry defaults are never auto-filled for these keys: docs put long
# sample copy in them, and silently submitting a paid task with sample text
# is worse than failing fast.
PROMPT_PARAM_KEYS = {"prompt", "text", "lyrics", "content", "input_text"}


def cmd_model_run(args):
    key, host = resolve_key(args), resolve_host(args)
    reg = load_registry()
    meta = next((e for e in reg["endpoints"] if e["endpoint"] == args.endpoint), None)
    meta_params = {p["key"]: p for p in meta["params"]} if meta else {}
    payload = {}
    if args.prompt is not None:
        payload["prompt"] = args.prompt
    for kv in args.param or []:
        try:
            k, v = kv.split("=", 1)
        except ValueError:
            print(f'bad --param "{kv}" — expected key=value', file=sys.stderr); sys.exit(2)
        if v.startswith("@"):                                    # local file → upload
            if not Path(v[1:]).is_file():
                print(f'bad --param "{kv}" — file not found: {v[1:]}', file=sys.stderr); sys.exit(2)
            up = upload_media(key, host, v[1:])
            v = up.get("download_url") or up.get("fileName")
        elif v.startswith("json:"):
            try:
                v = json.loads(v[5:])
            except json.JSONDecodeError as e:
                print(f'bad --param "{kv}" — invalid json: value: {e}', file=sys.stderr); sys.exit(2)
        elif v in ("true", "false"):
            # Match the type the registry default uses: BOOL params carry a
            # JSON boolean, LIST enums carry the literal "true"/"false" strings.
            if meta_params.get(k, {}).get("type") == "BOOL":
                v = (v == "true")
        elif re.fullmatch(r"-?\d+(\.\d+)?", v):
            # Only NUMBER params (and params unknown to the registry) get JSON
            # numbers; LIST enums use numeric strings (duration='5') and STRING
            # params must stay strings — matching what default-fill would send.
            if meta_params.get(k, {}).get("type") != "LIST":
                v = json.loads(v) if not args.no_coerce else v
        payload[k] = v
    # Auto-fill documented defaults ONLY for enum/bool/number params. The
    # "defaults" of required STRING/FILE/ARRAY params in the docs are sample
    # copy (sample image URLs, sample lyrics) — silently submitting them
    # launches a paid task on sample data, so those must be passed explicitly.
    if meta:
        for p in meta["params"]:
            if p["key"] in PROMPT_PARAM_KEYS:
                continue
            if p["key"] not in payload and p.get("required") and "default" in p \
                    and p["type"] in ("LIST", "BOOL", "NUMBER"):
                payload.setdefault(p["key"], p["default"])
    missing = [k for k, p in meta_params.items()
               if p.get("required") and k not in payload
               and (k in PROMPT_PARAM_KEYS or p["type"] in ("STRING", "FILE", "ARRAY"))]
    if missing:
        print(f'missing required param(s): {", ".join(missing)} — '
              f'pass them via --param k=v (prompt also accepts --prompt)',
              file=sys.stderr)
        sys.exit(2)
    resp = api_v2(key, host, f"/openapi/v2/{args.endpoint}", payload)
    task_id = str(resp.get("taskId") or "")
    if not task_id:
        emit(resp)
        sys.exit(1)
    if resp.get("status") in TERMINAL_STATES and not getattr(args, "wait", True):
        emit(resp)
        sys.exit(0 if resp.get("status") == "SUCCESS" else 1)
    run_and_deliver(key, host, task_id, args)


def cmd_task_status(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v1(key, host, "/task/openapi/status", {"taskId": args.task_id})))


def cmd_task_query(args):
    key, host = resolve_key(args), resolve_host(args)
    resp = query_v2(key, host, args.task_id)
    emit(resp)
    if resp.get("status") == "FAILED" or resp.get("errorCode") not in (None, "", 0):
        sys.exit(1)


def cmd_task_outputs(args):
    key, host = resolve_key(args), resolve_host(args)
    resp = api_v1(key, host, "/task/openapi/outputs", {"taskId": args.task_id})
    if resp.get("code") == 0 and args.outdir and resp.get("data"):
        resp = dict(resp)
        resp["data"] = download_results(resp["data"], args.outdir, args.task_id,
                                        overwrite=args.overwrite)
    emit(resp)  # 804/813 report running or queued states and keep exit code 0
    if any(r.get("downloadError") for r in (resp.get("data") or [])
           if isinstance(r, dict)):
        sys.exit(1)
    if resp.get("code") not in (0, "0", 804, "804", 813, "813"):
        sys.exit(1)


def cmd_task_cancel(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v1(key, host, "/task/openapi/cancel", {"taskId": args.task_id})))


def cmd_task_wait(args):
    key, host = resolve_key(args), resolve_host(args)
    run_and_deliver(key, host, args.task_id, args)


def cmd_webhook_detail(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v1(key, host, "/task/openapi/getWebhookDetail",
                      {"taskId": args.task_id})))


def cmd_webhook_retry(args):
    key, host = resolve_key(args), resolve_host(args)
    payload = {"webhookId": str(args.webhook_id)}
    if args.url:
        payload["webhookUrl"] = args.url
    emit(check(api_v1(key, host, "/task/openapi/retryWebhook", payload)))


def cmd_download(args):
    print(download(args.url, args.out, overwrite=args.overwrite))


def cmd_civitai_search(args):
    if args.limit < 1:
        print("--limit must be >= 1", file=sys.stderr); sys.exit(2)
    allowed = civitai_allow(args.base) if args.base else None
    emit(civitai_search(args.query, allowed=allowed, base=args.base, sort=args.sort,
                        period=args.period, limit=args.limit, nsfw=args.nsfw))


def cmd_civitai_info(args):
    token = civitai_key(required=False)
    ident = str(args.id)
    version_only = None
    ambiguous = False
    if args.version:
        version_only = civitai_request(f"/api/v1/model-versions/{ident}", token=token)
        model = civitai_request(f"/api/v1/models/{version_only.get('modelId')}", token=token)
    else:
        try:
            model = civitai_request(f"/api/v1/models/{ident}", token=token)
        except ApiError as exc:
            if exc.status != 404:
                raise
            # not a model id → treat as a version id and pull its parent model too
            version_only = civitai_request(f"/api/v1/model-versions/{ident}", token=token)
            model = civitai_request(f"/api/v1/models/{version_only.get('modelId')}", token=token)
        if version_only is None and not args.model:
            # the id resolved to a model, but the same number may also be a
            # version id — one cheap probe so the ambiguity is never silent
            ambiguous = civitai_mini(ident, token) is not None
            if ambiguous:
                print(f"{ident} is both a model id and a version id on civitai.com; "
                      "returning the model by default — pass --version for the version",
                      file=sys.stderr)
    versions = model.get("modelVersions") or []
    if version_only is not None:
        versions = [v for v in versions if str(v.get("id")) == str(version_only.get("id"))]
        if not versions:
            versions = [version_only]
    out = {
        "model": {
            "modelId": model.get("id"),
            "name": model.get("name"),
            "type": model.get("type"),
            "nsfw": model.get("nsfw"),
            "creator": (model.get("creator") or {}).get("username"),
            "tags": tag_names(model.get("tags"))[:10],
            "license": license_fields(model),
        },
        "versions": [],
    }
    if ambiguous:
        out["ambiguity"] = True
        out["hint"] = "this id is also a version id; re-run with --version to see it"
    for version in versions[:10]:
        mini = civitai_mini(version.get("id"), token)
        out["versions"].append(summarize_civitai_version(version, mini))
    emit(out)


def cmd_civitai_download(args):
    token = civitai_key(required=False)
    entry = civitai_download(args.version_id, args.file_id, token=token)
    emit({k: entry.get(k) for k in
          ("fileId", "sha256", "md5", "sizeMB", "fileName", "air", "localPath")})


def cmd_lora_sync(args):
    key, host = resolve_key(args), resolve_host(args)
    manifest = load_manifest()
    entry = manifest_entry(manifest, args.version_id)
    if entry and entry.get("rhFileName") and entry.get("rhHost") == host:
        emit({"versionId": args.version_id, "rhFileName": entry["rhFileName"],
              "localPath": entry.get("localPath"), "reused": True,
              "hint": "already synced to this host — put rhFileName into "
                      "RHLoraLoader.file_name"})
        return
    if not (entry and entry.get("localPath") and Path(entry["localPath"]).is_file()):
        token = civitai_key(required=False)
        entry = civitai_download(args.version_id, None, token=token)
        manifest = load_manifest()  # civitai_download rewrote it
    upload = upload_lora(key, host, entry["localPath"], args.name or entry.get("name"))
    stored = manifest_entry(manifest, args.version_id) or entry
    if args.name:
        stored["name"] = args.name
    stored.update({"rhFileName": upload.get("fileName"), "rhHost": host,
                   "status": "synced",
                   "syncedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    upsert_manifest(manifest, stored)
    emit({"versionId": args.version_id, "rhFileName": upload.get("fileName"),
          "localPath": stored.get("localPath"), "reused": bool(upload.get("reused")),
          "hint": "put rhFileName into RHLoraLoader.file_name "
                  "(see references/uploads.md)"})


def cmd_lora_find(args):
    key, host = resolve_key(args), resolve_host(args)
    allowed = civitai_allow(args.base) if args.base else None
    payload = {"resourceType": "LORA", "resourceName": args.query}
    if args.base:
        payload["baseModels"] = [args.base]
    records = [summarize_rh_lora(r)
               for r in rh_resource_records(key, host, payload)]
    results = civitai_search(args.query, allowed=allowed, base=args.base, limit=10)
    emit({"runninghub": {"count": len(records), "records": records},
          "civitai": {"count": len(results), "results": results}})


def cmd_lora_list(args):
    emit(load_manifest())


# ---------------------------------------------------------------------------
# Argument wiring
# ---------------------------------------------------------------------------

def add_run_options(p: argparse.ArgumentParser, default_timeout: float = 900,
                    include_no_wait: bool = True) -> None:
    if include_no_wait:
        p.add_argument("--no-wait", dest="wait", action="store_false",
                       help="submit only; print taskId for later polling")
    p.set_defaults(wait=True)
    p.add_argument("--timeout", type=float, default=default_timeout,
                   help="max seconds to poll (default 900)")
    p.add_argument("--quiet", action="store_true", help="no per-poll status lines")
    p.add_argument("--outdir",
                   help="download outputs to this directory (default: return URLs only)")
    p.add_argument("--overwrite", action="store_true",
                   help="replace existing output files (requires --outdir)")


def add_global(p: argparse.ArgumentParser, suppress: bool = False) -> None:
    # On subparsers use SUPPRESS so an explicit main-parser --key/--host
    # (placed before the subcommand) is not clobbered by subparser defaults.
    default = argparse.SUPPRESS if suppress else None
    p.add_argument("--key", default=default, help=argparse.SUPPRESS)
    p.add_argument("--host", default=default,
                   help="www.runninghub.cn (default) or www.runninghub.ai")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rh.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_global(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name, fn, help_text):
        p = sub.add_parser(name, help=help_text)
        add_global(p, suppress=True)
        p.set_defaults(fn=fn)
        return p

    cmd("account", cmd_account, "account balance & running task count")
    cmd("apikeys", cmd_apikeys, "list API keys on this account")
    cmd("queue", cmd_queue, "concurrency/queue status of this key")

    p = cmd("resources", cmd_resources, "public ComfyUI model list (UNET/CHECKPOINT/LORA/GGUF)")
    p.add_argument("--type", default="UNET", choices=["UNET", "CHECKPOINT", "LORA", "GGUF"])
    p.add_argument("--kw", help="model name keyword")
    p.add_argument("--base-models", help='comma list, e.g. "Flux2-Klein-9B,SD1.5"')
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--size", type=int, default=10)

    p = cmd("upload", cmd_upload, "upload image/audio/video/zip for workflow or model input")
    p.add_argument("file")

    p = cmd("upload-lora", cmd_upload_lora, "upload a .safetensors LoRA for RHLoraLoader")
    p.add_argument("file")
    p.add_argument("--name", help="display name (default: filename stem)")

    p = cmd("workflow", cmd_workflow_run, "run a ComfyUI workflow (API-mode task)")
    p.add_argument("workflow_id")
    p.add_argument("--node", action="append", metavar="ID:FIELD=VALUE",
                   help="nodeInfoList entry; value may be @file.txt or json:{...} (repeatable)")
    p.add_argument("--node-file", help="JSON array / {nodeInfoList:[...]} file")
    p.add_argument("--workflow-json", help="full API-format workflow JSON file (overrides nodes)")
    p.add_argument("--webhook", help="callback URL notified on TASK_END")
    p.add_argument("--instance", choices=["default", "plus"], help="plus = 48G GPU machines")
    p.add_argument("--retain", type=int, choices=range(10, 181), metavar="10-180",
                   help="keep instance warm N s (enterprise-shared keys)")
    p.add_argument("--personal-queue", action="store_true",
                   help="let exclusive-key tasks queue automatically")
    p.add_argument("--no-metadata", action="store_true", help="don't embed prompt metadata in outputs")
    p.add_argument("--password", help="access password if the workflow is encrypted")
    add_run_options(p, 900)

    p = cmd("workflow-json", cmd_workflow_json, "fetch a workflow's API-format JSON (find nodeIds/fields)")
    p.add_argument("workflow_id")
    p.add_argument("-o", "--out", help="output file (default workflow-<id>.api.json)")

    p = cmd("app", cmd_app_run, "run a published AI app (webapp)")
    p.add_argument("webapp_id")
    p.add_argument("--node", action="append", metavar="ID:FIELD=VALUE",
                   help="nodeInfoList entry (repeatable); get template: rh.py app-demo <id>")
    p.add_argument("--node-file", help="JSON array / {nodeInfoList:[...]} file")
    p.add_argument("--webhook", help="callback URL notified on TASK_END")
    p.add_argument("--instance", choices=["default", "plus"])
    p.add_argument("--password", help="access password if the app is encrypted")
    add_run_options(p, 1800)

    p = cmd("app-demo", cmd_app_demo, "get an AI app's API call demo / nodeInfoList template")
    p.add_argument("webapp_id")

    p = cmd("models", cmd_models, "list/search the model API registry")
    p.add_argument("--kw", help="keyword over endpoint/title/vendor/description")
    p.add_argument("--task", help="filter by task, e.g. text-to-video, image-to-video, text-to-image")
    p.set_defaults(info=False)
    p.add_argument("--info", metavar="ENDPOINT", help="show full params of one endpoint")

    p = cmd("model", cmd_model_run, "run a 标准模型 API endpoint")
    p.add_argument("endpoint", help="registry endpoint, e.g. seedance-v1.5-pro/text-to-video")
    p.add_argument("--prompt")
    p.add_argument("--param", action="append", metavar="KEY=VALUE",
                   help="model param; @path uploads a local file first; json:{...} embeds JSON")
    p.add_argument("--no-coerce", action="store_true", help="keep numeric values as strings")
    add_run_options(p, 1800)

    p = cmd("task-status", cmd_task_status, "v1 status: QUEUED/RUNNING/SUCCESS/FAILED")
    p.add_argument("task_id")

    p = cmd("task-query", cmd_task_query, "v2 query: status + results + usage")
    p.add_argument("task_id")

    p = cmd("task-outputs", cmd_task_outputs, "v1 outputs (per-node fileUrl + cost detail)")
    p.add_argument("task_id")
    p.add_argument("--outdir",
                   help="download outputs to this directory (default: return URLs only)")
    p.add_argument("--overwrite", action="store_true",
                   help="replace existing output files (requires --outdir)")

    p = cmd("task-cancel", cmd_task_cancel, "cancel a queued/running task")
    p.add_argument("task_id")

    p = cmd("task-wait", cmd_task_wait, "poll v2 query until done; optionally download outputs")
    p.add_argument("task_id")
    add_run_options(p, 900, include_no_wait=False)

    p = cmd("webhook-detail", cmd_webhook_detail, "webhook delivery status for a task")
    p.add_argument("task_id")

    p = cmd("webhook-retry", cmd_webhook_retry, "resend a webhook event by id")
    p.add_argument("webhook_id")
    p.add_argument("--url", help="override target URL")

    p = cmd("download", cmd_download, "download a result file URL to disk")
    p.add_argument("url")
    p.add_argument("-o", "--out")
    p.add_argument("--overwrite", action="store_true",
                   help="replace the output file if it already exists")

    p = cmd("civitai-search", cmd_civitai_search,
            "search LoRA models on civitai.com (anonymous; optional --base filter)")
    p.add_argument("query")
    p.add_argument("--base", metavar="RH_BASE",
                   help="RunningHub base model (key of data/basemodel_map.json), e.g. IL-XL")
    p.add_argument("--sort", choices=CIVITAI_SORTS)
    p.add_argument("--period", choices=["AllTime", "Year", "Month", "Week", "Day"])
    p.add_argument("--limit", type=int, default=20, help="max results (default 20)")
    p.add_argument("--nsfw", action="store_true", help="include NSFW results")

    p = cmd("civitai-info", cmd_civitai_info,
            "civitai.com model or version details (modelId/versionId auto-detected)")
    p.add_argument("id", help="modelId or versionId")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--model", action="store_true",
                       help="treat the id as a model id (skip version fallback)")
    group.add_argument("--version", action="store_true",
                       help="treat the id as a version id")

    p = cmd("civitai-download", cmd_civitai_download,
            "download a LoRA version from civitai.com (most files need CIVITAI_API_KEY)")
    p.add_argument("version_id", type=int)
    p.add_argument("--file-id", type=int, help="specific file of the version (default: primary)")

    p = cmd("lora-sync", cmd_lora_sync,
            "download a civitai LoRA and upload it to RunningHub (idempotent)")
    p.add_argument("version_id", type=int)
    p.add_argument("--name", help="display name for the RunningHub LoRA upload")

    p = cmd("lora-find", cmd_lora_find,
            "search RunningHub public LoRAs and civitai.com in one shot")
    p.add_argument("query")
    p.add_argument("--base", metavar="RH_BASE",
                   help="RunningHub base model filter (key of data/basemodel_map.json)")

    p = cmd("lora-list", cmd_lora_list, "print the local LoRA sync manifest")

    return ap


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "overwrite", False) and hasattr(args, "outdir") and not args.outdir:
        parser.error("--overwrite requires --outdir")
    if getattr(args, "info", None) and args.cmd == "models":
        args.endpoint = args.info
        cmd_models_info(args)
        return
    try:
        args.fn(args)
    except ApiError as e:
        print(json.dumps({"error": str(e), "code": e.code, "httpStatus": e.status,
                          "raw": e.raw if isinstance(e.raw, dict) else str(e.raw or "")},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    except (OSError, urllib.error.URLError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2),
              file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 — type name only: the message may carry URLs
        print(json.dumps({"error": type(e).__name__}, ensure_ascii=False),
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
