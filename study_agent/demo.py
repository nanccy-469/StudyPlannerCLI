from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from uuid import uuid4

from icalendar import Calendar, Event

from .common import Context, TZ, write_json
from .calendar_tools import Scheduler, write_ics
from .tools import ToolRegistry


def seed_data(ctx: Context) -> dict:
    if ctx.data.exists() and any(ctx.data.iterdir()):
        raise ValueError("data目录已有文件，未覆盖。请使用 --workspace 指定一个新的项目数据目录。")
    write_json(ctx.data / "metadata.json", {"is_demo": True,
               "notice": "课程规则、任务日期和课表均为虚构演示数据；替换为真实资料后将is_demo设为false。"})
    monday = ctx.now.date() + timedelta(days=7 - ctx.now.weekday())
    courses = [
        {"id": "软件工程", "name": "软件工程", "aliases": ["软工", "SE"], "syllabus": "courses/software_engineering.md"},
        {"id": "高等数学", "name": "高等数学", "aliases": ["高数", "数学", "math"], "syllabus": "courses/mathematics.md"},
        {"id": "大学物理", "name": "大学物理", "aliases": ["大物", "物理", "physics"], "syllabus": "courses/physics.md"},
    ]
    write_json(ctx.data / "courses.json", courses)
    syllabi = [
        "# 软件工程 示例课程大纲\n\n## 评分标准\n期末考试占50%，课程实验占30%，平时作业占20%。\n\n## 实验要求\n实验一实现一个Agent，提交代码仓库、核心prompt、工具定义、运行截图和实验报告。\n报告应说明主要功能、工具或Skills及上下文集成方法。\n\n## 考试范围\n软件生命周期、需求分析、系统设计与软件测试。\n",
        "# 高等数学 示例课程大纲\n\n## 评分标准\n期末考试占70%，平时作业占20%，课堂测验占10%。\n\n## 作业要求\n本次作业完成教材第三章第1至12题，写出推导过程。\n\n## 复习范围\n极限、导数、定积分与微分方程。\n",
        "# 大学物理 示例课程大纲\n\n## 评分标准\n期末考试占60%，实验占25%，平时作业占15%。\n\n## 考试要求\n期中考试为闭卷，复习范围为力学和热学。\n",
    ]
    for course, text in zip(courses, syllabi):
        path = ctx.data / course["syllabus"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def stamp(offset, hour, minute=0):
        return datetime.combine(monday + timedelta(days=offset), time(hour, minute), TZ).isoformat()

    tasks = [
        {"id": "se-lab-1", "title": "实验一 Agent实现与报告", "course": "软件工程", "kind": "assignment",
         "minutes": 120, "deadline": stamp(1, 23, 59), "priority": "high", "source": "data/tasks.json#/0",
         "requirements": "提交可运行仓库、核心prompt、工具Schema和实验报告；本示例时长估计120分钟。"},
        {"id": "se-exam-review", "title": "软件工程期中复习", "course": "软件工程", "kind": "exam",
         "minutes": 180, "deadline": stamp(2, 9), "priority": "high", "source": "data/tasks.json#/1",
         "requirements": "周三09:00考试，复习需求分析与软件测试；示例估计180分钟。"},
        {"id": "math-hw-3", "title": "高数第三章作业", "course": "高等数学", "kind": "assignment",
         "minutes": 90, "deadline": stamp(4, 23, 59), "priority": "medium", "source": "data/tasks.json#/2",
         "requirements": "完成第1至12题；示例估计90分钟。"},
        {"id": "physics-exam-review", "title": "大学物理期中复习", "course": "大学物理", "kind": "exam",
         "minutes": 120, "deadline": stamp(4, 9), "priority": "medium", "source": "data/tasks.json#/3",
         "requirements": "周五09:00考试，复习力学和热学；示例估计120分钟。"},
    ]
    write_json(ctx.data / "tasks.json", tasks)
    write_json(ctx.data / "preferences.json", {"daily_start": "18:00", "daily_end": "22:00",
               "max_session_minutes": 60, "break_minutes": 10})
    cal = Calendar()
    cal.add("prodid", "-//StudyPlannerCLI//Demo Timetable//CN")
    cal.add("version", "2.0")
    recurring = [(0, 9, 0, 11, 0, "软件工程课程"), (0, 19, 0, 20, 30, "实验课"),
                 (1, 10, 0, 12, 0, "高等数学课程"), (1, 19, 0, 20, 0, "社团活动"),
                 (3, 14, 0, 16, 0, "大学物理课程")]
    for index, (offset, h1, m1, h2, m2, title) in enumerate(recurring):
        event = Event()
        event.add("uid", f"demo-weekly-{index}@studyplanner.local")
        event.add("dtstart", datetime.fromisoformat(stamp(offset, h1, m1)).astimezone(timezone.utc))
        event.add("dtend", datetime.fromisoformat(stamp(offset, h2, m2)).astimezone(timezone.utc))
        event.add("summary", title)
        event.add("rrule", {"freq": "weekly", "count": 16})
        cal.add_component(event)
    for index, offset in enumerate((2, 4)):
        event = Event()
        event.add("uid", f"demo-exam-{index}@studyplanner.local")
        event.add("dtstart", datetime.fromisoformat(stamp(offset, 9)).astimezone(timezone.utc))
        event.add("dtend", datetime.fromisoformat(stamp(offset, 11)).astimezone(timezone.utc))
        event.add("summary", ["软件工程期中考试", "大学物理期中考试"][index])
        cal.add_component(event)
    write_ics(ctx.data / "calendars" / "timetable.ics", cal)
    return {"status": "created", "data_dir": str(ctx.data.resolve()), "next_monday": str(monday),
            "notice": "所有课程、评分、任务与日历均为虚构演示数据，请替换为真实资料。"}


def schedule_task(task: dict) -> dict:
    keys = ("id", "title", "course", "minutes", "deadline", "priority", "source")
    return {key: task.get(key) for key in keys}


def scheduler_args(start, end, tasks=None, daily_start=None, daily_end=None, action="plan"):
    return {"action": action, "start_date": str(start), "end_date": str(end), "tasks": tasks,
            "daily_start": daily_start, "daily_end": daily_end,
            "max_session_minutes": None, "break_minutes": None}


def run_demo(ctx: Context, emit=print) -> dict:
    # Demo always writes to a separate run, so it never commits into the user's data.
    isolated = Context(ctx.root / "runs" / f"demo-{uuid4().hex[:8]}", ctx.now)
    info = seed_data(isolated)
    monday = datetime.fromisoformat(info["next_monday"]).date()
    registry = ToolRegistry(isolated)
    transcript = []

    def say(text):
        emit(text)
        transcript.append({"kind": "text", "value": text})

    def call(name, arguments):
        result = registry.execute(name, arguments)
        transcript.append({"kind": "tool_call", "name": name, "arguments": arguments, "result": result})
        say(f"[工具] {name} -> {result['status']}")
        if result["status"] == "error":
            raise RuntimeError(result["message"])
        return result

    say("=== 离线脚本演示：不调用大模型；以下工具真实执行 ===")
    say(f"当前日期：{ctx.now.date()}；下周：{monday} 至 {monday + timedelta(days=6)}")
    say("用户：软件工程评分标准是什么？")
    query = {"query": "评分标准", "course": "软工", "start_date": None, "end_date": None, "top_k": 3}
    answer = call("course_rag_search", query)
    for item in answer["results"][:1]:
        say(f"助手：{item['text']} [来源：{item['source']}]")
    say("用户：帮我安排下周的复习。")
    exams = call("course_rag_search", {"query": "考试复习", "course": None,
                 "start_date": str(monday), "end_date": str(monday + timedelta(days=6)), "top_k": 5})
    tasks = [schedule_task(task) for task in exams["tasks"]]
    say("助手：查到软件工程、大学物理两门考试，示例估计共300分钟。")
    say("用户：先用周一、周二19:00到21:00。")
    first = call("calendar_scheduler", scheduler_args(monday, monday + timedelta(days=1), tasks, "19:00", "21:00"))
    say(f"助手：日历中有实验课和社团活动，可用{first['available_minutes']}分钟。")
    for task in first["unscheduled"]:
        say(f"  未排完：{task['title']}，剩余{task['remaining_minutes']}分钟。")
    say("助手：请调整课程优先级、可用时间或任务时长。")
    say("用户：先只安排软件工程，时间扩展到18:00至22:00。")
    second = call("calendar_scheduler", scheduler_args(monday, monday + timedelta(days=1),
                  [t for t in tasks if t["course"] == "软件工程"], "18:00", "22:00"))
    if second["status"] != "ok":
        raise RuntimeError("演示排期未完成")
    for block in second["blocks"]:
        say(f"  {block['start'][:16]} ~ {block['end'][11:16]} {block['title']}（{block['minutes']}分钟）")
    say(f"用户：/commit {second['plan_id']}")
    committed = Scheduler(isolated).commit(second["plan_id"])
    transcript.append({"kind": "commit", "result": committed})
    say(f"助手：已保存；ICS导出：{committed['export_file']}")
    output = {"status": "ok", "mode": "offline-scripted-demo", "root": str(isolated.root.resolve()),
              "first_plan": first["plan_id"], "confirmed_plan": second["plan_id"],
              "export_file": committed["export_file"]}
    write_json(isolated.root / "transcript.json", transcript)
    write_json(isolated.root / "summary.json", output)
    say(f"完整工具参数和结果：{isolated.root.resolve() / 'transcript.json'}")
    return output
