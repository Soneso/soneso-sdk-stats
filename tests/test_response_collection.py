"""Offline regression checks for response collection and protocol delivery.

Run with: python3 -m unittest discover -s tests -v
The workflow's actual inline Python is exercised with mocked HTTP responses.
"""
import contextlib
import html
import copy
import io
import itertools
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/collect-github-issues.yml'
SOURCE = textwrap.dedent(WORKFLOW.read_text().split("python3 <<'PYTHON'\n", 1)[1].split('\n          PYTHON', 1)[0])
NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
REPO = 'Soneso/stellar-ios-mac-sdk'
FOLDER = 'stellar-ios-mac-sdk'


def stamp(hours=0):
    return (NOW + timedelta(hours=hours)).isoformat().replace('+00:00', 'Z')


def api_item(number=1, kind='issue', **updates):
    item = dict(number=number, title='Community item', state='closed',
                created_at=stamp(-96), closed_at=stamp(-24), updated_at=stamp(-24),
                user={'login': 'contributor'}, author_association='NONE', labels=[],
                html_url=f'https://github.com/{REPO}/issues/{number}')
    if kind == 'pr':
        item.update(pull_request={}, draft=False)
    item.update(updates)
    return item


def item(kind='issue', **updates):
    value = dict(number=1, type=kind, author='contributor', author_association='NONE',
                 created_at=stamp(-96), state='closed', draft=False)
    value.update(updates)
    return value


def comment(at=None, login='maintainer', association='MEMBER', **updates):
    value = dict(created_at=at or stamp(-95), user={'login': login} if login else None,
                 author_association=association, html_url='https://github.com/example/comment/1')
    value.update(updates)
    return value


def event(kind='closed', at=None, login='maintainer', **updates):
    value = dict(event=kind, created_at=at or stamp(-95),
                 actor={'login': login} if login else None, url='https://api.github.com/example/events/1')
    value.update(updates)
    return value


def helpers():
    namespace = {}
    exec(compile(SOURCE.split('for repo, folder in REPOS.items():', 1)[0], str(WORKFLOW), 'exec'), namespace)
    namespace.update(now=NOW, today_str='2026-09-21', cutoff_90d='2026-06-24')
    return namespace


def build_module():
    path = ROOT / 'dashboard/build.py'
    namespace = {'__file__': str(path), '__name__': 'test_build'}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    namespace.update(NOW=NOW, TODAY=NOW.date())
    return namespace


class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.m = helpers()

    def collect(self, value=None, timeline=None, comments=None, reviews=None, review_comments=None, closures=None):
        def fetch(path, **kwargs):
            if '/timeline?' in path:
                return timeline or []
            if '/reviews?' in path:
                return reviews or []
            if '/pulls/' in path:
                return review_comments or []
            return comments or []
        self.m['fetch_all_pages'] = fetch
        self.m['linked_closures'] = lambda *args: closures or []
        return self.m['collect_response'](REPO, value or item())

    def test_full_comment_scan_and_actor_rules(self):
        comments = [comment(login='outsider', association='NONE') for _ in range(101)]
        comments += [comment(login='contributor'), comment(login='maintainer[bot]'), comment()]
        result = self.collect(comments=comments)
        self.assertEqual(result['response']['actor'], 'maintainer')
        self.assertEqual(result['response']['hours'], 1)

    def test_reviews_and_review_comments_precede_later_discussion(self):
        # The maintainer's reply sits in the author's own submitted review,
        # so it is published at its creation time and precedes everything.
        result = self.collect(item('pr'), comments=[comment(stamp(-40))],
                              reviews=[comment(submitted_at=None, id=7),
                                       comment(submitted_at=stamp(-94)),
                                       comment(submitted_at=stamp(-96), id=5, login='contributor')],
                              review_comments=[comment(stamp(-95), pull_request_review_id=5)])
        self.assertEqual(result['response']['kind'], 'review_comment')
        self.assertEqual(result['response']['at'], stamp(-95))
        result = self.collect(item('pr'), reviews=[comment(submitted_at=stamp(-94))])
        self.assertEqual(result['response']['kind'], 'review')
        self.assertIsNone(self.collect(item('pr'), reviews=[comment(submitted_at=None)])['response'])

    def test_review_comment_counts_from_publication(self):
        # Drafted at hour 47, published with its review at hour 49: the
        # response time is the submission, not the invisible draft time.
        result = self.collect(item('pr'),
                              reviews=[comment(submitted_at=stamp(-47), id=3)],
                              review_comments=[comment(stamp(-49), pull_request_review_id=3)])
        self.assertEqual(result['response']['at'], stamp(-47))
        self.assertEqual(result['response']['hours'], 49)
        # A reply created after submission keeps its own later time.
        result = self.collect(item('pr'),
                              reviews=[comment(submitted_at=stamp(-47), id=3, login='contributor')],
                              review_comments=[comment(stamp(-46), pull_request_review_id=3)])
        self.assertEqual(result['response']['kind'], 'review_comment')
        self.assertEqual(result['response']['at'], stamp(-46))
        # Comments inside an unsubmitted review are invisible and never count.
        result = self.collect(item('pr'), reviews=[comment(submitted_at=None, id=9)],
                              review_comments=[comment(stamp(-95), pull_request_review_id=9)])
        self.assertIsNone(result['response'])
        # A comment with no resolvable parent review is an evidence gap.
        with self.assertRaises(ValueError):
            self.collect(item('pr'), review_comments=[comment(stamp(-95), pull_request_review_id=9)])

    def test_pr_merge_close_and_manual_issue_close(self):
        self.assertIsNone(self.collect(timeline=[event()])['response'])
        self.assertEqual(self.collect(item('pr'), timeline=[event()])['response']['kind'], 'close')
        result = self.collect(item('pr'), timeline=[event(), event('merged')])
        self.assertEqual(result['response']['kind'], 'merge')
        self.assertEqual(result['disposition'], 'merged')
        self.assertIsNone(self.collect(item('pr'), timeline=[event(login='contributor')])['response'])
        self.assertIsNone(self.collect(item('pr'), timeline=[event(login='actor[bot]')])['response'])

    def test_linked_commit_and_explicit_pr_closer(self):
        self.assertEqual(self.collect(timeline=[event(commit_id='abc')])['response']['kind'], 'linked_close')
        closure = {'createdAt': stamp(-95), 'actor': None,
                   'closer': {'__typename': 'PullRequest', 'mergedAt': stamp(-95),
                              'mergedBy': {'login': 'maintainer'}, 'url': 'https://github.com/test/pull/2'}}
        self.assertEqual(self.collect(timeline=[event()], closures=[closure])['response']['actor'], 'maintainer')
        closure['closer']['mergedAt'] = None
        self.assertIsNone(self.collect(timeline=[event()], closures=[closure])['response'])
        # A mention by itself cannot make a manual close count.
        self.assertIsNone(self.collect(timeline=[event(), event('cross-referenced')])['response'])

    def test_unknown_attribution_only_when_it_changes_first_response(self):
        result = self.collect(item('pr'), timeline=[event(login=None)], comments=[comment(stamp(-94))])
        self.assertEqual(result['attribution'], 'unknown')
        self.assertIsNone(result['response'])
        result = self.collect(item('pr'), timeline=[event(login=None, at=stamp(-94))], comments=[comment()])
        self.assertEqual(result['attribution'], 'known')
        result = self.collect(item('pr'), timeline=[event(login=None)], comments=[comment()])
        self.assertEqual(result['attribution'], 'known')

    def test_draft_clocks(self):
        result = self.collect(item('pr'), timeline=[event('ready_for_review', stamp(-72))], comments=[comment()])
        self.assertEqual(result['response_clock']['at'], stamp(-72))
        self.assertEqual(result['response']['hours'], 0)
        result = self.collect(item('pr', draft=True))
        self.assertEqual(result['response_clock']['status'], 'unknown')
        result = self.collect(item('pr'), timeline=[event('convert_to_draft'), event('ready_for_review', stamp(-72))])
        self.assertEqual(result['response_clock']['kind'], 'creation')

    def test_exact_maturity_and_answer_thresholds_and_slow_median(self):
        values = []
        for age, delay in [(48, 48), (47.9999, 1), (96, 48 + 1/3600), (96, 72)]:
            value = item(created_at=stamp(-age))
            value.update(self.collect(value, comments=[comment(stamp(-age + delay))]))
            values.append(value)
        unanswered = item()
        unanswered.update(self.collect())
        values.append(unanswered)
        stats = self.m['response_stats'](values)
        self.assertEqual(stats['response_eligible_90d'], 4)
        self.assertEqual(stats['answered_within_48h_90d'], 1)
        self.assertEqual(stats['pending_90d'], 1)
        self.assertEqual(stats['unanswered_90d'], 1)
        self.assertEqual(stats['median_first_response_hours'], 48.0)

    def test_draft_maturity_unknown_exclusions_and_window(self):
        unknown = item('pr')
        unknown.update(self.collect(unknown, timeline=[event(login=None)]))
        draft = item('pr', draft=True)
        draft.update(self.collect(draft))
        pending = item('pr')
        pending.update(self.collect(pending, timeline=[event('ready_for_review', stamp(-47))]))
        stats = self.m['response_stats']([unknown, draft, pending])
        self.assertEqual(stats['response_eligible_90d'], 0)
        self.assertEqual(stats['unknown_attribution_90d'], 1)
        self.assertEqual(stats['unknown_clock_90d'], 1)
        self.assertEqual(stats['pending_90d'], 1)
        community = self.m['community_item']
        self.assertTrue(community(item(created_at='2026-06-24T00:00:00Z'), '2026-06-24'))
        self.assertFalse(community(item(created_at='2026-06-23T23:59:59Z'), '2026-06-24'))
        for update in ({'removed': True}, {'author': 'test[bot]'}, {'author_association': 'MEMBER'}):
            self.assertFalse(community(item(**update), '2026-06-24'))

    def test_pagination_and_mid_page_failure(self):
        calls = []
        def fetch(path, **kwargs):
            calls.append(path)
            return ([{'number': len(calls)}], 'page2' if path == 'page1' else None)
        self.m['fetch_json'] = fetch
        self.assertEqual(len(self.m['fetch_all_pages']('page1')), 2)
        def fail(path, **kwargs):
            if path == 'page2':
                raise TimeoutError('mid-page failure')
            return fetch(path)
        self.m['fetch_json'] = fail
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(self.m['fetch_all_pages']('page1'))

    def test_graphql_closer_pagination_and_budget(self):
        calls = []
        def fetch(path, **kwargs):
            calls.append(kwargs.get('arguments'))
            page = {'nodes': [{'createdAt': stamp(), 'closer': None}],
                    'pageInfo': {'hasNextPage': len(calls) == 1, 'endCursor': 'next'}}
            return {'data': {'repository': {'issue': {'timelineItems': page}}}}, None
        self.m['fetch_json'] = fetch
        self.assertEqual(len(self.m['linked_closures'](REPO, 1)), 2)
        self.assertIn('after=next', calls[1])
        fresh = helpers()
        fresh['run_started'] = 0
        fresh['RUN_BUDGET_SECONDS'] = 0
        with patch('time.sleep') as sleep, patch('subprocess.run') as run:
            with self.assertRaises(RuntimeError):
                fresh['fetch_json']('expired', per_item=True)
            sleep.assert_not_called()
            run.assert_not_called()

    def test_graphql_errors_and_request_count(self):
        reply = subprocess.CompletedProcess([], 0, 'HTTP/2.0 200 OK\n\n{"errors":[{"message":"rate limit"}]}', '')
        with patch('subprocess.run', return_value=reply):
            with self.assertRaises(ValueError):
                self.m['fetch_json']('graphql')
        self.assertEqual(self.m['request_stats'], {'requests': 1, 'failures': 1, 'timeouts': 0})
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('gh', 120)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.m['fetch_json']('timeout')
        self.assertEqual(self.m['request_stats']['timeouts'], 1)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class WorkflowTests(unittest.TestCase):
    def run_workflow(self, items, existing=None, failed=None, cap=500):
        paths = []
        def run(args, **kwargs):
            path = args[2]
            paths.append(path)
            if failed and failed in path:
                return subprocess.CompletedProcess(args, 1, '', 'simulated failure')
            if f'repos/{REPO}/issues?state=all' in path:
                data = items
            elif '/comments?' in path:
                data = [comment()]
            else:
                data = []
            return subprocess.CompletedProcess(args, 0, 'HTTP/2.0 200 OK\n\n' + json.dumps(data), '')
        with tempfile.TemporaryDirectory() as temp:
            previous = os.getcwd()
            try:
                os.chdir(temp)
                if existing:
                    Path(FOLDER).mkdir()
                    Path(FOLDER, 'github-issues.json').write_text(json.dumps(existing))
                with patch('datetime.datetime', FrozenDateTime), patch('subprocess.run', side_effect=run), patch('time.sleep'), contextlib.redirect_stdout(io.StringIO()):
                    exec(compile(SOURCE.replace('ISSUE_CAP = 500', f'ISSUE_CAP = {cap}'), str(WORKFLOW), 'exec'), {})
                result = json.loads(Path(FOLDER, 'github-issues.json').read_text())
                return result, paths
            finally:
                os.chdir(previous)

    def test_v2_invalidation_cache_and_byte_idempotence(self):
        legacy = item(first_response_at=stamp(-90), first_response_hours=6)
        result, paths = self.run_workflow([api_item()], {'issues': [legacy]})
        response = result['issues'][0]['response']
        self.assertEqual(response['hours'], 1)
        rerun, paths = self.run_workflow([api_item()], result)
        self.assertEqual(json.dumps(result), json.dumps(rerun))
        self.assertFalse(any('/comments?' in p or '/timeline?' in p for p in paths))
        reopened, paths = self.run_workflow([api_item(state='open', closed_at=None)], result)
        self.assertTrue(any('/comments?' in p for p in paths))
        changed, paths = self.run_workflow([api_item(updated_at=stamp(-1))], result)
        self.assertTrue(any('/comments?' in p for p in paths))
        result['issues'][0]['response'] = None
        unanswered, paths = self.run_workflow([api_item()], result)
        self.assertTrue(any('/comments?' in p for p in paths))

    def test_cap_partial_coverage_and_independent_response_failure(self):
        capped, _ = self.run_workflow([api_item(), api_item(2)], cap=1)
        self.assertEqual(capped['summary']['coverage'], 'incomplete')
        self.assertIsNone(capped['summary']['community_issues_created_365d'])
        self.assertIsNone(capped['summary']['community_issues_response_eligible_90d'])
        self.assertIsNone(capped['history'][0]['community_prs_merged_90d'])
        partial, _ = self.run_workflow([api_item(), {}])
        self.assertEqual(partial['summary']['response_coverage'], 'incomplete')
        good, _ = self.run_workflow([api_item()])
        bad, _ = self.run_workflow([api_item(updated_at=stamp(-1))], good, failed='/comments?')
        self.assertEqual(bad['summary']['coverage'], 'complete')
        self.assertEqual(bad['summary']['community_issues_created_365d'], 1)
        self.assertEqual(bad['summary']['response_coverage'], 'incomplete')
        self.assertIsNone(bad['summary']['community_issues_response_eligible_90d'])
        self.assertEqual(bad['summary']['response_collected_at'], good['summary']['response_collected_at'])

    def test_failed_open_scan_preserves_successful_same_day_history(self):
        good, _ = self.run_workflow([api_item()])
        bad, _ = self.run_workflow([api_item()], good, failed='state=open')
        self.assertEqual(bad['open_scan'], good['open_scan'])
        self.assertEqual(bad['history'][0]['open_scan'], good['history'][0]['open_scan'])


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.m = build_module()

    def test_packagist_last_full_month_needs_every_calendar_day(self):
        def month(label, first_day, last_day, downloads=1):
            return {'month': label, 'downloads': downloads, 'first_day': first_day, 'last_day': last_day}
        last_full = self.m['last_full_month']
        july = month('2026-07', '2026-07-01', '2026-07-31', 3107)
        august = month('2026-08', '2026-08-01', '2026-08-31', 2634)
        # 1 October: Packagist has reported September only through the 29th.
        self.assertEqual(last_full([july, august, month('2026-09', '2026-09-01', '2026-09-29')], date(2026, 10, 1)), august)
        # 2 October: the 30th is in and no October day exists yet.
        september = month('2026-09', '2026-09-01', '2026-09-30', 2984)
        self.assertEqual(last_full([july, august, september], date(2026, 10, 2)), september)
        # 3 October: the partial current month does not change the pick.
        october = month('2026-10', '2026-10-01', '2026-10-01')
        self.assertEqual(last_full([july, august, september, october], date(2026, 10, 3)), september)
        # Inside September a complete-looking September is still the current month.
        self.assertEqual(last_full([july, august, september], date(2026, 9, 30)), august)
        # The first month of a series that starts mid-month is not full.
        self.assertIsNone(last_full([month('2021-12', '2021-12-14', '2021-12-31')], date(2026, 10, 2)))
        # The pick does not depend on the order of the entries.
        self.assertEqual(last_full([september, july, august], date(2026, 10, 2)), september)
        # Entries without day bounds or with an unparseable label never qualify, even when they sort before today.
        self.assertIsNone(last_full([{'month': '2026-08', 'downloads': 1},
                                     {'month': '2025-13', 'first_day': 'x', 'last_day': 'y'},
                                     {'month': '2025-xy', 'first_day': '2025-xy-01', 'last_day': '2025-xy-31'}],
                                    date(2026, 10, 2)))
        self.assertIsNone(last_full([], date(2026, 10, 2)))

    def test_v2_zero_unknown_and_incomplete(self):
        old = '\n'.join(self.m['response_rows']({'definition_version': 2}))
        self.assertIn('n/a', old)
        self.assertNotIn('no eligible items', old)
        summary = {'definition_version': 3, 'coverage': 'complete', 'response_coverage': 'complete'}
        for kind in ('issues', 'prs'):
            summary.update({f'community_{kind}_{key}': value for key, value in {
                'response_eligible_90d': 0, 'answered_within_48h_90d': 0,
                'unknown_attribution_90d': 1, 'unknown_clock_90d': 1}.items()})
        text = '\n'.join(self.m['response_rows'](summary))
        self.assertIn('no eligible items', text)
        self.assertIn('1 unknown attribution', text)
        self.assertIn('1 unknown clock', text)
        summary['response_coverage'] = 'incomplete'
        text = '\n'.join(self.m['response_rows'](summary))
        self.assertNotIn('no eligible items', text)

    def test_protocol_gate_utc_dates_and_escaping(self):
        hostile = '<script>alert("x")</script>'
        entry = {'name': hostile, 'verified': None, 'mainnet_activation_date': '2026-09-16',
                 'activation_url': 'https://example.org/activation', 'caps': [{'name': 'CAP-85', 'url': 'https://example.org/cap'}],
                 'releases': {'ios': {'tag': hostile, 'published_at': '2026-09-16T01:00:00+02:00', 'url': 'https://example.org/release"x'}}}
        self.m['load_json'] = lambda p: {'upgrades': [entry]}
        self.assertIn('awaiting maintainer', self.m['build_protocol_delivery']())
        entry['verified'] = '2026-09-21'
        text = self.m['build_protocol_delivery']()
        self.assertIn('shipped 1 day before activation', text)
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)
        entry['releases']['ios']['published_at'] = '2026-09-16T00:00:00Z'
        self.assertIn('shipped on activation day', self.m['build_protocol_delivery']())
        entry['releases']['ios']['published_at'] = '2026-09-18T00:00:00Z'
        self.assertIn('shipped 2 days after', self.m['build_protocol_delivery']())
        for verified in (True, 'bad', {}, []):
            entry['verified'] = verified
            self.assertIn('awaiting maintainer', self.m['build_protocol_delivery']())

    def test_protocol_without_sdk_change_renders_reason_and_no_lag(self):
        entry = {'name': 'Protocol 29', 'verified': '2026-10-02', 'mainnet_activation_date': '2026-10-01',
                 'activation_url': 'https://example.org/activation', 'caps': [], 'releases': {},
                 'sdk_change_required': False, 'reason': 'Security release <no> XDR change',
                 'evidence_url': 'https://example.org/notes'}
        self.m['load_json'] = lambda p: {'upgrades': [entry]}
        text = self.m['build_protocol_delivery']()
        self.assertIn('no SDK change required', text)
        self.assertIn('href="https://example.org/notes"', text)
        self.assertIn('Security release &lt;no&gt; XDR change', text)
        self.assertNotIn('shipped', text)
        self.assertNotIn('n/a', text)
        # The gate: a no-change upgrade needs its reason and release-notes evidence.
        for missing in ('reason', 'evidence_url'):
            broken = dict(entry)
            del broken[missing]
            self.m['load_json'] = lambda p, b=broken: {'upgrades': [b]}
            self.assertIn('awaiting maintainer', self.m['build_protocol_delivery']())
        # Without the flag, an entry with no CAPs stays unpublished as before.
        self.m['load_json'] = lambda p: {'upgrades': [{**entry, 'sdk_change_required': True}]}
        self.assertIn('awaiting maintainer', self.m['build_protocol_delivery']())

    def test_all_sdk_subsets_and_missing_corrupt_hostile_inputs(self):
        original_loader = self.m['load_json']
        payload = '</script><img src=x onerror=alert(1)>'
        fixture = {'summary': {'definition_version': 3, 'collected_at': stamp(), 'response_coverage': 'complete'},
                   'issues': [{'number': payload, 'type': payload, 'created_at': stamp(-72),
                               'response_status': [], 'url': 'javascript:alert(1)',
                               'response': {'definition_version': 3, 'actor': payload, 'kind': payload,
                                            'hours': 1, 'url': 'https://example.org/"onmouseover="x'}}]}
        compatibility_fixture = json.loads((ROOT / 'tests/fixtures/compatibility.json').read_text())['collected']
        compatibility_fixture['seps'][0]['title'] = payload + '\u2014\U0001F600'
        compatibility_fixture['seps'][0]['upstream']['status'] = payload
        compatibility_fixture['seps'][0]['url'] = 'javascript:alert(1)'
        with tempfile.TemporaryDirectory() as temp:
            self.m['OUT'] = Path(temp) / 'index.html'
            pages = []
            for count in range(5):
                for keys in itertools.combinations([s['key'] for s in self.m['SDKS']], count):
                    self.m['ENABLED_SDKS'] = set(keys)
                    self.m['ACTIVE_SDKS'] = [s for s in self.m['SDKS'] if s['key'] in keys]
                    for malformed in ('real', None, [], 1, 'bad', {}, fixture, compatibility_fixture):
                        def loader(path):
                            if malformed == 'real':
                                return original_loader(path)
                            if malformed is compatibility_fixture:
                                return compatibility_fixture if path.name == 'compatibility.json' else original_loader(path)
                            if malformed is fixture:
                                return fixture if path.name == 'github-issues.json' else original_loader(path)
                            return malformed
                        self.m['load_json'] = loader
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.m['generate']()
                        text = self.m['OUT'].read_text()
                        self.assertNotIn('<img src=x', text)
                        self.assertNotIn('href="javascript:', text)
                        self.assertNotIn('\u2014', text)
                        self.assertFalse(any(0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF for c in text))
                        self.assertLess(text.index('id="definitions"'), text.index('<script>'))
                        pages.append({'script': re.search(r'<script>([\s\S]*?)</script>', text).group(1),
                                      'ids': re.findall(r'id="([^"]+)"', text)})
            if shutil.which('node'):
                script = r"""
const vm = require('vm');
const fs = require('fs');
for (const page of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  for (const hash of ['', '#definitions']) {
    const elements = new Map(page.ids.map(id => [id, {}])), listeners = [], options = [];
    const context = {
      document: {getElementById: id => elements.get(id) || null},
      window: {addEventListener(name, listener) {listeners.push([name, listener]);}},
      location: {hash},
      echarts: {init() {return {setOption(option) {options.push(option);}, resize() {}};}}
    };
    vm.runInNewContext(page.script, context, {timeout: 1000});
    const definitions = elements.get('definitions');
    if (!definitions) throw new Error('definitions element missing');
    if (Boolean(definitions.open) !== (hash === '#definitions')) {
      throw new Error(`definitions open=${definitions.open} after load with hash "${hash}"`);
    }
    const fire = name => listeners.filter(([event]) => event === name).forEach(([, listener]) => listener());
    definitions.open = false;
    fire('beforeprint');
    if (definitions.open !== true) throw new Error('definitions stay closed before print');
    definitions.open = false;
    context.location.hash = '#definitions';
    fire('hashchange');
    if (definitions.open !== true) throw new Error('definitions stay closed after hashchange');
  }
}
"""
                subprocess.run(['node', '-e', script], input=json.dumps(pages), text=True, check=True,
                               capture_output=True)
            corrupt = Path(temp) / 'corrupt.json'
            corrupt.write_text('{broken')
            self.assertIsNone(original_loader(corrupt))
            self.assertIsNone(original_loader(Path(temp) / 'missing.json'))

    def compatibility_card(self, raw, sdks=None):
        self.m['load_json'] = lambda path: raw if not isinstance(raw, list) else raw[next(i for i, sdk in enumerate(sdks) if sdk['folder'] == path.parent.name)]
        sdks = sdks or self.m['SDKS'][:1]
        with contextlib.redirect_stdout(io.StringIO()):
            data = [{'sdk': sdk, 'compatibility': self.m['extract_compatibility'](sdk)} for sdk in sdks]
        signals = {sdk['key']: self.m['Signals']() for sdk in sdks}
        with contextlib.redirect_stdout(io.StringIO()):
            page = self.m['build_compatibility_card'](data, signals)
        cells = {key: html.unescape(re.sub('<[^>]+>', '', text)) for key, text in
                 re.findall(r'<td data-kind="([^"]+)">(.*?)</td>', page)}
        cells.update({key: html.unescape(text) for key, text in re.findall(r'<span data-kind="([^"]+)">(.*?)</span>', page)})
        return page, cells, data, signals

    def release_cells(self, page, kind, key='ios'):
        row = re.search(r'<tr data-release="' + key + '-' + kind + r'">(.*?)</tr>', page)[1]
        return [html.unescape(re.sub('<[^>]+>', '', cell)) for cell in re.findall(r'<t[dh]>(.*?)</t[dh]>', row)]

    def test_compatibility_cells_and_invalid_entries(self):
        raw = json.loads((ROOT / 'tests/fixtures/compatibility.json').read_text())['collected']
        for states, phrase in [(['unknown'], 'SEP-{0} not compared'), (['no_version_field'], 'SEP-{0} has no upstream version'),
                               (['current'], '1 of 1 at current version'),
                               (['current', 'updated_date_not_later'],
                                '1 of 1 at current version; 1 without upstream update recorded since generation'),
                               (['updated_date_not_later', 'updated_date_later'],
                                'matrices record no version; 1 without upstream update recorded since generation; '
                                '1 with upstream update recorded since generation'),
                               (['no_version_field', 'no_version_field'], 'SEP-{0}, SEP-{1} have no upstream version')]:
            fixture = copy.deepcopy(raw)
            fixture['seps'] = fixture['seps'][:len(states)]
            for entry, state in zip(fixture['seps'], states):
                entry.update(state=state, layout='php', sep_version='1.0.0' if state == 'current' else None)
                entry['upstream'].update(version='1.0.0' if state != 'no_version_field' else None,
                                         updated=None if state == 'current' else '2099-01-01' if state == 'updated_date_later' else '2026-09-01')
            _, cells, data, _ = self.compatibility_card(fixture)
            self.assertEqual(cells['sep-versions'], phrase.format(*[entry['number'] for entry in fixture['seps']]))
            self.assertEqual(data[0]['compatibility']['summary']['sep_matrices'], len(states))
        for malformed in (None, {}, {'schema_version': 2}, {'schema_version': True}):
            page, cells, _, signals = self.compatibility_card(malformed)
            self.assertTrue(all('n/a' in text and '0 of 0' not in text for text in cells.values()))
            self.assertTrue(all(s['value'] is None and s['reason'] for s in signals[self.m['SDKS'][0]['key']].values()))
            self.assertTrue(all(s['value'] is None and s['reason'] == 'Missing or malformed compatibility data.'
                                for key, s in signals[self.m['SDKS'][0]['key']].items()
                                if key.startswith('compatibility.sep.')))
        for bad in (True, -1, 3.5, '50', [], {}):
            fixture = copy.deepcopy(raw)
            fixture['horizon']['total'] = bad
            fixture['seps'][0]['implemented'] = bad
            page, cells, data, _ = self.compatibility_card(fixture)
            self.assertIn('n/a', cells['horizon'])
            self.assertIn('<td class="nowrap">not compared: Matrix entry missing or malformed</td>', page)
            self.assertIsNone(data[0]['compatibility']['summary']['sep_at_full'])
        for bad in (True, 50.0):
            fixture = copy.deepcopy(raw)
            fixture['horizon'].update(full=bad, total=bad)
            _, _, data, _ = self.compatibility_card(fixture)
            self.assertEqual((data[0]['compatibility']['horizon']['coverage'], data[0]['compatibility']['horizon']['total']),
                             ('incomplete', None))
        for state in ('list_incomplete', 'cited_not_found', 'unknown'):
            fixture = copy.deepcopy(raw)
            fixture['horizon'].update(state=state, newer_stable_releases=None, reason='test reason')
            if state == 'list_incomplete':
                fixture['upstream']['horizon']['list_complete'] = False
            if state == 'unknown':
                fixture['horizon'].update(coverage='incomplete', full=None, total=None)
            _, cells, _, signals = self.compatibility_card(fixture)
            self.assertIn('newest stable ' + ('n/a: upstream release list incomplete' if state == 'list_incomplete' else 'v28.0.1'), cells['horizon'])
            self.assertIsNone(signals[self.m['SDKS'][0]['key']]['compatibility.horizon.newer_stable_releases']['value'])
            if state == 'cited_not_found':
                self.assertIn('cited release not found, count n/a', cells['horizon'])

    def test_compatibility_rejects_inconsistent_comparisons(self):
        raw = json.loads((ROOT / 'tests/fixtures/compatibility.json').read_text())['collected']
        for field, bad in [('upstream', []), ('sep_version', []), ('sep_version', 'wrong'),
                           ('state', {}), ('layout', 'ios')]:
            fixture = copy.deepcopy(raw)
            if field == 'layout':
                fixture['seps'][0].update(state='updated_date_not_later', sep_version=None)
            fixture['seps'][0][field] = bad
            _, _, data, _ = self.compatibility_card(fixture)
            entry = data[0]['compatibility']['seps'][0]
            self.assertEqual((entry['state'], entry['coverage'], bool(entry['reason'])), ('unknown', 'complete', True))
        for changes in [{'version': 'v21.3.0', 'state': 'cites_prerelease', 'cited_is_prerelease': True},
                        {'version': 'v27.0.0-rc.1', 'state': 'newer_available', 'cited_is_prerelease': False,
                         'newer_stable_releases': 1, 'newer': [{'tag': raw['upstream']['horizon']['current'],
                         'published_at': raw['upstream']['horizon']['published_at'], 'url': raw['upstream']['horizon']['url']}]},
                        {'version': 'v27.0.0'}, {'state': 'newer_available'}, {'state': 'cites_prerelease'},
                        {'state': 'newer_available', 'newer_stable_releases': 1,
                         'newer': [{'tag': 'v20.0.0', 'published_at': raw['source']['published_at'], 'url': 'https://example.org/release'}]}]:
            fixture = copy.deepcopy(raw)
            fixture['horizon'].update(changes)
            _, _, data, _ = self.compatibility_card(fixture)
            entry = data[0]['compatibility']['horizon']
            self.assertEqual((entry['state'], entry['newer_stable_releases'], entry['coverage'], bool(entry['reason'])),
                             ('unknown', None, 'complete', True))

        fixture = copy.deepcopy(raw)
        fixture['upstream']['horizon']['url'] = 'javascript:alert(1)'
        page, _, data, _ = self.compatibility_card(fixture)
        self.assertEqual(data[0]['compatibility']['horizon']['state'], 'unknown')
        self.assertNotIn('javascript:', page)

    def test_compatibility_guards_and_summary(self):
        raw = json.loads((ROOT / 'tests/fixtures/compatibility.json').read_text())['collected']
        raw['summary']['sep_matrices'] = 999
        raw['guards'] = [{'kind': 'sdk_version_mismatch', 'section': section, 'sep': 10 if section == 'sep' else None,
                          'file': section + '/matrix.md', 'text': section + ' header says old'} for section in ('horizon', 'rpc', 'sep')]
        raw['guards'].append({'kind': 'total_changed', 'section': 'rpc', 'sep': None, 'file': 'rpc/matrix.md', 'text': 'total changed'})
        _, _, data, signals = self.compatibility_card(raw)
        self.assertEqual(data[0]['compatibility']['summary']['sep_matrices'], len(raw['seps']))
        for key, signal in signals[self.m['SDKS'][0]['key']].items():
            self.assertEqual((key.split('.')[1] + ' header says old' in signal['reason'], 'total changed' in signal['reason']),
                             (True, key == 'compatibility.rpc.total'))
        second = copy.deepcopy(raw)
        second['horizon'].update(full=49, total=49)
        for changed, count, expected in [(False, 2, True), (True, 2, False), (False, 1, False)]:
            second['horizon']['version'] = 'v27.0.0' if changed else raw['horizon']['version']
            page, _, _, signals = self.compatibility_card([raw, second][:count], self.m['SDKS'][:count])
            self.assertEqual('totals differ across SDKs' in page, expected)
            if expected:
                self.assertIn('HORIZON totals differ across SDKs for v28.0.1: iOS 50, Flutter 49', page)
            self.assertEqual('totals differ across SDKs' in (signals[self.m['SDKS'][0]['key']]['compatibility.horizon.total']['reason'] or ''), expected)
        second['horizon'].update(version=raw['horizon']['version'], coverage='incomplete')
        page, _, _, _ = self.compatibility_card([raw, second], self.m['SDKS'][:2])
        self.assertNotIn('totals differ across SDKs', page)

    def test_compatibility_upstream_reasons_and_release_links(self):
        raw = json.loads((ROOT / 'tests/fixtures/compatibility.json').read_text())['collected']
        _, cells, _, _ = self.compatibility_card(None)
        self.assertEqual(cells['horizon'],
                         'n/a, n/a: Matrix entry missing or malformed, '
                         'newest stable n/a: upstream release data missing (matrix)')
        cases = [
            ({'list_complete': False}, 'upstream release list incomplete'),
            ({'list_complete': True, 'current': None}, 'no stable release in list'),
            (dict(raw['upstream']['horizon'], url='javascript:alert(1)'), 'upstream release data invalid'),
            (dict(raw['upstream']['horizon'], current='v28.0.1-rc.1'), 'upstream release data invalid'),
            ({}, 'upstream release data missing'),
        ]
        for upstream, reason in cases:
            fixture = copy.deepcopy(raw)
            fixture['horizon'].update(coverage='incomplete', reason='broken matrix.')
            fixture['upstream']['horizon'] = upstream
            page, cells, _, _ = self.compatibility_card(fixture)
            self.assertIn('n/a: broken matrix, newest stable n/a: ' + reason, cells['horizon'])
            self.assertEqual(self.release_cells(page, 'horizon')[3:], ['n/a: ' + reason, 'n/a: broken matrix'])
        fixture = copy.deepcopy(raw)
        fixture['horizon'].update(coverage='incomplete', reason='broken matrix.')
        page, cells, _, _ = self.compatibility_card(fixture)
        self.assertIn('n/a: broken matrix, newest stable v28.0.1', cells['horizon'])
        published = raw['upstream']['horizon']['published_at'][:10]
        self.assertEqual(self.release_cells(page, 'horizon')[2:], ['v28.0.1', f'v28.0.1 ({published})', 'n/a: broken matrix'])
        for count in (1, 2):
            fixture = copy.deepcopy(raw)
            up = fixture['upstream']['horizon']
            newer = [{'tag': up['current'], 'published_at': up['published_at'], 'url': up['url']}]
            if count == 2:
                newer.append({'tag': 'v28.0.0', 'published_at': up['published_at'],
                              'url': 'https://github.com/stellar/stellar-horizon/releases/tag/v28.0.0'})
            fixture['horizon'].update(version='v27.0.0', state='newer_available',
                                      newer_stable_releases=count, newer=newer)
            page, cells, _, _ = self.compatibility_card(fixture)
            phrase = f"{count} newer stable release{'s' if count != 1 else ''}"
            self.assertIn(phrase + ', newest v28.0.1', cells['horizon'])
            tags = ', '.join(f"{r['tag']} ({r['published_at'][:10]})" for r in newer)
            self.assertEqual(self.release_cells(page, 'horizon')[2:],
                             ['v27.0.0', f"{up['current']} ({up['published_at'][:10]})", f'{count}: {tags}'])
        fixture['horizon']['version'] = 'not-a-release'
        page, cells, _, signals = self.compatibility_card(fixture)
        self.assertEqual(cells['horizon'].split(', ', 1)[0], 'n/a')
        self.assertNotIn('/releases/tag/not-a-release', page)
        self.assertNotIn('https://github.com/stellar/stellar-horizon/releases/tag/not-a-release',
                         signals['ios']['compatibility.horizon.full']['evidence_urls'])

    def test_compatibility_sep_evidence_by_state(self):
        raw = json.loads((ROOT / 'tests/fixtures/compatibility.json').read_text())['collected']
        raw['seps'] = raw['seps'][:6]
        states = ('current', 'version_differs', 'updated_date_not_later',
                  'updated_date_later', 'no_version_field', 'unknown')
        for entry, state in zip(raw['seps'], states):
            entry.update(state=state, layout='php', reason=None,
                         sep_version='1.0.0' if state in ('current', 'version_differs') else None)
            entry['upstream'].update(version=None if state == 'no_version_field' else
                                    '2.0.0' if state == 'version_differs' else '1.0.0',
                                    updated='2099-01-01' if state == 'updated_date_later' else '2020-01-01')
        page, _, _, signals = self.compatibility_card(raw)
        for state, entry in zip(states, raw['seps']):
            urls = signals['ios']['compatibility.sep.' + state]['evidence_urls']
            self.assertEqual(set(urls), {entry['url'], entry['upstream']['url']})
            self.assertTrue(all('href="' + html.escape(url, quote=True) + '"' in page for url in urls))
        tree = 'https://github.com/Soneso/stellar-ios-mac-sdk/tree/' + raw['source']['commit'] + '/compatibility/sep'
        for field in ('matrices', 'at_full'):
            self.assertEqual(signals['ios']['compatibility.sep.' + field]['evidence_urls'], [tree])

    def test_malformed_nested_association_never_aborts_the_build(self):
        original_loader = self.m['load_json']
        data = json.loads((ROOT / FOLDER / 'github-issues.json').read_text())
        self.m['load_json'] = lambda path: data if path.name == 'github-issues.json' else original_loader(path)
        for bad in ([], {}):
            data['issues'][0]['author_association'] = bad
            issues = self.m['extract_issues']({'folder': FOLDER})
            self.assertEqual(issues['response_cohort'], [])
            self.assertFalse(issues['response_evidence_available'])
        with tempfile.TemporaryDirectory() as temp:
            self.m['OUT'] = Path(temp) / 'index.html'
            with contextlib.redirect_stdout(io.StringIO()):
                self.m['generate']()
            text = self.m['OUT'].read_text()
        self.assertIn('Response evidence unavailable.', text)


if __name__ == '__main__':
    unittest.main()
