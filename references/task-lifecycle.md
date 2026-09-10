# 任务生命周期：提交、轮询、结果、webhook、取消

所有 RunningHub 生成任务（工作流 / AI 应用 / 标准模型）走同一条生命周期：

```
提交 → taskId → [QUEUED] → RUNNING → SUCCESS(下载 results) 或 FAILED(读 errorCode/failedReason)
```

状态机：`CREATE → QUEUED → RUNNING → SUCCESS | FAILED`（v2 query 只报 QUEUED/RUNNING/SUCCESS/FAILED 四种）。

## 标准操作序列（推荐）

```bash
# 1) 提交并阻塞等待 + 自动下载（workflow/app/model/task-wait 均内置）
python3 scripts/rh.py workflow 1904136902449209346 --node "6:text=a cat" --timeout 600 --outdir ./out

# 2) 或者分步：先拿 taskId（--no-wait），稍后轮询
python3 scripts/rh.py workflow 1904136902449209346 --no-wait
python3 scripts/rh.py task-wait 1900000000000000001 --timeout 900 --outdir ./out
```

`rh.py` 默认 3 秒轮询一次 `/openapi/v2/query`，stderr 打印进度行，SUCCESS 后把每个 `results[].url` 下载为 `<outdir>/<taskId>_<序号>.<扩展名>` 并在 JSON 里附 `localPath`。

## 轮询策略建议

| 任务类型 | 典型耗时 | 建议 timeout |
|----------|----------|--------------|
| 文生图（SD/Flux 工作流） | 10–90 秒 | 300 秒 |
| 标准模型图片 | 5–60 秒 | 300 秒 |
| 视频（5–10s 时长） | 1–10 分钟 | 1200–1800 秒 |
| 3D / 长视频 / 音乐 | 1–15 分钟 | 1800 秒 |

- 冷启动排队可能把时间翻倍；同一工作流连续提交会复用实例（实测第二次同工作流仅 8 秒）。企业级-共享 Key 可用 `retainSeconds`(10–180) 显式保温。
- 轮询超时≠任务失败：exit code 3 只表示没等到，任务还在跑，稍后继续 `task-wait`。
- 并发上限看 Key：`rh.py queue`（消费级一般并发 2）。达到上限提交会报 `421 TASK_QUEUE_MAXED`，等几秒重试。

## 结果获取的三条路

| 接口 | 用途 | 备注 |
|------|------|------|
| `POST /openapi/v2/query`（`task-query`/`task-wait`） | **首选**：状态+结果+用量一体 | v2；结果在 `results[].url`，失败原因在 `failedReason`，花费在 `usage.consumeCoins` |
| `POST /task/openapi/outputs`（`task-outputs`） | 需要**每个输出节点的明细**（nodeId、taskCostTime、consumeCoins） | v1 已弃用；运行中返回 `code:804`、排队 `813`——这是状态不是错误 |
| `POST /task/openapi/status`（`task-status`） | 只要状态字符串 | v1 已弃用 |

**结果 URL 有效期约 1 天**（上传的 download_url 同理），拿到立刻下载；需要长期保存自行转存。

失败时 v2 query 的 `failedReason` 内含 `exception_type`、`node_id`、`traceback`（v1 outputs 的 805 响应里也有），定位到具体节点后对照修改 nodeInfoList。

## 实时进度（可选进阶）

提交响应里的 `netWssUrl`（wss://…/ws/c_instance?clientId=…）是 ComfyUI 原生 WebSocket 通道，可收到节点级进度（`progress` 事件、`executing`/`executed`），与 ComfyUI 桌面版协议一致。仅当需要做进度条 UI 时才用它；一般轮询足够。

## webhook 回调（免轮询）

提交时带 `--webhook https://your.server/hook`，任务结束平台主动 POST：

```json
{"event": "TASK_END", "taskId": "1904163390028185602",
 "eventData": "{\"code\":0,\"msg\":\"success\",\"data\":[{\"fileUrl\":\"...\",\"fileType\":\"png\",\"nodeId\":\"9\"}]}"}
```

注意 `eventData` 是**字符串化的 JSON**，需二次解析。

投递失败会自动重试（实测字段 `retryCount`）；排查与补发：

```bash
python3 scripts/rh.py webhook-detail <taskId>      # 看 callbackStatus/callbackResponse/retryCount
python3 scripts/rh.py webhook-retry <webhookId> --url https://new-hook/   # webhookId 来自上一条
```

## 取消与退款

```bash
python3 scripts/rh.py task-cancel <taskId>
```

排队/运行中均可取消（成功 `code:0`）。**提交错参数的任务应立刻取消**，避免无谓消耗；取消是否部分计费以 `usage.consumeCoins` 实际查询为准。

## 成本核对

每次任务完成后 v2 query 返回 `usage`：
```json
{"consumeCoins": "7", "taskCostTime": "32", "thirdPartyConsumeMoney": null}
```
`consumeCoins` 为消耗的 RH 币。批量跑之前先 `rh.py account` 看余额（`remainCoins`），跑完再对账。
