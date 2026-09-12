# 资源上传实战

## 1. 通用上传（`POST /openapi/v2/media/upload/binary`）

```bash
rh.py upload photo.png        # → data.fileName + download_url
```

- multipart/form-data，字段名 `file`；`rh.py` 分块读取文件，不会把整个视频或压缩包载入内存
- 支持：JPG/PNG/JPEG/WEBP、ZIP（图片压缩包）、MP3/WAV/FLAC、MP4/AVI/MOV/MKV
- **不是图床**：返回的 `download_url` 是签名链接，**约 1 天过期**；`fileName` 是服务器相对路径，不能拼外链
- 两个返回值的用途不同：

| 返回值 | 用在哪 |
|--------|--------|
| `fileName`（如 `openapi/61432a….png`） | ComfyUI 工作流节点：LoadImage 的 `image`、LoadImages(zip) 的 `upload`、LoadAudio 的 `audio`、LoadVideo 的 `video` |
| `download_url` | 标准模型 API 的 image/audio/video URL 参数 |

上传后立即使用（拼进 nodeInfoList 或模型参数）；要长期保存的输入文件留在本地，每次任务重新上传。

## 2. LoRA 上传（仅 RHLoraLoader 可用）

```bash
rh.py upload-lora my-style.safetensors --name 我的风格
# → {"fileName": "api-lora-cn/<md5>.safetensors", "md5Hex": "..."}
```

内部三步（手工调用时照做）：
1. 分块计算文件 MD5 hex
2. `POST /api/openapi/getLoraUploadUrl`，体 `{apiKey, loraName, md5Hex}` → 返回 `data.url`（预签名）与 `data.fileName`
3. `PUT data.url`，头 `Content-Type: application/octet-stream`，体为文件二进制

然后在工作流里用 `RHLoraLoader` 节点加载 `fileName`：
```bash
rh.py workflow <id> --node "15:lora_name=api-lora-cn/<md5>.safetensors"
```

注意：
- 该通道上传的 LoRA **只有 RHLoraLoader 认识**，原生 LoraLoader 读不到
- md5 是缓存键：同文件重复“上传”时，服务端可能只返回 `fileName`；`rh.py` 会直接返回已有文件信息
- 想用平台公共 LoRA：`rh.py resources --type LORA --kw <关键词>`，把 `nodeModelName` 填进节点

## 3. 旧接口（弃用）

`POST /task/openapi/upload`（v1，formData，仅图片）已被 v2 取代，不要在新代码里用。

## 4. 公共模型库检索（配合上传选模型）

```bash
rh.py resources --type CHECKPOINT --kw flux --size 20
# 类型: UNET | CHECKPOINT | LORA | GGUF；--base-models "SD1.5,Flux2-Klein-9B" 过滤底模
```

返回 `records[].nodeModelName` 即工作流 CheckpointLoader/UNETLoader/LoraLoader 节点里应填的确切模型名（含 `.safetensors` 后缀）。
