#!/usr/bin/env python3
"""Immutable approved build artifacts and parent-owned lifetime reservations.

Artifact/admission, dependency waves, checkpoints and reconciliation use the
existing lifecycle, graph, report and exact-head evaluator owners. Never merge,
grant authority or edit project files here. Run --help for its JSON interface.
It uses the existing product ledger and seeded secondmate identity/parent route.
"""
import argparse
import contextlib
from concurrent.futures import ThreadPoolExecutor
import fcntl
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import textwrap


SPEC = importlib.util.spec_from_file_location('product_contract', Path(__file__).with_name('fm-product-contract.py'))
pc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pc)
SCRIPTS = Path(__file__).resolve().parent
need, keys, safe, ident = pc.need, pc.keys, pc.safe, pc.ident


def read(path):
    raw = safe(path).read_bytes()
    value = pc.parse(raw)
    need(raw == pc.canonical(value) + b'\n', f'{path.name}: noncanonical or incomplete record')
    return value


def immutable(path, value):
    if safe(path).exists():
        need(read(path) == value, f'{path.name}: immutable record differs')
    else:
        pc.atomic(path, value)


def root(store):
    return safe(store.data / 'product-build', True)


@contextlib.contextmanager
def build_lock(store):
    directory = root(store)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with safe(directory / '.lock').open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        yield


def typed_artifact(value, bid):
    keys(value, {'schema', 'build', 'parent', 'contract', 'digest', 'revision', 'project', 'mode',
                 'yolo', 'branch_prefix', 'base_branch', 'baseline', 'work', 'hash'})
    need(value['hash'] == pc.digest({k: v for k, v in value.items() if k != 'hash'}), 'build artifact integrity mismatch')
    need(value['schema'] == 1 and value['build'] == bid, 'build identity/schema mismatch')
    need(pc.HASH.fullmatch(value['digest']), 'invalid approved artifact digest')
    return value


def original_artifact(store, bid):
    return typed_artifact(read(root(store) / 'artifacts' / (ident(bid) + '.json')), bid)


def revision_path(store, bid, digest):
    need(isinstance(digest, str) and pc.HASH.fullmatch(digest), 'invalid revision digest')
    return root(store) / 'revisions' / ident(bid) / (digest + '.json')


def approved_lineage(store, bid, digest):
    base = original_artifact(store, bid)
    wrappers, seen = [], set()
    while digest != base['digest']:
        need(digest not in seen, 'cyclic build revision lineage')
        seen.add(digest)
        wrapper = read(revision_path(store, bid, digest))
        keys(wrapper, {'schema', 'build', 'base', 'previous', 'artifact', 'resolutions', 'hash'})
        need(wrapper['schema'] == 1 and wrapper['build'] == bid and wrapper['base'] == base['hash'], 'revision belongs to another lifetime build')
        need(wrapper['hash'] == pc.digest({k: v for k, v in wrapper.items() if k != 'hash'}), 'revision lineage integrity mismatch')
        value = typed_artifact(wrapper['artifact'], bid)
        need(value['digest'] == digest, 'revision artifact digest mismatch')
        for field in set(base) - {'digest', 'revision', 'work', 'hash'}:
            need(value[field] == base[field], 'semantic revision cannot change build/project/delivery identity')
        wrappers.append(wrapper)
        digest = wrapper['previous']
    wrappers.reverse()
    previous = base
    for wrapper in wrappers:
        need(wrapper['artifact']['revision'] > previous['revision'], 'revision does not advance approved history')
        previous = wrapper['artifact']
    return base, wrappers


def artifact(store, bid):
    base = original_artifact(store, bid)
    data = store.load(base['contract'])
    revision = pc.current(data)
    digest = pc.digest(revision['manifest'])
    try:
        _, wrappers = approved_lineage(store, bid, digest)
    except FileNotFoundError as error:
        raise pc.Invalid('build artifact is stale; new approval requires explicit revision reconciliation') from error
    value = wrappers[-1]['artifact'] if wrappers else base
    need(value['revision'] == revision['contract']['revision'] and value['work'] == revision['contract']['work'], 'approved build roster/revision mismatch')
    for wrapper in wrappers:
        prior = data['revisions'][wrapper['artifact']['revision'] - 1]
        need(pc.digest(prior['manifest']) == wrapper['artifact']['digest'] and prior['contract']['work'] == wrapper['artifact']['work'], 'successor differs from approved ledger history')
        validate_resolutions(data, prior, wrapper['resolutions'])
    return value, data


def validate_resolutions(data, revision, values):
    need(isinstance(values, list) and all(isinstance(row, dict) for row in values), 'owner resolutions must be a list of objects')
    events = pc.rows(data['events'], 'events')
    pc.unique([row.get('change') for row in values], 'owner resolution ids')
    for row in values:
        keys(row, {'change', 'event'})
        ident(row['change'])
        event = events.get(row['event'])
        need(event and event['role'] == 'owner' and event['kind'] in {'source', 'intent', 'answer', 'decision'}
             and event['id'] in revision['contract']['intent'] and event['revision'] == revision['contract']['revision'],
             row['change'] + ': resolution requires exact new owner words inside independently approved intent, not blanket approval')


def revise(store, bid, payload):
    keys(payload, {'digest', 'previous', 'resolutions'})
    need(not safe(store.home / '.fm-secondmate-home').exists(), 'approved revision publication is parent-owned')
    base = original_artifact(store, bid)
    need(base['parent'] == str(store.home), 'only the emitting parent publishes a successor')
    data = store.load(base['contract'])
    current = pc.current(data)
    need(payload['digest'] == pc.digest(current['manifest']), 'successor must be the exact current approved manifest')
    _, ancestors = approved_lineage(store, bid, payload['previous'])
    previous = ancestors[-1]['artifact'] if ancestors else base
    need(current['contract']['revision'] > previous['revision'], 'successor must advance the prior approved artifact')
    validate_resolutions(data, current, payload['resolutions'])
    value = dict(base, digest=payload['digest'], revision=current['contract']['revision'], work=current['contract']['work'])
    value['hash'] = pc.digest({k: v for k, v in value.items() if k != 'hash'})
    wrapper = {'schema': 1, 'build': bid, 'base': base['hash'], 'previous': payload['previous'], 'artifact': value,
               'resolutions': payload['resolutions']}
    wrapper['hash'] = pc.digest(wrapper)
    immutable(revision_path(store, bid, payload['digest']), wrapper)
    return wrapper


def copy_lineage(source, target, bid, digest):
    base, wrappers = approved_lineage(source, bid, digest)
    immutable(root(target) / 'artifacts' / (bid + '.json'), base)
    for wrapper in wrappers:
        immutable(revision_path(target, bid, wrapper['artifact']['digest']), wrapper)


def copy_proof_artifacts(source, target, data):
    for wrapper in data['results']:
        need(wrapper['hash'] == pc.digest({k: v for k, v in wrapper.items() if k != 'hash'}), 'imported result wrapper is corrupt')
        for reference in wrapper['value']['artifacts']:
            dst = pc.artifact_path(target, data['id'], reference['path'])
            if safe(dst).exists():
                need(pc.hashlib.sha256(dst.read_bytes()).hexdigest() == reference['sha256'], 'imported proof path conflicts with retained bytes')
                continue
            src = pc.artifact_path(source, data['id'], reference['path'])
            need(src.is_file() and pc.hashlib.sha256(src.read_bytes()).hexdigest() == reference['sha256'], 'source proof is missing or corrupt')
            pc.atomic(dst, src.read_bytes())


def parent_identity(home):
    # Reuse the route parser instead of accepting a caller-selected parent.
    command = ['bash', '-c', '. "$1/fm-secondmate-parent-lib.sh"; fm_secondmate_parent_record_parse "$2/.fm-secondmate-parent" || exit; [ "$FM_SECONDMATE_PARENT_ROUTE" = local ] || exit 1; printf "%s" "$FM_SECONDMATE_PARENT_HOME"', '_', str(SCRIPTS), str(home)]
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    need(proc.returncode == 0 and proc.stdout, 'dedicated build requires a valid local parent binding')
    return Path(proc.stdout).resolve()


def owners(store):
    path = root(store) / 'owners.json'
    if not safe(path).exists():
        return path, []
    record = read(path)
    keys(record, {'schema', 'owners', 'integrity'})
    need(record['schema'] == 1 and record['integrity'] == pc.digest(record['owners']), 'lifetime reservation integrity mismatch')
    seen = {'build': set(), 'mate': set(), 'home': set()}
    for binding in record['owners']:
        keys(binding, {'build', 'artifact', 'mate', 'home', 'parent'})
        ident(binding['build']); ident(binding['mate'])
        need(binding['parent'] == str(store.home), 'reservation parent mismatch')
        for field in seen:
            need(binding[field] not in seen[field], 'conflicting lifetime reservations')
            seen[field].add(binding[field])
    return path, record['owners']


def emit(store, bid, payload):
    keys(payload, {'contract', 'project', 'mode', 'yolo', 'branch_prefix', 'base_branch', 'baseline'})
    need(not safe(store.home / '.fm-secondmate-home').exists(), 'artifact emission is parent-owned')
    data = store.load(payload['contract'])
    rev = pc.current(data)
    need(payload['mode'] in {'local-only', 'direct-PR', 'no-mistakes'} and payload['yolo'] in {'on', 'off'}, 'explicit delivery mode and merge posture required')
    need(Path(payload['project']).is_absolute(), 'absolute project path required')
    project = safe(payload['project'], True)
    need(project.is_dir(), 'existing project directory required')
    for field in ('branch_prefix', 'base_branch'):
        pc.string(payload[field], field)
    baseline = payload['baseline']
    need(isinstance(baseline, dict) and baseline.get('outcome') in {'success', 'partial', 'unsupported', 'failed', 'unavailable'}, 'explicit baseline observation required')
    if baseline['outcome'] in {'success', 'partial'}:
        need(baseline.get('project') == str(project) and pc.HASH.fullmatch(baseline.get('source_digest', ''))
             and pc.HASH.fullmatch(baseline.get('graph_sha256', '')), 'baseline needs exact project/input/output identity')
    value = dict(payload, schema=1, build=ident(bid), parent=str(store.home), digest=pc.digest(rev['manifest']),
                 revision=rev['contract']['revision'], work=rev['contract']['work'])
    value['project'] = str(project)
    value['hash'] = pc.digest(value)
    immutable(root(store) / 'artifacts' / (bid + '.json'), value)
    return value


def reserve(store, bid, payload):
    keys(payload, {'mate', 'home'})
    need(not safe(store.home / '.fm-secondmate-home').exists(), 'only the parent reserves a build owner')
    value, _ = artifact(store, bid)
    need(value['parent'] == str(store.home), 'only the emitting parent reserves this artifact')
    mate = ident(payload['mate'])
    need(Path(payload['home']).is_absolute(), 'absolute seeded home required')
    home = safe(payload['home'], True)
    need(home != store.home and home.is_dir() and home not in store.home.parents
         and store.home not in home.parents, 'dedicated home must be separate and non-overlapping')
    need(safe(home / '.fm-secondmate-home').read_text().strip() == mate, 'seeded identity mismatch')
    need(parent_identity(home) == store.home, 'secondmate belongs to another parent')
    registry = safe(store.data / 'secondmates.md').read_text().splitlines()
    matches = [line for line in registry if line.startswith('- ' + mate + ' - ')]
    need(len(matches) == 1 and '(home: ' + str(home) + '; scope:' in matches[0], 'secondmate is not in the exact local parent roster')
    path, index = owners(store)
    binding = {'build': bid, 'artifact': original_artifact(store, bid)['hash'], 'mate': mate, 'home': str(home), 'parent': str(store.home)}
    for previous in index:
        if previous['build'] == bid or previous['mate'] == mate or previous['home'] == str(home):
            need(previous == binding, 'lifetime build/secondmate reservation conflict')
            return binding
    need(not safe(home / '.fm-product-build.json').exists(), 'secondmate already has a lifetime build binding')
    index.append(binding)
    pc.atomic(path, {'schema': 1, 'owners': index, 'integrity': pc.digest(index)})
    return binding


def source_store(parent, payload):
    environment = dict(os.environ)
    try:
        os.environ['FM_HOME'] = str(parent)
        os.environ.pop('FM_DATA_OVERRIDE', None)
        os.environ.pop('FM_STATE_OVERRIDE', None)
        for field, variable in [('parent_data', 'FM_DATA_OVERRIDE'), ('parent_state', 'FM_STATE_OVERRIDE')]:
            if field in payload:
                os.environ[variable] = payload[field]
        return pc.Store()
    finally:
        os.environ.clear(); os.environ.update(environment)


def import_build(store, bid, payload):
    keys(payload, {'parent'}, {'parent_data', 'parent_state'})
    for field in ('parent_data', 'parent_state'):
        if field in payload:
            need(Path(payload[field]).is_absolute(), field + ': absolute path required')
            safe(payload[field], True)
    mate = ident(safe(store.home / '.fm-secondmate-home').read_text().strip())
    need(Path(payload['parent']).is_absolute(), 'absolute parent required')
    parent = safe(payload['parent'], True)
    need(parent != store.home and parent == parent_identity(store.home), 'import parent mismatch')
    source = source_store(parent, payload)
    # Fixed lock order: mate build -> parent build -> parent contract. Reservation
    # never acquires mate locks, so replay/import and concurrent reserve cannot
    # deadlock. Only the exact parent reservation authorizes these reads.
    with build_lock(source), source.lock():
        value, data = artifact(source, bid)
        _, index = owners(source)
        binding = next((row for row in index if row['build'] == bid), None)
        need(binding and binding['mate'] == mate and binding['home'] == str(store.home), 'no exact parent-owned reservation')
        need(binding['artifact'] == original_artifact(source, bid)['hash'], 'reservation artifact mismatch')
        marker = store.home / '.fm-product-build.json'
        if safe(marker).exists():
            need(read(marker) == binding, 'conflicting lifetime build import')
        else:
            need(not any(safe(store.state, True).glob('*.meta')), 'dedicated import found prior ordinary task records; preserve and reconcile existing work before binding this home')
        if store.path(data['id']).exists():
            existing = store.load(data['id'])
            need(pc.digest(pc.current(existing)['manifest']) == value['digest'], 'conflicting approved import')
            # Preserve all local attempts, budget consumption and observations.
        else:
            pc.atomic(store.path(data['id']), data)
        copy_lineage(source, store, bid, value['digest'])
        copy_proof_artifacts(source, store, data)
        immutable(root(store) / 'parent.json', dict(payload, parent=str(parent)))
        immutable(marker, binding)
    return binding


@contextlib.contextmanager
def revision_source(store, bid, digest):
    binding = local_binding(store, bid)
    locator = read(root(store) / 'parent.json')
    keys(locator, {'parent'}, {'parent_data', 'parent_state'})
    need(locator['parent'] == binding['parent'], 'revision parent locator mismatch')
    source = source_store(Path(binding['parent']), locator)
    with build_lock(source), source.lock():
        value, data = artifact(source, bid)
        _, index = owners(source)
        need(binding in index and binding['artifact'] == original_artifact(source, bid)['hash'], 'revision lacks the exact lifetime reservation')
        registered_owner(source, binding)
        need(value['digest'] == digest, 'revision import must name the exact current parent approval')
        yield source, value, data


def merge_revision_data(local, incoming):
    need(local['id'] == incoming['id'], 'revision changes contract identity')
    for field in ('events', 'revisions'):
        need(incoming[field][:len(local[field])] == local[field], field + ': divergent approved/source history; reconcile without rewriting either record')
    merged = dict(incoming)
    for field in ('results', 'gaps', 'budget_usage'):
        if local[field][:len(incoming[field])] == incoming[field]:
            merged[field] = local[field]
        elif incoming[field][:len(local[field])] == local[field]:
            merged[field] = incoming[field]
        else:
            need(False, field + ': concurrent divergent records; preserve both before reconciliation')
    return merged


def save_transition(path, record):
    record['integrity'] = pc.digest({k: v for k, v in record.items() if k != 'integrity'})
    pc.atomic(path, record)


def parent_command(store, message):
    return ['bash', '-c', '. "$1/fm-parent-channel-lib.sh"; destination=$(fm_parent_channel_destination "$2" "$3") || exit; fm_parent_channel_append_once "$destination" "$4"', '_', str(SCRIPTS), str(store.home), str(store.state), message]


@contextlib.contextmanager
def revision_adoption_lock(store, bid):
    path = root(store) / 'revisions' / ident(bid) / '.adoption.lock'
    safe(path.parent, True).mkdir(parents=True, exist_ok=True, mode=0o700)
    with safe(path).open('a') as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise pc.Invalid('approved revision import already active; preserve the transition and retry after its owner finishes') from error
        yield


def import_revision(store, bid, payload):
    keys(payload, {'digest'})
    need(isinstance(payload['digest'], str) and pc.HASH.fullmatch(payload['digest']), 'exact approved revision digest required')
    path = root(store) / 'revisions' / ident(bid) / 'transition.json'
    completed = False
    with build_lock(store), store.lock(), revision_source(store, bid, payload['digest']) as (source, value, incoming):
        local = store.load(value['contract'])
        merge_revision_data(local, incoming)  # Refuse divergent grants/proof before stopping anyone.
        if safe(path).exists():
            record = read(path)
            keys(record, {'schema', 'build', 'digest', 'artifact', 'prior', 'tasks', 'stops', 'phase', 'integrity'})
            need(record['schema'] == 1 and record['build'] == bid and record['integrity'] == pc.digest({k: v for k, v in record.items() if k != 'integrity'}), 'corrupt revision transition')
            if record['digest'] != payload['digest']:
                need(record['phase'] == 'complete', 'prior revision transition must finish before another starts')
                immutable(path.parent / ('transition-' + record['digest'] + '.json'), record)
                record = None
            else:
                need(record['artifact'] == value['hash'], 'revision transition artifact changed')
                completed = record['phase'] == 'complete'
        else:
            record = None
        if record is None:
            prior, _ = artifact(store, bid)
            need(prior['digest'] != payload['digest'], 'same revision is already imported; no successor transition needed')
            tasks = []
            for unit in prior['work']:
                meta_path = safe(store.state / (unit['task'] + '.meta'))
                if meta_path.exists():
                    meta, _ = recorded_worker(store, unit['task'], seeded_project(store, bid, prior), prior)
                    tasks.append(dict(task=unit['task'], identity={field: meta.get(field, '') for field in ('spawn_gen', 'window', 'worktree', 'harness', 'backend')}))
            record = {'schema': 1, 'build': bid, 'digest': payload['digest'], 'artifact': value['hash'], 'prior': prior,
                      'tasks': tasks, 'stops': [], 'phase': 'stopping'}
            save_transition(path, record)
    if not completed:
        # Exactly the prior approved direct-report roster, never endpoint
        # discovery. Existing exit owns liveness, keys and stop confirmation.
        # Exit is idempotent, so an interrupted transition can safely resume.
        for task in record['tasks']:
            meta, _ = recorded_worker(store, task['task'], seeded_project(store, bid, record['prior']), record['prior'])
            need({field: meta.get(field, '') for field in task['identity']} == task['identity'], 'worker incarnation changed during revision stop; reconcile exact ownership')
            proc = subprocess.run(['bash', str(SCRIPTS / 'fm-control.sh'), task['task'], 'exit'], text=True, capture_output=True, check=False)
            with build_lock(store):
                record['stops'].append({'task': task['task'], 'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})
                save_transition(path, record)
            need(proc.returncode == 0, 'revision adoption stopped safely: ' + proc.stderr.strip() + '; retained work and transition need reconciliation')
        with build_lock(store), store.lock(), revision_source(store, bid, payload['digest']) as (source, value, incoming):
            local = store.load(value['contract'])
            merged = merge_revision_data(local, incoming)
            _, wrappers = approved_lineage(source, bid, value['digest'])
            allowed_prior = {original_artifact(source, bid)['hash']} | {row['artifact']['hash'] for row in wrappers}
            need(record['prior']['hash'] in allowed_prior, 'successor does not descend from this imported build')
            changes = change_records(store, bid, record['prior'])
            known_questions = {row['id'] for row in changes if row['authority'] == 'owner-required'}
            need(all(row['change'] in known_questions for wrapper in wrappers for row in wrapper['resolutions']), 'successor resolution names an unknown owner question')
            archive = root(store) / 'revision-inputs' / bid / (record['prior']['hash'] + '.json')
            if not safe(archive).exists():
                immutable(archive, local)
            else:
                retained = read(archive)
                need(retained['integrity'] == pc.digest({k: v for k, v in retained.items() if k != 'integrity'}), 'retained pre-revision ledger is corrupt')
            record['phase'] = 'installing'
            save_transition(path, record)
            copy_lineage(source, store, bid, value['digest'])
            copy_proof_artifacts(source, store, incoming)
            store.save(merged)  # Only the shared ledger owner updates integrity.
            active = execution_path(store, bid)
            if safe(active).exists():
                previous = read(active)
                need(previous['artifact'] == record['prior']['hash'] and previous['integrity'] == pc.digest({k: v for k, v in previous.items() if k != 'integrity'}), 'execution changed during revision adoption; preserve it for reconciliation')
                immutable(root(store) / 'execution-history' / bid / (record['prior']['hash'] + '.json'), previous)
                active.unlink()  # Remove only the pointer, after immutable retention.
            record['phase'] = 'complete'
            save_transition(path, record)
    # A new approved revision can close exact keyed questions, but this does
    # not grant an external operation, reset budgets, land code or discard work.
    with build_lock(store), store.lock():
        _, value = owner_binding(store, bid)
        _, wrappers = approved_lineage(store, bid, value['digest'])
        changes = {row['id']: row for row in change_records(store, bid, value)}
        resolutions = [row for wrapper in wrappers for row in wrapper['resolutions']]
    replies = []
    for resolution in resolutions:
        change_record = changes[resolution['change']]
        message = ('resolved [at=' + str(int(pc.datetime.datetime.now(pc.datetime.timezone.utc).timestamp()))
                   + '] [key=build-' + pc.digest(change_record)[:24] + ']: Exact owner event ' + resolution['event']
                   + ' reconciled through approved revision ' + value['digest'] + '; independent operation and landing authority unchanged')
        proc = subprocess.run(parent_command(store, message), text=True, capture_output=True, check=False)
        replies.append({'change': resolution['change'], 'recorded': proc.returncode == 0, 'error': proc.stderr.strip() if proc.returncode else None})
    return {'outcome': 'approved-revision-imported', 'build': bid, 'digest': value['digest'], 'transition': record,
            'parent_replies': replies, 'next': 'prepare revised bounded slices and rebind exact task bodies through the shared engine; retain prior workers/copies and reverify at the integrated candidate'}


def budget_custody(store, bid, payload):
    keys(payload, {'command', 'contract', 'request'}, {'expected_digest'})
    need(payload['command'] in {'budget', 'budget-finish'}, 'unknown budget custody operation')
    request = dict(payload['request'])
    pc.string(request.get('operation_key'), 'budget operation key')
    logical_key = request['operation_key']
    request['operation_key'] = bid + ':' + logical_key
    with build_lock(store), store.lock():
        binding = local_binding(store, bid)
        base = original_artifact(store, bid)
        need(payload['contract'] == base['contract'], 'budget belongs to another build contract')
        locator = read(root(store) / 'parent.json')
        keys(locator, {'parent'}, {'parent_data', 'parent_state'})
        need(locator['parent'] == binding['parent'], 'budget parent locator mismatch')
        source = source_store(Path(binding['parent']), locator)
        with build_lock(source), source.lock():
            _, index = owners(source)
            need(binding in index and original_artifact(source, bid) == base, 'budget lacks exact lifetime parent custody')
            registered_owner(source, binding)
            local, parent = store.load(base['contract']), source.load(base['contract'])
            need(parent['budget_usage'][:len(local['budget_usage'])] == local['budget_usage'], 'divergent local budget journal must be reconciled before any further operation')
        expected = None
        if payload['command'] == 'budget':
            _, current = owner_binding(store, bid)
            expected = current['digest']
            need(not payload.get('expected_digest') or payload['expected_digest'] == expected, 'budget approval changed before parent reservation')
    environment = dict(os.environ, FM_HOME=str(source.home), FM_STATE_OVERRIDE=str(source.state), FM_DATA_OVERRIDE=str(source.data))
    command = [sys.executable, str(SCRIPTS / 'fm-product-contract.py'), payload['command'], base['contract']]
    if expected:
        command += ['--expected-digest', expected]
    proc = subprocess.run(command, input=pc.canonical(request).decode(), text=True, capture_output=True, env=environment, check=False)
    need(proc.returncode == 0, 'parent budget custody refused: ' + proc.stderr.strip())
    result = pc.parse(proc.stdout)
    result['custody_operation_key'] = request['operation_key']
    result['operation_key'] = logical_key
    result['custody'] = 'parent contract ledger; all dedicated builds share the original grant ceiling'
    # A projection failure after a confirmed reservation must never invite a
    # duplicate external call. The durable parent reservation stays authority.
    try:
        with build_lock(store), store.lock(), source.lock():
            local, parent = store.load(base['contract']), source.load(base['contract'])
            if local['events'] == parent['events'] and local['revisions'] == parent['revisions']:
                need(parent['budget_usage'][:len(local['budget_usage'])] == local['budget_usage'], 'local budget projection diverged')
                local['budget_usage'] = parent['budget_usage']
                store.save(local)
                result['projection'] = 'current'
            else:
                result['projection'] = 'pending approved revision import; parent reservation is retained'
    except (pc.Invalid, OSError) as error:
        result['projection'] = 'pending reconciliation: ' + str(error)
    return result


def local_binding(store, bid):
    mate = ident(safe(store.home / '.fm-secondmate-home').read_text().strip())
    binding = read(store.home / '.fm-product-build.json')
    keys(binding, {'build', 'artifact', 'mate', 'home', 'parent'})
    need(binding['build'] == bid, 'unrelated build assignment refused')
    need(binding['mate'] == mate and binding['home'] == str(store.home), 'dedicated identity mismatch')
    need(parent_identity(store.home) == Path(binding['parent']), 'dedicated parent mismatch')
    original = original_artifact(store, bid)
    need(binding['artifact'] == original['hash'] and original['parent'] == binding['parent'], 'reservation artifact/parent mismatch')
    return binding


def owner_binding(store, bid):
    binding = local_binding(store, bid)
    transition = root(store) / 'revisions' / bid / 'transition.json'
    if safe(transition).exists():
        need(read(transition)['phase'] == 'complete', 'approved revision transition incomplete; resume import-revision before dispatch')
    value, _ = artifact(store, bid)
    locator = read(root(store) / 'parent.json')
    keys(locator, {'parent'}, {'parent_data', 'parent_state'})
    need(locator['parent'] == binding['parent'], 'parent locator mismatch')
    source = source_store(Path(binding['parent']), locator)
    with build_lock(source), source.lock():
        approved, _ = artifact(source, bid)
        _, index = owners(source)
        need(approved == value and binding in index, 'dedicated approval/import is no longer current')
        registered_owner(source, binding)
    return binding, value


def show(store, bid):
    binding, value = owner_binding(store, bid)
    state = execution(store, bid, value) if safe(execution_path(store, bid)).exists() else 'not-started'
    return {'owner': binding, 'artifact': value, 'execution': state, 'product_acceptance': 'not-certified'}


def registered_owner(store, binding):
    registry = safe(store.data / 'secondmates.md').read_text().splitlines()
    matches = [line for line in registry if line.startswith('- ' + binding['mate'] + ' - ')]
    need(len(matches) == 1 and '(home: ' + binding['home'] + '; scope:' in matches[0], 'dedicated owner is no longer in the parent roster')


def route(store, bid, payload):
    """Parent-only permission for the existing seeder's two local clone guards."""
    keys(payload, {'home', 'project'})
    value, _ = artifact(store, bid)
    need(value['parent'] == str(store.home), 'project routing is parent-owned')
    need(value['mode'] == 'local-only', 'dedicated local route requires local-only artifact')
    home = safe(payload['home'], True)
    need(Path(payload['home']).is_absolute() and home != store.home, 'absolute dedicated home required')
    _, index = owners(store)
    binding = next((row for row in index if row['build'] == bid), None)
    need(binding and binding['home'] == str(home) and binding['artifact'] == original_artifact(store, bid)['hash'], 'no exact dedicated project reservation')
    registered_owner(store, binding)
    need(read(home / '.fm-product-build.json') == binding, 'dedicated import required before project routing')
    need(safe(home / '.fm-secondmate-home').read_text().strip() == binding['mate']
         and parent_identity(home) == store.home, 'dedicated project owner mismatch')
    need(Path(payload['project']).is_absolute() and str(safe(payload['project'], True)) == value['project'], 'project differs from approved build artifact')
    return {'outcome': 'dedicated-local-route', 'build': bid, 'project': value['project'], 'owner': binding}


def stamp_project(store, bid, payload):
    permit = route(store, bid, payload)
    project = safe(Path(payload['home']) / 'projects' / Path(payload['project']).name, True)
    need(safe(project / '.git', True).is_dir(), 'dedicated project must be an independent clone')
    proc = subprocess.run(['git', '-C', str(project), 'remote', 'get-url', 'origin'], text=True, capture_output=True, check=False)
    need(proc.returncode == 0 and proc.stdout.strip() == permit['project'], 'dedicated clone must originate at the exact parent project')
    value, _ = artifact(store, bid)
    receipt = {'build': bid, 'artifact': original_artifact(store, bid)['hash'], 'source': value['project']}
    immutable(project / '.git' / 'fm-product-build.json', receipt)
    return receipt


def execution_path(store, bid):
    return root(store) / 'executions' / (ident(bid) + '.json')


def execution(store, bid, value):
    record = read(execution_path(store, bid))
    keys(record, {'schema', 'build', 'artifact', 'plan', 'receipts', 'handoffs', 'integrity'})
    need(record['schema'] == 1 and record['build'] == bid and record['artifact'] == value['hash'], 'execution identity mismatch')
    need(record['integrity'] == pc.digest({k: v for k, v in record.items() if k != 'integrity'}), 'execution integrity mismatch')
    return record


def save_execution(store, record):
    record['integrity'] = pc.digest({k: v for k, v in record.items() if k != 'integrity'})
    pc.atomic(execution_path(store, record['build']), record)


def task_binding(store, task, value):
    args = argparse.Namespace(required=True, expected_digest=value['digest'], launch_view=False, phase='build')
    bound = pc.check_task(store, task, args)
    need(bound['id'] == value['contract'] and bound['digest'] == value['digest'], 'task belongs to another approved build contract')
    return bound


def prepare(store, bid, payload):
    """Bound execution details, never new requirement/work/task assignments."""
    _, value = owner_binding(store, bid)
    keys(payload, {'integration_unit', 'slices'})
    units = pc.rows(value['work'], 'work')
    slices = pc.rows(payload['slices'], 'slices')
    need(set(slices) == set(units), 'execution slices must equal the immutable approved work roster')
    need(payload['integration_unit'] in units, 'integration must use an existing approved assignment')
    need(not units[payload['integration_unit']]['depends'], 'integration assignment must be available in the first wave')
    for uid, item in slices.items():
        keys(item, {'id', 'inputs', 'interface', 'non_goals', 'scope', 'checkpoint'})
        for field in ('inputs', 'non_goals', 'scope'):
            pc.unique(item[field], uid + '/' + field)
            need(item[field], uid + ': bounded ' + field + ' required')
            for entry in item[field]:
                pc.string(entry, uid + '/' + field)
        pc.string(item['interface'], uid + '/interface')
        pc.string(item['checkpoint'], uid + '/checkpoint')
        task_binding(store, units[uid]['task'], value)
    if safe(execution_path(store, bid)).exists():
        record = execution(store, bid, value)
        need(record['plan'] == payload, 'execution plan is immutable; preserve existing custody and checkpoints')
    else:
        record = {'schema': 1, 'build': bid, 'artifact': value['hash'], 'plan': payload, 'receipts': [], 'handoffs': []}
        save_execution(store, record)
    return record


def git(project, *arguments):
    proc = subprocess.run(['git', '-C', str(project), *arguments], text=True, capture_output=True, check=False)
    need(proc.returncode == 0, 'git observation failed: ' + proc.stderr.strip())
    return proc.stdout.strip()


def seeded_project(store, bid, value):
    project = safe(store.home / 'projects' / Path(value['project']).name, True)
    need(read(project / '.git' / 'fm-product-build.json') ==
         {'build': bid, 'artifact': original_artifact(store, bid)['hash'], 'source': value['project']}, 'seeded project provenance mismatch')
    return project


def worker(store, task, project, value):
    task_binding(store, task, value)
    return recorded_worker(store, task, project, value)


def recorded_worker(store, task, project, value):
    # Stop authority is project/incarnation identity, not permission to perform
    # work under an old task body. A changed body must not prevent a safe exit.
    meta = pc.read_meta(safe(store.state / (task + '.meta')))
    need(meta.get('kind') == 'ship' and meta.get('project') == str(project), 'worker is not an approved project ship')
    need(meta.get('mode') == value['mode'] and meta.get('yolo') == value['yolo'], 'worker delivery custody changed')
    pc.string(meta.get('spawn_gen'), 'worker incarnation')
    wt = safe(meta.get('worktree', ''), True)
    need(wt.is_dir() and wt != project and git(wt, 'rev-parse', '--show-toplevel') == str(wt), 'worker must have a genuine isolated project root')
    common = lambda path: (path / git(path, 'rev-parse', '--git-common-dir')).resolve()
    need(common(wt) == common(project), 'worker does not belong to the seeded project repository')
    return meta, wt


def accepted_dependencies(store, value, record, unit, project):
    units = pc.rows(value['work'], 'work')
    receipts = {r['unit']: r for r in record['receipts']}
    data = store.load(value['contract'])
    selected = []
    for uid in unit['depends']:
        need(uid in receipts, unit['id'] + ': missing accepted integrated dependency ' + uid)
        receipt = receipts[uid]
        need(receipt['hash'] == pc.digest({k: v for k, v in receipt.items() if k != 'hash'}), 'integration receipt integrity mismatch')
        # Re-evaluate current retained proof, not a durable success sentence.
        pc.evaluate(store, data, {'head': receipt['head'], 'level': 'slice', 'unit': uid})
        need(subprocess.run(['git', '-C', str(project), 'merge-base', '--is-ancestor', receipt['head'], 'HEAD'],
                            capture_output=True, check=False).returncode == 0, 'accepted dependency is not in the integrated project')
        need(receipt['task'] == units[uid]['task'], 'dependency task assignment changed')
        selected.append(receipt['hash'])
    return selected


def require_report(store, bid):
    path = safe(store.home / 'data/product-report/active.json')
    need(path.is_file(), 'verified initial build reporting required before worker allocation')
    record = pc.parse(path.read_bytes())
    need(record.get('active') and record.get('build') == bid and record.get('start') is not None
         and any(row.get('slot') == 0 and row.get('published') is not None for row in record.get('receipts', [])),
         'verified initial build reporting required before worker allocation')


def frontier(store, bid):
    _, value = owner_binding(store, bid)
    record = execution(store, bid, value)
    project = seeded_project(store, bid, value)
    completed = {r['unit'] for r in record['receipts']}
    result = []
    for unit in value['work']:
        state, reason, inputs = 'ready', None, []
        try:
            require_report(store, bid)
            need(not pending_authority(store, bid, value, unit['requirements']), 'affected dispatch awaits exact owner input/new approved semantics')
            task_binding(store, unit['task'], value)
            inputs = accepted_dependencies(store, value, record, unit, project)
        except pc.Invalid as error:
            state, reason = 'waiting', str(error)
        if unit['id'] in completed:
            state = 'integrated'
        elif safe(store.state / (unit['task'] + '.meta')).exists():
            # Metadata is allocation history, not liveness. Recovery reconciles
            # this exact direct report through the existing control plane.
            state = 'allocated-reconcile-before-retry'
        result.append({'unit': unit['id'], 'task': unit['task'], 'state': state, 'reason': reason,
                       'accepted_inputs': inputs, 'requirements': unit['requirements'], 'journeys': unit['journeys']})
    return {'build': bid, 'head': git(project, 'rev-parse', 'HEAD'), 'units': result,
            'capacity': 'existing fm-spawn admission is authoritative; exit 75 leaves work queued',
            'context_pressure': {'measurement': 'unknown', 'policy': 'bounded approved slices; durable checkpoint before fresh same-assignment replacement'}}


def proof(store, value, reference):
    keys(reference, {'path', 'sha256'})
    path = pc.artifact_path(store, value['contract'], reference['path'])
    need(path.is_file() and pc.hashlib.sha256(path.read_bytes()).hexdigest() == reference['sha256'], 'missing/corrupt build proof artifact')
    return reference


def integrate(store, bid, payload):
    _, value = owner_binding(store, bid)
    keys(payload, {'unit', 'integrator', 'head', 'proof'}, {'supersedes'})
    record = execution(store, bid, value)
    units = pc.rows(value['work'], 'work')
    unit = units.get(payload['unit'])
    need(unit, 'integration unit is outside the approved roster')
    need(payload['integrator'] == record['plan']['integration_unit'], 'integration edits belong to the named approved integration worker')
    project = seeded_project(store, bid, value)
    accepted_inputs = accepted_dependencies(store, value, record, unit, project)
    implementer, wt = worker(store, unit['task'], project, value)
    integrator, integration_wt = worker(store, units[payload['integrator']]['task'], project, value)
    head = payload['head']
    need(isinstance(head, str) and pc.HEAD.fullmatch(head), 'full integrated head required')
    need(git(integration_wt, 'rev-parse', 'HEAD') == head and not git(integration_wt, 'status', '--porcelain'), 'integration candidate must be the named worker clean HEAD')
    candidate = git(wt, 'rev-parse', 'HEAD')
    need(subprocess.run(['git', '-C', str(project), 'merge-base', '--is-ancestor', candidate, head], capture_output=True, check=False).returncode == 0,
         'integration candidate does not contain the implementation worker commit')
    need(subprocess.run(['git', '-C', str(project), 'merge-base', '--is-ancestor', head, 'HEAD'], capture_output=True, check=False).returncode == 0,
         'receipt candidate has not been integrated through the selected delivery path')
    evaluated = pc.evaluate(store, store.load(value['contract']), {'head': head, 'level': 'slice', 'unit': unit['id']})
    receipt = dict(payload, task=unit['task'], implementation_head=candidate, worker=implementer['spawn_gen'],
                   integration_worker=integrator['spawn_gen'], accepted_inputs=accepted_inputs, acceptance=evaluated)
    proof(store, value, payload['proof'])
    receipt['hash'] = pc.digest(receipt)
    history = [r for r in record['receipts'] if r['unit'] == unit['id']]
    previous = history[-1] if history else None
    if previous and previous == receipt:
        return previous
    if previous:
        need(payload.get('supersedes') == previous['hash'], 'new integration must explicitly supersede the latest receipt; never rewrite accepted history')
    else:
        need('supersedes' not in payload, 'initial integration cannot supersede absent history')
    record['receipts'].append(receipt)
    save_execution(store, record)
    return receipt


def offer_delivery(store, bid, payload):
    _, value = owner_binding(store, bid)
    keys(payload, {'id', 'unit', 'units', 'head'})
    ident(payload['id']); pc.unique(payload['units'], 'delivery units')
    units = pc.rows(value['work'], 'work')
    plan = execution(store, bid, value)
    need(payload['unit'] == plan['plan']['integration_unit'] and payload['unit'] in payload['units'], 'delivery belongs to the named integration assignment')
    need(payload['units'] and set(payload['units']) <= set(units), 'delivery units outside approved roster')
    need(value['mode'] == 'local-only', 'local delivery requires the selected local-only path')
    need(not pending_authority(store, bid, value, [r for uid in payload['units'] for r in units[uid]['requirements']]), 'delivery awaits owner authority')
    project = seeded_project(store, bid, value)
    meta, wt = worker(store, units[payload['unit']]['task'], project, value)
    head = payload['head']
    need(pc.HEAD.fullmatch(head) and git(wt, 'rev-parse', 'HEAD') == head and not git(wt, 'status', '--porcelain'), 'delivery requires exact clean integration HEAD')
    need(meta.get('local_done_head') == head, 'integration worker must report readiness through the existing done-head owner')
    for uid in payload['units']:
        own, own_wt = worker(store, units[uid]['task'], project, value)
        need(subprocess.run(['git', '-C', str(project), 'merge-base', '--is-ancestor', git(own_wt, 'rev-parse', 'HEAD'), head], capture_output=True).returncode == 0, 'delivery lacks implementation commit')
        pc.evaluate(store, store.load(value['contract']), {'head': head, 'level': 'slice', 'unit': uid})
    document = dict(payload, build=bid, artifact=value['hash'], digest=value['digest'], task=units[payload['unit']]['task'],
                    project=str(project), branch=meta['branch'], incarnation=meta['spawn_gen'])
    document['hash'] = pc.digest(document)
    path = root(store) / 'offers' / bid / (payload['id'] + '.json')
    immutable(path, document)
    return document


def delivery_context(store, bid, payload):
    keys(payload, {'id'}, {'authority_event'})
    ident(payload['id'])
    value, _ = artifact(store, bid)
    base = original_artifact(store, bid)
    _, index = owners(store)
    binding = next((row for row in index if row['build'] == bid), None)
    need(binding and binding['artifact'] == base['hash'], 'parent delivery requires exact lifetime reservation')
    registered_owner(store, binding)
    child = source_store(binding['home'], {})
    local, current = owner_binding(child, bid)
    need(local == binding and current == value, 'delivery owner/current approval changed')
    offered = read(root(child) / 'offers' / bid / (payload['id'] + '.json'))
    need(offered == offer_delivery(child, bid, {key: offered[key] for key in ('id', 'unit', 'units', 'head')}), 'delivery offer is no longer current')
    need(not value['base_branch'] or git(Path(value['project']), 'symbolic-ref', '--short', 'HEAD') == value['base_branch'], 'local delivery must target the approved base on the parent default copy')
    data = store.load(value['contract'])
    if value['yolo'] != 'on':
        need(any(e['kind'] == 'delivery-authority' and e['role'] == 'owner' and e.get('authority') == 'merge' and e['head'] == offered['head'] and e['source_event'] == payload.get('authority_event') for e in data['events']), 'parent delivery requires explicit exact-head owner merge authority')
    need(not safe(store.state / (offered['task'] + '.meta')).exists(), 'parent must not already own this implementation assignment')
    return child, value, offered


def delivery_meta(store, bid, payload):
    child, value, offered = delivery_context(store, bid, payload)
    with build_lock(store), store.lock():
        need(artifact(store, bid)[0] == value, 'parent approval changed during delivery')
        return write_delivery_meta(store, bid, payload, child, value, offered)


def write_delivery_meta(store, bid, payload, child, value, offered):
    directory = safe(store.state / '.product-build-delivery' / bid / payload['id'], True)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    request = dict(payload, offer=offered)
    immutable(directory / 'request.json', request)
    meta = pc.read_meta(child.state / (offered['task'] + '.meta'))
    meta.update(project=value['project'], build_delivery_source=offered['project'], build_delivery_data=str(child.data), build_delivery_home=str(child.home), build_delivery_state=str(child.state))
    # The existing gate reads the child's actual approved evidence read-only;
    # no copied ledger or task alias creates another grant ceiling or owner.
    text = ''.join(key + '=' + val + '\n' for key, val in sorted(meta.items()))
    need(all('\n' not in val and '\r' not in val for val in meta.values()), 'newline in delivery metadata')
    path = directory / (offered['task'] + '.meta')
    if path.exists():
        need(path.read_text() == text, 'delivery metadata changed; preserve request and make a new offer')
    else:
        path.write_text(text); path.chmod(0o600)
    return {'meta': str(path), 'task': offered['task'], 'offer': offered['hash']}


def confirm_delivery(store, bid, payload):
    keys(payload, {'id', 'before', 'head'})
    directory = safe(store.state / '.product-build-delivery' / ident(bid) / ident(payload['id']))
    request = read(directory / 'request.json')
    value, _ = artifact(store, bid)
    offered = request['offer']
    need(payload['head'] == offered['head'] and pc.HEAD.fullmatch(payload['before']), 'landing receipt differs from offered head')
    need(subprocess.run(['git', '-C', value['project'], 'merge-base', '--is-ancestor', payload['head'], 'HEAD'], capture_output=True).returncode == 0, 'parent project does not contain offered landing')
    receipt = dict(payload, build=bid, offer=offered['hash'], project=value['project'], outcome='parent-local-landed')
    immutable(directory / 'landed.json', receipt)
    return receipt


def ready_successor(store, bid, payload):
    """Authorize only a landed integration wave's fresh same-task successor."""
    keys(payload, {'task', 'before', 'head'})
    _, value = owner_binding(store, bid)
    record = execution(store, bid, value)
    unit = pc.rows(value['work'], 'work')[record['plan']['integration_unit']]
    need(payload['task'] == unit['task'] and value['mode'] == 'local-only', 'readiness succession belongs only to the local integration assignment')
    project = seeded_project(store, bid, value)
    meta, wt = worker(store, unit['task'], project, value)
    need(meta.get('local_done_head') == payload['before'] and pc.HEAD.fullmatch(payload['head']), 'readiness predecessor changed')
    need(git(wt, 'rev-parse', 'HEAD') == payload['head'] and not git(wt, 'status', '--porcelain'), 'successor must be the clean worker HEAD')
    need(git(wt, 'rev-parse', meta['branch']) == payload['head'], 'successor branch differs from worker HEAD')
    need(not pending_authority(store, bid, value, unit['requirements']), 'successor awaits owner input')
    prior = [row for row in record['receipts'] if row['unit'] == unit['id']]
    need(prior and prior[-1]['head'] == payload['before'], 'prior readiness has no accepted integration receipt')
    need(prior[-1]['integration_worker'] != meta['spawn_gen'], 'next integration wave requires a fresh incarnation')
    accepted_dependencies(store, value, record, unit, project)
    need(subprocess.run(['git', '-C', str(wt), 'merge-base', '--is-ancestor', payload['before'], payload['head']], capture_output=True).returncode == 0, 'successor must preserve landed history')
    with revision_source(store, bid, value['digest']) as (parent, _, _):
        landed = []
        for path in safe(parent.state / '.product-build-delivery' / bid, True).glob('*/landed.json'):
            receipt = read(path)
            offered = read(path.parent / 'request.json')['offer']
            if (receipt['head'] == payload['before'] and receipt['offer'] == offered['hash']
                    and offered['task'] == unit['task'] and offered['incarnation'] == prior[-1]['integration_worker']):
                landed.append(receipt)
        need(landed, 'previous integration readiness must have confirmed parent landing')
    evaluated = pc.evaluate(store, store.load(value['contract']), {'head': payload['head'], 'level': 'slice', 'unit': unit['id']})
    receipt = dict(payload, build=bid, artifact=value['hash'], incarnation=meta['spawn_gen'],
                   previous_integration=prior[-1]['hash'], acceptance=evaluated)
    receipt['hash'] = pc.digest(receipt)
    immutable(root(store) / 'readiness' / bid / (receipt['hash'] + '.json'), receipt)
    return receipt


def sync_guard(store, bid, payload):
    keys(payload, {'project'})
    binding, value = owner_binding(store, bid)
    project = seeded_project(store, bid, value)
    need(str(project) == payload['project'] and git(project, 'remote', 'get-url', 'origin') == value['project'], 'build sync requires its original local parent origin')
    with revision_source(store, bid, value['digest']) as (parent, _, _):
        directory = safe(parent.state / '.product-build-delivery' / bid, True)
        receipts = [read(path) for path in directory.glob('*/landed.json')]
        need(any(row['build'] == bid and row['project'] == value['project'] and subprocess.run(['git', '-C', value['project'], 'merge-base', '--is-ancestor', row['head'], 'HEAD'], capture_output=True).returncode == 0 for row in receipts), 'build sync requires a confirmed parent landing')
    return {'outcome': 'parent-local-copy-sync-authorized', 'project': str(project)}


def handoff(store, bid, payload):
    _, value = owner_binding(store, bid)
    keys(payload, {'id', 'unit', 'commits', 'artifacts', 'decisions', 'defects', 'next_action', 'validation_custody'})
    ident(payload['id'])
    record = execution(store, bid, value)
    unit = pc.rows(value['work'], 'work').get(payload['unit'])
    need(unit, 'handoff unit is outside the approved roster')
    project = seeded_project(store, bid, value)
    inputs = accepted_dependencies(store, value, record, unit, project)
    meta, wt = worker(store, unit['task'], project, value)
    for field in ('next_action', 'validation_custody'):
        pc.string(payload[field], 'handoff/' + field)
    for field in ('commits', 'decisions', 'defects'):
        pc.unique(payload[field], 'handoff/' + field)
        for entry in payload[field]:
            pc.string(entry, 'handoff/' + field)
    need(isinstance(payload['artifacts'], list) and payload['artifacts'] and payload['commits'], 'handoff needs retained commit and proof references')
    for reference in payload['artifacts']:
        proof(store, value, reference)
    head = git(wt, 'rev-parse', 'HEAD')
    if payload['unit'] == record['plan']['integration_unit']:
        latest = {row['unit']: row for row in record['receipts']}
        for uid, receipt in latest.items():
            need(receipt['hash'] == pc.digest({k: v for k, v in receipt.items() if k != 'hash'}), 'integration handoff receipt is corrupt')
            pc.evaluate(store, store.load(value['contract']), {'head': receipt['head'], 'level': 'slice', 'unit': uid})
            need(subprocess.run(['git', '-C', str(wt), 'merge-base', '--is-ancestor', receipt['head'], head], capture_output=True).returncode == 0, 'integration handoff lacks accepted input commit')
            if receipt['hash'] not in inputs:
                inputs.append(receipt['hash'])
    for commit in payload['commits']:
        need(pc.HEAD.fullmatch(commit) and subprocess.run(['git', '-C', str(wt), 'merge-base', '--is-ancestor', commit, head],
             capture_output=True, check=False).returncode == 0, 'handoff commit not in worker history')
    # Dirty work is preserved and explicitly observed, never discarded or
    # silently turned into a checkpoint commit by this supervisor interface.
    body = dict(payload, build=bid, artifact=value['hash'], task=unit['task'], binding=task_binding(store, unit['task'], value),
                incarnation=meta['spawn_gen'], head=head, dirty=git(wt, 'status', '--porcelain'),
                accepted_inputs=inputs, slice=next(s for s in record['plan']['slices'] if s['id'] == unit['id']),
                pressure={'measurement': 'unknown', 'policy': 'bounded checkpoint; no claimed utilization'})
    body['hash'] = pc.digest(body)
    path = root(store) / 'handoffs' / bid / (payload['id'] + '.json')
    immutable(path, body)
    if body['hash'] not in record['handoffs']:
        record['handoffs'].append(body['hash'])
        save_execution(store, record)
    return {'path': str(path), 'handoff': body,
            'next': 'owner uses existing fm-control relaunch with this durable handoff as note; confirm a genuinely fresh context, not a resumed conversation'}


def guard(store, task, payload):
    """Preallocation admission; ordinary homes keep their existing policy."""
    keys(payload, {'kind', 'project', 'mode', 'yolo'})
    marker = safe(store.home / '.fm-product-build.json')
    if marker.exists():
        binding, value = owner_binding(store, read(marker)['build'])
        need(payload['kind'] == 'ship', 'dedicated build cannot launch scouts or nested secondmates')
        unit = next((row for row in value['work'] if row['task'] == task), None)
        need(unit, 'task is outside the immutable approved build roster')
        need(payload['mode'] == value['mode'] and payload['yolo'] == value['yolo'], 'build delivery posture mismatch')
        project = safe(payload['project'], True)
        # The existing seed surface owns the clone; admission never clones or
        # accepts an arbitrary caller-selected repository with a matching HEAD.
        expected = store.home / 'projects' / Path(value['project']).name
        need(project == expected and project.is_dir(), 'task is not using the dedicated seeded project')
        source = read(project / '.git' / 'fm-product-build.json')
        need(source == {'build': binding['build'], 'artifact': binding['artifact'], 'source': value['project']}, 'seeded project provenance mismatch')
        # Preparation retains exact approved task IDs. All ordinary spawns,
        # including those not invoked through a build front end, consume the
        # same accepted integrated dependency receipts before allocation.
        record = execution(store, binding['build'], value)
        need(not pending_authority(store, binding['build'], value, unit['requirements']), 'affected dispatch awaits exact owner input/new approved semantics')
        task_binding(store, task, value)
        require_report(store, binding['build'])
        accepted_dependencies(store, value, record, unit, project)
        return {'outcome': 'dedicated-task-authorized', 'build': binding['build'], 'unit': unit['id']}
    if payload['kind'] in {'ship', 'scout'}:
        # Parent-owned ledger history names the assignment; never read a mate's
        # child inventory or claim its task as a concurrent production owner.
        _, index = owners(store)
        for binding in index:
            base = original_artifact(store, binding['build'])
            data = store.load(base['contract'])
            tasks = {row['task'] for revision in data['revisions'] for row in revision['contract']['work']}
            need(task not in tasks, 'approved build assignment belongs to dedicated owner ' + binding['mate'] + ', not parent dispatch')
    if payload['kind'] == 'secondmate':
        # Resolve only the caller's exact target. Do not discover child homes.
        home = safe(payload['project'], True)
        _, index = owners(store)
        binding = next((row for row in index if row['mate'] == task or row['home'] == str(home)), None)
        if binding:
            value, _ = artifact(store, binding['build'])
            need(binding['mate'] == task and binding['home'] == str(home)
                 and binding['artifact'] == original_artifact(store, binding['build'])['hash'], 'dedicated launch reservation mismatch')
            registered_owner(store, binding)
            need(read(home / '.fm-product-build.json') == binding, 'dedicated import required before owner launch')
            need(safe(home / '.fm-secondmate-home').read_text().strip() == task
                 and parent_identity(home) == store.home, 'dedicated launch owner mismatch')
        else:
            need(not safe(home / '.fm-product-build.json').exists(), 'dedicated owner has no parent reservation')
    return {'outcome': 'ordinary-admission-unchanged'}


def change_records(store, bid, value):
    directory = root(store) / 'changes' / ident(bid)
    records = [read(path) for path in sorted(directory.glob('*.json'))] if safe(directory).exists() else []
    base, wrappers = approved_lineage(store, bid, value['digest'])
    allowed = {base['hash']} | {row['artifact']['hash'] for row in wrappers}
    for record in records:
        need(record['build'] == bid and record['artifact'] in allowed, 'foreign change history')
        need(record['hash'] == pc.digest({k: v for k, v in record.items() if k != 'hash'}), 'change integrity mismatch')
    return records


def change(store, bid, payload):
    _, value = owner_binding(store, bid)
    keys(payload, {'id', 'class', 'unit', 'reason', 'requirements', 'before', 'after', 'evidence', 'supersedes'}, {'failed_result'})
    ident(payload['id'])
    need(payload['class'] in {'defect', 'technical', 'semantic', 'owner-only'}, 'unknown authority class')
    unit = pc.rows(value['work'], 'work').get(payload['unit'])
    need(unit, 'change unit outside approved build')
    pc.refs(payload['requirements'], set(unit['requirements']), 'change requirements')
    for field in ('reason', 'before', 'after'):
        pc.string(payload[field], 'change/' + field)
    need(isinstance(payload['evidence'], list) and payload['evidence'], 'change requires retained evidence')
    for item in payload['evidence']:
        proof(store, value, item)
    records = change_records(store, bid, value)
    pc.refs(payload['supersedes'], {item['id'] for item in records}, 'change supersessions', nonempty=False)
    need(payload['id'] not in payload['supersedes'], 'change cannot supersede itself')
    if payload['class'] == 'defect':
        data = store.load(value['contract'])
        result = next((row for row in data['results'] if row['hash'] == payload.get('failed_result')), None)
        need(result and result['digest'] == value['digest'], 'defect requires an attributable current approved failed result')
        pc.validate_result(store, data, result['value'])
        needed = {aid for row in pc.current(data)['contract']['requirements'] if row['id'] in payload['requirements'] for aid in row['acceptance']}
        need(result['value']['acceptance'] in needed and result['value']['result'] == 'failed', 'repair must cite a failed approved oracle in scope')
    elif 'failed_result' in payload:
        need(False, 'only defect corrections cite failed-result custody')
    record = dict(payload, build=bid, artifact=value['hash'], digest=value['digest'], task=unit['task'],
                  authority='dedicated-secondmate' if payload['class'] in {'defect', 'technical'} else 'owner-required')
    record['hash'] = pc.digest(record)
    immutable(root(store) / 'changes' / bid / (payload['id'] + '.json'), record)
    return record


def pending_authority(store, bid, value, requirements):
    # No cross-slice semantic-impact proof is implemented. Conservatively stop
    # all dispatch rather than infer isolation from disjoint file/requirement IDs.
    _, wrappers = approved_lineage(store, bid, value['digest'])
    resolved = {row['change'] for wrapper in wrappers for row in wrapper['resolutions']}
    return [row['id'] for row in change_records(store, bid, value) if row['authority'] == 'owner-required' and row['id'] not in resolved]


def enact_change(store, bid, payload):
    keys(payload, {'id', 'change'})
    with build_lock(store), store.lock():
        path, action, fresh = reserve_action(store, bid, payload, 'change')
        if not fresh:
            return action
        _, value = owner_binding(store, bid)
        record = next((row for row in change_records(store, bid, value) if row['id'] == payload['change']), None)
        need(record, 'unknown change id')
        need(record['class'] != 'technical', 'technical overlay requires no execution or owner question')
        if record['class'] == 'defect':
            worker(store, record['task'], seeded_project(store, bid, value), value)
            message = ('Repair only the approved in-scope failed oracle in the attached durable change. Keep the existing delivery and validation custody; do not answer any authority finding yourself. Retain failed evidence and submit fresh exact-final-head results after repair.\n' + pc.canonical(record).decode())
            command = ['bash', str(SCRIPTS / 'fm-send.sh'), record['task'], message]
        else:
            message = ('needs-decision [at=' + str(int(pc.datetime.datetime.now(pc.datetime.timezone.utc).timestamp()))
                       + '] [key=build-' + pc.digest(record)[:24] + ']: Product build requires exact owner input before affected dispatch: ' + pc.canonical(record).decode())
            command = parent_command(store, message)
        action['change'] = record
        pc.atomic(path, action)
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    with build_lock(store), store.lock():
        owner_binding(store, bid)
        action.update(outcome='recorded' if proc.returncode == 0 else 'failed-reconcile', exit=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
        pc.atomic(path, action)
    return action


def automatic_repairs(store, bid, value, data, head):
    contract = pc.current(data)['contract']
    requirements = {row['id']: row for row in contract['requirements'] if row['state'] == 'accepted'}
    needed = {aid for row in requirements.values() for aid in row['acceptance']}
    latest = {row['value']['acceptance']: row for row in data['results']
              if row['digest'] == value['digest'] and row['value']['head'] == head}
    repairs = []
    for aid in sorted(needed):
        wrapper = latest.get(aid)
        if not wrapper:
            continue  # Missing proof is not a fabricated behavioral defect.
        need(wrapper['hash'] == pc.digest({k: v for k, v in wrapper.items() if k != 'hash'}), 'corrupt repair evidence wrapper')
        result = wrapper['value']
        acceptance = next(row for row in contract['acceptance'] if row['id'] == aid)
        age = (pc.datetime.datetime.now(pc.datetime.timezone.utc) - pc.datetime.datetime.fromisoformat(wrapper['timestamp'])).total_seconds()
        if not 0 <= age <= acceptance['freshness_seconds']:
            continue  # Expired proof needs fresh observation, not an assumed repair.
        if not (result['result'] == 'failed' and result['mode'] == 'real' and result['implementation'] == 'implemented'
                and result['reachable'] and result['enabled'] and not result['findings'] and not result['limitations']):
            continue  # Authority findings stay in their existing review custody.
        pc.validate_result(store, data, result)
        affected = {rid for rid, row in requirements.items() if aid in row['acceptance']}
        unit = next((row for row in value['work'] if affected & set(row['requirements'])), None)
        need(unit, 'failed acceptance has no approved repair assignment')
        request = {'id': 'repair-' + wrapper['hash'][:40], 'class': 'defect', 'unit': unit['id'],
                   'reason': 'Approved real oracle ' + aid + ' failed at the integrated candidate; repair within existing validation custody',
                   'requirements': sorted(affected & set(unit['requirements'])), 'before': head,
                   'after': 'Pending corrected candidate and fresh exact-head verification',
                   'evidence': result['artifacts'], 'supersedes': [], 'failed_result': wrapper['hash']}
        repairs.append(change(store, bid, request)['id'])
    return repairs


def repair_allocated(store, bid, payload):
    keys(payload, {'id'})
    ident(payload['id'])
    with build_lock(store), store.lock():
        _, value = owner_binding(store, bid)
        project = seeded_project(store, bid, value)
        data = store.load(value['contract'])
        changes = []
        for unit in value['work']:
            if safe(store.state / (unit['task'] + '.meta')).exists():
                _, wt = worker(store, unit['task'], project, value)
                changes.extend(automatic_repairs(store, bid, value, data, git(wt, 'rev-parse', 'HEAD')))
    # Reuse immutable classification/action replay and the ordinary text plane;
    # never keep ledger locks while the recipient's lifecycle is inspected.
    actions = [enact_change(store, bid, {'id': 'repair-' + pc.digest({'request': payload['id'], 'change': cid})[:40], 'change': cid}) for cid in dict.fromkeys(changes)]
    return {'build': bid, 'changes': list(dict.fromkeys(changes)), 'actions': actions}


def finish_build(store, bid, payload):
    keys(payload, {'id', 'debts'}, {'delivery'})
    ident(payload['id'])
    debts = pc.rows(payload['debts'], 'record debts')
    for item in debts.values():
        keys(item, {'id', 'artifact', 'reason', 'followup'})
        for field in ('artifact', 'reason', 'followup'):
            pc.string(item[field], 'record debt/' + field)
    mapping = refresh_map(store, bid, {'id': 'final-' + pc.digest(payload)[:40], 'stage': 'final'})
    with build_lock(store), store.lock():
        _, value = owner_binding(store, bid)
        project = seeded_project(store, bid, value)
        candidate = candidate_observation(project)
        data = store.load(value['contract'])
        selected = [row for row in pc.current(data)['contract']['requirements'] if row['state'] == 'accepted']
        inventory = {row['id']: sorted(row['acceptance']) for row in selected}
        product, delivery, failures = None, None, []
        try:
            need(not candidate['dirty'], 'integrated candidate has uncommitted behavior; commit and reverify exact head')
            need(not pending_authority(store, bid, value, inventory), 'owner input pending for affected behavior')
            product = pc.evaluate(store, data, {'head': candidate['head'], 'level': 'product'})
            need(product['verified_acceptance'] == sorted({aid for ids in inventory.values() for aid in ids}), 'incomplete accepted obligation inventory')
        except pc.Invalid as error:
            failures.append(str(error))
        if product and 'delivery' in payload:
            try:
                delivery = pc.evaluate(store, data, {'head': candidate['head'], 'level': 'delivery', 'delivery': payload['delivery']})
            except pc.Invalid as error:
                failures.append(str(error))
        repairs = automatic_repairs(store, bid, value, data, candidate['head']) if not product and not candidate['dirty'] and not pending_authority(store, bid, value, inventory) else []
        changes = change_records(store, bid, value)
        execution_record = execution(store, bid, value)
        history = [execution_record]
        history_directory = root(store) / 'execution-history' / bid
        base, wrappers = approved_lineage(store, bid, value['digest'])
        allowed = {base['hash']} | {row['artifact']['hash'] for row in wrappers}
        if safe(history_directory).exists():
            for path in sorted(history_directory.glob('*.json')):
                prior = read(path)
                need(prior['build'] == bid and prior['artifact'] in allowed and prior['integrity'] == pc.digest({k: v for k, v in prior.items() if k != 'integrity'}), 'corrupt or foreign retained execution history')
                history.append(prior)
        handoff_hashes = {digest for row in history for digest in row['handoffs']}
        handoffs = []
        for path in sorted((root(store) / 'handoffs' / bid).glob('*.json')):
            row = read(path)
            need(row['hash'] in handoff_hashes and row['hash'] == pc.digest({k: v for k, v in row.items() if k != 'hash'}), 'final handoff integrity mismatch')
            handoffs.append(row)
        need({row['hash'] for row in handoffs} == handoff_hashes, 'missing retained final handoff')
        resolved_questions = {row['change']: row['event'] for wrapper in wrappers for row in wrapper['resolutions']}
        # Preserve immutable change declarations, including superseded failed
        # attempts, and separately attach exact final-head verification. A repair
        # declaration's pending `after` text is not evidence of a repaired build.
        latest_results = {row['value']['acceptance']: row for row in data['results']
                          if row['digest'] == value['digest'] and row['value']['head'] == candidate['head']}
        change_verification = []
        for change in changes:
            if change['authority'] != 'dedicated-secondmate':
                continue
            aids = sorted({aid for rid in change['requirements'] for aid in inventory.get(rid, [])})
            verified = bool(product and aids)
            change_verification.append({'change': change['id'], 'declaration': change['hash'],
                'after_head': candidate['head'], 'acceptance': aids,
                'outcome': 'verified-at-final-candidate' if verified else 'unverified-or-superseded-scope',
                'results': [{'acceptance': aid, 'hash': latest_results[aid]['hash'],
                             'artifacts': latest_results[aid]['value']['artifacts']} for aid in aids] if verified else [],
                'record_debts': sorted(debts)})
        outcome = {'build': bid, 'artifact': value['hash'], 'digest': value['digest'], 'candidate': candidate,
                   'inventory': inventory, 'product': product or 'not-accepted', 'delivery': delivery or 'not-delivered',
                   'failures': failures, 'map': mapping, 'record_debts': list(debts.values()),
                   'changes': [row for row in changes if row['authority'] == 'dedicated-secondmate'],
                   'change_verification': change_verification,
                   'owner_questions': [dict(row, resolved_by_owner_event=resolved_questions.get(row['id'])) for row in changes if row['authority'] == 'owner-required'],
                   'unresolved_owner_questions': pending_authority(store, bid, value, inventory), 'handoffs': handoffs,
                   'repair_changes': repairs}
        directory = root(store) / 'final' / bid / payload['id']
        immutable(directory / 'summary.json', outcome)
        lines = ['Build ' + bid, 'Candidate ' + candidate['head'],
                 'Product: ' + ('working; accepted behavior' if product else 'NOT accepted'),
                 'Delivery: ' + ('complete' if delivery else 'not proved'),
                 'Authorized changes: ' + (', '.join(row['id'] for row in outcome['changes']) or 'none (zero changes)'),
                 'Handoffs: ' + (', '.join(row['id'] for row in handoffs) or 'none'),
                 'Record reconciliation is separate from working behavior.', 'Complete retained inventory and evidence:']
        lines.extend(pc.json.dumps(outcome, sort_keys=True, indent=2, ensure_ascii=True).splitlines())
        text = '\n'.join(piece for line in lines for piece in (textwrap.wrap(line, width=72, replace_whitespace=False, drop_whitespace=True) or [''])) + '\n'
        summary = directory / 'summary.txt'
        if safe(summary).exists():
            need(summary.read_text() == text, 'immutable final report differs')
        else:
            summary.write_text(text); summary.chmod(0o600)
    repair_actions = []
    for cid in repairs:
        try:
            repair_actions.append(enact_change(store, bid, {'id': 'auto-' + pc.digest({'build': bid, 'change': cid})[:40], 'change': cid}))
        except pc.Invalid as error:
            repair_actions.append({'change': cid, 'outcome': 'blocked', 'reason': str(error)})
    outcome['repair_actions'] = repair_actions
    proc = subprocess.run([sys.executable, str(SCRIPTS / 'fm-product-report.py'), 'publish', 'final-' + pc.digest({'build': bid, 'id': payload['id']})[:40], str(summary), '--build', bid], text=True, capture_output=True, check=False)
    publication = pc.parse(proc.stdout) if proc.returncode == 0 else None
    outcome['publication'] = {'outcome': 'published' if publication and publication['next'] == len(publication['chunks']) else 'pending',
                              'receipt': publication, 'error': proc.stderr.strip() if proc.returncode else None,
                              'retry': 'repeat finish with the same id and request; existing local report poll retries retained pages'}
    return outcome


def candidate_observation(project):
    proc = subprocess.run(['git', '-C', str(project), 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], capture_output=True, check=False)
    need(proc.returncode == 0, 'cannot enumerate candidate files')
    inputs = []
    for raw in sorted(set(proc.stdout.split(b'\0')) - {b''}):
        relative = os.fsdecode(raw)
        path = project / relative
        if path.is_symlink():
            content = 'symlink-not-read'
        elif not path.exists():
            content = 'deleted'
        elif path.is_file():
            digest = pc.hashlib.sha256()
            with safe(path).open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            content = digest.hexdigest()
        else:
            content = 'nonregular-not-read'
        inputs.append({'path': relative, 'content': content})
    return {'head': git(project, 'rev-parse', 'HEAD'), 'dirty': git(project, 'status', '--porcelain'), 'inputs': pc.digest(inputs)}


def map_path(store, bid, mid):
    return root(store) / 'maps' / ident(bid) / (ident(mid) + '.json')


def refresh_map(store, bid, payload):
    keys(payload, {'id', 'stage'})
    need(payload['stage'] in {'start', 'wave', 'final'}, 'unknown map boundary')
    with build_lock(store), store.lock():
        _, value = owner_binding(store, bid)
        project = seeded_project(store, bid, value)
        candidate = candidate_observation(project)
        path = map_path(store, bid, payload['id'])
        if safe(path).exists():
            prior = read(path)
            need(prior['request'] == payload and prior['candidate'] == candidate, 'map attempt already reserved for a different boundary/candidate; retry with a new id')
            return prior
        record = {'build': bid, 'artifact': value['hash'], 'request': payload, 'candidate': candidate,
                  'at': pc.now(), 'outcome': 'unavailable', 'debt': 'interrupted refresh; retry with a new immutable attempt id'}
        pc.atomic(path, record)
    # The GRAPH owner alone selects supported source, pin, private corpus,
    # network isolation and output. Refresh never installs or uploads anything.
    command = [sys.executable, str(SCRIPTS / 'fm-product-graph.py'), 'extract', '--project', str(project),
               '--private-root', str(root(store) / 'graph' / bid)]
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    try:
        receipt = pc.parse(proc.stdout)
        need(isinstance(receipt, dict), 'graph adapter returned no typed receipt')
    except (ValueError, pc.Invalid):
        receipt = {'outcome': 'failed', 'reason': 'graph adapter did not return a typed receipt', 'exit': proc.returncode}
    with build_lock(store), store.lock():
        owner_binding(store, bid)
        record['adapter'] = receipt
        status = receipt.get('outcome')
        record['outcome'] = status if status in {'success', 'partial', 'unsupported', 'failed', 'unavailable'} else 'unavailable'
        record['finished'] = pc.now()
        stable = candidate == candidate_observation(project)
        # Check exact copied supported inputs again, not only porcelain names.
        # A file can change repeatedly while keeping the same dirty status.
        if record['outcome'] in {'success', 'partial'}:
            try:
                manifest_path = safe(receipt['manifest'])
                need(manifest_path.is_relative_to(root(store) / 'graph' / bid), 'graph manifest outside this build')
                manifest = pc.parse(manifest_path.read_bytes())
                for row in manifest['included']:
                    src = safe(project / row['path'])
                    need(src.is_relative_to(project) and src.is_file(), 'graph input no longer exists')
                    need(pc.hashlib.sha256(src.read_bytes()).hexdigest() == row['sha256'], 'graph input changed during extraction')
                graph = safe(receipt['graph'])
                need(graph.is_relative_to(root(store) / 'graph' / bid) and pc.hashlib.sha256(graph.read_bytes()).hexdigest() == receipt['graph_sha256'], 'graph output hash mismatch')
                need(stable, 'candidate changed during map extraction')
                record['debt'] = None
            except (pc.Invalid, OSError, KeyError, ValueError) as error:
                record['outcome'] = 'stale'
                record['debt'] = str(error)
        else:
            record['debt'] = receipt.get('reason', 'no supported map; coverage remains explicit')
        record['retry'] = None if record['debt'] is None else {'owner': 'dedicated-build-secondmate', 'command': 'refresh', 'stage': payload['stage'], 'new_attempt_required': True}
        pc.atomic(path, record)
    return record


def map_history(store, bid):
    _, value = owner_binding(store, bid)
    directory = root(store) / 'maps' / ident(bid)
    records = [read(path) for path in sorted(directory.glob('*.json'))] if safe(directory).exists() else []
    base, wrappers = approved_lineage(store, bid, value['digest'])
    allowed = {base['hash']} | {row['artifact']['hash'] for row in wrappers}
    need(all(row['build'] == bid and row['artifact'] in allowed for row in records), 'foreign map history')
    return {'build': bid, 'baseline': value['baseline'], 'attempts': records,
            'limits': 'Only the named immutable candidate/input/output observation is covered; maps never certify behavior. Failed attempts never make a prior baseline current.'}


def action_path(store, bid, aid):
    return root(store) / 'actions' / ident(bid) / (ident(aid) + '.json')


def reserve_action(store, bid, payload, kind):
    _, value = owner_binding(store, bid)
    path = action_path(store, bid, payload['id'])
    if safe(path).exists():
        record = read(path)
        need(record['kind'] == kind and record['request'] == payload and record['artifact'] == value['hash'], 'conflicting action replay')
        return path, record, False
    record = {'build': bid, 'artifact': value['hash'], 'kind': kind, 'request': payload,
              'outcome': 'reserved-reconcile-before-retry'}
    return path, record, True


def dispatch_action(store, bid, payload):
    keys(payload, {'id', 'profiles'})
    require_report(store, bid)
    path, action, fresh = reserve_action(store, bid, payload, 'dispatch')
    if not fresh:
        return path, action, []
    _, value = owner_binding(store, bid)
    ready = {row['unit']: row for row in frontier(store, bid)['units'] if row['state'] == 'ready'}
    profiles = pc.rows(payload['profiles'], 'profiles')
    need(profiles and set(profiles) <= set(ready), 'dispatch only dependency-ready unallocated approved units')
    commands = []
    for uid, profile in profiles.items():
        keys(profile, {'id', 'harness', 'model', 'effort', 'dispatch_override'})
        for field in ('harness', 'model', 'effort', 'dispatch_override'):
            pc.string(profile[field], 'dispatch/' + field)
        # Existing dispatch admission owns supported profiles, quota, accounts,
        # capacity and backend selection. Never run an arbitrary command here.
        command = ['bash', str(SCRIPTS / 'fm-spawn.sh'), ready[uid]['task'], str(seeded_project(store, bid, value)),
                   '--mode', value['mode'], '--yolo', value['yolo'], '--branch-prefix', value['branch_prefix'],
                   '--harness', profile['harness'], '--model', profile['model'], '--effort', profile['effort'],
                   '--dispatch-override', profile['dispatch_override']]
        if value['base_branch'] and value['mode'] != 'local-only':
            command += ['--base-branch', value['base_branch']]
        elif value['base_branch']:
            # Local-only lands the default copy; its ordinary spawn interface
            # intentionally rejects a PR target flag, even when it says main.
            need(git(seeded_project(store, bid, value), 'symbolic-ref', '--short', 'HEAD') == value['base_branch'],
                 'local-only dispatch must use its approved default branch')
        commands.append({'unit': uid, 'task': ready[uid]['task'], 'command': command})
    action['commands'] = commands
    pc.atomic(path, action)
    return path, action, commands


def replacement_action(store, bid, payload):
    keys(payload, {'id', 'handoff'})
    path, action, fresh = reserve_action(store, bid, payload, 'replacement')
    if not fresh:
        return path, action, []
    _, value = owner_binding(store, bid)
    checkpoint = read(root(store) / 'handoffs' / ident(bid) / (ident(payload['handoff']) + '.json'))
    record = execution(store, bid, value)
    need(checkpoint['hash'] in record['handoffs'] and checkpoint['hash'] == pc.digest({k: v for k, v in checkpoint.items() if k != 'hash'}), 'handoff integrity mismatch')
    need(checkpoint['artifact'] == value['hash'], 'handoff approval changed')
    project = seeded_project(store, bid, value)
    meta, wt = worker(store, checkpoint['task'], project, value)
    need(meta['spawn_gen'] == checkpoint['incarnation'], 'handoff no longer describes the current worker')
    need(meta.get('backend') in {'tmux', 'herdr', 'rex'}, 'backend cannot prove a fresh replacement; preserve checkpoint and request supported recovery')
    need(not (meta.get('backend') == 'herdr' and meta.get('harness') == 'pi-signed'), 'fresh Pi-signed replacement is not verified; preserve the handoff rather than resume its conversation')
    fresh_context = meta.get('backend') == 'herdr' and meta.get('harness') == 'pi'
    if fresh_context:
        need(all(meta.get(field) for field in ('window', 'herdr_session', 'herdr_workspace_id', 'herdr_tab_id', 'herdr_pane_id')),
             'fresh Herdr replacement requires complete recorded endpoint identity')
    need(git(wt, 'rev-parse', 'HEAD') == checkpoint['head'] and git(wt, 'status', '--porcelain') == checkpoint['dirty'], 'worker changed after checkpoint; capture a new handoff before replacement')
    note = root(store) / 'handoffs' / bid / (payload['handoff'] + '.note.txt')
    text = ('Continue only this same approved assignment in a fresh context. Read the complete durable checkpoint below, including accepted inputs, proof hashes, decisions, defects, dirty work and next action. Preserve existing validation custody.\n'
            + pc.canonical(checkpoint).decode() + '\n')
    if safe(note).exists():
        need(note.read_text() == text, 'handoff note differs')
    else:
        note.write_text(text)
        note.chmod(0o600)
    commands = [{'unit': checkpoint['unit'], 'task': checkpoint['task'], 'prior_incarnation': meta['spawn_gen'],
                 'command': ['bash', str(SCRIPTS / 'fm-control.sh'), checkpoint['task'], 'relaunch', '--note-file', str(note)] + (['--fresh-context'] if fresh_context else [])}]
    action['commands'] = commands
    pc.atomic(path, action)
    return path, action, commands


def perform_action(store, bid, payload, kind):
    # Never retain build/ledger locks while invoking existing launch/control:
    # their preallocation guard acquires these same locks in a child process.
    with build_lock(store), store.lock():
        prepare_action = dispatch_action if kind == 'dispatch' else replacement_action
        path, action, commands = prepare_action(store, bid, payload)
    if not commands:
        return action
    if kind == 'dispatch':
        refresh_map(store, bid, {'id': 'dispatch-' + pc.digest(payload)[:40], 'stage': 'wave'})

    def invoke(item):
        try:
            proc = subprocess.run(item['command'], text=True, capture_output=True, check=False)
            return dict(item, exit=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
                        outcome='launched' if proc.returncode == 0 else 'queued-capacity' if proc.returncode == 75 else 'failed-reconcile')
        except OSError as error:
            return dict(item, exit=None, outcome='failed-reconcile', error=str(error))

    with ThreadPoolExecutor(max_workers=len(commands)) as pool:
        results = list(pool.map(invoke, commands))
    with build_lock(store), store.lock():
        # A successful control return is the existing owner's structural fresh
        # replacement guarantee; a changed token alone is never such proof.
        if kind == 'replace':
            for result in results:
                if result['exit'] == 0:
                    meta = pc.read_meta(safe(store.state / (result['task'] + '.meta')))
                    need(meta.get('spawn_gen') and meta['spawn_gen'] != result['prior_incarnation'], 'replacement did not publish a new incarnation; reconcile reserved action')
                    result['incarnation'] = meta['spawn_gen']
                    result['fresh_context_owner'] = 'fm-control verified distinct registered Pi session' if '--fresh-context' in result['command'] else 'fm-control verified relaunch'
        action['results'] = results
        action['outcome'] = 'finished' if all(row['exit'] == 0 for row in results) else 'reconcile-results'
        pc.atomic(path, action)
    return action


HELP = '''Dedicated product build artifact/admission and execution checkpoints (private local interfaces).
Usage: FM_HOME=/absolute/home python3 bin/fm-product-build.py COMMAND BUILD
Strict JSON stdin, JSON stdout. No artifact content is executed.
  emit     {contract,project,mode,yolo,branch_prefix,base_branch,baseline}
           Parent emits immutable current-approved artifact, exact work roster,
           baseline observation and delivery selection. No implementation launch.
  verify   {} verifies current approval, approved roster and artifact digest.
  reserve  {mate,home} parent atomically reserves one already-seeded registered
           local secondmate for this build for life. Same-build replay is safe.
  import   {parent,parent_data?,parent_state?} mate imports only its exact
           reserved artifact and approval; name nondefault parent overrides
           explicitly. Replay preserves existing observations and grant usage.
  budget-custody {command:budget|budget-finish,contract,request,expected_digest?}
           internal adapter used automatically by the shared contract CLI in
           dedicated homes. The parent's ledger reserves/settles the original
           grant across all builds; keys are scoped by immutable build identity.
           Imported budget snapshots never create a second grant ceiling, and a
           confirmed parent reservation stays authoritative if projection lags.
           Settlement may reconcile a prior operation during a semantic pause;
           new reservations require exact current approval at the parent.
  show     {} verifies and displays this dedicated home's imported binding.
  revise   {digest,previous,resolutions:[{change,event}]} parent publishes an
           immutable approved successor of the same lifetime build/project and
           delivery identity. Each resolution names new exact owner words in
           that independently approved intent, never a blanket approval event.
  import-revision {digest} dedicated owner stops only its prior exact approved
           direct-report roster through existing verified exit, then imports
           the parent's current approved successor. Source/revision histories
           must extend retained history; divergent proof or budget journals
           refuse rather than dropping or resetting consumption. Proof bytes,
           old ledgers, plans, receipts, workers and handoffs remain retained.
           Interrupted transitions block dispatch and resume idempotently;
           successful replay never stops successor workers. Rebind revised task
           bodies through the shared engine and prepare the revised approved
           roster before launch. Questions resolved by exact owner events are
           reconciled through the parent channel; no independent operation or
           landing authority is granted.
  prepare  {integration_unit,slices:[{id,inputs,interface,non_goals,scope,
           checkpoint}]} binds bounded execution details to the entire approved
           work roster and already-bound exact task IDs. No new task aliases.
  change   {id,class:defect|technical|semantic|owner-only,unit,reason,
           requirements,before,after,evidence:[{path,sha256}],supersedes,
           failed_result?} retains immutable attributed changes. Defects require
           a failed approved result hash and stay in existing validation custody.
           Technical overlays never rewrite approved semantics. Semantic/owner-
           only changes stop affected dispatch and require exact parent input;
           no implementation worker can resolve them through this interface.
           Defect repair steers and owner questions are enacted automatically.
  act-change {id,change} durable explicit action retry/reconciliation interface;
           interrupted reservations never resend blindly. Technical records need
           no execution. Semantic changes remain stopped until the parent has
           published and the owner has imported a newly approved revision with
           exact owner-event resolution, then rebound the affected task bodies.
  finish   {id,debts:[{id,artifact,reason,followup}],delivery?} attempts final map,
           inventories every accepted requirement/acceptance, evaluates real
           behavior with the shared engine and separately evaluates optional
           exact-head delivery proof. Retains all authorized changes, superseded
           attempts and handoffs, and queues the complete report through REPORT.
           Record/map/report debt never revokes passing behavior. Same id replays
           exact summary publication; changed candidates/debts need a new id.
  refresh  {id,stage:start|wave|final} attempts the existing local GRAPH adapter
           in data/product-build/graph/<build>, which must already be set up
           under separate pin/install authority. Retains immutable attempts,
           exact supported dirty input/output hashes, coverage and retry debt.
           Same-id replay never performs a duplicate extraction; use a new id
           for retry. Interrupted attempts remain explicitly unavailable.
           prepare attempts the deferred-start boundary; dispatch and integrate
           attempt wave boundaries automatically without blocking on map debt.
  maps     {} retains the initial baseline and all refresh observations without
           treating a failed/stale map as current or as behavioral evidence.
  dispatch {id,profiles:[{id:unit,harness,model,effort,dispatch_override}]}
           reserves a durable action before parallel existing fm-spawn calls.
           Consult each task's dispatch rules first; explain the resolved profile
           in dispatch_override. Existing admission owns account/quota/capacity.
           Replay never launches twice; interrupted reservations need exact task
           reconciliation, not an assumed retry. Exit 75 remains queued.
  replace  {id,handoff} checkpoints through existing fm-control relaunch with the
           retained handoff note. Keeps exact assignment, isolated copy and
           validation custody; refuses unsupported fresh-context backends or a
           worker that changed after its checkpoint. Herdr Pi requires
           fm-control --fresh-context: positively absent registration after exit,
           an agent-free shell and a distinct new registered session. Legacy
           stale authority refuses rather than resuming its old conversation.
           Pi-signed fresh replacement is not verified and refuses. A new incarnation
           alone is not context proof. No utilization fabricated.
  action   {id} reads one durable launch/replacement action after interruption.
  wave     {} lists all dependency-ready approved tasks without serializing
           overlapping files. Dispatch them with the existing fm-spawn CLI;
           its existing capacity refusal leaves work queued. Allocated records
           require existing direct-report reconciliation before retry.
  integrate {unit,integrator,head,proof:{path,sha256},supersedes?} records only a named
           integration worker's clean integrated candidate containing the
           implementation commit, after the shared exact-head slice evaluator.
           Never edits a project or substitutes this receipt for landing.
           Corrected candidates append receipts naming the exact latest hash
           in supersedes; dependency consumers use the latest accepted receipt.
  repair   {id} inspect only approved allocated workers' exact heads and retained
           failed oracles, classify in-scope defects and steer existing workers
           automatically. Missing proof is not a fabricated defect.
  offer    {id,unit,units,head} retain a clean ready local integration offer.
           Every included approved unit needs fresh exact-head acceptance.
           Parent lands via fm-merge-local.sh TASK --build-delivery BUILD ID
           [AUTHORITY_EVENT]. Requires explicit parent exact-head authority
           unless artifact yolo is on; never merges the child default copy.
           Metadata stays outside live parent inventory. Confirmed parent
           landing permits existing guarded fleet sync of the child local copy.
  delivery-meta {id,authority_event?} internal parent merge preflight.
  confirm-delivery {id,before,head} internal guarded-merge receipt producer.
  sync-guard {project} internal read-only local-copy sync admission.
  ready-successor {task,before,head} internal readiness guard: only the named
           local integrator in a fresh incarnation may advance a previously
           landed and accepted wave's latch, preserving its immutable receipt.
           The existing readiness owner retains its metadata locks and writes
           the new latch only after fresh exact-head slice acceptance.
  handoff  {id,unit,commits,artifacts:[{path,sha256}],decisions,defects,
           next_action,validation_custody} immutable checkpoint of exact binding,
           accepted inputs, incarnation, clean/dirty work and bounded next action.
           Context utilization stays unknown without a structural runtime source;
           the owner must use existing fresh same-assignment replacement, never
           treat an incarnation token or a resumed conversation as context proof.
  route    {home,project} parent verifies the exact imported local-only owner
           before the existing home seeder validates or clones that project.
  stamp-project {home,project} after the existing seeder clones from the exact
           local source, persist immutable clone provenance under its .git.
  guard    TASK {kind,project,mode,yolo} preallocation spawn admission. Dedicated
           homes permit only approved ship tasks in the provenance-bound clone;
           parents verify a reserved owner before launch and cannot dispatch
           that owner's exact approved task roster themselves. Unrelated
           maintenance keeps ordinary admission; project overlap is not a lock.
Private paths under FM_DATA_OVERRIDE (default FM_HOME/data):
  product-build/artifacts/<build>.json immutable typed artifact, canonical SHA-256
  product-build/owners.json parent lifetime index, integrity digest, atomic replace
  product-build/.lock parent/home advisory serialization
FM_HOME/.fm-product-build.json is the imported lifetime owner binding.
Approved ledgers remain owned by fm-product-contract.py; their checks and exact
work task IDs are consumed exactly, never invented as aliases. Only dispatch,
replace and revision-stop adoption invoke the existing lifecycle owners;
execution checkpoints and launch success never certify builds.
Reservation does not seed/launch a secondmate, free an identity, mutate a
project, or grant merge/publication/installation authority. Local-only routing
requires route permission in both existing seed guards and clone provenance;
parent-owned landing and ordinary domain mates are unchanged.
Recognized FM_TASK_ID values in the approved build roster cannot invoke owner
mutations, authority classifications, final acceptance or lifecycle actions.
Workers report findings through their own status/inbox and consume budgets
through the shared contract API; they never answer their own authority finding.
Remote dedicated builds are not supported. The contract engine's local
owner-channel trust boundary applies; these digests detect accidental corruption,
not malicious modification by the same OS user.
'''


def assert_owner_caller(store, command):
    if command in {'show', 'verify', 'maps', 'action', 'budget-custody'}:
        return
    actor = os.environ.get('FM_TASK_ID')
    if not actor:
        return  # Interactive owner channel, not same-UID tamper protection.
    ident(actor, 'worker marker')
    meta = pc.read_meta(safe(store.state / (actor + '.meta')))
    need(meta.get('kind') not in {'ship', 'scout'}, 'implementation worker cannot classify its own authority finding or perform dedicated-owner lifecycle/acceptance actions')
    marker = safe(store.home / '.fm-product-build.json')
    if marker.exists():
        builds = [read(marker)['build']]
    else:
        _, index = owners(store)
        builds = [row['build'] for row in index]
    for bid in builds:
        base = original_artifact(store, bid)
        data = store.load(base['contract'])
        tasks = {row['task'] for revision in data['revisions'] for row in revision['contract']['work']}
        need(actor not in tasks, 'implementation worker cannot classify its own authority finding or perform dedicated-owner lifecycle/acceptance actions')


def run(args, payload):
    store = pc.Store()
    assert_owner_caller(store, args.command)
    if args.command == 'budget-custody':
        return budget_custody(store, args.build, payload)
    if args.command == 'delivery-meta':
        return delivery_meta(store, args.build, payload)
    if args.command == 'import-revision':
        local_binding(store, args.build)
        with revision_adoption_lock(store, args.build):
            return import_revision(store, args.build, payload)
    if args.command == 'finish':
        return finish_build(store, args.build, payload)
    if args.command == 'repair':
        return repair_allocated(store, args.build, payload)
    if args.command == 'act-change':
        return enact_change(store, args.build, payload)
    if args.command == 'refresh':
        return refresh_map(store, args.build, payload)
    if args.command in {'dispatch', 'replace'}:
        return perform_action(store, args.build, payload, args.command)
    with build_lock(store), store.lock():
        if args.command == 'action':
            keys(payload, {'id'})
            owner_binding(store, args.build)
            return read(action_path(store, args.build, payload['id']))
        if args.command == 'revise':
            return revise(store, args.build, payload)
        if args.command == 'emit':
            return emit(store, args.build, payload)
        if args.command == 'reserve':
            return reserve(store, args.build, payload)
        if args.command == 'import':
            return import_build(store, args.build, payload)
        if args.command == 'route':
            return route(store, args.build, payload)
        if args.command == 'stamp-project':
            return stamp_project(store, args.build, payload)
        if args.command == 'guard':
            return guard(store, args.build, payload)
        if args.command == 'change':
            return change(store, args.build, payload)
        if args.command == 'prepare':
            return prepare(store, args.build, payload)
        if args.command == 'integrate':
            return integrate(store, args.build, payload)
        if args.command == 'offer':
            return offer_delivery(store, args.build, payload)
        if args.command == 'confirm-delivery':
            return confirm_delivery(store, args.build, payload)
        if args.command == 'sync-guard':
            return sync_guard(store, args.build, payload)
        if args.command == 'ready-successor':
            return ready_successor(store, args.build, payload)
        if args.command == 'handoff':
            return handoff(store, args.build, payload)
        keys(payload, set())
        if args.command == 'maps':
            return map_history(store, args.build)
        if args.command == 'wave':
            return frontier(store, args.build)
        if args.command == 'verify':
            return artifact(store, args.build)[0]
        return show(store, args.build)


def main():
    if '--help' in sys.argv or len(sys.argv) == 1:
        print(HELP); return 0
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('command', choices=['emit', 'verify', 'reserve', 'import', 'show', 'route', 'stamp-project', 'guard', 'prepare', 'wave', 'integrate', 'handoff', 'dispatch', 'replace', 'action', 'refresh', 'maps', 'change', 'act-change', 'finish', 'revise', 'import-revision', 'budget-custody', 'offer', 'delivery-meta', 'confirm-delivery', 'sync-guard', 'repair', 'ready-successor'])
    parser.add_argument('build')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        ident(args.build)
        result = run(args, pc.parse(sys.stdin.read() or '{}'))
        if args.command == 'change' and result['class'] != 'technical':
            result['action'] = enact_change(pc.Store(), args.build, {'id': 'change-' + result['hash'][:40], 'change': result['id']})
        if args.command in {'prepare', 'integrate'}:
            # Boundary orchestration is outside run's locks: the GRAPH owner
            # may take its own project-private lock. Map debt never changes
            # the selected path's behavioral acceptance result.
            stage = 'start' if args.command == 'prepare' else 'wave'
            store = pc.Store()
            _, value = owner_binding(store, args.build)
            project = seeded_project(store, args.build, value)
            attempt = stage + '-' + pc.digest({'result': result, 'candidate': candidate_observation(project)})[:40]
            refresh_map(store, args.build, {'id': attempt, 'stage': stage})
        print(pc.canonical(result).decode())
        return 0
    except (pc.Invalid, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('product-build: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
