#!/usr/bin/env python3
"""Secondmate-local reporting. See --help for the interface."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

INTERVAL = 900
IDENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')


def fail(message):
    raise ValueError(message)


def safe_id(value):
    if not IDENT.fullmatch(value) or value in ('.', '..'):
        fail('invalid identifier')
    return value


def root():
    home = Path(os.environ['FM_HOME']).resolve()
    marker = home / '.fm-secondmate-home'
    if marker.is_symlink() or not marker.is_file():
        fail('reporting requires a secondmate home')
    try:
        safe_id(marker.read_text().strip())
    except (UnicodeError, ValueError):
        fail('secondmate identity is invalid')
    return home


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, sort_keys=True) + '\n')
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def herdr(session, *args):
    # Tests can select the guarded lab helper; production uses the Herdr CLI.
    helper = os.environ.get('FM_PRODUCT_REPORT_LAB_HELPER')
    if helper:
        command = [helper, 'run', session, *args]
    else:
        command = ['herdr', *args, '--session', session]
    result = subprocess.run(command, text=True, capture_output=True, timeout=10)
    if result.returncode:
        fail('terminal unavailable: ' + result.stderr.strip())
    return result.stdout


def endpoint(record):
    # Herdr's foreground-client query is its title-clear operation. Do not
    # credit a captured pane while no client is attached to the session.
    client = json.loads(herdr(record['session'], 'terminal', 'title', 'clear'))
    reason = client['result'].get('reason')
    if reason != 'cleared':
        fail('terminal has no proven foreground client')
    info = json.loads(herdr(record['session'], 'pane', 'get', record['pane']))
    pane = info['result']['pane']
    if pane['terminal_id'] != record['terminal_id'] or not pane.get('focused'):
        fail('terminal identity changed or pane is not focused')
    return herdr(record['session'], 'pane', 'read', '--source', 'visible', record['pane'])


def observe(home, workers, now):
    observations = []
    for worker in workers:
        path = Path(os.environ.get('FM_STATE_OVERRIDE', str(home / 'state'))) / (worker + '.status')
        try:
            lines = path.read_text().splitlines()
            line = lines[-1] if lines else ''
            match = re.search(r'\[at=(\d+)\]', line)
            stamp = int(match.group(1)) if match else 0
            label = 'stale' if not stamp or now < stamp or now - stamp > INTERVAL else 'observed'
            clean = re.sub(r'[^ -~]', ' ', line)[:40]
            observations.append({'worker': worker, 'at': stamp, 'freshness': label, 'status': clean or 'unknown'})
        except OSError:
            observations.append({'worker': worker, 'at': 0, 'freshness': 'unknown', 'status': 'unknown'})
    return observations


def reserve(record, home, now, slot, missed):
    obs = observe(home, record['workers'], now)
    changed = (obs != record.get('last_observations')
               or record.get('evidence') != record.get('last_evidence'))
    seq = record['sequence'] + 1
    blockers = [row['worker'] for row in obs if row['status'].startswith(('blocked', 'needs-decision'))]
    evidence = record.get('evidence')
    evidence_lines = ['Last verified evidence: unknown']
    if evidence:
        freshness = 'stale' if now < evidence['at'] or now - evidence['at'] > INTERVAL else 'observed'
        evidence_lines = [f"Evidence {freshness} at {evidence['at']}", f"sha256 {evidence['sha256']}"]
    body = [f"Build {record['build']} report {seq} slot {slot} at {now}",
            f"{'changed' if changed else 'unchanged'}; missed intervals: {missed}",
            'Blockers: ' + (', '.join(blockers) if blockers else 'unknown'), *evidence_lines]
    for row in obs:
        body.append(f"{row['worker']}: {row['freshness']} at {row['at']}: {row['status']}")
    text = '\n'.join(body) + '\n'
    record['pending'] = {'sequence': seq, 'slot': slot, 'text': text,
                         'sha256': hashlib.sha256(text.encode()).hexdigest(),
                         'observations': obs, 'evidence': evidence, 'created': now}
    feed = home / 'data' / 'product-report' / 'feed' / record['build']
    feed.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = feed / f'{seq:08d}.txt'
    target.write_text(text)
    os.chmod(target, 0o600)


def queue_publication(record, home, publication, now):
    index = publication['next']
    if index >= len(publication['chunks']):
        return
    seq = record['sequence'] + 1
    content = publication['chunks'][index]
    text = (f"Build {record['build']}\nFinal {publication['id']}\nPart {index + 1}/{len(publication['chunks'])}\n"
            + content)
    pending = {'sequence': seq, 'slot': record['last_slot'], 'text': text,
               'sha256': hashlib.sha256(text.encode()).hexdigest(), 'created': now,
               'publication': publication['id'], 'part': index, 'content_sha256': publication['sha256']}
    record['pending'] = pending
    feed = home / 'data/product-report/feed' / record['build']
    feed.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = feed / f'{seq:08d}.txt'
    target.write_text(text)
    os.chmod(target, 0o600)


def main():
    parser = argparse.ArgumentParser(
        description='Private secondmate build reports. prepare binds a dedicated Herdr pane and starts follow there; activate refuses until the exact probe is visible. tick is called by the local supervision poll. Routine reports never use the parent channel.',
        epilog='State is FM_HOME/data/product-report/active.json; feed/<build>/<sequence>.txt holds reserved bytes. Each pending record has a sequence, slot, SHA-256, observation times and exact text. Receipts are appended only after an attached foreground client, focused terminal identity and exact visible pane readback are verified. Herdr title-clear is used to verify its foreground client and clears the outer title. A failed read leaves the same report pending; show exposes the error. Restart retries it, then emits a current report with missed slots. publish retains immutable exact printable final-report bytes, each line at most 72 columns, in pages of ten lines. All pages must receive the same real focused-pane readback before its publication is complete; unavailable sink leaves progress pending across restart, stop refuses pending final work, and tick retries retained pages while preserving the next due routine report. Repeated activate never restarts the established schedule. Set FM_PRODUCT_REPORT_NOW only in controlled tests.')
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('prepare')
    prepare.add_argument('build', type=safe_id)
    prepare.add_argument('--session', required=True, type=safe_id)
    prepare.add_argument('--pane', required=True)
    prepare.add_argument('--terminal-id', required=True)
    prepare.add_argument('--worker', action='append', type=safe_id, default=[])
    publish = sub.add_parser('publish', help='Queue an exact private final report through the already-proved sink; tick/readback receipts cover every page before publication is complete')
    publish.add_argument('id', type=safe_id)
    publish.add_argument('file', type=Path)
    publish.add_argument('--build', type=safe_id, help='Require this exact active build identity')
    evidence_parser = sub.add_parser('evidence', help='Record SHA-256 of an existing private evidence file')
    evidence_parser.add_argument('file', type=Path)
    for name in ('follow', 'activate', 'tick', 'show', 'stop'):
        sub.add_parser(name)
    args = parser.parse_args()
    home = root()
    directory = home / 'data' / 'product-report'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = directory / 'active.json'
    lock = directory / 'lock'
    if args.command == 'follow':
        last = None
        while True:
            try:
                record = json.loads(state.read_text())
                pending = record.get('pending')
                if pending and pending['sha256'] != last:
                    print(pending['text'], end='', flush=True)
                    last = pending['sha256']
                if record.get('stopped'):
                    return
            except (OSError, ValueError):
                pass
            time.sleep(1)
    with lock.open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        now = int(os.environ.get('FM_PRODUCT_REPORT_NOW', time.time()))
        if args.command == 'prepare':
            if len(args.worker) > 10 or len(set(args.worker)) != len(args.worker):
                fail('workers must be unique and limited to ten per report')
            if state.exists() and not json.loads(state.read_text()).get('stopped'):
                fail('a build is already bound')
            if (directory / 'feed' / args.build).exists():
                fail('build report feed already exists')
            if not re.fullmatch(r'w[0-9]+:p[0-9]+', args.pane):
                fail('invalid Herdr pane identifier')
            if json.loads(herdr(args.session, 'terminal', 'title', 'clear'))['result'].get('reason') != 'cleared':
                fail('terminal has no proven foreground client')
            record = {'build': args.build, 'session': args.session, 'pane': args.pane,
                      'terminal_id': args.terminal_id, 'workers': args.worker,
                      'active': False, 'stopped': False, 'sequence': 0,
                      'last_slot': 0, 'start': now, 'receipts': []}
            info = json.loads(herdr(args.session, 'pane', 'get', args.pane))['result']['pane']
            if info['terminal_id'] != args.terminal_id or not info.get('focused'):
                fail('authorized pane identity is not focused')
            reserve(record, home, now, 0, 0)
            save(state, record)
            command = ['env', f'FM_HOME={home}', sys.executable, str(Path(__file__).resolve()), 'follow']
            herdr(args.session, 'pane', 'run', args.pane, *(shlex.quote(part) for part in command))
            print('Follow started in the authorized pane; activate after readback.')
            return
        record = json.loads(state.read_text())
        if args.command == 'show':
            print(json.dumps(record, indent=2, sort_keys=True))
            return
        if args.command == 'stop':
            if any(item['next'] < len(item['chunks']) for item in record.get('publications', [])):
                fail('final report still pending; retain the sink for eventual delivery')
            record['stopped'] = True
            save(state, record)
            return
        if record.get('stopped'):
            fail('reporting stopped')
        if args.command == 'publish':
            if args.build is not None and args.build != record['build']:
                fail('final report sink belongs to another build')
            if not record.get('active') or not record.get('receipts'):
                fail('final publication requires an activated proved sink')
            path = args.file.resolve(strict=True)
            if not path.is_relative_to(home / 'data') or not path.is_file() or path.stat().st_size > 1024 * 1024:
                fail('final report must be an existing private file of at most one megabyte')
            raw = path.read_bytes()
            text = raw.decode('ascii')
            if not text or not text.endswith('\n') or any(c != '\n' and not (' ' <= c <= '~') for c in text):
                fail('final report must contain printable ASCII lines with a final newline')
            lines = text.splitlines(keepends=True)
            if any(len(line.rstrip('\n')) > 72 or line.rstrip('\n') != line.rstrip() for line in lines):
                fail('final report lines must fit 72 columns without trailing whitespace; format before publication')
            digest = hashlib.sha256(raw).hexdigest()
            publications = record.setdefault('publications', [])
            prior = next((item for item in publications if item['id'] == args.id), None)
            if prior:
                if prior['sha256'] != digest or prior['text'] != text:
                    fail('immutable final publication differs')
            else:
                publications.append({'id': args.id, 'text': text, 'sha256': digest,
                                     'chunks': [''.join(lines[i:i + 10]) for i in range(0, len(lines), 10)],
                                     'next': 0, 'receipts': []})
            if not record.get('pending'):
                item = next((item for item in publications if item['next'] < len(item['chunks'])), None)
                if item:
                    queue_publication(record, home, item, now)
            save(state, record)
            print(json.dumps(next(item for item in publications if item['id'] == args.id), sort_keys=True))
            return
        if args.command == 'evidence':
            path = args.file.resolve(strict=True)
            if not path.is_relative_to(home / 'data') or not path.is_file():
                fail('evidence must be a regular file inside this home data directory')
            if path.stat().st_size > 1024 * 1024:
                fail('evidence file exceeds one megabyte')
            record['evidence'] = {'path': str(path.relative_to(home)), 'at': now,
                                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            save(state, record)
            return
        pending = record.get('pending')
        if pending:
            try:
                screen = endpoint(record)
                if pending['text'].rstrip('\n') not in screen:
                    fail('reserved report not visible')
            except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
                record['error'] = str(error)
                save(state, record)
                if args.command == 'activate':
                    fail(str(error))
                return
            record['sequence'] = pending['sequence']
            if not pending.get('publication'):
                record['last_slot'] = pending['slot']
                record['last_observations'] = pending['observations']
                record['last_evidence'] = pending.get('evidence')
            record['receipts'].append({key: pending[key] for key in ('sequence', 'slot', 'sha256', 'created')})
            record['receipts'][-1]['published'] = now
            if pending.get('publication'):
                publication = next(item for item in record['publications'] if item['id'] == pending['publication'])
                if publication['next'] != pending['part']:
                    fail('final publication progress differs from pending page')
                receipt = dict(record['receipts'][-1], part=pending['part'], content_sha256=pending['content_sha256'])
                publication['receipts'].append(receipt)
                publication['next'] += 1
                record['receipts'][-1].update(publication=publication['id'], part=pending['part'], content_sha256=pending['content_sha256'])
            record['pending'] = None
            record.pop('error', None)
        if args.command == 'activate':
            if not record['active']:
                record['active'] = True
                record['start'] = now
        elif record['active']:
            slot = max(0, (now - record['start']) // INTERVAL)
            if slot > record['last_slot']:
                reserve(record, home, now, slot, slot - record['last_slot'] - 1)
            else:
                publication = next((item for item in record.get('publications', []) if item['next'] < len(item['chunks'])), None)
                if publication:
                    queue_publication(record, home, publication, now)
        save(state, record)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print('fm-product-report: ' + str(exc), file=sys.stderr)
        sys.exit(1)
