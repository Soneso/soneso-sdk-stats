#!/usr/bin/env python3
"""Freeze dashboard inputs and headlines. Run: python3 -m dashboard.snapshot 2026-Q3.

The quarter is a label, not a filter: all windows keep dashboard definitions
at creation time. Existing snapshot directories are never modified.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from datetime import datetime, timezone

from . import build


def create_snapshot(quarter, output_root=None, source_root=None, now=None):
    if not isinstance(quarter, str) or not re.fullmatch(r"20[0-9]{2}-Q[1-4]", quarter):
        raise ValueError("quarter must match ^20[0-9]{2}-Q[1-4]$")
    source_root = Path(source_root) if source_root is not None else build.ROOT
    output_root = Path(output_root) if output_root is not None else source_root / "snapshots"
    target = output_root / quarter
    if os.path.lexists(target):
        raise FileExistsError(f"Snapshot {target} already exists and is immutable; nothing changed.")
    # A snapshot is deliberate evidence: refuse a partial tree loudly
    # instead of freezing an incomplete capture.
    missing = [folder for folder in [s["folder"] for s in build.SDKS] + ["curated"]
               if not (source_root / folder).is_dir()]
    if missing:
        raise ValueError(f"Source folders missing, refusing a partial capture: {', '.join(missing)}")

    old_root, old_now, old_today = build.ROOT, build.NOW, build.TODAY
    try:
        build.NOW = now or datetime.now(timezone.utc)
        build.TODAY = build.NOW.date()
        methodology = build.build_provenance()
        output_root.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also prevents two local writers claiming a quarter.
        # Cleanup below owns only the directory this invocation created.
        try:
            target.mkdir()
        except FileExistsError:
            raise FileExistsError(f"Snapshot {target} already exists and is immutable; nothing changed.") from None
        try:
            inputs = target / "inputs"
            inputs.mkdir()
            manifest = {}
            for folder in [s["folder"] for s in build.SDKS] + ["curated"]:
                for source in sorted((source_root / folder).glob("*.json")):
                    relative = source.relative_to(source_root)
                    destination = inputs / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    data = source.read_bytes()
                    destination.write_bytes(data)
                    manifest[relative.as_posix()] = hashlib.sha256(data).hexdigest()

            # Compute exclusively from the bytes just frozen, even if the
            # original files change during this run.
            build.ROOT = inputs
            _, signals = build.render_dashboard()
            summary = {
                "schema_version": 1, "definition_version": build.DEFINITION_VERSION,
                "quarter": quarter, "created_at": methodology["generated_at"],
                "build_commit": methodology["build_commit"], "methodology": methodology,
                "inputs_sha256": manifest,
                "sdks": [{"key": sdk["key"], "folder": sdk["folder"], "label": sdk["label"],
                          "signals": signals[sdk["key"]]} for sdk in build.ACTIVE_SDKS],
            }
            (target / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            with (target / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
                fields = ["sdk", "metric", "window", "value", "coverage", "observed_at",
                          "unit", "sample_size", "window_end", "freshness", "reason", "protocol"]
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for sdk in summary["sdks"]:
                    for key, signal in sdk["signals"].items():
                        row = {field: signal.get(field) for field in fields}
                        # JSON cell values preserve null, numbers, strings and
                        # structured release context without losing type.
                        row.update(sdk=sdk["folder"], metric=key,
                                   value=json.dumps(signal["value"], separators=(",", ":"), allow_nan=False),
                                   protocol=json.dumps(signal["protocol"], separators=(",", ":")) if "protocol" in signal else "")
                        writer.writerow(row)
            (target / "README.md").write_text(
                f"This {quarter} snapshot freezes the SDK dashboard inputs byte for byte in `inputs/`, "
                "with SHA-256 hashes in `summary.json`. The JSON and CSV contain the same headline "
                f"values computed at {summary['created_at']} from source tree `{summary['build_commit']}`. "
                "The quarter labels the capture; rolling windows retain their dashboard meanings.\n\n"
                f"Methodology: [dashboard definitions at the producing commit]({methodology['definitions_at_commit']}), "
                f"definition version `{build.DEFINITION_VERSION}`. This directory is immutable: "
                "an existing quarter cannot be regenerated or overwritten.\n", encoding="utf-8")
        except BaseException:
            shutil.rmtree(target)
            raise
    finally:
        build.ROOT, build.NOW, build.TODAY = old_root, old_now, old_today
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quarter")
    parser.add_argument("--output-root", type=Path, help="Use a scratch directory for local validation.")
    args = parser.parse_args()
    try:
        result = create_snapshot(args.quarter, args.output_root)
    except (ValueError, FileExistsError) as error:
        parser.exit(1, f"Snapshot refused: {error}\n")
    print(f"Snapshot written to {result}")


if __name__ == "__main__":
    main()
