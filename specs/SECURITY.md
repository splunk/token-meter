# Security Policy

The canonical component and privacy-boundary map is
[ARCHITECTURE.md](ARCHITECTURE.md#privacy-and-security-invariants).

## Supported Versions

Token Meter is a small local tool. Security fixes should target the current
`main` branch unless the project later publishes versioned releases.

## Reporting A Vulnerability

Please report security issues privately to the project maintainers rather than
opening a public issue with exploit details.

When reporting, include:

- The affected commit or release.
- Your operating system and Python version.
- Steps to reproduce.
- Any relevant error messages.

Do not attach Claude Code, Codex, or Cursor session data unless you have reviewed
and redacted it. Local transcripts, Cursor's shared state database, and request
logs may contain prompts, responses, project paths, tool outputs, and other
sensitive information.

## Security Model

Token Meter is intended to run on your own machine and bind to `127.0.0.1`.
It should not expose the dashboard to public networks. The project should not
send logs, prompts, responses, project paths, token counts, or costs to external
services.

The Git page is a local-only Git reader. It inspects bounded remote-tracking reflogs
for successful-push markers and never fetches, pulls, pushes, updates refs, or
contacts a remote. Its SQLite ledger stores salted repository/object keys,
timestamped clear baselines, numeric text-line totals, and coverage state only.
Public project suffixes use the same per-machine salt. It must not
persist or project paths, remote/ref names, identities, messages, filenames,
diff content, raw object IDs, subprocess output, or exceptions.
Runtime-specific provider resources are not automatically safe public model
identifiers. For example, a Pi application-profile reference can contain
account-bearing segments. Such a value must never be projected verbatim or used
to infer a priced foundation model; it is replaced with a non-account generic
label before dashboard, native, or MCP projection.

The menu-bar quota view is a bounded exception to otherwise local processing.
It makes read-only account-usage requests to the provider matching the local
credential: Codex through its local app-server or signed-in usage API, Claude
through Anthropic's OAuth usage endpoint for first-party OAuth accounts, and
Cursor through its account usage summary. It must never send a provider token
to a different provider, persist copied credentials, expose credentials or raw
response bodies through localhost, or include them in logs and errors. Provider
requests use fixed HTTPS endpoints, hard timeouts, bounded response sizes, and
sanitized failures. Third-party Claude auth must fail closed as unavailable.

The optional `tokenmeter` MCP server is also local and read-only. It uses stdio,
does not open another listening port, and returns bounded derived evidence. It
must never return prompts, messages, reasoning text, tool arguments, tool
results, credentials, environment variables, configuration values, or log
paths. `check` detail is limited to the caller's matched current runtime and
project. Query tools may return opaque session IDs and content-free historical
evidence, but never session titles, project names, source paths, or native
provider payloads. Capability names are returned only when capability review is
explicitly requested.

The `sessions`, `trace`, `stats`, `goal`, and `schema` tools use strict input schemas,
positive output allowlists, and fixed limits. Session IDs identify only an
already discovered local source. Pagination cursors contain hashed query and
revision bindings, not paths or trace content; a changed revision invalidates
the cursor. Serialized query pages are capped at 65,536 bytes. The
`native_structure` view is not raw trace content: it admits only fixed native
type/subtype enums plus bounded numeric, status, model, and tool fields. Raw
prompts, responses, tool payloads, account data, and trace paths are not
available through MCP.

The dashboard agent Tok is a separate, explicit provider-processing boundary. A
sent message starts the user's signed-in Codex CLI in an ephemeral temporary
home and non-repository workspace. User configuration, rules, skills, plugins,
memories, and prior Codex sessions are not loaded. General shell, file, browser,
app, image, and sub-agent capabilities are disabled; the run receives only the
bundled Tok skill and an allowlist of read-only `tokenmeter` MCP tools. The
CLI is read-only, never asks for approval, has fixed time and output limits, and
must return a validated schema. Token Meter accepts an evidence-bearing answer
only after a completed `tokenmeter` MCP tool call is present in the CLI event
stream. Free text in that stream, stderr, paths, and raw errors are never sent
to the browser.

Tok messages are held only in browser memory. Token Meter settings contain at
most one allowlisted goal contract, numeric baseline/current snapshots, bounded
review timestamps and status codes, and one recommendation enum; they contain
neither the natural-language request nor Codex prose. The message and bounded
MCP results used for a response may be processed by OpenAI under the user's
Codex account. This is opt-in per chat request and per weekly-review setting; it
does not make raw traces available to Codex.

Coach-launched MCP may use the local `/coach/evidence` bridge only to avoid a
second local discovery scan. Its sanitized isolated environment receives two
separate per-process credentials: the normal action token and a dedicated
Coach-evidence token, sent only in the `X-Token-Meter-Action` and
`X-Token-Meter-Coach-Evidence` internal loopback
headers, never command arguments or model input. The route requires both tokens
alongside local-origin, JSON, request-size, tool-name, and argument validation,
dispatches through the same read-only allowlists as stdio MCP, and returns only
the usual bounded sanitized projection; neither token is returned. A failed,
malformed, oversized, or unavailable bridge response
falls back once to the existing local read. It never returns the dashboard
state, raw trace data, event text, or MCP payloads.

The flagged-tool projection names observed tool and MCP-server names with
bounded counts and a locally generated reason. Token Meter's own diagnostic tools
are excluded, the `scope` recommendation is dropped entirely, and no stored reason
string is forwarded, because that string can contain a local project path.

Tok's visible lifecycle is likewise content-free: it can name only fixed
execution boundaries, the name of the read-only MCP tool currently running, a
clamped count of completed evidence readings, and a visual elapsed timer. The
tool name is re-validated before it reaches the browser against the same
`MCP_TOOLS` allowlist the stdio interface uses, imported rather than copied, so
it can only ever be one of those fixed labels. The
lifecycle never reveals model reasoning, raw JSONL events, tool
arguments/results, or a provider-latency guarantee. Stop terminates only the
active ephemeral Coach child and does not target unrelated Codex or MCP
processes.

When an MCP tool is called, the bounded derived result is handed to the
connected Codex or Claude client. That client may send the result to its model
provider under the client's own terms and configuration. “Local analysis” means
Token Meter does not upload trace content or derived analytics; it does not mean
data returned to an explicitly connected AI client necessarily remains on the
machine. The separate quota requests above contain authentication and ordinary
provider usage-request metadata, but no local transcript content.

Dashboard connection actions are protected by the existing local-origin action
token and fixed subprocess argument vectors. They may add or remove only the
exact user-level MCP entry named `tokenmeter`, must refuse a conflicting entry,
and must verify the persisted state before reporting success. The MCP tools
themselves cannot change configuration, budgets, sessions, or Token Meter state.

Dashboard session deletion uses the same local-origin action token and accepts
only a canonical ID from the currently discovered session inventory. It moves
the exact discovered `.jsonl` file to the system Trash with collision-safe naming;
it does not delete provider metadata, project files, configuration, Cursor's
shared `state.vscdb` database/WAL, or Cursor request logs. The UI
requires an explicit confirmation and warns when the target appears to be the
live session.

Cursor enrichment is strictly read-only. Token Meter opens `state.vscdb` with
SQLite `mode=ro` and `query_only`, retains WAL visibility for a live Cursor
process, uses a short busy timeout, and falls back to the per-session transcript
if the shared database is missing, locked, corrupt, or has an unsupported
schema. Request-trace parsing accepts only a bounded allowlist of completed span
names and derives timing fields; it does not expose raw request-log contents.
