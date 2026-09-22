"""Offline snapshot/profile contracts. No network, commits, or real snapshots."""

import contextlib
import copy
import csv
from datetime import datetime, timezone
import hashlib
import html
import io
import itertools
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from dashboard import build
from dashboard.snapshot import create_snapshot


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 22, 12, 38, 36, tzinfo=timezone.utc)


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(build, "NOW", NOW))
        self.stack.enter_context(patch.object(build, "TODAY", NOW.date()))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(build, "OUT", self.temp / "docs" / "index.html"))

    def assert_clean_text(self, text):
        self.assertNotIn("\u2014", text)
        self.assertFalse(any(0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF for c in text))

    def assert_signal(self, signal):
        required = {"value", "unit", "sample_size", "window", "window_end", "coverage", "reason",
                    "observed_at", "freshness", "evidence_urls", "percentile", "percentile_reason",
                    "pool_size", "pool_size_reason", "source_files"}
        self.assertTrue(required <= signal.keys())
        self.assertIn(signal["coverage"], {"complete", "incomplete", "not_applicable"})
        self.assertIn(signal["freshness"], {"fresh", "stale", "unknown", "not_applicable"})
        self.assertIsInstance(signal["window"], str)
        if signal["value"] is None:
            self.assertTrue(signal["reason"])
            self.assertNotEqual(signal["coverage"], "complete")
        if signal["observed_at"] is not None:
            self.assertIsNotNone(build.parse_dt(signal["observed_at"]))
        self.assertTrue(all(build.valid_evidence_url(url) for url in signal["evidence_urls"]))
        self.assertTrue(all(isinstance(path, str) and not Path(path).is_absolute() and ".." not in Path(path).parts
                            for path in signal["source_files"]))
        self.assertIsNone(signal["percentile"])
        self.assertIsNone(signal["pool_size"])
        self.assertTrue(signal["percentile_reason"])
        self.assertTrue(signal["pool_size_reason"])

    def generate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            build.generate()
        output = build.OUT.parent / "profiles"
        index = json.loads((output / "index.json").read_text())
        self.assertEqual([s["key"] for s in index["sdks"]], [s["key"] for s in build.ACTIVE_SDKS])
        self.assertEqual(set(p.name for p in output.glob("*.json")),
                         {s["profile"] for s in index["sdks"]} | {"index.json"})
        profiles = {}
        page = build.OUT.read_text()
        self.assert_clean_text(page)
        for entry in index["sdks"]:
            text = (output / entry["profile"]).read_text()
            self.assert_clean_text(text)
            profile = json.loads(text)
            self.assert_clean_text(json.dumps(profile, ensure_ascii=False))
            self.assertEqual(profile["schema_version"], 1)
            self.assertEqual(profile["methodology"], index["methodology"])
            self.assertEqual(profile["as_of"], profile["methodology"]["as_of"])
            self.assertRegex(profile["methodology"]["build_commit"], r"^[0-9a-f]{40}$")
            self.assertEqual(profile["methodology"]["definition_version"], build.DEFINITION_VERSION)
            self.assertTrue(profile["methodology"]["dashboard_url"].endswith("#definitions"))
            self.assertTrue(profile["methodology"]["generated_at"])
            self.assertIsNone(profile["comparison_pool"])
            self.assertTrue(profile["comparison_pool_reason"])
            for signal in profile["signals"].values():
                self.assert_signal(signal)
                for url in signal["evidence_urls"]:
                    self.assertIn('href="' + build.esc(url) + '"', page)
            for key in ("activity.days_since_last_push", "activity.non_merge_commits",
                        "issues.answered_within_7d", "prs.answered_within_7d",
                        "maintainer_issues.created_90d", "maintainer_prs.created_90d", "host.contributor_activity"):
                self.assertIsNone(profile["signals"][key]["value"])
                self.assertTrue(profile["signals"][key]["reason"])
            profiles[entry["key"]] = profile
        return page, profiles

    def assert_page_values(self, page, profiles):
        # Read the actual generated card markup, independently of the recorder.
        cards = page.split('<div class="sdk-card">')[1:]
        for sdk, card in zip(build.ACTIVE_SDKS, cards):
            rows = dict((html.unescape(label), html.unescape(value)) for label, value in re.findall(
                r'<span class="sdk-stat-label">(.*?)</span><span class="sdk-stat-value"[^>]*>(.*?)</span>', card))
            m = profiles[sdk["key"]]["signals"]
            val = lambda key: m[key]["value"]
            self.assertEqual(rows["Days since last stable release"], build.format_days(val("release.days_since_last")))
            self.assertEqual(rows["Median release gap (365d)"], build.format_days(val("release.median_gap_days_365d")) + " days")
            self.assertEqual(rows["Stable releases (90d / 365d)"], f'{val("release.count_90d")} / {val("release.count_365d")}')
            for kind, label in (("issues", "issues"), ("prs", "PRs")):
                self.assertEqual(rows[f"Open {label}"], build.format_number(val(f"{kind}.open_count")))
                n, a = val(f"{kind}.response_eligible_90d"), val(f"{kind}.answered_within_48h_90d")
                self.assertEqual(rows[f"Community {label} answered within 48h (90d)"],
                                 f"{a} of {n}" if n else "no eligible items")
                median = m[f"{kind}.median_first_response_hours"]
                self.assertEqual(rows[f"Median first response (community {label}, 90d)"],
                                 "not applicable" if median["coverage"] == "not_applicable" else build.format_hours(median["value"]))
                created, closed = val(f"{kind}.created_365d"), val(f"{kind}.closed_365d")
                self.assertEqual(rows[f"Community {label} (365d)"],
                                 f"{created} opened, {closed} closed" if created else "no eligible items")
            disposition = (f'{val("prs.merged_90d"):,} merged, '
                           f'{val("prs.closed_without_merge_90d"):,} closed without merge, '
                           f'{val("prs.still_open_90d"):,} open')
            self.assertEqual(rows["Community PR disposition (90d)"], disposition)
            for key, signal in m.items():
                if key.startswith("protocol."):
                    lag = signal["value"]
                    phrase = (f"shipped {abs(lag)} days before activation" if lag < 0 else
                              f"shipped {lag} days after activation" if lag else "shipped on activation day")
                    self.assertIn(phrase, page)

    def test_real_profiles_match_page_and_loaded_inputs(self):
        original = build.load_json
        reads = []
        def load_once(path):
            self.assertNotIn(path, reads, "A source must not be reloaded for profiles")
            reads.append(path)
            return original(path)
        with patch.object(build, "load_json", load_once):
            page, profiles = self.generate()
        self.assert_page_values(page, profiles)
        for sdk in build.ACTIVE_SDKS:
            source = json.loads((ROOT / sdk["folder"] / "github-issues.json").read_text())
            metrics = profiles[sdk["key"]]["signals"]
            for kind in ("issues", "prs"):
                for key in ("response_eligible_90d", "answered_within_48h_90d", "unanswered_90d"):
                    self.assertEqual(metrics[f"{kind}.{key}"]["value"], source["summary"][f"community_{kind}_{key}"])
            self.assertEqual(metrics["issues.response_eligible_90d"]["observed_at"], source["summary"]["response_collected_at"])
        self.assertIsNone(profiles["kmp"]["signals"]["reach.dependents"]["value"])
        self.assertEqual(profiles["kmp"]["signals"]["reach.dependents"]["coverage"], "not_applicable")

    def test_snapshot_bytes_csv_values_and_immutability(self):
        page, profiles = self.generate()
        target = create_snapshot("2099-Q4", self.temp / "snapshots", now=NOW)
        summary = json.loads((target / "summary.json").read_text())
        inputs = [p for folder in [s["folder"] for s in build.SDKS] + ["curated"]
                  for p in (ROOT / folder).glob("*.json")]
        self.assertEqual(set(summary["inputs_sha256"]), {str(p.relative_to(ROOT)) for p in inputs})
        for path in inputs:
            relative = path.relative_to(ROOT)
            data = (target / "inputs" / relative).read_bytes()
            self.assertEqual(data, path.read_bytes())
            self.assertEqual(summary["inputs_sha256"][str(relative)], hashlib.sha256(data).hexdigest())
        self.assertEqual(summary["build_commit"], summary["methodology"]["build_commit"])
        self.assertEqual(summary["definition_version"], build.DEFINITION_VERSION)
        self.assertEqual(summary["created_at"], summary["methodology"]["generated_at"])
        expected = {}
        for sdk in summary["sdks"]:
            self.assertEqual(sdk["signals"], profiles[sdk["key"]]["signals"])
            for metric, signal in sdk["signals"].items():
                expected[(sdk["folder"], metric)] = signal
        with (target / "summary.csv").open(newline="") as stream:
            reader = csv.DictReader(stream)
            self.assertTrue({"sdk", "metric", "window", "value", "coverage", "observed_at"} <= set(reader.fieldnames))
            rows = list(reader)
        self.assertEqual(len(rows), len(expected))
        self.assertEqual(len({(r["sdk"], r["metric"]) for r in rows}), len(rows))
        for row in rows:
            signal = expected[(row["sdk"], row["metric"])]
            self.assertEqual(json.loads(row["value"]), signal["value"])
            for field in ("window", "coverage", "observed_at"):
                self.assertEqual(row[field], signal[field] or "")
        self.assertEqual(len((target / "README.md").read_text().strip().split("\n\n")), 2)
        for filename in ("summary.json", "summary.csv", "README.md"):
            self.assert_clean_text((target / filename).read_text())
        before = tree_bytes(self.temp / "snapshots")
        with self.assertRaisesRegex(FileExistsError, "immutable"):
            create_snapshot("2099-Q4", self.temp / "snapshots", now=NOW)
        self.assertEqual(before, tree_bytes(self.temp / "snapshots"))
        self.assert_page_values(page, profiles)

    def test_snapshot_rejects_bad_labels_existing_empty_dirs_and_symlinks(self):
        destination = self.temp / "snapshots"
        for quarter in ("2026-Q0", "2026-Q5", "1999-Q4", "2100-Q1", "2026-Q1\n", "../2026-Q1", "$(bad)", "", None):
            with self.assertRaises(ValueError):
                create_snapshot(quarter, destination)
            self.assertFalse(destination.exists())
        destination.mkdir()
        (destination / "2026-Q1").mkdir()
        (destination / "2026-Q2").symlink_to(self.temp / "missing")
        for quarter in ("2026-Q1", "2026-Q2"):
            with self.assertRaises(FileExistsError):
                create_snapshot(quarter, destination)
        self.assertTrue((destination / "2026-Q2").is_symlink())
        self.assertEqual(list((destination / "2026-Q1").iterdir()), [])

    def test_failed_snapshot_cleans_only_its_new_directory(self):
        destination = self.temp / "snapshots"
        destination.mkdir()
        (destination / "unrelated").write_bytes(b"preserve")
        with patch.object(build, "render_dashboard", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                create_snapshot("2099-Q4", destination, now=NOW)
        self.assertEqual(tree_bytes(destination), {"unrelated": b"preserve"})
        self.assertEqual(build.ROOT, ROOT)

    def test_competing_snapshot_writer_is_not_removed(self):
        destination = self.temp / "snapshots"
        target = destination / "2099-Q4"
        mkdir = Path.mkdir
        def competing_mkdir(path, *args, **kwargs):
            if path == target:
                mkdir(path, *args, **kwargs)
                (path / "other-writer").write_bytes(b"preserve")
                raise FileExistsError("Competing writer claimed the directory")
            return mkdir(path, *args, **kwargs)
        with patch.object(Path, "mkdir", competing_mkdir):
            with self.assertRaisesRegex(FileExistsError, "immutable"):
                create_snapshot("2099-Q4", destination, now=NOW)
        self.assertEqual(tree_bytes(target), {"other-writer": b"preserve"})
        self.assertEqual(build.ROOT, ROOT)

    def test_snapshot_computes_from_copies_even_if_originals_change(self):
        source = self.temp / "source"
        source.mkdir()
        sdk = build.SDKS[0]
        for folder in [s["folder"] for s in build.SDKS] + ["curated"]:
            (source / folder).mkdir()
        path = source / sdk["folder"] / "github-activity.json"
        data = (ROOT / sdk["folder"] / path.name).read_bytes()
        path.write_bytes(data)
        render = build.render_dashboard
        def changed_original():
            path.write_text("null")
            self.assertNotEqual(build.ROOT, source)
            return render()
        with patch.object(build, "render_dashboard", changed_original):
            target = create_snapshot("2099-Q4", self.temp / "snapshots", source, NOW)
        summary = json.loads((target / "summary.json").read_text())
        self.assertEqual((target / "inputs" / sdk["folder"] / path.name).read_bytes(), data)
        self.assertIsNotNone(summary["sdks"][0]["signals"]["release.count_90d"]["value"])

    def test_independent_coverage_malformed_counts_and_zero(self):
        original = build.load_json
        fixture = json.loads((ROOT / build.SDKS[0]["folder"] / "github-issues.json").read_text())
        with patch.object(build, "load_json", lambda path: fixture if path.name == "github-issues.json" else original(path)):
            fixture["summary"]["response_coverage"] = "incomplete"
            page, profiles = self.generate()
            for profile in profiles.values():
                m = profile["signals"]
                self.assertIsNone(m["issues.answered_within_48h_90d"]["value"])
                self.assertIsNone(m["prs.merged_90d"]["value"])
                self.assertIsNotNone(m["issues.created_365d"]["value"])
                self.assertEqual(m["issues.open_count"]["value"], 0)
                self.assertEqual(m["issues.open_median_age_days"]["coverage"], "not_applicable")
            fixture["summary"]["response_coverage"] = "complete"
            for bad in (True, -1, "2", [], 999):
                fixture["summary"]["community_issues_answered_within_48h_90d"] = bad
                _, profiles = self.generate()
                for p in profiles.values():
                    self.assertIsNone(p["signals"]["issues.answered_within_48h_90d"]["value"])
                    self.assertIsNone(p["signals"]["issues.median_first_response_hours"]["value"])
            fixture["summary"]["coverage"] = "incomplete"
            _, profiles = self.generate()
            for p in profiles.values():
                self.assertIsNone(p["signals"]["issues.created_365d"]["value"])

    def test_all_16_subsets_times_malformed_payloads(self):
        original = build.load_json
        hostile = {
            "summary": {"definition_version": 3, "coverage": "incomplete", "response_coverage": "incomplete"},
            "issues": [{"type": "pr", "number": '<img src=x onerror=alert(1)>', "author": "user",
                        "author_association": [], "created_at": NOW.isoformat(), "url": "javascript:alert(1)"}],
            "open_scan": {"open_issues": "bad", "open_prs": True},
        }
        for count in range(5):
            for keys in itertools.combinations([s["key"] for s in build.SDKS], count):
                active = [s for s in build.SDKS if s["key"] in keys]
                with patch.object(build, "ENABLED_SDKS", set(keys)), patch.object(build, "ACTIVE_SDKS", active):
                    for malformed in ("real", None, [], 1, "bad", {}, hostile):
                        def loader(path):
                            if malformed == "real":
                                return original(path)
                            if malformed is hostile:
                                return copy.deepcopy(hostile) if path.name == "github-issues.json" else original(path)
                            return malformed
                        with self.subTest(keys=keys, payload=repr(malformed)), patch.object(build, "load_json", loader):
                            page, profiles = self.generate()
                            self.assertNotIn('href="javascript:', page)
                            self.assertNotIn("<img src=x", page)
                            for profile in profiles.values():
                                m = profile["signals"]
                                if malformed != "real":
                                    self.assertIsNone(m["issues.open_count"]["value"])
                                    self.assertIsNone(m["issues.answered_within_48h_90d"]["value"])
                                if malformed != "real" and malformed is not hostile:
                                    self.assertIsNone(m["release.count_90d"]["value"])
                                    self.assertEqual(m["release.count_90d"]["coverage"], "incomplete")
                                    self.assertIsNone(m["protocol.lag_days"]["value"])

    def test_successful_empty_releases_remain_real_zero(self):
        original = build.load_json
        fixture = {"releases": {"all": [], "collected_at": "2026-09-01T12:00:00Z"}}
        with patch.object(build, "load_json", lambda path: fixture if path.name == "github-activity.json" else original(path)):
            page, profiles = self.generate()
        self.assertIn("insufficient release history", page)
        for profile in profiles.values():
            m = profile["signals"]
            for key in ("release.count_90d", "release.count_365d", "release.all_time_count"):
                self.assertEqual(m[key]["value"], 0)
                self.assertEqual(m[key]["coverage"], "complete")
                self.assertEqual(m[key]["freshness"], "stale")
            self.assertIsNone(m["release.days_since_last"]["value"])
            self.assertIsNone(m["release.median_gap_days_365d"]["value"])
            self.assertEqual(m["release.median_gap_days_365d"]["sample_size"], 0)

    def test_protocol_export_gates_utc_lag_and_missing_release(self):
        original = build.load_json
        entry = {"name": "Protocol test", "verified": "2026-09-22",
                 "mainnet_activation_date": "2026-09-22", "activation_url": "https://example.org/activation",
                 "caps": [{"name": "CAP", "url": "https://example.org/cap"}],
                 "releases": {"ios": {"tag": "v1", "published_at": "2026-09-22T00:30:00+02:00",
                                      "url": "https://example.org/release"}}}
        fixture = {"upgrades": [entry, {**entry, "verified": None}]}
        with patch.object(build, "load_json", lambda path: fixture if path.name == "protocol-delivery.json" else original(path)):
            page, profiles = self.generate()
        self.assertIn("shipped 1 day before activation", page)
        for key, profile in profiles.items():
            m = profile["signals"]
            self.assertNotIn("protocol.1.lag_days", m)
            metric = m["protocol.0.lag_days"]
            self.assertEqual(metric["observed_at"], "2026-09-22")
            self.assertEqual(metric["value"], -1 if key == "ios" else None)
            self.assertEqual(metric["coverage"], "complete" if key == "ios" else "incomplete")
        self.assertEqual(profiles["ios"]["signals"]["protocol.0.lag_days"]["protocol"]["release_date"], "2026-09-21")

    def test_serialization_failure_does_not_publish_partial_outputs(self):
        self.generate()
        before = tree_bytes(build.OUT.parent)
        with patch.object(build, "profile_documents", side_effect=ValueError("injected failure")):
            with self.assertRaises(ValueError):
                build.generate()
        self.assertEqual(tree_bytes(build.OUT.parent), before)

    def test_snapshot_refuses_partial_source_tree(self):
        source = self.temp / "partial-source"
        (source / build.SDKS[0]["folder"]).mkdir(parents=True)
        with self.assertRaises(ValueError):
            create_snapshot("2099-Q4", self.temp / "snapshots-partial", source, NOW)
        self.assertFalse((self.temp / "snapshots-partial").exists())

    def test_hostile_nested_fields_degrade_without_aborting(self):
        original = build.load_json
        sdk = build.SDKS[0]
        issues = json.loads((ROOT / sdk["folder"] / "github-issues.json").read_text())
        issues["summary"]["collected_at"] = float("inf")
        activity = json.loads((ROOT / sdk["folder"] / "github-activity.json").read_text())
        activity["releases"]["all"][0]["tag"] = {"x": float("inf")}

        def loader(path):
            if sdk["folder"] in str(path):
                if path.name == "github-issues.json":
                    return issues
                if path.name == "github-activity.json":
                    return activity
            return original(path)

        with patch.object(build, "load_json", loader):
            page, profiles = self.generate()
        signals = profiles[sdk["key"]]["signals"]
        self.assertIsNone(signals["release.latest_stable"]["value"])
        for record in signals.values():
            self.assertNotEqual(record["window_end"], float("inf"))
        other = profiles[build.SDKS[1]["key"]]["signals"]
        self.assertIsNotNone(other["release.count_365d"]["value"])
        json.dumps(profiles, allow_nan=False)

        source = self.temp / "hostile-source"
        for folder in [s["folder"] for s in build.SDKS] + ["curated"]:
            (source / folder).mkdir(parents=True)
            for path in sorted((ROOT / folder).glob("*.json")):
                (source / folder / path.name).write_bytes(path.read_bytes())
        (source / sdk["folder"] / "github-issues.json").write_text(json.dumps(issues))
        (source / sdk["folder"] / "github-activity.json").write_text(json.dumps(activity))
        target = create_snapshot("2099-Q2", self.temp / "snapshots-hostile", source, NOW)
        summary = json.loads((target / "summary.json").read_text())
        self.assertIsNone(summary["sdks"][0]["signals"]["release.latest_stable"]["value"])

    def test_numeric_gates_keep_big_integers_and_null_non_finite(self):
        signals = build.Signals()
        signals.add("big", 10 ** 400, "snapshot")
        self.assertEqual(signals["big"]["value"], 10 ** 400)
        signals.add("infinite", float("inf"), "snapshot")
        self.assertIsNone(signals["infinite"]["value"])
        signals.add("nested", {"tag": float("nan")}, "latest", numeric=False)
        self.assertIsNone(signals["nested"]["value"])
        signals.add("junk_window", 1, ["not-a-window"], window_end=float("inf"))
        self.assertEqual(signals["junk_window"]["window"], "unknown")
        self.assertIsNone(signals["junk_window"]["window_end"])
        json.dumps(signals, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
