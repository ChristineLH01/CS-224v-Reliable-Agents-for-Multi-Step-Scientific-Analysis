"""Opt-in Responses transport; method and engine protocols remain unchanged."""
from __future__ import annotations

import asyncio
import json
import os
import re

from sheldon.core.llm import LLMResult, OpenAIBackend, _parse_json_into, estimate_cost
from sheldon.core.types import Usage


def strict_output_schema(schema):
    """Constrain the wire format; keep upstream Pydantic models unchanged."""
    from openai.lib._pydantic import to_strict_json_schema
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key != "default"}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    return clean(to_strict_json_schema(schema))


class ResponsesBackend(OpenAIBackend):
    name = "openai-responses"

    def __init__(self, base_url=None, api_key=None):
        # No hidden SDK retries; total request timeout is enforced below.
        super().__init__(base_url, api_key, max_retries=0)

    def _input(self, req):
        items = []
        for message in req["messages"]:
            role, content = message["role"], message.get("content")
            if isinstance(content, str):
                items.append({"role": role, "content": content})
                continue
            # Replay exact Responses output, including encrypted reasoning.
            markers = [b for b in content or [] if b.get("type") == "responses_output"]
            if markers:
                for marker in markers:
                    items.extend(marker["items"])
                continue
            texts = []
            for block in content or []:
                kind = block.get("type")
                if kind == "text":
                    texts.append(block.get("text", ""))
                elif kind == "tool_result":
                    items.append({"type": "function_call_output", "call_id": block["tool_use_id"],
                                  "output": str(block.get("content", ""))})
                elif kind == "tool_use":
                    items.append({"type": "function_call", "call_id": block["id"],
                                  "name": block["name"], "arguments": json.dumps(block.get("input", {}))})
            if texts:
                items.append({"role": role, "content": "\n".join(texts)})
        return items

    def _response_params(self, req, schema):
        params = {"model": req["model"], "input": self._input(req),
                  "max_output_tokens": req["max_tokens"], "store": False,
                  "include": ["reasoning.encrypted_content"],
                  "reasoning": {"effort": req.get("responses_effort", "high")}}
        if req.get("system"):
            params["instructions"] = req["system"]
        tools = self._tools(req.get("tools"))
        if tools:
            # Explicitly preserve optional fields in upstream Pydantic schemas.
            params["tools"] = [{"type": "function", **t["function"], "strict": False} for t in tools]
        if schema:
            submission = self._submission_name(req, schema)
            if submission:
                # Upstream's required closing prompt says to call this tool
                # even when its budget gate switched to complete(schema=...).
                # Supply the requested submission protocol without changing
                # the prompt or offering any further analysis capabilities.
                params["tools"] = [{"type": "function", "name": submission,
                    "description": "Submit the requested structured result.",
                    "parameters": strict_output_schema(schema), "strict": True}]
                params["tool_choice"] = {"type": "function", "name": submission}
            else:
                params["text"] = {"format": {"type": "json_schema", "name": schema.__name__,
                                              "strict": True, "schema": strict_output_schema(schema)}}
        return params

    @staticmethod
    def _submission_name(req, schema):
        if schema is None:
            return None
        match = re.search(r"\bcall\s+(submit_[A-Za-z0-9_]+)\b", req.get("system", ""), re.IGNORECASE)
        return match.group(1) if match else None

    async def _create_response(self, params):
        if os.environ.get("SHELDON_RESPONSES_STREAM", "1") != "1":
            return await self.client.responses.create(**params)
        stream = await self.client.responses.create(**params, stream=True)
        final = None
        try:
            async for event in stream:
                if event.type in ("response.completed", "response.incomplete", "response.failed"):
                    final = event.response
                elif event.type == "error":
                    raise RuntimeError(f"Responses stream error: {getattr(event, 'message', '')}")
        finally:
            await stream.close()
        if final is None:
            raise RuntimeError("Responses stream ended without a terminal response")
        return final

    async def call(self, req, schema):
        params = self._response_params(req, schema)
        response = await asyncio.wait_for(
            self._create_response(params),
            timeout=float(os.environ.get("SHELDON_RESPONSES_TIMEOUT", "120")),
        )
        self.last_response = response
        if response.status != "completed":
            raise RuntimeError(f"Responses status={response.status}; details={response.incomplete_details}")
        text = response.output_text or ""
        raw = [item.model_dump(exclude_none=True) for item in response.output]
        content = [{"type": "responses_output", "items": raw}]
        if text:
            content.append({"type": "text", "text": text})
        for item in response.output:
            if item.type == "function_call":
                arguments = json.loads(item.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Function arguments must be a JSON object")
                content.append({"type": "tool_use", "id": item.call_id,
                                "name": item.name, "input": arguments})
        submission = self._submission_name(req, schema)
        if submission:
            matching = [block for block in content if block.get("type") == "tool_use" and block["name"] == submission]
            if len(matching) != 1:
                raise ValueError(f"Expected exactly one {submission} structured submission")
            parsed = schema.model_validate(matching[0]["input"])
            text = parsed.model_dump_json()
        else:
            parsed = _parse_json_into(schema, text) if schema and text else None
        if schema and parsed is None:
            raise ValueError(f"Responses output failed validation against {schema.__name__}")
        usage = response.usage
        cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        measured = Usage(calls=1, input_tokens=max(0, (getattr(usage, "input_tokens", 0) or 0) - cached),
                         output_tokens=getattr(usage, "output_tokens", 0) or 0, cache_read_tokens=cached)
        cost = getattr(usage, "cost", None)
        measured.cost_usd = float(cost) if cost is not None else estimate_cost(req["model"], measured)
        return LLMResult(text=text, model=response.model, parsed=parsed, content=content,
                         usage=measured, stop_reason="tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn")
