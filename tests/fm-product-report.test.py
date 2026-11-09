#!/usr/bin/env python3
"""Portable public CLI tests; a separate guarded Herdr test proves the sink."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(sys.argv.pop(1))
CLI = ROOT / 'bin/fm-product-report.py'


class Reports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / '.fm-secondmate-home').write_text('mate1\n')
        (self.home / 'state').mkdir()
        self.screen = self.home / 'screen'
        self.screen.write_text('')
        self.helper = self.home / 'helper'
        self.helper.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[3:]
if args[:3] == ['terminal', 'title', 'clear']:
    print(json.dumps({'result': {'reason': 'no_foreground_client' if os.environ.get('DOWN') else 'cleared'}}))
elif args[:2] == ['pane', 'get']:
    print(json.dumps({'result': {'pane': {'terminal_id': 'term1', 'focused': True}}}))
elif args[:2] == ['pane', 'read']:
    print(pathlib.Path(os.environ['SCREEN']).read_text(), end='')
elif args[:2] == ['pane', 'run']:
    pass
else:
    sys.exit(2)
''')
        self.helper.chmod(0o700)
        self.env = dict(os.environ, FM_HOME=str(self.home), SCREEN=str(self.screen),
                        FM_PRODUCT_REPORT_LAB_HELPER=str(self.helper))
        (self.home / 'state/db.status').write_text('working [at=1000]: database\n')

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, now, *args, down=False, ok=True):
        env = dict(self.env, FM_PRODUCT_REPORT_NOW=str(now))
        if down:
            env['DOWN'] = '1'
        result = subprocess.run([sys.executable, str(CLI), *args], env=env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return result

    def state(self):
        return json.loads((self.home / 'data/product-report/active.json').read_text())

    def visible(self):
        self.screen.write_text(self.state()['pending']['text'])

    def test_recovery_and_long_publication_preserve_due_routine_reports(self):
        self.run_cli(1000, 'prepare', 'build-one', '--session', 'fm-lab-report', '--pane', 'w1:p1', '--terminal-id', 'term1')
        self.visible(); self.run_cli(1000, 'activate')
        document = self.home / 'data/final.txt'
        document.write_text(''.join(f'result {i}\n' for i in range(35)))
        self.run_cli(1001, 'publish', 'summary-one', str(document), '--build', 'build-two', ok=False)
        self.assertEqual(self.state().get('publications', []), [])
        self.run_cli(1001, 'publish', 'summary-one', str(document), '--build', 'build-one')
        self.assertEqual(self.state()['pending']['created'], 1001)
        self.visible(); self.run_cli(1002, 'tick')
        self.visible(); self.run_cli(1899, 'activate')
        self.assertEqual(self.state()['start'], 1000)
        self.run_cli(1900, 'tick')
        current = self.state()
        self.assertEqual(current['pending']['slot'], 1)
        self.assertNotIn('publication', current['pending'])
        self.assertLess(current['publications'][0]['next'], len(current['publications'][0]['chunks']))
        self.visible(); self.run_cli(1901, 'tick')
        self.assertEqual(self.state()['pending']['publication'], 'summary-one')

    def test_final_report_exact_pages_failure_restart_and_zero_changes(self):
        self.run_cli(1000, 'prepare', 'build', '--session', 's', '--pane', 'w1:p1', '--terminal-id', 'term1')
        self.visible(); self.run_cli(1000, 'activate')
        report = self.home / 'data/final.txt'
        raw = 'Authorized changes: none (zero changes)\n' + ''.join(f'Exact evidence row {i}\n' for i in range(24))
        report.write_text(raw)
        self.run_cli(1001, 'publish', 'final-one', str(report))
        reserved = self.state()['pending']
        self.visible(); self.run_cli(1002, 'tick', down=True)
        self.assertEqual(self.state()['pending'], reserved)
        self.assertEqual(self.state()['publications'][0]['next'], 0)
        self.run_cli(1003, 'stop', ok=False)
        visible = []
        for now in (1004, 1005, 1006):
            pending = self.state()['pending']
            visible.append(pending['text'])
            self.visible(); self.run_cli(now, 'tick')
        publication = self.state()['publications'][0]
        self.assertEqual(publication['next'], 3)
        self.assertEqual(len(publication['receipts']), 3)
        self.assertEqual(''.join(publication['chunks']), raw)
        self.assertTrue(all(chunk in page for chunk, page in zip(publication['chunks'], visible)))
        self.run_cli(1007, 'publish', 'final-one', str(report))
        self.assertEqual(self.state()['publications'][0], publication)
        report.write_text('Changed inventory\n')
        self.run_cli(1008, 'publish', 'final-one', str(report), ok=False)
        self.assertEqual(self.state()['publications'][0], publication)
        self.assertFalse((self.home / 'state/parent-replies.status').exists())
        self.run_cli(1009, 'stop')

    def test_reporting_and_recovery(self):
        self.run_cli(1000, 'prepare', 'build', '--session', 's', '--pane', 'w1:p1',
                     '--terminal-id', 'term1', '--worker', 'db')
        self.run_cli(1000, 'activate', ok=False)
        self.visible()
        self.run_cli(1000, 'activate')
        self.run_cli(1899, 'tick')
        self.assertIsNone(self.state()['pending'])
        self.run_cli(1900, 'tick')
        pending = self.state()['pending']
        self.assertIn('unchanged', pending['text'])
        self.visible()
        self.run_cli(1901, 'tick')
        self.assertEqual(self.state()['receipts'][-1]['slot'], 1)
        self.run_cli(2800, 'tick')
        pending = self.state()['pending']
        self.assertIn('stale', pending['text'])
        self.visible()
        self.run_cli(2801, 'tick', down=True)
        self.assertEqual(self.state()['pending'], pending)
        self.assertEqual(len(self.state()['receipts']), 2)
        self.run_cli(4600, 'tick')
        self.assertEqual(self.state()['receipts'][-1]['sha256'], pending['sha256'])
        self.assertIn('missed intervals: 1', self.state()['pending']['text'])
        self.visible()
        self.run_cli(4601, 'tick')
        self.assertEqual(self.state()['receipts'][-1]['slot'], 4)
        proof = self.home / 'data' / 'proof.txt'
        proof.write_text('verified observation')
        self.run_cli(4602, 'evidence', str(proof))
        self.run_cli(5500, 'tick')
        self.assertIn('Evidence observed', self.state()['pending']['text'])
        self.assertIn('changed', self.state()['pending']['text'])
        self.assertFalse((self.home / 'state/parent-replies.status').exists())


if __name__ == '__main__':
    unittest.main()
