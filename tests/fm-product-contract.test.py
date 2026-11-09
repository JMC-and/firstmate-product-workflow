"""Behavioral contract replay through the supported CLI, never source assertions."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(sys.argv.pop(1))
CLI = ROOT / 'bin/fm-product-contract.sh'
HEAD = 'a' * 40


class ProductContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.env = {k: v for k, v in os.environ.items() if k not in {'FM_STATE_OVERRIDE', 'FM_DATA_OVERRIDE'}}
        self.env['FM_HOME'] = str(self.home)
        self.call('new', 'sample')
        self.event('EV-001', 'question', 'Which path?', role='firstmate', question='Q-001')
        self.event('EV-002', 'answer', 'Save via real CLI. Never contact leads.', question='Q-001')
        self.contract = {
            'schema': 1, 'id': 'sample', 'revision': 1, 'profile': 'compact',
            'author': 'planner', 'summary': 'Persist records without outreach.',
            'intent': ['EV-002'], 'sources': ['EV-001', 'EV-002'],
            'clauses': [
                {'id': 'CL-001', 'event': 'EV-002', 'start': 0, 'end': 18,
                 'text': 'Save via real CLI.', 'normative': True, 'state': 'accepted',
                 'requirements': ['REQ-001'], 'reason': 'Required path'},
                {'id': 'CL-002', 'event': 'EV-002', 'start': 18, 'end': 39,
                 'text': ' Never contact leads.', 'normative': True, 'state': 'accepted',
                 'requirements': ['REQ-002'], 'reason': 'Safety constraint'}],
            'requirements': [
                {'id': 'REQ-001', 'text': 'Save via real CLI.', 'state': 'accepted',
                 'clauses': ['CL-001'], 'technical_decision': None,
                 'acceptance': ['AC-001'], 'supersedes': []},
                {'id': 'REQ-002', 'text': 'Never contact leads.', 'state': 'accepted',
                 'clauses': ['CL-002'], 'technical_decision': None,
                 'acceptance': ['AC-002'], 'supersedes': []}],
            'acceptance': [
                {'id': 'AC-001', 'requirements': ['REQ-001'], 'given': 'Authorized operator',
                 'when': 'Save and reload using CLI', 'then': 'Record persists',
                 'oracle': 'Reload returns the identical record', 'kinds': ['cli', 'persistence'],
                 'environment': 'local-real', 'modes': ['real'], 'journeys': ['J-001'],
                 'code': ['cli/save'], 'tests': ['cli-roundtrip'], 'freshness_seconds': 3600},
                {'id': 'AC-002', 'requirements': ['REQ-002'], 'given': 'Unapproved lead',
                 'when': 'Attempt outbound contact', 'then': 'No send',
                 'oracle': 'Outbound ledger contains zero calls', 'kinds': ['negative'],
                 'environment': 'local-real', 'modes': ['real'], 'journeys': ['J-002'],
                 'code': ['cli/send'], 'tests': ['no-outreach'], 'freshness_seconds': 3600}],
            'journeys': [
                {'id': 'J-001', 'class': 'golden', 'steps': ['Save', 'Reload'],
                 'acceptance': ['AC-001'], 'environment': 'local-real'},
                {'id': 'J-002', 'class': 'denied', 'steps': ['Attempt send', 'Read ledger'],
                 'acceptance': ['AC-002'], 'environment': 'local-real'}],
            'journey_na': {'alternate': 'No alternate route', 'failure': 'Denial is the failure path',
                           'recovery': 'No mutable external operation'},
            'work': [{'id': 'WU-001', 'task': 'task-one', 'requirements': ['REQ-001', 'REQ-002'],
                      'journeys': ['J-001', 'J-002'], 'depends': [], 'level': 'product',
                      'instructions': 'Build real persistence and deny outreach.'}],
            'boundaries': [{'id': 'ENV-001', 'name': 'local-real', 'description': 'Real local CLI and disk',
                            'browser_na': 'CLI-only product'}],
            'budgets': [], 'conflicts': [], 'decisions': [],
            'completion': {'slice': 'Assigned criteria verified', 'product': 'All criteria at integrated head',
                           'delivery': 'Accepted head landed with existing authority'},
            'worker_instructions': 'Implement accepted rows only.',
            'acceptance_instructions': 'Use actual CLI and disk, then inspect outbound ledger.'}

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, cmd, ident='sample', data=None, ok=True, extra=()):
        ident = getattr(self, 'cid', 'sample') if ident == 'sample' else ident
        proc = subprocess.run([str(CLI), cmd, ident, *extra], env=self.env,
                              input=json.dumps(data, ensure_ascii=False) if data is not None else '',
                              text=True, capture_output=True)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            return json.loads(proc.stdout) if proc.stdout.strip().startswith(('{', '[')) else proc.stdout
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn('product-contract:', proc.stderr)
        return proc.stderr

    def event(self, ident, kind, text, role='owner', question=None, supersedes=None, **kw):
        return self.call('append', data={'id': ident, 'kind': kind, 'role': role,
                         'text': text, 'question': question, 'round': 'ROUND-001' if question else None,
                         'source_event': 'chat:' + ident, 'supersedes': supersedes,
                         'revision': kw.pop('revision', 1), 'completeness': 'complete',
                         'expected_bytes': len(text.encode()), 'expected_sha256': hashlib.sha256(text.encode()).hexdigest(), **kw})

    def draft(self, contract=None):
        return self.call('draft', data=contract or self.contract)

    def approve(self, eventid='EV-003'):
        self.draft()
        audit = {'reviewer': 'reverse-auditor', 'independent': True,
                 'contract_hash': self.call('inspect')['draft_hash'],
                 'source_ids': self.contract['sources'], 'clause_ids': [c['id'] for c in self.contract['clauses']],
                 'checks': {k: True for k in ['omission', 'weakening', 'negation', 'exceptions',
                                            'contradictions', 'invention', 'unresolved']}, 'findings': []}
        result = self.call('audit', data=audit)
        self.event(eventid, 'approval', 'Approve this exact contract.', manifest=result['digest'], revision=self.contract['revision'])
        self.call('approve', data={'event': eventid, 'digest': result['digest']})
        return result['digest']

    def bind(self):
        brief = self.home / 'data/task-one/brief.md'
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text('# Task\n## Captain\'s intent\nSave via real CLI. Never contact leads.\n\n## Firstmate spec\nBuild accepted paths.\n\n# Setup\nProduct contract: required\n')
        result = self.call('bind', data={'task': 'task-one', 'unit': 'WU-001'})
        (brief.parent / 'launch-brief.md').write_bytes(brief.read_bytes())
        return result

    def evidence(self, mode='real', implementation='implemented', head=HEAD):
        # A tiny real local public-interface target, never a live Leads service.
        # Proof is captured from separate CLI processes and persisted disk state.
        program = self.home / 'fixture-product.py'
        if not program.exists():
            program.write_text('''import json, os, sys
from pathlib import Path
record = Path(sys.argv[1])
action = sys.argv[2]
if os.environ.get('FIXTURE_DISABLED') == '1':
    print('required behavior disabled'); sys.exit(3)
log = record.parent / 'outbound-ledger.json'
events = json.loads(log.read_text()) if log.exists() else []
if action == 'save':
    record.write_text(json.dumps({'name': 'retained'})); print('saved')
elif action == 'reload':
    print(record.read_text())
elif action == 'send':
    events.append({'operation': 'send', 'result': 'denied'})
    log.write_text(json.dumps(events)); print('denied'); sys.exit(2)
elif action == 'audit':
    print(json.dumps({'outbound_calls': sum(e['result'] == 'sent' for e in events), 'denials': len(events)}))
else:
    sys.exit(4)
''')
        target = program if implementation != 'missing' else self.home / 'absent-product.py'
        env = dict(self.env, FIXTURE_DISABLED='1' if implementation == 'disabled' else '0')
        self.last_artifacts = {}
        for ac, kinds, journey, actions in [('AC-001', ['cli', 'persistence'], 'J-001', ['save', 'reload']),
                                            ('AC-002', ['negative'], 'J-002', ['send', 'audit'])]:
            observations = []
            for action in actions:
                argv = [sys.executable, str(target), str(self.home / 'record.json'), action]
                proc = subprocess.run(argv, env=env, capture_output=True, text=True)
                observations.append({'argv': argv, 'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})
            try:
                last = json.loads(observations[-1]['stdout'])
                passed = (last == {'name': 'retained'} and observations[0]['exit'] == 0) if ac == 'AC-001' else (last['outbound_calls'] == 0 and last['denials'] >= 1 and observations[0]['exit'] == 2)
            except (ValueError, KeyError, TypeError):
                passed = False
            sequence = str(len(self.call('inspect')['results']))
            artifact = 'proof-' + ac + '-' + sequence + '.json'
            payload = json.dumps({'reported_unit_tests': 423, 'actual_public_interface_observations': observations})
            self.call('artifact', data={'path': artifact, 'text': payload})
            self.last_artifacts[ac] = artifact
            self.call('result', data={'id': 'RES-' + ac + '-' + sequence, 'acceptance': ac, 'head': head,
                'environment': 'local-real', 'mode': mode, 'kinds': kinds,
                'implementation': implementation, 'reachable': target.exists(), 'enabled': implementation != 'disabled',
                'journeys': [journey], 'result': 'passed' if passed else 'failed', 'observed': payload,
                'oracle': next(x['oracle'] for x in self.contract['acceptance'] if x['id'] == ac),
                'actor': 'acceptance-runner', 'code': [str(target) + '@' + head],
                'tests': actions, 'flags': hashlib.sha256(env['FIXTURE_DISABLED'].encode()).hexdigest(),
                'limitations': [], 'findings': [], 'artifacts': [{'path': artifact,
                'sha256': hashlib.sha256(payload.encode()).hexdigest()}]})

    def test_compact_end_to_end(self):
        self.approve(); self.bind()
        self.call('check-task', 'task-one')
        self.evidence()
        result = self.call('evaluate', data={'head': HEAD, 'level': 'product'})
        self.assertEqual(result['outcome'], 'product-accepted')
        self.call('check-task', 'task-one', extra=('--phase', 'ready', '--head', HEAD))
        self.call('evaluate', data={'head': HEAD, 'level': 'delivery'}, ok=False)

    def test_supplied_questionnaire_original_and_extracted_answers_are_both_preserved(self):
        original = 'Q-001: Which path?\r\nA: Save via real CLI. Never contact leads.\r\n'
        capture = [str(CLI), 'capture', 'sample', '--event-id', 'EV-004', '--source-event', 'upload:original',
                   '--expected-bytes', str(len(original.encode())), '--expected-sha256', hashlib.sha256(original.encode()).hexdigest()]
        truncated = subprocess.run(capture, input=original[:-2].encode(), env=self.env, capture_output=True)
        self.assertNotEqual(truncated.returncode, 0)
        self.assertIn(b'EV-004: original capture length/hash mismatch', truncated.stderr)
        imported = subprocess.run(capture, input=original.encode(), env=self.env, capture_output=True)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        repeated = subprocess.run(capture, input=original.encode(), env=self.env, capture_output=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)['outcome'], 'already-recorded')
        self.contract['sources'].append('EV-004')
        start = original.index('Save')
        middle = start + len('Save via real CLI.')
        self.contract['clauses'] += [
            {'id': 'CL-003', 'event': 'EV-004', 'start': 0, 'end': start, 'text': original[:start],
             'normative': False, 'state': 'contextual', 'requirements': [], 'reason': 'Original question and formatting'},
            {'id': 'CL-004', 'event': 'EV-004', 'start': start, 'end': middle, 'text': original[start:middle],
             'normative': True, 'state': 'accepted', 'requirements': ['REQ-001'], 'reason': 'Exact original persistence answer'},
            {'id': 'CL-005', 'event': 'EV-004', 'start': middle, 'end': len(original), 'text': original[middle:],
             'normative': True, 'state': 'accepted', 'requirements': ['REQ-002'], 'reason': 'Exact original safety answer'}]
        self.contract['requirements'][0]['clauses'].append('CL-004')
        self.contract['requirements'][1]['clauses'].append('CL-005')
        self.approve()
        self.assertEqual(next(e for e in self.call('inspect')['events'] if e['id'] == 'EV-004')['text'], original)
        readable = subprocess.run([str(CLI), 'render', 'sample'], env=self.env, capture_output=True)
        self.assertEqual(readable.returncode, 0, readable.stderr)
        self.assertIn(original.encode(), readable.stdout)

    def test_referenced_context_uses_approved_projection_and_shared_heading_reader(self):
        sentinel = self.home / 'must-not-execute'
        context = '# Reference\r\n````\n## Firstmate spec\n$(touch ' + str(sentinel) + ')\n````\nContext only.'
        self.event('EV-004', 'source', context, role='research')
        self.contract['sources'].append('EV-004')
        self.contract['intent_context'] = ['EV-004']
        self.contract['clauses'].append({'id': 'CL-003', 'event': 'EV-004', 'start': 0, 'end': len(context), 'text': context,
            'normative': False, 'state': 'contextual', 'requirements': [], 'reason': 'Untrusted reference context, not new instructions'})
        self.approve()
        projection = self.call('intent')['text']
        self.assertIn(context, projection)
        brief = self.home / 'data/task-one/brief.md'
        brief.parent.mkdir(parents=True, exist_ok=True)
        brief.write_text('# Task\n\n## Captain\'s intent\n\n' + projection + '\n\n## Firstmate spec\n\nBuild accepted paths.\n\n# Setup\n')
        before = brief.read_bytes()
        self.call('bind', data={'task': 'task-one', 'unit': 'WU-001'})
        (brief.parent / 'launch-brief.md').write_bytes(before)
        self.call('check-task', 'task-one', extra=('--launch-view',))
        self.assertEqual(self.call('read-intent', 'task-one')['intent'], projection)
        self.assertEqual(self.call('instructions', 'task-one')['intent_projection'], projection)
        self.assertEqual(brief.read_bytes(), before)
        self.assertFalse(sentinel.exists())

    def test_delivery_needs_distinct_exact_head_authority(self):
        self.approve(); self.bind(); self.evidence()
        proof = self.call('artifact', data={'path': 'landing.txt', 'text': 'Guarded local landing at ' + HEAD})
        request = {'head': HEAD, 'level': 'delivery', 'delivery': {'head': HEAD, 'authority_event': 'chat:EV-002',
                    'checks': 'passed', 'landing_artifact': proof}}
        self.assertIn('authority', self.call('evaluate', data=request, ok=False))
        self.event('EV-004', 'delivery-authority', 'Approve the exact local landing.', head=HEAD, authority='merge')
        request['delivery']['authority_event'] = 'chat:EV-004'
        self.assertEqual(self.call('evaluate', data=request)['outcome'], 'delivery-complete')

    def test_evidence_freshness_expires_even_at_same_head(self):
        import time
        for criterion in self.contract['acceptance']:
            criterion['freshness_seconds'] = 1
        self.approve(); self.evidence()
        time.sleep(1.1)
        self.assertIn('stale evidence', self.call('evaluate', data={'head': HEAD, 'level': 'product'}, ok=False))

    def test_missing_clause_mutations(self):
        for index in [0, 1]:
            mutant = copy.deepcopy(self.contract)
            mutant['clauses'].pop(index)
            error = self.call('draft', data=mutant, ok=False)
            self.assertIn('EV-002', error)

    def test_weakening_audit_cannot_certify_edits(self):
        self.contract['requirements'][1]['text'] = 'Contact leads except for tests.'
        self.assertIn('REQ-002', self.call('draft', data=self.contract, ok=False))
        self.contract['requirements'][1]['text'] = 'Never contact leads.'
        self.draft()
        report = {'reviewer': 'planner', 'independent': True, 'contract_hash': 'a' * 64,
                  'source_ids': ['EV-001', 'EV-002'], 'clause_ids': ['CL-001', 'CL-002'],
                  'checks': {}, 'findings': []}
        self.call('audit', data=report, ok=False)
        self.call('approve', data={'event': 'EV-002', 'digest': 'a' * 64}, ok=False)

    def test_unsupported_invention(self):
        self.contract['requirements'][0]['clauses'] = []
        self.assertIn('REQ-001', self.call('draft', data=self.contract, ok=False))

    def test_literal_source_markers_are_not_unfinished_derived_fields(self):
        original = 'Persist supplied text, including the literal token {TASK}.'
        self.event('EV-004', 'source', original)
        self.contract['sources'].append('EV-004')
        self.contract['clauses'].append({'id': 'CL-003', 'event': 'EV-004', 'start': 0, 'end': len(original), 'text': original,
            'normative': True, 'state': 'accepted', 'requirements': ['REQ-003'], 'reason': 'Exact literal payload guarantee'})
        self.contract['requirements'].append({'id': 'REQ-003', 'text': original, 'state': 'accepted', 'clauses': ['CL-003'],
            'technical_decision': None, 'acceptance': ['AC-001'], 'supersedes': []})
        self.contract['acceptance'][0]['requirements'].append('REQ-003')
        self.contract['acceptance'][0]['oracle'] = 'Reload preserves the supplied literal TODO text exactly.'
        self.contract['work'][0]['requirements'].append('REQ-003')
        self.call('draft', data=self.contract)
        self.contract['worker_instructions'] = '{TASK}'
        self.call('draft', data=self.contract, ok=False)

    def test_placeholders_or_unknowns(self):
        for field in ['oracle', 'then', 'given']:
            mutant = copy.deepcopy(self.contract)
            mutant['acceptance'][0][field] = 'TBD'
            self.assertIn('AC-001', self.call('draft', data=mutant, ok=False))
        self.event('EV-004', 'question', 'How should synchronization work?', role='firstmate', question='Q-002')
        self.event('EV-005', 'answer', "I don't know", question='Q-002')
        self.contract['sources'] += ['EV-004', 'EV-005']
        self.assertIn('EV-005: unknown answer', self.call('draft', data=self.contract, ok=False))

    def test_conflicts_and_unresolved(self):
        self.contract['conflicts'] = [{'id': 'CON-001', 'state': 'unresolved'}]
        self.assertIn('CON-001', self.call('draft', data=self.contract, ok=False))

    def test_conflicting_answers_without_correction_are_not_defaults(self):
        words = 'Contact leads freely.'
        self.event('EV-004', 'answer', words, question='Q-001')
        self.contract['sources'].append('EV-004')
        self.contract['clauses'].append({'id': 'CL-003', 'event': 'EV-004', 'start': 0, 'end': len(words),
            'text': words, 'normative': True, 'state': 'accepted', 'requirements': ['REQ-003'], 'reason': 'Later answer'})
        self.contract['requirements'].append({'id': 'REQ-003', 'text': words, 'state': 'accepted',
            'clauses': ['CL-003'], 'technical_decision': None, 'acceptance': ['AC-002'], 'supersedes': []})
        self.contract['acceptance'][1]['requirements'].append('REQ-003')
        self.contract['work'][0]['requirements'].append('REQ-003')
        self.assertIn('contradictory answers', self.call('draft', data=self.contract, ok=False))

    def test_dependency_frontier_does_not_ask_past_unanswered_question(self):
        self.event('EV-004', 'question', 'Choose the prerequisite.', role='firstmate', question='Q-002')
        self.event('EV-005', 'question', 'Choose its dependent behavior.', role='firstmate', question='Q-003', depends_on=['Q-002'])
        self.contract['sources'] += ['EV-004', 'EV-005']
        self.assertIn('EV-005: dependent question', self.call('draft', data=self.contract, ok=False))

    def test_exact_transcript_and_later_correction(self):
        text = 'Не удалять. 不要发送。\n\n  exact whitespace\n'
        self.event('EV-004', 'source', text)
        self.event('EV-005', 'decision', 'Correct previous answer', supersedes='EV-002')
        record = self.call('inspect')
        self.assertEqual(record['events'][2]['text'], text)
        self.assertEqual(record['events'][1]['text'], 'Save via real CLI. Never contact leads.')
        self.assertIn(text, self.call('render'))

    def test_changed_approved_bytes(self):
        self.approve()
        record_path = self.home / 'data/product-contract/sample/record.json'
        record = json.loads(record_path.read_text())
        record['revisions'][0]['contract']['requirements'][0]['text'] = 'Different behavior'
        body = {k: v for k, v in record.items() if k != 'integrity'}
        record['integrity'] = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        record_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n')
        self.assertIn('approved manifest mismatch', self.call('inspect', ok=False))

    def test_revision_retains_old_guarantees_and_requires_rebinding(self):
        original_digest = self.approve(); self.bind()
        original = self.call('inspect')['revisions'][0]
        self.contract['revision'] = 2
        self.contract['clauses'][0]['state'] = 'superseded'
        self.contract['requirements'][0]['state'] = 'superseded'
        replacement_clause = dict(self.contract['clauses'][0], id='CL-003', state='accepted', requirements=['REQ-003'])
        replacement_requirement = dict(self.contract['requirements'][0], id='REQ-003', state='accepted', clauses=['CL-003'], supersedes=['REQ-001'])
        self.contract['clauses'].append(replacement_clause)
        self.contract['requirements'].append(replacement_requirement)
        self.contract['acceptance'][0]['requirements'].append('REQ-003')
        self.contract['work'][0]['requirements'].append('REQ-003')
        self.approve(eventid='EV-004')
        self.call('check-task', 'task-one', ok=False)
        self.call('bind', data={'task': 'task-one', 'unit': 'WU-001'})
        self.call('check-task', 'task-one')
        self.assertIn('changed during dispatch', self.call('check-task', 'task-one', extra=('--expected-digest', original_digest), ok=False))
        self.assertEqual(self.call('inspect')['revisions'][0], original)

    def test_later_round_partial_correction_preserves_other_obligations(self):
        self.approve(); self.bind()
        original = self.call('inspect')['revisions'][0]
        words = 'Never contact leads, including test messages.'
        self.event('EV-004', 'answer', words, question='Q-001', round='ROUND-002', supersedes='EV-002', revision=2)
        self.contract['revision'] = 2
        self.contract['sources'].append('EV-004')
        self.contract['intent'].append('EV-004')
        self.contract['clauses'][1]['state'] = 'superseded'
        self.contract['requirements'][1]['state'] = 'superseded'
        self.contract['clauses'].append({'id': 'CL-003', 'event': 'EV-004', 'start': 0, 'end': len(words),
            'text': words, 'normative': True, 'state': 'accepted', 'requirements': ['REQ-003'], 'reason': 'Clarified no-outreach rule only'})
        self.contract['requirements'].append({'id': 'REQ-003', 'text': words, 'state': 'accepted', 'clauses': ['CL-003'],
            'technical_decision': None, 'acceptance': ['AC-002'], 'supersedes': ['REQ-002']})
        self.contract['acceptance'][1]['requirements'].append('REQ-003')
        self.contract['work'][0]['requirements'].append('REQ-003')
        self.approve(eventid='EV-005')
        self.assertEqual(self.call('inspect')['revisions'][0], original)
        self.assertEqual(self.contract['requirements'][0]['state'], 'accepted')
        self.call('check-task', 'task-one', ok=False)
        brief = self.home / 'data/task-one/brief.md'
        brief.write_text(brief.read_text().replace('\n\n## Firstmate spec', '\n\n' + words + '\n\n## Firstmate spec'))
        self.call('bind', data={'task': 'task-one', 'unit': 'WU-001'})
        self.call('check-task', 'task-one')
        sources = self.call('instructions', 'task-one')['sources']
        self.assertEqual(next(e for e in sources if e['id'] == 'EV-002')['round'], 'ROUND-001')
        self.assertEqual(next(e for e in sources if e['id'] == 'EV-004')['round'], 'ROUND-002')

    def test_unknown_prerequisite_cannot_be_settled_retroactively(self):
        self.event('EV-004', 'question', 'Choose target.', role='firstmate', question='Q-002')
        self.event('EV-005', 'answer', "I don't know", question='Q-002')
        self.event('EV-006', 'question', 'Choose target-dependent behavior.', role='firstmate', question='Q-003', depends_on=['Q-002'])
        self.event('EV-007', 'decision', 'Use the local target.', supersedes='EV-005')
        self.contract['sources'] += ['EV-004', 'EV-005', 'EV-006', 'EV-007']
        self.assertIn('EV-006: dependent question', self.call('draft', data=self.contract, ok=False))

    def test_new_answer_revokes_dispatch(self):
        self.approve(); self.bind()
        self.event('EV-004', 'decision', 'Also require export', revision=2)
        self.call('check-task', 'task-one', ok=False)

    def test_lost_binding_cannot_downgrade_to_legacy(self):
        self.approve()
        brief = self.home / 'data/task-one/brief.md'; brief.parent.mkdir(parents=True)
        brief.write_text("# Task\n## Captain's intent\nSave via real CLI. Never contact leads.\n\n## Firstmate spec\nPreserve authored instructions.\n")
        original = brief.read_bytes()
        self.call('bind', data={'task': 'task-one', 'unit': 'WU-001'})
        self.assertEqual(brief.read_bytes(), original)
        state = self.home / 'state'; state.mkdir()
        (state / 'task-one.meta').write_text('kind=ship\n')
        (self.home / 'data/task-one/product-contract.json').unlink()
        self.call('check-task', 'task-one', ok=False)

    def test_launch_snapshot_cannot_diverge_from_valid_binding(self):
        self.approve(); self.bind()
        self.call('check-task', 'task-one', extra=('--launch-view',))
        launch = self.home / 'data/task-one/launch-brief.md'
        launch.write_text(launch.read_text().replace('Build accepted paths.', 'Deliver an obsolete product instead.'))
        self.assertIn('launch view', self.call('check-task', 'task-one', extra=('--launch-view',), ok=False))

    def test_changed_task_intent(self):
        self.approve(); self.bind()
        path = self.home / 'data/task-one/brief.md'
        path.write_text(path.read_text().replace('Build accepted paths.', 'Also send mail.'))
        self.call('check-task', 'task-one', ok=False)

    def test_leads_failure_replay(self):
        self.approve(); self.bind()
        for mode, implementation in [('synthetic', 'implemented'), ('real', 'disabled'), ('real', 'missing')]:
            # Append unique IDs: rejected states remain retained, not overwritten.
            self.evidence(mode=mode, implementation=implementation)
            error = self.call('evaluate', data={'head': HEAD, 'level': 'product'}, ok=False)
            self.assertIn('AC-001', error)
            self.assertIn('AC-002', error)
        self.evidence()
        self.call('evaluate', data={'head': 'c' * 40, 'level': 'product'}, ok=False)
        self.call('evaluate', data={'head': HEAD, 'level': 'product'})

    def test_hash_missing_proof_and_no_execution(self):
        self.approve()
        self.evidence()
        proof = self.home / 'data/product-contract/sample/artifacts' / self.last_artifacts['AC-001']
        proof.unlink()
        self.call('evaluate', data={'head': HEAD, 'level': 'product'}, ok=False)
        sentinel = self.home / 'must-not-execute'
        self.event('EV-004', 'source', '$(touch ' + str(sentinel) + ')')
        self.assertFalse(sentinel.exists())

    def test_paths_symlinks_schema(self):
        self.call('new', '../escape', ok=False)
        self.call('artifact', data={'path': '../../escape', 'text': 'bad'}, ok=False)
        (self.home / 'data/product-contract/sample/artifacts').mkdir(exist_ok=True)
        (self.home / 'data/product-contract/sample/artifacts/link').symlink_to(self.home)
        self.call('artifact', data={'path': 'link/escape', 'text': 'bad'}, ok=False)
        mutant = copy.deepcopy(self.contract); mutant['schema'] = 9
        self.call('draft', data=mutant, ok=False)
        mutant['schema'] = True
        self.call('draft', data=mutant, ok=False)
        mutant['schema'] = 1; mutant['approved'] = True
        self.call('draft', data=mutant, ok=False)

    def test_missing_new_task_and_legacy_boundary(self):
        self.call('check-task', 'new-task', ok=False, extra=('--required',))
        meta = self.home / 'state/legacy.meta'; meta.parent.mkdir()
        meta.write_text('kind=ship\n')
        self.call('check-task', 'legacy')
        self.call('check-task', 'legacy', ok=False, extra=('--required',))

    def test_restart_concurrent_append(self):
        processes = []
        for i in range(10):
            event = {'id': 'EV-C' + str(i), 'kind': 'source', 'role': 'owner', 'text': 'source ' + str(i),
                     'question': None, 'round': None, 'source_event': 'chat:' + str(i),
                     'supersedes': None, 'revision': 1, 'completeness': 'complete',
                     'expected_bytes': len(('source ' + str(i)).encode()),
                     'expected_sha256': hashlib.sha256(('source ' + str(i)).encode()).hexdigest()}
            proc = subprocess.Popen([str(CLI), 'append', 'sample'], env=self.env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            proc.stdin.write(json.dumps(event)); proc.stdin.close(); proc.stdin = None
            processes.append(proc)
        for proc in processes:
            _, err = proc.communicate(); self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(len(self.call('inspect')['events']), 12)
        # Interrupt a real pending writer while the owning-home lock is held.
        # Neither an incomplete write nor a phantom source/approval may appear.
        import fcntl
        with (self.home / 'data/product-contract/.lock').open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            pending = subprocess.Popen([str(CLI), 'append', 'sample'], env=self.env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            interrupted = dict(event, id='EV-INTERRUPTED', source_event='chat:interrupted')
            pending.stdin.write(json.dumps(interrupted)); pending.stdin.close(); pending.stdin = None
            pending.kill(); pending.communicate(timeout=5)
            self.assertLess(pending.returncode, 0)
        self.assertEqual(len(self.call('inspect')['events']), 12)
        # A stranded temp file is never interpreted as committed state.
        (self.home / 'data/product-contract/sample/.interrupted.tmp').write_text('{')
        self.assertEqual(len(self.call('inspect')['events']), 12)

    def test_transcript_length_truncation_and_missing_question(self):
        text = 'An incomplete transport body'
        event = {'id': 'EV-004', 'kind': 'source', 'role': 'owner', 'text': text,
                 'question': None, 'round': None, 'source_event': 'document:original',
                 'supersedes': None, 'revision': 1, 'completeness': 'complete',
                 'expected_bytes': len(text.encode()) + 1, 'expected_sha256': hashlib.sha256(text.encode()).hexdigest()}
        self.assertIn('EV-004', self.call('append', data=event, ok=False))
        event.update(expected_bytes=len(text.encode()), completeness='truncated')
        self.call('append', data=event, ok=False)
        self.event('EV-005', 'answer', 'Yes', question='Q-MISSING')
        self.call('draft', data=self.contract, ok=False)

    def test_negation_exception_mutations_and_audit_regeneration(self):
        self.draft()
        for behavior in ['Contact leads.', 'Never contact leads except for tests.']:
            self.contract['requirements'][1]['text'] = behavior
            self.assertIn('REQ-002', self.call('draft', data=self.contract, ok=False))
            self.contract['requirements'][1]['text'] = 'Never contact leads.'
            self.contract['acceptance'][1]['oracle'] = 'No outbound calls except test messages'
            self.draft()
            report = {'reviewer': 'reverse-auditor', 'independent': True,
                      'contract_hash': self.call('inspect')['draft_hash'],
                      'source_ids': self.contract['sources'], 'clause_ids': ['CL-001', 'CL-002'],
                      'checks': {k: True for k in ['omission', 'weakening', 'negation', 'exceptions',
                                                  'contradictions', 'invention', 'unresolved']},
                      'findings': [{'id': 'CL-002', 'problem': 'Weakens original Never contact leads.'}]}
            self.assertIn('semantic', self.call('audit', data=report, ok=False))
            self.call('approve', data={'event': 'EV-002', 'digest': 'a' * 64}, ok=False)

    def test_schema_cross_references_and_work_coverage(self):
        mutations = [lambda c: c['requirements'].append(copy.deepcopy(c['requirements'][0])),
                     lambda c: c['acceptance'][0].update(journeys=[]),
                     lambda c: c['work'][0].update(requirements=['REQ-002']),
                     lambda c: c['clauses'][0].update(state='contextual'),
                     lambda c: c['acceptance'][0].update(environment='unknown-target'),
                     lambda c: c['requirements'][0].update(supersedes=['REQ-MISSING']),
                     lambda c: c['boundaries'][0].update(browser_na=None),
                     lambda c: c['work'][0].update(depends=['WU-001']),
                     lambda c: (c['requirements'][0].update(supersedes=['REQ-002']), c['requirements'][1].update(supersedes=['REQ-001']))]
        for change in mutations:
            candidate = copy.deepcopy(self.contract); change(candidate)
            self.call('draft', data=candidate, ok=False)

    def test_wrong_intent_cannot_bind_approved_product(self):
        self.approve()
        brief = self.home / 'data/task-one/brief.md'; brief.parent.mkdir(parents=True)
        brief.write_text("# Task\n## Captain's intent\nSend all leads.\n\n## Firstmate spec\nImplement.\n# Setup\n")
        self.assertIn('owner words', self.call('bind', data={'task': 'task-one', 'unit': 'WU-001'}, ok=False))

    def test_slice_is_not_product_and_failed_latest_attempt_wins(self):
        self.contract['work'][0].update(requirements=['REQ-001'], journeys=['J-001'], level='slice')
        self.contract['work'].append({'id': 'WU-002', 'task': 'task-two', 'requirements': ['REQ-002'],
                                    'journeys': ['J-002'], 'depends': ['WU-001'], 'level': 'product',
                                    'instructions': 'Integrate safety proof.'})
        self.approve(); self.bind(); self.evidence()
        result = self.call('evaluate', data={'head': HEAD, 'level': 'slice', 'unit': 'WU-001'})
        self.assertEqual(result['remaining_product_requirements'], ['REQ-002'])
        self.evidence(implementation='disabled')
        self.call('evaluate', data={'head': HEAD, 'level': 'product'}, ok=False)

    def test_budget_reservation_restart_and_concurrency(self):
        self.contract['budgets'] = [{'id': 'BUD-001', 'provider': 'fixture', 'operation': 'read',
            'tenant': 'sandbox-only', 'credential_ref': 'ref-only', 'requests': 2, 'amount_minor': 0,
            'currency': 'USD', 'expires': '2099-01-01T00:00:00+00:00', 'authority': 'EV-002',
            'data': 'synthetic fixture data', 'cleanup': 'read-only', 'idempotency': 'unique key', 'concurrency': 1}]
        self.approve()
        request = {'id': 'BUD-001', 'requests': 1, 'amount_minor': 0, 'operation_key': 'read-1'}
        self.call('budget', data=request, ok=False, extra=('--expected-digest', '0' * 64))
        self.assertEqual(self.call('inspect')['budget_usage'], [])
        self.call('budget', data=request)
        self.call('budget', data=request, ok=False)
        request['operation_key'] = 'read-2'
        self.assertIn('unresolved', self.call('budget', data=request, ok=False))
        self.call('budget-finish', data={'id': 'BUD-001', 'operation_key': 'read-1', 'observed': 'read completed, no side effects'})
        self.call('budget', data=request)
        self.call('budget-finish', data={'id': 'BUD-001', 'operation_key': 'read-2', 'observed': 'read completed'})
        request['operation_key'] = 'read-3'
        self.assertIn('ceiling', self.call('budget', data=request, ok=False))
        self.contract['revision'] = 2
        self.contract['worker_instructions'] = 'Implement the same accepted obligations with updated internal guidance.'
        self.approve(eventid='EV-004')
        self.assertIn('ceiling', self.call('budget', data=request, ok=False))
        self.contract['revision'] = 3
        self.contract['budgets'][0]['id'] = 'BUD-002'
        self.assertIn('fresh owner authority', self.call('draft', data=self.contract, ok=False))
        self.contract['budgets'][0].update(id='BUD-001', requests=3)
        self.assertIn('fresh owner authority', self.call('draft', data=self.contract, ok=False))

    def test_multilingual_complete_contract(self):
        self.event('EV-004', 'source', '不要发送。 Не удалять.\r\n')
        self.contract['sources'].append('EV-004')
        self.contract['clauses'].append({'id': 'CL-003', 'event': 'EV-004', 'start': 0, 'end': 19,
            'text': '不要发送。 Не удалять.\r\n', 'normative': False, 'state': 'contextual',
            'requirements': [], 'reason': 'Preserved untranslated background'})
        self.contract['clauses'][-1]['end'] = len(self.contract['clauses'][-1]['text'])
        self.approve()
        record = self.call('inspect')
        self.assertEqual(record['events'][2]['text'], '不要发送。 Не удалять.\r\n')

    def test_execution_guard_stops_entire_compound_launch(self):
        script = '. "$1/bin/fm-product-contract-lib.sh"; fm_product_contract_launch_guard "$2" task-one "$1" required "$2/state" "$2/data"'
        proc = subprocess.run(['bash', '-c', script, 'fixture', str(ROOT), str(self.home)], env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        guard = proc.stdout
        # An expired assignment must not run a later command after a semicolon.
        launch = guard + 'printf first-command; printf second-command'
        refused = subprocess.run(['bash', '-c', launch], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(refused.stdout, '')
        self.approve(); self.bind()
        passed = subprocess.run(['bash', '-c', launch], env=self.env, capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(passed.stdout, 'first-commandsecond-command')
        self.event('EV-004', 'decision', 'New semantic requirement', revision=2)
        stale = subprocess.run(['bash', '-c', launch], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(stale.returncode, 0)
        self.assertEqual(stale.stdout, '')

    def test_all_delivery_modes_refuse_missing_contract_before_launch(self):
        setup = '. "$1/tests/lib.sh"; . "$1/tests/fixtures.sh"; fm_test_spawn_home "$2" codex; fm_git_init_commit "$2/projects/proj"; fm_test_make_spawn_fakebin "$2/mock" codex no-mistakes gh gh-axi >/dev/null'
        proc = subprocess.run(['bash', '-c', setup, 'fixture', str(ROOT), str(self.home)], env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        env = dict(self.env, PATH=str(self.home / 'mock/fakebin') + os.pathsep + os.environ['PATH'],
                   FM_BACKEND='tmux', FM_SPAWN_NO_GUARD='1', TMUX='fake,1,0')
        for mode in ['no-mistakes', 'direct-PR', 'local-only']:
            task = 'missing-' + mode
            proc = subprocess.run([str(ROOT / 'bin/fm-brief.sh'), task, 'proj', '--mode', mode], env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            brief = self.home / 'data' / task / 'brief.md'
            brief.write_text(brief.read_text().replace('{TASK}', 'Save via real CLI. Never contact leads.').replace('{FIRSTMATE_SPEC}', 'Implement agreed persistence.'))
            proc = subprocess.run([str(ROOT / 'bin/fm-spawn.sh'), task, str(self.home / 'projects/proj'), '--mode', mode, '--yolo', 'off'], env=env, capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn('missing approved product contract', proc.stderr + proc.stdout)
            self.assertFalse((self.home / 'state' / (task + '.meta')).exists())

    def test_synthetic_approval_helper_refuses_real_project_home(self):
        proc = subprocess.run([sys.executable, str(ROOT / 'tests/fm-product-contract.test.py'), str(ROOT),
                               '--fixture', str(ROOT), 'must-not-be-approved'], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('outside a marked test root', proc.stderr)
        (self.home / '.fm-test-fixture').write_text('synthetic test root\n')
        mixed = subprocess.run([sys.executable, str(ROOT / 'tests/fm-product-contract.test.py'), str(ROOT),
                                '--fixture', str(ROOT), 'must-not-be-approved', str(self.home / 'data')],
                               env=self.env, capture_output=True, text=True)
        self.assertNotEqual(mixed.returncode, 0)
        self.assertIn('outside a marked test root', mixed.stderr)

    def test_empty_legacy_overrides_keep_owning_home_paths(self):
        self.approve(); self.bind()
        self.env.update(FM_DATA_OVERRIDE='', FM_STATE_OVERRIDE='')
        self.call('check-task', 'task-one', extra=('--launch-view',))
        self.assertEqual(self.call('instructions', 'task-one')['binding']['id'], 'sample')

    def test_home_alias_shares_one_ledger_without_allowing_child_symlinks(self):
        self.approve(); self.bind()
        alias = self.home / 'home-alias'
        alias.symlink_to(self.home, target_is_directory=True)
        self.env.update(FM_HOME=str(alias), FM_DATA_OVERRIDE=str(alias / 'data'), FM_STATE_OVERRIDE=str(alias / 'state'))
        self.call('check-task', 'task-one')
        self.assertEqual(self.call('inspect')['revisions'][0]['contract'], self.contract)
        (self.home / 'data').rename(self.home / 'moved-data')
        (self.home / 'data').symlink_to(self.home / 'moved-data', target_is_directory=True)
        self.assertIn('symlink forbidden', self.call('check-task', 'task-one', ok=False))

    def test_required_assignment_cannot_fall_back_at_any_lifecycle_phase(self):
        self.approve(); self.bind(); self.evidence()
        state = self.home / 'state'; state.mkdir(exist_ok=True)
        (state / 'task-one.meta').write_text('kind=ship\n')
        binding = self.home / 'data/task-one/product-contract.json'
        original = binding.read_bytes()
        brief = self.home / 'data/task-one/brief.md'
        # Even removing the visible instruction cannot erase the retained marker.
        brief.write_text(brief.read_text().replace('Product contract: required\n', ''))
        for contents in [None, b'{', b'{}']:
            if contents is None:
                binding.unlink()
            else:
                binding.write_bytes(contents)
            for phase in ['build', 'ready']:
                with self.subTest(contents=contents, phase=phase):
                    self.call('check-task', 'task-one', extra=('--phase', phase, '--head', HEAD), ok=False)
            self.call('instructions', 'task-one', ok=False)
        binding.write_bytes(original)
        self.call('check-task', 'task-one', extra=('--phase', 'ready', '--head', HEAD))

    def test_admission_then_execution_rechecks_task_sources_and_exact_digest(self):
        original_digest = self.approve(); self.bind()
        script = '. "$1/bin/fm-product-contract-lib.sh"; fm_product_contract_launch_guard "$2" task-one "$1" required "$2/state" "$2/data" "$3"'
        proc = subprocess.run(['bash', '-c', script, 'fixture', str(ROOT), str(self.home), original_digest],
                              env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        launch = proc.stdout + 'printf execution-reached'

        def execute(ok):
            result = subprocess.run(['bash', '-c', launch], env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, ok, result.stderr)
            self.assertEqual(result.stdout, 'execution-reached' if ok else '')

        execute(True)
        brief = self.home / 'data/task-one/brief.md'
        original = brief.read_bytes()
        brief.write_bytes(original.replace(b'Build accepted paths.', b'Build changed paths.'))
        execute(False)
        brief.write_bytes(original)
        snapshot = brief.parent / 'launch-brief.md'
        snapshot.unlink()
        execute(False)
        snapshot.write_bytes(original)
        execute(True)
        self.event('EV-004', 'source', 'Additional context, not a changed product.', role='research', revision=2)
        execute(False)
        self.contract['revision'] = 2
        self.contract['sources'].append('EV-004')
        text = 'Additional context, not a changed product.'
        self.contract['clauses'].append({'id': 'CL-003', 'event': 'EV-004', 'start': 0, 'end': len(text),
            'text': text, 'normative': False, 'state': 'contextual', 'requirements': [], 'reason': 'Research context'})
        self.approve(eventid='EV-005'); self.bind()
        # A valid newly approved binding must not authorize an already allocated old launch.
        self.call('check-task', 'task-one', extra=('--launch-view',))
        execute(False)
        self.assertEqual(len(self.call('inspect')['revisions']), 2)

    def test_audit_and_approval_require_independence_complete_inventory_and_exact_manifest(self):
        self.draft()
        audit = {'reviewer': 'reverse-auditor', 'independent': True,
                 'contract_hash': self.call('inspect')['draft_hash'],
                 'source_ids': self.contract['sources'], 'clause_ids': ['CL-001', 'CL-002'],
                 'checks': {k: True for k in ['omission', 'weakening', 'negation', 'exceptions',
                                            'contradictions', 'invention', 'unresolved']}, 'findings': []}
        mutations = [lambda a: a.update(reviewer='planner'), lambda a: a.update(independent=False),
                     lambda a: a.update(contract_hash='0' * 64), lambda a: a.update(source_ids=['EV-002']),
                     lambda a: a.update(clause_ids=['CL-001']), lambda a: a['checks'].update(negation=1)]
        for mutate in mutations:
            candidate = copy.deepcopy(audit); mutate(candidate)
            self.call('audit', data=candidate, ok=False)
        digest = self.call('audit', data=audit)['digest']
        self.event('EV-003', 'approval', 'Approved generally.', manifest='0' * 64)
        self.call('approve', data={'event': 'EV-003', 'digest': digest}, ok=False)
        self.assertEqual(self.call('inspect')['revisions'], [])
        self.event('EV-004', 'approval', 'Approve the exact audited manifest.', manifest=digest)
        self.call('approve', data={'event': 'EV-004', 'digest': digest})
        self.assertEqual(self.call('inspect')['revisions'][0]['manifest']['audit_hash'],
                         hashlib.sha256(json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest())

    def test_results_are_typed_append_only_and_ready_requires_latest_exact_head(self):
        self.approve(); self.bind(); self.evidence()
        retained = self.call('inspect')['results']
        payload = retained[0]['value']
        mutations = [lambda r: r.update(reachable=1), lambda r: r.update(enabled='true'),
                     lambda r: r.update(head=HEAD[:8]), lambda r: r.update(result='complete'),
                     lambda r: r.update(mode='mock'), lambda r: r.update(implementation='done'),
                     lambda r: r.update(flags='default'), lambda r: r.update(artifacts=[]),
                     lambda r: r.update(extra=True)]
        for mutate in mutations:
            candidate = copy.deepcopy(payload); candidate['id'] = 'RES-INVALID'; mutate(candidate)
            self.call('result', data=candidate, ok=False)
        self.call('result', data=payload, ok=False)
        proof = self.last_artifacts['AC-001']
        self.call('artifact', data={'path': proof, 'text': 'replace original observation'}, ok=False)
        self.assertEqual(self.call('inspect')['results'], retained)
        self.call('check-task', 'task-one', extra=('--phase', 'ready', '--head', 'b' * 40), ok=False)
        failed = dict(payload, id='RES-FAILED-LATEST', result='failed', observed='A later real CLI attempt failed.')
        self.call('result', data=failed)
        self.assertIn('AC-001', self.call('check-task', 'task-one', extra=('--phase', 'ready', '--head', HEAD), ok=False))
        self.evidence()
        self.call('check-task', 'task-one', extra=('--phase', 'ready', '--head', HEAD))
        self.assertEqual(self.call('inspect')['results'][:len(retained)], retained)

    def test_backend_admission_matrix_keeps_t3_restrictions_without_vendor_calls(self):
        backends = ['tmux', 'herdr', 'zellij', 'orca', 'cmux', 'paseo', 'rex', 't3code', 'tern', 'hc']
        harnesses = ['claude', 'codex', 'opencode', 'pi', 'pi-signed', 'grok', 'kimi', 'cursor',
                    'gemini', 'muse', 'rovo', 'omp', 'agy', 'devin', 'commandcode', 'polytoken',
                    'cortex', 'kiro', 'hermes']
        script = '. "$1/bin/fm-backend.sh"; fm_backend_validate_spawn "$2" && fm_backend_validate_harness "$2" "$3"'
        for backend in backends:
            for harness in harnesses:
                with self.subTest(backend=backend, harness=harness):
                    proc = subprocess.run(['bash', '-c', script, 'fixture', str(ROOT), backend, harness],
                                          env=self.env, capture_output=True, text=True)
                    # This is the backend predicate, not full role/tool admission.
                    allowed = backend != 't3code' or harness in ['claude', 'codex']
                    self.assertEqual(proc.returncode == 0, allowed, proc.stderr)
                    if not allowed:
                        self.assertIn('only the claude and codex', proc.stderr)
        roles = '. "$1/bin/fm-control-lib.sh"; fm_control_harness_supports_kind "$2" "$3"'
        workers_only = {'gemini', 'muse', 'rovo', 'devin', 'commandcode', 'polytoken', 'cortex', 'kiro', 'hermes'}
        for harness in harnesses:
            for role in ['ship', 'scout', 'secondmate']:
                with self.subTest(harness=harness, role=role):
                    proc = subprocess.run(['bash', '-c', roles, 'fixture', str(ROOT), harness, role],
                                          env=self.env, capture_output=True, text=True)
                    self.assertEqual(proc.returncode == 0, role != 'secondmate' or harness not in workers_only)
        proc = subprocess.run(['bash', '-c', roles, 'fixture', str(ROOT), 'hermes', 'primary'], env=self.env)
        self.assertNotEqual(proc.returncode, 0)
        for backend in ['codex-app', 'unverified']:
            proc = subprocess.run(['bash', '-c', script, 'fixture', str(ROOT), backend, 'codex'],
                                  env=self.env, capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)

    def test_competing_budget_reservations_do_not_double_spend_after_restart(self):
        self.contract['budgets'] = [{'id': 'BUD-001', 'provider': 'fixture', 'operation': 'read',
            'tenant': 'sandbox-only', 'credential_ref': 'ref-only', 'requests': 1, 'amount_minor': 0,
            'currency': 'USD', 'expires': '2099-01-01T00:00:00+00:00', 'authority': 'EV-002',
            'data': 'synthetic fixture data', 'cleanup': 'read-only', 'idempotency': 'unique key', 'concurrency': 1}]
        self.approve()
        processes = []
        for key in ['read-a', 'read-b']:
            payload = {'id': 'BUD-001', 'requests': 1, 'amount_minor': 0, 'operation_key': key}
            proc = subprocess.Popen([str(CLI), 'budget', 'sample'], env=self.env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            proc.stdin.write(json.dumps(payload)); proc.stdin.close(); proc.stdin = None
            processes.append(proc)
        outcomes = [(p.communicate(timeout=10), p.returncode) for p in processes]
        self.assertEqual(sum(rc == 0 for _, rc in outcomes), 1, outcomes)
        usage = self.call('inspect')['budget_usage']
        self.assertEqual(len(usage), 1)
        self.call('budget-finish', data={'id': 'BUD-001', 'operation_key': usage[0]['operation_key'],
                                       'observed': 'Completed real local fixture read; no external operation.'})
        self.assertEqual(self.call('inspect')['budget_usage'][0], usage[0])
        self.call('budget', data={'id': 'BUD-001', 'requests': 1, 'amount_minor': 0, 'operation_key': 'read-c'}, ok=False)
        self.assertEqual(len(self.call('inspect')['budget_usage']), 2)

    def test_gap_does_not_self_update(self):
        self.call('gap', data={'id': 'GAP-001', 'class': 'omitted-source', 'description': 'Missed deny clause',
                               'fixture': 'Sanitized CLI requirement', 'regression': 'deny-replay',
                               'confirmed': True})
        self.assertEqual(self.call('inspect')['gaps'][0]['class'], 'omitted-source')


def prepare_fixture(home, task, data_dir=None):
    """Approved synthetic planning fixture for tests of unrelated launch mechanics."""
    home = Path(home).resolve()
    data_dir = Path(data_dir).resolve() if data_dir else home / 'data'
    home_scope = next((path for path in [home, *home.parents] if (path / '.fm-test-fixture').is_file()), None)
    data_scope = next((path for path in [data_dir, *data_dir.parents] if (path / '.fm-test-fixture').is_file()), None)
    if home_scope is None or home_scope != data_scope:
        raise SystemExit('Synthetic approval fixture refuses a home outside a marked test root or data from a different test root')
    fixture = ProductContract()
    fixture.setUp()
    try:
        fixture.home = Path(home).resolve()
        fixture.env['FM_HOME'] = str(fixture.home)
        fixture.env['FM_DATA_OVERRIDE'] = str(data_dir)
        fixture.env.pop('FM_STATE_OVERRIDE', None)
        brief = data_dir / task / 'brief.md'
        text = brief.read_text()
        words = fixture.call('read-intent', task)['intent']
        fixture.cid = 'fixture-' + hashlib.sha256((task + text).encode()).hexdigest()[:24]
        contract = fixture.contract
        contract['id'] = fixture.cid
        contract['clauses'] = [dict(contract['clauses'][0], start=0, end=len(words), text=words)]
        contract['requirements'] = [dict(contract['requirements'][0], text=words)]
        contract['acceptance'] = [contract['acceptance'][0]]
        contract['journeys'] = [contract['journeys'][0]]
        contract['journey_na']['denied'] = 'Synthetic launch-mechanics test, no denied user path'
        contract['work'][0].update(task=task, requirements=['REQ-001'], journeys=['J-001'])
        path = fixture.home / 'data/product-contract' / fixture.cid / 'record.json'
        if not path.exists():
            fixture.call('new')
            fixture.event('EV-001', 'question', 'What is the requested test task?', role='firstmate', question='Q-001')
            fixture.event('EV-002', 'answer', words, question='Q-001')
            fixture.approve()
        fixture.call('bind', data={'task': task, 'unit': 'WU-001'})
    finally:
        fixture.tearDown()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--fixture':
        prepare_fixture(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        unittest.main(verbosity=2)
