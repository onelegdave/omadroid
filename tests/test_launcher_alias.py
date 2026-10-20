import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phone_mirror as phone
import safe_process as commands

LAUNCHER = 'usr/bin/omarchy-launch-terminal'
PACKAGED = '/usr/share/omarchy/bin/omarchy-launch-terminal'


class LauncherAliasTests(unittest.TestCase):
    """Root-managed launcher aliases, in an isolated fixture tree.

    The fixture's owner stands in for root: ROOT and TRUSTED_UID are patched
    together, so every ownership, mode, link, and format check still runs.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / 'root'
        self.record = self.base / 'record'
        self.marker = self.base / 'replacement-ran'
        for directory in ('usr/bin', 'usr/share/omarchy/bin', 'home/user/.local/share/omarchy/bin'):
            (self.root / directory).mkdir(parents=True)
        for directory in [self.root, *self.root.rglob('*')]:
            directory.chmod(0o755)
        bash = self.root / 'usr/bin/bash'
        shutil.copyfile(os.path.realpath('/usr/bin/bash'), bash)
        bash.chmod(0o755)
        self.script(PACKAGED)
        for name, value in (('ROOT', str(self.root)), ('TRUSTED_UID', os.getuid())):
            patcher = patch.object(commands, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = patch.dict(os.environ, {'XDG_CACHE_HOME': temp.name, 'XDG_STATE_HOME': temp.name})
        environment.start()
        self.addCleanup(environment.stop)

    def script(self, path, body=None, mode=0o755):
        # A fake launcher only records its arguments; it never opens a terminal.
        target = self.root / path.lstrip('/')
        target.write_text(body or '#!/bin/bash\nprintf "%s\\n" "$@" > ' + str(self.record) + '\necho verified\n')
        target.chmod(mode)
        return target

    def link(self, target, at=LAUNCHER):
        (self.root / at).symlink_to(target)

    def resolved(self):
        with commands.verified_tool('omarchy-launch-terminal', script=True) as (path, _):
            return path

    def refused(self, error=(OSError, commands.ToolError)):
        self.assertFalse(commands.available('omarchy-launch-terminal'))
        with self.assertRaises(error):
            self.resolved()

    def run_launcher(self, args):
        with commands.launch('omarchy-launch-terminal', args, script=True) as process:
            return commands.collect(process, args, 5)

    def test_root_managed_relative_and_absolute_aliases_are_accepted(self):
        for target in ('../share/omarchy/bin/omarchy-launch-terminal', PACKAGED):
            with self.subTest(target=target):
                self.link(target)
                self.assertTrue(commands.available('omarchy-launch-terminal'))
                self.assertEqual(self.resolved(), PACKAGED)
                (self.root / LAUNCHER).unlink()

    def test_alias_launch_runs_verified_script_through_verified_bash(self):
        self.link(PACKAGED)
        result = self.run_launcher(['one two', '$(touch ' + str(self.marker) + ')'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'verified\n')
        self.assertEqual(self.record.read_text().splitlines(), ['one two', '$(touch ' + str(self.marker) + ')'])
        self.assertFalse(self.marker.exists())

    def test_alias_and_script_replaced_after_validation_cannot_redirect_launch(self):
        self.link(PACKAGED)
        spawn = commands.subprocess.Popen

        def replace_then_spawn(*args, **kwargs):
            (self.root / LAUNCHER).unlink()
            self.link('../share/omarchy/bin/replacement/omarchy-launch-terminal')
            packaged = self.root / PACKAGED.lstrip('/')
            packaged.unlink()
            self.script(PACKAGED, '#!/bin/bash\ntouch ' + str(self.marker) + '\necho replaced\n')
            return spawn(*args, **kwargs)

        with patch.object(commands.subprocess, 'Popen', side_effect=replace_then_spawn):
            result = self.run_launcher(['core'])
        self.assertEqual(result.stdout, 'verified\n')
        self.assertFalse(self.marker.exists())

    def test_alias_chain_is_bounded(self):
        def chain(hops):
            directories = ['usr/lib/chain' + str(index) for index in range(hops - 1)]
            entries = [LAUNCHER] + [d + '/omarchy-launch-terminal' for d in directories]
            for directory in directories:
                (self.root / directory).mkdir(parents=True, exist_ok=True)
                for made in (self.root / 'usr/lib', self.root / directory):
                    made.chmod(0o755)
            for entry, following in zip(entries, entries[1:] + [PACKAGED.lstrip('/')]):
                (self.root / entry).unlink(missing_ok=True)
                self.link('/' + following, entry)

        self.assertEqual(commands.LAUNCHER_ALIAS_LIMIT, 4)
        chain(4)
        self.assertEqual(self.resolved(), PACKAGED)
        chain(5)
        self.refused(commands.ToolError)

    def test_loops_and_dangling_aliases_are_refused(self):
        self.link('omarchy-launch-terminal')
        self.refused(commands.ToolError)
        (self.root / LAUNCHER).unlink()
        self.link(PACKAGED)
        (self.root / PACKAGED.lstrip('/')).unlink()
        self.link('/' + LAUNCHER, PACKAGED.lstrip('/'))
        self.refused(commands.ToolError)
        (self.root / LAUNCHER).unlink()
        self.link('/usr/share/omarchy/missing/omarchy-launch-terminal')
        self.refused(FileNotFoundError)

    def test_user_owned_home_checkout_alias_is_refused(self):
        # OmarchyMac's 3.x-to-4 upgrade links /usr/bin/omarchy-* into the
        # user's ~/.local/share/omarchy checkout. A user-owned home is untrusted.
        checkout = 'home/user/.local/share/omarchy/bin/omarchy-launch-terminal'
        self.script(checkout)
        self.link('/' + checkout)
        self.assertEqual(self.resolved(), '/' + checkout)
        user_home = os.stat(self.root / 'home/user').st_ino
        fstat = os.fstat

        def user_owned(fd):
            info = fstat(fd)
            if info.st_ino != user_home:
                return info
            fields = list(info)
            fields[4] = commands.TRUSTED_UID + 1
            return os.stat_result(fields)

        with patch.object(commands.os, 'fstat', side_effect=user_owned):
            self.refused(commands.ToolError)

    def test_symlinked_omarchy_directory_is_refused(self):
        # The same upgrade also points /usr/share/omarchy at that checkout.
        shutil.rmtree(self.root / 'usr/share/omarchy')
        self.script('home/user/.local/share/omarchy/bin/omarchy-launch-terminal')
        self.link('../../home/user/.local/share/omarchy', 'usr/share/omarchy')
        self.link('../share/omarchy/bin/omarchy-launch-terminal')
        self.refused(OSError)

    def test_writable_ancestors_and_targets_are_refused(self):
        self.link(PACKAGED)
        for path, mode in (('usr/share', 0o777), ('usr/share/omarchy/bin', 0o775),
                           (PACKAGED.lstrip('/'), 0o777), (PACKAGED.lstrip('/'), 0o4755)):
            with self.subTest(path=path, mode=oct(mode)):
                original = (self.root / path).stat().st_mode & 0o7777
                (self.root / path).chmod(mode)
                try:
                    self.refused(commands.ToolError)
                finally:
                    (self.root / path).chmod(original)
        self.assertTrue(commands.available('omarchy-launch-terminal'))

    def test_foreign_owned_alias_link_is_refused(self):
        self.link(PACKAGED)
        stat = os.stat

        def foreign_link(path, *args, **kwargs):
            info = stat(path, *args, **kwargs)
            if kwargs.get('follow_symlinks') is False:
                fields = list(info)
                fields[4] = commands.TRUSTED_UID + 1
                return os.stat_result(fields)
            return info

        with patch.object(commands.os, 'stat', side_effect=foreign_link):
            self.refused(commands.ToolError)

    def test_alias_target_must_be_the_launcher_bash_script(self):
        self.script('usr/share/omarchy/bin/other')
        cases = (
            ('/usr/share/omarchy/bin/other', None),
            ('/usr/bin/bash', None),
            ('/usr/share/omarchy/bin', None),
            (PACKAGED, '#!/bin/sh\necho no\n'),
            (PACKAGED, '#!/usr/bin/env bash\necho no\n'),
            (PACKAGED, '#!/bin/bash -p\necho no\n'),
        )
        for target, body in cases:
            with self.subTest(target=target, body=body):
                if body:
                    self.script(PACKAGED, body)
                self.link(target)
                self.refused(commands.ToolError)
                (self.root / LAUNCHER).unlink()
                self.script(PACKAGED)
        # An ELF binary renamed as the launcher is not accepted through an alias.
        shutil.copyfile(self.root / 'usr/bin/bash', self.root / PACKAGED.lstrip('/'))
        self.link(PACKAGED)
        self.refused(commands.ToolError)

    def test_launcher_alias_requires_script_mode(self):
        self.link(PACKAGED)
        with self.assertRaises(OSError):
            with commands.verified_tool('omarchy-launch-terminal'):
                pass

    def test_other_tool_aliases_stay_refused(self):
        for name in ('adb', 'scrcpy', 'pacman', 'bash'):
            with self.subTest(name=name):
                target = self.root / 'usr/share/omarchy/bin' / name
                shutil.copyfile(os.path.realpath('/usr/bin/bash'), target)
                target.chmod(0o755)
                if name == 'bash':
                    (self.root / 'usr/bin/bash').unlink()
                self.link('../share/omarchy/bin/' + name, 'usr/bin/' + name)
                self.assertFalse(commands.available(name))
                with self.assertRaises(OSError):
                    with commands.verified_tool(name, script=True):
                        pass

    def test_accepted_alias_installs_through_fixed_launch_route(self):
        self.link('../share/omarchy/bin/omarchy-launch-terminal')
        result = phone.action({'action': 'install-tools', 'group': 'core'})
        self.assertTrue(result['ok'])
        args = self.record.read_text().splitlines()
        self.assertEqual(args[:2], ['/usr/bin/env', '-i'])
        self.assertEqual(args[-5:], ['/usr/bin/bash', '--noprofile', '--norc',
                                     str(Path(phone.__file__).absolute().parent / 'install-deps.sh'), 'core'])

    def test_refused_alias_gives_manual_guidance_and_never_installs(self):
        shutil.rmtree(self.root / 'usr/share/omarchy')
        self.script('home/user/.local/share/omarchy/bin/omarchy-launch-terminal')
        self.link('../../home/user/.local/share/omarchy', 'usr/share/omarchy')
        self.link('../share/omarchy/bin/omarchy-launch-terminal')
        with patch.object(commands.subprocess, 'Popen') as spawn:
            with self.assertRaises(phone.UserError) as error:
                phone.action({'action': 'install-tools', 'group': 'core'})
        message = str(error.exception)
        self.assertIn('Automatic installation is unavailable', message)
        self.assertIn('sudo pacman -S --needed scrcpy android-tools', message)
        self.assertNotIn('Too many levels', message)
        spawn.assert_not_called()
        self.assertFalse(self.record.exists())


if __name__ == '__main__':
    unittest.main()
