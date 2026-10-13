"""Exercise the pinned native child runtime against localhost-only model/API fixtures.

The small probe calls the public subagent API directly. It is not a replacement
research dispatcher: the production delegation plugin has its own contract tests.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

from jsonschema import Draft202012Validator
import pytest


ROOT = Path(__file__).resolve().parents[4]
MARKER = "NATIVE_CHILD_PROBE "
PROBE = r"""
export const name = 'native-child-probe';
export const inject = ['tools', 'subagents', 'agents'];
export function apply(ctx) {
  const emit = value => process.stderr.write('NATIVE_CHILD_PROBE ' + JSON.stringify(value) + '\n');
  ctx.tools.register({
    name: 'probe_delegate', description: 'Offline native child contract probe.',
    parameters: {type:'object', properties:{role:{type:'string'}}, required:['role'], additionalProperties:false},
    output: {schema:{type:'object', additionalProperties:true}, render: (_args, value) => [{type:'text', text:JSON.stringify(value)}]},
    isConcurrencySafe: () => true,
    async execute(args, exec) {
      const controller = new AbortController();
      const abort = () => controller.abort(exec.signal.reason);
      exec.signal.addEventListener('abort', abort, {once:true});
      if (exec.signal.aborted) abort();
      let run, timer, result;
      try {
        run = await ctx.subagents.start('spawn', {
          parent:exec.agent, label:args.role, prompt:[{type:'text', text:'TASK: ' + args.role}],
          persona:'Only perform the supplied offline fixture task.',
          toolFilter:{allow:['mcp__research__read_market_source', 'probe_delegate']},
          maxDepth:2,
          outputSchema:{type:'object', properties:{summary:{type:'string'}, source_ids:{type:'array',items:{type:'string'}}}, required:['summary','source_ids'], additionalProperties:false},
          signal:controller.signal,
        });
        if (args.role === 'cancel') timer = setTimeout(() => controller.abort('offline cancellation'), 500);
        result = await run.result;
        emit({event:'result', role:args.role, id:run.id, ...result});
        return {id:run.id, ...result};
      } catch (error) {
        emit({event:'error', role:args.role, error:String(error)});
        throw error;
      } finally {
        clearTimeout(timer);
        exec.signal.removeEventListener('abort', abort);
        if (run) {
          await run.dispose();
          emit({event:'disposed', role:args.role, id:run.id, still_registered:ctx.agents.get(run.id) !== undefined});
        }
      }
    },
  });
}
"""


def _tool(name, arguments, call_id="call"):
    return {"index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def _run_native_probe(tmp_path, scenario, *, production=False):
    if not (ROOT / "infra/harness/node_modules/@deepseek-ai/dsh/lib/bin.js").exists() or not shutil.which("node"):
        if os.environ.get("CI"):
            pytest.fail("CI must provision the pinned Harness and Node for native research tests")
        pytest.skip("Pinned Harness and Node must be provisioned for the native probe")
    requests, api_calls, tasks = [], [], {}
    barrier = threading.Barrier(2)
    release_cancel = threading.Event()
    lock = threading.Lock()

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            api_calls.append((self.path, self.headers.get("Authorization"), None))
            self.reply_json({"source_id": "text:offline-original", "document_id": "offline-document",
                             "version_id": "offline-original", "text": "Original fixture evidence."})

        def reply_json(self, value, status=200):
            wire = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/v1/chat/completions":
                api_calls.append((self.path, self.headers.get("Authorization"), body))
                if self.path.endswith("/tasks"):
                    from watchlist_app.services.research_tasks import result_schema
                    with lock:
                        reused = next((task for task in tasks.values()
                            if scenario == "reuse" and task.get("status") == "completed"
                            and all(task.get(key) == value for key, value in body.items())), None)
                        if reused:
                            self.reply_json({**reused, "reused": True})
                            return
                        task_id = f"task-{len(tasks) + 1}"
                        tasks[task_id] = {**body, "task_id": task_id, "status": "running"}
                        task = dict(tasks[task_id])
                    self.reply_json({**task, "reused": False, "result_schema": result_schema(body["role"]),
                        "context": {"role": body["role"], "skill": f"ONLY_{body['role']}_SKILL", "instrument_id": "offline-asset"}})
                    return
                if "/tasks/" in self.path:
                    task_id = self.path.split("/tasks/")[1].split("/")[0]
                    task = tasks[task_id]
                    if scenario == "reject" and self.path.endswith("/result"):
                        self.reply_json({"detail": "Fixture rejects an unbound original source."}, 422)
                        return
                    if "result" in body:
                        from pydantic import ValidationError
                        from watchlist_app.services.research_tasks import ResearchTaskResult
                        try:
                            ResearchTaskResult.model_validate(body["result"])
                        except ValidationError as error:
                            self.reply_json({"detail": error.errors(include_input=False, include_context=False)}, 422)
                            return
                    task.update(body)
                    task["status"] = body["result"]["status"] if "result" in body else "failed"
                    self.reply_json(task)
                    return
                self.reply_json({"cutoff": "2026-10-13T00:00:00Z", "private_note": "PARENT_CONTEXT_ONLY"})
                return
            users = " ".join(json.dumps(m.get("content")) for m in body["messages"] if m["role"] == "user")
            match = re.search(r"TASK: (themes|quantitative|topic|too_deep|plain|cancel)", users)
            role = match[1] if match else "main"
            with lock:
                step = sum(previous["role"] == role for previous in requests)
                requests.append({"role": role, "step": step, "request": body})
            calls, content = None, None
            if role == "main":
                if step == 0:
                    calls = [_tool("mcp__research__read_research_context", {})]
                    if production:
                        # The root has no assigned task, including before its
                        # first delegation establishes the plugin's root ID.
                        calls.append(_tool("read_assigned_context", {}, "unassigned_root"))
                        calls[-1]["index"] = 1
                elif step == 1 or (scenario == "reuse" and step == 2):
                    roles = (["themes", "quantitative"] if scenario == "parallel" else
                             ["themes"] if scenario == "reuse" else ["quantitative"] if scenario == "reject" else [scenario])
                    calls = [_tool("delegate_research" if production else "probe_delegate",
                        {"role": ("themes" if child == "plain" else child), "instrument_id": "offline-asset",
                         "instruction": f"TASK: {child}"} if production else {"role": child},
                        f"delegate_{child}") for child in roles]
                    for index, call in enumerate(calls):
                        call["index"] = index
                else:
                    content = "NATIVE_PROBE_DONE"
            elif scenario == "parallel" and role in {"themes", "quantitative"} and step == 0:
                try:
                    barrier.wait(timeout=8)
                except threading.BrokenBarrierError:
                    content = "Sibling did not start concurrently."
                else:
                    calls = ([_tool("delegate_research" if production else "probe_delegate",
                        {"role": "themes", "instrument_id": "offline-asset", "instruction": "TASK: topic"}
                        if production else {"role": "topic"})] if role == "themes" else
                             [_tool("mcp__research__submit_research_review", {"result": {"reviews": []}})])
            elif role == "topic" and step == 0:
                calls = [_tool("delegate_research" if production else "probe_delegate",
                    {"role": "themes", "instrument_id": "offline-asset", "instruction": "TASK: too_deep"}
                    if production else {"role": "too_deep"})]
            elif production and role in {"themes", "topic"} and step == 1:
                calls = [_tool("read_assigned_context", {"path": ["task_results"], "offset": 1, "limit": 2})]
            elif scenario == "reject" and step >= 1:
                content = "The proposed source was rejected; I cannot provide a valid result."
            elif role == "quantitative" and step == 1:
                calls = [_tool("structured_output", {"summary": "Missing required source IDs."})]
            elif role == "quantitative" and step == 2:
                calls = [_tool("mcp__research__read_market_source", {"document_id": "text:offline-original"})]
            elif role == "plain":
                content = "A plain final answer is not a structured result."
            elif role == "cancel":
                release_cancel.wait(10)
                content = "The cancelled model request cannot complete a child result."
            else:
                calls = [_tool("structured_output", {"summary": f"Verified {role}", "source_ids": ["text:offline-original"],
                    **({"status": "completed"} if production else {})})]
            delta = {"role": "assistant", "reasoning_content": "Offline contract fixture."}
            delta.update({"tool_calls": calls} if calls else {"content": content})
            chunks = [{"id": "fixture", "object": "chat.completion.chunk", "created": 1,
                       "model": "deepseek-v4.1-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"id": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": 20}}]
            wire = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(wire)))
                self.end_headers()
                self.wfile.write(wire)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected when the native child's request is cancelled.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    plugin = tmp_path / "native-probe.mjs"
    plugin.write_text(PROBE)
    patch = tmp_path / "native-probe.patch.json"
    patch.write_text(json.dumps([
        {"id": "subagent", "disabled": False},
        {"id": "subagent-spawn-in-process", "disabled": False},
        {"insert": [{"id": "native-child-probe", "name": str(ROOT / "apps/watchlist/backend/scripts/research_delegation.mjs") if production else str(plugin)}]},
    ]))
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR") if key in os.environ}
    env.update({"DEEPSEEK_API_KEY": "offline-fixture-key", "DEEPSEEK_BASE_URL": base + "/v1",
                "DSH_HOME": str(tmp_path / "home"), "DSH_TELEMETRY_DISABLED": "1", "DSH_TOOLS_MODE": "native",
                "DSH_PERMISSION_MODE": "read-only", "INVESTMENT_STUDIO_RESEARCH_PROJECT_ROOT": str(ROOT),
                "INVESTMENT_STUDIO_RESEARCH_API_BASE_URL": base + "/api",
                "INVESTMENT_STUDIO_RESEARCH_RUN_ID": "offline-run", "INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN": "offline-run-token",
                "INVESTMENT_STUDIO_RESEARCH_PERSONA": "Use only the offline fixture tools.",
                "INVESTMENT_STUDIO_RESEARCH_MODEL_CONCURRENCY": "2",
                "INVESTMENT_STUDIO_RESEARCH_SECTION": "overview",
                "INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE": "sector" if production else "conversation"})
    command = [str(ROOT / "infra/harness/run.sh"), "--profile", "headless", "--patch",
               str(ROOT / "infra/config/deepseek_harness.patch.yml"), "--patch",
               str(ROOT / f"apps/watchlist/backend/config/{'sector' if production else 'research'}_harness.patch.yml"), "--patch", str(patch),
               "Read the parent-only fixture, then perform the requested native child probe."]
    try:
        result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=50)
    finally:
        release_cancel.set()
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stderr
    assert "NATIVE_PROBE_DONE" in result.stdout, result.stderr
    events = [json.loads(line[len(MARKER):]) for line in result.stderr.splitlines() if line.startswith(MARKER)]
    return requests, api_calls, events


def test_native_spawn_parallel_fresh_context_depth_tools_and_structured_result(tmp_path):
    requests, api_calls, events = _run_native_probe(tmp_path, "parallel")
    initial = {row["role"]: row["request"] for row in requests if row["step"] == 0}
    assert set(initial) == {"main", "themes", "quantitative", "topic"}
    for role in ("themes", "quantitative", "topic"):
        assert "PARENT_CONTEXT_ONLY" not in json.dumps(initial[role])
        names = {tool["function"]["name"] for tool in initial[role]["tools"]}
        assert "structured_output" in names and "probe_delegate" in names
        assert "mcp__research__submit_research_review" not in names
    quant_second = next(row["request"] for row in requests if row["role"] == "quantitative" and row["step"] == 1)
    assert "unknown tool" in json.dumps(quant_second).lower()
    quant_third = next(row["request"] for row in requests if row["role"] == "quantitative" and row["step"] == 2)
    invalid_reply = [row["content"] for row in quant_third["messages"] if row["role"] == "tool"][-1]
    assert "source_ids" in str(invalid_reply) and "required" in str(invalid_reply).lower()
    results = {row["role"]: row for row in events if row["event"] == "result"}
    assert set(results) == {"themes", "quantitative", "topic"}
    assert len({row["id"] for row in results.values()}) == 3
    assert all(row["stopReason"] == "completed" and row["structured"]["source_ids"] == ["text:offline-original"] for row in results.values())
    assert any(row["role"] == "too_deep" and "depth" in row["error"].lower() for row in events if row["event"] == "error")
    assert {row["id"] for row in events if row["event"] == "disposed" and not row["still_registered"]} == {row["id"] for row in results.values()}
    assert api_calls and all(auth == "Bearer offline-run-token" for _, auth, _ in api_calls)
    assert not any("submit" in path for path, _, _ in api_calls)
    assert any("market-source?document_id=text%3Aoffline-original" in path for path, _, _ in api_calls)
    assert "Original fixture evidence." in json.dumps(next(row["request"] for row in requests
        if row["role"] == "quantitative" and row["step"] == 3))


@pytest.mark.parametrize("scenario,reason", [("plain", "error"), ("cancel", "aborted")])
def test_native_spawn_missing_structured_result_and_cancellation_dispose_child(tmp_path, scenario, reason):
    requests, _, events = _run_native_probe(tmp_path, scenario)
    assert any(row["role"] == scenario for row in requests)
    result = next(row for row in events if row["event"] == "result")
    assert result["stopReason"] == reason and "structured" not in result
    disposed = next(row for row in events if row["event"] == "disposed")
    assert disposed["id"] == result["id"] and not disposed["still_registered"]


def test_production_native_bridge_saves_typed_children_with_runtime_parent_identity(tmp_path):
    requests, api_calls, _ = _run_native_probe(tmp_path, "parallel", production=True)
    # Inspect the actual pinned Harness -> provider wire, including tools added
    # by the production plugin and the native child structured-output tool.
    for row in requests:
        for tool in row["request"]["tools"]:
            function = tool["function"]
            parameters = function["parameters"]
            assert parameters.get("type") == "object", function["name"]
            assert isinstance(parameters.get("properties"), dict), function["name"]
            Draft202012Validator.check_schema(parameters)
            assert set(parameters.get("required", [])) <= parameters["properties"].keys()
    root_tools = {tool["function"]["name"]: tool["function"]["parameters"]
                  for tool in requests[0]["request"]["tools"]}
    assert set(root_tools["delegate_research"]["required"]) == {"role", "instrument_id", "instruction"}
    assert not root_tools["read_assigned_context"].get("required")
    for row in requests:
        if row["role"] == "main":
            continue
        structured = next(tool["function"]["parameters"] for tool in row["request"]["tools"]
                          if tool["function"]["name"] == "structured_output")
        assert {"status", "summary"} <= set(structured["required"])
    before_delegation = next(row["request"] for row in requests if row["role"] == "main" and row["step"] == 1)
    assert "belongs to an assigned research child" in json.dumps(before_delegation)
    created = [body for path, _, body in api_calls if path.endswith("/tasks")]
    saved = [(path, body) for path, _, body in api_calls if path.endswith("/result") and "status" in body["result"]]
    assert len(created) == len(saved) == 3, api_calls
    parents = [body for body in created if body["parent_task_id"] is None]
    child = next(body for body in created if body["parent_task_id"] is not None)
    assert {body["role"] for body in parents} == {"themes", "quantitative"}
    assert child["role"] == "themes" and child["instruction"] == "TASK: topic"
    assert child["parent_task_id"] in {f"task-{i + 1}" for i, row in enumerate(created) if row["role"] == "themes"}
    assert all(body["result"]["status"] == "completed" for _, body in saved)
    assert all(set(body["result"]) == {"status", "summary", "source_ids"} for _, body in saved)
    assert all(auth == "Bearer offline-run-token" for _, auth, _ in api_calls)
    assert not any(path.endswith(("/failure", "/submit")) for path, _, _ in api_calls)
    assert sum("/context?" in path and "offset=1" in path and "limit=2" in path for path, _, _ in api_calls) == 2
    initial = {row["role"]: row["request"] for row in requests if row["step"] == 0}
    assert set(initial) == {"main", "themes", "quantitative", "topic"}
    for role in ("themes", "quantitative", "topic"):
        assert "PARENT_CONTEXT_ONLY" not in json.dumps(initial[role])
        own_role = "themes" if role == "topic" else role
        assert f"ONLY_{own_role}_SKILL" in json.dumps(initial[role])
        assert f"ONLY_{'themes' if own_role == 'quantitative' else 'quantitative'}_SKILL" not in json.dumps(initial[role])
        tools = {tool["function"]["name"] for tool in initial[role]["tools"]}
        assert "mcp__research__submit_research_review" not in tools
        assert "mcp__research__submit_research_section" not in tools
        assert ("delegate_research" in tools) is (role == "themes")
        assert "read_assigned_context" in tools


def test_production_native_bridge_records_missing_typed_result_as_failure(tmp_path):
    _, api_calls, _ = _run_native_probe(tmp_path, "plain", production=True)
    failures = [body for path, _, body in api_calls if path.endswith("/failure")]
    assert len(failures) == 1 and "complete proposal (error)" in failures[0]["message"]
    assert not any(path.endswith("/result") for path, _, _ in api_calls)


def test_production_native_bridge_records_api_rejection_without_publication(tmp_path):
    requests, api_calls, _ = _run_native_probe(tmp_path, "reject", production=True)
    assert sum(path.endswith("/result") for path, _, _ in api_calls) == 1
    failure = next(body for path, _, body in api_calls if path.endswith("/failure"))
    assert "complete proposal (error)" in failure["message"]
    refused = next(row["request"] for row in requests if row["role"] == "quantitative" and row["step"] == 1)
    assert "422" in json.dumps(refused) and "unbound original source" in json.dumps(refused)
    assert not any(path.endswith("/submit") for path, _, _ in api_calls)


def test_production_native_bridge_reuses_completed_task_without_another_child(tmp_path):
    requests, api_calls, _ = _run_native_probe(tmp_path, "reuse", production=True)
    assert sum(path.endswith("/tasks") for path, _, _ in api_calls) == 2
    assert sum(path.endswith("/result") for path, _, _ in api_calls) == 1
    assert sum(row["role"] == "themes" for row in requests) == 1
    last = next(row["request"] for row in requests if row["role"] == "main" and row["step"] == 3)
    assert '"reused":true' in json.dumps(last, separators=(",", ":")).replace('\\"', '"')


def test_native_provider_slots_remove_cancelled_waiters_and_release_capacity():
    if not shutil.which("node"):
        pytest.skip("Node must be provisioned")
    script = """
import {modelSlots} from './apps/watchlist/backend/scripts/research_delegation.mjs';
import assert from 'node:assert/strict';
const acquire = modelSlots(1);
const first = await acquire();
const controller = new AbortController();
const cancelled = acquire(controller.signal);
controller.abort(new Error('cancelled queued request'));
await assert.rejects(cancelled, /cancelled queued request/);
let entered = false;
const waiting = acquire().then(release => { entered = true; return release; });
await Promise.resolve();
assert.equal(entered, false);
first();
const second = await waiting;
assert.equal(entered, true);
second();
const third = await acquire();
third();
console.log('SLOTS_CLEAN');
"""
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "SLOTS_CLEAN" in result.stdout, result.stderr
