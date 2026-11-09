#!/usr/bin/env python3
"""Private structural Graphify adapter; no workflow, agent, hooks or provider calls.

Usage: python3 bin/fm-product-graph.py {probe,setup,extract} --project ABS
       --private-root ABS [--python INTERPRETER] [--authority REFERENCE]
       [--allow-install --allow-network] [--timeout SECONDS]

The private root must be outside the source project and any Git working copy.
It is bound to exactly one project. setup alone creates it, and requires explicit
installation AND package-network authority plus a recorded authority reference.
Those flags record existing permission; they do not grant permission themselves.
Only uv, the existing package installer, is used; no tool/interpreter bootstrap.
Resolver hashes and installed distribution metadata are retained under the root.
probe never installs or accepts a global executable. extract never uses network.
Each attempt retains a private receipt, corpus manifest and local JSON graph.
Exit: 0 ready/success, 2 unavailable/unsupported/partial/failed/refused.

Network isolation is mandatory: macOS sandbox-exec or Linux user+network unshare.
If the platform cannot establish it, extraction is unavailable, not a hosted or
unsandboxed fallback. A Python audit guard also denies network and child launches.
The dependency pin is trusted code, not an adversarial native-code sandbox.
Source is copied to a filtered snapshot; no project config or code is executed.
The conservative name/content exclusions are not a general secret detector.
Coverage and parse limitations remain explicit; a graph is not acceptance proof.
Freshness orchestration and final skill entry points belong to later work units.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import uuid

PIN = '0.9.102'
PACKAGE = 'graphifyy==' + PIN
BLOCK_DIRS = {'data', 'state', 'config', 'vendor', 'node_modules', 'venv', 'env',
              '__pycache__', 'dist', 'build', 'target', 'coverage', 'secrets',
              'credentials', 'fixtures', 'testdata'}
BLOCK_SUFFIXES = {'.json', '.yaml', '.yml', '.tfvars', '.dmi', '.md', '.txt',
                  '.csv', '.pem', '.key', '.crt', '.p12', '.pfx', '.log', '.sqlite', '.db'}
SECRET_NAME = re.compile(r'(?:^|[._-])(?:secret\w*|credentials?|password\w*|tokens?|api[_-]?keys?|id_rsa|id_ed25519)(?:[._-]|$)', re.I)
SECRET_CONTENT = re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})|(?:secret\w*|password\w*|api[_-]?key|access[_-]?token)\s*[=:]\s*[\x22\x27][^\x22\x27\r\n]+', re.I)
MAX_FILE = 2 * 1024 * 1024
MAX_CORPUS = 100 * 1024 * 1024


class Refused(Exception):
    pass


def need(condition, message):
    if not condition:
        raise Refused(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@contextlib.contextmanager
def operation_lock(root):
    fd = os.open(safe(root / '.operation.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('another operation owns this private project environment; retry after it completes')
        yield
    finally:
        os.close(fd)


def safe(path):
    path = Path(os.path.abspath(path))
    for p in [*reversed(path.parents), path]:
        need(not p.is_symlink(), 'symlink path component forbidden: ' + str(p))
    return path


def write_json(path, value):
    safe(path)
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, sort_keys=True, indent=2)
            f.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def clean_env(home):
    return {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(home),
            'LANG': 'en_US.UTF-8', 'LC_ALL': 'C', 'UV_CACHE_DIR': str(home / 'uv-cache'),
            'UV_PYTHON_DOWNLOADS': 'never', 'UV_NO_CONFIG': '1',
            'PYTHONDONTWRITEBYTECODE': '1'}


def run(command, cwd, timeout=120):
    return subprocess.run([str(x) for x in command], cwd=cwd, env=clean_env(cwd),
                          capture_output=True, text=True, timeout=timeout)


def isolation():
    if sys.platform == 'darwin':
        tool = shutil.which('sandbox-exec')
        if tool:
            return [tool, '-p', '(version 1)(allow default)(deny network*)'], 'macos-sandbox-network-deny'
    if sys.platform.startswith('linux'):
        tool = shutil.which('unshare')
        if tool:
            return [tool, '--user', '--map-root-user', '--net', '--'], 'linux-user-network-namespace'
    raise Refused('network isolation unavailable; install/enable the supported OS sandbox explicitly')


def private_root(project, root):
    need(project.is_dir(), 'project must be an existing directory')
    need(project != root and project not in root.parents and root not in project.parents,
         'private root and project must be disjoint')
    # Reject private output in a tracked copy, even when the path is ignored.
    parent = root
    while not parent.exists():
        parent = parent.parent
    found = run(['git', '-C', parent, 'rev-parse', '--show-toplevel'], parent)
    need(found.returncode != 0, 'private root must be outside every Git working copy')


def metadata(python, root):
    code = '''import importlib.metadata as m,json,sys,hashlib
from pathlib import Path
d=m.distribution('graphifyy')
print(json.dumps({'version':d.version,'interpreter':sys.executable,'prefix':sys.prefix,
 'metadata':str(d._path),'record_sha256':hashlib.sha256(d.read_text('RECORD').encode()).hexdigest(),
 'entry_points':[{'name':e.name,'value':e.value} for e in d.entry_points],
 'distributions':sorted((x.metadata['Name'],x.version) for x in m.distributions())}))'''
    p = run([python, '-I', '-B', '-c', code], root)
    need(p.returncode == 0, 'private distribution metadata unavailable')
    value = json.loads(p.stdout)
    need(value['version'] == PIN, 'private Graphify version mismatch: ' + value['version'])
    need(Path(value['prefix']).resolve() == (root / 'venv').resolve(), 'interpreter is not the private project environment')
    need(root / 'venv' in safe(value['metadata']).parents, 'distribution outside the private environment')
    need(any(e['name'] == 'graphify' for e in value['entry_points']), 'Graphify executable entry point missing')
    executable = root / 'venv/bin/graphify'
    need(executable.is_file() and not executable.is_symlink(), 'private graphify executable missing')
    value['executable'] = str(executable)
    value['executable_sha256'] = sha(executable.read_bytes())
    return value


def binding(project, root):
    marker = safe(root / 'project.json')
    need(marker.is_file(), 'private environment absent; run authorized setup')
    value = json.loads(marker.read_text())
    need(value == {'schema': 1, 'project': str(project), 'package': PACKAGE},
         'private environment belongs to a different project or pin')


def attempt(root, operation):
    attempts = safe(root / 'attempts')
    attempts.mkdir(mode=0o700, exist_ok=True)
    directory = attempts / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S-') + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    return directory, {'schema': 1, 'operation': operation, 'package': PACKAGE, 'started': stamp()}


def setup(args, project, root):
    need(args.allow_install and args.allow_network and args.authority,
         'setup requires existing installation and package-network authority: --allow-install --allow-network --authority REFERENCE')
    installer = shutil.which('uv')
    need(installer, 'existing uv package installer unavailable; no bootstrap attempted')
    if root.exists():
        binding(project, root)
    else:
        root.mkdir(parents=True, mode=0o700)
        write_json(root / 'project.json', {'schema': 1, 'project': str(project), 'package': PACKAGE})
    directory, receipt = attempt(root, 'setup')
    receipt.update(authority=args.authority, source_network=False,
                   package_network=True, installer=str(Path(installer).resolve()),
                   output=str(directory), receipt=str(directory / 'receipt.json'))
    try:
        version = run([installer, '--version'], directory)
        need(version.returncode == 0, 'uv version probe failed')
        receipt['installer_version'] = version.stdout.strip()
        lock = directory / 'requirements.lock'
        commands = []
        if not (root / 'venv').exists():
            commands.append([installer, 'venv', '--no-config', '--no-python-downloads',
                             '--python', args.python, str(root / 'venv')])
        else:
            safe(root / 'venv')
        commands.extend([
            [installer, 'pip', 'compile', '--no-config', '--no-build', '--generate-hashes',
             '--default-index', 'https://pypi.org/simple', '--python', str(root / 'venv/bin/python'),
             '--output-file', str(lock), '-'],
            [installer, 'pip', 'install', '--verbose', '--no-config', '--only-binary', ':all:', '--require-hashes',
             '--default-index', 'https://pypi.org/simple', '--python', str(root / 'venv/bin/python'), '-r', str(lock)]])
        receipt['commands'] = commands
        for i, command in enumerate(commands):
            p = subprocess.run(command, input=PACKAGE + '\n' if command[-1] == '-' else None,
                               cwd=directory, env=clean_env(directory), capture_output=True,
                               text=True, timeout=args.timeout)
            (directory / ('installer-%d.log' % i)).write_text(p.stdout + p.stderr)
            need(p.returncode == 0, 'package setup failed; see private installer receipt')
        receipt['resolver'] = {'index': 'https://pypi.org/simple', 'lock': str(lock),
                               'sha256': sha(lock.read_bytes()), 'artifacts': 'wheel-only, resolver SHA256 allowlist enforced'}
        receipt['environment'] = metadata(root / 'venv/bin/python', root)
        receipt['outcome'] = 'installed'
    except (Refused, OSError, ValueError, subprocess.SubprocessError) as e:
        receipt.update(outcome='failed', reason=str(e))
    receipt['finished'] = stamp()
    write_json(directory / 'receipt.json', receipt)
    if receipt['outcome'] == 'installed':
        write_json(root / 'installation.json', receipt)
    return receipt, 0 if receipt['outcome'] == 'installed' else 2


def worker_command(python, directory, mode, timeout):
    prefix, backend = isolation()
    p = run(prefix + [str(python), '-I', '-B', str(Path(__file__).resolve()),
                      '_worker', mode, str(directory)], directory, timeout)
    # Logs may contain source paths; they remain private, never printed in chat.
    (directory / ('worker-' + mode + '.log')).write_text(p.stdout + p.stderr)
    need(p.returncode == 0, 'isolated Graphify ' + mode + ' failed; see private worker log')
    result = json.loads((directory / (mode + '.json')).read_text())
    result['isolation'] = backend
    return result


def probe(root, directory, timeout):
    installation = safe(root / 'installation.json')
    need(installation.is_file(), 'private installation provenance missing; run authorized setup')
    saved = json.loads(installation.read_text())
    lock = safe(saved['resolver']['lock'])
    need(root in lock.parents and lock.is_file() and sha(lock.read_bytes()) == saved['resolver']['sha256'],
         'resolver provenance missing or changed')
    value = metadata(root / 'venv/bin/python', root)
    need(value == saved['environment'], 'private environment changed since authorized installation')
    value['capabilities'] = worker_command(root / 'venv/bin/python', directory, 'capability', timeout)
    return value


def snapshot(project, directory, extensions):
    corpus = directory / 'corpus'
    corpus.mkdir(mode=0o700)
    manifest = {'included': [], 'excluded': [], 'unsupported': [], 'parse_failed': []}
    total = 0
    for base, dirs, files in os.walk(project, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(base) / name
            rel = path.relative_to(project).as_posix()
            row = {'path': rel}
            if path.is_symlink():
                row['reason'] = 'symlink'
            elif name.startswith('.') or name.lower() in BLOCK_DIRS or SECRET_NAME.search(name):
                row['reason'] = 'private-or-vendor-name'
            elif path.is_dir():
                continue
            elif path.suffix.lower() in BLOCK_SUFFIXES:
                row['reason'] = 'non-code-or-sensitive-format'
            elif path.suffix.lower() not in extensions:
                row['reason'] = 'no-supported-code-extractor'
                manifest['unsupported'].append(row)
                continue
            else:
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, 'rb') as f:
                        st = os.fstat(f.fileno())
                        need(stat.S_ISREG(st.st_mode), 'not-regular')
                        need(st.st_size <= MAX_FILE, 'file-size-limit')
                        raw = f.read(MAX_FILE + 1)
                    need(len(raw) <= MAX_FILE and total + len(raw) <= MAX_CORPUS, 'corpus-size-limit')
                    raw.decode('utf-8')
                    need(b'\0' not in raw, 'binary')
                    need(not SECRET_CONTENT.search(raw), 'secret-pattern')
                    dest = corpus / rel
                    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    dest.write_bytes(raw)
                    dest.chmod(0o600)
                    total += len(raw)
                    manifest['included'].append({'path': rel, 'sha256': sha(raw), 'bytes': len(raw)})
                    continue
                except (Refused, OSError, UnicodeError) as e:
                    # Never copy exception text or matched secret contents into receipts.
                    row['reason'] = str(e) if isinstance(e, Refused) else type(e).__name__
            manifest['excluded'].append(row)
            if name in dirs:
                dirs.remove(name)
    manifest['source_digest'] = sha(json.dumps(manifest['included'], sort_keys=True).encode())
    return manifest


def execute(args, project, root):
    binding(project, root)
    directory, receipt = attempt(root, args.command)
    receipt.update(project=str(project), output=str(directory), source_network=False,
                   package_network=False, semantic=False, hooks=False,
                   privacy_limits='Conservative names/content filters; not a general secret detector')
    try:
        receipt['environment'] = probe(root, directory, args.timeout)
        if args.command == 'probe':
            receipt['outcome'] = 'ready'
        else:
            extensions = receipt['environment']['capabilities']['extensions']
            manifest = snapshot(project, directory, extensions)
            write_json(directory / 'manifest.json', manifest)
            receipt.update(manifest=str(directory / 'manifest.json'), source_digest=manifest['source_digest'],
                           coverage={k: len(manifest[k]) for k in ('included', 'excluded', 'unsupported', 'parse_failed')})
            if not manifest['included']:
                receipt['outcome'] = 'unsupported'
                receipt['reason'] = 'no eligible supported code; no map claimed'
            else:
                result = worker_command(root / 'venv/bin/python', directory, 'extract', args.timeout)
                manifest['parse_failed'] = result['parse_failed']
                manifest['unsupported'].extend(result['unsupported'])
                write_json(directory / 'manifest.json', manifest)
                graph = directory / 'graph.json'
                receipt.update(graph=str(graph), graph_sha256=sha(graph.read_bytes()),
                               source_digest=manifest['source_digest'], network=result['network'],
                               coverage={k: len(manifest[k]) for k in ('included', 'excluded', 'unsupported', 'parse_failed')})
                receipt['outcome'] = 'partial' if manifest['parse_failed'] or manifest['unsupported'] else 'success'
                receipt['limits'] = 'Structural symbols/edges only; no semantic completeness or acceptance claim'
    except (Refused, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        receipt.update(outcome='unavailable' if args.command == 'probe' else 'failed', reason=str(e))
    receipt['finished'] = stamp()
    write_json(directory / 'receipt.json', receipt)
    receipt['receipt'] = str(directory / 'receipt.json')
    return receipt, 0 if receipt['outcome'] in {'ready', 'success'} else 2


def worker(mode, directory):
    """Runs only in the mandatory OS network sandbox, never against original code."""
    # Observe OS denial before adding the Python guard, with no source payload.
    denied = False
    try:
        with socket.socket() as s:
            s.settimeout(0.2)
            s.connect(('198.51.100.1', 9))
    except OSError as e:
        denied = e.errno in {1, 13, 101}  # EPERM, EACCES, ENETUNREACH
    need(denied, 'OS network-denial probe did not prove enforcement')

    def audit(event, args):
        if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec', 'ctypes.dlopen'}:
            # tree-sitter's normal extension import does not use ctypes.
            raise PermissionError('network/child/foreign-library operation denied by code-only adapter')
    sys.addaudithook(audit)
    need(importlib.metadata.version('graphifyy') == PIN, 'pin changed before extraction')
    from importlib import import_module
    module = import_module('graphify.extract')
    from graphify.detect import CODE_EXTENSIONS
    network = {'os_denial_observed': True, 'python_audit_guard': True, 'source_outbound_calls': 0}
    if mode == 'capability':
        extensions = sorted(e for e in CODE_EXTENSIONS if e.lower() not in BLOCK_SUFFIXES
                            and module._get_extractor(Path('probe' + e)) is not None)
        source = directory / 'probe-corpus'
        source.mkdir()
        code = source / 'probe.py'
        code.write_text('def code_map_probe():\n    return 1\n')
        with contextlib.redirect_stdout(sys.stderr):
            result = module.extract([code], root=source, cache_root=directory / 'probe-cache', parallel=False)
        need(any(n.get('label') == 'code_map_probe()' for n in result['nodes']), 'local AST capability probe failed')
        write_json(directory / 'capability.json', {'code_only': True, 'local_output': True,
                   'api': 'graphify.extract.extract(paths, root=corpus, cache_root=private, parallel=False)',
                   'extensions': extensions, 'extension_basis': 'Pinned dispatch registry; per-file grammar/parse failures remain explicit',
                   'network': network})
        return
    corpus = directory / 'corpus'
    manifest = json.loads((directory / 'manifest.json').read_text())
    paths, failures, unsupported = [], [], []
    for row in manifest['included']:
        path = corpus / row['path']
        need(sha(path.read_bytes()) == row['sha256'], 'snapshot changed before extraction')
        extractor = module._get_extractor(path)
        if extractor is None:
            unsupported.append({'path': row['path'], 'reason': 'no-extractor'})
            continue
        try:
            # The aggregate API tolerates parser recovery; retain per-file truth.
            with contextlib.redirect_stdout(sys.stderr):
                value = module._safe_extract(extractor, path, scan_root=corpus)
            if value.get('error') or value.get('parse_errors') or value.get('skipped') or not value.get('nodes'):
                failures.append({'path': row['path'], 'reason': 'parse-error-or-declined-or-empty'})
            else:
                paths.append(path)
        except Exception as e:
            failures.append({'path': row['path'], 'reason': type(e).__name__})
    with contextlib.redirect_stdout(sys.stderr):
        graph = module.extract(paths, root=corpus, cache_root=directory / 'cache', parallel=False)
    for path in graph.pop('failed_sources', []):
        failures.append({'path': Path(path).relative_to(corpus).as_posix(), 'reason': 'aggregate-extraction-failed'})
    for path in graph.pop('skipped_sources', []):
        failures.append({'path': Path(path).relative_to(corpus).as_posix(), 'reason': 'aggregate-declined'})
    graph.pop('extracted_sources', None)
    write_json(directory / 'graph.json', graph)
    write_json(directory / 'extract.json', {'parse_failed': failures, 'unsupported': unsupported, 'network': network})


def main():
    os.umask(0o077)
    if len(sys.argv) == 4 and sys.argv[1] == '_worker':
        worker(sys.argv[2], Path(sys.argv[3]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['probe', 'setup', 'extract'])
    parser.add_argument('--project', required=True)
    parser.add_argument('--private-root', required=True)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--authority')
    parser.add_argument('--allow-install', action='store_true')
    parser.add_argument('--allow-network', action='store_true')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    try:
        need(Path(args.project).is_absolute() and Path(args.private_root).is_absolute(), 'absolute project and private root required')
        need(args.timeout > 0, 'positive timeout required')
        project, root = safe(args.project), safe(args.private_root)
        private_root(project, root)
        if args.command == 'setup':
            need(args.allow_install and args.allow_network and args.authority,
                 'setup requires existing installation and package-network authority: --allow-install --allow-network --authority REFERENCE')
            need(shutil.which('uv'), 'existing uv package installer unavailable; no bootstrap attempted')
            if not root.exists():
                root.mkdir(parents=True, mode=0o700)
                write_json(root / 'project.json', {'schema': 1, 'project': str(project), 'package': PACKAGE})
        else:
            need(not args.allow_install and not args.allow_network, 'probe/extract do not accept setup authority flags')
        binding(project, root)
        need(stat.S_IMODE(root.stat().st_mode) & 0o077 == 0, 'private root must not be group/world accessible')
        with operation_lock(root):
            if args.command == 'setup':
                result, code = setup(args, project, root)
            else:
                result, code = execute(args, project, root)
        print(json.dumps(result, sort_keys=True))
        return code
    except (Refused, OSError, ValueError, subprocess.SubprocessError) as e:
        print(json.dumps({'outcome': 'refused', 'package': PACKAGE, 'reason': str(e)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
