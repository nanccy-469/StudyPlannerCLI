from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, time
from pathlib import Path
from urllib.parse import urlsplit

from .agent import Agent, ResponsesClient
from .calendar_tools import Scheduler
from .common import Context, ROOT, TZ, load_env, parse_date, read_json
from .demo import run_demo, schedule_task, scheduler_args, seed_data
from .knowledge import KnowledgeBase
from .schemas import TOOLS
from .tools import ToolRegistry


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def show(result: dict, as_json=False):
    if as_json:
        dump(result)
        return
    print(f"状态：{result['status']}")
    if result.get("message"):
        print(result["message"])
    if result.get("notice"):
        print(result["notice"])
    for item in result.get("results", []):
        print(f"  {item['text']}\n  来源：{item['source']}")
    for task in result.get("tasks", []):
        print(f"  [{task['id']}] {task['title']} | 截止 {task['deadline']} | 估计{task['minutes']}分钟")
    for item in result.get("candidates", []):
        print(f"  候选课程：{item['name']} ({item['id']})")
    if "available_minutes" in result:
        print(f"可用时间：{result['available_minutes']}分钟（学习块之间另需休息）")
    for event in result.get("busy_events", []):
        print(f"  已有日程：{event['start'][:16]} ~ {event['end'][:16]} {event['title']}")
    if "blocks" not in result:
        for slot in result.get("free_windows", []):
            print(f"  空闲：{slot['start'][:16]} ~ {slot['end'][11:16]}（{slot['minutes']}分钟）")
    for block in result.get("blocks", []):
        print(f"  安排：{block['start'][:16]} ~ {block['end'][11:16]} {block['title']}（{block['minutes']}分钟）")
    for task in result.get("unscheduled", []):
        print(f"  未排完：{task['title']}，剩余{task['remaining_minutes']}分钟；{task['reason']}")
    for name in ("plan_id", "preview_file", "export_file", "data_dir", "next_monday"):
        if result.get(name):
            print(f"{name}：{result[name]}")
    if result.get("already_scheduled"):
        print("已安排，跳过：" + ", ".join(result["already_scheduled"]))


def parser():
    p = argparse.ArgumentParser(description="个人学业/日程管家 CLI（两个Function Calling工具）")
    p.add_argument("--workspace", type=Path, default=ROOT, help="课程数据、状态和导出所在目录")
    p.add_argument("--today", help="演示用固定日期YYYY-MM-DD，时间固定为08:00；真实使用请省略")
    p.add_argument("--trace", action="store_true", help="显示真实模型的工具名、参数与工具结果")
    p.add_argument("--json", action="store_true", help="直接工具命令输出JSON")
    p.add_argument("--model", help="真实API模式使用的模型ID；也可通过OPENAI_MODEL设置")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("demo", help="无需密钥的固定脚本演示，工具真实执行")
    sub.add_parser("init-demo", help="在空数据目录生成示例课程、任务和课表")
    sub.add_parser("tools", help="输出两个工具的JSON Schema")
    sub.add_parser("doctor", help="检查配置（不打印密钥）")
    sub.add_parser("chat", help="真实大模型多轮对话，需要API密钥")
    ask = sub.add_parser("ask", help="向真实大模型提问一次")
    ask.add_argument("text")
    search = sub.add_parser("search", help="直接调用课程查询工具，无需密钥")
    search.add_argument("query")
    search.add_argument("--course")
    search.add_argument("--start")
    search.add_argument("--end")
    search.add_argument("--top-k", type=int, default=5)
    inspect = sub.add_parser("calendar", help="查看忙闲时间，无需密钥")
    plan = sub.add_parser("plan", help="直接生成排期预览，无需密钥")
    for command in (inspect, plan):
        command.add_argument("--start", required=True)
        command.add_argument("--end", required=True)
        command.add_argument("--daily-start")
        command.add_argument("--daily-end")
        command.add_argument("--session-minutes", type=int)
        command.add_argument("--break-minutes", type=int)
    group = plan.add_mutually_exclusive_group(required=True)
    group.add_argument("--task-ids", nargs="+", help="data/tasks.json中的任务ID")
    group.add_argument("--tasks-file", type=Path, help="包含任务数组的JSON文件")
    commit = sub.add_parser("commit", help="保存一个完整排期，导出confirmed ICS")
    commit.add_argument("plan_id")
    return p


def chat(agent: Agent):
    print("学业管家 · 真实API模式")
    provider = "智谱 BigModel" if getattr(agent.client, "is_bigmodel", False) else "Responses API"
    print(f"模型：{agent.client.model}；接口：{provider}")
    if agent.ctx.is_demo:
        print("当前使用示例课程资料；日期和规则用于演示，请替换为真实资料后再作为个人日程使用。")
    print("工具详情：" + ("开启（/trace off可关闭）" if agent.trace else "关闭（/trace on可开启）"))
    print("输入 /help 查看命令；输入 /exit 退出。课程、时间与偏好会在本次对话中保留。")
    print(f"本地运行记录：{agent.log_path}")
    while True:
        try:
            text = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return
        if not text:
            continue
        if text in ("/exit", "/quit"):
            return
        try:
            if text == "/help":
                print("/tools 工具Schema；/trace on或off 切换工具详情；/reset 清空会话上下文；/commit PLAN_ID 保存预览；/exit 退出")
            elif text in ("/trace on", "/trace off"):
                agent.trace = text.endswith(" on")
                print("工具详情已" + ("开启。" if agent.trace else "关闭。"))
            elif text == "/tools":
                dump(TOOLS)
            elif text == "/reset":
                agent.history.clear()
                agent.log("reset", {})
                print("会话上下文已清空，保存的日历仍可用于后续冲突检查。")
            elif text.startswith("/commit "):
                result = Scheduler(agent.ctx).commit(text.split(maxsplit=1)[1].strip())
                agent.record_commit(result)
                show(result)
            elif text.startswith("/"):
                print("未知命令；输入 /help 查看。")
            else:
                print("助手 > " + agent.turn(text))
        except KeyboardInterrupt:
            print("\n本轮已取消，可以继续输入问题或用 /exit 退出。")
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"操作未完成：{exc}")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        root = args.workspace.expanduser().resolve()
        fixed = datetime.combine(parse_date(args.today), time(8), TZ) if args.today else None
        ctx = Context(root, fixed)
        load_env(ROOT / ".env")
        command = args.command or "chat"
        if command == "tools":
            dump(TOOLS)
            return 0
        if command == "doctor":
            result = {"status": "ok", "python": sys.executable, "workspace": str(root),
                      "timezone": "Asia/Shanghai", "now": ctx.now.isoformat(),
                      "api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
                      "model_configured": bool(args.model or os.getenv("OPENAI_MODEL")),
                      "model": args.model or os.getenv("OPENAI_MODEL", ""),
                      "api_host": urlsplit(os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).hostname,
                      "data_ready": (ctx.data / "courses.json").exists(),
                      "api_connected": "未检测；该命令不发送API请求"}
            dump(result)
            return 0
        if command == "init-demo":
            show(seed_data(ctx), args.json)
            return 0
        if command == "demo":
            result = run_demo(ctx, emit=(lambda _: None) if args.json else print)
            if args.json:
                dump(result)
            return 0
        if not (ctx.data / "courses.json").exists():
            raise ValueError("缺少课程数据；请先运行 python main.py init-demo，或在data中放入真实资料")
        registry = ToolRegistry(ctx)
        if command == "search":
            result = registry.execute("course_rag_search", {"query": args.query, "course": args.course,
                       "start_date": args.start, "end_date": args.end, "top_k": args.top_k})
        elif command in ("calendar", "plan"):
            tasks = None
            if command == "plan":
                if args.tasks_file:
                    tasks = read_json(args.tasks_file)
                else:
                    known = {task["id"]: task for task in KnowledgeBase(ctx).tasks}
                    missing = set(args.task_ids) - set(known)
                    if missing:
                        raise ValueError("未知任务ID：" + ", ".join(sorted(missing)))
                    tasks = [schedule_task(known[key]) for key in args.task_ids]
            params = scheduler_args(args.start, args.end, tasks, args.daily_start,
                                    args.daily_end, "inspect" if command == "calendar" else "plan")
            params.update(max_session_minutes=args.session_minutes, break_minutes=args.break_minutes)
            result = registry.execute("calendar_scheduler", params)
        elif command == "commit":
            result = Scheduler(ctx).commit(args.plan_id)
        elif command in ("chat", "ask"):
            agent = Agent(ctx, ResponsesClient.from_env(args.model), args.trace)
            if command == "ask":
                print(agent.turn(args.text))
            else:
                chat(agent)
            return 0
        else:
            raise ValueError("未知命令")
        show(result, args.json)
        return 1 if result["status"] == "error" else 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        result = {"status": "error", "message": str(exc)}
        show(result, args.json)
        return 1
