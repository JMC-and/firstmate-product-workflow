#!/usr/bin/env python3
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('package', ROOT / 'scripts/package.py')
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class PackageInstall(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.worktree = Path(self.temporary.name) / 'worktree'
        self.git('init', '-q', '-b', 'main', str(self.worktree))
        (self.worktree / 'seed').write_text('seed\n')
        self.git('-C', str(self.worktree), 'add', 'seed')
        self.git('-C', str(self.worktree), '-c', 'user.name=Package Test',
                 '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'seed')
        self.nested = self.worktree / 'nested'
        self.nested.mkdir()
        self.bare = Path(self.temporary.name) / 'bare.git'
        self.git('clone', '-q', '--bare', str(self.worktree), str(self.bare))

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True).strip()

    def invoke(self, source, pin=None):
        inventory = mock.Mock(return_value=[])
        overlay = mock.Mock()
        arguments = ['package.py', 'install', str(source)]
        stderr = io.StringIO()
        stdout = io.StringIO()
        patches = [mock.patch.object(sys, 'argv', arguments),
                   mock.patch.object(PACKAGE, 'inventory', inventory),
                   mock.patch.object(PACKAGE, 'overlay', overlay)]
        if pin is not None:
            patches.append(mock.patch.object(PACKAGE, 'PIN', pin))
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
                try:
                    PACKAGE.main()
                except SystemExit as error:
                    return error.code, stderr.getvalue(), inventory, overlay
        return 0, stdout.getvalue(), inventory, overlay

    def test_install_rejects_nested_directory_before_writes(self):
        code, output, inventory, overlay = self.invoke(self.nested)
        self.assertEqual(code, 2)
        self.assertIn('installation requires the worktree root', output)
        inventory.assert_not_called()
        overlay.assert_not_called()

    def test_install_rejects_bare_repository_before_writes(self):
        code, output, inventory, overlay = self.invoke(self.bare)
        self.assertEqual(code, 2)
        self.assertIn('installation requires a non-bare worktree root', output)
        inventory.assert_not_called()
        overlay.assert_not_called()

    def test_install_accepts_resolved_worktree_root(self):
        pin = self.git('-C', str(self.worktree), 'rev-parse', 'HEAD')
        code, output, inventory, overlay = self.invoke(self.worktree, pin)
        self.assertEqual(code, 0)
        self.assertIn('Installed pinned workflow overlay', output)
        inventory.assert_called_once_with()
        overlay.assert_called_once_with(self.worktree.resolve(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
