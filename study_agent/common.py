from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("日期应为 YYYY-MM-DD") from exc


def parse_time(value: str) -> time:
    try:
        result = time.fromisoformat(value)
        if result.tzinfo or result.second or result.microsecond:
            raise ValueError()
        return result
    except (TypeError, ValueError) as exc:
        raise ValueError("每日时间应为 HH:MM（本地时间）") from exc


def parse_datetime(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(TZ)
    except (TypeError, ValueError) as exc:
        raise ValueError("时间应包含时区，例如 2026-10-20T23:59:00+08:00") from exc


def date_range(start: date, end: date):
    if end < start or (end - start).days > 30:
        raise ValueError("结束日期不能早于开始日期，一次最多安排31天")
    for index in range((end - start).days + 1):
        yield start + timedelta(days=index)


@dataclass
class Context:
    root: Path = ROOT
    fixed_now: datetime | None = None

    @property
    def now(self) -> datetime:
        return self.fixed_now or datetime.now(TZ)

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def is_demo(self) -> bool:
        path = self.data / "metadata.json"
        return read_json(path).get("is_demo") is True if path.exists() else False

    @property
    def preferences(self) -> dict:
        path = self.data / "preferences.json"
        return read_json(path) if path.exists() else {
            "daily_start": "18:00", "daily_end": "22:00",
            "max_session_minutes": 60, "break_minutes": 10,
        }


def load_env(path: Path) -> None:
    """Read simple KEY=value pairs without overriding the current environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)
