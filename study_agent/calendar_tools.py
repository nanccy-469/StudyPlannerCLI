from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from icalendar import Calendar, Event
import recurring_ical_events

from .common import Context, TZ, date_range, parse_date, parse_datetime, parse_time, read_json, write_json


def at_local(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)
    return datetime.combine(value, time.min, TZ)


def calendar_files(ctx: Context) -> list[Path]:
    paths = sorted((ctx.data / "calendars").glob("*.ics"))
    committed = ctx.state / "committed.ics"
    return paths + ([committed] if committed.exists() else [])


def snapshot(ctx: Context) -> str:
    digest = hashlib.sha256()
    for path in calendar_files(ctx):
        digest.update(str(path.relative_to(ctx.root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def read_events(ctx: Context, start: datetime, end: datetime) -> list[dict]:
    events = []
    for path in calendar_files(ctx):
        try:
            cal = Calendar.from_ical(path.read_bytes())
            # Fail closed for malformed events; do not silently schedule over them.
            for entry in cal.walk("VEVENT"):
                if "DTSTART" not in entry:
                    raise ValueError("事件缺少DTSTART")
                for field in ("DTSTART", "DTEND"):
                    prop = entry.get(field)
                    if prop is not None and prop.params.get("TZID") and isinstance(prop.dt, datetime) and prop.dt.tzinfo is None:
                        raise ValueError(f"无法识别时区 {prop.params['TZID']}")
                if entry.errors:
                    raise ValueError(str(entry.errors))
            instances = recurring_ical_events.of(cal).between(start, end)
            if len(instances) > 10000:
                raise ValueError("时间范围内日历事件过多")
            for item in instances:
                if str(item.get("STATUS", "")).upper() == "CANCELLED" or str(item.get("TRANSP", "")).upper() == "TRANSPARENT":
                    continue
                raw_start = item.decoded("DTSTART")
                begin = at_local(raw_start)
                if "DTEND" in item:
                    finish = at_local(item.decoded("DTEND"))
                elif "DURATION" in item:
                    finish = begin + item.decoded("DURATION")
                else:
                    finish = begin + (timedelta(days=1) if not isinstance(raw_start, datetime) else timedelta())
                if finish < begin:
                    raise ValueError("事件结束时间早于开始时间")
                if begin < end and finish > start:
                    events.append({"title": str(item.get("SUMMARY", "未命名事件")),
                                   "start": begin.isoformat(), "end": finish.isoformat(),
                                   "source": str(path.relative_to(ctx.root)),
                                   "task_id": str(item.get("X-STUDY-TASK-ID", "")),
                                   "plan_id": str(item.get("X-STUDY-PLAN-ID", ""))})
        except Exception as exc:
            raise ValueError(f"无法读取日历 {path.name}：{exc}") from exc
    return sorted(events, key=lambda event: event["start"])


def subtract_busy(start: datetime, end: datetime, busy: list[tuple]) -> list[tuple]:
    free, cursor = [], start
    for begin, finish in sorted(busy):
        if finish <= cursor or begin >= end:
            continue
        if begin > cursor:
            free.append((cursor, min(begin, end)))
        cursor = max(cursor, finish)
        if cursor >= end:
            break
    if cursor < end:
        free.append((cursor, end))
    return free


def minute_ceil(moment: datetime) -> datetime:
    if moment.second or moment.microsecond:
        moment += timedelta(minutes=1)
    return moment.replace(second=0, microsecond=0)


def make_calendar(blocks: list[dict], plan_id: str, confirmed: bool) -> Calendar:
    cal = Calendar()
    cal.add("prodid", "-//StudyPlannerCLI//Academic Planner//CN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "学业管家学习安排")
    for index, block in enumerate(blocks):
        event = Event()
        event.add("uid", f"{plan_id}-{index}@studyplanner.local")
        event.add("dtstamp", datetime.now(timezone.utc))
        # UTC avoids nonportable TZID definitions when imported into other apps.
        event.add("dtstart", parse_datetime(block["start"]).astimezone(timezone.utc))
        event.add("dtend", parse_datetime(block["end"]).astimezone(timezone.utc))
        event.add("summary", f"{block['course']} · {block['title']}")
        event.add("description", f"任务ID: {block['task_id']}\n来源: {block.get('source') or '用户提供'}")
        event.add("status", "CONFIRMED" if confirmed else "TENTATIVE")
        event.add("sequence", 1 if confirmed else 0)
        event.add("x-study-task-id", block["task_id"])
        event.add("x-study-plan-id", plan_id)
        cal.add_component(event)
    return cal


def write_ics(path: Path, cal: Calendar):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".ics.tmp")
    temp.write_bytes(cal.to_ical())
    os.replace(temp, path)


class Scheduler:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def run(self, action, start_date, end_date, tasks, daily_start,
            daily_end, max_session_minutes, break_minutes) -> dict:
        start, end = parse_date(start_date), parse_date(end_date)
        days = list(date_range(start, end))
        prefs = self.ctx.preferences
        opening = parse_time(daily_start if daily_start is not None else prefs["daily_start"])
        closing = parse_time(daily_end if daily_end is not None else prefs["daily_end"])
        session = max_session_minutes if max_session_minutes is not None else prefs["max_session_minutes"]
        rest = break_minutes if break_minutes is not None else prefs["break_minutes"]
        if opening >= closing:
            raise ValueError("每日开始时间必须早于结束时间；跨午夜请分成两个日期")
        if not isinstance(session, int) or not 15 <= session <= 240 or not isinstance(rest, int) or not 0 <= rest <= 120:
            raise ValueError("每段学习时长应为15到240分钟，休息时长应为0到120分钟")
        lower = datetime.combine(start, time.min, TZ)
        upper = datetime.combine(end + timedelta(days=1), time.min, TZ)
        digest = snapshot(self.ctx)
        # Expand a margin to enforce breaks before/after nearby committed sessions.
        events = read_events(self.ctx, lower - timedelta(days=1), upper + timedelta(days=1))
        busy = []
        for event in events:
            margin = timedelta(minutes=rest) if event["task_id"] else timedelta()
            busy.append((parse_datetime(event["start"]) - margin,
                         parse_datetime(event["end"]) + margin))

        def windows(deadline=upper):
            free = []
            for day in days:
                begin = max(datetime.combine(day, opening, TZ), minute_ceil(self.ctx.now))
                finish = min(datetime.combine(day, closing, TZ), deadline)
                if begin < finish:
                    free.extend(subtract_busy(begin, finish, busy))
            return free

        initial = windows()
        result = {"status": "ok", "timezone": "Asia/Shanghai", "start_date": start_date,
                  "end_date": end_date, "busy_events": [e for e in events if parse_datetime(e["start"]) < upper and parse_datetime(e["end"]) > lower],
                  "free_windows": [{"start": a.isoformat(), "end": b.isoformat(),
                                    "minutes": int((b - a).total_seconds() // 60)} for a, b in initial],
                  "available_minutes": sum(int((b - a).total_seconds() // 60) for a, b in initial)}
        if action == "inspect":
            return result
        if not tasks:
            raise ValueError("生成排期需要非空tasks；请先查询课程任务或补充任务和时长")
        if len({t["id"] for t in tasks}) != len(tasks):
            raise ValueError("同一个排期中任务ID不能重复")
        # Read all committed task IDs, even if they lie outside this date range.
        committed_path = self.ctx.state / "committed.ics"
        existing_ids = set()
        if committed_path.exists():
            existing_ids = {str(e.get("X-STUDY-TASK-ID", "")) for e in Calendar.from_ical(committed_path.read_bytes()).walk("VEVENT")}
        already = [t["id"] for t in tasks if t["id"] in existing_ids]
        active = [t for t in tasks if t["id"] not in existing_ids]
        if not active:
            result.update({"status": "already_scheduled", "blocks": [], "unscheduled": [],
                           "already_scheduled": already,
                           "message": "这些任务已保存到本地日历，无需再次安排。"})
            return result
        priority = {"high": 0, "medium": 1, "low": 2}
        active.sort(key=lambda t: (priority[t["priority"]], parse_datetime(t["deadline"]), t["id"]))
        blocks, unscheduled = [], []
        for task in active:
            remaining = task["minutes"]
            deadline = parse_datetime(task["deadline"])
            while remaining > 0:
                options = [(a, b) for a, b in windows(deadline)
                           if int((b - a).total_seconds() // 60) >= min(15, remaining)]
                if not options:
                    break
                begin, finish = options[0]
                minutes = min(session, remaining, int((finish - begin).total_seconds() // 60))
                finish = begin + timedelta(minutes=minutes)
                blocks.append({"task_id": task["id"], "title": task["title"], "course": task["course"],
                               "start": begin.isoformat(), "end": finish.isoformat(),
                               "minutes": minutes, "deadline": task["deadline"], "source": task.get("source")})
                busy.append((begin - timedelta(minutes=rest), finish + timedelta(minutes=rest)))
                remaining -= minutes
            if remaining:
                unscheduled.append({"task_id": task["id"], "title": task["title"],
                                    "remaining_minutes": remaining, "deadline": task["deadline"],
                                    "reason": "当前优先级和排期策略下，截止前无法排完（已考虑休息）"})
        plan_id = uuid4().hex[:12]
        blocks.sort(key=lambda block: block["start"])
        result.update({"status": "needs_clarification" if unscheduled else "ok",
                       "plan_id": plan_id, "blocks": blocks, "unscheduled": unscheduled,
                       "already_scheduled": already, "calendar_snapshot": digest,
                       "created_at": self.ctx.now.isoformat(), "break_minutes": rest,
                       "message": "存在未排完任务，请用户调整优先级、时长或可用时间，再重新plan。" if unscheduled
                       else f"预览完成。输入 /commit {plan_id} 保存；原始日历不会被修改。"})
        export = self.ctx.root / "exports" / f"preview-{plan_id}.ics"
        write_ics(export, make_calendar(blocks, plan_id, False))
        result["preview_file"] = str(export.resolve())
        write_json(self.ctx.state / "plans" / f"{plan_id}.json", result)
        return result

    def commit(self, plan_id: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{12}", plan_id):
            raise ValueError("无效plan_id，应为12位十六进制字符")
        self.ctx.state.mkdir(parents=True, exist_ok=True)
        with commit_lock(self.ctx.state / ".commit.lock"):
            plan_path = self.ctx.state / "plans" / f"{plan_id}.json"
            if not plan_path.exists():
                raise ValueError("找不到排期，请先生成预览")
            plan = read_json(plan_path)
            path = self.ctx.state / "committed.ics"
            combined = Calendar.from_ical(path.read_bytes()) if path.exists() else make_calendar([], "", True)
            export = self.ctx.root / "exports" / f"confirmed-{plan_id}.ics"
            entries = [e for e in combined.walk("VEVENT") if str(e.get("X-STUDY-PLAN-ID", "")) == plan_id]
            if entries:
                # Retry repairs an interrupted export without duplicating saved events.
                cal = make_calendar([], "", True)
                for entry in entries:
                    cal.add_component(entry)
                write_ics(export, cal)
                return {"status": "already_committed", "plan_id": plan_id, "export_file": str(export.resolve())}
            if plan["unscheduled"]:
                raise ValueError("预览中仍有未排完任务，请调整任务或可用时间后重新生成")
            if not plan["blocks"]:
                raise ValueError("预览没有新学习块，无需保存")
            if snapshot(self.ctx) != plan["calendar_snapshot"]:
                raise ValueError("日历已变化，请重新生成排期，避免使用过期预览")
            # Also verify the preview against deadlines and the actual current time.
            for block in plan["blocks"]:
                begin, finish = parse_datetime(block["start"]), parse_datetime(block["end"])
                if begin < self.ctx.now or finish > parse_datetime(block["deadline"]) or finish <= begin:
                    raise ValueError("预览已过期或超过任务截止时间，请重新生成")
            new = make_calendar(plan["blocks"], plan_id, True)
            for entry in new.walk("VEVENT"):
                combined.add_component(entry)
            # committed.ics is the sole authoritative commit record.
            write_ics(path, combined)
            write_ics(export, new)
            return {"status": "committed", "plan_id": plan_id, "blocks": plan["blocks"],
                    "export_file": str(export.resolve()),
                    "message": "已保存到本地日历；新的排期会避开这些学习块。可将confirmed文件导入日历软件。"}


@contextmanager
def commit_lock(path: Path):
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("另一个进程正在保存排期；若上次异常退出，请确认无运行进程后移除state/.commit.lock") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)
