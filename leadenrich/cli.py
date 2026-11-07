"""Command line interface for lead-enrich."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from .normalize import normalize_fields
from .resolve import Record, match_pairs, resolve

INPUT_FIELDS = {"records", "record", "data", "leads", "items"}


def _load_payload(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    # utf-8-sig: exports from Excel and many Windows tools carry a BOM, and a
    # BOM is not a JSON error a user can reasonably be expected to understand.
    text = Path(path).read_text(encoding="utf-8-sig")
    if path.endswith(".jsonl"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def load_records(paths: Sequence[str], *, region: Optional[str]) -> List[Record]:
    records: List[Record] = []
    for path in paths:
        payload = _load_payload(path)
        stem = "stdin" if path == "-" else Path(path).stem
        if isinstance(payload, dict):
            rows = None
            for key in INPUT_FIELDS:
                if key in payload and isinstance(payload[key], list):
                    rows = payload[key]
                    break
            if rows is None:
                rows = [payload]
            default_source = str(payload.get("source", stem))
            source = default_source
        elif isinstance(payload, list):
            rows = payload
            source = stem
        else:
            raise ValueError(f"{path}: expected an object or a list of objects")

        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{path}: every record must be an object")
            row = dict(row)
            record_source = str(row.pop("source", source))
            records.append(Record.build(row, record_source, region=region))
    return records


def cmd_merge(args: argparse.Namespace) -> int:
    records = load_records(args.inputs, region=args.region)
    source_order = [s.strip() for s in args.source_order.split(",")] if args.source_order else None
    merged = resolve(
        records,
        threshold=args.threshold,
        source_order=source_order,
        review_below=args.review_below,
    )
    payload = {
        "summary": {
            "input_records": len(records),
            "merged_entities": len(merged),
            "needs_review": sum(1 for m in merged if m.needs_review),
            "with_conflicts": sum(1 for m in merged if m.conflicts),
            "field_issues": sum(len(r.issues) for r in records),
            "threshold": args.threshold,
        },
        "records": [m.to_dict() for m in merged],
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(json.dumps(payload["summary"], indent=2))
    else:
        print(text)
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    records = load_records(args.inputs, region=args.region)
    pairs = match_pairs(records, threshold=args.threshold)
    by_id = {r.id: r for r in records}
    if args.json:
        print(json.dumps([p.to_dict() for p in pairs], indent=2, ensure_ascii=False))
        return 0
    if not pairs:
        print(f"no pairs at or above {args.threshold}")
        return 0
    for pair in pairs:
        left, right = by_id[pair.left], by_id[pair.right]
        print(f"{pair.score:.3f}  {left.source}:{left.id}  <->  {right.source}:{right.id}")
        for agreement in pair.agreements:
            if agreement.score > 0:
                print(f"        {agreement.field:<8} {agreement.score:.2f}  {agreement.note}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    records = load_records(args.inputs, region=args.region)
    payload = []
    issues = 0
    for record in records:
        entry = record.to_dict()
        issues += len(record.issues)
        payload.append(entry)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for entry in payload:
            print(f"{entry['source']}:{entry['id']}  {entry['fields']}")
            for issue in entry["issues"]:
                print(f"    ! {issue['field']}: {issue['reason']}  (raw={issue['raw']!r})")
        print(f"\n{len(records)} records, {issues} field issues")
    return 0


def cmd_normalise(args: argparse.Namespace) -> int:
    fields, issues = normalize_fields(dict(args.pairs), region=args.region)
    print(json.dumps({"fields": fields, "issues": [i.as_dict() for i in issues]}, indent=2))
    return 0


def _pair(text: str) -> Tuple[str, str]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("expected field=value")
    key, _, value = text.partition("=")
    return key.strip(), value.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leadenrich",
        description="Deterministic lead de-duplication with provenance and conflict reporting.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("inputs", nargs="+", help="JSON or JSONL files ('-' for stdin)")
        p.add_argument("--region", default=None, help="ISO region for phone numbers without a country code")

    merge = sub.add_parser("merge", help="cluster records and merge each cluster")
    common(merge)
    merge.add_argument("--out", default=None)
    merge.add_argument("--threshold", type=float, default=0.82)
    merge.add_argument("--review-below", type=float, default=0.70)
    merge.add_argument("--source-order", default=None, help="comma separated, highest trust first")
    merge.set_defaults(func=cmd_merge)

    match = sub.add_parser("match", help="show candidate pairs above the threshold")
    common(match)
    match.add_argument("--threshold", type=float, default=0.82)
    match.add_argument("--json", action="store_true")
    match.set_defaults(func=cmd_match)

    validate = sub.add_parser("validate", help="normalise every record and report bad fields")
    common(validate)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    norm = sub.add_parser("normalise", help="normalise a single record given as field=value")
    norm.add_argument("pairs", nargs="+", type=_pair)
    norm.add_argument("--region", default=None)
    norm.set_defaults(func=cmd_normalise)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"error: no such file: {exc.filename}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
