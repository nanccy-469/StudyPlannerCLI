# 个人学业与日程管家 CLI

这是一个可运行的 Python 命令行 Agent。它通过两个 Function Calling 工具查询课程资料、读取本地 ICS 日历、拆分学习任务、检查冲突并导出新的日历文件。适用于“现代软件开发方法实践 实验一”的代码与运行演示。

**项目附带的课程、评分比例、任务和考试日期全部是虚构演示数据。** 它们不是实验模板或真实课程的规定。真实使用时请替换 `data/` 下的资料。

## 先运行

需要 Python 3.11 或更新版本。在 Windows PowerShell 中进入本项目目录。

当前机器的项目虚拟环境已经安装好依赖，可以直接运行：

```powershell
.\.venv\Scripts\python.exe main.py --today 2026-10-16 demo
```

也可以双击 `demo.cmd`。演示会真实执行课程检索、排期和 ICS 导出，但对话和工具选择是固定脚本，不调用大模型。每次运行的数据保存在单独的 `runs/demo-*/` 目录，不会影响主日历。

如果是从源码压缩包解压，先安装环境：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --today 2026-10-16 demo
```

`requirements-lock.txt` 记录本次验证使用的精确版本；需要相同依赖版本时用它代替 `requirements.txt`。

## 真实大模型对话

### 智谱 BigModel 与 GLM-5.3-Flash

可以使用智谱模型标准API及截图中的GLM-5.3-Flash。先复制专用配置，再填写自己的智谱Key：

```powershell
Copy-Item .env.bigmodel.example .env
notepad .env
```

配置内容如下，`OPENAI_*`只是兼容接口使用的变量名，密钥应填智谱平台的“模型标准API”Key：

```dotenv
OPENAI_API_KEY=你的智谱模型标准API密钥
OPENAI_MODEL=glm-5.3-flash
OPENAI_BASE_URL=https://open.bigmodel.cn/api/v1
```

已有`.env`时直接编辑，避免复制覆盖。资源包截图不包含API Key；在智谱控制台的[API Keys](https://bigmodel.cn/usercenter/proj-mgmt/apikeys)中创建“模型标准API”类型的Key。模型标准API与Coding Plan的Key类型不同。

```powershell
.\.venv\Scripts\python.exe main.py doctor
.\.venv\Scripts\python.exe main.py chat
```

`doctor`应显示`model`为`glm-5.3-flash`、`api_host`为`open.bigmodel.cn`。它只检查本地配置，不测试密钥或额度。聊天启动后显示所用模型和接口。程序使用智谱文档定义的独立Responses端点`/api/v1/responses`，不能把Chat Completions使用的`/api/paas/v4`地址直接填进来。

智谱适配使用文档列出的请求字段，不发送OpenAI专用的`include`和并行工具参数；保留本地严格Schema校验，支持官方响应示例中的单对象文本content。GLM-5.3-Flash保持思考开启，使用`reasoning.effort=high`，输出上限8192 tokens（含思考）。已在用户本地环境在线验证“软件工程实验一什么时候交”：1次工具调用、2次模型请求、0个工具错误。

### OpenAI 或其他兼容 Responses 的服务

1. 将 `.env.example` 复制为 `.env`。
2. 填写自己的 `OPENAI_API_KEY`，并将 `OPENAI_MODEL` 填为账户可用、支持 Responses API 工具调用的模型 ID。
3. 运行聊天。密钥只放在本地 `.env` 或环境变量中，不要写进源码或提交到仓库。

```powershell
Copy-Item .env.example .env
# 用编辑器填写 .env，已有 .env 时请直接编辑，不要再次复制覆盖。
.\.venv\Scripts\python.exe main.py chat
```

聊天示例：

```text
你 > 软件工程实验一什么时候交？
你 > 帮我安排下周的复习。
你 > 我周一周二晚上19点到21点可以学习。
你 > 优先软件工程，时间可以扩展到18点到22点。
你 > /commit 模型展示的12位plan_id
```

日常使用`chat`即可，默认只显示最终回答。聊天中输入`/trace on`可查看工具参数和完整JSON结果，输入`/trace off`恢复简洁显示；启动时也可加`--trace`用于录屏。详细工具调用始终保存在本地日志中，不需要为了留存实验记录一直打开trace。模型可能根据课程信息是否充分产生不同的澄清问题；真实路由和措辞由模型决定。

聊天内置命令：

| 命令 | 功能 |
| --- | --- |
| `/help` | 显示命令 |
| `/tools` | 查看两个工具的 JSON Schema |
| `/trace on`、`/trace off` | 开启或关闭完整工具参数与结果 |
| `/reset` | 清空会话上下文，保留已保存的日历 |
| `/commit PLAN_ID` | 保存完整预览并导出 confirmed ICS |
| `/exit` | 退出 |

也可以单次提问：

```powershell
.\.venv\Scripts\python.exe main.py --trace ask "软件工程评分标准是什么？"
```

`OPENAI_BASE_URL` 默认为 `https://api.openai.com/v1`，程序会追加 `/responses`。自定义服务必须实现 **Responses API** 的工具调用协议；仅提供 `/chat/completions` 的服务不能直接使用这个适配器。环境变量优先于 `.env`。聊天记录保存在本地 `runs/chat-*.jsonl`；只有当前进程保留完整模型上下文，退出后不会自动恢复对话。

已完成本地工具与模拟API协议测试，并在用户配置的智谱环境中完成一条真实截止日期查询的在线回归。其他真实模型场景尚未系统评测；离线脚本演示不能替代真实Function Calling运行证据。

## 无需密钥的工具命令

仓库中的静态样例以 2026-10-16 为参考日期，对应下周 2026-10-19 至 2026-10-25。`--today` 将逻辑时间固定为当天08:00，用于复现演示；日常使用请省略，让系统使用北京时间。

```powershell
# 检索评分与来源
.\.venv\Scripts\python.exe main.py search "评分标准" --course "软工"

# 查询某个日期范围内的考试
.\.venv\Scripts\python.exe main.py search "考试" --start 2026-10-19 --end 2026-10-25

# 检查两个晚上的空闲时间
.\.venv\Scripts\python.exe main.py --today 2026-10-16 calendar --start 2026-10-19 --end 2026-10-20 --daily-start 19:00 --daily-end 21:00

# 给已有任务生成预览
.\.venv\Scripts\python.exe main.py --today 2026-10-16 plan --start 2026-10-19 --end 2026-10-20 --task-ids se-exam-review --daily-start 18:00 --daily-end 22:00

# 将上一步输出的真实plan_id填入；不要直接使用下面的占位文本
.\.venv\Scripts\python.exe main.py --today 2026-10-16 commit PLAN_ID

# 查看工具Schema或配置状态；doctor不发API请求
.\.venv\Scripts\python.exe main.py tools
.\.venv\Scripts\python.exe main.py doctor
```

全局选项 `--json`、`--trace`、`--today`、`--workspace`、`--model` 放在子命令之前。例：`python main.py --json search "作业" --course "高数"`。直接命令中的课程名请使用 `--course` 指定；真实对话中由模型提取课程名。

自定义任务使用 `plan --tasks-file examples/custom_tasks.json`，文件应为任务数组。日期范围仍需通过 `--start`、`--end` 指定。任务 `deadline` 必须包含时区。

想按新的日期重新生成演示资料，可以使用一个新的空数据目录：

```powershell
.\.venv\Scripts\python.exe main.py --workspace .\my-study-data init-demo
.\.venv\Scripts\python.exe main.py --workspace .\my-study-data --trace chat
```

`init-demo` 遇到已有数据会拒绝覆盖。

## 两个工具及上下文集成

| 工具 | 输入重点 | 实际执行与输出 |
| --- | --- | --- |
| `course_rag_search` | 问题、课程名或别名、截止日期范围、top_k | 从 Markdown 中按标题与行切片，以中文二元词和 BM25 风格的词项评分检索，返回原文片段、文件行号及匹配的任务；歧义时返回候选 |
| `calendar_scheduler` | inspect/plan、学习日期范围、任务时长与deadline、每日可用时段、休息长度 | 读取并展开 ICS 事件，计算忙闲区间；按优先级和截止时间拆分任务，返回学习块、未排完任务及 ICS 预览 |

这是词项检索型 RAG，不使用向量数据库、Embedding 或 PDF 解析。Markdown 提供课程规则；`tasks.json` 是任务日期和时长的结构化来源。课程检索的日期范围筛选的是截止/考试日期；排期的日期范围指定学习发生的日期。

两个工具都使用严格 JSON Schema：每个对象关闭额外字段，所有字段列入 `required`，可选值通过 `null` 表达。程序在本地再次校验参数，按白名单分派到 Python 实现。模型不执行 shell 命令。

真实运行循环如下，`agent.py` 中没有关键词硬编码路由：

```text
用户输入
   ↓
模型接收系统prompt、当前日期、默认偏好、会话历史和工具Schema
   ↓
模型输出 function_call（工具名、JSON参数、call_id）
   ↓
本地参数校验 → 执行课程检索或日历排期
   ↓
function_call_output 按相同call_id返回模型
   ↓
模型继续调用另一个工具、询问信息或给出最终回答
```

上下文包括四部分：检索到的课程片段及来源；日历工具返回的日程和忙闲窗口；同一会话中已确认的课程、时长和偏好；程序提供的当前日期、下周日期范围及默认学习设置。模型返回的全部 output item 都被保留，用于继续工具调用和多轮对话。

## 排期行为与文件

- 默认时区为 Asia/Shanghai，每日18:00到22:00、单块最多60分钟、学习块间至少休息10分钟，均可调整。
- 排序按高/中/低优先级，同优先级先排较早截止的任务；使用确定性贪心算法，不保证所有可行排期都能找到，也不宣称全局最优。
- 避开已有事件、全天事件、重复事件及已保存的学习块。支持 RRULE、EXDATE 和 RECURRENCE-ID，由日历库负责展开。透明事件和已取消事件不占用时间；全天事件的 DTEND 为不包含的结束日期。
- 学习块不晚于任务deadline结束，避免当前时间之前的时段。一轮最多安排31天，默认最小学习块15分钟，最后的不足15分钟尾段可以单独安排。
- 时间排不完时返回 `needs_clarification` 及剩余分钟，仍可查看临时预览；必须调整后重新生成完整计划才能保存。
- `plan` 写入 `state/plans/PLAN_ID.json`，并导出标记为 TENTATIVE 的 `exports/preview-PLAN_ID.ics`。
- `commit` 把新学习块记入 `state/committed.ics`，导出标记为 CONFIRMED 的 `exports/confirmed-PLAN_ID.ics`；导出只包含本次新安排，原始课表不被改写。
- 已保存任务依据稳定task_id避免重复安排；相同plan_id重复保存不会增加重复事件。两次保存之间若日历改变或预览时间已过去，需要重新plan。
- ICS 文件使用UTC存储，命令行以北京时间显示。将 confirmed 文件导入日历软件后才能在该软件看到安排，本项目不连接云日历。

`state/committed.ics` 作为本地忙闲判断的持久记录，退出聊天后仍保留。当前版本没有修改/撤销已保存任务的交互命令；适合课程实验中的查询、计划和首次保存流程。

## 替换为真实课程资料

```text
data/
  courses.json          课程ID、名称、别名和Markdown路径
  tasks.json            任务ID、课程、类型、时长、deadline、优先级和来源
  preferences.json      默认可用时间和休息偏好
  metadata.json         is_demo标记，示例数据为true
  courses/*.md          真实课程大纲或整理后的作业要求
  calendars/*.ics       从日历软件导出的课表和其他日程
```

`courses.json` 中的 `id` 与 `tasks.json` 中的 `course` 要一致。示例中的考试记录表示“考试前的复习任务”，其deadline为考试开始时间；考试本身也作为忙碌事件放入日历。日历目录中的所有 `.ics` 都会读取，替换资料时请移出虚构课表。全部换成真实资料后，将`metadata.json`中的`is_demo`设为false；在此之前，系统会明确标注示例数据。

## 代码入口

```text
main.py                        命令行入口
study_agent/cli.py             子命令与多轮聊天
study_agent/agent.py           Responses HTTP适配及工具调用循环
study_agent/schemas.py         两个工具的严格JSON Schema
study_agent/tools.py           参数校验与白名单分派
study_agent/knowledge.py       课程别名消歧、词项检索、任务筛选
study_agent/calendar_tools.py  日历展开、排期、预览与幂等保存
study_agent/demo.py            演示数据和明确标注的离线脚本
prompts/system.md             核心系统prompt
tests/                        本地工具与模拟API协议测试
docs/experiment_notes.md       报告素材和建议截图
docs/validation.md             验证范围与结果
```

## 测试

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
```

已通过34项测试。测试中的API客户端使用固定响应夹具，只验证协议处理、上下文保留与调用编排，不测试真实模型的意图识别准确率。新增用例复现GLM把JSON null传成字符串"null"的实际问题，验证日期查询一次成功、嵌套可选字段兼容、严格拒绝无效日期、重复错误停止，以及静默聊天模式。测试临时数据只在项目 `runs/_tests/` 下创建和清理。

## 接口资料

- [OpenAI 官方 Function Calling 文档](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI Responses API 参考](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [icalendar 使用文档](https://icalendar.readthedocs.io/en/latest/how-to/usage.html)
- [recurring-ical-events 重复事件示例](https://recurring-ical-events.readthedocs.io/en/latest/user-guide/examples.html)
- [智谱 GLM-5.3-Flash 模型文档](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)
- [智谱 Responses API 兼容说明](https://docs.bigmodel.cn/cn/guide/develop/responses/introduction)
- [智谱 API Key 创建说明](https://docs.bigmodel.cn/cn/guide/start/quick-start)
