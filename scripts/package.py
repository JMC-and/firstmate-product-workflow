#!/usr/bin/env python3
"""Install the pinned workflow overlay or run focused tests in a disposable fixture.

Usage: python3 scripts/package.py {install,test} /absolute/firstmate-checkout
install requires a clean checkout at the supported commit and explicit invocation.
It copies only inventory-listed workflow files, not tests or private Home data.
test reads the checkout's committed tree, overlays this package in a temporary
fixture, and runs the four focused upstream suites without live model launches.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PIN = '733763dd844b8101fc6f55ee6abb1a442ff19173'


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()


def inventory():
    rows = json.loads((ROOT / 'inventory.json').read_text())['files']
    for row in rows:
        path = ROOT / row['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('package integrity mismatch: ' + row['path'])
    return rows


def overlay(target, rows, tests=False):
    for row in rows:
        if not tests and row['path'].startswith('tests/'):
            continue
        path = target / row['path']
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError('symlink target refused: ' + str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / row['path'], path)


def main():
    if not hasattr(tarfile, 'data_filter'):
        raise SystemExit('Package tooling requires Python 3.12 or newer.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['install', 'test'])
    parser.add_argument('firstmate', type=Path)
    parser.add_argument('--scratch', type=Path, help='existing non-Git temporary-data directory')
    args = parser.parse_args()
    source = args.firstmate
    if not source.is_absolute() or not source.is_dir():
        parser.error('an absolute existing Firstmate checkout is required')
    if git(source, 'rev-parse', 'HEAD') != PIN:
        parser.error('only Firstmate commit ' + PIN + ' is supported')
    rows = inventory()
    if args.command == 'install':
        if git(source, 'status', '--porcelain'):
            parser.error('installation requires a clean checkout')
        overlay(source, rows)
        print('Installed pinned workflow overlay; no Home, hooks or config changed.')
        return
    # Graph privacy tests require temporary data outside every Git checkout.
    with tempfile.TemporaryDirectory(dir=args.scratch) as directory:
        target = Path(directory)
        archive = target / 'source.tar'
        subprocess.run(['git', '-C', str(source), 'archive', '--format=tar',
                        '--output=' + str(archive), PIN], check=True)
        with tarfile.open(archive) as tree:
            tree.extractall(target / 'firstmate', filter='data')
        archive.unlink()
        fixture = target / 'firstmate'
        overlay(fixture, rows, tests=True)
        temp = target / 'tmp'
        temp.mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('FM_', 'GIT_'))}
        env.update(TMPDIR=str(temp), PYTHONDONTWRITEBYTECODE='1', FM_LIVE='0',
                   GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME='Package test', GIT_AUTHOR_EMAIL='test@example.invalid',
                   GIT_COMMITTER_NAME='Package test', GIT_COMMITTER_EMAIL='test@example.invalid')
        for command in (['init', '-q', '-b', 'main'], ['add', '.'],
                        ['commit', '-qm', 'Pinned disposable test fixture']):
            subprocess.run(['git', '-C', str(fixture), *command], env=env, check=True)
        for subject in ('contract', 'build', 'graph', 'report'):
            subprocess.run(['bash', str(fixture / ('tests/fm-product-' + subject + '.test.sh'))],
                           cwd=fixture, env=env, check=True)
    print('All four focused suites passed in the pinned Firstmate fixture.')


if __name__ == '__main__':
    main()
