"""Local lexical retrieval: course aliases, Chinese bigrams and source citations."""
from __future__ import annotations

import math
import re
from collections import Counter

from .common import Context, parse_date, parse_datetime, read_json


def tokens(text: str) -> list[str]:
    text = text.lower()
    words = re.findall(r"[a-z0-9_]+", text)
    for span in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(span[i:i + 2] for i in range(len(span) - 1))
        if len(span) == 1:
            words.append(span)
    return words


class KnowledgeBase:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.courses = read_json(ctx.data / "courses.json")
        self.tasks = read_json(ctx.data / "tasks.json")

    def resolve(self, course: str | None) -> tuple[list[dict], bool]:
        if not course:
            return self.courses, False
        query = course.strip().casefold()
        exact = [c for c in self.courses if query in
                 [str(c["id"]).casefold(), c["name"].casefold(),
                  *[a.casefold() for a in c.get("aliases", [])]]]
        if exact:
            return exact, len(exact) > 1
        candidates = [c for c in self.courses if any(query in name.casefold()
                      for name in [c["id"], c["name"], *c.get("aliases", [])])]
        return candidates, len(candidates) > 1

    def search(self, query: str, course: str | None, start_date: str | None,
               end_date: str | None, top_k: int) -> dict:
        courses, ambiguous = self.resolve(course)
        candidates = [{"id": c["id"], "name": c["name"]} for c in courses]
        if ambiguous:
            return {"status": "needs_clarification", "message": "课程名称对应多门课，请用户选择。",
                    "candidates": candidates, "results": [], "tasks": []}
        if not courses:
            return {"status": "not_found", "message": "未找到该课程，请确认课程名。",
                    "candidates": [], "results": [], "tasks": []}
        start = parse_date(start_date) if start_date else None
        end = parse_date(end_date) if end_date else None
        if start and end and end < start:
            raise ValueError("结束日期不能早于开始日期")
        ids = {c["id"] for c in courses}
        exam_intent = any(w in query.lower() for w in ("考试", "期中", "期末", "exam", "复习"))
        assignment_intent = any(w in query.lower() for w in ("作业", "实验", "assignment", "ddl", "截止", "提交"))
        task_intent = exam_intent or assignment_intent or any(w in query for w in ("任务", "安排", "日程"))
        selected_tasks = []
        for task in self.tasks:
            if task["course"] not in ids:
                continue
            due = parse_datetime(task["deadline"]).date()
            if (start and due < start) or (end and due > end):
                continue
            if exam_intent and not assignment_intent and task["kind"] != "exam":
                continue
            if assignment_intent and not exam_intent and task["kind"] != "assignment":
                continue
            selected_tasks.append(dict(task))

        chunks = []
        for c in courses:
            path = (self.ctx.data / c["syllabus"]).resolve()
            if not path.is_relative_to(self.ctx.data.resolve()):
                raise ValueError("课程资料路径必须位于 data 目录")
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            heading = c["name"]
            for number, line in enumerate(lines, 1):
                if line.startswith("#"):
                    heading = line.lstrip("# ")
                elif line.strip():
                    chunks.append({"course": c["name"], "text": f"{heading}：{line.strip()}",
                                   "source": f"data/{c['syllabus']}:{number}"})
        # Only matching task records enter the date-filtered task context.
        for index, task in enumerate(self.tasks):
            if task in selected_tasks:
                chunks.append({"course": task["course"],
                               "text": f"{task['title']} 截止 {task['deadline']}。{task['requirements']}",
                               "source": f"data/tasks.json#/{index}"})
        q = set(tokens(query))
        counts = [Counter(tokens(c["text"])) for c in chunks]
        average = sum(sum(c.values()) for c in counts) / max(1, len(counts))
        for chunk, frequencies in zip(chunks, counts):
            score = 0.0
            length = sum(frequencies.values())
            for token in q:
                count = frequencies[token]
                if not count:
                    continue
                df = sum(token in item for item in counts)
                idf = math.log(1 + (len(counts) - df + .5) / (df + .5))
                score += idf * count * 2.2 / (count + 1.2 * (.25 + .75 * length / max(1, average)))
            chunk["score"] = round(score, 4)
        results = sorted((c for c in chunks if c["score"] > 0),
                         key=lambda c: c["score"], reverse=True)[:top_k]
        # A grading question should not silently inject unrelated deadlines.
        returned_tasks = selected_tasks if task_intent or start or end else []
        return {"status": "ok" if results or returned_tasks else "not_found",
                "courses": candidates, "results": results,
                "tasks": returned_tasks,
                "data_mode": "demo" if self.ctx.is_demo else "user",
                "note": ("当前所有课程规则、任务及截止日期均为虚构演示数据，不是真实课程要求。" if self.ctx.is_demo else "")
                        + "日期范围按截止日期/考试日期筛选。分钟数为估计，可由用户调整。资料缺失时请说明，禁止编造。"}
