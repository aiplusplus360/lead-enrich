"""Demo: merge two lead sources and explain every decision.

    python examples/demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from leadenrich import Record, match_pairs, resolve  # noqa: E402
from leadenrich.cli import load_records  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> int:
    records = load_records([str(HERE / "crm.json"), str(HERE / "ads.json")], region="US")

    print("normalised input")
    print("-" * 74)
    for record in records:
        fields = ", ".join(f"{k}={v}" for k, v in sorted(record.fields.items()))
        print(f"  {record.source}:{record.id}  {fields}")
        for issue in record.issues:
            print(f"      ! {issue.field}: {issue.reason}")
    print()

    print("candidate pairs")
    print("-" * 74)
    for pair in match_pairs(records, threshold=0.5):
        mark = "MERGE" if pair.score >= 0.82 else "review"
        print(f"  {pair.score:.3f} {mark:<6} {pair.left} <-> {pair.right}")
        for agreement in pair.agreements:
            print(f"        {agreement.field:<8} {agreement.score:.2f}  {agreement.note}")
    print()

    merged = resolve(records, threshold=0.82, source_order=["crm", "ads"])
    print("merged entities")
    print("-" * 74)
    for entity in merged:
        flag = " REVIEW" if entity.needs_review else ""
        print(f"  confidence {entity.confidence:.3f}  size {entity.size}{flag}")
        for name, value in sorted(entity.fields.items()):
            print(f"      {name:<10} {value!r:<32} <- {entity.provenance[name]}")
        for conflict in entity.conflicts:
            options = " vs ".join(f"{source}={value!r}" for source, value in conflict.values)
            print(f"      CONFLICT {conflict.field}: {options}")
        print()

    print("summary")
    print("-" * 74)
    print(json.dumps(
        {
            "input_records": len(records),
            "merged_entities": len(merged),
            "needs_review": sum(1 for m in merged if m.needs_review),
            "with_conflicts": sum(1 for m in merged if m.conflicts),
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
