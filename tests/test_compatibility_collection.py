"""Offline tests of the inline compatibility collector.

Fixture matrices keep the header and the Overall Coverage block of each tagged
file; Horizon, RPC, Flutter SEP-0048, PHP SEP-0051, and the historic files are
complete copies; preambles are the fence excerpt.
"""
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/collect-compatibility.yml'
SOURCE = textwrap.dedent(WORKFLOW.read_text().split("python3 <<'PYTHON'\n", 1)[1].split('\n          PYTHON', 1)[0])
FIXTURES = json.loads((ROOT / 'tests/fixtures/compatibility.json').read_text())
NOW = datetime(2026, 9, 30, 10, 40, tzinfo=timezone.utc)

def matrix_path(folder, section, number=10):
    if section == 'sep':
        return f'compatibility/sep/SEP-{number:04d}_COMPATIBILITY_MATRIX.md'
    name = ('COMPATIBILITY_MATRIX.md' if section == 'horizon' and folder == 'stellar-php-sdk'
            else section.upper() + '_COMPATIBILITY_MATRIX.md')
    return f'compatibility/{section}/{name}'


def helpers():
    namespace = {}
    exec(compile(SOURCE.split('for sdk in SDKS:', 1)[0], str(WORKFLOW), 'exec'), namespace)
    namespace.update(now=NOW, stamp='2026-09-30T10:40:00Z')
    return namespace

def release(tag='v28.0.1', **values):
    return dict(tag_name=tag, draft=False, prerelease=False, published_at='2026-08-27T18:40:46Z',
                html_url='https://github.com/stellar/stellar-rpc/releases/tag/' + tag, **values)

class CollectionTests(unittest.TestCase):

    def setUp(self):
        self.m = helpers()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        cwd = os.getcwd()
        os.chdir(self.temp)
        self.addCleanup(os.chdir, cwd)
        self.calls = []
        self.responses = {}
        self.m['fetch'] = self.fetch

    def fetch(self, url):
        self.calls.append(url)
        for fragment, response in self.responses.items():
            if fragment in url:
                if isinstance(response, Exception):
                    raise response
                return response
        if '/releases?' in url:
            return (200, json.dumps([release()]), {})
        if url.endswith('stellar-protocol/commits/master'):
            return (200, json.dumps({'sha': FIXTURES['protocol_commit'], 'commit': {'committer': {'date': '2026-09-22T12:00:00Z'}}}), {})
        if '/ecosystem/sep-' in url:
            n = str(int(url.rsplit('sep-', 1)[1].split('.')[0]))
            return (200, FIXTURES['preambles'][n], {})
        for folder, fixture in FIXTURES['sdks'].items():
            if '/' + folder + '/' not in url:
                continue
            if url.endswith('/releases/latest'):
                return (200, json.dumps(release(fixture['tag'])), {})
            if '/commits/' in url:
                return (200, json.dumps({'sha': fixture['commit']}), {})
            if '/contents/' in url:
                files = [{'type': 'file', 'name': Path(p).name} for p in fixture['files'] if '/sep/' in p]
                return (200, json.dumps(files + [{'type': 'file', 'name': 'README.md'}]), {})
            path = 'compatibility/' + url.split('/compatibility/', 1)[1]
            return (200, fixture['files'][path], {})
        raise AssertionError(url)

    def collect(self, folder='stellar-ios-mac-sdk'):
        with contextlib.redirect_stdout(io.StringIO()):
            self.m['collect'](folder)
        return json.loads(Path(folder, 'compatibility.json').read_text())

    def entry(self, folder='stellar-ios-mac-sdk', section='sep', number=10, text=None):
        fixture = FIXTURES['sdks'][folder]
        path = matrix_path(folder, section, number)
        return self.m['parse_matrix'](
            text if text is not None else fixture['files'][path], section, path,
            'https://example.org/matrix', fixture['tag'].removeprefix('v'),
            'php' if folder == 'stellar-php-sdk' else 'ios')

    def test_real_headers_and_states(self):
        expected = {
            'stellar-ios-mac-sdk': (19, 18, 0, 0, 1),
            'stellar_flutter_sdk': (20, 18, 1, 0, 1),
            'stellar-php-sdk': (22, 0, 0, 20, 2),
            'kmp-stellar-sdk': (18, 17, 0, 0, 1),
        }
        for folder, wanted in expected.items():
            with self.subTest(folder=folder):
                data = self.collect(folder)
                s = data['summary']
                self.assertEqual((s['sep_matrices'], s['sep_current'], len(s['sep_version_differs']),
                                  s['sep_updated_date_not_later'], len(s['sep_no_version_field'])), wanted)
                self.assertEqual((s['sep_at_full'], s['complete'], s['sep_unknown']), (wanted[0], True, []))
                for kind, total in [('horizon', 50), ('rpc', 12)]:
                    e = data[kind]
                    self.assertEqual((e['full'], e['total'], e['state'], e['newer_stable_releases'],
                                      e['sdk_version_matches_tag']), (total, total, 'current', 0, True))
                self.assertTrue(all(('/' + data['source']['commit'] + '/' in e['url'] for e in [data['horizon'], data['rpc']] + data['seps'])))
        self.assertEqual(sum(('/ecosystem/sep-' in u for u in self.calls)), 22)
        self.assertTrue(all(('?ref=' + FIXTURES['sdks'][f]['commit'] in u for f in expected for u in self.calls if '/' + f + '/contents/' in u)))

    def test_streaming_coverage_boundary(self):
        for folder in FIXTURES['sdks']:
            entry = self.entry(folder, section='horizon')
            self.assertEqual((entry['coverage'], entry['full'], entry['total'], entry['percent_printed']), ('complete', 50, 50, 100.0))

    def test_header_and_block_boundaries(self):
        path = matrix_path('stellar-ios-mac-sdk', 'sep')
        source = FIXTURES['sdks']['stellar-ios-mac-sdk']['files'][path]
        noise = ('## Implementation Status\n\n**SDK Version:** 9.9.9\n\n'
                 '**SEP Version:** 9.9.9\n\n- \u2705 **Implemented:** 1/1\n')
        text = source.replace('## Overall Coverage', noise + '\n## Overall Coverage') + '\n' + noise
        e = self.entry(text=text)
        self.assertEqual((e['coverage'], e['sdk_version'], e['sep_version'], e['implemented']),
                         ('complete', '3.12.0', '3.4.1', 24))

    def test_budget_exhaustion_retains_unfinished_sdks(self):
        for folder in FIXTURES['sdks']:
            Path(folder).mkdir()
            observed = '2026-09-24T10:40:00Z' if folder == 'stellar_flutter_sdk' else '2026-09-22T10:40:00Z'
            Path(folder, 'compatibility.json').write_text(json.dumps({'collected_at': observed}))
        before = {folder: Path(folder, 'compatibility.json').read_bytes() for folder in self.m['SDKS']}
        self.m['started'] = 0
        clock = [0]

        def budget_fetch(url):
            self.m['remaining']()
            result = self.fetch(url)
            if '/stellar_flutter_sdk/releases/latest' in url:
                clock[0] = self.m['BUDGET'] + 1
                self.m['remaining']()
            return result
        self.m['fetch'] = budget_fetch
        output = self.temp / 'budget-output'
        with (patch('time.monotonic', side_effect=lambda: clock[0]),
              patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}),
              contextlib.redirect_stdout(io.StringIO())):
            exec(SOURCE[SOURCE.index('for sdk in SDKS:'):], self.m)
        successful = json.loads(Path('stellar-ios-mac-sdk/compatibility.json').read_text())
        self.assertEqual(successful['collected_at'], '2026-09-30T10:40:00Z')
        self.assertEqual({f: Path(f, 'compatibility.json').read_bytes() for f in self.m['SDKS'][1:]}, {f: before[f] for f in self.m['SDKS'][1:]})
        self.assertEqual(output.read_text(), 'stale=stellar-php-sdk,kmp-stellar-sdk\n')

    def test_header_variants(self):
        e = self.entry('stellar-php-sdk', 'rpc', text=FIXTURES['historic']['php_mixed']['text'])
        self.assertEqual((e['version'], e['released'], e['sdk_version_matches_tag']), ('v25.0.1', '2026-04-03', False))
        self.assertEqual(self.entry(section='horizon', text=FIXTURES['historic']['ios_old']['text'])['coverage'], 'incomplete')
        path = 'compatibility/rpc/RPC_COMPATIBILITY_MATRIX.md'
        original = FIXTURES['sdks']['stellar_flutter_sdk']['files'][path]
        for replacement, version, released in [('v28.0.1 (released unknown)', 'v28.0.1', None), ('v29.0.0-rc.1', 'v29.0.0-rc.1', None)]:
            e = self.entry('stellar_flutter_sdk', 'rpc', text=original.replace('v28.0.1 (released 2026-08-27)', replacement))
            self.assertEqual((e['coverage'], e['version'], e['released']), ('complete', version, released))
        self.assertEqual(self.entry(number=6)['sep_status'], 'Active (Interactive components are deprecated in favor of SEP-24)')
        self.assertEqual(self.entry(number=10)['server_only_excluded'], 2)
        for folder in ('stellar-ios-mac-sdk', 'stellar_flutter_sdk', 'kmp-stellar-sdk'):
            self.assertIsNone(self.entry(folder, number=5)['sep_version'])

    def test_invalid_counts_identity_and_missing_blocks(self):
        source = FIXTURES['sdks']['stellar-ios-mac-sdk']['files']['compatibility/sep/SEP-0010_COMPATIBILITY_MATRIX.md']
        failures = [
            source.replace('24/24 fields', '23/24 fields'),
            source.replace('0/24', '1/24'),
            source.replace('0/24', '0/25'),
            source.replace('# SEP-0010', '# SEP-0011'),
            source.split('## Overall Coverage')[0],
        ]
        for text in failures:
            with self.subTest(text=text[:40]):
                e = self.entry(text=text)
                self.assertEqual((e['coverage'], e['implemented'], e['number'], e['state']), ('incomplete', None, 10, 'unknown'))
                self.assertTrue(e['reason'].startswith(e['file'] + ':'))
        php = FIXTURES['sdks']['stellar-php-sdk']['files']['compatibility/sep/SEP-0010_COMPATIBILITY_MATRIX.md']
        self.assertEqual(self.entry('stellar-php-sdk', text=php.split('## Overall Coverage')[0])['coverage'], 'incomplete')
        self.assertEqual(self.entry('stellar-php-sdk', text=php.split('## Overall Coverage')[0])['reason'],
                         matrix_path('stellar-php-sdk', 'sep') + ': missing Overall Coverage block')

    def test_preambles_and_seven_rules(self):
        base = self.entry()
        cases = [
            ('ios', '3.4.1', '3.4.1', '2024-03-20', 'current'),
            ('ios', '9.0', '3.4.1', None, 'version_differs'),
            ('ios', None, '3.4.1', '2024-03-20', 'unknown'),
            ('ios', None, None, '2024-03-20', 'no_version_field'),
            ('php', None, '3.4.1', None, 'unknown'),
            ('php', '3.4.1', '3.4.1', None, 'current'),
            ('php', 'v3.4.0', '3.4.1', None, 'version_differs'),
            ('php', None, '3.4.1', '2026-09-27', 'updated_date_not_later'),
            ('php', None, '3.4.1', '2026-09-28', 'updated_date_later'),
            ('php', None, None, None, 'no_version_field'),
            ('ios', '0.5.0', None, '2020-05-04', 'no_version_field'),
            ('php', '1.0.0', None, None, 'no_version_field'),
        ]
        for layout, mv, uv, updated, state in cases:
            for colon in (':', ''):
                with self.subTest(layout=layout, mv=mv, uv=uv, updated=updated, colon=colon):
                    preamble = 'Status: Active\n' + (f'Version{colon} {uv}\n' if uv else '') + (f'Updated: {updated}\n' if updated else '')
                    up = self.m['parse_preamble'](preamble)
                    e = {**base, 'layout': layout, 'sep_version': mv, 'upstream': up}
                    self.assertEqual(self.m['sep_state'](e)[0], state)
        for text in (None, '## Preamble\n```\nVersion: 3.4.1\n```'):
            e = {**base, 'upstream': self.m['parse_preamble'](text)}
            self.assertEqual(self.m['sep_state'](e)[0], 'unknown')
        php = FIXTURES['sdks']['stellar-php-sdk']['files']['compatibility/sep/SEP-0010_COMPATIBILITY_MATRIX.md']
        e = self.entry('stellar-php-sdk', text=php.replace('## Overall Coverage', '**SEP Version:** 3.4.1\n**SEP Status:** Active\n\n## Overall Coverage'))
        self.assertEqual((e['sep_version'], e['sep_status']), ('3.4.1', 'Active'))

    def test_release_pagination_flags_and_failure(self):
        original = self.fetch
        replies = [
            [release('v26.0.0'), release('rpcclient-v30.0.0'), release('v29.0.0-rc.1'),
             {**release('v21.3.0'), 'prerelease': True},
             {**release('v30.0.0'), 'draft': True, 'published_at': None}],
            [release('v28.0.1'), release('v26.0.0')],
        ]

        def pages(url):
            if '/releases?' not in url:
                return original(url)
            page = 1 if 'page=2' in url else 0
            headers = {'Link': '<https://api.github.com/repos/stellar/stellar-rpc/releases?per_page=100&page=2>; rel="next"'}
            return (200, json.dumps(replies[page]), headers if page == 0 else {})
        self.m['fetch'] = pages
        up, entries, stable = self.m['releases']('rpc')
        self.assertEqual((up['current'], sorted(r['tag_name'] for r in stable)),
                         ('v28.0.1', ['v26.0.0', 'v28.0.1']))
        draft = self.entry(section='rpc')
        draft['version'] = 'v30.0.0'
        self.m['release_state'](draft, up, entries, stable)
        self.assertEqual((draft['state'], draft['newer_stable_releases']), ('cited_not_found', None))
        e = self.entry(section='rpc')
        e['version'] = 'v26.0.0'
        self.m['release_state'](e, up, entries, stable)
        self.assertEqual((up['current'], len(entries), e['newer_stable_releases'], e['newer'][0]['tag'], e['state']),
                         ('v28.0.1', 6, 1, 'v28.0.1', 'newer_available'))
        for tag, flag in [('v29.0.0-rc.1', False), ('v21.3.0', True)]:
            entries[tag] = {**release(tag), 'prerelease': flag}
            e = self.entry(section='rpc')
            e['version'] = tag
            self.m['release_state'](e, up, entries, stable)
            self.assertEqual((e['state'], e['cited_is_prerelease']), ('cites_prerelease', True))
        e = self.entry(section='rpc')
        e['version'] = 'v20.0.0'
        self.m['release_state'](e, up, entries, stable)
        self.assertEqual((e['state'], e['newer_stable_releases']), ('cited_not_found', None))
        for data, state in [([{**release(), 'draft': True}], 'unknown'), ([], 'unknown')]:
            self.m.update(cache={}, shared={})
            self.m['fetch'] = lambda url: (200, json.dumps(data), {})
            args = self.m['releases']('rpc')
            e = self.entry(section='rpc')
            self.m['release_state'](e, *args)
            self.assertEqual((e['state'], e['newer_stable_releases']), (state, None))
        self.m.update(cache={}, shared={})
        self.m['fetch'] = lambda url: (200, '{}', {})
        with self.assertRaisesRegex(ValueError, 'invalid release list'):
            self.m['releases']('rpc')
        self.m.update(cache={}, shared={})
        self.m['fetch'] = lambda url: (200, '[{}]', {})
        with self.assertRaisesRegex(ValueError, 'invalid release entry'):
            self.m['releases']('rpc')

    def test_release_cap(self):
        calls = []

        def pages(url):
            calls.append(url)
            url = f'https://api.github.com/repos/stellar/stellar-rpc/releases?per_page=100&page={len(calls) + 1}'
            return (200, json.dumps([release()]), {'Link': f'<{url}>; rel="next"'})
        self.m['fetch'] = pages
        args = self.m['releases']('rpc')
        e = self.entry(section='rpc')
        self.m['release_state'](e, *args)
        self.assertEqual((len(calls), args[0]['current'], e['state'], e['newer_stable_releases']), (3, None, 'list_incomplete', None))

    def test_failures_retain_or_write(self):
        good = self.collect()
        path = Path('stellar-ios-mac-sdk/compatibility.json')
        before = path.read_bytes()
        failures = [
            ('/releases/latest', TimeoutError('timeout')),
            ('/contents/', (200, '[]', {})),
            ('/ecosystem/sep-0010.md', (500, '', {})),
            ('/ecosystem/sep-0010.md', TimeoutError('SEP timeout')),
            ('/commits/', TimeoutError('budget')),
        ]
        for fragment, response in failures:
            self.m.update(cache={}, shared={})
            self.responses = {fragment: response}
            with self.assertRaises(Exception):
                self.collect()
            self.assertEqual(path.read_bytes(), before)
        self.m.update(cache={}, shared={})
        self.responses = {'/ecosystem/sep-0010.md': (404, '', {})}
        data = self.collect()
        sep = next((e for e in data['seps'] if e['number'] == 10))
        self.assertEqual((sep['state'], sep['coverage'], sep['upstream']['found'], data['summary']['sep_at_full']), ('unknown', 'complete', False, 19))
        self.m.update(cache={}, shared={})
        self.responses = {'/horizon/': (404, '', {}), '/SEP-0010_': (200, 'bad matrix', {})}
        data = self.collect()
        self.assertEqual((data['horizon']['coverage'], data['horizon']['total'], data['summary']['sep_matrices'],
                          data['summary']['sep_at_full'], data['summary']['sep_unknown']),
                         ('incomplete', None, 19, None, [10]))
        self.assertEqual(data['rpc']['total'], 12)
        self.assertIsNone(data['history'][0]['sdk_version_matches_tag'])
        self.m.update(cache={}, shared={})
        self.responses = {'/SEP-0010_': (404, '', {})}
        self.assertIn('file not found at commit', next((e for e in self.collect()['seps'] if e['number'] == 10))['reason'])

    def test_upstream_failure_isolation(self):
        for folder in self.m['SDKS']:
            self.collect(folder)
        before = {f: Path(f, 'compatibility.json').read_bytes() for f in self.m['SDKS']}
        self.m['stamp'] = '2026-10-01T10:40:00Z'
        self.m.update(cache={}, shared={})
        self.responses = {'/ecosystem/sep-0031.md': (500, '', {})}
        outcomes = {}
        for folder in self.m['SDKS']:
            try:
                self.collect(folder)
                outcomes[folder] = True
            except ValueError:
                outcomes[folder] = False
        self.assertEqual(list(outcomes.values()), [True, True, False, False])
        self.assertTrue(all(((Path(f, 'compatibility.json').read_bytes() == before[f]) != outcomes[f] for f in outcomes)))
        self.m.update(cache={}, shared={})
        self.responses = {'stellar-horizon/releases': (500, '', {})}
        call_start = len(self.calls)
        with self.assertRaises(ValueError):
            self.collect()
        self.assertFalse(any(('releases/latest' in u for u in self.calls[call_start:])))

    def test_history_and_guards(self):
        first = self.collect()
        path = Path('stellar-ios-mac-sdk/compatibility.json')
        old = {**first['history'][0], 'date': '2026-09-29', 'tag': '3.11.0', 'rpc_total': 13}
        older = {**old, 'date': '2026-09-28', 'rpc_total': 14}
        first['history'] = [older, old]
        path.write_text(json.dumps(first))
        data = self.collect()
        self.assertEqual([e['date'] for e in data['history']], ['2026-09-30', '2026-09-29', '2026-09-28'])
        self.assertIn('differs from 13 at tag 3.11.0', data['guards'][0]['text'])
        self.assertEqual(len(self.collect()['history']), 3)
        data['history'] = [{**old, 'rpc_version': 'v27.0.0'}]
        path.write_text(json.dumps(data))
        self.assertEqual(self.collect()['guards'], [])
        matrix = FIXTURES['sdks']['stellar-ios-mac-sdk']['files'][matrix_path('stellar-ios-mac-sdk', 'sep')]
        text = matrix.replace('**SDK Version:** 3.12.0', '**SDK Version:** 3.11.0')
        self.m.update(cache={}, shared={})
        self.responses = {'/SEP-0010_': (200, text, {})}
        data = self.collect()
        guard = data['guards'][0]
        self.assertEqual((guard['section'], guard['sep'], data['history'][0]['sdk_version_matches_tag']), ('sep', 10, False))

        for section in ('horizon', 'rpc'):
            with self.subTest(section=section):
                matrix = FIXTURES['sdks']['stellar-ios-mac-sdk']['files'][
                    matrix_path('stellar-ios-mac-sdk', section)]
                matrix = matrix.replace('**SDK Version:** 3.12.0', '**SDK Version:** 3.11.0')
                self.m.update(cache={}, shared={})
                self.responses = {'/' + section + '/': (200, matrix, {})}
                guards = self.collect()['guards']
                self.assertEqual([(g['kind'], g['section'], g['sep'], g['text']) for g in guards],
                                 [('sdk_version_mismatch', section, None, 'header says 3.11.0')])

    def test_workflow_staleness_and_shell(self):
        for folder, stamp in [('stellar-ios-mac-sdk', '2026-09-22T10:40:00Z'), ('stellar_flutter_sdk', '2026-09-24T10:40:00Z')]:
            Path(folder).mkdir()
            Path(folder, 'compatibility.json').write_text(json.dumps({'collected_at': stamp}))
        self.m['collect'] = lambda sdk: None
        output = self.temp / 'output'
        with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}), contextlib.redirect_stdout(io.StringIO()):
            exec(SOURCE[SOURCE.index('for sdk in SDKS:'):], self.m)
        self.assertEqual(output.read_text(), 'stale=stellar-ios-mac-sdk,stellar-php-sdk,kmp-stellar-sdk\n')
        workflow = WORKFLOW.read_text()
        self.assertLess(workflow.index('id: collection'), workflow.index('- name: Commit and push'))
        self.assertLess(workflow.index('- name: Commit and push'), workflow.index('- name: Staleness check'))
        final = workflow.split('- name: Staleness check', 1)[1].split('- name:', 1)[0]
        self.assertIn("if: ${{ steps.collection.outputs.stale != '' }}", final)
        shell = textwrap.dedent(final.split('run: |\n', 1)[1])
        for value, expected in [('', 0), ('ios', 1)]:
            process = subprocess.run(['bash', '-c', shell], env={**os.environ, 'STALE_SDKS': value}, capture_output=True)
            self.assertEqual(process.returncode, expected)

class FetchTests(unittest.TestCase):

    def test_retries_headers_timeouts_and_budget(self):
        m = helpers()
        calls = []

        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b'body'

        def open_url(request, timeout):
            calls.append((request, timeout))
            if len(calls) == 1:
                raise URLError('connection')
            return Response()
        m['urlopen'] = open_url
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}), patch('time.sleep'):
            self.assertEqual(m['fetch']('https://api.github.com/repos/example/repo')[0:2], (200, 'body'))
            self.assertEqual((len(calls), calls[-1][0].get_header('Authorization'), calls[-1][1]), (2, 'Bearer test-token', 20))
            m['fetch']('https://raw.githubusercontent.com/example/file')
            self.assertIsNone(calls[-1][0].get_header('Authorization'))
        for status in (404, 500, 429):
            count = []

            def error(request, timeout):
                count.append(1)
                raise HTTPError(request.full_url, status, 'failure', {'Retry-After': '1'}, None)
            m['urlopen'] = error
            with patch('time.sleep'):
                if status == 404:
                    self.assertEqual(m['fetch']('https://api.github.com/test')[0], 404)
                else:
                    with self.assertRaises(HTTPError):
                        m['fetch']('https://api.github.com/test')
            self.assertEqual(len(count), 1 if status == 404 else 2)
        m['BUDGET'] = 0
        with self.assertRaisesRegex(TimeoutError, 'budget'):
            m['fetch']('https://api.github.com/test')
if __name__ == '__main__':
    unittest.main()
