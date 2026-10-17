"""Exactly two Responses API function tools, with strict JSON schemas."""


def obj(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def nullable(kind, description, **constraints):
    return {"type": [kind, "null"],
            "description": description + '。无值时用JSON null，不要使用字符串"null"。', **constraints}


TASK = obj({
    "id": {"type": "string", "minLength": 1, "maxLength": 100},
    "title": {"type": "string", "minLength": 1, "maxLength": 200},
    "course": {"type": "string", "minLength": 1, "maxLength": 100},
    "minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
    "deadline": {"type": "string", "description": "包含时区的ISO时间；考试复习在考试开始前完成"},
    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
    "source": nullable("string", "资料来源或用户明确提供；未知填null，不得编造"),
})

TOOLS = [
    {"type": "function", "name": "course_rag_search", "strict": True,
     "description": "检索本地课程大纲中的评分标准/作业要求及结构化任务中的DDL和考试。先查资料再安排；课程歧义时返回候选。",
     "parameters": obj({
         "query": {"type": "string", "minLength": 1, "maxLength": 1000},
         "course": nullable("string", "课程名、别名或ID；查全部课程填null"),
         "start_date": nullable("string", "按任务截止日期筛选，YYYY-MM-DD，包含当天；未指定范围填null，不得自行编造范围", format="date"),
         "end_date": nullable("string", "YYYY-MM-DD，包含当天；未指定范围填null，不得自行编造范围", format="date"),
         "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
     })},
    {"type": "function", "name": "calendar_scheduler", "strict": True,
     "description": "读取本地ICS日历，展开重复事件，检查空闲时间或生成无冲突排期预览。先plan后由用户/commit保存；不能改变已有课程。",
     "parameters": obj({
         "action": {"type": "string", "enum": ["inspect", "plan"]},
         "start_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD，包含当天"},
         "end_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD，包含当天，范围最多31天"},
         "tasks": {"type": ["array", "null"], "items": TASK, "maxItems": 30,
                   "description": "plan传任务列表；inspect填null。任务时长来自资料估计或用户确认"},
         "daily_start": nullable("string", "每日最早开始HH:MM；null使用preferences.json"),
         "daily_end": nullable("string", "每日最晚结束HH:MM；null使用preferences.json"),
         "max_session_minutes": {"type": ["integer", "null"], "minimum": 15, "maximum": 240,
                                 "description": "每个学习块最多分钟数；null使用默认"},
         "break_minutes": {"type": ["integer", "null"], "minimum": 0, "maximum": 120,
                           "description": "学习块之间最少休息分钟数；null使用默认"},
     })},
]

SCHEMAS = {tool["name"]: tool["parameters"] for tool in TOOLS}
