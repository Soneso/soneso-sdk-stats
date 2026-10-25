"""Offline tests of the inline Scarf collector with a fixed date and a fake export fetch."""
import contextlib
from datetime import date, timedelta
import io
import json
from pathlib import Path
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/collect-scarf.yml'
SOURCE = textwrap.dedent(WORKFLOW.read_text().split("python3 <<'PYTHON'\n", 1)[1].split('\n          PYTHON', 1)[0])
TODAY = date(2026, 10, 25)


def day(offset):
    return (TODAY - timedelta(days=offset)).isoformat()


class ScarfCollectionTests(unittest.TestCase):

    def setUp(self):
        self.m = {'__name__': 'collect_scarf'}
        exec(compile(SOURCE, str(WORKFLOW), 'exec'), self.m)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name, 'kmp-stellar-sdk', 'scarf.json')

    def fetch_for(self, daily, artifact_total=40):
        """Fake fetch: daily by-total rows for {offset: total}, fixed window and origin exports."""
        def fetch(params):
            if 'rollup=daily' in params:
                return [{'date': day(offset), 'total': total} for offset, total in daily.items()]
            if 'breakdown=by-origin' in params:
                return [{'origin_id': 'o1'}, {'origin_id': 'o2'}]
            return [{'artifact_name': 'stellar-sdk', 'total': artifact_total, 'unique_origins': 2}]
        return fetch

    def run_collector(self, fetch):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.m['run'](TODAY, fetch, str(self.path))
        return json.loads(self.path.read_text()), out.getvalue()

    def write_existing(self, daily, as_of_offset=1):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            'schema_version': 1,
            'latest': {'as_of': day(as_of_offset), 'downloads_90d': 9, 'unique_sources_90d': 1},
            'packages_90d': [], 'daily': [{'date': d, 'downloads': n} for d, n in daily.items()]}, indent=2) + '\n')

    def test_zero_fill_stops_at_newest_exported_day_and_gap_warns(self):
        data, out = self.run_collector(self.fetch_for({20: 30, 16: 12, 14: 9, 13: 8}))
        stored = {e['date']: e['downloads'] for e in data['daily']}
        expected = {day(o): 0 for o in range(13, 45)}
        expected.update({day(20): 30, day(16): 12, day(14): 9, day(13): 8})
        self.assertEqual(stored, expected)
        self.assertEqual([e['date'] for e in data['daily']], sorted(expected, reverse=True))
        self.assertEqual(data['latest'], {
            'as_of': '2026-10-25', 'data_through': '2026-10-12', 'downloads_90d': 40,
            'unique_sources_90d': 2, 'window_start': '2026-07-28', 'window_end': '2026-10-26'})
        self.assertIn('::warning::Scarf has delivered no Maven Central data after 2026-10-12 (13 days)\n', out)

    def test_merge_drops_window_days_the_export_no_longer_produces(self):
        self.write_existing({day(9): 0, day(10): 0, day(12): 0, day(14): 999, day(60): 77, day(44): 5})
        data, _ = self.run_collector(self.fetch_for({14: 9, 13: 8}))
        stored = {e['date']: e['downloads'] for e in data['daily']}
        self.assertEqual(stored, {**{day(o): 0 for o in range(13, 45)}, day(14): 9, day(13): 8, day(60): 77})

    def test_fresh_data_fills_to_newest_day_and_gap_boundary(self):
        data, out = self.run_collector(self.fetch_for({30: 4, 3: 7}))
        stored = {e['date']: e['downloads'] for e in data['daily']}
        self.assertEqual(data['latest']['data_through'], day(3))
        self.assertEqual(stored, {**{day(o): 0 for o in range(4, 45)}, day(30): 4, day(3): 7})
        self.assertNotIn('::warning::', out)
        for offset, warned in ((10, False), (11, True)):
            with self.subTest(offset=offset):
                data, out = self.run_collector(self.fetch_for({offset: 1}))
                self.assertEqual(data['latest']['data_through'], day(offset))
                self.assertEqual(f'::warning::Scarf has delivered no Maven Central data after {day(offset)} ({offset} days)' in out, warned)
                self.assertEqual('::warning::' in out, warned)

    def test_rejected_exports_keep_the_existing_file(self):
        def failing(params):
            raise OSError('connection reset')
        cases = [
            (self.fetch_for({}), 1, 0, '::warning::Scarf export was empty or failed validation; keeping existing data\n'),
            (failing, 1, 0, '::warning::Scarf API fetch failed: connection reset; keeping existing data\n'),
            (self.fetch_for({20: 3}, artifact_total=0), 1, 0,
             '::warning::Scarf export reported zero downloads over the summary window; keeping existing data\n'),
            (self.fetch_for({}), 8, 1, '::error::Scarf data is stale or unusable (last good run: 2026-10-17)\n'),
        ]
        for fetch, as_of_offset, code, message in cases:
            with self.subTest(message=message):
                self.write_existing({day(20): 3}, as_of_offset)
                before = self.path.read_bytes()
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exit_:
                    self.m['run'](TODAY, fetch, str(self.path))
                self.assertEqual(exit_.exception.code, code)
                self.assertIn(message, out.getvalue())
                self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
