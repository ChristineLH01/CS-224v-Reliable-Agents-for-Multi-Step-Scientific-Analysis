import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from sheldon.core.llm import LLM
from sheldon.core.responses_backend import ResponsesBackend


class Answer(BaseModel):
    value: str = ""


class ResponseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Individual streaming tests opt in; other fixtures model one-shot SDK responses.
        patcher = patch.dict("os.environ", {"SHELDON_RESPONSES_STREAM": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def backend(self):
        backend = object.__new__(ResponsesBackend)
        backend._warned_server_tools = set()
        return backend

    def test_exact_replay_without_duplicate_tool_calls(self):
        backend = self.backend()
        raw = [{"type": "reasoning", "id": "r1", "encrypted_content": "opaque"},
               {"type": "function_call", "id": "f1", "call_id": "c1", "name": "read", "arguments": "{}"}]
        req = {"messages": [{"role": "user", "content": "test"},
            {"role": "assistant", "content": [{"type": "responses_output", "items": raw},
                {"type": "tool_use", "id": "c1", "name": "read", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "result"}]}]}
        translated = backend._input(req)
        self.assertEqual(translated[1:3], raw)
        self.assertEqual(len([x for x in translated if x.get("type") == "function_call"]), 1)
        self.assertEqual(translated[-1]["call_id"], "c1")

    def test_effort_and_optional_schema(self):
        llm = LLM(provider="openai-responses", model="synthetic")
        req = llm._request("test", "instructions", "synthetic", "low", 256, True, False)
        params = self.backend()._response_params(req, Answer)
        self.assertEqual(params["reasoning"], {"effort": "low"})
        self.assertEqual(params["instructions"], "instructions")
        self.assertTrue(params["text"]["format"]["strict"])
        wire_schema = params["text"]["format"]["schema"]
        self.assertEqual(wire_schema["required"], ["value"])
        self.assertFalse(wire_schema["additionalProperties"])
        self.assertNotIn("default", wire_schema["properties"]["value"])
        self.assertFalse(params["store"])

    async def test_required_submission_is_offered_and_validated(self):
        backend = self.backend()
        req = {"model": "synthetic", "max_tokens": 128, "messages": [],
               "system": "Call submit_final once."}
        params = backend._response_params(req, Answer)
        self.assertNotIn("text", params)
        self.assertEqual(params["tool_choice"], {"type": "function", "name": "submit_final"})
        raw = {"type": "function_call", "call_id": "c1", "name": "submit_final", "arguments": '{"value":"ok"}'}
        item = SimpleNamespace(**raw, model_dump=lambda **kw: raw)
        response = SimpleNamespace(status="completed", output_text="", model="synthetic",
                                   output=[item], usage=None)
        backend.client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)))
        result = await backend.call(req, Answer)
        self.assertEqual(result.parsed.value, "ok")

    async def test_schema_validation_and_usage(self):
        backend = self.backend()
        raw = {"type": "message", "content": [{"type": "output_text", "text": '{"value":"ok"}'}]}
        response = SimpleNamespace(status="completed", output_text='{"value":"ok"}', model="synthetic",
            output=[SimpleNamespace(type="message", model_dump=lambda **kw: raw)],
            usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                  input_tokens_details=SimpleNamespace(cached_tokens=25), cost=0.01))
        backend.client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)))
        req = {"model": "synthetic", "max_tokens": 128, "messages": [{"role": "user", "content": "test"}]}
        result = await backend.call(req, Answer)
        self.assertEqual(result.parsed.value, "ok")
        self.assertEqual(result.usage.input_tokens, 75)
        self.assertEqual(result.usage.cache_read_tokens, 25)
        self.assertEqual(result.usage.cost_usd, 0.01)
        response.output_text = ""
        with self.assertRaises(ValueError):
            await backend.call(req, Answer)

    async def test_total_timeout(self):
        backend = self.backend()
        async def slow(**kwargs):
            await asyncio.sleep(1)
        backend.client = SimpleNamespace(responses=SimpleNamespace(create=slow))
        with patch.dict("os.environ", {"SHELDON_RESPONSES_TIMEOUT": "0.01"}):
            with self.assertRaises(TimeoutError):
                await backend.call({"model": "synthetic", "max_tokens": 128, "messages": []}, None)

    async def test_stream_preserves_terminal_response(self):
        backend = self.backend()
        response = object()
        class Stream:
            closed = False
            def __aiter__(self):
                return self.events()
            async def events(self):
                yield SimpleNamespace(type="response.output_text.delta", delta="partial")
                yield SimpleNamespace(type="response.completed", response=response)
            async def close(self):
                self.closed = True
        stream = Stream()
        backend.client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=stream)))
        with patch.dict("os.environ", {"SHELDON_RESPONSES_STREAM": "1"}):
            result = await backend._create_response({"model": "synthetic"})
        self.assertIs(result, response)
        self.assertTrue(stream.closed)
        self.assertTrue(backend.client.responses.create.call_args.kwargs["stream"])


if __name__ == "__main__":
    unittest.main()
