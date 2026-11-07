"""Assert the merge output for the example sources.

Used by CI so the expected behaviour is executable rather than described in a
comment: exit code 0 means every invariant held.

    python scripts/check_merge.py merged.json
"""

from __future__ import annotations

import json
import sys


def main(argv):
    if len(argv) != 2:
        print("usage: check_merge.py <merged.json>", file=sys.stderr)
        return 2

    with open(argv[1], encoding="utf-8") as handle:
        data = json.load(handle)

    summary = data["summary"]
    assert summary["input_records"] == 6, summary
    assert summary["merged_entities"] == 4, summary
    assert summary["with_conflicts"] == 2, summary
    assert summary["field_issues"] == 0, summary

    zenith = [r for r in data["records"] if r["fields"].get("email") == "info@zenith.io"]
    assert len(zenith) == 2, zenith
    assert all(r["size"] == 1 for r in zenith), zenith

    jane = [r for r in data["records"] if r["fields"].get("city") in ("Berlin", "Munich")]
    assert len(jane) == 1, jane
    jane = jane[0]
    assert jane["size"] == 2, jane
    assert jane["fields"]["city"] == "Berlin", jane
    assert jane["provenance"]["city"] == "crm", jane
    assert jane["conflicts"][0]["field"] == "city", jane
    assert jane["fields"]["campaign"] == "spring-retargeting", jane

    sam = [r for r in data["records"] if "okafor" in (r["fields"].get("name") or "").lower()]
    assert len(sam) == 1, sam
    sam = sam[0]
    assert sam["size"] == 2, sam
    assert sam["fields"]["phone"] == "+14155550199", sam
    assert sam["needs_review"] is True, sam

    print("merge invariants hold:", json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
