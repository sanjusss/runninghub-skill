# 错误码与排查手册

## 通用排查顺序

1. **401 / "API Key 不存在" / 1002 / 802** → key 错或站点不对（.cn 的 key 打不通 .ai）。换 `--host` 重试，或 `rh.py apikeys` 确认。
2. **1014** → 标准模型 API 仅企业级-共享 Key。`rh.py queue` 看 `apiKeyType`。
3. **301 PARAMS_INVALID / 1007** → 参数缺失或类型不符。`rh.py models --info <endpoint>` 或 `workflow-json` 核对字段名/枚举值。
4. **803 APIKEY_INVALID_NODE_INFO** → nodeInfoList 的 nodeId/fieldName 与工作流不符。`rh.py workflow-json <id>` 逐字核对。
5. **804/813（v1 outputs）** → 不是失败！804=运行中、813=排队中，继续轮询即可（v2 query 无此歧义）。

## v1 接口错误码（code/msg）

| code | msg | 含义与处理 |
|------|-----|-----------|
| 0 | success | 成功 |
| 1 | （各业务） | 如 "webhook event not exists"（任务没配 webhook），按场景判断 |
| 301 | PARAMS_INVALID | 参数错误：缺必填/类型不符 |
| 380 | WORKFLOW_NOT_EXISTS | 工作流 ID 无效，或不在当前站点 |
| 412 | TOKEN_INVALID | API 路径拼错（检查 URL） |
| 415 | TASK_INSTANCE_MAXED | 独占机器不足，30–120 秒后重试 |
| 416 | TASK_CREATE_FAILED_BY_NOT_ENOUGH_WALLET | 余额不足，充值 |
| 421 | TASK_QUEUE_MAXED | 共享并发达上限：等几秒重试或降并发 |
| 423 | TASK_NOT_FOUNED | taskId 错误或已被清理 |
| 433 | VALIDATE_PROMPT_FAILED | 工作流校验失败，看 msg/`promptTips.node_errors` |
| 435 | TASK_USER_EXCLAPI_INSTANCE_NOT_FOUND | 48G 调用缺 `"instanceType":"plus"` |
| 436 | TASK_USER_EXCLAPI_REQUIRED | 独占会员到期 |
| 500 | UNKNOWN_ERROR | 服务端异常，联系支持 |
| 801 | APIKEY_UNSUPPORTED_FREE_USER | 免费用户不能用 API Key |
| 802 | APIKEY_UNAUTHORIZED | Key 未授权/已禁用 |
| 803 | APIKEY_INVALID_NODE_INFO | nodeInfoList 与工作流不匹配 |
| 804 | APIKEY_TASK_IS_RUNNING | 任务运行中（outputs 接口的状态信号） |
| 805 | APIKEY_TASK_STATUS_ERROR | 任务失败/中断，`data.failedReason` 有 traceback |
| 806 | APIKEY_USER_NOT_FOUND | Key 关联用户不存在 |
| 807 | APIKEY_TASK_NOT_FOUND | 任务不存在（取消/查询时） |
| 808 | APIKEY_UPLOAD_FAILED | 上传失败（存储/网络） |
| 809 | APIKEY_FILE_SIZE_EXCEEDED | 文件超限，压缩后重试 |
| 810 | WORKFLOW_NOT_SAVED_OR_NOT_RUNNING | 工作流需先在网页**保存并手动运行过一次** |
| 811 | CORPAPIKEY_INVALID | 企业 Key 无效 |
| 812 | CORPAPIKEY_INSUFFICIENT_FUNDS | 企业账户余额不足 |
| 813 | APIKEY_TASK_IS_QUEUED | 排队中（状态信号，勿重试提交） |
| 814 | PERSONAL_QUEUE_COUNT_LIMIT | 个人队列超 1000 |
| 901 | WEBAPP_NOT_EXISTS | webappId 错误或不在当前站点 |

## v2 接口错误码（errorCode/errorMessage）

| code | 含义与处理 |
|------|-----------|
| 1000 | 未知错误，重试或联系支持 |
| 1001 | URL 无效（路径拼错） |
| 1002 | API Key 无效 |
| 1003 | 请求频率超限，放慢 |
| 1004 | 任务不存在或已过期 |
| 1005 / 1010 / 1011 | 服务端错误/不可用/繁忙：稍后重试 |
| 1006 | 任务执行超时：重试 |
| 1007 | 参数校验失败 |
| 1008 | 文件超大小限制 |
| 1009 | HTTP 方法不对（注意 GET/POST，如 queue/status 是 GET） |
| 1012 | 上游服务异常 |
| 1013 | 文件处理失败（检查 URL 或重新上传） |
| 1014 | 标准模型 API 仅限企业级-共享 Key |
| 1015 | 生成失败，重试 |
| 1101 | 工作流节点数据解析错误 |
| 1501 | 内容审核未通过：改提示词/图片 |
| 1504 | 模型响应超时 |
| 1505 | 禁止真人写实生成：改提示词/参考图 |

## 任务失败的定位

v2 query 失败响应：
```json
{"status": "FAILED", "errorCode": "1000", "errorMessage": "unknown error",
 "failedReason": {"exception_type": "TypeError", "node_id": "276",
                  "node_name": "SONIC_PreData", "traceback": ["..."]}}
```
先看 `failedReason.node_id` 定位节点，再 `workflow-json` 核对该节点 inputs；`traceback` 数组是 ComfyUI 执行栈。

## 网络层

- `network error: ...`（rh.py）→ 本地网络/代理问题；国际站 `.ai` 从国内访问可能需要代理
- 结果 URL 下载失败 → 多为签名过期（约 1 天），重新生成或让用户重跑
