import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from study_agent.agent import Agent, ResponsesClient
from study_agent.common import Context, ROOT, TZ
from study_agent.demo import scheduler_args, seed_data
from study_agent.schemas import TOOLS


def message(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def call(name, args, index):
    return {"type": "function_call", "name": name, "arguments": json.dumps(args, ensure_ascii=False),
            "call_id": f"call_{index}", "id": f"fc_{index}", "status": "completed"}


class ScriptedClient:
    """Protocol fixture, not an LLM; only tests orchestration and context handling."""
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def create(self, instructions, history, tools):
        self.requests.append(copy.deepcopy({"instructions": instructions, "input": history, "tools": tools}))
        return {"status": "completed", "output": next(self.outputs)}


class AgentTest(unittest.TestCase):
    def setUp(self):
        parent = (ROOT / "runs" / "_tests").resolve()
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(parent))
        self.addCleanup(self.temp.cleanup)
        self.ctx = Context(Path(self.temp.name), datetime(2026, 10, 16, 8, tzinfo=TZ))
        seed_data(self.ctx)

    def query(self):
        return {"query": "考试", "course": "软件工程", "start_date": "2026-10-19", "end_date": "2026-10-25", "top_k": 5}

    def test_tool_chaining_preserves_call_ids_and_reasoning(self):
        reasoning = {"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "fixture"}
        client = ScriptedClient([
            [reasoning, call("course_rag_search", self.query(), 1)],
            [call("calendar_scheduler", scheduler_args("2026-10-19", "2026-10-20", action="inspect"), 2)],
            [message("已查到考试和空闲时间。")],
        ])
        agent = Agent(self.ctx, client)
        self.assertIn("空闲", agent.turn("查下周软工考试和空闲时间"))
        second = client.requests[1]["input"]
        self.assertIn(reasoning, second)
        outputs = [x for x in second if x.get("type") == "function_call_output"]
        self.assertEqual(outputs[0]["call_id"], "call_1")
        self.assertEqual(json.loads(outputs[0]["output"])["tasks"][0]["id"], "se-exam-review")
        self.assertEqual(len([x for x in client.requests[2]["input"] if x.get("type") == "function_call_output"]), 2)
        self.assertIn("2026-10-19", client.requests[0]["instructions"])
        self.assertEqual(len(client.requests[0]["tools"]), 2)

    def test_multiturn_clarification_keeps_user_context(self):
        client = ScriptedClient([[message("你要复习哪门课？")],
                                 [call("course_rag_search", self.query(), 1)],
                                 [message("软件工程下周三有考试。")]])
        agent = Agent(self.ctx, client)
        agent.turn("帮我安排下周复习")
        agent.turn("软件工程，晚上18点到22点")
        request = client.requests[1]["input"]
        self.assertIn("帮我安排下周复习", json.dumps(request, ensure_ascii=False))
        self.assertIn("18点到22点", json.dumps(request, ensure_ascii=False))
        agent.record_commit({"status": "committed", "plan_id": "000000000001"})
        self.assertEqual(agent.history[-1]["role"], "developer")

    def test_invalid_model_arguments_return_tool_error(self):
        client = ScriptedClient([[call("course_rag_search", {"query": "考试"}, 1)], [message("请补充查询参数。")]])
        agent = Agent(self.ctx, client)
        agent.turn("查考试")
        output = client.requests[1]["input"][-1]
        self.assertEqual(json.loads(output["output"])["code"], "invalid_arguments")

    def test_tool_loop_is_bounded(self):
        client = ScriptedClient([[call("course_rag_search", self.query(), n)] for n in range(10)])
        agent = Agent(self.ctx, client)
        with self.assertRaisesRegex(RuntimeError, "10次"):
            agent.turn("考试")
        self.assertEqual(len(client.requests), 10)
        self.assertEqual(agent.history, [])

    def test_http_request_payload_and_unicode(self):
        client = ResponsesClient("test-secret", "test-model", "https://example.test/v1")
        payload = {"status": "completed", "output": [message("你好") ]}
        with patch.object(client.opener, "open", return_value=io.BytesIO(json.dumps(payload).encode())) as opened:
            result = client.create("提示词", [{"role": "user", "content": "中文"}], [])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/v1/responses")
        body = json.loads(request.data)
        self.assertFalse(body["store"])
        self.assertFalse(body["parallel_tool_calls"])
        self.assertIn("reasoning.encrypted_content", body["include"])
        self.assertEqual(result["output"][0]["content"][0]["text"], "你好")

    def test_http_failure_redacts_key_and_rejects_truncation(self):
        client = ResponsesClient("test-secret", "test-model")
        error = HTTPError(client.url, 401, "Unauthorized", {}, io.BytesIO(b"invalid test-secret"))
        with patch.object(client.opener, "open", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                client.create("", [], [])
            self.assertNotIn("test-secret", str(caught.exception))
        with patch.object(client.opener, "open", return_value=io.BytesIO(b'{"status":"incomplete","output":[]}')):
            with self.assertRaisesRegex(RuntimeError, "截断"):
                client.create("", [], [])

    def test_api_config_validation(self):
        with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
            ResponsesClient("", "model")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            ResponsesClient("secret", "model", "http://example.com/v1")
        with self.assertRaises(ValueError):
            ResponsesClient("secret", "model", "https://user:secret@example.com/v1")

    def test_bigmodel_request_fields_and_tool_schema(self):
        client = ResponsesClient("test-bigmodel-key", "glm-5.3-flash", "https://open.bigmodel.cn/api/v1")
        original = copy.deepcopy(TOOLS)
        payload = {"status": "completed", "output": [message("你好") ]}
        with patch.object(client.opener, "open", return_value=io.BytesIO(json.dumps(payload).encode())) as opened:
            client.create("提示词", [{"role": "user", "content": "中文"}], TOOLS)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://open.bigmodel.cn/api/v1/responses")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "glm-5.3-flash")
        self.assertNotIn("include", body)
        self.assertNotIn("parallel_tool_calls", body)
        self.assertFalse(body["store"])
        self.assertEqual(body["reasoning"], {"effort": "high"})
        self.assertNotIn("strict", body["tools"][0])
        self.assertEqual(body["tools"][0]["parameters"], TOOLS[0]["parameters"])
        self.assertEqual(TOOLS, original)

    def test_bigmodel_wrong_endpoint_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "https://open.bigmodel.cn/api/v1"):
            ResponsesClient("test-key", "glm-5.3-flash", "https://open.bigmodel.cn/api/paas/v4")

    def test_bigmodel_object_content_tool_roundtrip(self):
        answer = {"type": "message", "role": "assistant", "content": {"type": "output_text", "text": "软件工程下周三考试。"}}
        client = ScriptedClient([[call("course_rag_search", self.query(), 1)], [answer]])
        agent = Agent(self.ctx, client)
        result = agent.turn("下周软件工程考试")
        self.assertIn("软件工程下周三考试。", result)
        self.assertIn("示例", result)
        self.assertEqual(agent.history[-1], answer)
        self.assertEqual(client.requests[1]["input"][-1]["call_id"], "call_1")

    def test_bigmodel_env_configuration_keeps_model_id(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "glm-5.3-flash",
                                       "OPENAI_BASE_URL": "https://open.bigmodel.cn/api/v1"}):
            client = ResponsesClient.from_env()
        self.assertTrue(client.is_bigmodel)
        self.assertEqual(client.model, "glm-5.3-flash")

    def test_reported_glm_null_arguments_finish_in_one_tool_call(self):
        args = {"course": "软件工程", "end_date": "null", "query": "软件工程 实验一 提交 截止时间 DDL",
                "start_date": "null", "top_k": 3}
        client = ScriptedClient([[call("course_rag_search", args, 1)],
                                 [message("示例资料中，实验一截止于2026-10-20 23:59。来源：data/tasks.json#/0。")]])
        agent = Agent(self.ctx, client)
        result = agent.turn("软件工程实验一什么时候交？")
        self.assertIn("2026-10-20 23:59", result)
        self.assertEqual(len(client.requests), 2)
        tool_result = json.loads(client.requests[1]["input"][-1]["output"])
        self.assertEqual(tool_result["status"], "ok")
        self.assertIn("JSON null", client.requests[0]["instructions"])

    def test_repeated_identical_errors_stop_without_wasting_more_model_requests(self):
        first = {**self.query(), "start_date": "bad-date"}
        second = {**first, "query": "只改写查询词不能修复日期"}
        client = ScriptedClient([[call("course_rag_search", first, 1)], [call("course_rag_search", second, 2)]])
        agent = Agent(self.ctx, client)
        with self.assertRaisesRegex(RuntimeError, "已停止重复重试"):
            agent.turn("软件工程考试")
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(agent.history, [])

    def test_chat_can_disable_trace_and_hide_tool_json(self):
        from study_agent.cli import chat
        client = ScriptedClient([[call("course_rag_search", self.query(), 1)], [message("示例资料中，下周三考试。")]])
        client.model = "protocol-test"
        agent = Agent(self.ctx, client, trace=True)
        console = io.StringIO()
        with patch("builtins.input", side_effect=["/trace off", "下周软件工程考试？", "/trace on", "/exit"]), redirect_stdout(console):
            chat(agent)
        output = console.getvalue()
        self.assertIn("助手 > 示例资料中，下周三考试。", output)
        self.assertNotIn("[工具结果]", output)
        self.assertNotIn('"status":', output)
        self.assertTrue(agent.trace)


if __name__ == "__main__":
    unittest.main()
