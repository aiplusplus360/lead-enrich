"""Entity resolution with provenance and explicit conflicts.

The design decision worth arguing about: a merge never silently picks a value.
Every field that two sources disagree on is emitted as a ``Conflict`` with both
values and both sources, and the winning value is annotated with where it came
from. Silent last-write-wins is why the CRM address is wrong and nobody can say
when it became wrong.

Matching is deterministic: records are ordered by ``(source, id)`` before any
pair is compared, buckets are walked in sorted order, and every tie is broken
lexicographically. Same input, same clusters, same ids, every run.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .normalize import FieldIssue, is_role_address, normalize_fields, registrable_domain
from .similarity import best_similarity

__all__ = [
    "Agreement",
    "Comparison",
    "Conflict",
    "MergedRecord",
    "Record",
    "blocking_keys",
    "compare",
    "match_pairs",
    "resolve",
]

CORE_FIELDS: Tuple[str, ...] = ("email", "phone", "domain", "company", "name")

FIELD_WEIGHTS: Dict[str, float] = {
    "email": 0.45,
    "phone": 0.20,
    "domain": 0.15,
    "company": 0.12,
    "name": 0.08,
}

# An exact email is decisive unless it is a shared role mailbox, where it only
# says the two records are about the same *organisation*.
EXACT_EMAIL_SCORE = 0.95
ROLE_EMAIL_SCORE = 0.70
EXACT_PHONE_SCORE = 0.85
DOMAIN_COMPANY_SCORE = 0.90
WEAK_ONLY_CAP = 0.79


@dataclass(frozen=True)
class Record:
    id: str
    source: str
    fields: Mapping[str, str]
    issues: Tuple[FieldIssue, ...] = ()

    @classmethod
    def build(cls, raw: Mapping[str, object], source: str, *, region: Optional[str] = None) -> "Record":
        fields, issues = normalize_fields(dict(raw), region=region)
        payload = "\x00".join(f"{k}={v}" for k, v in sorted(fields.items()))
        digest = hashlib.sha256(f"{source}\x00{payload}".encode("utf-8")).hexdigest()[:12]
        return cls(id=digest, source=source, fields=fields, issues=tuple(issues))

    def get(self, field_name: str) -> Optional[str]:
        return self.fields.get(field_name)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "fields": dict(self.fields),
            "issues": [issue.as_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class Agreement:
    field: str
    score: float
    note: str


@dataclass
class Comparison:
    left: str
    right: str
    score: float
    agreements: List[Agreement] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.score > 0

    def to_dict(self) -> dict:
        return {
            "left": self.left,
            "right": self.right,
            "score": round(self.score, 4),
            "agreements": [
                {"field": a.field, "score": round(a.score, 3), "note": a.note}
                for a in self.agreements
            ],
        }


@dataclass(frozen=True)
class Conflict:
    field: str
    values: Tuple[Tuple[str, str], ...]  # (source, value)

    def as_dict(self) -> dict:
        return {"field": self.field, "values": [{"source": s, "value": v} for s, v in self.values]}


@dataclass
class MergedRecord:
    fields: Dict[str, str]
    provenance: Dict[str, str]
    conflicts: List[Conflict]
    confidence: float
    sources: List[str]
    record_ids: List[str]
    issues: List[dict] = field(default_factory=list)
    needs_review: bool = False

    @property
    def size(self) -> int:
        return len(self.record_ids)

    def to_dict(self) -> dict:
        return {
            "fields": dict(self.fields),
            "provenance": dict(self.provenance),
            "conflicts": [c.as_dict() for c in self.conflicts],
            "confidence": self.confidence,
            "size": self.size,
            "sources": list(self.sources),
            "record_ids": list(self.record_ids),
            "needs_review": self.needs_review,
            "issues": list(self.issues),
        }


def blocking_keys(record: Record) -> Set[str]:
    """Candidate-generator keys. Two records must share one to be compared.

    This is the recall/precision trade-off every resolver makes. Keys are chosen
    so a typo in a first name cannot prevent a match, while a completely
    different person cannot create a candidate pair.
    """
    keys: Set[str] = set()
    email = record.get("email")
    if email:
        keys.add("e:" + email)
    domain = record.get("domain")
    if domain:
        keys.add("d:" + domain)
    phone = record.get("phone")
    if phone:
        keys.add("p:" + phone[-9:])
    name = record.get("name")
    if name:
        parts = name.lower().split()
        if parts:
            keys.add("s:" + parts[-1])
    company = record.get("company")
    if company:
        first = company.lower().split()[0]
        keys.add("c:" + first[:8])
    return keys


def compare(left: Record, right: Record) -> Comparison:
    """Weighted, per-field agreement between two records."""
    agreements: List[Agreement] = []

    def add(name: str, score: float, note: str) -> None:
        agreements.append(Agreement(name, score, note))

    email_a, email_b = left.get("email"), right.get("email")
    exact_email = False
    if email_a and email_b:
        if email_a == email_b:
            role = is_role_address(email_a)
            exact_email = not role
            add("email", ROLE_EMAIL_SCORE if role else 1.0,
                "shared role mailbox" if role else "exact email")
        elif email_a.split("@")[-1] == email_b.split("@")[-1]:
            add("email", 0.10, "same email domain, different mailbox")
        else:
            add("email", 0.0, "different email")

    phone_a, phone_b = left.get("phone"), right.get("phone")
    exact_phone = False
    if phone_a and phone_b:
        if phone_a == phone_b:
            exact_phone = True
            add("phone", 1.0, "exact phone")
        elif phone_a[-9:] == phone_b[-9:]:
            add("phone", 0.75, "same last nine digits")
        else:
            add("phone", 0.0, "different phone")

    domain_a, domain_b = left.get("domain"), right.get("domain")
    if domain_a and domain_b:
        if domain_a == domain_b:
            add("domain", 1.0, "exact domain")
        elif registrable_domain(domain_a) == registrable_domain(domain_b):
            add("domain", 0.80, "same registrable domain")
        else:
            add("domain", 0.0, "different domain")

    company_a, company_b = left.get("company"), right.get("company")
    if company_a and company_b:
        score = best_similarity(company_a, company_b)
        add("company", score, "company name similarity")

    name_a, name_b = left.get("name"), right.get("name")
    if name_a and name_b:
        score = best_similarity(name_a, name_b)
        add("name", score, "person name similarity")

    if not agreements:
        return Comparison(left.id, right.id, 0.0, [])

    total_weight = sum(FIELD_WEIGHTS[a.field] for a in agreements)
    score = sum(FIELD_WEIGHTS[a.field] * a.score for a in agreements) / total_weight

    if exact_email:
        score = max(score, EXACT_EMAIL_SCORE)
    if exact_phone:
        score = max(score, EXACT_PHONE_SCORE)
    company_score = next((a.score for a in agreements if a.field == "company"), 0.0)
    domain_score = next((a.score for a in agreements if a.field == "domain"), 0.0)
    if company_score >= 0.9 and domain_score >= 0.9:
        score = max(score, DOMAIN_COMPANY_SCORE)

    # Name and company similarity are suggestive, never decisive. Without an
    # email, phone or domain in common, two "John Smith"s at similar companies
    # can clear any threshold, so the score is capped below the default match
    # threshold and the pair lands in human review instead of auto-merging.
    if not any(a.field in ("email", "phone", "domain") for a in agreements):
        score = min(score, WEAK_ONLY_CAP)

    return Comparison(left.id, right.id, min(1.0, score), agreements)


def _candidates(records: Sequence[Record]) -> List[Tuple[int, int]]:
    buckets: Dict[str, List[int]] = {}
    for position, record in enumerate(records):
        for key in blocking_keys(record):
            buckets.setdefault(key, []).append(position)
    pairs: Set[Tuple[int, int]] = set()
    for key in sorted(buckets):
        members = buckets[key]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add((members[i], members[j]))
    return sorted(pairs)


def _pick(
    values: Sequence[Tuple[str, str]],
    source_rank: Mapping[str, int],
) -> Tuple[str, str]:
    counts: Dict[str, int] = {}
    sources_by_value: Dict[str, Set[str]] = {}
    for source, value in values:
        counts[value] = counts.get(value, 0) + 1
        sources_by_value.setdefault(value, set()).add(source)
    return min(
        counts,
        key=lambda value: (
            -counts[value],
            min(source_rank.get(s, len(source_rank)) for s in sources_by_value[value]),
            value,
        ),
    )


def _merge_group(
    members: Sequence[Record],
    comparisons: Sequence[Comparison],
    source_order: Optional[Sequence[str]],
    review_below: float,
) -> MergedRecord:
    source_rank = {name: index for index, name in enumerate(source_order or [])}

    all_fields: List[str] = []
    for member in members:
        for name in member.fields:
            if name not in all_fields:
                all_fields.append(name)

    fields: Dict[str, str] = {}
    provenance: Dict[str, str] = {}
    conflicts: List[Conflict] = []

    for name in sorted(all_fields):
        values = [(m.source, m.fields[name]) for m in members if name in m.fields]
        if not values:
            continue
        distinct = []
        for _, value in values:
            if value not in distinct:
                distinct.append(value)
        winner = _pick(values, source_rank)
        fields[name] = winner
        provenance[name] = min(
            (source for source, value in values if value == winner),
            key=lambda source: source_rank.get(source, len(source_rank)),
        )
        if len(distinct) > 1:
            conflicts.append(Conflict(field=name, values=tuple(values)))

    present = sum(1 for name in CORE_FIELDS if name in fields)
    completeness = present / len(CORE_FIELDS)
    if len(members) > 1 and comparisons:
        pair_score = sum(c.score for c in comparisons) / len(comparisons)
        base = 0.55 * pair_score + 0.45 * completeness
    else:
        base = completeness
    penalty = min(0.20, 0.05 * len(conflicts))
    confidence = round(max(0.0, min(1.0, base - penalty)), 4)

    issues: List[dict] = []
    for member in members:
        for issue in member.issues:
            entry = issue.as_dict()
            entry["source"] = member.source
            issues.append(entry)

    return MergedRecord(
        fields=fields,
        provenance=provenance,
        conflicts=conflicts,
        confidence=confidence,
        sources=sorted({m.source for m in members}),
        record_ids=[m.id for m in members],
        issues=issues,
        needs_review=confidence < review_below,
    )


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, node: int) -> int:
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[max(root_a, root_b)] = min(root_a, root_b)


def match_pairs(
    records: Sequence[Record],
    *,
    threshold: float = 0.82,
) -> List[Comparison]:
    """Every candidate pair at or above ``threshold``, strongest first."""
    ordered = sorted(records, key=lambda r: (r.source, r.id))
    results = []
    for i, j in _candidates(ordered):
        comparison = compare(ordered[i], ordered[j])
        if comparison.score >= threshold:
            results.append(comparison)
    results.sort(key=lambda c: (-c.score, c.left, c.right))
    return results


def resolve(
    records: Iterable[Record],
    *,
    threshold: float = 0.82,
    source_order: Optional[Sequence[str]] = None,
    review_below: float = 0.70,
) -> List[MergedRecord]:
    """Cluster records and merge each cluster."""
    ordered = sorted(records, key=lambda r: (r.source, r.id))
    if not ordered:
        return []

    union = _UnionFind(len(ordered))
    comparisons: List[Comparison] = []
    for i, j in _candidates(ordered):
        comparison = compare(ordered[i], ordered[j])
        if comparison.score >= threshold:
            union.union(i, j)
            comparisons.append(comparison)

    groups: Dict[int, List[int]] = {}
    for position in range(len(ordered)):
        groups.setdefault(union.find(position), []).append(position)

    merged = []
    for root in sorted(groups):
        members = [ordered[p] for p in groups[root]]
        member_ids = {m.id for m in members}
        inside = [c for c in comparisons if c.left in member_ids and c.right in member_ids]
        merged.append(_merge_group(members, inside, source_order, review_below))

    merged.sort(
        key=lambda m: (
            -m.size,
            -m.confidence,
            m.fields.get("email", ""),
            m.fields.get("name", ""),
            m.fields.get("company", ""),
        )
    )
    return merged
