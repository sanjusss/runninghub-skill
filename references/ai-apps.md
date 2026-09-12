# AI 应用（webapp）API 实战

AI 应用 = 平台上别人（或你自己）发布的**封装好的 ComfyUI 工作流**，面向终端用户的表单式界面。API 调用比裸工作流更简单：输入节点少、语义化命名字段。

## 1. 拿到 webappId

应用页 URL：`https://www.runninghub.cn/webapp/<webappId>` 或 `?webappId=`。用户发来链接直接提数字。

## 2. 先取调用模板（强烈推荐第一步）

```bash
python3 scripts/rh.py app-demo 1877265245566922800
```

响应 `data` 包含：
- `nodeInfoList`：该应用**全部可输入节点**，含 `nodeId`、`fieldName`、`fieldValue`(默认值)、`fieldType`、中英文说明——照抄结构改值即可
- `curl`：一条现成的 curl 命令
- `webappName`、`covers`(封面图)、`tags`、`statisticsInfo`(点赞/使用量)

报 `901 WEBAPP_NOT_EXISTS` = ID 不存在或不在当前站点（.cn/.ai 应用互不相通，换 host 重试）。

## 3. 发起任务

```bash
python3 scripts/rh.py app <webappId> \
  --node "122:prompt=一个在教室里的金发女孩" \
  --node "123:image=openapi/61432a….png" \
  --password 访问密码 --timeout 1800
```

端点：`POST /task/openapi/ai-app/run`，体：`apiKey`、`webappId`（JSON 数字，不是字符串；`rh.py app` 已自动转换）、`nodeInfoList`、可选 `webhookUrl`/`instanceType`/`accessPassword`。

与工作流 API 的差异：
1. 结果**不带工作流信息**（无 ComfyUI metadata）
2. `nodeInfoList` 里的 nodeId 是**应用表单节点**（app-demo 给出的），不是底层工作流的节点
3. 图片输入同样用 `rh.py upload` 的 `fileName`（走应用内部 LoadImage）或公网 URL（应用若用 LoadImageFromUrl）
4. `--instance plus` 可选 48G 机型

## 4. 查结果

与工作流完全一致（`task-wait`/`task-query`），见 `task-lifecycle.md`。AI 应用往往一分钟以上，`--timeout 1800` 稳妥。任务成功后默认返回结果 URL；需要下载时传入 `--outdir <绝对目录>`。

## 5. 找"有什么应用"

API 不提供应用广场列表接口——让用户在网页 https://www.runninghub.cn/ai-app 或应用广场挑选后发链接/ID 给你；或用浏览器技能访问广场页提取。拿到候选 ID 后用 `app-demo` 反查名称、封面、统计信息与输入表单。
