# ComfyUI 工作流 API 实战

工作流 = 用户在 RunningHub 网页版（或导入的）ComfyUI 画布，保存后获得数字 ID。API 调用它等效于点"运行"按钮，并可在运行前替换任意节点参数。

## 1. 拿到 workflowId

- 网页版打开工作流编辑页，URL 形如 `https://www.runninghub.cn/comfyui/workflow/<workflowId>` 或查询参数 `workflowId=`
- 用户给的链接里直接提取数字串
- 实测：文档示例 `1904136902449209346` 是公开可跑的演示工作流（SDXL 文生图，节点 6=提示词、3=KSampler、9=SaveImage）

## 2. 搞清 nodeId / fieldName / fieldValue（nodeInfoList 机制）

`nodeInfoList` 的每一项 `{"nodeId": "6", "fieldName": "text", "fieldValue": "..."}` 表示"把 6 号节点的 text 输入替换为某值"。

确定方法（二选一）：

```bash
# 方法 A：拉取该工作流的 API 格式 JSON（无需网页操作）
rh.py workflow-json 1904136902449209346 -o wf.api.json
```
得到的 JSON 结构：`{"3": {"class_type": "KSampler", "inputs": {"seed":…, "steps":…}}, "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "…"}}, …}`。
- 顶层 key = nodeId
- `inputs` 里的 key = fieldName
- 方法 B：网页版右上角"导出工作流 API"下载同一份文件

```bash
# 修改提示词与种子
rh.py workflow 1904136902449209346 \
  --node "6:text=1 girl in classroom" \
  --node "3:seed=1231231"
```

`--node` 语法：`nodeId:fieldName=value`，可重复。value 支持：
- `@path/to/file.txt` — 读文件内容作为值（长提示词/歌词）
- `json:[1,2]` — 内嵌 JSON（批量、结构化输入）
- 也可 `--node-file nodes.json` 直接给完整 `[{nodeId,fieldName,fieldValue},…]` 数组

### 填写规则（踩坑点）

1. **API 调用强制随机 seed**——固定 seed 必须写进 nodeInfoList。
2. `fieldValue` 是 `["7", 0]` 这种数组 = 节点连线，**不要改**。
3. 找不到的 fieldName（如 KSampler 的 `control_after_generate`）是纯前端字段，API 不生效。
4. 数值类型保持一致：seed/steps/cfg 传数字或数字字符串均可（服务端兼容），但枚举字符串要精确（如 `sampler_name: "dpmpp_2m"`）。
5. `promptTips.result=false` 时 `node_errors` 会指出具体节点的问题——提交虽返回 taskId 但跑不通，先看这个字段（`rh.py` 会打 WARNING）。

## 3. 带图片/音频/视频输入

先上传拿 `fileName`，再喂给对应加载节点：

```bash
rh.py upload photo.png
# → data.fileName = "openapi/61432a….png"
rh.py workflow <workflowId> --node "10:image=openapi/61432a….png"
```

| 上传文件 | 工作流节点 | fieldName |
|----------|-----------|-----------|
| 图片 | LoadImage | `image` |
| 图片 ZIP | LoadImages(zip) | `upload`（值为返回的 fileName） |
| 音频 | LoadAudio | `audio` |
| 视频 | LoadVideo | `video` |

有公网图床链接时可改用 `LoadImageFromUrl` 节点直接填 URL，省一步上传。

LoRA（仅 `RHLoraLoader` 节点）：`rh.py upload-lora my.safetensors` 一步完成 md5+预签名上传，返回的 `fileName` 填入节点。详见 `uploads.md`。

## 4. 直接传完整工作流 JSON（免保存到平台）

`--workflow-json wf.api.json`：把本地 API 格式 JSON 作为 `workflow` 字段提交（此时 `workflowId` 仍需是一个已存在的合法 ID 作为载体，nodeInfoList 被忽略）。适合程序化生成工作流、批量换模型的场景。

也可以把任意 ComfyUI 导出的 API JSON 用 `--workflow-json` 直接跑，不用在网页里建工作流。

## 5. 算力与调度参数

| 参数 | 场景 |
|------|------|
| `--instance plus` | 48G 显存机型（默认 default=24G）。报 `435 TASK_USER_EXCLAPI_INSTANCE_NOT_FOUND` 时检查这里 |
| `--retain 60` | 企业级-共享 Key 任务结束保温实例 10–180 秒，连续提交提速明显（额外计费） |
| `--personal-queue` | 独占 Key 任务进个人队列自动调度（上限 1000，超出报 814） |
| `--webhook URL` | 结束回调，见 task-lifecycle.md |
| `--password X` | 工作流开启了加密访问时必须传 |
| `--no-metadata` | 不往输出图里写提示词元数据 |

## 6. 原生 ComfyUI 协议（高级）

`https://{host}/proxy/{API_KEY_FROM_ENV}`（24G）和 `https://{host}/proxy-plus/{API_KEY_FROM_ENV}`（48G）等价于本地 ComfyUI 的 `http://127.0.0.1:8188`：`/prompt`、`/object_info`、`/history`、WebSocket 全套可用。URL 中必须使用环境变量里的真实 key，不要把 URL 写进日志或回复。该方式适合已有 ComfyUI 客户端或插件的场景；模型需先在 RunningHub 网页“模型库”收藏。

## 7. 端到端验证过的例子

```bash
# 提交（替换提示词与 seed）→ 32 秒 SUCCESS → 消耗 7 RH 币 → 返回结果 URL
rh.py workflow 1904136902449209346 \
  --node "6:text=a tiny robot painting a wall mural, colorful" \
  --node "3:seed=777"
```

常见错误：`380 WORKFLOW_NOT_EXISTS`（ID 错/非本站）、`810 WORKFLOW_NOT_SAVED_OR_NOT_RUNNING`（需先在网页保存并手动运行过一次）、`803 APIKEY_INVALID_NODE_INFO`（nodeId/fieldName 与工作流不匹配——用 workflow-json 重新核对）。
