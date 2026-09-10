# 账户、API Key 类型与权限

## 1. Key 类型与能力矩阵（决定你能调什么）

| API 类型 | 消费级-会员 | 企业级-共享 | 企业级-独占 |
|----------|:---:|:---:|:---:|
| ComfyUI 工作流 API | ✅ | ✅ | ✅ |
| AI 应用 API | ✅ | ✅ | ✅ |
| 标准模型 API | ❌(1014) | ✅ | ❌(1014) |
| LLM API（llm.runninghub.cn） | ❌ | ✅ | ❌ |

- 企业级-独占 Key 需先购买独占机器才能创建；任务默认抢占式，可用 `usePersonalQueue:true` 改为自动排队
- 消费级 Key 并发低（实测 NORMAL 型 `concurrentLimit: 2`），超并发报 `421 TASK_QUEUE_MAXED`
- 账户维度：个人账户（默认）与团队账户（企业协作/统一结算，Key 归属团队）

## 2. 常用命令

```bash
python3 scripts/rh.py account   # 余额 remainCoins(RH币)/remainMoney、当前任务数、apiType
python3 scripts/rh.py apikeys   # Key 列表（掩码）、状态、额度、创建时间
python3 scripts/rh.py queue     # apiKeyType(EXCLUSIVE/SHARED/NORMAL)、并发上限、运行/排队数
```

`account` 的 `apiType` 与 `queue` 的 `apiKeyType` 告诉你手里这把 Key 属于哪类——**调用模型 API 报 1014 时先查这里确认 Key 类型**。

## 3. 两站点规则

- `www.runninghub.cn`（国内，CNY）与 `www.runninghub.ai`（国际，USD）**账号、Key、工作流、AI 应用完全独立**
- key 在哪个站创建就只能调哪个站；报 401/`API Key 不存在` 时先怀疑 host 传错
- 计费币种不同（`account` 的 `currency` 字段：CNY/USD）；结果文件存储桶也不同（国内 cos.ap-beijing / 国际 cos.ap-hongkong）

## 4. 开销控制

- 每个 Generative 任务按次/时长扣 RH 币，提交前 `account` 查 `remainCoins`
- 跑错立刻 `task-cancel`；完成后 `usage.consumeCoins` 对账
- 高频同工作流任务：企业级-共享 Key 加 `--retain 60`（保温实例 10–180s，减少冷启动排队，保温期额外计费）
- 结果/上传 URL 约 1 天过期，及时下载转存
