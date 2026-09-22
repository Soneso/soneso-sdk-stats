"""Offline regression checks for response collection and protocol delivery.

Run with: python3 -m unittest discover -s tests -v
The workflow's actual inline Python is exercised with mocked HTTP responses.
"""
import contextlib
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
from datetime import datetime, timedelta, timezone
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

    def test_all_sdk_subsets_and_missing_corrupt_hostile_inputs(self):
        original_loader = self.m['load_json']
        payload = '</script><img src=x onerror=alert(1)>'
        fixture = {'summary': {'definition_version': 3, 'collected_at': stamp(), 'response_coverage': 'complete'},
                   'issues': [{'number': payload, 'type': payload, 'created_at': stamp(-72),
                               'response_status': [], 'url': 'javascript:alert(1)',
                               'response': {'definition_version': 3, 'actor': payload, 'kind': payload,
                                            'hours': 1, 'url': 'https://example.org/"onmouseover="x'}}]}
        with tempfile.TemporaryDirectory() as temp:
            self.m['OUT'] = Path(temp) / 'index.html'
            pages = []
            for count in range(5):
                for keys in itertools.combinations([s['key'] for s in self.m['SDKS']], count):
                    self.m['ENABLED_SDKS'] = set(keys)
                    self.m['ACTIVE_SDKS'] = [s for s in self.m['SDKS'] if s['key'] in keys]
                    for malformed in ('real', None, [], 1, 'bad', {}, fixture):
                        def loader(path):
                            if malformed == 'real':
                                return original_loader(path)
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
                        pages.append({'script': re.search(r'<script>([\s\S]*?)</script>', text).group(1),
                                      'ids': re.findall(r'id="([^"]+)"', text)})
            if shutil.which('node'):
                script = r"""
const vm = require('vm');
const fs = require('fs');
for (const page of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  const ids = new Set(page.ids), options = [];
  vm.runInNewContext(page.script, {
    document: {getElementById: id => ids.has(id) ? {} : null},
    window: {addEventListener() {}},
    echarts: {init() {return {setOption(option) {options.push(option);}, resize() {}};}}
  }, {timeout: 1000});
}
"""
                subprocess.run(['node', '-e', script], input=json.dumps(pages), text=True, check=True,
                               capture_output=True)
            corrupt = Path(temp) / 'corrupt.json'
            corrupt.write_text('{broken')
            self.assertIsNone(original_loader(corrupt))
            self.assertIsNone(original_loader(Path(temp) / 'missing.json'))

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
