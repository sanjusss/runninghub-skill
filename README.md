# RunningHub 开放 API Skill

一个操作 RunningHub（www.runninghub.cn / www.runninghub.ai）全部开放 API 的 agent skill：ComfyUI 工作流任务、AI 应用（webapp）、标准模型 API（356 个文/图/音/视频/3D 生成端点）、任务查询与 webhook、资源上传（图片/音频/视频/LoRA）、账户与 API Key 管理。

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
export RUNNINGHUB_API_KEY=<你的key>
export RUNNINGHUB_HOST=www.runninghub.ai   # 可选，默认 www.runninghub.cn；两站 key 互不相通
```

Key 在网页端创建：https://www.runninghub.cn/enterprise-api/consumerApi （国际站把域名换成 www.runninghub.ai）。

注意：标准模型 API 仅企业级-共享 Key 可调用（消费级 Key 会收到错误码 1014）；工作流与 AI 应用 API 各类 Key 均可。

## 快速验证

```bash
python3 scripts/rh.py account                 # 余额与任务数
python3 scripts/rh.py workflow 1904136902449209346 --node "6:text=a cat"   # 跑公开演示工作流
python3 scripts/rh.py models --task text-to-video                            # 浏览 356 个模型端点
```

完整用法见 [SKILL.md](SKILL.md) 与 `references/` 下各文档。

## 目录结构

```
SKILL.md                        # 主入口：双站点、命令速查、场景路由
scripts/rh.py                   # 统一 CLI（20 个子命令，纯标准库）
scripts/build_models_registry.py# 从官方文档重建 data/models.json
data/models.json                # 356 个标准模型端点目录（参数/枚举/默认值）
references/                     # 9 份按需加载的深度文档（API 参考/任务生命周期/错误码等）
```

## 数据维护

RunningHub 上新模型后重跑（需 PyYAML + 网络）：

```bash
python3 scripts/build_models_registry.py
```

官方 API 文档：https://www.runninghub.cn/runninghub-api-doc-cn/
