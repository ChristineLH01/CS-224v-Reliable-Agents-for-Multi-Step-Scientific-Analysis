"""SHELDON's model calls over the OpenAI Responses API.

SHELDON's own OpenAI backend speaks Chat Completions and never sends a reasoning
effort. On the LiteLLM proxy, GPT-6 models refuse function tools together with a
reasoning effort on that route ("use /v1/responses or set reasoning_effort to
'none'"), so SHELDON could not run its tool loop at `high` effort. This backend is
the same contract on the Responses API:

  SHELDON (Anthropic-shaped)        Responses API
  ------------------------------    ---------------------------------------------
  system                            instructions
  user / assistant text             input messages
  tool_use block                    function_call item
  tool_result block                 function_call_output item
  schema=<pydantic model>           text.format = strict json_schema
  run effort                        reasoning.effort, on every call

Every call streams (the proxy refuses non-streamed responses over 640 KB), sets
`store: false`, and asks for the encrypted reasoning back. Each reply's raw output
items, reasoning included, are kept in memory and replayed with the next turn of the
same conversation, which reasoning models need to continue a tool loop. The
conversation SHELDON keeps (and writes into its trace) holds only a short reference
to them, so traces do not fill up with encrypted blobs.

Only the transport changes: SHELDON builds the same requests, and the stage
prompts, tools, loop and parsing are its own.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from pydantic import BaseModel
from sheldon.core.llm import LLM, Backend, LLMResult, _parse_json_into, client_timeout, estimate_cost, normalize_base_url
from sheldon.core.types import Usage

log = logging.getLogger(__name__)

#: content block type carrying a reference to a reply's raw output items
ITEMS_BLOCK = "responses_items"


class ResponsesStreamError(RuntimeError):
    """A streamed response that failed or broke off. `status_code` lets SHELDON's own
    retry ladder (`sheldon.core.llm._transient`) treat it like the HTTP error it is."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """`model`'s JSON schema in the form strict structured outputs accept: every
    property required, no extra properties, no defaults."""
    try:
        from openai.lib._pydantic import to_strict_json_schema  # noqa: PLC0415

        schema = to_strict_json_schema(model)
    except Exception:  # noqa: BLE001 - fall back to a plain conversion
        schema = model.model_json_schema()

    def fix(node: Any) -> Any:
        if isinstance(node, list):
            return [fix(x) for x in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            if key in ("default", "title"):
                continue
            if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
                out[key] = {name: fix(sub) for name, sub in value.items()}  # names, not keywords
            else:
                out[key] = fix(value)
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    return fix(schema)


class ResponsesBackend(Backend):
    name = "openai-responses"
    supports_server_tools = False

    def __init__(self, *, base_url: str, api_key: str, effort: str | None = "high",
                 usd_per_mtok: list[float] | tuple[float, ...] | None = None, max_retries: int = 2,
                 client: Any = None) -> None:
        if client is None:
            from openai import AsyncOpenAI  # noqa: PLC0415

            client = AsyncOpenAI(base_url=normalize_base_url(base_url) or None, api_key=api_key or "unused",
                                 max_retries=max_retries, timeout=client_timeout())
        self.client = client
        self.effort = effort
        self.prices = tuple(usd_per_mtok) if usd_per_mtok else None
        #: reference -> the raw output items of one reply, replayed with later turns
        self._items: dict[str, list[dict[str, Any]]] = {}
        self._warned: set[str] = set()

    # -- request ------------------------------------------------------------------

    def _input(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in req["messages"]:
            role, content = message.get("role"), message.get("content")
            if isinstance(content, str):
                items.append({"role": role, "content": content})
                continue
            blocks = list(content or [])
            if role == "assistant":
                ref = next((b.get("ref") for b in blocks if b.get("type") == ITEMS_BLOCK), None)
                if ref and ref in self._items:
                    items.extend(self._items[ref])  # the reply exactly as it came, reasoning included
                    continue
                # A reply this process did not receive (a test fixture, say): rebuild it
                # from its text and tool calls, without reasoning.
                for b in blocks:
                    if b.get("type") == "text" and b.get("text"):
                        items.append({"role": "assistant", "content": b["text"]})
                    elif b.get("type") == "tool_use":
                        items.append({"type": "function_call", "call_id": b.get("id"), "name": b.get("name"),
                                      "arguments": json.dumps(b.get("input") or {})})
                continue
            for b in blocks:  # a user turn: tool results, then any text, in order
                if b.get("type") == "tool_result":
                    items.append({"type": "function_call_output", "call_id": b.get("tool_use_id"),
                                  "output": _text_of(b.get("content"))})
                elif b.get("type") == "text" and b.get("text"):
                    items.append({"role": role or "user", "content": b["text"]})
        return items

    def _tools(self, tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        out = []
        for tool in tools or []:
            if "input_schema" not in tool:
                name = tool.get("name") or tool.get("type") or "?"
                if name not in self._warned:
                    self._warned.add(name)
                    log.warning("dropping server-side tool %r: it has no Responses API equivalent here", name)
                continue
            # SHELDON's submit tools are generated from pydantic models with optional
            # fields, which strict function schemas reject; keep them as written.
            out.append({"type": "function", "name": tool["name"], "description": tool.get("description", ""),
                        "parameters": tool["input_schema"], "strict": False})
        return out

    def _effort(self, req: dict[str, Any]) -> str | None:
        return ((req.get("output_config") or {}).get("effort")) or self.effort

    def params(self, req: dict[str, Any], schema: type[BaseModel] | None, strict: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": req["model"],
            "input": self._input(req),
            "max_output_tokens": req["max_tokens"],
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        effort = self._effort(req)
        if effort:
            params["reasoning"] = {"effort": effort}
        if req.get("system"):
            params["instructions"] = req["system"]
        tools = self._tools(req.get("tools"))
        if tools:
            params["tools"] = tools
        if schema is not None:
            fmt: dict[str, Any] = {"type": "json_schema", "name": schema.__name__[:64]}
            if strict:
                fmt.update(schema=strict_schema(schema), strict=True)
            else:
                fmt.update(schema=schema.model_json_schema(), strict=False)
            params["text"] = {"format": fmt}
        return params

    # -- the call -------------------------------------------------------------------

    async def _stream(self, params: dict[str, Any]) -> Any:
        stream = await self.client.responses.create(**params, stream=True)
        final = None
        try:
            async for event in stream:
                kind = getattr(event, "type", "")
                if kind in ("response.completed", "response.incomplete", "response.failed"):
                    final = event.response
                elif kind == "error":
                    code = str(getattr(event, "code", "") or "")
                    raise ResponsesStreamError(f"stream error {code}: {getattr(event, 'message', '')}",
                                               429 if "rate" in code.lower() else 500)
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:  # noqa: BLE001
                    pass
        if final is None:
            raise ResponsesStreamError("the response stream ended without a final response", 502)
        if getattr(final, "status", "") == "failed":
            err = getattr(final, "error", None)
            raise ResponsesStreamError(f"response failed: {getattr(err, 'code', '')} {getattr(err, 'message', '')}")
        return final

    async def call(self, req: dict[str, Any], schema: type[BaseModel] | None) -> LLMResult:
        params = self.params(req, schema)
        try:
            response = await self._stream(params)
        except Exception as e:
            # A schema the strict mode will not take: once more without strict.
            if schema is None or getattr(e, "status_code", None) != 400 or "schema" not in str(e).lower():
                raise
            log.info("strict json_schema rejected for %s; retrying without strict", schema.__name__)
            response = await self._stream(self.params(req, schema, strict=False))
        return self.result(response, req, schema)

    def result(self, response: Any, req: dict[str, Any], schema: type[BaseModel] | None) -> LLMResult:
        output = list(getattr(response, "output", None) or [])
        raw = [_dump(item) for item in output]
        status = getattr(response, "status", "completed")
        cut = status == "incomplete"
        ref = uuid.uuid4().hex
        # A reply cut off by the output cap is reported as SHELDON's Anthropic path reports
        # it (stop_reason max_tokens, which ends a tool loop), never as a tool call: its last
        # call's arguments may be half-written. Only its text is replayed, since a call with
        # no output, or reasoning with no item after it, makes the next turn fail.
        self._items[ref] = [i for i in raw if i.get("type") == "message"] if cut else raw
        text = getattr(response, "output_text", None)
        if text is None:
            text = "".join(p.get("text", "") for i in raw if i.get("type") == "message"
                           for p in i.get("content") or [] if p.get("type") == "output_text")
        refusal = next((p.get("refusal") for i in raw if i.get("type") == "message"
                        for p in i.get("content") or [] if p.get("type") == "refusal"), None)
        content: list[dict[str, Any]] = [{"type": ITEMS_BLOCK, "ref": ref}]
        if text:
            content.append({"type": "text", "text": text})
        calls = 0
        for item in [] if cut else raw:
            if item.get("type") != "function_call":
                continue
            calls += 1
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            content.append({"type": "tool_use", "id": item.get("call_id"), "name": item.get("name"),
                            "input": arguments if isinstance(arguments, dict) else {"value": arguments}})
        if cut:
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "") or "incomplete"
            stop = "max_tokens" if reason == "max_output_tokens" else str(reason)
        elif calls:
            stop = "tool_use"
        elif refusal:
            stop = "refusal"
        else:
            stop = "end_turn"
        parsed = _parse_json_into(schema, text) if schema is not None and text else None
        return LLMResult(text=text or "", usage=self.usage(response, req["model"]),
                         model=getattr(response, "model", None) or req["model"], parsed=parsed,
                         stop_reason=stop, refusal=refusal, content=content)

    def usage(self, response: Any, model: str) -> Usage:
        u = getattr(response, "usage", None)
        total_in = int(getattr(u, "input_tokens", 0) or 0)
        cached = int(getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0)
        out = int(getattr(u, "output_tokens", 0) or 0)  # reasoning included
        usage = Usage(calls=1, input_tokens=max(0, total_in - cached), output_tokens=out, cache_read_tokens=cached)
        if self.prices:
            p_in, p_cached, p_out = self.prices
            usage.cost_usd = (usage.input_tokens * p_in + cached * p_cached + out * p_out) / 1e6
        else:
            usage.cost_usd = estimate_cost(model, usage)
        return usage


class ResponsesLLM(LLM):
    """SHELDON's `LLM` (cache, budget, trace, retries untouched) on the Responses backend.
    The run's effort goes to the backend: SHELDON puts effort into requests only for
    Claude models."""

    def __init__(self, *, base_url: str, api_key: str, usd_per_mtok: list[float] | None = None,
                 client: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("provider", "openai")
        super().__init__(base_url=base_url, api_key=api_key, **kwargs)
        self._usd_per_mtok = usd_per_mtok
        self._client = client

    def _backend(self) -> Backend:
        if self._backend_impl is None:
            self._backend_impl = ResponsesBackend(base_url=self.base_url or "", api_key=self.api_key or "",
                                                  effort=self.effort, usd_per_mtok=self._usd_per_mtok,
                                                  client=self._client)
        return self._backend_impl


def _dump(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return item.model_dump(exclude_none=True)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return "" if content is None else str(content)
