"""Dedicated build public CLI replay, using real approved persisted contracts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location('contract_cases', ROOT / 'tests/fm-product-contract.test.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
sys.argv.insert(1, str(ROOT))
report_spec = importlib.util.spec_from_file_location('report_cases', ROOT / 'tests/fm-product-report.test.py')
report_cases = importlib.util.module_from_spec(report_spec)
report_spec.loader.exec_module(report_cases)


class Build(unittest.TestCase):
    def setUp(self):
        fixtures.ProductContract.setUp(self)
        self.project = self.home / 'projects/app'
        self.project.mkdir(parents=True)
        (self.project / 'app.py').write_text('def save():\n    return "retained"\n')

    tearDown = fixtures.ProductContract.tearDown
    call = fixtures.ProductContract.call
    event = fixtures.ProductContract.event
    draft = fixtures.ProductContract.draft
    approve = fixtures.ProductContract.approve
    evidence = fixtures.ProductContract.evidence

    def build(self, command, bid='build-one', data=None, env=None, ok=True):
        proc = subprocess.run([sys.executable, str(ROOT / 'bin/fm-product-build.py'), command, bid],
                              input=json.dumps(data or {}), text=True, capture_output=True, env=env or self.env)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            return json.loads(proc.stdout)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        return proc.stderr

    def emit(self, bid='build-one'):
        return self.build('emit', bid, {'contract': 'sample', 'project': str(self.project), 'mode': 'local-only',
                          'yolo': 'off', 'branch_prefix': 'fm/', 'base_branch': 'main',
                          'baseline': {'outcome': 'unavailable', 'reason': 'not installed'}})

    def seed(self):
        # Exact durable provisioning records; no endpoint or live vendor fixture.
        mate_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(mate_tmp.cleanup)
        self.mate = Path(mate_tmp.name).resolve()
        (self.mate / '.fm-secondmate-home').write_text('builder\n')
        (self.mate / '.fm-secondmate-parent').write_text('schema=fm-secondmate-parent.v1\nroute=local\nparent_home=' + str(self.home) + '\n')
        (self.home / 'data/secondmates.md').write_text('- builder - dedicated build (home: ' + str(self.mate) + '; scope: one build; projects: none; added 2026-11-06)\n')
        self.menv = dict(self.env, FM_HOME=str(self.mate))

    def test_two_workflow_discovery_metadata(self):
        # Skill front matter is the public discovery/invocation interface, not
        # executable implementation source. Do not assert procedure prose here.
        discovered = {}
        for path in (ROOT / '.agents/skills').glob('product-*/SKILL.md'):
            header = path.read_text().split('---', 2)[1]
            fields = {}
            for line in header.splitlines():
                if line and not line.startswith(' '):
                    key, value = line.split(':', 1)
                    fields[key] = value.strip()
            fields['internal'] = bool(re.search(r'^  internal: true$', header, re.MULTILINE))
            discovered[fields['name']] = fields
        self.assertEqual({name: (fields['user-invocable'], fields['internal'])
                          for name, fields in discovered.items()},
                         {'product-plan': ('true', True),
                          'product-build': ('true', True),
                          'product-contract': ('false', True)})

    def test_plan_emission_stops_then_later_build_uses_same_engine(self):
        approved_digest = self.approve()
        original = self.call('inspect')
        artifact = self.emit()
        self.assertEqual(artifact, self.build('verify'))  # a new CLI process, not model memory
        self.assertEqual(artifact['digest'], approved_digest)
        self.assertEqual(artifact['work'], self.contract['work'])
        # Approved Plan only emitted private material, not an owner or workers.
        self.assertFalse((self.home / 'data/product-build/owners.json').exists())
        self.assertFalse((self.home / '.fm-product-build.json').exists())
        self.assertFalse(list((self.home / 'state').glob('*.meta')))
        self.assertEqual(self.call('inspect'), original)
        self.build('wave', ok=False)
        # Explicit later Build adopts the exact retained artifact and shared
        # source/audit/approval ledger; it does not maintain a second spec.
        self.seed()
        self.build('reserve', data={'mate': 'builder', 'home': str(self.mate)})
        self.build('import', data={'parent': str(self.home)}, env=self.menv)
        self.assertEqual(self.build('show', env=self.menv)['artifact'], artifact)
        old = self.env
        self.env = self.menv
        try:
            self.assertEqual(self.call('inspect'), original)
        finally:
            self.env = old
        self.clone_project()
        self.prepare_units(reporting=False)
        waiting = self.build('wave', env=self.menv)['units'][0]
        self.assertEqual(waiting['state'], 'waiting')
        self.assertIn('verified initial build reporting', waiting['reason'])
        self.activate_report()
        self.assertEqual(self.build('wave', env=self.menv)['units'][0]['state'], 'ready')
        self.assertFalse(list((self.mate / 'state').glob('*.meta')))
        self.event('EV-004', 'source', 'Changed product semantics need revised approval.')
        self.build('verify', ok=False)
        self.build('wave', env=self.menv, ok=False)

    def test_approval_artifact_and_lifetime_owner(self):
        self.emit_unapproved()
        self.approve(); self.seed()
        value = self.emit()
        self.assertEqual(value, self.emit())
        self.assertEqual(value, self.build('verify'))
        request = {'mate': 'builder', 'home': str(self.mate)}
        first = self.build('reserve', data=request)
        self.assertEqual(first, self.build('reserve', data=request))
        self.emit('other-build')
        self.assertIn('lifetime', self.build('reserve', 'other-build', request, ok=False))
        self.assertFalse((self.mate / '.fm-product-build.json').exists())
        self.build('import', data={'parent': str(self.home)}, env=self.menv)
        self.assertEqual(first, self.build('import', data={'parent': str(self.home)}, env=self.menv))
        self.assertEqual(first, self.build('show', env=self.menv)['owner'])
        self.assertIn('unrelated', self.build('show', 'other-build', env=self.menv, ok=False))
        self.build('reserve', data=request, env=self.menv, ok=False)

    def emit_unapproved(self):
        self.build('emit', data={'contract': 'sample', 'project': str(self.project), 'mode': 'local-only',
                   'yolo': 'off', 'branch_prefix': 'fm/', 'base_branch': 'main',
                   'baseline': {'outcome': 'unavailable'}}, ok=False)

    def start(self):
        self.approve(); self.seed(); self.emit()
        self.build('reserve', data={'mate': 'builder', 'home': str(self.mate)})
        self.build('import', data={'parent': str(self.home)}, env=self.menv)

    def test_first_import_preserves_preexisting_ordinary_work(self):
        self.approve(); self.seed(); self.emit()
        self.build('reserve', data={'mate': 'builder', 'home': str(self.mate)})
        (self.mate / 'state').mkdir()
        prior = self.mate / 'state/prior-work.meta'
        prior.write_text('kind=ship\nproject=/preserved/unlanded/work\n')
        refusal = self.build('import', data={'parent': str(self.home)}, env=self.menv, ok=False)
        self.assertIn('prior ordinary task records', refusal)
        self.assertEqual(prior.read_text(), 'kind=ship\nproject=/preserved/unlanded/work\n')
        self.assertFalse((self.mate / '.fm-product-build.json').exists())

    def test_import_replay_preserves_real_attempts(self):
        self.start()
        parent_env = self.env
        self.env = self.menv
        try:
            self.evidence()
            previous = self.call('inspect')
            self.assertEqual(len(previous['results']), 2)
            self.build('import', data={'parent': str(self.home)}, env=self.menv)
            self.assertEqual(previous, self.call('inspect'))
        finally:
            self.env = parent_env

    def test_stale_approval_and_changed_artifact_refuse(self):
        self.start()
        self.event('EV-004', 'source', 'An additional product requirement.')
        self.build('verify', ok=False)
        self.build('reserve', data={'mate': 'builder', 'home': str(self.mate)}, ok=False)
        self.build('import', data={'parent': str(self.home)}, env=self.menv, ok=False)

    def test_corrupt_artifact_and_symlinked_home_refuse(self):
        self.approve(); self.seed(); self.emit()
        path = self.home / 'data/product-build/artifacts/build-one.json'
        value = json.loads(path.read_text()); value['mode'] = 'direct-PR'
        path.write_text(json.dumps(value, separators=(',', ':'), sort_keys=True) + '\n')
        self.build('verify', ok=False)
        linked = self.home / 'alias'
        linked.symlink_to(self.mate, target_is_directory=True)
        self.build('reserve', data={'mate': 'builder', 'home': str(linked)}, ok=False)

    def test_unreserved_remote_and_corrupt_reservations_refuse(self):
        self.approve(); self.seed(); self.emit()
        self.build('import', data={'parent': str(self.home)}, env=self.menv, ok=False)
        self.assertFalse((self.mate / '.fm-product-build.json').exists())
        (self.mate / '.fm-secondmate-parent').write_text('schema=fm-secondmate-parent.v1\nroute=remote\n')
        self.build('reserve', data={'mate': 'builder', 'home': str(self.mate)}, ok=False)
        (self.mate / '.fm-secondmate-parent').write_text('schema=fm-secondmate-parent.v1\nroute=local\nparent_home=' + str(self.home) + '\n')
        self.build('reserve', data={'mate': 'builder', 'home': str(self.mate)})
        path = self.home / 'data/product-build/owners.json'
        record = json.loads(path.read_text()); record['owners'] = []
        path.write_text(json.dumps(record, separators=(',', ':'), sort_keys=True) + '\n')
        self.build('reserve', data={'mate': 'builder', 'home': str(self.mate)}, ok=False)
        self.assertEqual((self.project / 'app.py').read_text(), 'def save():\n    return "retained"\n')
        self.assertFalse(list((self.home / 'state').glob('*.meta')))

    def git(self, path, *args):
        proc = subprocess.run(['git', '-C', str(path), *args], env=self.env, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout.strip()

    def clone_project(self):
        self.git(self.project, 'init', '-b', 'main')
        self.git(self.project, 'add', '.')
        self.git(self.project, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Initial fixture')
        destination = self.mate / 'projects/app'
        destination.parent.mkdir()
        proc = subprocess.run(['git', 'clone', '--no-hardlinks', str(self.project), str(destination)],
                              env=self.env, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.build('stamp-project', data={'home': str(self.mate), 'project': str(self.project)})
        return destination

    def activate_report(self):
        report = report_cases.Reports('test_reporting_and_recovery')
        report.setUp(); self.addCleanup(report.tearDown)
        report.home = self.mate
        report.env = dict(self.menv, SCREEN=str(report.screen), FM_PRODUCT_REPORT_LAB_HELPER=str(report.helper))
        report.run_cli(1000, 'prepare', 'build-one', '--session', 'fixture', '--pane', 'w1:p1', '--terminal-id', 'term1')
        report.visible(); report.run_cli(1000, 'activate')
        self.menv.update(FM_PRODUCT_REPORT_LAB_HELPER=str(report.helper))
        return report

    def prepare_units(self, reporting=True):
        original_env = self.env
        self.env = self.menv
        try:
            intent = self.call('intent')['text']
            for unit in self.contract['work']:
                brief = self.mate / 'data' / unit['task'] / 'brief.md'
                brief.parent.mkdir(parents=True, exist_ok=True)
                brief.write_text("# Task\n## Captain's intent\n" + intent + '\n\n## Firstmate spec\n' + unit['instructions'] + '\n\n# Setup\nProduct contract: required\n')
                self.call('bind', data={'task': unit['task'], 'unit': unit['id']})
            self.plan = {'integration_unit': self.contract['work'][0]['id'],
                         'slices': [{'id': unit['id'], 'inputs': ['Approved requirements and accepted dependency receipts'],
                                    'interface': 'Public CLI preservation', 'non_goals': ['No unrelated behavior'],
                                    'scope': ['app.py'], 'checkpoint': 'One bounded approved slice; stop for handoff before extending it'}
                                   for unit in self.contract['work']]}
            prepared = self.build('prepare', data=self.plan, env=self.menv)
            if reporting:
                self.activate_report()
            return prepared
        finally:
            self.env = original_env

    def test_no_worker_allocation_before_verified_initial_reporting(self):
        self.start(); destination = self.clone_project(); self.prepare_units(reporting=False)
        permit = {'kind': 'ship', 'project': str(destination), 'mode': 'local-only', 'yolo': 'off'}
        refusal = self.build('guard', 'task-one', permit, env=self.menv, ok=False)
        self.assertIn('verified initial build reporting', refusal)
        self.assertFalse(list((self.mate / 'state').glob('*.meta')))
        self.activate_report()
        self.build('guard', 'task-one', permit, env=self.menv)

    def test_dedicated_admission_and_parent_current_approval(self):
        self.start()
        destination = self.clone_project()
        self.prepare_units()
        permit = {'kind': 'ship', 'project': str(destination), 'mode': 'local-only', 'yolo': 'off'}
        self.assertEqual(self.build('guard', 'task-one', permit, env=self.menv)['outcome'], 'dedicated-task-authorized')
        self.build('guard', 'unapproved-task', permit, env=self.menv, ok=False)
        self.build('guard', 'task-one', dict(permit, kind='secondmate'), env=self.menv, ok=False)
        self.build('guard', 'task-one', dict(permit, yolo='on'), env=self.menv, ok=False)
        self.build('guard', 'task-one', dict(permit, project=str(self.project)), env=self.menv, ok=False)
        owner = {'kind': 'secondmate', 'project': str(self.mate), 'mode': '', 'yolo': ''}
        self.build('guard', 'builder', owner)
        self.build('guard', 'different-mate', owner, ok=False)
        parent_ship = {'kind': 'ship', 'project': str(self.project), 'mode': 'local-only', 'yolo': 'off'}
        self.assertIn('dedicated owner', self.build('guard', 'task-one', parent_ship, ok=False))
        self.assertEqual(self.build('guard', 'unrelated-maintenance', parent_ship)['outcome'], 'ordinary-admission-unchanged')
        self.event('EV-004', 'source', 'Changed meaning must stop already-imported work.')
        self.build('guard', 'task-one', permit, env=self.menv, ok=False)
        self.build('guard', 'builder', owner, ok=False)

    def test_local_route_is_exact_and_clone_provenance_is_immutable(self):
        self.start()
        permit = {'home': str(self.mate), 'project': str(self.project)}
        self.build('route', data=permit)
        self.build('route', data=dict(permit, project=str(self.home)), ok=False)
        self.build('route', data=permit, env=self.menv, ok=False)
        destination = self.clone_project()
        before = (destination / '.git/fm-product-build.json').read_bytes()
        self.build('stamp-project', data=permit)
        self.assertEqual(before, (destination / '.git/fm-product-build.json').read_bytes())
        self.git(destination, 'remote', 'set-url', 'origin', str(self.home))
        self.build('stamp-project', data=permit, ok=False)
        self.assertEqual(before, (destination / '.git/fm-product-build.json').read_bytes())
        (self.home / 'data/secondmates.md').write_text('')
        self.build('route', data=permit, ok=False)
        self.build('show', env=self.menv, ok=False)

    def test_wave_exact_roster_and_unmeasured_bounded_checkpoint(self):
        # Independent slices may overlap app.py; file overlap is not a DAG edge.
        self.contract['work'] = [dict(self.contract['work'][0], id=uid, task=task, level='slice', depends=deps)
                                 for uid, task, deps in [('WU-001', 'task-one', []), ('WU-002', 'task-two', []),
                                                         ('WU-003', 'task-three', ['WU-001', 'WU-002'])]]
        self.start(); destination = self.clone_project(); original = self.prepare_units()
        self.assertEqual(original, self.build('prepare', data=self.plan, env=self.menv))
        wave = self.build('wave', env=self.menv)
        self.assertEqual([u['state'] for u in wave['units']], ['ready', 'ready', 'waiting'])
        self.assertEqual(wave['context_pressure']['measurement'], 'unknown')
        self.assertIn('WU-001', wave['units'][2]['reason'])
        permit = {'kind': 'ship', 'project': str(destination), 'mode': 'local-only', 'yolo': 'off'}
        self.build('guard', 'task-one', permit, env=self.menv)
        self.build('guard', 'task-two', permit, env=self.menv)
        self.build('guard', 'task-three', permit, env=self.menv, ok=False)
        changed = dict(self.plan, integration_unit='WU-002')
        self.build('prepare', data=changed, env=self.menv, ok=False)
        aliases = dict(self.plan, slices=[dict(self.plan['slices'][0], id='unbound-alias')])
        self.build('prepare', data=aliases, env=self.menv, ok=False)
        brief = self.mate / 'data/task-one/brief.md'
        brief.write_text(brief.read_text().replace('Build real persistence', 'Different task'))
        self.build('guard', 'task-one', permit, env=self.menv, ok=False)

    def test_parent_guarded_local_delivery_and_confirmed_child_sync(self):
        self.start(); destination = self.clone_project(); self.prepare_units()
        wt = self.mate / 'delivery-worker'
        self.git(destination, 'worktree', 'add', '-b', 'fm/task-one', str(wt))
        (wt / 'app.py').write_text('def save():\n    return "retained"\n\ndef reload():\n    return save()\n')
        self.git(wt, 'add', 'app.py')
        self.git(wt, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Delivery fixture')
        head = self.git(wt, 'rev-parse', 'HEAD')
        before = self.git(self.project, 'rev-parse', 'HEAD')
        (self.mate / 'state').mkdir(exist_ok=True)
        (self.mate / 'state/task-one.meta').write_text('kind=ship\nproject=' + str(destination) + '\nworktree=' + str(wt)
            + '\nbranch=fm/task-one\nmode=local-only\nyolo=off\nspawn_gen=delivery-one\nlocal_done_head=' + head + '\n')
        old_env, old_home = self.env, self.home
        self.env, self.home = self.menv, self.mate
        try:
            self.evidence(head=head)
        finally:
            self.env, self.home = old_env, old_home
        self.build('sync-guard', data={'project': str(destination)}, env=self.menv, ok=False)
        self.build('offer', data={'id': 'wave-one', 'unit': 'WU-001', 'units': ['WU-001'], 'head': head}, env=self.menv)
        self.build('delivery-meta', data={'id': 'wave-one'}, ok=False)
        self.assertEqual(self.git(self.project, 'rev-parse', 'HEAD'), before)
        self.event('EV-LAND', 'delivery-authority', 'Merge this exact synthetic fixture head.', head=head, authority='merge')
        (self.home / 'state').mkdir(exist_ok=True)
        env = dict(self.env, FM_ROOT_OVERRIDE=str(ROOT))
        proc = subprocess.run(['bash', str(ROOT / 'bin/fm-merge-local.sh'), 'task-one', '--build-delivery', 'build-one', 'wave-one', 'chat:EV-LAND'], env=env, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.git(self.project, 'rev-parse', 'HEAD'), head)
        self.assertEqual(self.git(destination, 'rev-parse', 'HEAD'), before)
        self.assertFalse((self.home / 'state/task-one.meta').exists())
        self.build('sync-guard', data={'project': str(destination)}, env=self.menv)
        (self.mate / 'data/projects.md').write_text('- app [local-only] - synthetic project (added 2026-11-08)\n')
        sync = subprocess.run(['bash', str(ROOT / 'bin/fm-fleet-sync.sh'), str(destination)], env=dict(self.menv, FM_ROOT_OVERRIDE=str(ROOT)), text=True, capture_output=True)
        self.assertEqual(sync.returncode, 0, sync.stderr + sync.stdout)
        self.assertEqual(self.git(destination, 'rev-parse', 'HEAD'), head, sync.stdout)
        self.env, self.home = self.menv, self.mate
        try:
            relative = self.last_artifacts['AC-001']
            reference = {'path': relative, 'sha256': hashlib.sha256((self.mate / 'data/product-contract/sample/artifacts' / relative).read_bytes()).hexdigest()}
            self.build('integrate', data={'unit': 'WU-001', 'integrator': 'WU-001', 'head': head, 'proof': reference}, env=self.menv)
            (wt / 'app.py').write_text((wt / 'app.py').read_text() + '\n# accepted next wave\n')
            self.git(wt, 'add', 'app.py')
            self.git(wt, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Next fixture wave')
            successor = self.git(wt, 'rev-parse', 'HEAD')
            request = {'task': 'task-one', 'before': head, 'head': successor}
            self.assertIn('fresh incarnation', self.build('ready-successor', data=request, env=self.menv, ok=False))
            metadata = self.mate / 'state/task-one.meta'
            metadata.write_text(metadata.read_text().replace('spawn_gen=delivery-one', 'spawn_gen=delivery-two'))
            self.build('ready-successor', data=request, env=self.menv, ok=False)  # missing successor proof
            self.evidence(head=successor)
            self.build('ready-successor', data=request, env=self.menv)
            pin = subprocess.run(['bash', '-c', 'source "$1/bin/fm-dod-lib.sh"; captured=$(< "$2"); fm_dod_pin_local_done_head "$3" task-one "$2" "$4" "$captured"', 'pin', str(ROOT), str(metadata), str(self.mate / 'state'), successor], env=self.menv, text=True, capture_output=True)
            self.assertEqual(pin.returncode, 0, pin.stderr)
            self.assertIn('local_done_head=' + successor, metadata.read_text())
            previous = json.loads((old_home / 'state/.product-build-delivery/build-one/wave-one/request.json').read_text())
            self.assertEqual(previous['offer']['head'], head)
            self.build('ready-successor', data=dict(request, task='task-two'), env=self.menv, ok=False)
        finally:
            self.env, self.home = old_env, old_home

    def test_named_integration_and_durable_handoff_use_real_git_and_shared_evaluator(self):
        self.start(); destination = self.clone_project(); self.prepare_units()
        wt = self.mate / 'isolated-worker'
        self.git(destination, 'worktree', 'add', '-b', 'fm/fixture', str(wt))
        (wt / 'app.py').write_text('def save():\n    return "retained"\n\ndef reload():\n    return save()\n')
        self.git(wt, 'add', 'app.py')
        self.git(wt, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Implement fixture')
        head = self.git(wt, 'rev-parse', 'HEAD')
        (self.mate / 'state').mkdir()
        metadata = self.mate / 'state/task-one.meta'
        metadata.write_text('kind=ship\nproject=' + str(destination) + '\nworktree=' + str(wt)
                            + '\nmode=local-only\nyolo=off\nspawn_gen=fixture-incarnation-one\nbackend=tmux\n')
        old_home, old_env = self.home, self.env
        self.home, self.env = self.mate, self.menv
        try:
            self.evidence(head=head)
            relative = self.last_artifacts['AC-001']
            raw = (self.mate / 'data/product-contract/sample/artifacts' / relative).read_bytes()
            reference = {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()}
            request = {'unit': 'WU-001', 'integrator': 'WU-001', 'head': head, 'proof': reference}
            self.build('integrate', data=request, env=self.menv, ok=False)
            self.git(destination, 'merge', '--ff-only', 'fm/fixture')
            receipt = self.build('integrate', data=request, env=self.menv)
            self.assertEqual(receipt, self.build('integrate', data=request, env=self.menv))
            self.assertEqual(receipt['acceptance']['verified_acceptance'], ['AC-001', 'AC-002'])
            self.assertEqual(receipt['integration_worker'], 'fixture-incarnation-one')
            (wt / 'app.py').write_text((wt / 'app.py').read_text() + '\n# corrected implementation\n')
            self.git(wt, 'add', 'app.py')
            self.git(wt, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Correct fixture')
            head = self.git(wt, 'rev-parse', 'HEAD')
            self.git(destination, 'merge', '--ff-only', 'fm/fixture')
            self.evidence(head=head)
            relative = self.last_artifacts['AC-001']
            reference = {'path': relative, 'sha256': hashlib.sha256((self.mate / 'data/product-contract/sample/artifacts' / relative).read_bytes()).hexdigest()}
            correction = dict(request, head=head, proof=reference)
            self.build('integrate', data=correction, env=self.menv, ok=False)
            corrected = self.build('integrate', data=dict(correction, supersedes=receipt['hash']), env=self.menv)
            self.assertEqual(corrected['supersedes'], receipt['hash'])
            history = self.build('show', env=self.menv)['execution']['receipts']
            self.assertEqual([r['hash'] for r in history], [receipt['hash'], corrected['hash']])
            (wt / 'app.py').write_text((wt / 'app.py').read_text() + '\n# pending local work\n')
            checkpoint = {'id': 'checkpoint-one', 'unit': 'WU-001', 'commits': [head], 'artifacts': [reference],
                          'decisions': ['Preserve public CLI'], 'defects': [], 'next_action': 'Finish pending assertion',
                          'validation_custody': 'Existing local-only task acceptance'}
            handoff = self.build('handoff', data=checkpoint, env=self.menv)
            self.assertEqual(handoff, self.build('handoff', data=checkpoint, env=self.menv))
            self.assertEqual(handoff['handoff']['head'], head)
            self.assertEqual(handoff['handoff']['incarnation'], 'fixture-incarnation-one')
            self.assertIn('app.py', handoff['handoff']['dirty'])
            self.assertEqual(handoff['handoff']['pressure']['measurement'], 'unknown')
            before_meta = metadata.read_text()
            metadata.write_text(before_meta.replace('backend=tmux', 'backend=herdr') + 'harness=pi\n')
            before_replacement = metadata.read_bytes()
            refusal = self.build('replace', data={'id': 'fresh-one', 'handoff': 'checkpoint-one'}, env=self.menv, ok=False)
            self.assertIn('complete recorded endpoint identity', refusal)
            self.assertEqual(metadata.read_bytes(), before_replacement)
            self.assertFalse((self.mate / 'data/product-build/actions/build-one/fresh-one.json').exists())
            metadata.write_text(before_meta)
            self.build('handoff', data=dict(checkpoint, next_action='Changed checkpoint'), env=self.menv, ok=False)
            self.assertIn('pending local work', (wt / 'app.py').read_text())
        finally:
            self.home, self.env = old_home, old_env

    def test_dispatch_reservation_replay_uses_real_existing_admission(self):
        self.contract['work'] = [dict(self.contract['work'][0], id=uid, task=task, level='slice', depends=deps)
                                 for uid, task, deps in [('WU-001', 'task-one', []), ('WU-002', 'task-two', []),
                                                         ('WU-003', 'task-three', ['WU-001'])]]
        self.start(); self.clone_project(); self.prepare_units()
        profile = {'id': 'WU-001', 'harness': 'unverified-product-test', 'model': 'default', 'effort': 'low',
                   'dispatch_override': 'Negative admission test; no supported agent may launch'}
        request = {'id': 'dispatch-one', 'profiles': [profile, dict(profile, id='WU-002')]}
        action = self.build('dispatch', data=request, env=self.menv)
        self.assertEqual(action['outcome'], 'reconcile-results')
        self.assertEqual(len(action['results']), 2)
        self.assertTrue(all(row['exit'] != 0 for row in action['results']))
        self.assertEqual(action, self.build('dispatch', data=request, env=self.menv))
        self.assertEqual(action, self.build('action', data={'id': 'dispatch-one'}, env=self.menv))
        self.assertFalse(list((self.mate / 'state').glob('*.meta')))
        self.build('dispatch', data=dict(request, profiles=[dict(profile, id='WU-003')]), env=self.menv, ok=False)
        self.build('dispatch', data={'id': 'denied-dependent', 'profiles': [dict(profile, id='WU-003')]}, env=self.menv, ok=False)
        self.assertFalse((self.mate / 'data/product-build/actions/build-one/denied-dependent.json').exists())

    def test_integrator_must_be_available_before_dependency_receipts(self):
        self.contract['work'] = [dict(self.contract['work'][0], id=uid, task=task, level='slice', depends=deps)
                                 for uid, task, deps in [('WU-001', 'task-one', []), ('WU-002', 'task-two', ['WU-001'])]]
        self.start(); self.clone_project(); self.prepare_units()
        self.build('prepare', data=dict(self.plan, integration_unit='WU-002'), env=self.menv, ok=False)

    def test_map_attempt_history_observes_dirty_content_and_preserves_failures(self):
        self.start(); destination = self.clone_project(); self.prepare_units()
        first = self.build('refresh', data={'id': 'first-map', 'stage': 'final'}, env=self.menv)
        self.assertIsNotNone(first['debt'])
        self.assertTrue(first['retry']['new_attempt_required'])
        self.assertEqual(first, self.build('refresh', data={'id': 'first-map', 'stage': 'final'}, env=self.menv))
        (destination / 'app.py').write_text('def changed():\n    return 1\n')
        second = self.build('refresh', data={'id': 'dirty-map', 'stage': 'final'}, env=self.menv)
        (destination / 'app.py').write_text('def changed():\n    return 2\n')
        self.build('refresh', data={'id': 'dirty-map', 'stage': 'final'}, env=self.menv, ok=False)
        third = self.build('refresh', data={'id': 'retry-map', 'stage': 'final'}, env=self.menv)
        self.assertNotEqual(second['candidate']['inputs'], third['candidate']['inputs'])
        history = self.build('maps', env=self.menv)
        self.assertEqual(history['baseline']['outcome'], 'unavailable')
        self.assertEqual(len(history['attempts']), 4)  # automatic deferred start plus explicit attempts
        self.assertIn(first, history['attempts'])
        self.assertIn(second, history['attempts'])

    def test_finish_inventory_record_debt_exact_change_set_and_owner_question(self):
        self.start(); destination = self.clone_project(); self.prepare_units()
        old_home, old_env = self.home, self.env
        self.home, self.env = self.mate, self.menv
        try:
            head = self.git(destination, 'rev-parse', 'HEAD')
            self.evidence(head=head)
            relative = self.last_artifacts['AC-001']
            raw = (self.mate / 'data/product-contract/sample/artifacts' / relative).read_bytes()
            ref = {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()}
            request = {'id': 'variance-one', 'class': 'technical', 'unit': 'WU-001', 'reason': 'Equivalent private path',
                       'requirements': self.contract['work'][0]['requirements'], 'before': head + ':old-path',
                       'after': head + ':new-path', 'evidence': [ref], 'supersedes': []}
            first = self.build('change', data=request, env=self.menv)
            second = self.build('change', data=dict(request, id='variance-two', supersedes=['variance-one']), env=self.menv)
            self.assertEqual(first, self.build('change', data=request, env=self.menv))
            completion = {'id': 'complete-one', 'debts': [{'id': 'projection', 'artifact': 'as-built specification',
                           'reason': 'Private technical projection pending', 'followup': 'Dedicated secondmate reconciliation'}]}
            outcome = self.build('finish', data=completion, env=self.menv)
            self.assertEqual(outcome['product']['outcome'], 'product-accepted')
            self.assertEqual(outcome['product']['verified_acceptance'], ['AC-001', 'AC-002'])
            self.assertEqual(set(outcome['inventory']), {'REQ-001', 'REQ-002'})
            self.assertEqual(outcome['delivery'], 'not-delivered')
            self.assertEqual(outcome['publication']['outcome'], 'pending')
            self.assertEqual(outcome['publication']['receipt']['receipts'], [])
            self.assertEqual(outcome['publication']['receipt']['next'], 0)
            self.assertEqual([row['id'] for row in outcome['changes']], ['variance-one', 'variance-two'])
            self.assertEqual(outcome['changes'][1]['supersedes'], ['variance-one'])
            self.assertEqual({row['change'] for row in outcome['change_verification']}, {'variance-one', 'variance-two'})
            for row in outcome['change_verification']:
                self.assertEqual(row['after_head'], head)
                self.assertEqual(row['outcome'], 'verified-at-final-candidate')
                self.assertEqual(row['record_debts'], ['projection'])
                self.assertTrue(row['results'])
            self.assertEqual(outcome, self.build('finish', data=completion, env=self.menv))
            question = self.build('change', data=dict(request, id='scope-change', **{'class': 'semantic'}), env=self.menv)
            self.assertEqual(question['authority'], 'owner-required')
            self.assertEqual(question['action']['outcome'], 'recorded')
            status = old_home / 'state/builder.status'
            self.assertIn('scope-change', status.read_text())
            before = status.read_bytes()
            self.build('change', data=dict(request, id='scope-change', **{'class': 'semantic'}), env=self.menv)
            self.assertEqual(status.read_bytes(), before)
            self.assertEqual(self.build('wave', env=self.menv)['units'][0]['state'], 'waiting')
            denied = self.build('finish', data=dict(completion, id='pending-scope'), env=self.menv)
            self.assertEqual(denied['product'], 'not-accepted')
            self.assertIn('owner input pending', denied['failures'][0])
            self.assertEqual(second['digest'], first['digest'])
        finally:
            self.home, self.env = old_home, old_env

    def test_real_failed_oracle_generates_in_scope_repair_without_owner_question(self):
        self.start(); destination = self.clone_project(); self.prepare_units()
        old_home, old_env = self.home, self.env
        self.home, self.env = self.mate, self.menv
        try:
            head = self.git(destination, 'rev-parse', 'HEAD')
            self.evidence(head=head)
            target = self.home / 'fixture-product.py'
            target.write_text(target.read_text().replace("'name': 'retained'", "'name': 'lost'"))
            self.evidence(head=head)
            outcome = self.build('finish', data={'id': 'failed-behavior', 'debts': []}, env=self.menv)
            self.assertEqual(outcome['product'], 'not-accepted')
            self.assertEqual(len(outcome['repair_changes']), 1)
            self.assertEqual(outcome['changes'][0]['class'], 'defect')
            self.assertEqual(outcome['owner_questions'], [])
            self.assertEqual(outcome['repair_actions'][0]['outcome'], 'blocked')  # no live worker allocated in this portable case
            self.assertFalse((old_home / 'state/builder.status').exists())
            target.write_text(target.read_text().replace("'name': 'lost'", "'name': 'retained'"))
            self.evidence(head=head)
            repaired = self.build('finish', data={'id': 'repaired-behavior', 'debts': []}, env=self.menv)
            self.assertEqual(repaired['product']['outcome'], 'product-accepted')
            self.assertEqual(repaired['repair_changes'], [])
            self.assertEqual(len(repaired['changes']), 1)  # failed history is never hidden by later passing proof
        finally:
            self.home, self.env = old_home, old_env

    def successor_approval(self):
        words = 'Never contact leads, including test messages.'
        self.event('EV-004', 'answer', words, question='Q-001', round='ROUND-002', supersedes='EV-002', revision=2)
        self.contract['revision'] = 2
        self.contract['sources'].append('EV-004'); self.contract['intent'].append('EV-004')
        self.contract['clauses'][1]['state'] = 'superseded'; self.contract['requirements'][1]['state'] = 'superseded'
        self.contract['clauses'].append({'id': 'CL-003', 'event': 'EV-004', 'start': 0, 'end': len(words), 'text': words,
            'normative': True, 'state': 'accepted', 'requirements': ['REQ-003'], 'reason': 'Exact owner clarification'})
        self.contract['requirements'].append({'id': 'REQ-003', 'text': words, 'state': 'accepted', 'clauses': ['CL-003'],
            'technical_decision': None, 'acceptance': ['AC-002'], 'supersedes': ['REQ-002']})
        self.contract['acceptance'][1]['requirements'].append('REQ-003')
        self.contract['work'][0]['requirements'].append('REQ-003')
        return self.approve(eventid='EV-005'), words

    def revision_budget(self):
        self.contract['budgets'] = [{'id': 'BUD-001', 'provider': 'fixture', 'operation': 'read', 'tenant': 'private-fixture',
            'credential_ref': 'reference-only', 'requests': 2, 'amount_minor': 0, 'currency': 'USD',
            'expires': '2099-01-01T00:00:00+00:00', 'authority': 'EV-002', 'data': 'synthetic fixture only',
            'cleanup': 'read-only', 'idempotency': 'unique key', 'concurrency': 1}]

    def test_approved_semantic_successor_preserves_lifetime_proof_and_budget_history(self):
        self.revision_budget(); self.start(); destination = self.clone_project(); self.prepare_units()
        initial = self.build('verify')
        owner_path = self.mate / '.fm-product-build.json'
        owner_bytes = owner_path.read_bytes()
        artifact_path = self.home / 'data/product-build/artifacts/build-one.json'
        original_bytes = artifact_path.read_bytes()
        old_home, old_env = self.home, self.env
        self.home, self.env = self.mate, self.menv
        request = {'id': 'BUD-001', 'requests': 1, 'amount_minor': 0, 'operation_key': 'reserved-before-revision'}
        try:
            head = self.git(destination, 'rev-parse', 'HEAD')
            self.evidence(head=head)
            self.call('budget', data=request)
            retained = self.call('inspect')
            path = self.last_artifacts['AC-001']
            raw = (self.mate / 'data/product-contract/sample/artifacts' / path).read_bytes()
            ref = {'path': path, 'sha256': hashlib.sha256(raw).hexdigest()}
            self.build('change', data={'id': 'scope-question', 'class': 'semantic', 'unit': 'WU-001', 'reason': 'Clarify test-message exclusion',
                'requirements': ['REQ-002'], 'before': 'Original denial', 'after': 'Await exact owner words', 'evidence': [ref], 'supersedes': []}, env=self.menv)
        finally:
            self.home, self.env = old_home, old_env
        self.assertEqual(self.call('inspect')['budget_usage'], retained['budget_usage'])
        self.assertIn('unresolved', self.call('budget', data=dict(request, operation_key='parent-cannot-bypass-custody'), ok=False))
        digest, words = self.successor_approval()
        self.build('revise', data={'digest': digest, 'previous': initial['digest'], 'resolutions': [{'change': 'scope-question', 'event': 'EV-005'}]}, ok=False)
        published = self.build('revise', data={'digest': digest, 'previous': initial['digest'], 'resolutions': [{'change': 'scope-question', 'event': 'EV-004'}]})
        self.build('show', env=self.menv, ok=False)
        imported = self.build('import-revision', data={'digest': digest}, env=self.menv)
        self.assertEqual(imported['outcome'], 'approved-revision-imported')
        self.assertEqual(imported['transition']['phase'], 'complete')
        self.assertEqual(imported, self.build('import-revision', data={'digest': digest}, env=self.menv))
        self.assertEqual(owner_path.read_bytes(), owner_bytes)
        self.assertEqual(artifact_path.read_bytes(), original_bytes)
        self.assertEqual(self.build('show', env=self.menv)['artifact'], published['artifact'])
        self.assertEqual(self.build('show', env=self.menv)['execution'], 'not-started')
        self.assertTrue((self.mate / 'data/product-build/execution-history/build-one' / (initial['hash'] + '.json')).exists())
        self.assertIn('resolved ', (self.home / 'state/builder.status').read_text())
        self.home, self.env = self.mate, self.menv
        try:
            current = self.call('inspect')
            self.assertEqual(current['results'], retained['results'])
            self.assertEqual(current['budget_usage'], retained['budget_usage'])
            self.call('check-task', 'task-one', ok=False)
            brief = self.home / 'data/task-one/brief.md'
            brief.write_text(brief.read_text().replace('\n\n## Firstmate spec', '\n\n' + words + '\n\n## Firstmate spec'))
            self.call('bind', data={'task': 'task-one', 'unit': 'WU-001'})
            self.build('prepare', data=self.plan, env=self.menv)
            self.assertEqual(self.build('wave', env=self.menv)['units'][0]['state'], 'ready')
            self.call('budget', data=request, ok=False)
            self.call('budget-finish', data={'id': 'BUD-001', 'operation_key': request['operation_key'], 'observed': 'Synthetic reservation reconciled; no provider call performed'})
            second = dict(request, operation_key='reserved-after-revision')
            self.call('budget', data=second)
            self.call('budget-finish', data={'id': 'BUD-001', 'operation_key': second['operation_key'], 'observed': 'Synthetic reservation reconciled; no provider call performed'})
            self.assertIn('ceiling', self.call('budget', data=dict(request, operation_key='must-not-reset'), ok=False))
            before_proof = self.build('finish', data={'id': 'new-revision-missing-proof', 'debts': []}, env=self.menv)
            self.assertEqual(before_proof['product'], 'not-accepted')
            self.evidence(head=head)
            after = self.build('finish', data={'id': 'new-revision-verified', 'debts': []}, env=self.menv)
            self.assertEqual(after['product']['outcome'], 'product-accepted')
            self.assertEqual(after['product']['verified_acceptance'], ['AC-001', 'AC-002'])
            self.assertEqual(set(after['inventory']), {'REQ-001', 'REQ-003'})
            self.assertEqual(after['unresolved_owner_questions'], [])
            self.assertEqual(after['owner_questions'][0]['resolved_by_owner_event'], 'EV-004')
        finally:
            self.home, self.env = old_home, old_env

    def test_parent_budget_custody_settles_prior_operation_during_semantic_pause(self):
        self.revision_budget(); self.start(); self.clone_project(); self.prepare_units()
        old_env = self.env
        request = {'id': 'BUD-001', 'requests': 1, 'amount_minor': 0, 'operation_key': 'prior-operation'}
        self.env = self.menv
        try:
            reserved = self.call('budget', data=request)
            self.assertEqual(reserved['operation_key'], request['operation_key'])
            self.assertEqual(reserved['custody_operation_key'], 'build-one:prior-operation')
        finally:
            self.env = old_env
        self.event('EV-004', 'decision', 'A proposed semantic change is awaiting independent audit and approval.', revision=2)
        self.env = self.menv
        try:
            self.call('budget', data=dict(request, operation_key='unauthorized-new-operation'), ok=False)
            settled = self.call('budget-finish', data={'id': 'BUD-001', 'operation_key': request['operation_key'],
                'observed': 'Prior synthetic reservation reconciled; no provider call was performed'})
            self.assertIn('pending', settled['projection'])
        finally:
            self.env = old_env
        usage = self.call('inspect')['budget_usage']
        self.assertEqual(len(usage), 2)
        self.assertEqual(usage[0]['requests'], 1)
        self.assertEqual(usage[1]['operation_key'], 'build-one:prior-operation')
        self.assertIn('no provider call', usage[1]['observed'])

    def test_implementation_worker_cannot_answer_its_own_authority_finding(self):
        self.start(); self.clone_project(); self.prepare_units()
        worker_env = dict(self.menv, FM_TASK_ID='task-one')
        refusal = self.build('change', data={'id': 'self-answer'}, env=worker_env, ok=False)
        self.assertIn('implementation worker cannot classify its own authority finding', refusal)
        self.assertFalse((self.mate / 'data/product-build/changes/build-one/self-answer.json').exists())
        self.assertFalse((self.home / 'state/builder.status').exists())
        self.build('finish', data={'id': 'self-accept', 'debts': []}, env=worker_env, ok=False)
        self.assertFalse((self.mate / 'data/product-build/final/build-one/self-accept').exists())
        (self.mate / 'state').mkdir(exist_ok=True)
        (self.mate / 'state/old-unrelated.meta').write_text('kind=ship\n')
        self.build('change', data={'id': 'unrelated-self-answer'}, env=dict(self.menv, FM_TASK_ID='old-unrelated'), ok=False)

    def test_existing_seeder_requires_dedicated_permission_for_local_only(self):
        self.start()
        self.git(self.project, 'init', '-b', 'main')
        self.git(self.project, 'add', '.')
        self.git(self.project, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Initial fixture')
        (self.home / 'data/projects.md').write_text('- app [local-only] - fixture (added 2026-11-08)\n')
        (self.mate / 'AGENTS.md').write_text('# Fixture home\n')
        (self.mate / 'bin').mkdir()
        arguments = ['bash', str(ROOT / 'bin/fm-home-seed.sh'), 'builder', str(self.mate), 'app']
        env = dict(self.env, FM_SECONDMATE_CHARTER='One approved build', FM_SECONDMATE_SCOPE='One approved build')
        refused = subprocess.run(arguments, env=env, text=True, capture_output=True)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertFalse((self.mate / 'projects/app').exists())
        allowed = subprocess.run(arguments, env=dict(env, FM_PRODUCT_BUILD='build-one'), text=True, capture_output=True)
        self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
        destination = self.mate / 'projects/app'
        self.assertEqual((destination / 'app.py').read_bytes(), (self.project / 'app.py').read_bytes())
        self.assertTrue((destination / '.git/fm-product-build.json').is_file())
        self.assertEqual(self.git(self.project, 'remote'), '')
        repeated = subprocess.run(arguments, env=dict(env, FM_PRODUCT_BUILD='build-one'), text=True, capture_output=True)
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)

    def test_dedicated_parent_landing_and_nested_seed_refuse_before_mutation(self):
        self.start()
        (self.mate / 'state').mkdir()
        for script, arguments in [('fm-merge-local.sh', ['task-one']),
                                  ('fm-home-seed.sh', ['nested', str(self.mate / 'nested'), '--no-projects'])]:
            proc = subprocess.run(['bash', str(ROOT / 'bin' / script), *arguments],
                                  env=self.menv, text=True, capture_output=True)
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn('dedicated build', proc.stderr)
        self.assertFalse((self.mate / 'nested').exists())
        self.assertFalse((self.mate / 'state/task-one.meta').exists())

    def test_racing_reservations_and_conflicting_roster(self):
        self.approve(); self.seed(); self.emit(); self.emit('build-two')
        command = [sys.executable, str(ROOT / 'bin/fm-product-build.py'), 'reserve']
        body = json.dumps({'mate': 'builder', 'home': str(self.mate)})
        jobs = [subprocess.Popen(command + [bid], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=self.env) for bid in ('build-one', 'build-two')]
        replies = [job.communicate(body) for job in jobs]
        self.assertEqual(sorted(job.returncode for job in jobs), [0, 1], replies)
        self.assertEqual(len(json.loads((self.home / 'data/product-build/owners.json').read_text())['owners']), 1)
        (self.home / 'data/secondmates.md').write_text('')
        self.build('reserve', data={'mate': 'builder', 'home': str(self.mate)}, ok=False)


if __name__ == '__main__':
    unittest.main()
