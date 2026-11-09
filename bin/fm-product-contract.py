#!/usr/bin/env python3
"""One private, transactional source ledger, validator and completion evaluator.

No network, embedded-command interpreter, agent launcher or review pipeline.
Uses Python 3 standard library and a home-local advisory lock, not worker locks.
The local owner channel is a trust boundary, not same-UID tamper protection.
"""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

try:
    import fcntl
except ImportError:
    print('product-contract: POSIX Python file locking is required; use the supported macOS/Linux toolchain', file=sys.stderr)
    sys.exit(1)

VERSION = '1.0.0'
SCHEMA = 1
STATES = {'accepted', 'deferred', 'excluded', 'contextual', 'superseded', 'unresolved'}
CLASSES = {'golden', 'alternate', 'denied', 'failure', 'recovery'}
CHECKS = {'omission', 'weakening', 'negation', 'exceptions', 'contradictions', 'invention', 'unresolved'}
ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,100}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
HEAD = re.compile(r'[0-9a-f]{40}(?:[0-9a-f]{24})?\Z')
PLACEHOLDER = re.compile(r'^\s*(?:TODO|TBD|FIXME|PLACEHOLDER)\b|:\s*(?:TODO|TBD|FIXME|PLACEHOLDER)\s*$|\{(?:TASK|FIRSTMATE_SPEC)\}|<[^>]*(?:fill|insert|computed)[^>]*>', re.I | re.M)


class Invalid(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Invalid(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def keys(value, required, optional=(), label='record'):
    need(isinstance(value, dict), f'{label}: expected object')
    need(set(required) <= set(value), f'{label}: missing fields {sorted(set(required) - set(value))}')
    need(set(value) <= set(required) | set(optional), f'{label}: unknown fields {sorted(set(value) - set(required) - set(optional))}')


def string(value, label, placeholders=False):
    need(isinstance(value, str) and bool(value.strip()), f'{label}: missing nonempty text')
    need(placeholders or not PLACEHOLDER.search(value), f'{label}: unresolved placeholder')
    return value


def ident(value, label='id'):
    need(isinstance(value, str) and bool(ID.fullmatch(value)), f'{label}: unsafe identifier {value!r}')
    return value


def unique(values, label):
    need(isinstance(values, list), f'{label}: expected list')
    need(all(isinstance(x, str) for x in values), f'{label}: expected string identifiers')
    need(len(values) == len(set(values)), f'{label}: duplicate references')
    return values


def rows(values, label):
    need(isinstance(values, list), f'{label}: expected rows')
    out = {}
    for value in values:
        need(isinstance(value, dict), f'{label}: expected object')
        key = ident(value.get('id'), label)
        need(key not in out, f'{key}: duplicate ID')
        out[key] = value
    return out


def refs(values, targets, label, nonempty=True):
    unique(values, label)
    need(not nonempty or values, f'{label}: empty required mapping')
    for value in values:
        need(value in targets, f'{label}: unknown reference {value}')


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, f'JSON: duplicate key {key}')
        result[key] = value
    return result


def parse(raw):
    return json.loads(raw, object_pairs_hook=reject_duplicates,
                      parse_constant=lambda value: (_ for _ in ()).throw(Invalid(f'JSON: invalid constant {value}')))


def safe(path, directory=False):
    """Reject every symlink component, including ones that resolve inside home."""
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        if part.exists() or part.is_symlink():
            info = part.lstat()
            need(not stat.S_ISLNK(info.st_mode), f'{part}: symlink forbidden')
            if part != path or directory:
                need(stat.S_ISDIR(info.st_mode), f'{part}: expected directory')
    return path


def atomic(path, value, create_parents=True):
    safe(path)
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    else:
        need(path.parent.is_dir(), f'{path.parent.name}: task directory disappeared during binding; do not recreate cleaned-up work')
    safe(path.parent, True)
    fd, temp = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value if isinstance(value, bytes) else canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self):
        home = os.environ.get('FM_HOME')
        need(home and Path(home).is_absolute(), 'FM_HOME must name the explicit owning absolute home')
        # The explicit home may have an alias spelling; it still owns one lock
        # and ledger. Resolve only that home selector, never child data symlinks.
        spelling = Path(os.path.abspath(home))
        self.home = safe(Path(home).resolve(), True)

        def private_path(value, leaf):
            if not value:
                return self.home / leaf
            path = Path(os.path.abspath(value))
            try:
                path = self.home / path.relative_to(spelling)
            except ValueError:
                pass
            return path

        self.state = safe(private_path(os.environ.get('FM_STATE_OVERRIDE'), 'state'), True)
        self.data = safe(private_path(os.environ.get('FM_DATA_OVERRIDE'), 'data'), True)
        self.root = safe(self.home / 'data/product-contract', True)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextlib.contextmanager
    def lock(self):
        path = safe(self.root / '.lock')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def path(self, cid):
        return safe(self.root / ident(cid) / 'record.json')

    def load(self, cid):
        path = self.path(cid)
        need(path.is_file(), f'{cid}: missing contract ledger; run new and capture original sources')
        raw = path.read_bytes()
        data = parse(raw)
        need(raw == canonical(data) + b'\n', f'{cid}: noncanonical/changed ledger bytes; recover the original atomic record, not a hand-edited projection')
        keys(data, {'schema', 'id', 'events', 'draft', 'audit', 'revisions', 'results', 'gaps', 'budget_usage', 'integrity'})
        need(data['integrity'] == digest({k: v for k, v in data.items() if k != 'integrity'}), f'{cid}: ledger integrity mismatch; restore complete original record, never repair approval hashes in place')
        need(type(data['schema']) is int and data['schema'] == SCHEMA and data['id'] == cid, f'{cid}: unsupported schema or identity mismatch')
        events = rows(data['events'], 'events')
        previous = None
        for event in data['events']:
            event_schema(event, events)
            body = {k: v for k, v in event.items() if k not in {'hash'}}
            need(event['previous'] == previous and event['hash'] == digest(body), f"{event['id']}: corrupt/truncated source hash chain")
            previous = event['hash']
        for n, rev in enumerate(data['revisions'], 1):
            keys(rev, {'contract', 'audit', 'manifest', 'approval'})
            need(rev['contract']['revision'] == n, f'{cid}: revision sequence corrupted at {n}')
            manifest = manifest_for(data, rev['contract'], rev['audit'])
            need(canonical(manifest) == canonical(rev['manifest']), f'{cid}/revision-{n}: approved manifest mismatch')
            approval = events.get(rev['approval'])
            need(approval and approval['kind'] == 'approval' and approval['role'] == 'owner'
                 and approval.get('manifest') == digest(manifest) and approval['revision'] == n, f'{cid}/revision-{n}: approval integrity failure')
        return data

    def save(self, data):
        data['integrity'] = digest({k: v for k, v in data.items() if k != 'integrity'})
        atomic(self.path(data['id']), data)

    def task_paths(self, task):
        ident(task, 'task')
        base = safe(self.data / task, True)
        return safe(base / 'product-contract.json'), safe(base / 'brief.md')


def event_schema(event, events):
    keys(event, {'id', 'kind', 'role', 'text', 'question', 'round', 'source_event', 'supersedes',
                 'revision', 'completeness', 'timestamp', 'previous', 'hash', 'expected_bytes', 'expected_sha256'}, {'manifest', 'depends_on', 'head', 'authority'}, label=event.get('id', 'source-event'))
    key = event['id']
    need(event['kind'] in {'question', 'answer', 'source', 'intent', 'decision', 'approval', 'delivery-authority'}, f'{key}: unknown source kind')
    need(event['role'] in {'owner', 'firstmate', 'research'}, f'{key}: unknown role')
    string(event['text'], key, True)
    original = event['text'].encode('utf-8')
    need(type(event['expected_bytes']) is int and len(original) == event['expected_bytes']
         and hashlib.sha256(original).hexdigest() == event['expected_sha256'], f'{key}: source length/hash mismatch; recapture untruncated original bytes')
    string(event['source_event'], key + '/source_event', True)
    need(type(event['revision']) is int and event['revision'] > 0, f'{key}: invalid revision')
    need(event['completeness'] == 'complete', f'{key}: absent, truncated or incomplete source; recapture original')
    if event['kind'] in {'question', 'answer'}:
        ident(event['question'], key + '/question'); ident(event['round'], key + '/round')
    else:
        need(event['question'] is None and event['round'] is None, f'{key}: non-question event has question binding')
    if event['kind'] == 'approval':
        need(event['role'] == 'owner' and isinstance(event.get('manifest'), str) and HASH.fullmatch(event['manifest']), f'{key}: owner approval must bind exact manifest digest')
    if event['kind'] == 'delivery-authority':
        need(event['role'] == 'owner' and isinstance(event.get('head'), str) and HEAD.fullmatch(event['head']) and event.get('authority') in {'merge', 'release'}, f'{key}: delivery authority must record actual owner authority for exact head')
    if event['supersedes']:
        old = events.get(event['supersedes'])
        need(old and old['id'] != key, f'{key}: invalid supersession pointer')
        need(old['kind'] == event['kind'] or event['kind'] == 'decision', f'{key}: incompatible correction')


def unknown_answer(text):
    return text.strip().lower().rstrip('.!?。') in {"i don't know", 'unknown', 'not sure', 'не знаю', '不知道', 'no sé', 'je ne sais pas', 'todo', 'tbd', 'fixme', 'placeholder'}


def source_events(data):
    return {e['id']: e for e in data['events'] if e['kind'] not in {'approval', 'delivery-authority'}}


def contract_schema(data, contract):
    keys(contract, {'schema', 'id', 'revision', 'profile', 'author', 'summary', 'intent', 'sources',
                    'clauses', 'requirements', 'acceptance', 'journeys', 'journey_na', 'work', 'boundaries',
                    'budgets', 'conflicts', 'decisions', 'completion', 'worker_instructions', 'acceptance_instructions'}, {'intent_context'}, label=data['id'])
    cid = data['id']
    need(type(contract['schema']) is int and contract['schema'] == SCHEMA and contract['id'] == cid, f'{cid}: unsupported contract schema/identity')
    need(type(contract['revision']) is int and contract['revision'] == len(data['revisions']) + 1, f'{cid}: draft must use next revision')
    need(contract['profile'] in {'compact', 'full'}, f'{cid}: unknown profile')
    for field in ['author', 'summary', 'worker_instructions', 'acceptance_instructions']:
        string(contract[field], cid + '/' + field)
    events = source_events(data)
    event_positions = {eid: position for position, eid in enumerate(events)}
    need(set(unique(contract['sources'], 'sources')) == set(events), f'{cid}: source inventory mismatch: {sorted(set(events) ^ set(contract["sources"]))}')
    refs(contract['intent'], events, 'intent')
    refs(contract.get('intent_context', []), events, 'intent_context', False)
    for eid in contract['intent']:
        need(events[eid]['role'] == 'owner', f'{eid}: intent must preserve actual owner words')
    questions = {}
    answered = set()
    for event in events.values():
        need(event['revision'] <= contract['revision'], f'{event["id"]}: source belongs to a later contract revision')
        if event['kind'] in {'answer', 'decision'} and unknown_answer(event['text']):
            need(any(e['supersedes'] == event['id'] and (event['role'] != 'owner' or e['role'] == 'owner') for e in events.values()), f'{event["id"]}: unknown answer remains unresolved; obtain an explicit later decision')
    for event in events.values():
        if event['kind'] == 'question':
            need(event['question'] not in questions, f"{event['question']}: reused question ID; corrections require new question IDs")
            refs(event.get('depends_on', []), questions, event['id'] + '/depends_on', False)
            need(set(event.get('depends_on', [])) <= answered, f'{event["id"]}: dependent question asked before prerequisite answer')
            questions[event['question']] = event
        if event['kind'] == 'answer':
            question = questions.get(event['question'])
            need(question, f"{event['id']}: missing original question {event['question']}")
            prior_answer = events.get(event['supersedes'])
            correction = prior_answer and prior_answer['kind'] == 'answer' and prior_answer['question'] == event['question']
            need(question['round'] == event['round'] or correction, f"{event['id']}: a later-round answer needs an explicit correction pointer for {event['question']}")
            need(event['role'] == 'owner', f"{event['id']}: answer is not owner input")
            if not unknown_answer(event['text']):
                answered.add(event['question'])
        if event['kind'] == 'decision' and event['supersedes'] and event['role'] == 'owner' and not unknown_answer(event['text']):
            prior_answer = events.get(event['supersedes'])
            if prior_answer and prior_answer['kind'] == 'answer':
                answered.add(prior_answer['question'])
    retired_questions = {questions[e['question']]['id'] for e in events.values() if e['kind'] == 'question' and any(n['supersedes'] == e['id'] for n in events.values())}
    active_questions = {qid for qid, event in questions.items() if event['id'] not in retired_questions}
    need(questions and active_questions <= answered, f'{cid}: mandatory questionnaire incomplete: {sorted(active_questions - answered)}')
    clauses = rows(contract['clauses'], 'clauses')
    reqs = rows(contract['requirements'], 'requirements')
    acceptance = rows(contract['acceptance'], 'acceptance')
    journeys = rows(contract['journeys'], 'journeys')
    units = rows(contract['work'], 'work')
    environments = rows(contract['boundaries'], 'boundaries')
    names = set()
    for env in environments.values():
        keys(env, {'id', 'name', 'description', 'browser_na'}, label=env['id'])
        string(env['name'], env['id']); string(env['description'], env['id'])
        need(env['name'] not in names, f"{env['id']}: duplicate environment name")
        names.add(env['name'])
        if env['browser_na'] is not None:
            string(env['browser_na'], env['id'] + '/browser_na')
    for clause in clauses.values():
        key = clause['id']
        keys(clause, {'id', 'event', 'start', 'end', 'text', 'normative', 'state', 'requirements', 'reason'}, label=key)
        event = events.get(clause['event'])
        need(event and event['kind'] != 'question', f'{key}: invalid clause source {clause["event"]}')
        start, end = clause['start'], clause['end']
        need(type(start) is int and type(end) is int and 0 <= start < end <= len(event['text']), f'{key}: invalid exact source span')
        need(clause['text'] == event['text'][start:end], f'{key}: provenance text differs from original {event["id"]}')
        need(type(clause['normative']) is bool and clause['state'] in STATES, f'{key}: invalid disposition')
        need(clause['state'] != 'unresolved', f'{key}: unresolved source clause')
        need(not clause['normative'] or clause['state'] != 'contextual', f'{key}: normative obligation cannot be contextual')
        string(clause['reason'], key + '/reason')
        refs(clause['requirements'], reqs, key, clause['state'] == 'accepted')
    # A corrected logical obligation can have several original source copies.
    # Keep every retired quote covered when an active replacement cites its ID.
    replaced_ids = set()
    pending = [rid for req in reqs.values() if req.get('state') == 'accepted'
               for rid in unique(req.get('supersedes', []), req['id'] + '/supersedes')]
    while pending:
        rid = pending.pop()
        if rid in replaced_ids:
            continue
        replaced_ids.add(rid)
        if rid in reqs:
            pending.extend(unique(reqs[rid].get('supersedes', []), rid + '/supersedes'))
    for eid, event in events.items():
        if event['kind'] == 'question':
            continue
        corrected_source = any(e['supersedes'] == eid for e in events.values())
        active_spans = [(c['start'], c['end']) for c in clauses.values() if c['event'] == eid and c['state'] != 'superseded']
        spans = sorted([(c['start'], c['end']) for c in clauses.values() if c['event'] == eid
                        and (c['state'] != 'superseded' or ((set(c['requirements']) & replaced_ids or (corrected_source and not c['requirements']))
                             and not any(a < c['end'] and b > c['start'] for a, b in active_spans)))])
        cursor = 0
        for start, end in spans:
            need(start >= cursor, f'{eid}: overlapping atomic clauses at {start}')
            need(not event['text'][cursor:start].strip(), f'{eid}: omitted source clause at characters {cursor}:{start}')
            cursor = end
        need(not event['text'][cursor:].strip(), f'{eid}: omitted source clause at characters {cursor}:{len(event["text"])}')
        # More than one answer to a question needs an explicit immutable correction.
        if event['kind'] == 'answer':
            older = [e for e in events.values() if e['kind'] == 'answer' and e['question'] == event['question'] and event_positions[e['id']] < event_positions[event['id']]]
            need(not older or event['supersedes'] in {e['id'] for e in older}, f'{eid}: contradictory answers require explicit supersession/resolution')
        if corrected_source:
            need(any(c['state'] == 'superseded' for c in clauses.values() if c['event'] == eid), f'{eid}: corrected source must retain explicit superseded clause dispositions; preserve unaffected clauses')
    for req in reqs.values():
        key = req['id']
        keys(req, {'id', 'text', 'state', 'clauses', 'technical_decision', 'acceptance', 'supersedes'}, label=key)
        string(req['text'], key, True)
        need(req['state'] in STATES and req['state'] != 'unresolved', f'{key}: unresolved requirement')
        refs(req['clauses'], clauses, key + '/clauses', False)
        need(req['clauses'] or req['technical_decision'], f'{key}: unsupported invented requirement; cite source or labelled Firstmate technical decision')
        normative = list(dict.fromkeys(clauses[cl]['text'].strip() for cl in req['clauses'] if clauses[cl]['normative']))
        if not normative:
            string(req['text'], key)
        if normative:
            need(req['text'].strip() == '\n'.join(normative), f'{key}: source-backed requirement must preserve exact normative guarantees; put derived build instructions separately, never weaken or add exceptions')
        if req['technical_decision'] is not None:
            string(req['technical_decision'], key + '/Firstmate-technical-decision')
        refs(req['acceptance'], acceptance, key + '/acceptance', req['state'] == 'accepted')
        refs(req['supersedes'], reqs, key + '/supersedes', False)
        need(key not in req['supersedes'], f'{key}: cannot supersede itself')
        for retired in req['supersedes']:
            need(reqs[retired]['state'] == 'superseded', f'{key}/{retired}: replacement must retain the earlier requirement as superseded')
        for cl in req['clauses']:
            need(key in clauses[cl]['requirements'] and clauses[cl]['state'] == req['state'], f'{key}/{cl}: bidirectional disposition mismatch')
        for ac in req['acceptance']:
            need(key in acceptance[ac]['requirements'], f'{key}/{ac}: missing reverse acceptance mapping')
    for clause in clauses.values():
        for req in clause['requirements']:
            need(clause['id'] in reqs[req]['clauses'], f'{clause["id"]}/{req}: missing reverse source mapping')
    for ac in acceptance.values():
        key = ac['id']
        keys(ac, {'id', 'requirements', 'given', 'when', 'then', 'oracle', 'kinds', 'environment', 'modes', 'journeys', 'code', 'tests', 'freshness_seconds'}, label=key)
        refs(ac['requirements'], reqs, key)
        for req in ac['requirements']:
            need(key in reqs[req]['acceptance'], f'{key}/{req}: reverse mapping missing')
        for field in ['given', 'when', 'then', 'oracle']:
            string(ac[field], key + '/' + field)
            need(ac[field].strip().lower() not in {'works', 'passes', 'handle errors', 'as expected'}, f'{key}/{field}: no measurable oracle')
        for field in ['kinds', 'code', 'tests']:
            unique(ac[field], key + '/' + field)
            need(ac[field], f'{key}: missing {field} plan')
            for value in ac[field]:
                string(value, key + '/' + field)
        unique(ac['modes'], key)
        need(ac['modes'] and set(ac['modes']) <= {'real', 'sandbox', 'synthetic'}, f'{key}: invalid evidence boundary')
        need(type(ac['freshness_seconds']) is int and ac['freshness_seconds'] > 0, f'{key}: positive evidence freshness window required')
        need(ac['environment'] in names, f'{key}: unknown evidence environment')
        refs(ac['journeys'], journeys, key + '/journeys')
    for env in environments.values():
        if env['browser_na'] is None:
            need(any(ac['environment'] == env['name'] and 'browser' in ac['kinds'] for ac in acceptance.values()), f'{env["id"]}: applicable browser surface lacks planned real-browser acceptance')
    for journey in journeys.values():
        key = journey['id']
        keys(journey, {'id', 'class', 'steps', 'acceptance', 'environment'}, label=key)
        need(journey['class'] in CLASSES, f'{key}: unknown journey class')
        unique(journey['steps'], key)
        need(journey['steps'], f'{key}: missing ordered steps')
        for step in journey['steps']:
            string(step, key)
        refs(journey['acceptance'], acceptance, key)
        need(journey['environment'] in names, f'{key}: unknown journey environment')
        for ac in journey['acceptance']:
            need(key in acceptance[ac]['journeys'] and acceptance[ac]['environment'] == journey['environment'], f'{key}/{ac}: journey trace mismatch')
    need(any(j['class'] == 'golden' for j in journeys.values()), f'{cid}: missing integrated golden journey')
    missing_classes = CLASSES - {j['class'] for j in journeys.values()}
    need(isinstance(contract['journey_na'], dict) and set(contract['journey_na']) == missing_classes, f'{cid}: explain N/A journey classes {sorted(missing_classes)}')
    for cls, reason in contract['journey_na'].items():
        string(reason, cls + '/N/A')
    for unit in units.values():
        key = unit['id']
        keys(unit, {'id', 'task', 'requirements', 'journeys', 'depends', 'level', 'instructions'}, label=key)
        ident(unit['task'], key + '/task')
        need(unit['level'] in {'slice', 'product'}, f'{key}: invalid completion level')
        string(unit['instructions'], key)
        refs(unit['requirements'], reqs, key)
        refs(unit['journeys'], journeys, key)
        refs(unit['depends'], units, key + '/depends', False)
        need(key not in unit['depends'], f'{key}: self dependency')
        for req in unit['requirements']:
            for ac in reqs[req]['acceptance']:
                need(set(acceptance[ac]['journeys']) <= set(unit['journeys']), f'{key}/{req}/{ac}: journey not assigned')
    need(len({u['task'] for u in units.values()}) == len(units), f'{cid}: duplicate task assignment')
    visited = {'depends': set(), 'supersedes': set()}
    def visit(key, trail, mapping, field):
        need(key not in trail, f'{key}: cyclic {field} references')
        if key in visited[field]:
            return
        for dep in mapping[key][field]:
            visit(dep, trail | {key}, mapping, field)
        visited[field].add(key)
    for key in units:
        visit(key, set(), units, 'depends')
    for key in reqs:
        visit(key, set(), reqs, 'supersedes')
    for req in reqs.values():
        if req['state'] == 'accepted':
            need(any(req['id'] in u['requirements'] for u in units.values()), f'{req["id"]}: missing work assignment')
    for conflict in contract['conflicts']:
        keys(conflict, {'id', 'state'}, {'resolution'})
        need(conflict['state'] == 'resolved' and conflict.get('resolution') in events and events[conflict['resolution']]['role'] == 'owner', f'{conflict.get("id")}: unresolved conflict; record owner resolution')
    for decision in contract['decisions']:
        keys(decision, {'id', 'text', 'event'})
        string(decision['text'], decision['id'])
        need(decision['event'] in events, f'{decision["id"]}: decision lacks source')
    keys(contract['completion'], {'slice', 'product', 'delivery'})
    for level, predicate in contract['completion'].items():
        string(predicate, 'completion/' + level)
    budget_schema(contract['budgets'], events)
    # Stable IDs cannot disappear or be recycled across revisions.
    if data['revisions']:
        old = data['revisions'][-1]['contract']
        for field in ['clauses', 'requirements', 'acceptance', 'journeys', 'work']:
            before = rows(old[field], field); after = rows(contract[field], field)
            need(set(before) <= set(after), f'{field}: removed stable IDs {sorted(set(before) - set(after))}; retain superseded rows')
        for key, requirement in rows(old['requirements'], 'requirements').items():
            other = reqs[key]
            need(all(requirement[f] == other[f] for f in ['text', 'clauses', 'technical_decision', 'supersedes']), f'{key}: stable requirement ID reused for different source guarantees; retain old row and add a superseding ID')
        for key, clause in rows(old['clauses'], 'clauses').items():
            other = rows(contract['clauses'], 'clauses')[key]
            need(all(clause[f] == other[f] for f in ['event', 'start', 'end', 'text']), f'{key}: stable source ID reused')
        prior_budgets = [b for revision in data['revisions'] for b in revision['contract']['budgets']]
        current_budgets = rows(old['budgets'], 'budgets')
        for budget in contract['budgets']:
            previous = current_budgets.get(budget['id'])
            if previous and previous['authority'] == budget['authority']:
                need(canonical(previous) == canonical(budget), f'{budget["id"]}: changed grant needs fresh owner authority; a specification approval does not renew budgets')
            else:
                need(budget['authority'] not in {b['authority'] for b in prior_budgets},
                     f'{budget["id"]}: renamed or renewed grant needs fresh owner authority; preserve prior consumption')


def budget_schema(budgets, events):
    for budget in rows(budgets, 'budgets').values():
        key = budget['id']
        keys(budget, {'id', 'provider', 'operation', 'tenant', 'credential_ref', 'requests', 'amount_minor',
                      'currency', 'expires', 'authority', 'data', 'cleanup', 'idempotency', 'concurrency'}, label=key)
        for field in ['provider', 'operation', 'tenant', 'credential_ref', 'currency', 'expires', 'data', 'cleanup', 'idempotency']:
            string(budget[field], key + '/' + field)
        for field in ['requests', 'amount_minor', 'concurrency']:
            need(type(budget[field]) is int and budget[field] >= 0, f'{key}: invalid {field} ceiling')
        need(budget['concurrency'] <= 1, f'{key}: only serialized external operations supported')
        need(budget['requests'] == 0 or budget['concurrency'] == 1, f'{key}: positive request budget contradicts zero operation authority')
        authority = events.get(budget['authority'])
        need(authority and authority['role'] == 'owner', f'{key}: missing external-operation authority')
        try:
            expiry = datetime.datetime.fromisoformat(budget['expires'])
        except ValueError:
            raise Invalid(f'{key}: authority expiry must be an ISO timestamp with timezone') from None
        need(expiry.tzinfo is not None, f'{key}: authority expiry needs explicit timezone')


def audit_schema(data, contract, audit):
    keys(audit, {'reviewer', 'independent', 'contract_hash', 'source_ids', 'clause_ids', 'checks', 'findings'})
    string(audit['reviewer'], 'audit/reviewer')
    need(audit['independent'] is True and audit['reviewer'] != contract['author'], 'audit: independent reverse auditor cannot certify its own edits')
    need(audit['contract_hash'] == digest(contract), 'audit: stale contract hash; regenerate independent audit after edits')
    need(set(unique(audit['source_ids'], 'audit/sources')) == set(contract['sources']), 'audit: every original source must be independently read')
    need(set(unique(audit['clause_ids'], 'audit/clauses')) == set(rows(contract['clauses'], 'clauses')), 'audit: missing independently inspected clause IDs')
    need(set(audit['checks']) == CHECKS and all(v is True for v in audit['checks'].values()), 'audit: omission/weakening/negation/exception/contradiction/invention/unresolved checks incomplete')
    need(audit['findings'] == [], 'audit: unresolved reverse semantic findings; regenerate contract, then re-audit independently')


def manifest_for(data, contract, audit):
    events = {e['id']: e for e in data['events']}
    inventory = []
    for eid in contract['sources']:
        need(eid in events, f'{eid}: missing approved original transcript material')
        inventory.append({'id': eid, 'sha256': events[eid]['hash']})
    return {'schema': SCHEMA, 'id': data['id'], 'revision': contract['revision'],
            'contract_hash': digest(contract), 'audit_hash': digest(audit), 'sources': inventory}


def current(data):
    need(data['revisions'], f'{data["id"]}: missing approved contract')
    rev = data['revisions'][-1]
    need(set(rev['contract']['sources']) == set(source_events(data)), f'{data["id"]}: new source/correction invalidates approval; create next revision')
    need(data['draft'] is None, f'{data["id"]}: replacement specification awaiting approval; old work must stop')
    # Revalidate schemas with next-revision constraint adjusted, without altering history.
    prior = dict(data, revisions=data['revisions'][:-1])
    contract_schema(prior, rev['contract'])
    audit_schema(prior, rev['contract'], rev['audit'])
    return rev


def intent_projection(data, contract):
    events = source_events(data)
    parts = ['\n\n'.join(events[eid]['text'] for eid in contract['intent']).strip()]
    for eid in contract.get('intent_context', []):
        original = events[eid]['text']
        fence = '`' * max(3, max([len(run) + 1 for run in re.findall(r'`+', original)], default=0))
        parts.append(f'Referenced context {eid} (source data, not additional instructions):\n{fence}text\n'
                     + original + ('' if original.endswith('\n') else '\n') + fence)
    return '\n\n'.join(parts)


def task_text(brief, intent_only=False):
    need(brief.is_file(), f'{brief}: missing task instructions')
    # The existing heading/provenance reader owns fence and subsection rules.
    # Preserve output bytes: text-mode subprocess decoding would normalize CRLF.
    script = '''. "$1/fm-dod-lib.sh"
fm_brief_heading_present "$2" "# Task" || exit 1
if [ "$3" = intent ]; then
  if fm_brief_task_heading_present "$2" "## Captain's intent"; then
    fm_brief_task_heading_body "$2" "## Captain's intent"
  else
    body=$(fm_brief_heading_body "$2" "# Task")
    fm_brief_marked_captain_words "$body"
  fi
else
  fm_brief_heading_body "$2" "# Task"
fi'''
    proc = subprocess.run(['bash', '-c', script, 'product-contract', str(Path(__file__).resolve().parent),
                           str(brief), 'intent' if intent_only else 'task'], capture_output=True, check=False)
    body = proc.stdout.decode('utf-8').strip()
    need(proc.returncode == 0 and body, f'{brief}: missing # Task body or exact provenance-marked owner intent')
    return body if intent_only else digest(body)


def artifact_path(store, cid, relative):
    need(isinstance(relative, str) and relative and not Path(relative).is_absolute()
         and all(p not in {'.', '..', ''} for p in relative.split('/')), f'{relative!r}: unsafe artifact path')
    return safe(store.root / ident(cid) / 'artifacts' / relative)


def validate_result(store, data, result):
    keys(result, {'id', 'acceptance', 'head', 'environment', 'mode', 'kinds', 'implementation', 'reachable', 'enabled',
                  'journeys', 'result', 'observed', 'oracle', 'actor', 'code', 'tests', 'flags', 'limitations', 'findings', 'artifacts'})
    ident(result['id'])
    rev = current(data); contract = rev['contract']
    need(result['acceptance'] in rows(contract['acceptance'], 'acceptance'), f'{result["acceptance"]}: unknown acceptance ID')
    need(isinstance(result['head'], str) and HEAD.fullmatch(result['head']), 'result: full candidate head required')
    need(result['mode'] in {'real', 'sandbox', 'synthetic'}, 'result: unknown evidence mode')
    need(result['implementation'] in {'implemented', 'partial', 'missing', 'disabled', 'placeholder'}, 'result: invalid implementation state')
    need(type(result['reachable']) is bool and type(result['enabled']) is bool, 'result: reachability/enablement must be explicit booleans')
    need(result['result'] in {'passed', 'failed', 'unverified'}, 'result: invalid proof outcome')
    need(isinstance(result['flags'], str) and HASH.fullmatch(result['flags']), f'{result["acceptance"]}: configuration/feature-flags digest required')
    for field in ['observed', 'oracle', 'actor', 'environment']:
        string(result[field], result['acceptance'] + '/' + field)
    for field in ['kinds', 'journeys', 'code', 'tests', 'limitations', 'findings']:
        unique(result[field], result['acceptance'] + '/' + field)
    need(result['code'] and result['tests'] and result['artifacts'], f'{result["acceptance"]}: missing actual code/test/proof references')
    for artifact in result['artifacts']:
        keys(artifact, {'path', 'sha256'})
        path = artifact_path(store, data['id'], artifact['path'])
        need(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256'], f'{result["acceptance"]}: missing/corrupt evidence artifact {artifact["path"]}')


def evaluate(store, data, request):
    keys(request, {'head', 'level'}, {'unit', 'delivery'})
    rev = current(data); contract = rev['contract']; md = digest(rev['manifest'])
    need(isinstance(request['head'], str) and HEAD.fullmatch(request['head']), f'{data["id"]}: full integrated candidate head required')
    need(request['level'] in {'slice', 'product', 'delivery'}, 'completion: unknown predicate')
    reqs = rows(contract['requirements'], 'requirements')
    selected = {r['id'] for r in reqs.values() if r['state'] == 'accepted'}
    if request['level'] == 'slice':
        unit = rows(contract['work'], 'work').get(request.get('unit'))
        need(unit, 'completion: slice requires assigned work unit')
        selected &= set(unit['requirements'])
    need(selected, f'{request.get("unit", data["id"])}: no accepted obligations; a cancelled/deferred scope cannot be certified as a completed product or slice')
    needed = {ac for rid in selected for ac in reqs[rid]['acceptance']}
    unmet = []
    configurations = {}
    for ac in contract['acceptance']:
        if ac['id'] not in needed:
            continue
        candidates = [r for r in data['results'] if r['value']['acceptance'] == ac['id'] and r['digest'] == md
                      and r['value']['head'] == request['head']]
        if not candidates:
            unmet.append(f'{ac["id"]}: no evidence at current head'); continue
        wrapper = candidates[-1]
        need(wrapper['hash'] == digest({k: v for k, v in wrapper.items() if k != 'hash'}), f'{ac["id"]}: corrupt result record')
        result = wrapper['value']
        try:
            validate_result(store, data, result)
            observed_at = datetime.datetime.fromisoformat(wrapper['timestamp'])
            age = (datetime.datetime.now(datetime.timezone.utc) - observed_at).total_seconds()
            need(0 <= age <= ac['freshness_seconds'], 'stale evidence; rerun within approved freshness window')
            prior_flags = configurations.setdefault(result['environment'], result['flags'])
            need(prior_flags == result['flags'], 'integrated evidence uses inconsistent target configuration/flags')
            need(result['implementation'] == 'implemented' and result['reachable'] and result['enabled'], 'required real path missing, partial, unreachable or disabled')
            need(result['result'] == 'passed' and not result['findings'] and not result['limitations'], 'failed/unverified evidence or unresolved acceptance findings/limitations')
            need(result['mode'] in ac['modes'], f'{result["mode"]}-only proof cannot satisfy approved {ac["modes"]} boundary')
            need(result['environment'] == ac['environment'], 'wrong target environment')
            need(result['oracle'] == ac['oracle'], 'evidence oracle differs from approved acceptance')
            need(set(ac['kinds']) <= set(result['kinds']), 'missing required evidence kinds')
            need(set(ac['journeys']) <= set(result['journeys']), 'required integrated journey missing')
        except Invalid as error:
            unmet.append(f'{ac["id"]}: {error}')
    need(not unmet, 'completion refused: ' + '; '.join(unmet))
    if request['level'] == 'delivery':
        proof = request.get('delivery')
        need(isinstance(proof, dict), 'delivery: product acceptance is not proof of landing/authority/checks')
        keys(proof, {'head', 'authority_event', 'checks', 'landing_artifact'})
        owner_events = {e['source_event'] for e in data['events'] if e['kind'] == 'delivery-authority' and e['role'] == 'owner' and e['head'] == request['head']}
        need(proof['head'] == request['head'] and proof['authority_event'] in owner_events and proof['checks'] == 'passed', 'delivery: missing exact-head authority or passing delivery checks')
        artifact = proof['landing_artifact']; keys(artifact, {'path', 'sha256'})
        path = artifact_path(store, data['id'], artifact['path'])
        need(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256'], 'delivery: missing/corrupt landing proof')
    return {'schema': SCHEMA, 'id': data['id'], 'revision': contract['revision'], 'digest': md,
            'head': request['head'], 'outcome': {'slice': 'slice-accepted', 'product': 'product-accepted', 'delivery': 'delivery-complete'}[request['level']],
            'source_dispositions': [{'id': c['id'], 'state': c['state'], 'reason': c['reason']} for c in contract['clauses'] if c['state'] != 'accepted'],
            'verified_acceptance': sorted(needed), 'remaining_product_requirements': sorted({r['id'] for r in reqs.values() if r['state'] == 'accepted'} - selected)}


def read_meta(path):
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            need(key not in values, f'{path.name}: duplicate metadata field {key}')
            values[key] = value
    return values


def check_task(store, task, args):
    binding_path, brief = store.task_paths(task)
    if not binding_path.is_file():
        meta = safe(store.state / (task + '.meta'))
        marked = brief.is_file() and 'Product contract: required' in brief.read_text()
        required_record = safe(store.root / 'required' / (task + '.json'))
        need(not args.required and not args.expected_digest and meta.is_file() and not marked and not required_record.exists(),
             f'{task}: missing approved product contract assignment; prepare, approve and bind before implementation')
        return {'outcome': 'legacy-existing-task', 'product_acceptance': 'not-certified'}
    binding = parse(binding_path.read_bytes())
    keys(binding, {'schema', 'task', 'id', 'revision', 'digest', 'unit', 'task_hash'})
    need(type(binding['schema']) is int and binding['schema'] == SCHEMA and type(binding['revision']) is int and binding['task'] == task, f'{task}: invalid task binding')
    need(not args.expected_digest or binding['digest'] == args.expected_digest, f'{task}: contract changed during dispatch; discard stale launch instructions and prepare again')
    data = store.load(binding['id']); rev = current(data)
    need(binding['revision'] == rev['contract']['revision'] and binding['digest'] == digest(rev['manifest']), f'{task}: stale contract revision/digest; rebind only after new approval')
    need(binding['task_hash'] == task_text(brief), f'{task}: task intent/specification changed; capture exact new words and revise approved contract')
    if args.launch_view:
        launch_view = safe(brief.parent / 'launch-brief.md')
        need(binding['task_hash'] == task_text(launch_view), f'{task}: rendered launch view differs from approved task; regenerate instructions before dispatch')
    unit = rows(rev['contract']['work'], 'work').get(binding['unit'])
    need(unit and unit['task'] == task, f'{task}: work assignment mismatch')
    reqs = rows(rev['contract']['requirements'], 'requirements')
    need(any(reqs[r]['state'] == 'accepted' for r in unit['requirements']), f'{task}/{unit["id"]}: no active accepted implementation obligations')
    if args.phase == 'ready':
        head = args.head
        if not head:
            meta = read_meta(safe(store.state / (task + '.meta')))
            head = meta.get('pr_head') if meta.get('mode') == 'no-mistakes' else None
            if not head:
                worktree = meta.get('worktree')
                need(worktree and Path(worktree).is_dir(), f'{task}: cannot resolve current acceptance candidate')
                proc = subprocess.run(['git', '-C', worktree, 'rev-parse', '--verify', 'HEAD'], capture_output=True, text=True, check=False)
                need(proc.returncode == 0, f'{task}: cannot resolve candidate head')
                head = proc.stdout.strip()
        return evaluate(store, data, {'head': head, 'level': unit['level'], 'unit': unit['id']})
    return {'outcome': 'implementation-authorized', **binding}


HELP = """Private product-contract __VERSION__ - deterministic interfaces (no network or publication).
Usage: FM_HOME=/absolute/home bin/fm-product-contract.sh COMMAND ID [options]
Mutation payloads are strict JSON on stdin, except capture reads original bytes.
Outputs are JSON except render; --version reports the reviewed SOP release.
  paths TASK             reject unsafe private paths before rendering instructions
  new ID                 create empty private ledger (0600, directories 0700)
  append ID              append exact source event; no rewriting prior events
  capture ID --event-id SRC --source-event REF --expected-bytes N
      --expected-sha256 HASH [--revision N] [--role owner|firstmate|research]
      [--supersedes SRC]  read original UTF-8 bytes on stdin, not a model summary
  draft ID               validate canonical contract; replace only unapproved draft
  audit ID               record independent reverse-source audit of exact draft hash
  approve ID             {event, digest}: bind actual owner approval event to manifest
  bind ID                {task, unit}: bind approved work assignment to task's # Task
  intent ID              render approved intent/context projection before binding
  read-intent TASK         read canonical or legacy exact owner-intent subsection
  instructions TASK       render exact intent, derived worker/acceptance instructions
  check-task TASK         validate binding/current sources/approval before work
      --required         new ship/promotion: missing assignment always refuses
      --phase ready      evaluate assigned slice/product before ready/landing
      --head SHA         exact candidate (otherwise recorded pipeline/work copy head)
      --expected-digest SHA256  pin the validated revision across dispatch or budget reservation
      --launch-view      also verify the generated launch task body against binding
  artifact ID            {path,text}: create immutable private UTF-8 proof artifact
  result ID              append typed acceptance observations at exact head/digest
  evaluate ID            {head,level,unit?,delivery?}: slice/product/delivery predicate
  budget ID              {id,requests,amount_minor,operation_key}: reserve BEFORE call
  budget-finish ID       {id,operation_key,observed}: reconcile operation; no refund
  gap ID                 {id,class,description,fixture,regression,confirmed}
  inspect ID             canonical machine-readable ledger (PRIVATE)
  render ID              readable view generated from same ledger (PRIVATE)
Schema/field authority: this program's validation errors and the product-contract
skill references/interfaces.md. No content is executed. Reserve budget consumes
capacity even after a crash; never auto-refund uncertain external effects.
Existing running unmarked tasks remain legacy, never certified product-complete.
The owner-channel recorder must be trusted; same-user malicious edits/forged
owner events cannot be cryptographically distinguished by local files alone.
"""


def run(args, payload):
    store = Store()
    if args.command == 'paths':
        _, brief = store.task_paths(args.identifier)
        safe(brief.parent / 'launch-brief.md')
        return {'outcome': 'safe-private-paths'}
    if args.command in {'budget', 'budget-finish'} and safe(store.home / '.fm-product-build.json').exists():
        binding = parse(safe(store.home / '.fm-product-build.json').read_bytes())
        build = ident(binding['build'], 'dedicated build')
        request = {'command': args.command, 'contract': ident(args.identifier), 'request': payload, 'expected_digest': args.expected_digest}
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name('fm-product-build.py')), 'budget-custody', build],
                              input=canonical(request).decode(), text=True, capture_output=True, check=False)
        need(proc.returncode == 0, proc.stderr.strip() or 'dedicated build budget custody unavailable')
        return parse(proc.stdout)
    with store.lock():
        if args.command == 'read-intent':
            return {'intent': task_text(store.task_paths(args.identifier)[1], intent_only=True)}
        if args.command in {'check-task', 'instructions'}:
            outcome = check_task(store, args.identifier, args)
            if args.command == 'check-task':
                return outcome
            need(outcome['outcome'] == 'implementation-authorized', 'legacy task: no product contract instructions')
            data = store.load(outcome['id']); rev = current(data); contract = rev['contract']
            unit = rows(contract['work'], 'work')[outcome['unit']]
            events = {e['id']: e for e in data['events']}
            return {'binding': outcome, 'intent': [events[e]['text'] for e in contract['intent']],
                    'intent_projection': intent_projection(data, contract),
                    'work': unit, 'requirements': contract['requirements'], 'clauses': contract['clauses'],
                    'sources': [events[eid] for eid in contract['sources']], 'acceptance': contract['acceptance'],
                    'journeys': contract['journeys'], 'boundaries': contract['boundaries'], 'budgets': contract['budgets'],
                    'completion': contract['completion'], 'worker_instructions': contract['worker_instructions'],
                    'acceptance_instructions': contract['acceptance_instructions']}
        cid = ident(args.identifier)
        if args.command == 'new':
            path = store.path(cid)
            need(not path.exists(), f'{cid}: ledger already exists')
            data = {'schema': SCHEMA, 'id': cid, 'events': [], 'draft': None, 'audit': None,
                    'revisions': [], 'results': [], 'gaps': [], 'budget_usage': []}
            store.save(data)
            return {'id': cid, 'outcome': 'created'}
        data = store.load(cid)
        cmd = args.command
        if cmd == 'append':
            need(isinstance(payload, dict), 'source event: expected JSON object')
            need(not {'timestamp', 'previous', 'hash'} & set(payload), 'source event: generated fields cannot be supplied')
            existing = next((e for e in data['events'] if e['id'] == payload.get('id')), None)
            if existing:
                original_payload = {k: v for k, v in existing.items() if k not in {'timestamp', 'previous', 'hash'}}
                need(canonical(original_payload) == canonical(payload), f'{existing["id"]}: duplicate ID with changed source; append a correction with supersedes')
                return {'id': existing['id'], 'sha256': existing['hash'], 'outcome': 'already-recorded'}
            event = dict(payload, timestamp=now(), previous=data['events'][-1]['hash'] if data['events'] else None)
            event['hash'] = digest(event)
            events = {e['id']: e for e in data['events']}
            event_schema(event, events)
            if event['supersedes']:
                need(event['supersedes'] in events, 'source correction: supersedes must reference earlier immutable event')
            data['events'].append(event)
            # Any new product meaning invalidates pending audit and existing current approval.
            if event['kind'] not in {'approval', 'delivery-authority'}:
                data['audit'] = None
            result = {'id': event['id'], 'sha256': event['hash']}
        elif cmd == 'draft':
            contract_schema(data, payload)
            data['draft'] = payload; data['audit'] = None
            result = {'draft_hash': digest(payload)}
        elif cmd == 'audit':
            need(data['draft'], f'{cid}: generate complete specification first')
            contract_schema(data, data['draft']); audit_schema(data, data['draft'], payload)
            data['audit'] = payload
            result = {'digest': digest(manifest_for(data, data['draft'], payload))}
        elif cmd == 'approve':
            keys(payload, {'event', 'digest'})
            need(data['draft'] and data['audit'], f'{cid}: complete specification and independent semantic audit required')
            contract_schema(data, data['draft']); audit_schema(data, data['draft'], data['audit'])
            manifest = manifest_for(data, data['draft'], data['audit']); md = digest(manifest)
            event = next((e for e in data['events'] if e['id'] == payload['event']), None)
            need(event and event['kind'] == 'approval' and event['role'] == 'owner'
                 and event.get('manifest') == md == payload['digest'] and event['revision'] == data['draft']['revision'], f'{cid}: actual owner approval must name exact manifest digest')
            data['revisions'].append({'contract': data['draft'], 'audit': data['audit'], 'manifest': manifest, 'approval': event['id']})
            data['draft'] = None; data['audit'] = None
            result = {'outcome': 'approved', 'digest': md}
        elif cmd == 'bind':
            keys(payload, {'task', 'unit'})
            rev = current(data); unit = rows(rev['contract']['work'], 'work').get(payload['unit'])
            need(unit and unit['task'] == payload['task'], 'binding: task/unit not assigned in approved contract')
            reqs = rows(rev['contract']['requirements'], 'requirements')
            need(any(reqs[r]['state'] == 'accepted' for r in unit['requirements']), f'{unit["id"]}: no active accepted implementation obligations')
            path, brief = store.task_paths(payload['task'])
            events = {e['id']: e for e in data['events']}
            exact_intent = intent_projection(data, rev['contract'])
            need(task_text(brief, intent_only=True) == exact_intent, f'{payload["task"]}: actual task intent differs from approved exact owner words')
            # Preserve authored instructions byte-for-byte. A durable
            # admission marker lives with the private history, not in a mutable
            # brief: a lost binding must never downgrade this task to legacy.
            required_record = safe(store.root / 'required' / (payload['task'] + '.json'))
            if not required_record.exists():
                atomic(required_record, {'schema': SCHEMA, 'task': payload['task']})
            result = {'schema': SCHEMA, 'task': payload['task'], 'id': cid,
                      'revision': rev['contract']['revision'], 'digest': digest(rev['manifest']),
                      'unit': unit['id'], 'task_hash': task_text(brief)}
            atomic(path, result, create_parents=False)
        elif cmd == 'artifact':
            keys(payload, {'path', 'text'})
            need(isinstance(payload['text'], str), 'artifact: exact UTF-8 text required')
            path = artifact_path(store, cid, payload['path'])
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            safe(path.parent, True)
            if path.exists():
                need(path.read_bytes() == payload['text'].encode(), f'{payload["path"]}: immutable artifact already exists with different bytes')
            else:
                atomic(path, payload['text'].encode())
            result = {'path': payload['path'], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        elif cmd == 'result':
            validate_result(store, data, payload)
            need(payload['id'] not in {r['value']['id'] for r in data['results']}, f'{payload["id"]}: duplicate result ID; retain prior attempt and append a new ID')
            wrapper = {'value': payload, 'digest': digest(current(data)['manifest']), 'timestamp': now(), 'sequence': len(data['results']) + 1}
            wrapper['hash'] = digest(wrapper)
            data['results'].append(wrapper)
            result = {'outcome': 'recorded', 'sequence': wrapper['sequence']}
        elif cmd == 'evaluate':
            return evaluate(store, data, payload)
        elif cmd == 'budget-finish':
            keys(payload, {'id', 'operation_key', 'observed'})
            need(data['revisions'], 'budget: no approved reservation history')
            string(payload['observed'], 'budget/reconciliation')
            matching = [u for u in data['budget_usage'] if u['id'] == payload['id'] and u['operation_key'] == payload['operation_key'] and 'observed' not in u]
            need(len(matching) == 1, 'budget: no unique active reservation to reconcile')
            # Append settlement rather than rewriting the reservation or refunding.
            result = dict(payload, timestamp=now(), revision=matching[0]['revision'])
            need(not any(u.get('observed') is not None and u['id'] == payload['id'] and u['operation_key'] == payload['operation_key'] for u in data['budget_usage']), 'budget: operation already reconciled')
            data['budget_usage'].append(result)
        elif cmd == 'budget':
            keys(payload, {'id', 'requests', 'amount_minor', 'operation_key'})
            rev = current(data)
            need(not args.expected_digest or args.expected_digest == digest(rev['manifest']), 'budget: approval changed before reservation; preserve the prior operation key and recheck authority')
            budget = rows(rev['contract']['budgets'], 'budgets').get(payload['id'])
            need(budget, f'{payload["id"]}: no approved external-operation budget; zero authority')
            string(payload['operation_key'], 'budget/operation_key')
            need(datetime.datetime.fromisoformat(budget['expires']) > datetime.datetime.now(datetime.timezone.utc), f'{budget["id"]}: external-operation authority expired')
            for field in ['requests', 'amount_minor']:
                need(type(payload[field]) is int and payload[field] >= 0, f'{budget["id"]}: invalid budget reservation')
            grant_versions = {n for n, previous in enumerate(data['revisions'], 1)
                              if any(b['id'] == budget['id'] and b['authority'] == budget['authority'] for b in previous['contract']['budgets'])}
            usage = [u for u in data['budget_usage'] if u['revision'] in grant_versions and u['id'] == budget['id']]
            same = [u for u in data['budget_usage'] if u['id'] == budget['id'] and u['operation_key'] == payload['operation_key']]
            need(not same, f'{budget["id"]}: operation key already reserved; reconcile uncertain operation, never repeat call')
            settlements = {(u['id'], u['revision'], u['operation_key']) for u in data['budget_usage'] if 'observed' in u}
            active = [u for u in data['budget_usage'] if 'requests' in u and (u['id'], u['revision'], u['operation_key']) not in settlements]
            need(not active, f'{budget["id"]}: prior operation unresolved; reconcile its effects before another external call')
            need(payload['requests'] > 0, f'{budget["id"]}: external operation requires positive request reservation')
            for field in ['requests', 'amount_minor']:
                need(sum(u.get(field, 0) for u in usage) + payload[field] <= budget[field], f'{budget["id"]}: {field} ceiling exhausted; required evidence remains unverified')
            entry = dict(payload, revision=rev['contract']['revision'], timestamp=now())
            data['budget_usage'].append(entry); result = {'outcome': 'reserved', **entry}
        elif cmd == 'gap':
            keys(payload, {'id', 'class', 'description', 'fixture', 'regression', 'confirmed'})
            ident(payload['id'])
            need(payload['class'] in {'omitted-source', 'ambiguous-question', 'wrong-assumption', 'missing-edge-case', 'inadequate-evidence'}, 'gap: unsupported classification')
            need(payload['confirmed'] is True, 'gap: confirm actual specification defect before proposing skill correction')
            need(payload['id'] not in {g['id'] for g in data['gaps']}, 'gap: preserve prior correction record with a new ID')
            for field in ['description', 'fixture', 'regression']:
                string(payload[field], 'gap/' + field)
            data['gaps'].append(dict(payload, timestamp=now(), skill_version=VERSION))
            result = {'outcome': 'gap-recorded', 'next': 'sanitized failing public-interface regression, then deliberate reviewed versioned correction; no self-update'}
        elif cmd == 'intent':
            rev = current(data)
            return {'id': cid, 'revision': rev['contract']['revision'], 'digest': digest(rev['manifest']),
                    'text': intent_projection(data, rev['contract'])}
        elif cmd == 'inspect':
            return dict(data, draft_hash=digest(data['draft']) if data['draft'] else None)
        elif cmd == 'render':
            lines = [f'# Private product contract: {cid}', '', '## Original questionnaire and source transcript']
            for event in data['events']:
                lines += ['', f"### {event['id']} | {event['kind']} | {event['question']} | {event['round']} | revision {event['revision']}",
                          f"{event['timestamp']} | {event['source_event']} | supersedes {event['supersedes']}", '', event['text']]
            contract = data['draft'] or (data['revisions'][-1]['contract'] if data['revisions'] else None)
            if contract:
                lines += ['', '## Canonical specification', contract['summary'], '', '## Intent provenance']
                events = {e['id']: e for e in data['events']}
                lines += [events[eid]['text'] for eid in contract['intent']]
                lines += ['', '## Derived build instructions', contract['worker_instructions'], '', '## Requirements']
                lines += [f"- {r['id']} [{r['state']}]: {r['text']} (sources: {', '.join(r['clauses'])})" for r in contract['requirements']]
                lines += ['', '## Acceptance instructions', contract['acceptance_instructions'], '', '## Complete structured contract', '```json', json.dumps(contract, ensure_ascii=False, indent=2), '```']
            return '\n'.join(lines) + '\n'
        else:
            raise Invalid(f'unknown command {cmd}')
        store.save(data)
        return result


def main():
    if sys.argv[1:] == ['--version']:
        print(VERSION); return 0
    if len(sys.argv) == 1 or '--help' in sys.argv:
        print(HELP.replace('__VERSION__', VERSION)); return 0
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('command', choices=['paths', 'new', 'append', 'capture', 'draft', 'audit', 'approve', 'bind', 'check-task', 'read-intent', 'instructions', 'artifact', 'result', 'evaluate', 'budget', 'budget-finish', 'gap', 'intent', 'inspect', 'render'])
    parser.add_argument('identifier')
    parser.add_argument('--required', action='store_true')
    parser.add_argument('--phase', choices=['build', 'ready'], default='build')
    parser.add_argument('--head')
    parser.add_argument('--expected-digest')
    parser.add_argument('--launch-view', action='store_true')
    parser.add_argument('--event-id')
    parser.add_argument('--source-event')
    parser.add_argument('--expected-bytes', type=int)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--revision', type=int, default=1)
    parser.add_argument('--role', choices=['owner', 'firstmate', 'research'], default='owner')
    parser.add_argument('--supersedes')
    args = parser.parse_args()
    try:
        if args.command == 'capture':
            raw = sys.stdin.buffer.read()
            ident(args.event_id, 'capture/event-id')
            string(args.source_event, args.event_id + '/source_event', True)
            need(len(raw) == args.expected_bytes and hashlib.sha256(raw).hexdigest() == args.expected_sha256,
                 f'{args.event_id}: original capture length/hash mismatch; do not import a truncated or changed source')
            try:
                original_text = raw.decode('utf-8')
            except UnicodeDecodeError:
                raise Invalid(f'{args.event_id}: original source is not UTF-8; retain the binary original and obtain an authorized exact text extraction') from None
            payload = {'id': args.event_id, 'kind': 'source', 'role': args.role, 'text': original_text,
                       'question': None, 'round': None, 'source_event': args.source_event, 'supersedes': args.supersedes,
                       'revision': args.revision, 'completeness': 'complete',
                       'expected_bytes': args.expected_bytes, 'expected_sha256': args.expected_sha256}
            args.command = 'append'
        else:
            payload = None if args.command in {'paths', 'new', 'check-task', 'read-intent', 'instructions', 'intent', 'inspect', 'render'} else parse(sys.stdin.read())
        result = run(args, payload)
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False), end='' if isinstance(result, str) else '\n')
        return 0
    except (Invalid, ValueError, TypeError, KeyError, OSError, RecursionError, AttributeError, IndexError) as error:
        print(f'product-contract: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
