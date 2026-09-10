# 标准模型 API 实战（文/图/音/视频/3D 生成模型）

标准模型 API 是 RunningHub 把各家生成模型（可灵、海螺、Vidu、万相、seedance、seedream、悠船、混元 3D、自研 rhart 系列等）封装成的统一 REST 接口，**不需要搭建工作流**。

## 0. 前置条件（最容易翻车）

- **仅企业级-共享 Key 可调用**。消费级/独占 Key 调用直接报 `errorCode: 1014`（"Standard Model API is restricted to Enterprise-Shared API Keys only"）。此时改用工作流 API 跑同类模型，或让用户升级 Key。
- 请求头 `Authorization: Bearer <KEY>`，请求体**不带** apiKey。

## 1. 选模型

356 个端点的完整目录在 `data/models.json`（含每个参数的枚举与默认值）：

```bash
python3 scripts/rh.py models                       # 按任务类型实时统计
python3 scripts/rh.py models --task text-to-video  # 某类任务的全部模型
python3 scripts/rh.py models --kw kling            # 关键词搜（厂商/名称/描述）
python3 scripts/rh.py models --info kling-video-o3-pro/text-to-video   # 某模型的全部参数
```

任务类型以 `models` 命令的实时统计为准（text-to-video、image-to-video、text-to-image、image-to-image、reference-to-video、text-to-audio、text-to-music、video-tools、image-to-3d、prompt-enhance 等 20+ 类，官方文档会不定期增删）。

选型建议：官方稳定版 > 低价渠道版（便宜但不稳定，描述里会注明）；pro 画质好、fast/turbo 快、lite 便宜；需要真人/主体一致性认准 reference-to-video 类。

## 2. 调用

```bash
python3 scripts/rh.py model <endpoint> --prompt "..." --param k=v ... --timeout 1800 --outdir ./out
```

- `--prompt` 对应模型的 prompt 参数；TTS 等个别模型的主参数名是 `text`，用 `--param text=...` 传
- **默认值只对枚举/布尔/数值型必填参数自动补齐**（如 aspectRatio/duration/resolution）；**必填的字符串/URL/文件类参数绝不自动填默认值**——文档里它们的"默认值"是示例文案/示例图片链接，缺了会直接报错退出（exit 2），避免拿官方示例数据跑付费任务
- `--param k=true/false`：BOOL 型参数自动转布尔；枚举型参数按文档以字符串发送
- `--param k=数字`：数值型（NUMBER）参数转 JSON 数字；枚举型参数（如 `duration=5`）保持字符串，与文档枚举一致
- 参数值传本地文件路径（`--param image=@/path/photo.png`）会**自动先上传**并把返回的 `download_url` 填入；`@` 指向的文件不存在会报错
- 提交响应就可能直接是 SUCCESS（快任务），否则脚本自动轮询到结束并下载

原始 HTTP：
```
POST https://{host}/openapi/v2/<endpoint>
{"prompt": "...", "aspectRatio": "16:9", ...}
→ {"taskId","status","errorCode","errorMessage","results":[{"url","outputType"}],"usage"}
```

## 3. 输入素材规范

- 图/音/视频输入参数接受**公网 URL**。本地文件先 `rh.py upload <file>`，用返回的 `download_url`（有效期约 1 天）
- 多图输入（参考生视频等）用数组参数：`--param images=json:["url1","url2"]`
- 首尾帧：通常是 `firstFrameImage` + `lastFrameImage` 两个参数（以 `--info` 实际字段为准）

## 4. 输出与花费

- `results[].url` 为结果文件（视频 mp4 / 图片 jpg/png / 音频 mp3 / 3D glb 等），`outputType` 标类型；`rh.py` 自动按扩展名下载
- `usage.consumeCoins` 是本次消耗（RH 币）；批量前先 `rh.py account` 查余额
- 内容审核不通过报 `1501/1505`（改提示词或换图）；真人相关模型有额外限制

## 5. 典型示例

```bash
# 文生视频（字节 seedance 1.5 pro）
python3 scripts/rh.py model seedance-v1.5-pro/text-to-video \
  --prompt "电影级镜头：雨夜霓虹街头的女侦探" \
  --param aspectRatio=16:9 --param duration=5 --param resolution=720p \
  --param generateAudio=true

# 图生视频（上传本地图 → 自动转 URL）
python3 scripts/rh.py model kling-video-o3-pro/image-to-video \
  --prompt "让画面中的人物挥手微笑" --param image=@./photo.png

# 文生图
python3 scripts/rh.py model seedream-v5-pro/text-to-image \
  --prompt "高端护肤品电商主图，纯白展台" --param width=1024 --param height=1024

# 音乐
python3 scripts/rh.py model rhart-audio/text-to-audio/music-2.5 \
  --prompt "80s synth-pop, female vocal, 120BPM"

# 3D
python3 scripts/rh.py model hunyuan3d-v3.1/image-to-3d --param image=@./toy.png
```

模型清单维护：`python3 scripts/build_models_registry.py`（详见 SKILL.md 数据维护节）。
