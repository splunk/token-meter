# Claude Cost Correctness Design

## Data flow

The Claude adapter remains the owner of physical trace discovery and logical
message identity. It groups a canonical main transcript with nested agent
transcripts, loads the bounded group, and deduplicates assistant records by
`message.id` before normalization. Rows without a stable message or row ID are
kept distinct; only byte-identical physical rows are safe to deduplicate.

Normalized Claude usage keeps the existing aggregate fields and adds three
content-free counts:

- `cache_creation_5m_input_tokens`
- `cache_creation_1h_input_tokens`
- `cache_creation_unspecified_input_tokens`

It also carries internal completeness flags used by the compatibility
estimator. Existing runtime-neutral consumers continue to use the aggregate
cache-write count.

## Pricing

The four editable price fields remain backward compatible. `cache_write`
continues to mean the five-minute/default write rate. For trace-backed Claude
one-hour writes, the estimator derives the published two-times-input rate from
the effective input rate, including a user override. Fast mode replaces the
standard base quote only for catalog rules with published fast pricing. The
Opus 4.6 fast flag retains standard pricing, while unsupported fast requests
fail closed. Explicit global or US geography is accepted only on the published
Claude 4.6-and-later model range; the US-only multiplier applies after selecting
the effective rates. Web-search request cost is an additional `server_tools`
breakdown component and appears as its own dashboard tooltip row.

Invalid explicit billing detail makes that record unpriced. Missing historical
cache-duration detail is different: the existing cache-write rate is retained
as a compatibility estimate, while coverage is incomplete.

## Discovery and revision identity

Each legacy discovery record stores a private `_trace_paths` tuple. A native
`SessionSource` uses a `claude-jsonl-group` opaque locator when the tuple has
more than one member. The locator is never projected. A SHA-256 digest of the
sorted path signatures keeps `SourceRevision` bounded while invalidating on any
member change.

Claude Code grouping is structurally bounded to the main transcript and its
same-stem `subagents` directory. Claude Desktop local-agent grouping is bounded
to the metadata-owned session directory, or the one sibling directory in which
the declared CLI session ID is found. Audit logs and unrelated title/probe
traces are excluded.

## Compatibility and privacy

Aggregates and dashboard payloads retain existing total cache-write fields and
cost semantics. New duration fields are allowlisted only in normalized session
and MCP usage projections. Group paths remain private. Multi-file deletion is
denied by the existing ambiguous-session safety rule.
