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
SCARF_TITLE = "<h2>Maven Central Daily Downloads (KMP, via Scarf)</h2>"
SCARF_LATEST = {"as_of": "2026-09-22", "downloads_90d": 21561, "unique_sources_90d": 2157}
SCARF_HEADLINE = ('\n    <div class="usage-headline">Downloads (last 90 days): 21,561 &middot; unique sources (last 90 days): 2,157</div>'
                  '\n    <div id="chart-scarf" class="chart"></div>')


def scarf_notice(last):
    return ('\n    <p class="muted">Scarf is delivering no Maven Central download data for this SDK. '
            f'{last} The daily chart and the 90-day totals return when delivery resumes.</p>')


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
        self.assertIn('<details class="definitions" id="definitions">',
                      page.splitlines()[build.definitions_line(page) - 1])
        with self.assertRaises(RuntimeError):
            build.definitions_line(page.replace('id="definitions"', ''))
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
                    if signal["protocol"]["sdk_change_required"] is False:
                        self.assertIsNone(lag)
                        self.assertEqual(signal["coverage"], "not_applicable")
                        self.assertIn("no SDK change required", page)
                        continue
                    phrase = (f"shipped {abs(lag)} days before activation" if lag < 0 else
                              f"shipped {lag} days after activation" if lag else "shipped on activation day")
                    self.assertIn(phrase, page)

        self.assert_compatibility_values(page, profiles)

    def assert_compatibility_values(self, page, profiles):
        card = page.split('id="compatibility"', 1)[1].split('<details class="compatibility-seps"', 1)[0]
        rows = dict(re.findall(r'<tr data-sdk="([^"]+)">(.*?)</tr>', card))
        for sdk in build.ACTIVE_SDKS:
            cells = {key: html.unescape(re.sub('<[^>]+>', '', text)) for key, text in
                     re.findall(r'<td data-kind="([^"]+)">(.*?)</td>', rows[sdk['key']])}
            seps = re.search(r'<details class="compatibility-seps" data-sdk="' + sdk['key'] + r'">(.*?)</details>', page)[1]
            cells['sep-versions'] = html.unescape(re.search(r'<span data-kind="sep-versions">(.*?)</span>', seps)[1])
            self.assertIn('<th>SEP</th><th>Title</th><th>Fields</th><th>Matrix version</th><th>Upstream version</th>'
                          '<th>Upstream updated</th><th>Matrix generated</th><th>Status</th><th>State</th><th>Evidence</th>', seps)
            signals = {key.removeprefix('compatibility.'): value for key, value in profiles[sdk['key']]['signals'].items()
                       if key.startswith('compatibility.')}
            source = json.loads((ROOT / sdk['folder'] / 'compatibility.json').read_text())
            for entry in source['seps']:
                row = re.search(r'<tr data-sep="' + str(entry['number']) + r'">(.*?)</tr>', seps)[1]
                self.assertTrue(row.startswith(f"<td class=\"nowrap\">SEP-{entry['number']}</td>"
                                               f"<td>{build.esc(build.display_text(entry['title']))}</td>"
                                               f"<td class=\"nowrap\">{entry['implemented']} of {entry['total']}"), row)
                if entry['server_only_excluded']:
                    self.assertIn(f"<td class=\"nowrap\">{entry['implemented']} of {entry['total']} "
                                  f"({entry['server_only_excluded']} server-only excluded)</td>", seps)
                if entry['state'] == 'current':
                    self.assertIn(f"<td>{entry['sep_version']}</td><td>{entry['upstream']['version']}</td>"
                                  f"<td>{entry['upstream']['updated'] or 'n/a'}</td><td>{entry['generated_date']}</td>"
                                  f"<td>{build.esc(build.display_text(entry['upstream']['status']))}</td>"
                                  '<td class="nowrap">at current version</td>', row)
            expected = {kind + '.' + field: source[kind][field] for kind in ('horizon', 'rpc')
                        for field in ('full', 'total', 'newer_stable_releases')}
            expected.update({'sep.' + key.removeprefix('sep_'): len(value) if isinstance(value, list) else value
                             for key, value in source['summary'].items() if key != 'complete'})
            self.assertEqual({key: signal['value'] for key, signal in signals.items()}, expected)
            for key, signal in signals.items():
                section, field = key.split('.')
                coverage = field in ('full', 'total', 'matrices', 'at_full')
                upstream = 'stellar_protocol' if section == 'sep' else section
                self.assertEqual((signal['unit'], signal['window'], signal['window_end'], signal['observed_at'], signal['source_files']),
                                 ('count', 'latest_release' if coverage else 'snapshot',
                                  source['source']['published_at'] if coverage else source['upstream'][upstream]['checked_at'],
                                  source['collected_at'], [sdk['folder'] + '/compatibility.json']))
                self.assertEqual(signal['compatibility'], {
                    'tag': source['source']['tag'], 'commit': source['source']['commit'],
                    'published_at': source['source']['published_at'],
                    'stellar_protocol_commit': source['upstream']['stellar_protocol']['commit'],
                    **{kind: {'version': source[kind]['version'], 'state': source[kind]['state'],
                              'current': source['upstream'][kind]['current'], 'newer': source[kind]['newer']}
                       for kind in ('horizon', 'rpc')}})
            for kind in ('horizon', 'rpc'):
                self.assertIn(f"{expected[kind + '.full']} of {expected[kind + '.total']} {'endpoints' if kind == 'horizon' else 'methods'}", cells[kind])
                releases = page.split('<summary>Upstream releases</summary>', 1)[1].split('</details>', 1)[0]
                row = re.search(r'<tr data-release="' + sdk['key'] + '-' + kind + r'">(.*?)</tr>', releases)[1]
                newer = html.unescape(re.sub('<[^>]+>', '', row.rsplit('<td>', 1)[1]))
                count = expected[kind + '.newer_stable_releases']
                self.assertEqual(newer.split(':')[0], str(count) if count is not None else 'n/a')
            self.assertIn(f"{expected['sep.at_full']} of {expected['sep.matrices']} report full coverage", cells['sep-matrices'])
            compared = expected['sep.current'] + expected['sep.version_differs']
            phrases = ([f"{expected['sep.current']} of {compared} at current version"] if compared else ['matrices record no version'])
            phrases += [f"{expected['sep.' + field]} {label}" for field, label in
                        [('updated_date_not_later', 'without upstream update recorded since generation'),
                         ('updated_date_later', 'with upstream update recorded since generation')] if expected['sep.' + field]]
            for field, label in [('version_differs', ' at a different version'), ('unknown', ' not compared'),
                                 ('no_version_field', ' no upstream version')]:
                numbers = source['summary']['sep_' + field]
                if numbers:
                    verb = (' has' if len(numbers) == 1 else ' have') if field == 'no_version_field' else ''
                    phrases.append(', '.join('SEP-' + str(n) for n in numbers) + verb + label)
            self.assertTrue(all(phrase in cells['sep-versions'] for phrase in phrases), (phrases, cells['sep-versions']))
        self.assertEqual(page.count('<td>Compatibility matrices<br>'), len(build.ACTIVE_SDKS))
        self.assertIn('Matrix-reported support; checks and exclusions differ by SDK. Matching versions do not verify conformance.', page)
        self.assertIn('SEP-23 has no matrix in any SDK.', page)
        self.assertLess(page.index('Protocol Delivery'), page.index('id="compatibility"'))
        self.assertLess(page.index('id="compatibility"'), page.index('Release Timeline'))

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
        # Ledger drafts (verified is not an ISO date) render no row and export no signal.
        delivery = page.split("<h2>Protocol Delivery</h2>", 1)[1].split('<div class="card"', 1)[0]
        for index, entry in enumerate(json.loads((ROOT / "curated" / "protocol-delivery.json").read_text())["upgrades"]):
            if not build.valid_verified_date(entry.get("verified")):
                self.assertNotIn(build.esc(entry["name"]), delivery)
                for profile in profiles.values():
                    self.assertFalse([key for key in profile["signals"] if key.startswith(f"protocol.{index}.")])

    def test_packagist_last_full_month_at_a_month_start(self):
        months = json.loads((ROOT / "stellar-php-sdk" / "packagist.json").read_text())["monthly_history"]["months"]
        by_month = {m["month"]: m for m in months}
        # 2 October 2026: September is complete in the file and Packagist has not reported an October day yet.
        october_2 = datetime(2026, 10, 2, 16, 30, tzinfo=timezone.utc)
        for build_now, month in ((NOW, "2026-08"), (october_2, "2026-09")):
            with patch.object(build, "NOW", build_now), patch.object(build, "TODAY", build_now.date()):
                page, profiles = self.generate()
            expected = by_month[month]
            signal = profiles["php"]["signals"]["packagist.last_full_month_downloads"]
            self.assertEqual(signal["window"], expected["month"])
            self.assertEqual(signal["value"], expected["downloads"])
            headline = re.search(r"last full month \((\d{4}-\d{2})\): ([\d,]+)", page)
            self.assertIsNotNone(headline)
            self.assertEqual(headline.groups(), (expected["month"], build.format_number(expected["downloads"])))

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
            self.assertEqual(json.loads(row["compatibility"]) if row["compatibility"] else None, signal.get("compatibility"))
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

    def test_protocol_export_without_sdk_change_is_not_applicable(self):
        original = build.load_json
        entry = {"name": "Protocol 29", "verified": "2026-10-02", "mainnet_activation_date": "2026-10-01",
                 "activation_url": "https://example.org/activation", "caps": [], "releases": {},
                 "sdk_change_required": False, "reason": "Security release without XDR or API changes",
                 "evidence_url": "https://example.org/notes"}
        fixture = {"upgrades": [entry]}
        with patch.object(build, "load_json", lambda path: fixture if path.name == "protocol-delivery.json" else original(path)):
            page, profiles = self.generate()
        self.assertIn("no SDK change required", page)
        for profile in profiles.values():
            metric = profile["signals"]["protocol.0.lag_days"]
            self.assertIsNone(metric["value"])
            self.assertEqual(metric["coverage"], "not_applicable")
            self.assertEqual(metric["reason"], entry["reason"])
            self.assertEqual(metric["evidence_urls"], ["https://example.org/activation", "https://example.org/notes"])
            self.assertIs(metric["protocol"]["sdk_change_required"], False)
            self.assertIsNone(metric["protocol"]["release_tag"])

    def scarf_page(self, latest, daily):
        """Render with a scarf.json fixture (None for latest: load_json returns None) and return the
        KMP card body, its two signals, DATA.scarf_daily and the page."""
        fixture = None if latest is None else {"schema_version": 1, "latest": latest, "packages_90d": [], "daily": daily}
        original = build.load_json
        with patch.object(build, "load_json", lambda path: fixture if path.name == "scarf.json" else original(path)):
            page, profiles = self.generate()
        self.assertEqual(page.count(SCARF_TITLE), 1)
        card = page.split(SCARF_TITLE, 1)[1].split("\n  </div>", 1)[0]
        chart = json.loads(re.search(r"const DATA = (.*);\n", page)[1])["scarf_daily"]
        signals = [profiles["kmp"]["signals"]["scarf." + key] for key in ("downloads_90d", "unique_sources_90d")]
        return card, signals, chart, page

    def assert_scarf_gap(self, card, signals, chart, page, last, reason):
        self.assertEqual(card, scarf_notice(last))
        self.assertNotIn('id="chart-scarf"', page)
        self.assertNotIn("Downloads (last 90 days)", page)
        self.assertEqual(chart, [])
        for signal in signals:
            self.assertEqual((signal["value"], signal["coverage"], signal["window"], signal["reason"]),
                             (None, "incomplete", "90d", reason))

    def assert_scarf_totals(self, card, signals, chart, daily):
        self.assertEqual(card, SCARF_HEADLINE)
        self.assertEqual(chart, daily)
        self.assertEqual([(s["value"], s["coverage"], s["window"], s["observed_at"]) for s in signals],
                         [(21561, "complete", "90d", "2026-09-22"), (2157, "complete", "90d", "2026-09-22")])

    def test_scarf_gap_after_ten_days_replaces_totals_with_notice(self):
        daily = [{"date": d, "downloads": n} for d, n in
                 (("2026-09-14", 0), ("2026-09-13", 0), ("2026-09-12", 0), ("2026-09-11", 8), ("2026-09-10", 20))]
        card, signals, chart, page = self.scarf_page({**SCARF_LATEST, "data_through": "2026-09-11"}, daily)
        self.assert_scarf_gap(card, signals, chart, page, "The last delivered day is 2026-09-11, checked 2026-09-22.",
                              "Scarf delivered no Maven Central download data after 2026-09-11; "
                              "the 90-day totals are undercounted and not published.")
        self.assertEqual([s["observed_at"] for s in signals], ["2026-09-22", "2026-09-22"])
        self.assertIn("When Scarf has delivered no day for more than 10 days, the KMP card states the last delivered day", page)
        self.assert_scarf_totals(*self.scarf_page({**SCARF_LATEST, "data_through": "2026-09-12"}, daily)[:3], daily)

    def test_scarf_gap_fallback_uses_the_newest_day_with_downloads(self):
        for newest, gap in (("2026-09-09", True), ("2026-09-19", False)):
            with self.subTest(newest=newest):
                daily = ([{"date": f"2026-09-{d}", "downloads": 0} for d in range(21, int(newest[-2:]), -1)]
                         + [{"date": newest, "downloads": 5}, {"date": "2026-09-01", "downloads": 7}])
                result = self.scarf_page(dict(SCARF_LATEST), daily)
                if gap:
                    self.assert_scarf_gap(*result, f"The last delivered day is {newest}, checked 2026-09-22.",
                                          f"Scarf delivered no Maven Central download data after {newest}; "
                                          "the 90-day totals are undercounted and not published.")
                else:
                    self.assert_scarf_totals(*result[:3], daily)

    def test_scarf_gap_without_any_delivered_day(self):
        reason = "Scarf has delivered no Maven Central download data yet; the 90-day totals are undercounted and not published."
        without_as_of = {key: value for key, value in SCARF_LATEST.items() if key != "as_of"}
        for latest, last in (({**SCARF_LATEST, "data_through": None}, "No day has been delivered yet, checked 2026-09-22."),
                             (without_as_of, "No day has been delivered yet.")):
            with self.subTest(latest=latest):
                self.assert_scarf_gap(*self.scarf_page(latest, []), last, reason)
        # A non-ISO data_through counts as missing, so the newest day with downloads decides.
        daily = [{"date": "2026-09-12", "downloads": 0}, {"date": "2026-09-09", "downloads": 3}]
        self.assert_scarf_gap(*self.scarf_page({**SCARF_LATEST, "data_through": "12.10.2026"}, daily),
                              "The last delivered day is 2026-09-09, checked 2026-09-22.",
                              "Scarf delivered no Maven Central download data after 2026-09-09; "
                              "the 90-day totals are undercounted and not published.")

    def test_scarf_local_file_failure_keeps_the_na_card(self):
        invalid_totals = {**SCARF_LATEST, "downloads_90d": "21561", "data_through": "2026-09-01"}
        for latest in (None, invalid_totals):
            with self.subTest(latest=latest):
                card, signals, chart, page = self.scarf_page(latest, [])
                self.assertEqual(card, '\n    <div class="usage-headline">Downloads (last 90 days): n/a &middot; '
                                       'unique sources (last 90 days): n/a</div>\n    <div id="chart-scarf" class="chart"></div>')
                self.assertNotIn("Scarf is delivering no", page)
                self.assertEqual(chart, [])
                for signal in signals:
                    self.assertEqual((signal["value"], signal["coverage"], signal["reason"]),
                                     (None, "incomplete", "Missing, malformed, or incomplete source data."))

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
