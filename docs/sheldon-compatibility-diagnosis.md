# SHELDON / Stanford proxy diagnosis

Current scope update: the custom automatic judge, trajectory-length limit and
judge configuration have been removed at the user's request. Historical trial
score files remain unchanged. The required upstream score hook is now a no-op
marked not_evaluated; it does not read rubrics or call a model. Solving and
answer/trace persistence remain enabled.

Cleanup note: one-off diagnostic outputs were preserved in
`upstream-sheldon/runs/diagnostics-20261029.zip`, retaining their original paths
inside the archive. Historical paths below refer to those archived files where
applicable. The standalone Responses check, captured-finalization replay, and
finalization-variant scripts were removed after diagnosis; the reusable native
harness check and pilot runner remain. All benchmark trial directories remain.

## Latest verified status: one full diagnostic completed

Run `debug-20261029-233055-866892__54e7a3a379` completed hb020 end to end in
107.161 seconds, with 16 successful model calls, no recorded error, and
$0.193819 estimated usage. Finalization returned via the requested native
submission tool in about 7 seconds. The final species was Homo sapiens, matching
the rubric text, but explicitly low confidence and without successful sequence
confirmation. No judge or complete protocol audit was performed. This is one
successful local diagnostic, not a 5-question score or stability guarantee.

The communication adapter now supplies a `submit_*` function when a
schema-backed completion's system prompt explicitly requires it, including
required finalization after the search budget is exhausted. Function arguments
are validated against the original Pydantic model. Stage prompts and search
algorithms were not changed. Streaming and strict output schemas remain enabled.
This transport revision is recorded as `responses-stream-strict-submit-v2`.

Checked October 29, 2026 against the upstream README, local source (upstream
commit `7fbf027d1fb414443612d4ad2661b396c8352cd3`), Stanford proxy OpenAPI,
authenticated model metadata, and the actual failed trial traces.
No paid inference was performed for this investigation.

## Follow-up: Responses compatibility check passed

After the user's approval, three synthetic live requests were made using
`gpt-5.6-terra`, explicit `reasoning.effort=high`, SDK retries disabled, and a
60-second external deadline per request. All passed:

| Check | Result | Request time |
|---|---|---:|
| Strict JSON-schema output | Pass | 1.57 s |
| Native function call | Pass | 1.02 s |
| Tool-result continuation and strict final JSON | Pass | 1.42 s |

The final response correctly reproduced a random token read by a local tool,
not present in the original prompt. Prior response output items, including
reasoning items, were replayed with the function result. Requests used
`store=false`. Total usage: 248 input tokens and 67 output tokens; the provider
did not return a dollar cost. These tests establish support for this small
Responses workflow, not completion of SHELDON or BioMysteryBench.

The initial standalone check script was removed during cleanup; the maintained
replacement is `upstream-sheldon/scripts/check_responses_harness.py`.
Local evidence: `upstream-sheldon/runs/responses-check-20261029-225644-421102/`.
The no-inference statement above refers to the preceding read-only investigation.

## Follow-up: SHELDON transport integrated and tested

Added the explicit `openai-responses` provider, and switched the preview/debug
config back to the upstream native `toolloop` engine. The search method, stage
prompts and engine algorithms are unchanged. The communication layer now sends
effort explicitly, preserves raw reasoning/function-call output across turns,
validates schema results, records request start/failure, and uses no SDK retries
with a total per-request deadline. The provider is part of configuration and
therefore run identity, separate from earlier Chat Completions/ReAct trials.

Ten offline tests and upstream staged/session smoke checks passed. A live
three-call check through SHELDON's actual LLM/converse implementation passed
tool execution, native submit-tool output, and schema validation with optional
fields. Estimated cost: $0.00199; provider dollar cost was unavailable. Evidence:
`upstream-sheldon/runs/responses-harness-20261029-225912-969530/`.
This validates integrated transport, not yet a complete biological task.

## Confirmed findings

### Finalization-only diagnosis: concrete upstream size error

Further native pilot `debug-20261029-232414-862498` used streaming and strict
JSON output. It reached finalization, then received an explicit incomplete
response with reason `max_output_tokens`, rather than a local timeout. The
debug configuration's 4,096-token cap includes reasoning as well as visible
answer tokens. Thus the debug budget itself can prevent a final answer;
this is a separate failure from the earlier proxy response-size error.
Strict schema success on a replay is not proof that all fresh trials finish.

**Correction after fresh pilot:** the streaming debug trial tagged
`debug-20261029-231754-696504` again failed in finalization after its 90-second
deadline. It completed 15 model calls first, had no final answer, and recorded
$0.247387 estimated solver usage. The one successful finalization replay below
does not establish a stable fix. Do not mark the complete pipeline as verified.

**Follow-up workaround tested:** keeping the original high effort, 4,096-token
cap and identical final prompt, switching only to streaming returned a valid
FinalOut in 29.86 seconds. Estimated usage cost was $0.034417. The answer was
Staphylococcus aureus, which does not match the rubric; this is a transport
diagnostic, not a solved task or a fresh complete trial. The provider now uses
streaming by default and the diagnostic wrapper explicitly enables it.

Lowering the non-streaming cap to 512 or 1,024 avoided the size error in these
checks but returned incomplete/max_output_tokens after 11.36s and 16.92s,
respectively. Those truncated responses were rejected, not counted as answers.
Streaming is the tested workaround; the precise internal cause of the service's
non-streaming size error remains unverified. Original search evidence was weak
and the 15-call diagnostic budget was exhausted before comparative analysis.

The native-tool debug trial completed 15 model requests and 13 tools, then
timed out in finalization. Its original successful message histories were
replayed offline and matched exactly; the final request was reconstructed
without executing tools or calling models. It contains 17,731 user-prompt
characters and requests 4,096 maximum output tokens with high effort.

Two finalization-only retries with a longer request deadline returned HTTP 400
after roughly 94–105 seconds. The detailed response states:

`MAX_READ_SIZE_EXCEEDED: The response content length cannot be greater than 655360 bytes long.`

This is a service-side response-content size restriction, not evidence of an
invalid key or missing input file. It is not yet established which internal
response component exceeded the limit or whether reasoning content caused it.
Reducing reasoning alone was not validated as a fix: a none-effort diagnostic
timed out at its shorter 30-second deadline. The local 90-second timeout can
mask the eventual service error. Do not claim that simply extending it fixes
the task. No complete biological answer was produced by these retries.

Evidence: `runs/finalize-detailed-result.json` and the reconstruction/diagnostic
scripts under `upstream-sheldon/scripts/`. These are diagnostic retries, not
additional successful benchmark trials.

1. The endpoint and aliases are valid. Authenticated model discovery succeeded.
   The proxy reports LiteLLM API 1.106.3. `gpt-5.6-terra` maps to
   `openai/system.ai.gpt-5-6-terra`; `claude-opus-5.5` maps to
   `openai/system.ai.claude-opus-5-5`. The SDK base `/v1` is appropriate;
   `normalize_base_url()` also strips a pasted `/chat/completions` suffix.

2. Upstream's OpenAIBackend always calls `client.chat.completions.create`.
   It does not implement a Responses backend. The Stanford OpenAPI advertises
   both `/v1/chat/completions` and `/v1/responses`, but this alone does not
   demonstrate per-model or credential compatibility with Responses.

3. The actual Terra toolloop trial received HTTP 400: function tools with
   reasoning are unsupported on Chat Completions; use Responses or explicitly
   set reasoning effort to none. Changing only the base URL cannot migrate
   request and response formats. The backend does not explicitly send the
   configured effort. The source of the server's effective reasoning default
   remains unverified; do not claim high effort was honored.

4. The actual Opus trial received HTTP 400: structured-output translation
   requires forced tool use which the served model rejects. Although model
   metadata advertises `supports_response_schema: true`, that flag is not a
   guarantee for this exact combination. The backend's JSON fallback detects
   only `response_format` or `json_schema` in the exception text, so the phrase
   "Structured output is not supported" did not trigger the fallback.

5. Upstream README documents ReAct as a fallback for proxies. That made local
   execution possible, but the observed model repeatedly emitted empty
   `Final Answer:` messages. ReActEngine accepts this as stage completion.
   In methods/stage.py, recovery includes only the original prompt plus final
   text, not EngineResult.tool_calls or the full tool transcript. Empty final
   text therefore loses the tool observations during recovery. This explains
   statements claiming no data access even after successful file reads. It
   does not prove that all models or all upstream benchmarks suffer this issue.

6. The planning output removed the biological question from `situation` and
   retained it only in `requirements`. Exploration then generated hypotheses
   about how to interpret the structure (sequence vs ligand vs architecture),
   rather than alternative organism identities. This is an observed semantic
   failure in this trial, separate from HTTP compatibility. More trials or a
   targeted check are needed to determine its frequency.

7. Local input access now works: the trace shows a successful read of the
   2,914,556-byte mmCIF. `gemmi` was absent and Biopython raised "Missing closing
   semicolon" on the scrubbed file. Neither establishes that the biological
   task is impossible; robust parsing or a fallback extraction is needed.

8. The diagnostic reached round.done at 321.375s, then no completed model event
   appeared before the external 600s kill. Finalization is required even after
   the 300s search budget expires. A 90s HTTP read timeout is not a total-call
   deadline; OpenAI SDK max_retries defaults to 4, separate from the harness's
   SHELDON_RETRY_ATTEMPTS=1. These facts permit a long wait, but the exact reason
   for the missing final response is not established by the existing trace.

## Interpretation

This is not an API-key failure, a missing dataset, or a completed benchmark
answer scored as wrong. It combines native API incompatibility, weaknesses in
the ReAct fallback/recovery path, a planning semantic failure, and secondary
local analysis errors. Simple schema/tool smoke checks did not validate the
full SHELDON stage protocol; earlier readiness statements were too broad.

## Next work, in order

1. Validate Responses with the same alias, explicit reasoning setting,
   structured output, a tool call, tool-result continuation and usage capture.
   If compatible, add a transport adapter; leave hypothesis/observation search
   and stage prompts unchanged. Record this transport modification explicitly.
2. Enforce explicit bounded retries and total-call timeouts; log request start,
   completion and failure so waiting is observable.
3. Test that planning retains the biological problem in its situation and
   that stage submission preserves evidence. Do not silently disable planning
   or alter prompts while claiming exact upstream settings.
4. Verify parsing and database access, then run one end-to-end pilot before
   expanding to five tasks or 25 trials.

Sources: https://github.com/stanford-oval/sheldon and
https://models.genie.stanford.edu/openapi.json. Runtime evidence is stored in
the local Opus, Terra and debug trial traces under upstream-sheldon/runs/.
