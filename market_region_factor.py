"""Pure Python market/service-region matching factor for task S3-MATCH-020.

This module uses a normalized internal DTO. It does not assume that S2-TAX uses
these field names; an integration adapter must map the upstream contract here.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum
from typing import Final, Iterable

# Boundary defaults for rules the task leaves unspecified. These are local,
# bounded fallbacks, not buyer-confirmed policy. They are module constants so
# the behavior cannot drift silently; change them only with a matching README
# update, test update, and buyer confirmation.
UNRELATED_PAIR_SCORE: Final[float] = 0.0
PARENT_CHILD_MAX_DEPTH: Final[int] = 1


class RegionDataError(ValueError):
    """Raised when the normalized region hierarchy is invalid."""


class UnknownRegionCodeError(RegionDataError):
    """Raised when a non-empty input code is absent from the loaded dictionary."""


@dataclass(frozen=True, slots=True)
class RegionRecord:
    """Normalized region dictionary record used by the factor adapter."""

    code: str
    parent_code: str | None
    is_domestic: bool


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    demand_code: str
    candidate_code: str
    demand_source_field: str
    candidate_source_field: str
    score: float
    relation: str


@dataclass(frozen=True, slots=True)
class DemandEvidence:
    """Best match for one demand region; unmatched regions have no pairs."""

    demand_code: str
    score: float
    matched_pairs: tuple[MatchEvidence, ...]


@dataclass(frozen=True, slots=True)
class FactorEvidence:
    demand_source_field: str
    missing_fields: tuple[str, ...]
    demand_results: tuple[DemandEvidence, ...]


@dataclass(frozen=True, slots=True)
class FactorResult:
    score: float
    reason: None
    evidence: FactorEvidence


@dataclass(frozen=True, slots=True)
class _Candidate:
    code: str
    source_field: str


class RegionHierarchy:
    """Validated multi-root region tree with direct relationship queries."""

    def __init__(self, records: Iterable[RegionRecord]) -> None:
        if not isinstance(PARENT_CHILD_MAX_DEPTH, int) or PARENT_CHILD_MAX_DEPTH < 1:
            raise RegionDataError("PARENT_CHILD_MAX_DEPTH must be a positive integer")
        by_code: dict[str, RegionRecord] = {}
        for record in records:
            if not isinstance(record, RegionRecord):
                raise RegionDataError("hierarchy records must be RegionRecord instances")
            if not isinstance(record.code, str) or not record.code.strip():
                raise RegionDataError("region code must be a non-empty string")
            code = record.code.strip()
            if code in by_code:
                raise RegionDataError(f"duplicate region code: {code}")
            if record.parent_code is not None and not isinstance(record.parent_code, str):
                raise RegionDataError(f"parent code for {code} must be a string or None")
            if record.parent_code is not None and not record.parent_code.strip():
                raise RegionDataError(f"parent code for {code} must be None or non-empty")
            if not isinstance(record.is_domestic, bool):
                raise RegionDataError(f"is_domestic for {code} must be bool")
            parent_code = record.parent_code.strip() if record.parent_code is not None else None
            by_code[code] = RegionRecord(code, parent_code, record.is_domestic)

        for record in by_code.values():
            if record.parent_code is not None and record.parent_code not in by_code:
                raise RegionDataError(
                    f"missing parent {record.parent_code} for region {record.code}"
                )
            if record.parent_code == record.code:
                raise RegionDataError(f"region {record.code} cannot parent itself")

        self._records = by_code
        self._validate_acyclic()

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(self._records)

    def require(self, code: str) -> RegionRecord:
        try:
            return self._records[code]
        except KeyError as exc:
            raise UnknownRegionCodeError(f"unknown region code: {code}") from exc

    def is_domestic(self, code: str) -> bool:
        return self.require(code).is_domestic

    def parent_child_depth(self, ancestor: str, descendant: str) -> int | None:
        """Return the number of parent edges from descendant up to ancestor.

        ``0`` means the same code; ``1`` means a direct parent/child edge;
        ``2`` or more means a deeper ancestor. ``None`` means the two codes are
        not on the same parent chain. The scoring boundary is governed by
        ``PARENT_CHILD_MAX_DEPTH`` (default ``1``, direct edges only).
        """
        self.require(ancestor)
        self.require(descendant)
        depth = 0
        current: str | None = descendant
        while current is not None:
            if current == ancestor:
                return depth
            current = self._records[current].parent_code
            depth += 1
        return None

    def is_direct_parent_child(self, left: str, right: str) -> bool:
        """True only for a single parent edge, in either direction.

        Deeper ancestor/descendant pairs are deliberately outside the default
        boundary (``PARENT_CHILD_MAX_DEPTH == 1``).
        """
        left_to_right = self.parent_child_depth(left, right)
        right_to_left = self.parent_child_depth(right, left)
        return left_to_right == 1 or right_to_left == 1

    def are_direct_siblings(self, left: str, right: str) -> bool:
        left_parent = self.require(left).parent_code
        right_parent = self.require(right).parent_code
        return left_parent is not None and left_parent == right_parent

    def _validate_acyclic(self) -> None:
        # A parent pointer graph has at most one outgoing edge per node. Walking
        # each chain makes cycle detection linear in the number of records.
        complete: set[str] = set()
        for start in self._records:
            if start in complete:
                continue
            path: set[str] = set()
            current: str | None = start
            while current is not None and current not in complete:
                if current in path:
                    raise RegionDataError(f"cycle detected at region {current}")
                path.add(current)
                current = self._records[current].parent_code
            complete.update(path)


def _normalize_codes(values: Iterable[str | None] | str | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        items: Iterable[str | None] = (values,)
    else:
        try:
            items = iter(values)
        except TypeError as exc:
            raise RegionDataError("region codes must be an iterable of strings or None") from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for value in items:
        if value is None:
            continue
        if not isinstance(value, str):
            raise RegionDataError("region code entries must be strings or None")
        code = value.strip()
        if code and code not in seen:
            normalized.append(code)
            seen.add(code)
    return tuple(normalized)


def _pair_score(
    demand_code: str,
    candidate_code: str,
    hierarchy: RegionHierarchy,
    national_code: str,
) -> tuple[float, str | None]:
    """Return the first applicable rule; later values are explicit assumptions."""
    if demand_code == candidate_code:
        return 1.0, "exact"

    # The national override must precede direct parent/child scoring: e.g. a
    # nationwide candidate matching its direct domestic child still scores 0.6.
    if candidate_code == national_code and hierarchy.is_domestic(demand_code):
        return 0.6, "national_domestic_special"

    # The parent/child boundary is deliberately capped by
    # PARENT_CHILD_MAX_DEPTH (default 1, direct edges only). A grandparent or
    # deeper ancestor is not a parent/child match under the local default.
    depths = [
        depth
        for depth in (
            hierarchy.parent_child_depth(demand_code, candidate_code),
            hierarchy.parent_child_depth(candidate_code, demand_code),
        )
        if depth is not None
    ]
    if depths and min(depths) <= PARENT_CHILD_MAX_DEPTH:
        depth = min(depths)
        relation = "direct_parent_child" if depth == 1 else "ancestor_descendant"
        return 0.7, relation

    if hierarchy.are_direct_siblings(demand_code, candidate_code):
        return 0.3, "direct_siblings"

    # The task does not define a score for valid codes outside the three
    # relationship classes above. Local bounded default: no defined overlap =
    # UNRELATED_PAIR_SCORE (0.0).
    return UNRELATED_PAIR_SCORE, None


def calculate_market_region_factor(
    hierarchy: RegionHierarchy,
    *,
    demand_market_codes: Iterable[str | None] | str | None,
    supply_market_codes: Iterable[str | None] | str | None = None,
    capability_service_area_tags: Iterable[str | None] | str | None = None,
    national_code: str,
    demand_source_field: str = "demand.market_codes",
    supply_source_field: str = "supply_item.market_codes",
    capability_source_field: str = "enterprise_capability_card.service_area_tags",
) -> FactorResult:
    """Score demand areas against the union of both candidate-region sources.

    Missing-data policy for this prototype:
    * The whole logical side is missing only when it has no nonblank code.
    * Blank/None elements inside a nonempty list are ignored.
    * Unknown nonblank codes raise ``UnknownRegionCodeError`` instead of silently
      becoming a neutral score or an unmatched score.
    * If both candidate sources provide the same code, it is scored once per
      source for provenance; tied best pairs retain every source field.

    Scores are not rounded. Multi-demand averages use ``math.fsum``.
    """
    if not isinstance(national_code, str) or not national_code.strip():
        raise RegionDataError("national_code must be a non-empty string")
    national_code = national_code.strip()
    hierarchy.require(national_code)

    if not isinstance(demand_source_field, str) or not demand_source_field.strip():
        raise RegionDataError("demand source field name must be a non-empty string")
    demand_source_field = demand_source_field.strip()

    if not isinstance(supply_source_field, str) or not supply_source_field.strip():
        raise RegionDataError("candidate source field names must be non-empty strings")
    if not isinstance(capability_source_field, str) or not capability_source_field.strip():
        raise RegionDataError("candidate source field names must be non-empty strings")
    supply_source_field = supply_source_field.strip()
    capability_source_field = capability_source_field.strip()

    demand_codes = _normalize_codes(demand_market_codes)
    candidates: list[_Candidate] = []
    for values, field_name in (
        (supply_market_codes, supply_source_field),
        (capability_service_area_tags, capability_source_field),
    ):
        if not isinstance(field_name, str) or not field_name.strip():
            raise RegionDataError("candidate source field names must be non-empty strings")
        for code in _normalize_codes(values):
            candidates.append(_Candidate(code, field_name.strip()))

    # Validate every usable code before scoring; malformed dictionary references
    # must not be mistaken for missing inputs or zero-overlap pairs.
    for code in demand_codes:
        hierarchy.require(code)
    for candidate in candidates:
        hierarchy.require(candidate.code)

    # Keep different source fields for evidence, but eliminate duplicate entries
    # from the same field. Sorting makes the result deterministic.
    candidates = sorted(
        set(candidates), key=lambda candidate: (candidate.code, candidate.source_field)
    )

    missing_fields: list[str] = []
    if not demand_codes:
        missing_fields.append(demand_source_field)
    if not candidates:
        missing_fields.extend((supply_source_field, capability_source_field))

    if missing_fields:
        if not demand_codes:
            demand_results: tuple[DemandEvidence, ...] = ()
        elif not candidates:
            demand_results = tuple(
                DemandEvidence(code, 0.5, ()) for code in demand_codes
            )
        else:
            # This branch is defensive; a missing field should always correspond
            # to a missing logical side, not an optional source.
            demand_results = ()
        evidence = FactorEvidence(
            demand_source_field=demand_source_field,
            missing_fields=tuple(missing_fields),
            demand_results=demand_results,
        )
        return FactorResult(score=0.5, reason=None, evidence=evidence)

    demand_results_list: list[DemandEvidence] = []
    for demand_code in demand_codes:
        evaluated: list[tuple[float, str | None, _Candidate]] = []
        for candidate in candidates:
            score, relation = _pair_score(
                demand_code, candidate.code, hierarchy, national_code
            )
            evaluated.append((score, relation, candidate))

        best_score = max(score for score, _, _ in evaluated)
        # Only defined relationships produce evidence. A fallback score (even if
        # the boundary constant is changed away from 0.0) is not a match.
        winners = [
            entry for entry in evaluated if entry[0] == best_score and entry[1] is not None
        ]
        matched_pairs: tuple[MatchEvidence, ...] = tuple(
            MatchEvidence(
                demand_code=demand_code,
                candidate_code=candidate.code,
                demand_source_field=demand_source_field,
                candidate_source_field=candidate.source_field,
                score=score,
                relation=relation or "",
            )
            for score, relation, candidate in winners
        )
        demand_results_list.append(
            DemandEvidence(demand_code, best_score, matched_pairs)
        )

    overall_score = fsum(item.score for item in demand_results_list) / len(
        demand_results_list
    )
    evidence = FactorEvidence(
        demand_source_field=demand_source_field,
        missing_fields=(),
        demand_results=tuple(demand_results_list),
    )
    return FactorResult(score=overall_score, reason=None, evidence=evidence)
