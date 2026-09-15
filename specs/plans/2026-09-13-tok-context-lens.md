# Tok Context Lens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Tok feel responsive and calm by eliminating the avoidable MCP
rescan, exposing truthful content-free run activity, supporting safe
cancellation, and replacing the tabbed mini-dashboard with one conversation.

**Architecture:** Codex continues to call the same allowlisted stdio Token Meter
MCP tools. Coach-launched MCP processes first dispatch through a guarded local
warm-evidence route backed by the running server's `AgentAPIService`, then fall
back once to the existing local path. The Codex adapter consumes JSONL events as
they arrive and projects only a bounded activity state; `page.html` turns that
state into one inline Tok response and keeps goal/weekly controls behind a
compact goal rail.

**Tech Stack:** Python 3.8+ standard library, `unittest`, stdio MCP, Codex CLI
JSONL, localhost HTTP, and the single-file HTML/CSS/JavaScript dashboard.

**Spec:** `specs/2026-09-11-goals-coach-design.md`

## Global Constraints

- Work only in `.worktrees/goals-coach` on `codex/goals-coach`; leave `main`
  untouched.
- Keep the Python server and MCP implementation dependency-free and local-only.
- Codex must still choose and call an allowlisted Token Meter MCP tool; the warm
  route changes transport cost, not the evidence contract.
- Never expose or persist prompts, responses, reasoning, JSONL text, MCP
  arguments/results, credentials, action tokens, paths, project names, session
  titles, or raw traces.
- Keep action tokens out of command arguments and model input.
- Missing evidence remains unavailable; estimates remain labeled estimates.
- Do not add a top-level Goals page or change the required dashboard navigation
  order.
- Do not change the user's Codex model or saved Codex configuration.
- Preserve the current local-scan MCP behavior when the warm route is absent or
  unhealthy.
- One tracked-file writer owns the worktree until the implementation freezes.
- Do not commit, push, open a pull request, merge, install a release, or post
  externally without the corresponding explicit approval. Local source-to-runtime
  installation for verification remains in scope after implementation.

---

### Task 1: Shared tool dispatch and warm Coach MCP evidence

**Files:**

- Modify: `token_meter/services/agent_api.py`
- Modify: `token_meter_mcp.py`
- Modify: `token_meter/app.py`
- Modify: `token_meter/coach/codex.py`
- Test: `tests/test_mcp_server.py`
- Test: `tests/test_coach_http.py`
- Test: `tests/test_coach.py`

**Interfaces:**

- Consumes: `AgentAPIService`, `_ACTION_TOKEN`, `PORT`, Coach's sanitized child
  environment, and the existing MCP tool schemas.
- Produces: `dispatch_agent_tool(service, name, arguments, caller) -> dict`,
  `POST /coach/evidence`, and the optional MCP environment variables
  `TOKEN_METER_COACH_EVIDENCE_URL` and `TOKEN_METER_COACH_ACTION_TOKEN`.

- [ ] **Step 1: Add failing shared-dispatch tests**

Add table-driven tests proving the shared dispatcher accepts every existing MCP
tool with its current arguments and rejects unknown tools, non-object arguments,
and extra keys without invoking the service.

```python
def test_shared_agent_dispatch_rejects_unknown_and_extra_arguments(self):
    from token_meter.services.agent_api import dispatch_agent_tool

    service = mock.Mock()
    with self.assertRaisesRegex(ValueError, "Unknown tool"):
        dispatch_agent_tool(service, "erase", {}, caller={})
    with self.assertRaisesRegex(ValueError, "unsupported argument"):
        dispatch_agent_tool(
            service, "usage", {"window": "7d", "secret": True}, caller={},
        )
    self.assertEqual(service.mock_calls, [])
```

- [ ] **Step 2: Run the dispatcher tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_mcp_server -v
```

Expected: failure because `dispatch_agent_tool` does not exist.

- [ ] **Step 3: Centralize tool names and argument allowlists**

Define immutable allowlists in `token_meter/services/agent_api.py` and route
caller-aware tools explicitly.

```python
AGENT_TOOL_ARGUMENTS = {
    "check": frozenset({"focus", "execution", "session_id"}),
    "usage": frozenset({"window", "focus"}),
    "capabilities": frozenset({"scope", "limit"}),
    "sessions": frozenset({"scope", "runtime", "client", "model", "state",
                           "start", "end", "cursor", "limit"}),
    "trace": frozenset({"session_id", "view", "sections", "execution",
                        "event_types", "cursor", "limit"}),
    "stats": frozenset({"metrics", "group_by", "runtime", "client", "model",
                        "state", "session_id", "start", "end", "sort_by",
                        "sort_direction", "cursor", "limit"}),
    "goal": frozenset({"focus"}),
    "schema": frozenset({"subject", "runtime"}),
}

def dispatch_agent_tool(service, name, arguments, caller=None):
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")
    allowed = AGENT_TOOL_ARGUMENTS.get(name)
    if allowed is None:
        raise ValueError("Unknown tool: {}".format(name))
    if set(arguments) - allowed:
        raise ValueError("{} received an unsupported argument".format(name))
    values = dict(arguments)
    if name in frozenset({"check", "capabilities", "sessions"}):
        values["caller"] = caller or {}
    return getattr(service, name)(**values)
```

Replace `token_meter_mcp.call_tool`'s duplicated dispatch chain with this helper.
Keep JSON-RPC errors and `structuredContent` byte-for-byte compatible.

- [ ] **Step 4: Add failing warm-route HTTP and MCP fallback tests**

Cover the route and both transports with literal sentinels:

```python
def test_coach_evidence_route_uses_warm_service_and_existing_guards(self):
    status, body = self.request(
        "POST", "/coach/evidence",
        {"tool": "usage", "arguments": {"window": "7d", "focus": "spend"}},
        headers=self.action_headers(),
    )
    self.assertEqual(status, 200)
    self.assertEqual(body["data_scope"], "usage_7d_spend")
    self.assertEqual(self.service.warm_calls, [
        ("usage", {"window": "7d", "focus": "spend"}),
    ])
```

Add MCP tests with an injected opener proving:

- a valid warm response bypasses `meter.application()` entirely;
- connection failure, timeout, non-200, oversized body, malformed JSON, or a
  non-object projection falls back exactly once;
- unknown tools and invalid arguments fail before network or local dispatch;
- the action token is sent only as `X-Token-Meter-Action`.

- [ ] **Step 5: Run warm-route tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_mcp_server tests.test_coach_http tests.test_coach -v
```

Expected: failures because `/coach/evidence` and the warm MCP client do not
exist.

- [ ] **Step 6: Implement the guarded warm route and one-shot fallback**

Add a maximum 8 KiB JSON body for `/coach/evidence`, reuse the current Origin,
content-type, and action-token preflight, allow only the module-level
`MCP_TOOLS`, and
dispatch through the shared helper against `application().agent_api`.

In `token_meter_mcp.py`, use `urllib.request` with a short timeout and a 64 KiB
response cap only when both Coach environment variables are present. Return
`None` on transport failure so `call_tool` performs the existing local dispatch
once; never include exception text in the MCP result.

Extend `CodexCoach` with an injected `child_environment() -> dict` callback. Add
the two bridge values after
`_isolated_environment(self._environment(codex_path), temporary)` and assert
that neither value appears in the command list or prompt.

- [ ] **Step 7: Run Task 1 tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_mcp_server tests.test_coach_http tests.test_coach -v
```

Expected: all shared-dispatch, route, privacy, warm-path, and fallback cases pass.

- [ ] **Step 8: Record the approval-gated checkpoint**

Run `git status --short` and `git diff --check`. Do not commit unless the user
separately approves a commit.

### Task 2: Streaming Codex lifecycle and exact-run cancellation

**Files:**

- Modify: `token_meter/coach/codex.py`
- Test: `tests/test_coach.py`

**Interfaces:**

- Consumes: Codex JSONL stdout, the existing 90-second timeout, output byte
  limits, and injected runners.
- Produces: `CodexCoach.status() -> {available, status, activity?}`,
  `CodexCoach.cancel() -> {ok, changed}`, and activity values
  `opening_codex|reading_token_meter|checking_evidence`.

- [ ] **Step 1: Write failing lifecycle-state tests**

Use a blocking injected runner and synchronization events to prove state is
visible only during the owned run and clears on success and failure.

```python
def test_status_projects_only_content_free_live_activity(self):
    runner_started = threading.Event()
    release = threading.Event()

    def runner(command, prompt, **options):
        options["on_process"](FakeProcess())
        options["on_event"]({"type": "thread.started"})
        runner_started.set()
        self.assertTrue(release.wait(2))
        return valid_completed_result()

    thread = threading.Thread(target=lambda: coach.run(self.valid_request()))
    thread.start()
    self.assertTrue(runner_started.wait(2))
    self.assertEqual(coach.status()["activity"]["stage"], "opening_codex")
    self.assertNotIn("prompt", coach.status()["activity"])
    release.set()
    thread.join(2)
    self.assertNotIn("activity", coach.status())
```

Add event fixtures for allowlisted MCP start/completion and hostile agent-message
content. Assert only fixed stage enums and numeric start time leave the adapter.

- [ ] **Step 2: Write failing cancellation-race tests**

Prove `cancel()` terminates only the registered process for the current run,
returns `changed=False` before/after a run, maps the active run to `cancelled`,
and cannot target a later process when the first run finishes during cancel.

- [ ] **Step 3: Run lifecycle tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_coach.CodexCoachTests -v
```

Expected: failures because activity callbacks and `cancel()` are absent.

- [ ] **Step 4: Replace buffered `communicate` with bounded JSONL consumption**

Keep `subprocess.Popen` inside the default runner. Read stdout on a dedicated
daemon thread into a bounded `queue.Queue`; the owner loop checks the monotonic
deadline and cancellation event without blocking. Accumulate at most 64 KiB,
parse only complete JSONL rows, and invoke `on_event(row)` without retaining
agent-message text in activity state. Continue writing the strict final result
to the existing temporary result path.

Use one monotonically increasing run id under an activity lock:

```python
self._activity = {
    "run_id": run_id,
    "stage": "opening_codex",
    "started_at": time.time(),
    "cancellable": True,
}
```

`cancel()` captures `(run_id, process)` under the lock, sets only that run's
event, and never consults a global process name or PID search. The runner calls
`terminate()`, waits a short grace interval, then calls `kill()` only if its
exact process remains alive.

- [ ] **Step 5: Preserve existing output and error contracts**

Verify byte caps before decoding, keep `_mcp_observed` proof, map a user stop to
`CoachRunError("cancelled")`, keep timeout as `timeout`, and clear activity,
process ownership, and cancellation state in `finally` for every path.

- [ ] **Step 6: Run Task 2 tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_coach.CodexCoachTests -v
```

Expected: all existing executor/privacy tests and new streaming/cancel tests pass.

- [ ] **Step 7: Record the approval-gated checkpoint**

Run `git status --short` and `git diff --check`. Do not commit without separate
approval.

### Task 3: Coach state, cancel HTTP contract, and schema simplification

**Files:**

- Modify: `token_meter/coach/service.py`
- Modify: `token_meter/coach/codex.py`
- Modify: `token_meter/app.py`
- Modify: `token_meter/coach/workspace/.agents/skills/token-meter-coach/SKILL.md`
- Test: `tests/test_coach.py`
- Test: `tests/test_coach_http.py`

**Interfaces:**

- Consumes: Task 2's `status()` and `cancel()` results.
- Produces: `CoachService.cancel() -> dict`, `POST /coach/cancel`, and a Chat
  schema with `message`, up to three evidence rows, one optional navigation
  action, and one optional goal draft but no generated prompt stack.

- [ ] **Step 1: Add failing service and HTTP cancellation tests**

Test successful cancel, idle idempotency, action-token/origin/content-type/body
guards, stable `cancelled` error copy, and a race where `/coach/ask` completes
just before `/coach/cancel`.

```python
def test_cancel_route_stops_only_active_coach_run(self):
    status, body = self.request(
        "POST", "/coach/cancel", {}, headers=self.action_headers(),
    )
    self.assertEqual(status, 200)
    self.assertEqual(body, {"ok": True, "changed": True})
    self.assertEqual(self.service.cancel_calls, 1)
```

- [ ] **Step 2: Add failing response-cardinality tests**

Remove `suggested_prompts` from a valid result fixture. Reject more than one
next action at the service boundary: when `goal_draft` is present, sanitize
`navigation` to `None`. Keep at most three content-free evidence rows.

- [ ] **Step 3: Run Task 3 tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_coach tests.test_coach_http -v
```

Expected: failures for missing cancel routing and the old required
`suggested_prompts` schema.

- [ ] **Step 4: Implement service/HTTP cancellation and bounded state**

Add `cancelled` to the allowlisted Coach error codes, return a calm
`Tok stopped this request.` browser message, and expose only
`stage`, `started_at`, and `cancellable` under `state.agent.activity`.

Wire `/coach/cancel` through the existing protected POST preflight. Make an idle
cancel a successful no-op and keep scheduler/interactive locking semantics.

- [ ] **Step 5: Simplify Chat output without weakening goal drafts**

Remove `suggested_prompts` from `CHAT_SCHEMA`, `_sanitize_chat`, fixtures, and
the bundled skill instructions. Preserve strict additional-property rejection,
plain text limits, navigation route allowlisting, goal normalization, and MCP
evidence proof.

- [ ] **Step 6: Run Task 3 tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_coach tests.test_coach_http -v
```

Expected: service, scheduler, schema, cancellation, and HTTP tests pass.

- [ ] **Step 7: Record the approval-gated checkpoint**

Run `git status --short` and `git diff --check`. Do not commit without separate
approval.

### Task 4: Replace the tabbed Coach with the Tok context lens

**Files:**

- Modify: `page.html`
- Modify: `tests/test_coach_dashboard.py`
- Modify: `tests/test_meter.py`

**Interfaces:**

- Consumes: `/coach/state`, `/coach/ask`, `/coach/cancel`, `/coach/goal`, and
  `/coach/weekly`.
- Produces: one docked/overlay conversation, `coachBeginTurn(content)`,
  `coachApplyActivity(activity)`, `coachResolveTurn(reply)`,
  `coachStopTurn()`, and `setCoachSheet("chat"|"goal")`.

- [ ] **Step 1: Replace old source-contract assertions with failing lens tests**

Assert the permanent tabs and badges are absent and the new shell is present:

```python
for removed in (
    "data-coach-tab=chat", "data-coach-tab=goal",
    "data-coach-tab=weekly", "id=coach-agent-state",
):
    self.assertNotIn(removed, self.page)

for required in (
    "id=coach-activity role=status aria-live=polite",
    "id=coach-goal-rail", "id=coach-goal-sheet",
    "id=coach-context-label", "id=coach-stop",
):
    self.assertIn(required, self.page)
```

Add Node harness tests proving submit immediately inserts one transient Tok row,
timer text is `aria-hidden`, repeated state polls update the same row, final and
error results replace it, and no working row enters `coachMessages` history.

- [ ] **Step 2: Add failing interaction and accessibility tests**

Cover these exact behaviors:

- the textarea remains enabled while `coachBusy` is true;
- send is disabled and stop becomes visible only after eight seconds when the
  server reports `cancellable=true`;
- cancel is idempotent and a late ask response cannot replace `Stopped`;
- goal rail is absent without a goal and opens the in-panel sheet with focus;
- Escape closes the sheet before it closes Tok;
- closing/reopening Tok preserves the live working row;
- evidence renders in one `<details>` source line with no more than three rows;
- a goal draft suppresses navigation so one next action remains;
- route changes clear the decorative focus class;
- reduced motion disables orbit, outline, and response transition animation.

- [ ] **Step 3: Run dashboard tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_coach_dashboard tests.test_meter -v
```

Expected: failures because the old tabbed shell and rendering behavior remain.

- [ ] **Step 4: Implement the one-line shell and goal sheet**

Remove the persistent role badge, readiness text, and tablist. Keep Tok's mark,
name, current route label, and close control in one row. Retain the chat view as
the default surface and convert the existing goal/weekly cards into one hidden
in-panel goal sheet reached from a thin goal rail beneath the composer.

The empty conversation contains one sentence and two prompt buttons. Hide it
after the first message. Do not persist message text or generated prose.

- [ ] **Step 5: Implement one mutable activity row**

`coachBeginTurn` appends the user message and one `.coachActivityMessage` with
`Starting Tok`. Start a visual timer but update the polite live region only when
the fixed stage enum changes. Poll `/coach/state` every 500-800 ms only while a
turn is active; stop polling on completion, error, stop, or page unload.

Map server enums through a literal object:

```javascript
const COACH_ACTIVITY_COPY={
 opening_codex:'Opening Codex',
 reading_token_meter:'Reading Token Meter',
 checking_evidence:'Checking the evidence',
};
```

Use a client turn id so stale ask, state, or cancel responses cannot mutate a
later turn.

- [ ] **Step 6: Implement compact answers and dashboard focus**

Render one paragraph, a closed `<details>` with a summary such as
`Token Meter evidence · 3 signals`, and at most one button. Apply a
`.coachContextFocus` class only to the current allowlisted route container while
the MCP-reading stage is active; remove it on every terminal path and route
change.

- [ ] **Step 7: Implement the delayed stop control**

Keep the textarea enabled but prevent a second submission. After eight seconds,
show stop only when the latest state says the exact active run is cancellable.
POST `{}` to `/coach/cancel` with the action token. Replace the activity row with
`Stopped` and one Retry button; ignore a later result for that cancelled client
turn id.

- [ ] **Step 8: Run Task 4 tests and embedded-JS parsing**

Run:

```bash
python3 -m unittest tests.test_coach_dashboard tests.test_meter -v
node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"
```

Expected: focused UI tests pass and Node prints `js ok`.

- [ ] **Step 9: Record the approval-gated checkpoint**

Run `git status --short` and `git diff --check`. Do not commit without separate
approval.

### Task 5: Documentation, packaging, and frozen-head verification

**Files:**

- Modify: `README.md`
- Modify: `specs/ARCHITECTURE.md`
- Modify: `specs/SECURITY.md`
- Modify: `specs/USER_GUIDE.md`
- Modify: `specs/plans/active.md` (ignored; never stage)
- Verify: `runtime-manifest.txt`

**Interfaces:**

- Consumes: the completed warm route, lifecycle projection, cancellation, and
  context-lens UI.
- Produces: accurate user/privacy/architecture documentation and one immutable
  source snapshot for independent gates.

- [ ] **Step 1: Update user and architecture documentation**

Document one conversation instead of tabs, compact evidence disclosure, active
goal rail, real stage copy, stop behavior, warm-route/fallback semantics, and
the unchanged Codex/OpenAI processing disclosure. State explicitly that final
provider latency is variable and the UI never reveals model reasoning.

- [ ] **Step 2: Run focused Coach and MCP gates**

Run:

```bash
python3 -m unittest \
  tests.test_coach tests.test_coach_http tests.test_coach_dashboard \
  tests.test_mcp_server tests.test_mcp_queries -v
```

Expected: all focused cases pass.

- [ ] **Step 3: Run static and full source gates**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/token-meter-pycache python3 -m py_compile \
  meter.py token_meter_mcp.py $(find token_meter -type f -name '*.py' -print)
python3 -m unittest discover -s tests -v
node -e "const fs=require('fs');const h=fs.readFileSync('page.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);new Function(m[1]);console.log('js ok')"
bash -n scripts/install scripts/install-linux scripts/install-launch-agent \
  scripts/install-systemd-user scripts/run-menubar scripts/run-token-meter-mcp \
  scripts/start-token-meter scripts/uninstall-launch-agent \
  scripts/uninstall-systemd-user scripts/update scripts/update-linux
swiftc menubar/TokenMeterMenuBar.swift -o /private/tmp/token-meter-menubar
TOKEN_METER_MENUBAR_SMOKE=1 /private/tmp/token-meter-menubar
git diff --check
```

Expected: all available checks pass; target-platform skips remain explicit.

- [ ] **Step 4: Run browser behavior and visual checks**

At 1440x900 and 1024x800, verify empty, conversation, immediate working,
MCP-reading, evidence-checking, stopped, error, answer, active-goal rail, goal
sheet, reduced-motion, keyboard, focus return, docked, and overlay states. Use a
controlled slow fake to inspect all states without depending on provider timing.

- [ ] **Step 5: Measure one real warm Codex/MCP turn**

Ask the same bounded Spend question used for the baseline. Record time to the
first projected stage, MCP start/completion, final answer, and total duration.
Verify the JSONL contains an observed Token Meter MCP call and the warm server
served the evidence request without invoking fallback discovery. Report the
measurement as one local sample, not a latency SLA.

- [ ] **Step 6: Install and verify exact source/runtime parity**

Run `./scripts/install`, then verify `/health`, `/menubar`, `/coach/state`, both
LaunchAgents, automatic start, expanded `runtime-manifest.txt` parity, and the
installer's printed uninstall command. Re-run the real Tok smoke against the
installed runtime.

- [ ] **Step 7: Freeze and fingerprint tracked files**

Record base, head, `git status --short`, `git diff --stat`, `git diff --check`,
and a deterministic hash of all changed tracked and untracked feature files.
Make no tracked edits after this point without invalidating every independent
gate.

- [ ] **Step 8: Run independent high-risk gates**

Route the immutable snapshot to one read-only tester and two read-only Token
Meter reviewers with distinct lenses:

- tester: functional, latency-path, browser, and installed-runtime evidence;
- reviewer 1: correctness, cancellation races, regressions, and maintainability;
- reviewer 2: privacy boundaries, evidence fidelity, accessibility, and UX
  density.

Require handoffs matching `.agents/workflow/handoff.schema.json`. Resolve every
finding and rerun invalidated gates after any edit.

- [ ] **Step 9: Stop before external delivery**

Report changed files, exact tests, local latency evidence, browser evidence,
installed-runtime parity, reviewer findings, and unverified Windows/Linux live
behavior. Do not commit, push, create a pull request, merge, release, or post
externally without explicit approval.
