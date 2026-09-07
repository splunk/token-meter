"""Dependency-injected, read-only session and trace query service."""

import datetime
import os
import time
from collections import defaultdict

from token_meter.mcp.contracts import (
    MCPQueryError,
    bounded_page,
    normalize_limit,
    normalize_string_list,
    read_cursor,
)
from token_meter.mcp.projections import (
    ALL_TRACE_SECTIONS,
    native_structure_projection,
    safe_identity,
    safe_number,
    session_projection,
    standardized_trace_projection,
)
from token_meter.mcp.schema import (
    DIMENSIONS,
    METRICS,
    schema_projection,
)


SESSION_SCOPES = {"current_project", "all"}
SESSION_STATES = {"current", "completed", "historical"}
TRACE_VIEWS = {"standardized", "native_structure"}
TRACE_EVENT_TYPES = {
    "start", "user", "model", "reasoning", "tool_call", "tool_result",
    "usage", "context", "compaction", "coordination", "complete", "error",
}
TRACE_PAGE_BYTES = 60_000
SCHEMA_SUBJECTS = {
    "sessions", "standardized_trace", "native_structure", "stats",
}
TOOL_METRICS = {"tool_calls", "tool_result_tokens"}
SESSION_METRICS = {"session_count", "context_latest", "context_peak"}


def _optional_string(value, field, maximum=240):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MCPQueryError(
            "invalid_argument", "{} must be a string".format(field),
        )
    value = value.strip()
    if len(value) > maximum:
        raise MCPQueryError(
            "invalid_argument", "{} is too long".format(field),
        )
    return value


def _required_string(value, field, maximum=240):
    result = _optional_string(value, field, maximum)
    if not result:
        raise MCPQueryError(
            "invalid_argument", "{} is required".format(field),
        )
    return result


def _timestamp(value, field):
    value = _optional_string(value, field, 64)
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        raise MCPQueryError(
            "invalid_argument", "{} must be an ISO-8601 timestamp".format(field),
        )


def _project_matches(project_key, candidate, requested):
    candidate = project_key(candidate)
    requested = project_key(requested)
    if not candidate or not requested:
        return False
    candidate = candidate.rstrip("/\\")
    requested = requested.rstrip("/\\")
    return (
        candidate == requested
        or requested.startswith(candidate + os.sep)
        or requested.startswith(candidate + "/")
        or requested.startswith(candidate + "\\")
    )


def normalize_trace_arguments(session_id, view, sections, execution,
                              event_types, limit):
    session_id = _required_string(session_id, "session_id")
    view = _optional_string(view, "view", 40) or "standardized"
    if view not in TRACE_VIEWS:
        raise MCPQueryError("invalid_argument", "view is unsupported")
    sections = normalize_string_list(
        sections,
        "sections",
        set(ALL_TRACE_SECTIONS),
        len(ALL_TRACE_SECTIONS),
        default=ALL_TRACE_SECTIONS,
    )
    event_types = normalize_string_list(
        event_types,
        "event_types",
        TRACE_EVENT_TYPES,
        len(TRACE_EVENT_TYPES),
    )
    if execution is not None:
        try:
            execution = int(execution)
        except (TypeError, ValueError):
            raise MCPQueryError(
                "invalid_argument", "execution must be a positive integer",
            )
        if isinstance(execution, bool) or execution < 1:
            raise MCPQueryError(
                "invalid_argument", "execution must be a positive integer",
            )
    return {
        "session_id": session_id,
        "view": view,
        "sections": sections,
        "execution": execution,
        "event_types": event_types,
        "limit": normalize_limit(limit, 50, 200),
    }


def _utc_day(timestamp):
    try:
        return datetime.datetime.fromtimestamp(
            float(timestamp), tz=datetime.timezone.utc,
        ).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _metric_grain(metrics, group_by):
    has_tool_metric = bool(set(metrics) & TOOL_METRICS)
    has_tool_dimension = bool(set(group_by) & {"tool_name", "tool_category"})
    has_execution_metric = any(
        name not in TOOL_METRICS and name not in SESSION_METRICS
        for name in metrics
    )
    if (has_tool_metric or has_tool_dimension) and has_execution_metric:
        raise MCPQueryError(
            "invalid_argument",
            "tool metrics cannot be combined with execution metrics",
        )
    if has_tool_dimension and not has_tool_metric:
        raise MCPQueryError(
            "invalid_argument", "tool dimensions require a tool metric",
        )
    return "tool" if (has_tool_metric or has_tool_dimension) else "execution"


def _record_dimensions(session, model=None, timestamp=None, tool=None):
    tool = tool or {}
    return {
        "runtime": session.get("runtime"),
        "client": session.get("client"),
        "model_provider": session.get("model_provider"),
        "model": model or session.get("model"),
        "day": _utc_day(timestamp or session.get("started_at")),
        "session_id": session.get("id"),
        "tool_category": tool.get("category"),
        "tool_name": tool.get("name"),
    }


def _execution_records(source, state):
    projection = standardized_trace_projection(
        source,
        state,
        ("session", "executions", "tools", "context"),
        None,
        (),
    )
    session = projection.get("session") or {}
    context = projection.get("context") or {}
    rows = []
    for execution in projection.get("executions") or []:
        tokens = execution.get("tokens") or {}
        timing = execution.get("timing") or {}
        counts = execution.get("counts") or {}
        record = {
            "session_id": session.get("id"),
            "timestamp": execution.get("timestamp"),
            "dimensions": _record_dimensions(
                session, execution.get("model"), execution.get("timestamp"),
            ),
            "metrics": {
                "execution_count": 1,
                "input_tokens": tokens.get("input"),
                "output_tokens": tokens.get("output"),
                "cache_read_tokens": tokens.get("cache_read"),
                "cache_write_tokens": tokens.get("cache_write"),
                "cache_write_5m_tokens": tokens.get("cache_write_5m"),
                "cache_write_1h_tokens": tokens.get("cache_write_1h"),
                "cache_write_unspecified_tokens": tokens.get(
                    "cache_write_unspecified"
                ),
                "total_tokens": tokens.get("total"),
                "cost_usd": execution.get("cost_usd"),
                "active_seconds": timing.get("active_seconds"),
                "wait_seconds": timing.get("wait_seconds"),
                "ttft_seconds": timing.get("ttft_seconds"),
                "model_calls": counts.get("model_calls"),
                "attempts": counts.get("attempts"),
                "retries": counts.get("retries"),
                "failed_attempts": counts.get("failed_attempts"),
                "context_latest": context.get("latest"),
                "context_peak": context.get("peak"),
            },
        }
        rows.append(record)
    if not rows:
        rows.append({
            "session_id": session.get("id"),
            "timestamp": None,
            "dimensions": _record_dimensions(session),
            "metrics": {
                "execution_count": 0,
                "context_latest": context.get("latest"),
                "context_peak": context.get("peak"),
            },
        })
    return rows, projection


def _tool_records(source, state):
    projection = standardized_trace_projection(
        source,
        state,
        ("session", "context"),
        None,
        (),
    )
    session = projection.get("session") or {}
    context = projection.get("context") or {}
    rows = []
    for execution in state.get("executions") or []:
        if not isinstance(execution, dict):
            continue
        timestamp = safe_number(execution.get("ts"))
        model = safe_identity(execution.get("model"), 160) or None
        for tool in execution.get("tools") or []:
            if not isinstance(tool, dict):
                continue
            name = safe_identity(tool.get("name"))
            if not name:
                continue
            namespace = safe_identity(tool.get("namespace")) or None
            category = safe_identity(
                tool.get("category") or namespace or tool.get("kind")
            ) or None
            rows.append({
                "session_id": session.get("id"),
                "timestamp": timestamp,
                "dimensions": _record_dimensions(
                    session,
                    model,
                    timestamp,
                    tool={"name": name, "category": category},
                ),
                "metrics": {
                    "tool_calls": 1,
                    "tool_result_tokens": safe_number(
                        tool.get("output_tokens"), integer=True,
                    ),
                    "context_latest": context.get("latest"),
                    "context_peak": context.get("peak"),
                },
            })
    if not rows:
        aggregate = standardized_trace_projection(
            source, state, ("tools",), None, (),
        )
        for tool in aggregate.get("tools") or []:
            category = tool.get("category") or tool.get("namespace")
            rows.append({
                "session_id": session.get("id"),
                "timestamp": None,
                "dimensions": _record_dimensions(
                    session, tool={**tool, "category": category},
                ),
                "metrics": {
                    "tool_calls": tool.get("calls"),
                    "tool_result_tokens": tool.get("output_tokens"),
                    "context_latest": context.get("latest"),
                    "context_peak": context.get("peak"),
                },
            })
    return rows, projection


def _record_matches_filters(record, filters):
    dimensions = record.get("dimensions") or {}
    if filters["model"] and dimensions.get("model") != filters["model"]:
        return False
    timestamp = record.get("timestamp")
    if filters["start"] is not None and (
        timestamp is None or timestamp < filters["start"]
    ):
        return False
    if filters["end"] is not None and (
        timestamp is None or timestamp > filters["end"]
    ):
        return False
    return True


def _aggregate_records(records, metrics, group_by):
    buckets = {}
    for record in records:
        dimensions = record.get("dimensions") or {}
        key = tuple(dimensions.get(name) for name in group_by)
        bucket = buckets.setdefault(key, {
            "dimensions": {
                name: dimensions.get(name) for name in group_by
            },
            "metrics": {},
            "coverage": {},
            "sessions": set(),
            "seen_session_metrics": defaultdict(set),
        })
        session_id = record.get("session_id")
        if session_id:
            bucket["sessions"].add(session_id)
        for metric in metrics:
            coverage = bucket["coverage"].setdefault(
                metric, {"covered": 0, "unavailable": 0},
            )
            if metric == "session_count":
                continue
            value = (record.get("metrics") or {}).get(metric)
            if metric in {"context_latest", "context_peak"}:
                if session_id in bucket["seen_session_metrics"][metric]:
                    continue
                bucket["seen_session_metrics"][metric].add(session_id)
            if value is None:
                coverage["unavailable"] += 1
                continue
            coverage["covered"] += 1
            reduction = METRICS[metric].get("reduction")
            if reduction == "max":
                current = bucket["metrics"].get(metric)
                bucket["metrics"][metric] = value if current is None else max(current, value)
            else:
                bucket["metrics"][metric] = bucket["metrics"].get(metric, 0) + value
    output = []
    for bucket in buckets.values():
        if "session_count" in metrics:
            count = len(bucket["sessions"])
            bucket["metrics"]["session_count"] = count
            bucket["coverage"]["session_count"] = {
                "covered": count,
                "unavailable": 0,
            }
        for metric in metrics:
            if metric not in bucket["metrics"]:
                bucket["metrics"][metric] = None
            value = bucket["metrics"][metric]
            if isinstance(value, float):
                bucket["metrics"][metric] = round(value, 6)
        bucket.pop("sessions", None)
        bucket.pop("seen_session_metrics", None)
        output.append(bucket)
    return output


class MCPQueryService:
    """Query local evidence through injected application-owned callbacks."""

    def __init__(self, *, sources, find_session, summary, state, revision,
                 project_key, runtime_descriptors, now=None):
        self._sources = sources
        self._find_session = find_session
        self._summary = summary
        self._state = state
        self._revision = revision
        self._project_key = project_key
        self._runtime_descriptors = runtime_descriptors
        self._now = now or time.time

    def sessions(self, scope="current_project", runtime=None, client=None,
                 model=None, state=None, start=None, end=None, cursor=None,
                 limit=None, caller=None):
        scope = _optional_string(scope, "scope", 40) or "current_project"
        if scope not in SESSION_SCOPES:
            raise MCPQueryError("invalid_argument", "scope is unsupported")
        filters = {
            "runtime": _optional_string(runtime, "runtime", 120),
            "client": _optional_string(client, "client", 120),
            "model": _optional_string(model, "model", 160),
            "state": _optional_string(state, "state", 40),
            "start": _timestamp(start, "start"),
            "end": _timestamp(end, "end"),
        }
        if filters["state"] and filters["state"] not in SESSION_STATES:
            raise MCPQueryError("invalid_argument", "state is unsupported")
        if (
            filters["start"] is not None
            and filters["end"] is not None
            and filters["start"] > filters["end"]
        ):
            raise MCPQueryError(
                "invalid_argument", "start must not be later than end",
            )
        limit = normalize_limit(limit, 20, 100)
        caller = caller or {}
        caller_project = str(caller.get("project") or caller.get("cwd") or "")
        if scope == "current_project" and not self._project_key(caller_project):
            raise MCPQueryError(
                "invalid_argument", "current project context is unavailable",
            )
        source_rows = list(self._sources() or ())
        projected = []
        matched_sources = []
        for source in source_rows:
            if scope == "current_project" and not _project_matches(
                self._project_key, source.get("project"), caller_project,
            ):
                continue
            if filters["runtime"] and source.get("provider") != filters["runtime"]:
                continue
            if filters["client"] and (
                source.get("client") or source.get("provider")
            ) != filters["client"]:
                continue
            summary = self._summary(source) or {}
            row = session_projection(source, summary, self._now())
            if filters["model"] and row.get("model") != filters["model"]:
                continue
            if filters["state"] and row.get("state") != filters["state"]:
                continue
            activity = row.get("last_activity_at")
            if filters["start"] is not None and (
                activity is None or activity < filters["start"]
            ):
                continue
            if filters["end"] is not None and (
                activity is None or activity > filters["end"]
            ):
                continue
            projected.append(row)
            matched_sources.append(source)
        ordering = sorted(
            range(len(projected)),
            key=lambda index: (
                -(projected[index].get("last_activity_at") or 0),
                projected[index].get("id") or "",
            ),
        )
        projected = [projected[index] for index in ordering]
        matched_sources = [matched_sources[index] for index in ordering]
        query = {
            "scope": scope,
            "filters": filters,
            "limit": limit,
        }
        revision = tuple(
            (row.get("id"), self._revision(source))
            for row, source in zip(projected, matched_sources)
        )
        offset = read_cursor(cursor, query, revision) if cursor else 0
        page = bounded_page(
            projected,
            offset=offset,
            limit=limit,
            query=query,
            revision=revision,
            max_bytes=TRACE_PAGE_BYTES,
        )
        return {
            "ok": True,
            "as_of": self._now(),
            "data_scope": "session_inventory",
            "scope": scope,
            "sessions": page.pop("items"),
            "page": page,
        }

    def trace(self, session_id, view="standardized", sections=None,
              execution=None, event_types=None, cursor=None, limit=None):
        arguments = normalize_trace_arguments(
            session_id, view, sections, execution, event_types, limit,
        )
        return self._trace_result(arguments, cursor)

    def stats(self, metrics, group_by=None, runtime=None, client=None,
              model=None, state=None, session_id=None, start=None, end=None,
              sort_by=None, sort_direction="desc", cursor=None, limit=None):
        metrics = normalize_string_list(
            metrics, "metrics", set(METRICS), 8,
        )
        if not metrics:
            raise MCPQueryError(
                "invalid_argument", "metrics must contain at least one value",
            )
        group_by = normalize_string_list(
            group_by, "group_by", set(DIMENSIONS), 3,
        )
        grain = _metric_grain(metrics, group_by)
        filters = {
            "runtime": _optional_string(runtime, "runtime", 120),
            "client": _optional_string(client, "client", 120),
            "model": _optional_string(model, "model", 160),
            "state": _optional_string(state, "state", 40),
            "session_id": _optional_string(session_id, "session_id", 240),
            "start": _timestamp(start, "start"),
            "end": _timestamp(end, "end"),
        }
        if filters["state"] and filters["state"] not in SESSION_STATES:
            raise MCPQueryError("invalid_argument", "state is unsupported")
        if (
            filters["start"] is not None
            and filters["end"] is not None
            and filters["start"] > filters["end"]
        ):
            raise MCPQueryError(
                "invalid_argument", "start must not be later than end",
            )
        sort_by = _optional_string(sort_by, "sort_by", 80) or metrics[0]
        if sort_by not in metrics:
            raise MCPQueryError(
                "invalid_argument", "sort_by must name an included metric",
            )
        sort_direction = _optional_string(
            sort_direction, "sort_direction", 8,
        ) or "desc"
        if sort_direction not in {"asc", "desc"}:
            raise MCPQueryError(
                "invalid_argument", "sort_direction must be asc or desc",
            )
        limit = normalize_limit(limit, 20, 100)
        records = []
        matched_sources = []
        for source in list(self._sources() or ()):
            if filters["runtime"] and source.get("provider") != filters["runtime"]:
                continue
            if filters["client"] and (
                source.get("client") or source.get("provider")
            ) != filters["client"]:
                continue
            if filters["session_id"] and source.get("id") != filters["session_id"]:
                continue
            summary = self._summary(source) or {}
            listed = session_projection(source, summary, self._now())
            if filters["state"] and listed.get("state") != filters["state"]:
                continue
            detailed = self._state(source)
            if detailed:
                source_records, _projection = (
                    _tool_records(source, detailed)
                    if grain == "tool"
                    else _execution_records(source, detailed)
                )
                source_records = [
                    record for record in source_records
                    if _record_matches_filters(record, filters)
                ]
            elif grain == "execution":
                source_records = [{
                    "session_id": listed.get("id"),
                    "timestamp": listed.get("last_activity_at"),
                    "dimensions": _record_dimensions(
                        listed, timestamp=listed.get("last_activity_at"),
                    ),
                    "metrics": {"execution_count": None},
                }]
                source_records = [
                    record for record in source_records
                    if _record_matches_filters(record, filters)
                ]
            else:
                source_records = []
            if source_records:
                matched_sources.append(source)
                records.extend(source_records)
        groups = _aggregate_records(records, metrics, group_by)
        totals_rows = _aggregate_records(records, metrics, ())
        if totals_rows:
            totals = totals_rows[0]["metrics"]
            coverage = totals_rows[0]["coverage"]
        else:
            totals = {
                metric: (0 if metric == "session_count" else None)
                for metric in metrics
            }
            coverage = {
                metric: {"covered": 0, "unavailable": 0}
                for metric in metrics
            }

        def sort_key(row):
            value = row["metrics"].get(sort_by)
            unavailable = value is None
            numeric = float(value or 0)
            primary = numeric if sort_direction == "asc" else -numeric
            dimensions = tuple(
                str(row["dimensions"].get(name) or "") for name in group_by
            )
            return unavailable, primary, dimensions

        groups.sort(key=sort_key)
        query = {
            "metrics": metrics,
            "group_by": group_by,
            "filters": filters,
            "sort_by": sort_by,
            "sort_direction": sort_direction,
            "limit": limit,
        }
        revision = tuple(
            (source.get("id"), self._revision(source))
            for source in matched_sources
        )
        offset = read_cursor(cursor, query, revision) if cursor else 0
        page = bounded_page(
            groups,
            offset=offset,
            limit=limit,
            query=query,
            revision=revision,
            max_bytes=TRACE_PAGE_BYTES,
        )
        return {
            "ok": True,
            "schema_version": "1.0",
            "as_of": self._now(),
            "data_scope": "standardized_statistics",
            "metrics": list(metrics),
            "group_by": list(group_by),
            "totals": totals,
            "coverage": coverage,
            "groups": page.pop("items"),
            "page": page,
        }

    def schema(self, subject="stats", runtime=None):
        subject = _optional_string(subject, "subject", 40) or "stats"
        if subject not in SCHEMA_SUBJECTS:
            raise MCPQueryError("invalid_argument", "subject is unsupported")
        runtime = _optional_string(runtime, "runtime", 120)
        result = schema_projection(
            subject, self._runtime_descriptors(), runtime=runtime,
        )
        result.update({
            "as_of": self._now(),
            "data_scope": "query_schema",
        })
        return result

    def _trace_result(self, arguments, cursor):
        sources = list(self._sources() or ())
        source = self._find_session(arguments["session_id"], sources)
        if source is None:
            raise MCPQueryError(
                "session_not_found", "the requested session was not found",
            )
        state = self._state(source)
        if not state:
            raise MCPQueryError(
                "evidence_unavailable", "detailed session evidence is unavailable",
            )
        query = {
            "session_id": arguments["session_id"],
            "view": arguments["view"],
            "sections": arguments["sections"],
            "execution": arguments["execution"],
            "event_types": arguments["event_types"],
            "limit": arguments["limit"],
        }
        revision = self._revision(source)
        offset = read_cursor(cursor, query, revision) if cursor else 0
        if arguments["view"] == "native_structure":
            records = native_structure_projection(
                source, state, arguments["execution"], arguments["event_types"],
            )
            page = bounded_page(
                records,
                offset=offset,
                limit=arguments["limit"],
                query=query,
                revision=revision,
                max_bytes=TRACE_PAGE_BYTES,
            )
            return {
                "ok": True,
                "schema_version": "1.0",
                "as_of": self._now(),
                "data_scope": "selected_session_native_structure",
                "view": arguments["view"],
                "records": page.pop("items"),
                "page": page,
            }
        projection = standardized_trace_projection(
            source,
            state,
            arguments["sections"],
            arguments["execution"],
            arguments["event_types"],
        )
        flat = []
        for section in ("executions", "events", "tools"):
            for item in projection.get(section) or []:
                flat.append({"section": section, "value": item})
            if section in projection:
                projection[section] = []
        page = bounded_page(
            flat,
            offset=offset,
            limit=arguments["limit"],
            query=query,
            revision=revision,
            max_bytes=TRACE_PAGE_BYTES,
        )
        for item in page.pop("items"):
            projection[item["section"]].append(item["value"])
        projection.update({
            "ok": True,
            "as_of": self._now(),
            "data_scope": "selected_session_standardized_trace",
            "view": arguments["view"],
            "page": page,
        })
        return projection
