#!/usr/bin/env python3
"""Unified CLI for every RunningHub open API (www.runninghub.cn / www.runninghub.ai).

Python 3.9+ standard library only — no pip installs required.

Coverage
  Platform   : account / api-keys / queue status / public ComfyUI model list
  Tasks      : v2 query, v1 status & outputs, cancel, wait-and-download
  Workflows  : run (simple/advanced nodeInfoList), fetch API-format JSON
  AI apps    : run, fetch API call demo (nodeInfoList template)
  Models     : search registry, show params, run any 标准模型 API endpoint
  Uploads    : media (v2 binary), LoRA (md5 + presigned PUT)
  Webhooks   : event detail, retry

API key resolution: --key flag > $RUNNINGHUB_API_KEY
Host resolution   : --host flag > $RUNNINGHUB_HOST > www.runninghub.cn
  (use --host www.runninghub.ai for the international site)

Exit codes: 0 ok, 1 API error, 2 usage/input error, 3 poll timeout.
Full endpoint docs: references/ in the skill directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = SCRIPT_DIR.parent / "data" / "models.json"

DEFAULT_HOST = "www.runninghub.cn"
TERMINAL_STATES = {"SUCCESS", "FAILED"}
POLL_INTERVAL = 3.0


# ---------------------------------------------------------------------------
# HTTP core
# ---------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, message: str, code=None, raw=None):
        super().__init__(message)
        self.code = code
        self.raw = raw


def resolve_key(args) -> str:
    key = getattr(args, "key", None) or os.environ.get("RUNNINGHUB_API_KEY", "")
    key = key.strip()
    if not key:
        print("No API key. Pass --key, or export RUNNINGHUB_API_KEY. "
              "Create keys at https://www.runninghub.cn/enterprise-api/consumerApi",
              file=sys.stderr)
        sys.exit(2)
    return key


def resolve_host(args) -> str:
    host = getattr(args, "host", None) or os.environ.get("RUNNINGHUB_HOST", "") or DEFAULT_HOST
    return host.removeprefix("https://").removeprefix("http://").rstrip("/")


def http_request(method: str, url: str, *, headers=None, body=None, timeout=120) -> dict:
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("User-Agent", "runninghub-skill/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        payload = e.read()
    except urllib.error.URLError as e:
        raise ApiError(f"network error: {e.reason}") from e
    text = payload.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ApiError(f"non-JSON response from {url}: {text[:300]}") from None


def api_v1(key: str, host: str, path: str, payload: dict | None = None, method: str = "POST") -> dict:
    """Legacy /task /api /uc endpoints: apiKey in body/query + Bearer header."""
    body = dict(payload or {})
    body.setdefault("apiKey", key)
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

def upload_media(key: str, host: str, file_path: str) -> dict:
    path = Path(file_path)
    if not path.is_file():
        print(f"file not found: {file_path}", file=sys.stderr); sys.exit(2)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    boundary = "----rh-skill-" + hashlib.md5(str(time.time()).encode()).hexdigest()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    resp = http_request(
        "POST", f"https://{host}/openapi/v2/media/upload/binary",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }, body=body, timeout=600)
    check(resp)
    return resp["data"]


def upload_lora(key: str, host: str, file_path: str, lora_name: str | None) -> dict:
    """3-step LoRA flow: md5 → presigned URL (v1 API) → PUT to COS."""
    path = Path(file_path)
    if not path.is_file():
        print(f"file not found: {file_path}", file=sys.stderr); sys.exit(2)
    md5 = hashlib.md5(path.read_bytes()).hexdigest()
    name = lora_name or path.stem
    resp = check(api_v1(key, host, "/api/openapi/getLoraUploadUrl",
                        {"loraName": name, "md5Hex": md5}))
    data = resp["data"]
    if not isinstance(data, dict) or not data.get("url"):
        emit(resp)
        sys.exit("no upload url returned (LoRA may already exist — try the fileName directly)")
    http_request("PUT", data["url"],
                 headers={"Content-Type": "application/octet-stream"},
                 body=path.read_bytes(), timeout=1800)
    return {"fileName": data.get("fileName"), "md5Hex": md5,
            "hint": f"use fileName in the RHLoraLoader node"}


def download(url: str, out: str | None) -> str:
    """Stream a result URL to disk; returns the local path."""
    if not out:
        name = os.path.basename(urllib.parse.urlparse(url).path) or "download"
        out = str(Path.cwd() / name)
    req = urllib.request.Request(url, headers={"User-Agent": "runninghub-skill/1.0"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(out, "wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    return out


def download_results(results: list, outdir: str, task_id: str) -> list[dict]:
    """Download every file result; annotate each entry with its local path."""
    saved = []
    outdir_path = Path(outdir)
    outdir_path.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(results or []):
        entry = dict(r)
        url = r.get("url") or r.get("fileUrl")
        if url:
            ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or f'.{r.get("outputType") or r.get("fileType") or "bin"}'
            out = str(outdir_path / f"{task_id}_{i}{ext}")
            try:
                entry["localPath"] = download(url, out)
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
    while True:
        resp = query_v2(key, host, task_id)
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
        out["results"] = download_results(final.get("results"), args.outdir, task_id)
    else:
        out["errorCode"] = final.get("errorCode")
        out["errorMessage"] = final.get("errorMessage")
        out["failedReason"] = final.get("failedReason")
    emit(out)
    if final.get("status") != "SUCCESS":
        sys.exit(1)


# ---------------------------------------------------------------------------
# Models registry
# ---------------------------------------------------------------------------

def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        print(f"models registry missing: {REGISTRY_PATH}\n"
              "rebuild with: python3 scripts/build_models_registry.py", file=sys.stderr); sys.exit(2)
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


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
# CLI commands
# ---------------------------------------------------------------------------

def cmd_account(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v1(key, host, "/uc/openapi/accountStatus", {"apikey": key})))


def cmd_apikeys(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v2(key, host, "/openapi/v2/api-key/list", method="GET")))


def cmd_queue(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v2(key, host, "/openapi/v2/queue/status", method="GET")))


def cmd_resources(args):
    key, host = resolve_key(args), resolve_host(args)
    payload = {"resourceType": args.type, "resourceName": args.kw or "",
               "current": args.page, "size": args.size}
    if args.base_models:
        payload["baseModels"] = args.base_models.split(",")
    emit(check(api_v2(key, host, "/openapi/v2/resource/list", payload)))


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
        wf = Path(args.workflow_json).read_text(encoding="utf-8")
        json.loads(wf)  # validate
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
    emit(query_v2(key, host, args.task_id))


def cmd_task_outputs(args):
    key, host = resolve_key(args), resolve_host(args)
    resp = api_v1(key, host, "/task/openapi/outputs", {"taskId": args.task_id})
    emit(resp)  # 804/813/805 envelopes are informative, not fatal
    if resp.get("code") == 0 and args.download and (resp.get("data")):
        print(json.dumps(download_results(resp["data"], args.outdir, args.task_id),
                         ensure_ascii=False, indent=2))


def cmd_task_cancel(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(check(api_v1(key, host, "/task/openapi/cancel", {"taskId": args.task_id})))


def cmd_task_wait(args):
    key, host = resolve_key(args), resolve_host(args)
    run_and_deliver(key, host, args.task_id, args)


def cmd_webhook_detail(args):
    key, host = resolve_key(args), resolve_host(args)
    emit(api_v1(key, host, "/task/openapi/getWebhookDetail", {"taskId": args.task_id}))


def cmd_webhook_retry(args):
    key, host = resolve_key(args), resolve_host(args)
    payload = {"webhookId": str(args.webhook_id)}
    if args.url:
        payload["webhookUrl"] = args.url
    emit(api_v1(key, host, "/task/openapi/retryWebhook", payload))


def cmd_download(args):
    print(download(args.url, args.out))


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
    p.add_argument("--outdir", default=".", help="directory for downloaded outputs")


def add_global(p: argparse.ArgumentParser, suppress: bool = False) -> None:
    # On subparsers use SUPPRESS so an explicit main-parser --key/--host
    # (placed before the subcommand) is not clobbered by subparser defaults.
    default = argparse.SUPPRESS if suppress else None
    p.add_argument("--key", default=default, help="API key (default $RUNNINGHUB_API_KEY)")
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
    p.add_argument("--download", action="store_true")
    p.add_argument("--outdir", default=".")

    p = cmd("task-cancel", cmd_task_cancel, "cancel a queued/running task")
    p.add_argument("task_id")

    p = cmd("task-wait", cmd_task_wait, "poll v2 query until done, then download outputs")
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

    return ap


def main() -> None:
    args = build_parser().parse_args()
    if getattr(args, "info", None) and args.cmd == "models":
        args.endpoint = args.info
        cmd_models_info(args)
        return
    try:
        args.fn(args)
    except ApiError as e:
        print(json.dumps({"error": str(e), "code": e.code,
                          "raw": e.raw if isinstance(e.raw, dict) else str(e.raw or "")},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
