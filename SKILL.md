---
name: runninghub
description: "调用 RunningHub 开放 API：运行 ComfyUI 工作流和 AI 应用，查询并调用文、图、音、视频及 3D 模型，管理任务，上传资源，查询账户、API Key 与队列。当用户提到 RunningHub、runninghub.ai、runninghub.cn、webappId、workflowId、RH 币，或明确要求通过该平台生成内容时使用。"
---

# RunningHub 开放 API 技能

RunningHub 是基于 ComfyUI 的 AI 算力云平台，分为两个独立站点，**账号与 API Key 互不相通**：

| 站点 | 域名 | 说明 |
|------|------|------|
| 国内站 | `www.runninghub.cn` | 官方文档默认域名（人民币计价） |
| 国际站 | `www.runninghub.ai` | 面向海外（美元计价，结果文件走海外 COS） |

两个站点 API 路径完全一致，只有 Host 不同。用户给你 key 或链接时，从链接域名判断站点；判断不了就先在两个 host 上各试一次 `account` 命令。

## 统一工具脚本

支持的操作都通过本 skill 目录下的 `scripts/rh.py` 完成（纯 Python 3.9+ 标准库，无需安装依赖）。运行前先解析 `SKILL.md` 所在目录，后续始终使用 `rh.py` 的绝对路径。references 中的 `rh.py` 也指这个绝对路径。

```bash
python3 <skill目录>/scripts/rh.py <命令> [参数] --host <www.runninghub.cn|www.runninghub.ai>
```

- API key 只从已配置的 `RUNNINGHUB_API_KEY` 读取。不要编造示例 key，不要把 key 写进脚本、命令参数或回复
- 如果环境变量不存在，停止调用并请用户在本机配置
- `--host` 可省略，读取 `RUNNINGHUB_HOST`，默认 `www.runninghub.cn`
- `--host` 只接受 `www.runninghub.cn` 和 `www.runninghub.ai`，可放在子命令前后

### 命令速查

| 场景 | 命令 |
|------|------|
| 查余额/任务数 | `rh.py account` |
| 查 Key 列表 / 并发队列 | `rh.py apikeys` / `rh.py queue` |
| 跑 ComfyUI 工作流 | `rh.py workflow <workflowId> --node 6:text=提示词` |
| 拿工作流 API JSON（找 nodeId/fieldName） | `rh.py workflow-json <workflowId>` |
| 跑 AI 应用 | `rh.py app <webappId> --node ...`（模板先 `rh.py app-demo <webappId>`） |
| 跑标准模型（可灵/海螺/seedream…） | `rh.py model <endpoint> --prompt ... --param k=v` |
| 搜可用模型 | `rh.py models --task text-to-video --kw kling`（详情 `--info <endpoint>`） |
| 查任务（推荐 v2） | `rh.py task-query <taskId>` |
| 等任务完成并返回结果 URL | `rh.py task-wait <taskId>` |
| 等任务完成并下载 | `rh.py task-wait <taskId> --outdir <绝对目录>` |
| 查任务（v1 带消费明细） | `rh.py task-status` / `rh.py task-outputs` |
| 取消任务 | `rh.py task-cancel <taskId>` |
| 上传图片/音频/视频/zip | `rh.py upload <file>` |
| 上传 LoRA | `rh.py upload-lora <file.safetensors>` |
| 查公共 ComfyUI 模型 | `rh.py resources --type CHECKPOINT --kw flux` |
| webhook 调试 | `rh.py webhook-detail <taskId>` / `rh.py webhook-retry <webhookId>` |
| 下载结果文件 | `rh.py download <url> -o out.png` |

除 `models`（纯文本列表）与 `download`（打印本地路径）外，其余命令输出 JSON（stdout）；轮询进度与提示走 stderr。脚本退出码：0 成功、1 API 或文件交付错误、2 参数或输入错误（含缺少 API key）、3 轮询超时。

## 核心调用模型（任务型）

所有生成类 API（工作流/AI 应用/标准模型）都是异步任务制：

```
提交任务 → taskId → 轮询状态(QUEUED/RUNNING) → SUCCESS(拿 results[].url) / FAILED(拿 errorCode)
```

`workflow`、`app`、`model` 会创建收费任务，只能在用户明确要求生成或运行时调用。查询、选型和排错请求不代表用户同意创建任务。提交请求只发送一次，不要因网络异常自动重提。

这些命令默认等待任务完成；`task-wait` 用于等待已有任务。任务成功后默认返回 `results[].url`，不下载文件。传入 `--outdir <目录>` 才会下载，并在结果中增加 `localPath`。已有文件默认不会覆盖；确认需要替换时再加 `--overwrite`。使用 `--no-wait` 可只提交任务。

## 按场景深入（按需阅读）

| 场景 | 先读 |
|------|------|
| 跑/改 ComfyUI 工作流，nodeInfoList 不会填 | `references/comfyui-workflows.md` |
| 跑 AI 应用（webapp） | `references/ai-apps.md` |
| 文/图/音/视频/3D 标准模型 | `references/model-api.md` |
| 上传输入素材或 LoRA | `references/uploads.md` |
| 任务状态含义、轮询策略、webhook 回调 | `references/task-lifecycle.md` |
| 余额、Key 类型、权限、并发 | `references/account-and-keys.md` |
| 报错排查 | `references/errors.md` |
| 全部端点清单（含原始路径） | `references/api-reference.md` |
| 看懂/检查生成结果图片 | `references/image-analysis.md` |

## 关键注意事项

1. **Key 类型决定能力**：标准模型 API 仅限"企业级-共享"Key（报 1014 就是 Key 类型不对）；消费级/会员 Key 只能跑 AI 应用和工作流。详见 `references/account-and-keys.md`。
2. **任务会产生费用**：提交前可用 `rh.py account` 查余额；提错任务立刻 `rh.py task-cancel`。结果 URL 有效期约 1 天。需要保留文件时，传入指向用户工作区的绝对 `--outdir`；不要把最终产物保存到临时目录。
3. **v1 查询接口的语义陷阱**：`/task/openapi/outputs` 在任务运行中会返回 `code:804`、排队中返回 `813`——这不是失败，是状态信号。优先用 v2 `task-query`。
4. **seed 会被强制随机**：API 调用会重置 seed，需要固定 seed 就必须写进 nodeInfoList。
5. **图片识别**：需要检查图片时，按 `references/image-analysis.md` 使用当前环境已有的图片查看能力。不要假定环境允许创建子代理。若要通过 RunningHub API 识别图片，先用 `rh.py models --task image-to-text` 确认可用端点。

## 数据维护

`data/models.json` 由 `scripts/build_models_registry.py` 从官方文档生成，包含模型端点、参数枚举和可安全复用的默认值。使用 `rh.py models` 查询，不要把整个 JSON 文件读入对话上下文。RunningHub 更新模型后可重新构建（需要 PyYAML 与网络）：

```bash
python3 <skill目录>/scripts/build_models_registry.py
```

官方文档：https://www.runninghub.cn/runninghub-api-doc-cn/ （国际站同路径）。平台网页操作手册（非 API）：https://runninghub.feishu.cn/wiki/Vc77w5EaUirOY5kyTQNcrr7GnUd
