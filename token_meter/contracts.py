"""Immutable contracts shared across Token Meter architecture boundaries.

These objects deliberately exclude raw prompts, responses, reasoning, tool
payloads, credentials, and account data. Internal source locators are kept in
opaque values and must pass through explicit public projections.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import (
    FrozenSet,
    Generic,
    Mapping,
    Optional,
    Tuple,
    TypeVar,
)


T = TypeVar("T")


def _require_identifier(value, field_name):
    value = str(value or "").strip()
    if not value:
        raise ValueError("{} must not be empty".format(field_name))
    if len(value) > 120:
        raise ValueError("{} is too long".format(field_name))
    return value


class EvidenceBasis(Enum):
    """How strongly a normalized value is supported by source evidence."""

    MEASURED = "measured"
    INFERRED = "inferred"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class EvidenceValue(Generic[T]):
    """A value paired with its evidence basis.

    ``None`` is reserved for unavailable evidence. A measured zero therefore
    remains observably different from a value the source cannot provide.
    """

    value: Optional[T]
    basis: EvidenceBasis

    def __post_init__(self):
        if self.basis is EvidenceBasis.UNAVAILABLE and self.value is not None:
            raise ValueError("unavailable evidence must not carry a value")
        if self.basis is not EvidenceBasis.UNAVAILABLE and self.value is None:
            raise ValueError("available evidence must carry a value")

    @classmethod
    def unavailable(cls):
        return cls(None, EvidenceBasis.UNAVAILABLE)


@dataclass(frozen=True)
class ModelRef:
    provider_id: str
    model_id: str
    variant: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(
            self, "provider_id", _require_identifier(self.provider_id, "provider_id")
        )
        model_id = str(self.model_id or "").strip()
        if not model_id:
            raise ValueError("model_id must not be empty")
        if len(model_id) > 160:
            raise ValueError("model_id is too long")
        object.__setattr__(self, "model_id", model_id)
        if self.variant is not None:
            variant = str(self.variant).strip()
            object.__setattr__(self, "variant", variant or None)


@dataclass(frozen=True)
class PriceQuery:
    """Runtime-neutral request for one model price at one observation time."""

    model: ModelRef
    observed_at: Optional[datetime] = None

    def __post_init__(self):
        if not isinstance(self.model, ModelRef):
            raise ValueError("model must be a ModelRef")
        if self.observed_at is not None and not isinstance(self.observed_at, datetime):
            raise ValueError("observed_at must be a datetime or None")


@dataclass(frozen=True)
class PriceQuote:
    """A complete per-million-token quote or an explicit unavailable result."""

    model: ModelRef
    input_per_million: Optional[float]
    output_per_million: Optional[float]
    cache_read_per_million: Optional[float]
    cache_write_per_million: Optional[float]
    basis: EvidenceBasis
    matched_rule: Optional[str] = None
    _legacy_price: Optional[Mapping[str, float]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self):
        values = (
            self.input_per_million,
            self.output_per_million,
            self.cache_read_per_million,
            self.cache_write_per_million,
        )
        if self.basis is EvidenceBasis.UNAVAILABLE:
            if any(value is not None for value in values):
                raise ValueError("unavailable price quotes must not carry rates")
            if self.matched_rule is not None:
                raise ValueError("unavailable price quotes must not carry a matched rule")
            object.__setattr__(self, "_legacy_price", None)
        elif any(value is None for value in values):
            raise ValueError("available price quotes must carry every rate")
        else:
            object.__setattr__(self, "_legacy_price", {
                "input": float(self.input_per_million),
                "output": float(self.output_per_million),
                "cache_write": float(self.cache_write_per_million),
                "cache_read": float(self.cache_read_per_million),
            })

    @classmethod
    def unavailable(cls, model):
        return cls(
            model=model,
            input_per_million=None,
            output_per_million=None,
            cache_read_per_million=None,
            cache_write_per_million=None,
            basis=EvidenceBasis.UNAVAILABLE,
            matched_rule=None,
        )

    @property
    def available(self):
        return self.basis is not EvidenceBasis.UNAVAILABLE

    def to_legacy_price(self):
        return self._legacy_price


@dataclass(frozen=True)
class RuntimeModelKey:
    """Default aggregation key; identical models stay scoped by runtime."""

    runtime_id: str
    model: ModelRef

    def __post_init__(self):
        object.__setattr__(
            self, "runtime_id", _require_identifier(self.runtime_id, "runtime_id")
        )


@dataclass(frozen=True)
class SourceLocator:
    """Adapter-owned source identity that must never be publicly serialized."""

    kind: str
    value: str

    def __post_init__(self):
        object.__setattr__(self, "kind", _require_identifier(self.kind, "locator kind"))
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("locator value must not be empty")


@dataclass(frozen=True)
class SourceRevision:
    """Bounded immutable revision components used for cache invalidation."""

    parts: Tuple[str, ...]

    def __post_init__(self):
        parts = tuple(str(part) for part in self.parts)
        if len(parts) > 16:
            raise ValueError("source revision has too many components")
        if any(len(part) > 240 for part in parts):
            raise ValueError("source revision component is too long")
        object.__setattr__(self, "parts", parts)


@dataclass(frozen=True)
class SessionSource:
    runtime_id: str
    client_id: str
    session_id: str
    display_label: str
    project: Optional[str]
    locator: SourceLocator
    activity_mtime: float
    revision: SourceRevision
    model_ref: Optional[ModelRef]
    account_provider_id: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(
            self, "runtime_id", _require_identifier(self.runtime_id, "runtime_id")
        )
        object.__setattr__(
            self, "client_id", _require_identifier(self.client_id, "client_id")
        )
        object.__setattr__(
            self, "session_id", _require_identifier(self.session_id, "session_id")
        )
        label = str(self.display_label or "").strip()
        if not label or len(label) > 120:
            raise ValueError("display_label must be between 1 and 120 characters")
        object.__setattr__(self, "display_label", label)
        try:
            activity_mtime = float(self.activity_mtime)
        except (TypeError, ValueError):
            raise ValueError("activity_mtime must be numeric")
        object.__setattr__(self, "activity_mtime", activity_mtime)
        if self.account_provider_id is not None:
            object.__setattr__(
                self,
                "account_provider_id",
                _require_identifier(self.account_provider_id, "account_provider_id"),
            )


@dataclass(frozen=True)
class UsageEvidence:
    input_tokens: EvidenceValue
    output_tokens: EvidenceValue
    cache_read_tokens: EvidenceValue
    cache_write_tokens: EvidenceValue
    cost_usd: EvidenceValue
    cache_write_5m_tokens: EvidenceValue = field(
        default_factory=EvidenceValue.unavailable
    )
    cache_write_1h_tokens: EvidenceValue = field(
        default_factory=EvidenceValue.unavailable
    )
    cache_write_unspecified_tokens: EvidenceValue = field(
        default_factory=EvidenceValue.unavailable
    )

    @classmethod
    def unavailable(cls):
        unavailable = EvidenceValue.unavailable
        return cls(
            input_tokens=unavailable(),
            output_tokens=unavailable(),
            cache_read_tokens=unavailable(),
            cache_write_tokens=unavailable(),
            cost_usd=unavailable(),
        )


@dataclass(frozen=True)
class TimingEvidence:
    active_seconds: EvidenceValue
    wait_seconds: EvidenceValue
    ttft_seconds: EvidenceValue

    @classmethod
    def unavailable(cls):
        unavailable = EvidenceValue.unavailable
        return cls(
            active_seconds=unavailable(),
            wait_seconds=unavailable(),
            ttft_seconds=unavailable(),
        )


@dataclass(frozen=True)
class ToolEvent:
    """Payload-free normalized tool evidence."""

    name: str
    category: str
    status: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "name", _require_identifier(self.name, "tool name"))
        object.__setattr__(
            self, "category", _require_identifier(self.category, "tool category")
        )


@dataclass(frozen=True)
class TurnSummary:
    index: int
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    output_tokens: EvidenceValue


@dataclass(frozen=True)
class PricingBasis:
    model: ModelRef
    observed_at: Optional[datetime]
    approximate: bool
    matched_rule: Optional[str] = None


@dataclass(frozen=True)
class ParseWarning:
    code: str
    message: str

    def __post_init__(self):
        object.__setattr__(self, "code", _require_identifier(self.code, "warning code"))
        message = " ".join(str(self.message or "").split())
        if not message or len(message) > 240:
            raise ValueError("warning message must be between 1 and 240 characters")
        object.__setattr__(self, "message", message)


class DetailLevel(Enum):
    SUMMARY = "summary"
    FULL = "full"


@dataclass(frozen=True)
class NormalizedSession:
    source: SessionSource
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    usage: UsageEvidence
    timing: TimingEvidence
    tools: Tuple[ToolEvent, ...]
    turns: Tuple[TurnSummary, ...]
    pricing_basis: Optional[PricingBasis]
    capabilities: FrozenSet[str]
    warnings: Tuple[ParseWarning, ...]
    detail: DetailLevel

    def __post_init__(self):
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "turns", tuple(self.turns))
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "warnings", tuple(self.warnings))


class DeletionDisposition(Enum):
    DENY = "deny"
    TRASH = "trash"


@dataclass(frozen=True)
class DeletionPlan:
    disposition: DeletionDisposition
    reason: str
    targets: Tuple[SourceLocator, ...] = ()

    def __post_init__(self):
        reason = " ".join(str(self.reason or "").split())
        if not reason or len(reason) > 160:
            raise ValueError("deletion reason must be between 1 and 160 characters")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "targets", tuple(self.targets))
        if self.disposition is DeletionDisposition.DENY and self.targets:
            raise ValueError("denied deletion plans must not contain targets")

    @classmethod
    def deny(cls, reason):
        return cls(DeletionDisposition.DENY, reason, ())


@dataclass(frozen=True)
class RuntimeDescriptor:
    runtime_id: str
    label: str
    capabilities: FrozenSet[str]
    symbol: str
    color: str
    account_provider_id: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(
            self, "runtime_id", _require_identifier(self.runtime_id, "runtime_id")
        )
        label = str(self.label or "").strip()
        if not label or len(label) > 120:
            raise ValueError("runtime label must be between 1 and 120 characters")
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "symbol", _require_identifier(self.symbol, "symbol"))
        object.__setattr__(self, "color", _require_identifier(self.color, "color"))
        if self.account_provider_id is not None:
            object.__setattr__(
                self,
                "account_provider_id",
                _require_identifier(self.account_provider_id, "account_provider_id"),
            )


@dataclass(frozen=True)
class DiscoveryContext:
    home: str
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        home = str(self.home or "").strip()
        if not home:
            raise ValueError("home must not be empty")
        object.__setattr__(self, "home", home)
        object.__setattr__(
            self, "environment", MappingProxyType(dict(self.environment or {}))
        )


@dataclass(frozen=True)
class AdapterFailure:
    runtime_id: str
    operation: str
    code: str

    def __post_init__(self):
        object.__setattr__(
            self, "runtime_id", _require_identifier(self.runtime_id, "runtime_id")
        )
        object.__setattr__(
            self, "operation", _require_identifier(self.operation, "operation")
        )
        object.__setattr__(self, "code", _require_identifier(self.code, "failure code"))

    def to_public_dict(self):
        return {
            "runtime_id": self.runtime_id,
            "operation": self.operation,
            "code": self.code,
        }


@dataclass(frozen=True)
class DiscoveryResult:
    sources: Tuple[SessionSource, ...]
    failures: Tuple[AdapterFailure, ...]

    def __post_init__(self):
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "failures", tuple(self.failures))

    def to_public_dict(self):
        return {
            "sources": [session_source_public_dict(source) for source in self.sources],
            "failures": [failure.to_public_dict() for failure in self.failures],
        }


def session_source_public_dict(source):
    """Return only the bounded, content-free public identity of a source."""

    model = None
    if source.model_ref is not None:
        model = {
            "provider_id": source.model_ref.provider_id,
            "model_id": source.model_ref.model_id,
            "variant": source.model_ref.variant,
        }
    return {
        "runtime_id": source.runtime_id,
        "client_id": source.client_id,
        "session_id": source.session_id,
        "display_label": source.display_label,
        "project": source.project,
        "activity_mtime": source.activity_mtime,
        "model": model,
        "account_provider_id": source.account_provider_id,
    }
