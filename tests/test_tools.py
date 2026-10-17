import json
import copy
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from icalendar import Calendar, Event

from study_agent.calendar_tools import Scheduler, read_events, write_ics
from study_agent.common import Context, ROOT, TZ, parse_datetime, write_json
from study_agent.demo import run_demo, scheduler_args, seed_data
from study_agent.schemas import TOOLS
from study_agent.tools import ToolRegistry


class ToolsTest(unittest.TestCase):
    def setUp(self):
        parent = (ROOT / "runs" / "_tests").resolve()
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(parent))
        self.addCleanup(self.temp.cleanup)
        self.ctx = Context(Path(self.temp.name), datetime(2026, 10, 16, 8, tzinfo=TZ))
        seed_data(self.ctx)
        self.registry = ToolRegistry(self.ctx)

    def search(self, query, course=None, start=None, end=None):
        return self.registry.execute("course_rag_search", {
            "query": query, "course": course, "start_date": start, "end_date": end, "top_k": 5})

    def plan(self, tasks, **overrides):
        arguments = scheduler_args("2026-10-19", "2026-10-20", tasks, "18:00", "22:00")
        arguments.update(overrides)
        return self.registry.execute("calendar_scheduler", arguments)

    def task(self, **changes):
        value = {"id": "custom", "title": "复习", "course": "软件工程", "minutes": 180,
                 "deadline": "2026-10-21T09:00:00+08:00", "priority": "high", "source": "用户输入"}
        value.update(changes)
        return value

    def add_calendar(self, events):
        cal = Calendar()
        cal.add("version", "2.0")
        cal.add("prodid", "-//Tests//CN")
        for event in events:
            cal.add_component(event)
        write_ics(self.ctx.data / "calendars" / "additional.ics", cal)

    def event(self, start, end, **properties):
        event = Event()
        event.add("uid", "test-event")
        event.add("summary", "测试事件")
        event.add("dtstart", start)
        event.add("dtend", end)
        for key, value in properties.items():
            event.add(key, value)
        return event

    def test_alias_and_source(self):
        result = self.search("评分标准", "软工")
        self.assertEqual(result["status"], "ok")
        self.assertIn("50%", result["results"][0]["text"])
        source, line = result["results"][0]["source"].rsplit(":", 1)
        self.assertIn("50%", (self.ctx.root / source).read_text(encoding="utf-8").splitlines()[int(line) - 1])
        self.assertEqual(result["tasks"], [])

    def test_date_filtered_exams_and_unknown_facts(self):
        result = self.search("考试", start="2026-10-19", end="2026-10-25")
        self.assertEqual(len(result["tasks"]), 2)
        self.assertTrue(all(t["kind"] == "exam" for t in result["tasks"]))
        self.assertEqual(self.search("考试", start="2026-11-01", end="2026-11-02")["tasks"], [])
        self.assertEqual(self.search("奖学金资格", "软件工程")["status"], "not_found")
        self.assertEqual(self.search("评分", "不存在的课程")["status"], "not_found")

    def test_course_ambiguity(self):
        courses = json.loads((self.ctx.data / "courses.json").read_text(encoding="utf-8"))
        courses.append({"id": "离散数学", "name": "离散数学", "aliases": ["数学"], "syllabus": "courses/mathematics.md"})
        write_json(self.ctx.data / "courses.json", courses)
        result = self.search("作业", "数学")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(len(result["candidates"]), 2)

    def test_schema_validation_and_tool_whitelist(self):
        self.assertEqual(self.registry.execute("shell", {})["code"], "unknown_tool")
        self.assertEqual(self.registry.execute("course_rag_search", "{broken")["status"], "error")
        self.assertEqual(self.registry.execute("calendar_scheduler", {})["code"], "invalid_arguments")
        self.assertEqual(self.plan([self.task(minutes=-2)])["code"], "invalid_arguments")
        self.assertEqual(self.plan([self.task()], extra="bad")["code"], "invalid_arguments")

    def test_reported_string_null_query_returns_deadline_without_guessing_dates(self):
        args = {"course": "软件工程", "end_date": "null", "query": "软件工程 实验一 提交 截止时间 DDL",
                "start_date": "null", "top_k": 3}
        original = copy.deepcopy(args)
        result = self.registry.execute("course_rag_search", json.dumps(args, ensure_ascii=False))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["tasks"][0]["deadline"], "2026-10-20T23:59:00+08:00")
        self.assertEqual(result["data_mode"], "demo")
        self.assertIn("虚构演示", result["note"])
        self.assertEqual(args, original)

    def test_omitted_nullable_fields_have_defaults_but_required_fields_stay_required(self):
        result = self.registry.execute("course_rag_search", {"query": "考试", "top_k": 3})
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(len(result["tasks"]), 2)
        self.assertEqual(self.registry.execute("course_rag_search", {"top_k": 3})["code"], "invalid_arguments")

    def test_null_defaults_are_recursive_and_do_not_replace_required_text(self):
        task = self.task(title="null", source="null", minutes=30)
        result = self.plan([task], daily_start="null", daily_end="", max_session_minutes="null", break_minutes="None")
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["blocks"][0]["title"], "null")
        self.assertIsNone(result["blocks"][0]["source"])
        self.assertEqual(task["source"], "null")

    def test_invalid_real_dates_and_required_null_return_argument_error(self):
        query = {"query": "实验一截止", "course": "软件工程", "start_date": "2026-02-30", "end_date": None, "top_k": 3}
        result = self.registry.execute("course_rag_search", query)
        self.assertEqual(result["code"], "invalid_arguments")
        self.assertIn("start_date", result["message"])
        self.assertIn("不要编造日期", result["hint"])
        self.assertEqual(self.plan([self.task()], start_date="null")["code"], "invalid_arguments")

    def test_strict_schema_objects(self):
        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node["additionalProperties"])
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                for child in node.values():
                    check(child)
            elif isinstance(node, list):
                for child in node:
                    check(child)
        self.assertEqual(len(TOOLS), 2)
        for tool in TOOLS:
            check(tool["parameters"])

    def test_no_conflicts_deadline_breaks_and_roundtrip(self):
        result = self.plan([self.task()])
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(sum(b["minutes"] for b in result["blocks"]), 180)
        busy = read_events(self.ctx, datetime(2026, 10, 19, tzinfo=TZ), datetime(2026, 10, 21, tzinfo=TZ))
        previous_end = None
        for block in result["blocks"]:
            start, end = parse_datetime(block["start"]), parse_datetime(block["end"])
            self.assertLessEqual(end, parse_datetime(block["deadline"]))
            self.assertLessEqual(block["minutes"], 60)
            if previous_end:
                self.assertGreaterEqual((start - previous_end).total_seconds(), 600)
            previous_end = end
            for event in busy:
                self.assertFalse(start < parse_datetime(event["end"]) and end > parse_datetime(event["start"]))
        preview = Calendar.from_ical(Path(result["preview_file"]).read_bytes())
        self.assertEqual(len(preview.walk("VEVENT")), len(result["blocks"]))
        self.assertTrue(all(e["STATUS"] == "TENTATIVE" for e in preview.walk("VEVENT")))
        first = Scheduler(self.ctx).commit(result["plan_id"])
        self.assertEqual(first["status"], "committed")
        second = Scheduler(self.ctx).commit(result["plan_id"])
        self.assertEqual(second["status"], "already_committed")
        confirmed = Calendar.from_ical(Path(first["export_file"]).read_bytes())
        self.assertEqual(len(confirmed.walk("VEVENT")), len(result["blocks"]))
        self.assertTrue(all(e["STATUS"] == "CONFIRMED" for e in confirmed.walk("VEVENT")))
        self.assertEqual(self.plan([self.task()])["already_scheduled"], ["custom"])

    def test_insufficient_capacity_and_no_partial_commit(self):
        result = self.plan([self.task(minutes=300)], daily_start="19:00", daily_end="21:00")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["available_minutes"], 90)
        scheduled = sum(b["minutes"] for b in result["blocks"])
        self.assertEqual(scheduled + result["unscheduled"][0]["remaining_minutes"], 300)
        with self.assertRaisesRegex(ValueError, "未排完"):
            Scheduler(self.ctx).commit(result["plan_id"])
        self.assertFalse((self.ctx.state / "committed.ics").exists())

    def test_all_day_event_exclusive_end_and_transparency(self):
        all_day = self.event(date(2026, 10, 19), date(2026, 10, 20))
        transparent = self.event(datetime(2026, 10, 20, 18, tzinfo=TZ), datetime(2026, 10, 20, 22, tzinfo=TZ), transp="TRANSPARENT")
        transparent["UID"] = "transparent"
        self.add_calendar([all_day, transparent])
        result = self.plan([self.task(minutes=90)])
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(all(b["start"].startswith("2026-10-20") for b in result["blocks"]))

    def test_rrule_exdate_and_override(self):
        event = self.event(datetime(2026, 10, 19, 18, tzinfo=TZ), datetime(2026, 10, 19, 19, tzinfo=TZ), rrule={"freq": "daily", "count": 3})
        event.add("exdate", datetime(2026, 10, 20, 18, tzinfo=TZ))
        override = self.event(datetime(2026, 10, 21, 20, tzinfo=TZ), datetime(2026, 10, 21, 21, tzinfo=TZ))
        override.add("recurrence-id", datetime(2026, 10, 21, 18, tzinfo=TZ))
        self.add_calendar([event, override])
        events = read_events(self.ctx, datetime(2026, 10, 19, tzinfo=TZ), datetime(2026, 10, 22, tzinfo=TZ))
        own = [e for e in events if e["source"].endswith("additional.ics")]
        self.assertEqual(len(own), 2, own)
        self.assertEqual(own[1]["start"], "2026-10-21T20:00:00+08:00")

    def test_existing_weekly_events_expand(self):
        events = read_events(self.ctx, datetime(2026, 10, 26, tzinfo=TZ), datetime(2026, 10, 28, tzinfo=TZ))
        self.assertTrue(any(e["title"] == "实验课" and e["start"].startswith("2026-10-26") for e in events))

    def test_past_and_deadline_clipping(self):
        self.ctx.fixed_now = datetime(2026, 10, 19, 18, 30, 25, tzinfo=TZ)
        result = self.plan([self.task(minutes=60, deadline="2026-10-19T19:00:00+08:00")])
        self.assertEqual(result["status"], "needs_clarification")
        self.assertTrue(all(parse_datetime(b["start"]) >= self.ctx.now for b in result["blocks"]))
        self.assertTrue(all(parse_datetime(b["end"]) <= datetime(2026, 10, 19, 19, tzinfo=TZ) for b in result["blocks"]))
        self.assertEqual(result["blocks"][0]["minutes"], 29)

    def test_stale_calendar_and_expired_plan(self):
        result = self.plan([self.task(minutes=30)])
        self.add_calendar([self.event(date(2026, 10, 19), date(2026, 10, 20))])
        with self.assertRaisesRegex(ValueError, "日历已变化"):
            Scheduler(self.ctx).commit(result["plan_id"])
        fresh = self.plan([self.task(minutes=30)])
        self.ctx.fixed_now = datetime(2026, 10, 21, tzinfo=TZ)
        with self.assertRaisesRegex(ValueError, "过期"):
            Scheduler(self.ctx).commit(fresh["plan_id"])

    def test_invalid_dates_tasks_and_preview_id(self):
        self.assertEqual(self.plan([self.task()], end_date="2026-10-01")["status"], "error")
        self.assertEqual(self.plan([self.task()], end_date="2027-01-01")["status"], "error")
        self.assertEqual(self.plan([self.task(), self.task()])["status"], "error")
        self.assertEqual(self.plan([self.task(deadline="2026-10-20T10:00:00")])["status"], "error")
        with self.assertRaisesRegex(ValueError, "无效plan_id"):
            Scheduler(self.ctx).commit("../../data/tasks")

    def test_malformed_calendar_fails_closed(self):
        event = Event()
        event.add("uid", "broken")
        self.add_calendar([event])
        result = self.plan([self.task()])
        self.assertEqual(result["status"], "error")
        self.assertIn("缺少DTSTART", result["message"])

    def test_priority_allocation(self):
        result = self.plan([self.task(id="low", minutes=60, priority="low"),
                            self.task(id="high", minutes=60)], daily_start="20:30", daily_end="21:00",
                           end_date="2026-10-19", break_minutes=0)
        self.assertEqual(result["blocks"][0]["task_id"], "high")

    def test_full_offline_demo_is_isolated(self):
        result = run_demo(self.ctx, emit=lambda _: None)
        self.assertEqual(result["mode"], "offline-scripted-demo")
        self.assertTrue(Path(result["export_file"]).exists())
        self.assertFalse((self.ctx.state / "committed.ics").exists())


if __name__ == "__main__":
    unittest.main()
