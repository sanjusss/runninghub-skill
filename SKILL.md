---
name: runninghub
description: "操作 RunningHub 全部开放 API 的完整技能：ComfyUI 工作流任务、AI 应用（webapp）、标准模型 API（文/图/音/视频/3D，350+ 端点）、任务查询与 webhook、资源上传（图片/音频/视频/LoRA）、账户与 API Key 管理。当用户提到 RunningHub、runninghub.ai、runninghub.cn、跑 ComfyUI 工作流、webappId、workflowId、RH 币、用可灵/海螺/Vidu/万相/seedance 等模型生成图片视频音频 3D 时使用本技能。"
---

# RunningHub 开放 API 技能

RunningHub 是基于 ComfyUI 的 AI 算力云平台，分为两个独立站点，**账号与 API Key 互不相通**：

| 站点 | 域名 | 说明 |
|------|------|------|
| 国内站 | `www.runninghub.cn` | 官方文档默认域名（人民币计价） |
| 国际站 | `www.runninghub.ai` | 面向海外（美元计价，结果文件走海外 COS） |

两个站点 API 路径完全一致，只有 Host 不同。用户给你 key 或链接时，从链接域名判断站点；判断不了就先在两个 host 上各试一次 `account` 命令。

## 统一工具脚本

所有操作都通过本 skill 目录下的 `scripts/rh.py`（纯 Python 3.9+ 标准库，无需安装任何依赖）。**下文及 references 里的 `rh.py` / `python3 scripts/rh.py` 均指该文件的绝对路径**——skill 触发时工作目录通常在用户项目里，直接照抄相对路径会找不到文件：

```bash
python3 <skill目录>/scripts/rh.py <命令> [参数] --key <APIKEY> --host <www.runninghub.cn|www.runninghub.ai>
```

- `--key` 可省略，读取环境变量 `RUNNINGHUB_API_KEY`
- `--host` 可省略，读取 `RUNNINGHUB_HOST`，默认 `www.runninghub.cn`
- 两个参数放在子命令前后均可

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
| 等任务完成并下载 | `rh.py task-wait <taskId> --outdir .` |
| 查任务（v1 带消费明细） | `rh.py task-status` / `rh.py task-outputs` |
| 取消任务 | `rh.py task-cancel <taskId>` |
| 上传图片/音频/视频/zip | `rh.py upload <file>` |
| 上传 LoRA | `rh.py upload-lora <file.safetensors>` |
| 查公共 ComfyUI 模型 | `rh.py resources --type CHECKPOINT --kw flux` |
| webhook 调试 | `rh.py webhook-detail <taskId>` / `rh.py webhook-retry <webhookId>` |
| 下载结果文件 | `rh.py download <url> -o out.png` |

除 `models`（纯文本列表）与 `download`（打印本地路径）外，其余命令输出 JSON（stdout）；轮询进度与提示走 stderr。脚本退出码：0 成功、1 API 错误、2 参数/输入错误（含缺少 API key）、3 轮询超时。

## 核心调用模型（任务型）

所有生成类 API（工作流/AI 应用/标准模型）都是异步任务制：

```
提交任务 → taskId → 轮询状态(QUEUED/RUNNING) → SUCCESS(拿 results[].url) / FAILED(拿 errorCode)
```

`rh.py` 的 `workflow`/`app`/`model`/`task-wait` 命令已内置"提交+轮询+下载"，一次完成；加 `--no-wait` 可只提交。

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
2. **任务花真钱**：提交前可用 `rh.py account` 查余额；提错任务立刻 `rh.py task-cancel`。结果 URL 有效期约 1 天（国际站实测），拿到就下载。
3. **v1 查询接口的语义陷阱**：`/task/openapi/outputs` 在任务运行中会返回 `code:804`、排队中返回 `813`——这不是失败，是状态信号。优先用 v2 `task-query`。
4. **seed 会被强制随机**：API 调用会重置 seed，需要固定 seed 就必须写进 nodeInfoList。
5. **图片识别**：需要识别/检查生成图片或用户上传图片时，按 `references/image-analysis.md` 用具备视觉能力的子代理。RunningHub 图生文端点当前已从官方模型目录下线，若要走 API 备选路线，先 `rh.py models --task image-to-text` 实时确认可用再调用。

## 数据维护

`data/models.json`（356 个标准模型端点，含参数枚举与默认值）由 `scripts/build_models_registry.py` 从官方文档自动生成。RunningHub 上新模型后重新运行即可（需 PyYAML 与网络，输出路径默认已指向 data/models.json）：

```bash
python3 <skill目录>/scripts/build_models_registry.py
```

官方文档：https://www.runninghub.cn/runninghub-api-doc-cn/ （国际站同路径）。平台网页操作手册（非 API）：https://runninghub.feishu.cn/wiki/Vc77w5EaUirOY5kyTQNcrr7GnUd
