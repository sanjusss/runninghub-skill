# `Civitai` `LoRA` 搜索、下载与同步

把 `Civitai`（模型分享社区）上的 `LoRA` 下载到本地，再上传到 `RunningHub`，供工作流里的 `RHLoraLoader` 节点使用。`LoRA` 指在基础模型（下文称底模）之上增量训练的小模型文件，叠加后能改变出图的角色、风格或概念。

分工：`AI` 负责判断（拆关键词、比较候选、装配参数），`rh.py` 负责全部网络请求、下载、校验、去重和上传。

## 1. 前置条件

- `RUNNINGHUB_API_KEY`：必填，沿用本技能的约定。
- `CIVITAI_API_KEY`：可选。搜索与详情匿名可用；下载大多需要（`Civitai` 上多数模型要求登录后下载）。在 `Civitai` 网页的用户设置 → `API Keys` 里创建。
- 网络：本机要能访问 `civitai.com`。中国大陆通常需要代理：`export HTTPS_PROXY=...`。脚本不做连通性预检，只在连接失败时提示这一条。

## 2. 意图解析（先想清楚再调用）

用户想要"某个风格/角色/概念的 `LoRA`"时，先确定三件事：

1. **关键词**：拆成 1~2 个短英文词。`Civitai` 的搜索是英文全文匹配，中文词基本搜不到；角色名、画师名、概念词直接用英文原词（如 `hatsune miku`、`pixel art`）。长描述只保留最核心的名词。
2. **目标底模**：从对话或工作流确定，取值必须是 `RunningHub` 前端底模枚举名（如 `IL-XL`、`SDXL 1.0`、`F1基础 D`，完整清单是 `data/basemodel_map.json` 的键）。判断不了就先问用户，不要猜。
3. **商用约束**：用户提到商用、卖图时，只保留 `license.allowCommercialUse` 包含 `Image` 或 `Sell` 的候选。

`NSFW` 默认关闭。用户明确要求时才给 `civitai-search` 加 `--nsfw`。

## 3. 命令序列

```
lora-find（一次拿到公共库 + Civitai 两段候选）
→ AI 按排序公式比较，向用户推荐 2~3 个并等待确认
→ lora-sync（下载 + 校验 + 上传，幂等）
→ 装配 RHLoraLoader
```

1. **找候选**：`rh.py lora-find "<关键词>" --base <底模>`。输出两段：`runninghub`（平台公共库，能直接用就优先用，省去下载和上传）与 `civitai`（需要同步的候选，最多 10 条，要更多或要排序就改用 `civitai-search`）。公共库记录的 `civitaiSource` 字段是从 `desc` 链接提取的 `modelId`/`versionId`，与 `civitai` 段对照可确认"同一个 `LoRA` 公共库已有"。
2. **比较**：按第 4 节公式打分。向用户展示推荐时列出：名称、底模、文件大小、下载量、点赞数、触发词。
3. **同步**：`rh.py lora-sync <versionId> [--name 显示名]`。已同步过的版本直接返回已有 `rhFileName`，不重新下载、不重新上传。需要细看版本（文件列表、哈希、`Early Access` 状态）时先跑 `rh.py civitai-info <modelId|versionId>`（两种 ID 自动识别；同一个数字同时是两种 ID 时，默认按模型返回并带 `ambiguity` 标记，要查版本加 `--version`，要跳过歧义探测加 `--model`）。
4. **装配**：见第 6 节。

相关命令：

- `rh.py civitai-search <关键词> [--base 底模] [--sort 排序] [--period 周期] [--limit N] [--nsfw]`：单独搜 `Civitai`，支持按下载量/评分/时间排序。
- `rh.py civitai-download <versionId> [--file-id N]`：只下载不上传（校验 `SHA256` 并写入清单）。下载前有两道守卫：模型类型必须是 `LoRA` 家族（`LORA`/`LoCon`/`DoRA`，`Checkpoint` 等会被拒绝），文件必须是 `SafeTensor` 格式（主文件不是时自动改选同版本的其他 `SafeTensor` 文件；整个版本没有时报错并列出文件清单）。
- `rh.py lora-list`：查看本地清单（历史同步记录）。

本地状态：清单在 `${RH_LORA_HOME:-~/.runninghub/loras}/manifest.json`，文件存同目录的 `files/` 下，文件名形如 `{modelId}-{versionId}-{原始文件名}`。清单的读取和写入没有加锁：不要并发运行 `lora-sync` 或 `civitai-download`，后结束的进程会覆盖先结束的进程写入的记录。

## 4. 候选排序公式

```
score = 0.45×相关度 + 0.30×质量 + 0.15×热度 + 0.10×血统系数
```

- 相关度：`AI` 给 0~1 的主观分，看关键词是否命中名称、标签、触发词。
- 质量：`thumbsUpCount / (1 + downloadCount)`，在候选内归一化到 0~1（除以候选中的最大值）。
- 热度：`log(1 + downloadCount)`，同样在候选内归一化。
- 血统系数：查 `data/basemodel_map.json` 中该底模条目的 `weight`（键是 `Civitai` 的 `baseModel` 值），没有 `weight` 时取 1.0。跨血统候选（如 `SDXL` 底模挂 `Pony` 的 `LoRA`）系数低，若前两项分数高仍可入选，但要向用户说明效果会劣化。

## 5. 兼容性规则（0~7）

规则 0 是硬门槛，违反就排除；1~7 影响排序与装配动作。

0. **架构硬门槛**：版本 `baseModel` 必须在 `data/basemodel_map.json` 对应底模的 `allow` 集合内（`--base` 参数已在本地过滤）。不在集合内的直接排除。`allow` 为空（如 `HunyuanVideo1.5`、`voxcpm`）表示 `Civitai` 上没有兼容 `LoRA`，直接告诉用户没有可用选项。
1. **`SDXL` 族跨血统能加载但效果劣化**：`SDXL 1.0` 底模可挂 `Illustrious`/`NoobAI`/`Pony` 的 `LoRA`，系数 0.5/0.5/0.2；`IL-XL`、`NoobAI-XL` 同理（见映射表）。跨血统候选入选时要提示劣化风险。
2. **视频 `LoRA` 精确到变体**：`WAN2.1` 的 `t2v` 与 `i2v`、`480p` 与 `720p` 是不同的 `LoRA`，不通用。选定前按工作流里 `checkpoint` 的文件名核对变体。
3. **`Wan 2.2` `A14B` 加速 `LoRA` 成对**：高噪声与低噪声两个 `LoRA` 要一起加载（各接一个加载节点），只挂一个效果错误。
4. **触发词原样注入**：`trainedWords` 第一项是主触发词，整段复制进正向提示词开头，不改写、不翻译；其余项是可选的姿态/服装词，按画面需要追加。条目里带的 `<lora:...:1>` 前缀要去掉——经 API 上传的 `LoRA` 由加载节点生效，提示词里不需要这个标记。
5. **强度默认 0.8**：`strength_model` 与 `strength_clip` 从 0.8 起调。效果过强降到 0.6，太弱升到 1.0。
6. **`Pony`/`Illustrious`/`NoobAI` 系加 `clip skip 2`**：工作流里 `CLIPSetLastLayer` 节点的 `stop_at_clip_layer` 设为 `-2`，否则颜色泛灰。
7. **许可过滤**：商用场景只选 `license.allowCommercialUse` 包含 `Image` 或 `Sell` 的候选。注意：`Civitai` 的许可字段是创作者填写的意愿声明，不是法律上的授权证明。

## 6. 工作流装配

### 6.1 经 API 上传的 `LoRA`（`lora-sync` 的产物）

用 `RHLoraLoader` 节点。参数实测自 `/proxy/{key}/object_info`（2026-09-12）：

| 参数 | 类型 | 填法 |
|------|------|------|
| `model` | 连线 | 接 `checkpoint`/`UNET` 加载节点的 `MODEL` 输出 |
| `file_name` | STRING | `lora-sync` 返回的 `rhFileName` |
| `strength_model` | FLOAT | 默认 1.0，建议 0.8 |
| `strength_clip` | FLOAT | 默认 1.0，建议 0.8 |
| `clip` | 连线 | 可选，接 `CLIP` 加载节点 |

```bash
rh.py workflow <workflowId> \
  --node "15:file_name=api-lora-cn/<md5>.safetensors" \
  --node "15:strength_model=0.8" \
  --node "15:strength_clip=0.8"
```

字段名是 `file_name`，不是 `lora_name`（`lora_name` 是原生 `LoraLoader` 的字段）。

### 6.2 公共库 `LoRA`（`lora-find` 的 `runninghub` 段）

不需要上传，用原生 `LoraLoader` 节点，`lora_name` 填记录的 `nodeModelName`（如 `armonowneyes_il_v2.safetensors`）。公共模型不需要在网页收藏，API 任务可直接使用。

## 7. 失败矩阵

| 现象 | 含义 | 处理 |
|------|------|------|
| "belongs to a Checkpoint model, not a LoRA" | 该版本不是 `LoRA` 家族模型 | 换模型；`RHLoraLoader` 只能加载 `LoRA` |
| "has no SafeTensor file" | 版本只有 `zip`/`ckpt` 等文件 | 换版本或换文件 |
| HTTP 401 | 下载要求登录 | 设置 `CIVITAI_API_KEY` 后重试 |
| HTTP 403（带 `deadline`） | `Early Access` 未到期 | 报错里有截止时间；等期限或换版本 |
| HTTP 410 | 版本已归档 | 换版本 |
| HTTP 404 | 版本或文件不存在 | 换版本，或用 `--file-id` 指定其他文件 |
| HTTP 416 | 续传时本地 `.part` 文件已不小于文件本身 | 脚本视为已下载完整，直接进入校验；校验失败才删除 `.part` 并报错 |
| HTTP 429 / 503 | 限流 / 维护 | 脚本已内置退避重试（1 秒起、30 秒封顶、最多 5 次，优先读 `Retry-After` 头）；仍失败就稍后再跑。下载中途断开同样自动续传，从 `.part` 已有字节继续 |
| `RunningHub` 809 | `LoRA` 超过平台大小上限 | 换更小的文件或量化版本；上限值平台未公开 |
| `SHA256` 不一致 | 下载内容与 `Civitai` 记录的哈希不符 | 脚本已删除 `.part` 文件并报错。`Civitai` 数据库存在已知的错误哈希（上游 `issue`），先打开模型页人工核对，不要直接重下 |

## 8. 安全约定

- `CIVITAI_API_KEY` 与 `RUNNINGHUB_API_KEY` 只从环境变量读取，不写进命令参数、脚本、日志或回复。
- 下载鉴权用 `?token=` 拼在请求 URL 上（跨域跳转会剥掉自定义请求头），该 URL 只存在于进程内存；错误信息里的 URL 一律剥掉查询参数，token 不进日志、不进清单。token 只附加在 `civitai.com` 域名的下载地址上，下载地址指向其他域名时不带 token。
- 代理只提示不强制：脚本不主动设置代理，只在连接失败时提示 `HTTPS_PROXY`。
- `Civitai` 的许可字段是创作者的请求，不是法律许可；商用前自行核实。

## 9. 底模映射与匹配规则

`data/basemodel_map.json` 的键是 `RunningHub` 前端底模枚举名（2026-09-12 从 `www.runninghub.cn` 与 `www.runninghub.ai` 各拉一次，两站一致，共 46 个）。每个条目包含：

- `allow`：允许的 `Civitai` `baseModel` 值列表。`--base` 过滤在本地执行，规则是大小写不敏感的整串匹配；映射表已列出 `768`、`Unclip` 等变体，因此不需要模糊匹配。
- `weight`：血统系数，供第 4 节公式使用；省略时默认 1.0。
- `note`：映射的注意事项（置信度、变体提醒）。

`RunningHub` 公共库版本记录里的 `baseModel` 会出现映射表之外的值（如 `Flux.1`）。匹配不上时把该条目标记为"需人工确认"，不要直接丢弃，也不要硬套到相近底模。
