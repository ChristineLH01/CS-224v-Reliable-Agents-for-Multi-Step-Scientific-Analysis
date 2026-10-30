"""Verify Responses through SHELDON's actual LLM and native tool-loop path."""
import asyncio
import json
import secrets
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pydantic import BaseModel
from sheldon.core.cache import DiskCache
from sheldon.core.io import load_dotenv
from sheldon.core.llm import LLM
from sheldon.core.trace import Trace


class Check(BaseModel):
    token: str
    notes: str = ""


async def main():
    load_dotenv(ROOT / ".env")
    output = ROOT / "runs" / ("responses-harness-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    trace = Trace("synthetic", output / "trace.jsonl")
    llm = LLM(provider="openai-responses", model="gpt-5.6-terra", effort="high",
              max_tokens=2048, cache=DiskCache(enabled=False))
    token = secrets.token_hex(16)
    tools = [{"name": "read_probe", "description": "Read the private synthetic token.",
              "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
             {"name": "submit_check", "description": "Submit the token returned by read_probe.",
              "input_schema": Check.model_json_schema()}]
    executed = []
    async def execute(name, args):
        if name != "read_probe" or args:
            raise ValueError("Unexpected tool invocation")
        executed.append(name)
        return json.dumps({"token": token})
    try:
        with trace:
            result = await llm.converse("Call read_probe, then submit its exact token using submit_check.",
                tools=tools, execute=execute, stop_on={"submit_check"}, max_iterations=3, tag="native_check")
            submitted = result.calls_to("submit_check")
            assert executed and submitted and Check.model_validate(submitted[0].input).token == token
            parsed = await llm.complete('Return token="schema-ok".', schema=Check, tag="schema_check")
            assert parsed.parsed.token == "schema-ok"
        summary = {"passed": True, "provider": "openai-responses", "engine_protocol": "native",
                   "explicit_effort": "high", "usage_estimate": llm.total.to_dict()}
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary, indent=2))
        print("Saved:", output)
    finally:
        await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())
