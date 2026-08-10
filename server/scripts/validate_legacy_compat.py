from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.light.service import ALLOWED_EXTENSIONS, parse_light_bytes
from app.modules.questionnaire.s0_import import _validate_s0, _willing, parse_table


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only compatibility check against ANOLighting V5 test data")
    parser.add_argument("--state-json", required=True, help="V5 state.json containing cached Lighting summaries")
    parser.add_argument("--light-dir", required=True, help="Historical Lighting test folder")
    parser.add_argument("--s0", help="Optional historical S0 CSV/XLSX")
    args = parser.parse_args()

    state = json.loads(Path(args.state_json).read_text(encoding="utf-8"))
    cached = {
        Path(path).name: entry.get("summary", {})
        for path, entry in (state.get("scan_index", {}).get("lighting", {}) or {}).items()
    }
    compared = 0
    mismatches: list[dict] = []
    qualities: dict[str, int] = {}
    fields = {
        "records_expected": "light_records_expected",
        "records_total": "light_records_total",
        "records_valid": "light_records_valid",
        "records_saturated": "light_records_saturated",
        "valid_pct": "light_valid_pct",
        "quality": "light_quality",
        "photopic_mean": "photopic_mean",
        "photopic_median": "photopic_median",
        "photopic_max": "photopic_max",
        "melanopic_mean": "melanopic_mean",
        "melanopic_median": "melanopic_median",
        "melanopic_max": "melanopic_max",
    }
    for path in sorted(Path(args.light_dir).iterdir()):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        actual = parse_light_bytes(path.name, path.read_bytes())
        qualities[actual["quality"]] = qualities.get(actual["quality"], 0) + 1
        expected = cached.get(path.name)
        if not expected:
            continue
        compared += 1
        differences = {
            new: {"expected": expected.get(old), "actual": actual.get(new)}
            for new, old in fields.items()
            if expected.get(old) != actual.get(new)
        }
        if differences:
            mismatches.append({"filename": path.name, "differences": differences})

    result = {
        "lighting": {
            "cached_files": len(cached),
            "compared_files": compared,
            "qualities": qualities,
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        }
    }
    if args.s0:
        s0_path = Path(args.s0)
        rows = parse_table(s0_path.name, s0_path.read_bytes())
        _validate_s0(rows)
        result["s0"] = {
            "filename": s0_path.name,
            "rows": len(rows),
            "willing": sum(_willing(row) for row in rows),
            "filtered": sum(not _willing(row) for row in rows),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())

