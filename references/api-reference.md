# RunningHub 开放 API 完整参考

所有端点均在两个站点可用：`https://www.runninghub.cn` 与 `https://www.runninghub.ai`（下文以 `{host}` 指代）。

认证方式（所有端点通用）：
- **v1 平台端点**（`/task/*`、`/api/*`、`/uc/*`）：请求头 `Authorization: Bearer <KEY>`，同时把 `apiKey` 放进请求体（GET 则放 query）
- **v2 端点**（`/openapi/v2/*`）：仅请求头 `Authorization: Bearer <KEY>`，请求体不带 apiKey
- v1 端点还要求 `Host` 头与访问域名一致（HTTP/1.1 默认行为，一般无需手动处理）

响应包络：
- v1：`{"code": 0, "msg": "success", "errorMessages": null, "data": {...}}`，`code != 0` 即失败
- v2：直接返回业务对象（如 `{taskId, status, errorCode, errorMessage, results, ...}`），失败时 `errorCode` 非空；Key 管理类 v2 接口仍用 `code/msg` 包络

---

## 1. ComfyUI 工作流

### 1.1 发起任务（简易/高级同一端点）
```
POST {host}/task/openapi/create
```
"简易"与"高级"的区别只是带不带 `nodeInfoList`。

请求体：
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| apiKey | string | 是 | |
| workflowId | string | 是 | 工作流 ID（网页版工作流编辑页 URL 里 `workflowId=` 后的数字） |
| nodeInfoList | array | 否 | 节点参数替换列表 `[{nodeId, fieldName, fieldValue}]` |
| workflow | string | 否 | 完整 API 格式工作流 JSON（字符串）。传入后忽略 nodeInfoList，但 workflowId 仍需是已存在的合法 ID |
| webhookUrl | string | 否 | 任务结束回调地址（TASK_END 事件） |
| instanceType | string | 否 | `default`(24G 显存)/`plus`(48G 显存) |
| retainSeconds | int | 否 | 10~180，任务结束后保留实例秒数以减少冷启动（仅企业级-共享 Key，额外计费） |
| usePersonalQueue | bool | 否 | 独占 Key 任务自动进个人队列排队（上限 1000） |
| addMetadata | bool | 否 | 默认 true，是否在输出图写入提示词元信息 |
| accessPassword | string | 否 | 工作流开启加密访问时的访问密码 |

响应 `data`：
```json
{
  "taskId": "1900000000000000001",
  "taskStatus": "RUNNING",          // CREATE/QUEUED/RUNNING/SUCCESS/FAILED
  "clientId": "a1b2c3d4e5f6...",
  "netWssUrl": "wss://{host}:443/ws/c_instance?...",  // ComfyUI 原生 WS 进度通道（可选）
  "promptTips": "{\"result\": true, \"error\": null, ...}"  // 工作流校验结果，result=false 时有 node_errors
}
```

### 1.2 获取工作流 API 格式 JSON
```
POST {host}/api/openapi/getJsonApiFormat
{"apiKey": "...", "workflowId": "1904136902449209346"}
```
响应 `data.prompt` 为字符串化的 ComfyUI API 格式 JSON（节点 ID → class_type/inputs）。用于确定 nodeId 与 fieldName。

### 1.3 取消任务
```
POST {host}/task/openapi/cancel
{"apiKey": "...", "taskId": "..."}
```
成功 `code:0`；任务不存在 `807`。运行中的任务取消即停止计费消耗。

---

## 2. AI 应用

### 2.1 发起 AI 应用任务
```
POST {host}/task/openapi/ai-app/run
```
请求体：`apiKey`、`webappId`（必填，应用页 URL 里 `webappId=` 后的数字）、`nodeInfoList`（该应用的输入节点）、`webhookUrl`、`instanceType`、`accessPassword`（应用开启加密访问时）。

响应同 1.1。注意：AI 应用产出的图片/视频**不带工作流信息**。

### 2.2 获取 AI 应用调用示例（含 nodeInfoList 模板）
```
GET {host}/api/webapp/apiCallDemo?apiKey=<KEY>&webappId=<ID>
```
响应 `data`：`curl`（现成 curl 示例）、`webappName`、`nodeInfoList`（该应用全部可填节点及默认值）、`covers`、`tags`、`statisticsInfo`。**调用不熟悉的 AI 应用前先取这个模板。**

---

## 3. 标准模型 API（356 端点，随官方目录更新）

通用形态：
```
POST {host}/openapi/v2/{endpoint}
```
`{endpoint}` 如 `seedance-v1.5-pro/text-to-video`、`rhart-image-n-pro/text-to-image`、`rhart-audio/text-to-audio/music-2.5`、`hunyuan3d-v3.1/image-to-3d`。请求体为模型参数（prompt、aspectRatio、duration、resolution、generateAudio…），完整目录见 `data/models.json`（`rh.py models` 查询）。

响应（提交即返回，可能已含结果）：
```json
{
  "taskId": "2009191190196789249",
  "status": "QUEUED|RUNNING|SUCCESS|FAILED",
  "errorCode": "", "errorMessage": "",
  "results": [{"url": "https://...", "outputType": "jpg", "nodeId": "", "text": null}],
  "usage": {"consumeCoins": "17", "taskCostTime": "83", ...}
}
```

**权限**：仅企业级-共享 Key 可用，否则 `errorCode: 1014`。图/音/视频输入参数一般接受公网 URL（`rh.py upload` 返回的 `download_url` 可直接用，有效期约 1 天）。

详细用法见 `references/model-api.md`。

---

## 4. 任务查询与 webhook

### 4.1 查询任务生成结果 V2（推荐）
```
POST {host}/openapi/v2/query
{"taskId": "..."}
```
响应：`taskId`、`status`(QUEUED/RUNNING/SUCCESS/FAILED)、`errorCode`、`errorMessage`、`results[]`(url/outputType/nodeId/text)、`failedReason`（失败时含 ComfyUI traceback）、`usage`(consumeCoins/taskCostTime)、`taskUsageList`（子任务）。

### 4.2 查询任务状态 v1（已弃用，仍可用）
```
POST {host}/task/openapi/status  →  {"code":0,"data":"RUNNING"}
```

### 4.3 查询任务生成结果 v1（已弃用，仍可用）
```
POST {host}/task/openapi/outputs
```
- 成功：`data:[{fileUrl, fileType, taskCostTime, nodeId, consumeCoins, storageType, bucket, objectKey}]`
- 运行中：`code:804, msg:APIKEY_TASK_IS_RUNNING`，`data.netWssUrl` 可选用于进度
- 排队中：`code:813`
- 失败：`code:805`，`data.failedReason`（exception_type/node_id/traceback…）

### 4.4 获取 webhook 事件详情
```
POST {host}/task/openapi/getWebhookDetail
{"apiKey": "...", "taskId": "..."}
```
响应 `data`：`id`、`webhookUrl`、`eventData`、`callbackStatus`(SUCCESS/FAILED)、`callbackResponse`、`retryCount`、时间戳。任务未配置 webhook 时返回 `code:1 "webhook event not exists"`。

### 4.5 重发 webhook 事件
```
POST {host}/task/openapi/retryWebhook
{"apiKey": "...", "webhookId": "<4.4 返回的 id>", "webhookUrl": "https://..."}  // webhookUrl 可选，可改投递地址
```

### webhook 回调格式（平台 → 你的 URL，POST）
```json
{"event": "TASK_END", "taskId": "1904163390028185602",
 "eventData": "{\"code\":0,\"msg\":\"success\",\"data\":[{\"fileUrl\":\"...\",\"fileType\":\"png\",\"nodeId\":\"9\"}]}"}
```

---

## 5. 资源上传

### 5.1 文件上传（v2，multipart）
```
POST {host}/openapi/v2/media/upload/binary
Content-Type: multipart/form-data; 字段名 file
```
支持：图片(JPG/PNG/JPEG/WEBP)、图片压缩包(ZIP)、音频(MP3/WAV/FLAC)、视频(MP4/AVI/MOV/MKV)。

响应 `data`：
```json
{"type": "image",
 "download_url": "https://...(签名 URL，有效期约 1 天) → 用于标准模型 API 参数",
 "fileName": "openapi/61432a...png → 用于 ComfyUI LoadImage/LoadAudio/LoadVideo 节点",
 "size": "3490"}
```

### 5.2 LoRA 上传（3 步，RHLoraLoader 专用）
1. `POST {host}/api/openapi/getLoraUploadUrl`，体：`{apiKey, loraName, md5Hex}`（文件 MD5 十六进制）
2. 响应 `data.url` 为预签名地址，`PUT` 上传文件本体（`Content-Type: application/octet-stream`）
3. `data.fileName`（形如 `api-lora-cn/<md5>.safetensors`）填入 `RHLoraLoader` 节点

注意：此方式上传的 LoRA 仅 RHLoraLoader 节点可用；以 md5 为缓存键，重复上传同文件直接复用。

### 5.3 上传资源 v1（已弃用）
`POST {host}/task/openapi/upload`（formData: apiKey + file，仅图片）。新代码一律用 5.1。

---

## 6. 账户与 Key

### 6.1 获取账户信息
```
POST {host}/uc/openapi/accountStatus   {"apikey": "<KEY>"}
```
响应 `data`：`remainCoins`(RH 币)、`remainMoney`+`currency`(钱包余额)、`currentTaskCounts`、`apiType`(NORMAL 等)。

### 6.2 查询 API Key 列表
```
GET {host}/openapi/v2/api-key/list
```
响应 `data[]`：`key`(掩码)、`apiKeyName`、`status`、`quotaLimit/quotaUsed`、`createdAt` 等。

### 6.3 查询指定 Key 队列状态
```
GET {host}/openapi/v2/queue/status
```
响应 `data`：`apiKeyType`(EXCLUSIVE 独占/SHARED 共享/NORMAL 消费级)、`concurrentLimit`、`runningCount`、`queuedCount`、`totalCurrentTasks`、`instanceCount(s)`。

### 6.4 获取公共模型列表（ComfyUI 模型库检索）
```
POST {host}/openapi/v2/resource/list
{"resourceType": "UNET|CHECKPOINT|LORA|GGUF", "resourceName": "关键词",
 "baseModels": ["Flux2-Klein-9B"], "current": 1, "size": 10}
```
响应 `data.records[]`：`nodeModelName`(填入工作流加载节点的名字)、`posterUrl`、`owner`、`tags`、分页信息。

---

## 7. 原生 ComfyUI 协议代理

把 RunningHub 当本地 ComfyUI 用（兼容 SillyTavern/Krita/EasyAI 等插件）：
```
24G 显存: https://{host}/proxy/<apiKey>
48G 显存: https://{host}/proxy-plus/<apiKey>
```
等同于 `http://127.0.0.1:8188` 的 ComfyUI 原生接口（/prompt、/history、WebSocket…）。模型需先在 RunningHub 模型库收藏。

---

## 8. LLM API（OpenAI 兼容）

```
POST https://llm.runninghub.cn/v1/chat/completions
Authorization: Bearer <企业级-共享 KEY>
{"model": "glm-5.2", "messages": [...], "max_tokens": 2048}
```
仅国内站 `llm.runninghub.cn`，仅企业级-共享 Key，兼容 OpenAI/Anthropic/Gemini 协议（`/v1/chat/completions` 等）。

---

## 参考来源与实测说明

- 文档：https://www.runninghub.cn/runninghub-api-doc-cn/（任务状态/输出 v1 已标弃用，推荐 v2 query）
- 本表全部端点已于 2026-09 在 `www.runninghub.ai` 实测通过（模型 API 与 AI 应用受 Key 权限限制验证到错误码层面：1014/901）。
