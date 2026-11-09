"""CLI-only privacy/authority regressions, with an explicitly authorized real-pin lane.

FM_GRAPH_INSTALL_AUTHORITY=<existing permission reference> enables live package
setup and real OS-network-denied extraction. FM_GRAPH_PYTHON selects an already
installed interpreter supported by graphifyy; no interpreter is downloaded.
FM_GRAPH_PROOF_DIR retains real observations outside every Git copy, if supplied.
The default portable cases install nothing and do not impersonate Graphify.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(sys.argv.pop(1)).resolve()
CLI = ROOT / 'bin/fm-product-graph.py'


class GraphCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        self.private = self.base / 'private'
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1')
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE'):
            self.env.pop(key, None)

    def call(self, operation, *extra, code=2):
        p = subprocess.run([sys.executable, str(CLI), operation, '--project', str(self.project),
                            '--private-root', str(self.private), *extra],
                           env=self.env, capture_output=True, text=True, timeout=180)
        self.assertEqual(p.returncode, code, p.stdout + p.stderr)
        self.assertEqual(p.stderr, '', p.stderr)
        return json.loads(p.stdout)

    def bound_root(self):
        self.private.mkdir(mode=0o700)
        (self.private / 'project.json').write_text(json.dumps({
            'schema': 1, 'project': str(self.project), 'package': 'graphifyy==0.9.102'}))

    def test_missing_private_pin_ignores_wrong_global(self):
        tools = self.base / 'bin'
        tools.mkdir()
        global_tool = tools / 'graphify'
        global_tool.write_text('#!/bin/sh\necho WRONG_GLOBAL_9.9\ntouch "' + str(self.base / 'called') + '"\n')
        global_tool.chmod(0o755)
        self.env['PATH'] = str(tools) + os.pathsep + self.env['PATH']
        value = self.call('probe')
        self.assertEqual(value['outcome'], 'refused')
        self.assertIn('absent', value['reason'])
        self.assertFalse((self.base / 'called').exists())
        self.assertFalse(self.private.exists())

    def test_setup_requires_both_authorities_and_reference(self):
        for flags in ([], ['--allow-install'], ['--allow-network'],
                      ['--allow-install', '--allow-network']):
            value = self.call('setup', *flags)
            self.assertIn('authority', value['reason'])
            self.assertFalse(self.private.exists())

    def test_extraction_never_accepts_install_or_network_flags(self):
        value = self.call('extract', '--allow-network')
        self.assertIn('do not accept', value['reason'])
        self.assertFalse(self.private.exists())

    def test_outputs_cannot_be_in_source_or_git_copy(self):
        self.private = self.project / 'private'
        self.assertIn('disjoint', self.call('setup')['reason'])
        self.private = self.base / 'git' / 'private'
        self.private.parent.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.private.parent)], env=self.env, check=True)
        self.assertIn('Git working copy', self.call('setup')['reason'])
        self.assertFalse(self.private.exists())

    def test_symlink_root_and_parent_refused(self):
        actual = self.base / 'actual'
        actual.mkdir()
        self.private.symlink_to(actual, target_is_directory=True)
        self.assertIn('symlink', self.call('probe')['reason'])
        self.private = self.private / 'child'
        self.assertIn('symlink', self.call('probe')['reason'])

    def test_project_binding_cannot_be_repurposed(self):
        self.bound_root()
        self.project = self.base / 'different-project'
        self.project.mkdir()
        self.assertIn('different project', self.call('probe')['reason'])

    def test_missing_provenance_persists_failure(self):
        self.bound_root()
        value = self.call('extract')
        self.assertEqual(value['outcome'], 'failed')
        self.assertIn('provenance', value['reason'])
        receipt = Path(value['receipt'])
        self.assertTrue(receipt.is_file())
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertEqual(json.loads(receipt.read_text())['outcome'], 'failed')
        second = self.call('extract')
        self.assertNotEqual(value['receipt'], second['receipt'])

    def test_group_readable_private_root_refused(self):
        self.bound_root()
        self.private.chmod(0o755)
        self.assertIn('group/world', self.call('probe')['reason'])

    def test_concurrent_setup_or_extract_cannot_change_environment(self):
        self.bound_root()
        with (self.private / '.operation.lock').open('w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertIn('another operation', self.call('extract')['reason'])
        self.assertEqual(self.call('extract')['outcome'], 'failed')

    @unittest.skipUnless(os.environ.get('FM_GRAPH_INSTALL_AUTHORITY'), 'live setup requires existing package-install/network authority')
    def test_real_pin_privacy_coverage_and_recovery(self):
        if os.environ.get('FM_GRAPH_PROOF_DIR'):
            self.base = Path(os.environ['FM_GRAPH_PROOF_DIR']).resolve()
            self.base.mkdir(parents=True, exist_ok=False)
            self.project, self.private = self.base / 'project', self.base / 'private'
            self.project.mkdir()
        # This is real observation over synthetic product inputs, not a stub tool.
        (self.project / 'app.py').write_text('from helpers import answer\n\ndef run():\n    return answer()\n')
        (self.project / 'helpers.py').write_text('def answer():\n    return 42\n')
        canary = 'DO_NOT_MAP_PRIVATE_CANARY_493187'
        (self.project / '.env').write_text("TOKEN='" + canary + "'\n")
        (self.project / 'secrets.py').write_text("value='" + canary + "'\n")
        (self.project / 'settings.py').write_text("password='" + canary + "'\n")
        (self.project / 'unmapped.wat').write_text('(module)\n')
        (self.project / 'bad.py').write_text('def broken(:\n')
        (self.project / 'data').mkdir()
        (self.project / 'data' / 'hidden.py').write_text(canary)
        (self.project / 'linked.py').symlink_to(self.project / '.env')
        before = {str(p.relative_to(self.project)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.project.rglob('*') if p.is_file() and not p.is_symlink()}
        self.assertEqual(self.call('probe')['outcome'], 'refused')
        self.assertEqual(self.call('setup', '--allow-install', '--authority', 'denied-network')['outcome'], 'refused')
        # Actual denied package traffic, not just a missing authority flag.
        if sys.platform == 'darwin':
            prefix = [shutil.which('sandbox-exec'), '-p', '(version 1)(allow default)(deny network*)']
        else:
            prefix = [shutil.which('unshare'), '--user', '--map-root-user', '--net', '--']
        self.assertTrue(prefix[0], 'real proof needs the supported OS network sandbox')
        p = subprocess.run(prefix + [sys.executable, str(CLI), 'setup', '--project', str(self.project),
                            '--private-root', str(self.private), '--allow-install', '--allow-network',
                            '--authority', os.environ['FM_GRAPH_INSTALL_AUTHORITY'], '--python',
                            os.environ.get('FM_GRAPH_PYTHON', sys.executable), '--timeout', '3'],
                           env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        denied = json.loads(p.stdout)
        self.assertEqual(denied['outcome'], 'failed')
        self.assertTrue(Path(denied['receipt']).exists())
        self.assertFalse((self.private / 'installation.json').exists())
        # Retry in the same private environment after restoring package network.
        setup = self.call('setup', '--allow-install', '--allow-network', '--authority',
                          os.environ['FM_GRAPH_INSTALL_AUTHORITY'], '--python',
                          os.environ.get('FM_GRAPH_PYTHON', sys.executable), code=0)
        self.assertEqual(setup['environment']['version'], '0.9.102')
        self.assertTrue(Path(setup['resolver']['lock']).is_file())
        self.assertTrue(Path(setup['environment']['executable']).is_relative_to(self.private))
        probe = self.call('probe', code=0)
        self.assertTrue(probe['environment']['capabilities']['code_only'])
        self.assertTrue(probe['environment']['capabilities']['network']['os_denial_observed'])
        result = self.call('extract')
        self.assertEqual(result['outcome'], 'partial')
        self.assertEqual(result['network']['source_outbound_calls'], 0)
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual({r['path'] for r in manifest['parse_failed']}, {'bad.py'})
        self.assertIn('unmapped.wat', {r['path'] for r in manifest['unsupported']})
        self.assertTrue({'.env', 'secrets.py', 'settings.py', 'linked.py', 'data'} <= {r['path'] for r in manifest['excluded']})
        graph = json.loads(Path(result['graph']).read_text())
        self.assertTrue(any(n['label'] == 'answer()' for n in graph['nodes']))
        self.assertTrue(any(e['relation'] == 'calls' for e in graph['edges']))
        for p in Path(result['output']).rglob('*'):
            if p.is_file():
                self.assertNotIn(canary.encode(), p.read_bytes(), str(p))
        after = {str(p.relative_to(self.project)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.project.rglob('*') if p.is_file() and not p.is_symlink()}
        self.assertEqual(before, after)
        self.assertFalse((self.project / 'AGENTS.md').exists())
        # Recovery: remove the unsupported and broken input, not the failed receipts.
        (self.project / 'unmapped.wat').unlink()
        (self.project / 'bad.py').unlink()
        recovered = self.call('extract', code=0)
        self.assertEqual(recovered['outcome'], 'success')
        self.assertTrue(Path(result['receipt']).exists())
        # Unsupported-only is never a successful/empty baseline.
        (self.project / 'app.py').unlink()
        (self.project / 'helpers.py').unlink()
        (self.project / 'unmapped.wat').write_text('(module)\n')
        self.assertEqual(self.call('extract')['outcome'], 'unsupported')
        # Metadata mismatch refuses before any package execution, despite a global tool.
        metadata = Path(setup['environment']['metadata']) / 'METADATA'
        original = metadata.read_text()
        try:
            metadata.write_text(original.replace('Version: 0.9.102\n', 'Version: 9.9\n', 1))
            self.assertEqual(self.call('probe')['outcome'], 'unavailable')
        finally:
            metadata.write_text(original)
        # No installer is called during extraction; dropping uv from PATH is safe.
        self.env['PATH'] = '/usr/bin:/bin'
        self.assertEqual(self.call('extract')['outcome'], 'unsupported')
        (self.base / 'observations.json').write_text(json.dumps({
            'network_denied_setup': denied, 'setup': setup, 'probe': probe, 'partial': result, 'recovered': recovered,
            'source_before': before, 'source_after': after, 'canary_absent': True}, indent=2))


if __name__ == '__main__':
    unittest.main(verbosity=2)
