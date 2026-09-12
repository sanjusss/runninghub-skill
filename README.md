# RunningHub 开放 API Skill

一个调用 RunningHub（www.runninghub.cn / www.runninghub.ai）开放 API 的 agent skill：运行 ComfyUI 工作流和 AI 应用，查询并调用标准模型，管理任务，上传资源，查询账户、API Key 与队列，并支持从 `Civitai` 搜索、下载 `LoRA` 同步到平台。

## 安装

本仓库**根目录就是 skill 目录**（SKILL.md 在第一层）。安装到 ZCode / Claude Code 等 agent：

```bash
# ZCode（项目级或用户级，二选一）
cp -r . <项目>/.agents/skills/runninghub/
cp -r . ~/.agents/skills/runninghub/

# Claude Code 风格的 skills 目录同理
cp -r . <skills-dir>/runninghub/
```

复制后目录结构应为 `<skills-dir>/runninghub/SKILL.md`（SKILL.md 必须位于以 skill 名命名的目录第一层）。

无任何 pip 依赖：`scripts/rh.py` 仅用 Python 3.9+ 标准库。仅重建模型目录（`scripts/build_models_registry.py`）时才需要 PyYAML 与网络。

## 配置

```bash
read -rsp "RunningHub API Key: " RUNNINGHUB_API_KEY && echo
export RUNNINGHUB_API_KEY
export RUNNINGHUB_HOST=www.runninghub.ai   # 可选，默认 www.runninghub.cn；两站 key 互不相通
export CIVITAI_API_KEY=...                 # 可选，仅从 civitai.com 下载 LoRA 时需要（搜索匿名可用）
```

脚本优先从环境变量读取 key。不要把 key 写进项目文件或命令参数。

`CIVITAI_API_KEY` 在 `Civitai` 网页的用户设置 → `API Keys` 里创建；`civitai-search`/`civitai-info`/`lora-find` 不需要它。

Key 在网页端创建：https://www.runninghub.cn/enterprise-api/consumerApi （国际站把域名换成 www.runninghub.ai）。

注意：标准模型 API 仅企业级-共享 Key 可调用（消费级 Key 会收到错误码 1014）；工作流与 AI 应用 API 各类 Key 均可。

## 快速验证

```bash
python3 scripts/rh.py account                 # 余额与任务数
python3 scripts/rh.py models --task text-to-video  # 浏览当前模型目录，不创建收费任务
python3 scripts/rh.py --help
```

完整用法见 [SKILL.md](SKILL.md) 与 `references/` 下各文档。

## 目录结构

```
SKILL.md                        # 主入口：双站点、命令速查、场景路由
scripts/rh.py                   # 统一 CLI（26 个子命令，纯标准库）
scripts/build_models_registry.py# 从官方文档重建 data/models.json
data/models.json                # 标准模型端点目录（参数、枚举、安全默认值）
data/basemodel_map.json         # RunningHub 底模 → Civitai baseModel 允许集与血统权重
references/                     # 10 份按需加载的深度文档（API 参考/任务生命周期/错误码/Civitai LoRA 同步等）
```

## 数据维护

RunningHub 上新模型后重跑（需 PyYAML + 网络）：

```bash
python3 scripts/build_models_registry.py
```

构建器会删除文档中的输入文件示例和签名 URL 参数。任何文档页面读取失败时，默认保留现有目录，不写入不完整结果。

官方 API 文档：https://www.runninghub.cn/runninghub-api-doc-cn/
