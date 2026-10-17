"""Stateless Responses API loop; HTTP client is stdlib and independently testable."""
from __future__ import annotations

import json
import os
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import uuid4

from .common import Context
from .schemas import TOOLS
from .tools import ToolRegistry


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ResponsesClient:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", timeout: int = 60):
        if not api_key or not model:
            raise ValueError("真实对话需要OPENAI_API_KEY和OPENAI_MODEL。复制.env.example为.env填写；无需密钥请运行 demo。")
        parsed = urlsplit(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise ValueError("OPENAI_BASE_URL应为不带账户、查询参数的API基础地址")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
            raise ValueError("远程API必须使用HTTPS；HTTP仅用于本地服务")
        self.is_bigmodel = parsed.hostname == "open.bigmodel.cn"
        if self.is_bigmodel and parsed.path.rstrip("/") != "/api/v1":
            raise ValueError("智谱Responses接口的OPENAI_BASE_URL应为https://open.bigmodel.cn/api/v1；不要使用Chat Completions的/api/paas/v4地址")
        self.api_key, self.model = api_key, model
        self.url, self.timeout = base_url.rstrip("/") + "/responses", timeout
        self.opener = build_opener(NoRedirect())

    @classmethod
    def from_env(cls, model: str | None = None):
        return cls(os.getenv("OPENAI_API_KEY", ""), model or os.getenv("OPENAI_MODEL", ""),
                   os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))

    def create(self, instructions: str, history: list[dict], tools: list[dict]) -> dict:
        body = {"model": self.model, "instructions": instructions, "input": history,
                "tools": tools, "store": False, "max_output_tokens": 5000}
        if self.is_bigmodel:
            # Use only fields documented by BigModel's distinct Responses endpoint.
            # Schema constraints are still enforced locally by ToolRegistry.
            body["tools"] = [{k: v for k, v in tool.items() if k != "strict"} for tool in tools]
            body["reasoning"] = {"effort": "high"}
            body["max_output_tokens"] = 8192
        else:
            body["parallel_tool_calls"] = False
            body["include"] = ["reasoning.encrypted_content"]
        request = Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            message = exc.read(4096).decode("utf-8", errors="replace").replace(self.api_key, "[REDACTED]")
            raise RuntimeError(f"API返回HTTP {exc.code}：{message}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"无法连接模型API：{str(exc).replace(self.api_key, '[REDACTED]')}") from exc
        except ValueError as exc:
            raise RuntimeError("模型API没有返回有效JSON") from exc
        if not isinstance(result, dict) or result.get("error") or result.get("status") in ("failed", "incomplete"):
            raise RuntimeError("模型响应失败或输出被截断，请缩小任务范围后重试")
        if not isinstance(result.get("output"), list):
            raise RuntimeError("接口响应缺少output；该服务需要支持Responses API")
        return result


class Agent:
    def __init__(self, ctx: Context, client, trace: bool = False, emit=print):
        self.ctx, self.client, self.trace, self.emit = ctx, client, trace, emit
        self.registry = ToolRegistry(ctx)
        self.history: list[dict] = []
        self.log_path = ctx.root / "runs" / f"chat-{uuid4().hex[:12]}.jsonl"

    def log(self, kind: str, value) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": self.ctx.now.isoformat(), "kind": kind, "value": value}, ensure_ascii=False) + "\n")

    def instructions(self) -> str:
        now = self.ctx.now
        monday = now.date() + timedelta(days=7 - now.weekday())
        prompt_path = self.ctx.root / "prompts" / "system.md"
        # Demo/test contexts use an isolated data root but the same actual prompt.
        if not prompt_path.exists():
            from .common import ROOT
            prompt_path = ROOT / "prompts" / "system.md"
        return prompt_path.read_text(encoding="utf-8") + (
            f"\n\n运行时上下文：当前时间 {now.isoformat()}，下周为 {monday} 至 {monday + timedelta(days=6)}。"
            f"\n用户默认偏好：{json.dumps(self.ctx.preferences, ensure_ascii=False)}"
            + ("\n数据模式：示例。当前课程规则、DDL和课表为虚构演示数据，回答课程事实时必须明确说‘示例资料’，不能当作真实课程要求。" if self.ctx.is_demo else "\n数据模式：用户资料。")
        )

    def turn(self, text: str) -> str:
        if len(json.dumps(self.history, ensure_ascii=False)) > 180000:
            raise ValueError("会话上下文已较长，请用 /reset 开启新会话，再说明当前任务")
        pending = [*self.history, {"role": "user", "content": text}]
        self.log("user", text)
        repeated_errors = {}
        used_demo_data = False
        for _ in range(10):
            response = self.client.create(self.instructions(), pending, TOOLS)
            output = response["output"]
            # Preserve every returned item, including encrypted reasoning, for continuation.
            pending.extend(output)
            calls = [item for item in output if item.get("type") == "function_call"]
            if not calls:
                parts = []
                for item in output:
                    if item.get("type") != "message":
                        continue
                    content = item.get("content") or []
                    if isinstance(content, str):
                        parts.append(content)
                        continue
                    if isinstance(content, dict):
                        content = [content]
                    for part in content:
                        if isinstance(part, dict) and part.get("type") in ("output_text", "refusal"):
                            parts.append(part.get("text", part.get("refusal", "")))
                answer = "\n".join(parts).strip()
                if not answer:
                    raise RuntimeError("模型没有返回文本或工具调用，请重试")
                if used_demo_data and not any(word in answer.splitlines()[0] for word in ("示例", "演示")):
                    answer = "示例资料：" + answer
                self.history = pending
                self.log("assistant", answer)
                return answer
            for call in calls:
                self.log("tool_call", {"name": call.get("name"), "arguments": call.get("arguments")})
                if self.trace:
                    self.emit(f"[工具调用] {call.get('name')} {call.get('arguments')}")
                result = self.registry.execute(call.get("name", ""), call.get("arguments", "{}"))
                used_demo_data = used_demo_data or result.get("data_mode") == "demo"
                self.log("tool_result", result)
                if self.trace:
                    self.emit("[工具结果] " + json.dumps(result, ensure_ascii=False, indent=2))
                pending.append({"type": "function_call_output", "call_id": call["call_id"],
                                "output": json.dumps(result, ensure_ascii=False)})
                if result.get("status") == "error":
                    signature = (call.get("name"), result.get("code"), result.get("message"))
                    repeated_errors[signature] = repeated_errors.get(signature, 0) + 1
                    if repeated_errors[signature] >= 2:
                        raise RuntimeError("已停止重复重试，避免继续消耗额度。工具连续返回相同错误：" + result["message"])
        raise RuntimeError("本轮已达到10次模型请求上限；请缩小任务范围。已生成的预览保存在state/plans。")

    def record_commit(self, result: dict) -> None:
        # CLI-generated evidence is trusted developer context, not a fake tool call.
        self.history.append({"role": "developer", "content": "用户在CLI执行了/commit，程序实际结果：" + json.dumps(result, ensure_ascii=False)})
        self.log("commit", result)
