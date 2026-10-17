from __future__ import annotations

import json

from jsonschema import Draft202012Validator, FormatChecker

from .calendar_tools import Scheduler
from .common import Context
from .knowledge import KnowledgeBase
from .schemas import SCHEMAS


def normalize_arguments(value, schema):
    """Coerce absent values only where the schema explicitly allows JSON null."""
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    if "null" in kinds and isinstance(value, str) and value.strip().casefold() in ("null", "none", ""):
        return None
    if isinstance(value, dict) and "object" in kinds:
        properties = schema.get("properties", {})
        result = {key: normalize_arguments(item, properties.get(key, {})) for key, item in value.items()}
        for key in schema.get("required", []):
            child = properties.get(key, {})
            if key not in result and "null" in child.get("type", []):
                result[key] = None
        return result
    if isinstance(value, list) and "array" in kinds:
        return [normalize_arguments(item, schema.get("items", {})) for item in value]
    return value


class ToolRegistry:
    """Whitelist dispatch: never eval model-generated code or shell commands."""
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def execute(self, name: str, arguments: dict | str) -> dict:
        if name not in SCHEMAS:
            return {"status": "error", "code": "unknown_tool", "message": f"未注册工具：{name}"}
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            args = normalize_arguments(args, SCHEMAS[name])
            errors = list(Draft202012Validator(SCHEMAS[name], format_checker=FormatChecker()).iter_errors(args))
            if errors:
                error = errors[0]
                return {"status": "error", "code": "invalid_arguments",
                        "message": f"参数 {'/'.join(map(str, error.absolute_path)) or '<root>'}：{error.message}",
                        "hint": "只修正出错字段。无日期筛选时使用JSON null（不加引号），不要编造日期范围，也不要重复相同错误参数。"}
            if name == "course_rag_search":
                return KnowledgeBase(self.ctx).search(**args)
            return Scheduler(self.ctx).run(**args)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return {"status": "error", "code": "tool_failed", "message": str(exc)}
