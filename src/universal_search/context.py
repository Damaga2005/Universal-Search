"""Personal context layer (phase 008).

User-defined logical contexts (University, Personal, Projects, Work...),
each holding one or more indexed roots plus explainable preferences:
subjects/categories, preferred sources, document types, related terms and an
optional recency preference.

Everything here is deterministic and explainable: related terms only widen
the retrieval pool (scoring keeps using the original query), and the context
boost is a small bounded signal layered on top of exact-match ranking, so a
personalized result can never outrank a document whose name or content
matches the query exactly.

Privacy: contexts are plain local configuration. Local usage learning is a
separate, disabled-by-default signal recorded in the local database only
(`usage_events`); no AI, no external service, no upload exists anywhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from universal_search.appconfig import AppConfig

# Additive bonuses composing one bounded signal (sum is capped at 1.0).
CONTEXT_ROOT_BONUS = 0.5
CONTEXT_SUBJECT_BONUS = 0.25
CONTEXT_TYPE_BONUS = 0.15
CONTEXT_SOURCE_BONUS = 0.10
CONTEXT_RECENCY_BONUS = 0.10
CONTEXT_BOOST_CAP = 1.0

# Opens needed to saturate the usage signal (4+ opens => 1.0).
USAGE_SATURATION_OPENS = 4.0


@dataclass(frozen=True, slots=True)
class Context:
    """One user-defined context with its explainable preferences."""

    name: str
    roots: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    preferred_sources: tuple[str, ...] = ()
    doc_types: tuple[str, ...] = ()  # normalized: lowercase, no leading dot
    related_terms: dict[str, tuple[str, ...]] = field(default_factory=dict)
    recency_days: int | None = None


# -- parsing / serialization ---------------------------------------------------

def _list(value) -> tuple:
    return value if isinstance(value, (list, tuple)) else ()


def context_from_dict(raw: object) -> Context | None:
    """Normalize a raw config entry; ``None`` when unusable (e.g. no name)."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    roots = tuple(str(item).strip() for item in _list(raw.get("roots")) if str(item).strip())
    subjects = tuple(
        str(item).casefold().strip()
        for item in _list(raw.get("subjects"))
        if str(item).strip()
    )
    sources = tuple(
        str(item).casefold().strip()
        for item in _list(raw.get("preferred_sources"))
        if str(item).strip()
    )
    doc_types = tuple(
        str(item).casefold().lstrip(".").strip()
        for item in _list(raw.get("doc_types"))
        if str(item).strip()
    )
    related: dict[str, tuple[str, ...]] = {}
    raw_related = raw.get("related_terms")
    if isinstance(raw_related, dict):
        for key, values in raw_related.items():
            term = str(key).casefold().strip()
            synonyms = tuple(
                str(item).casefold().strip()
                for item in _list(values)
                if str(item).strip()
            )
            if term:
                related[term] = synonyms
    recency_raw = raw.get("recency_days")
    recency = (
        int(recency_raw)
        if isinstance(recency_raw, (int, float))
        and not isinstance(recency_raw, bool)
        and recency_raw > 0
        else None
    )
    return Context(
        name=name,
        roots=roots,
        subjects=subjects,
        preferred_sources=sources,
        doc_types=doc_types,
        related_terms=related,
        recency_days=recency,
    )


def context_to_dict(context: Context) -> dict:
    return {
        "name": context.name,
        "roots": list(context.roots),
        "subjects": list(context.subjects),
        "preferred_sources": list(context.preferred_sources),
        "doc_types": list(context.doc_types),
        "related_terms": {key: list(values) for key, values in context.related_terms.items()},
        "recency_days": context.recency_days,
    }


def load_contexts(config: AppConfig) -> tuple[Context, ...]:
    parsed = (context_from_dict(raw) for raw in config.contexts)
    return tuple(context for context in parsed if context is not None)


def get_context(config: AppConfig, name: str) -> Context | None:
    """Look up a context by name (case-insensitive)."""
    wanted = str(name or "").casefold()
    for context in load_contexts(config):
        if context.name.casefold() == wanted:
            return context
    return None


# -- configuration mutations ---------------------------------------------------

def _union(existing: tuple[str, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
    merged = list(existing)
    for item in extra:
        if item not in merged:
            merged.append(item)
    return tuple(merged)


def with_context(config: AppConfig, context: Context, *, merge: bool = True) -> AppConfig:
    """Add or update a context by name, merging with an existing one.

    Roots of the context are also ensured to be in ``config.roots`` so they
    are actually indexed (a context root nobody indexes cannot help).
    """
    existing = get_context(config, context.name)
    merged = context
    if merge and existing is not None:
        merged = Context(
            name=existing.name,
            roots=_union(existing.roots, context.roots),
            subjects=_union(existing.subjects, context.subjects),
            preferred_sources=_union(existing.preferred_sources, context.preferred_sources),
            doc_types=_union(existing.doc_types, context.doc_types),
            related_terms={**existing.related_terms, **context.related_terms},
            recency_days=(
                context.recency_days
                if context.recency_days is not None
                else existing.recency_days
            ),
        )
    contexts = [
        raw
        for raw in config.contexts
        if str(raw.get("name", "")).casefold() != merged.name.casefold()
    ]
    contexts.append(context_to_dict(merged))
    roots = config.roots
    for root in merged.roots:
        if root not in roots:
            roots = roots + (root,)
    return replace(config, contexts=tuple(contexts), roots=roots)


def with_context_removed(config: AppConfig, name: str) -> AppConfig:
    wanted = str(name or "").casefold()
    contexts = tuple(
        raw
        for raw in config.contexts
        if str(raw.get("name", "")).casefold() != wanted
    )
    active = config.active_context
    if active.casefold() == wanted:
        active = ""
    return replace(config, contexts=contexts, active_context=active)


def with_active_context(config: AppConfig, name: str) -> AppConfig:
    """Activate a context (empty name clears it).

    Raises ``ValueError`` for unknown names instead of silently doing nothing.
    """
    name = str(name or "").strip()
    if not name:
        return replace(config, active_context="")
    context = get_context(config, name)
    if context is None:
        raise ValueError(f"contexto desconocido: {name}")
    return replace(config, active_context=context.name)


def configured_roots(config: AppConfig) -> tuple[str, ...]:
    """Roots the indexer must cover: explicit roots plus every context root."""
    roots = list(config.roots)
    for context in load_contexts(config):
        for root in context.roots:
            if root not in roots:
                roots.append(root)
    return tuple(roots)


# -- query-time signals ----------------------------------------------------------

def expansion_terms(terms: tuple[str, ...], context: Context | None) -> tuple[str, ...]:
    """Related terms that widen retrieval for this query.

    Expansions only enter the FTS candidate query — scoring always uses the
    original terms, so exact matches keep every one of their signals and are
    never hidden behind a synonym.
    """
    if context is None or not terms:
        return ()
    seen = set(terms)
    expansions: list[str] = []
    for term in terms:
        for synonym in context.related_terms.get(term, ()):
            if synonym and synonym not in seen:
                seen.add(synonym)
                expansions.append(synonym)
    return tuple(expansions)


def context_boost_for(
    context: Context,
    *,
    path: str,
    source: str = "",
    doc_type: str = "",
    modified_at: str | None = None,
    now: datetime | None = None,
) -> tuple[float, tuple[str, ...]]:
    """Bounded contextual preference for one document, with its reasons.

    The returned reasons make the personalization explainable: every point of
    boost maps to ``root``, ``subject``, ``type``, ``source`` or ``recency``.
    """
    boost = 0.0
    reasons: list[str] = []
    lowered = str(path).casefold()

    for root in context.roots:
        normalized = str(root).casefold().rstrip("\\/")
        if normalized and (
            lowered == normalized
            or lowered.startswith(normalized + "\\")
            or lowered.startswith(normalized + "/")
        ):
            boost += CONTEXT_ROOT_BONUS
            reasons.append(f"root:{root}")
            break

    for subject in context.subjects:
        if subject in lowered:
            boost += CONTEXT_SUBJECT_BONUS
            reasons.append(f"subject:{subject}")
            break

    kind = str(doc_type).casefold().lstrip(".")
    if kind and kind in context.doc_types:
        boost += CONTEXT_TYPE_BONUS
        reasons.append(f"type:{kind}")

    if str(source).casefold() in context.preferred_sources:
        boost += CONTEXT_SOURCE_BONUS
        reasons.append(f"source:{source}")

    if context.recency_days is not None and modified_at:
        try:
            modified = datetime.fromisoformat(str(modified_at))
            if modified.tzinfo is None:
                modified = modified.replace(tzinfo=timezone.utc)
            reference = now or datetime.now(timezone.utc)
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            if (reference - modified).days <= context.recency_days:
                boost += CONTEXT_RECENCY_BONUS
                reasons.append(f"recency:{context.recency_days}d")
        except ValueError:
            pass

    return min(boost, CONTEXT_BOOST_CAP), tuple(reasons)


def usage_boost_from(open_count: int) -> float:
    """Saturating usage signal: 0 opens -> 0, 4+ opens -> 1."""
    if open_count <= 0:
        return 0.0
    return min(1.0, math.log1p(open_count) / math.log1p(USAGE_SATURATION_OPENS))
