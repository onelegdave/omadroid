import json
import os
from pathlib import Path
import platform
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phone_mirror as phone
import safe_process as commands

LAUNCHER = 'usr/bin/omarchy-launch-terminal'
FOOT = 'usr/bin/foot'
# OmarchyMac's 3.x-to-Quattro upgrade keeps Omarchy in this user checkout and
# links /usr/share/omarchy and each /usr/bin/omarchy-* command into it.
CHECKOUT = 'home/user/.local/share/omarchy'
# Tiny static programs stand in for the terminal, so no window ever opens:
# one waits for a signal (pause or ppoll) and one exits with foot's failure code 230.
PROGRAMS = {
    'x86_64': (0x3e, bytes.fromhex('b822000000' '0f05' 'ebf7'),
               bytes.fromhex('bfe6000000' 'b83c000000' '0f05')),
    'aarch64': (0xb7, struct.pack('<7I', 0xd2800000, 0xd2800001, 0xd2800002, 0xd2800003, 0xd2800928, 0xd4000001, 0x17fffffa),
                struct.pack('<3I', 0xd2801cc0, 0xd2800ba8, 0xd4000001)),
}


def tiny_elf(waits):
    machine, wait, fail = PROGRAMS[platform.machine()]
    code = wait if waits else fail
    base, size = 0x400000, 64 + 56 + len(code)
    header = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + bytes(9), 2, machine, 1,
                         base + 120, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    return header + struct.pack('<IIQQQQQQ', 1, 5, 0, base, base, size, size, 0x1000) + code


class InstallerTerminalTests(unittest.TestCase):
    """Installer routes in an isolated fixture root whose owner stands in for root.

    ROOT and TRUSTED_UID are patched together, so every ownership, mode, link,
    and format check still runs against the fixture.
    """

    def setUp(self):
        if platform.machine() not in PROGRAMS:
            self.skipTest('No stand-in terminal program for ' + platform.machine())
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / 'root'
        self.marker = self.base / 'home-launcher-ran'
        self.record = self.base / 'record'
        for directory in ('usr/bin', CHECKOUT + '/bin'):
            (self.root / directory).mkdir(parents=True)
        for directory in [self.root, *self.root.rglob('*')]:
            directory.chmod(0o755)
        bash = self.root / 'usr/bin/bash'
        shutil.copyfile(os.path.realpath('/usr/bin/bash'), bash)
        bash.chmod(0o755)
        for name, value in (('ROOT', str(self.root)), ('TRUSTED_UID', os.getuid())):
            patcher = patch.object(commands, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = patch.dict(os.environ, {'XDG_CACHE_HOME': temp.name, 'XDG_STATE_HOME': temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.stop_terminals)
        self.terminal_inodes = set()
        # Keep each started terminal's handle so cleanup can reap it.
        self.opened = []
        open_terminal = commands.open_terminal
        patcher = patch.object(commands, 'open_terminal', side_effect=lambda command: self.opened.append(open_terminal(command)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def upgrade_layout(self):
        # Even a well-formed script behind the link must never run. Only the
        # no-follow open stops it, since the fixture owner is "root" here.
        home = self.root / CHECKOUT / 'bin/omarchy-launch-terminal'
        home.write_text('#!/bin/bash\ntouch ' + str(self.marker) + '\n')
        home.chmod(0o755)
        (self.root / 'usr/share').mkdir(mode=0o755)
        (self.root / 'usr/share/omarchy').symlink_to('../../' + CHECKOUT)
        (self.root / LAUNCHER).symlink_to(home)

    def packaged_launcher(self):
        # A fake packaged launcher records its arguments; it opens no terminal.
        launcher = self.root / LAUNCHER
        launcher.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > ' + str(self.record) + '\n')
        launcher.chmod(0o755)

    def terminal(self, waits=True, path=FOOT, mode=0o755):
        target = self.root / path
        target.write_bytes(tiny_elf(waits))
        target.chmod(mode)
        info = target.stat()
        self.terminal_inodes.add((info.st_dev, info.st_ino))
        return target

    def running(self):
        found = []
        for entry in os.listdir('/proc'):
            if entry.isdigit():
                try:
                    info = os.stat('/proc/' + entry + '/exe')
                except OSError:
                    continue
                if (info.st_dev, info.st_ino) in self.terminal_inodes:
                    found.append(int(entry))
        return found

    def stop_terminals(self):
        for process in self.opened:
            process.kill()
            process.wait()
        # Terminals left by the separate one-shot backend are no longer our children.
        for pid in self.running():
            os.kill(pid, signal.SIGKILL)

    def installer_args(self, group='core'):
        return ['/usr/bin/env', '-i', *[key + '=' + value for key, value in commands.tool_environment().items()],
                '/usr/bin/bash', '--noprofile', '--norc', str(Path(phone.__file__).absolute().parent / 'install-deps.sh'), group]

    def assert_manual(self, group='core', packages='scrcpy android-tools'):
        with patch.object(commands.subprocess, 'Popen') as spawn:
            self.assertFalse(phone.status()['dependencies']['installer'])
            with self.assertRaises(phone.UserError) as error:
                phone.action({'action': 'install-tools', 'group': group})
        message = str(error.exception)
        self.assertIn('Automatic installation is unavailable', message)
        self.assertIn('sudo pacman -S --needed ' + packages, message)
        self.assertNotIn('Too many levels', message)
        spawn.assert_not_called()
        self.assertFalse(self.marker.exists())

    def test_upgrade_layout_opens_installer_in_verified_packaged_terminal(self):
        self.upgrade_layout()
        foot = self.terminal()
        self.assertFalse(commands.available('omarchy-launch-terminal'))
        self.assertTrue(phone.status()['dependencies']['installer'])
        started = time.monotonic()
        result = phone.action({'action': 'install-tools', 'group': 'core'})
        self.assertTrue(result['ok'])
        self.assertLess(time.monotonic() - started, commands.TERMINAL_STARTUP + 2)
        self.assertFalse(self.marker.exists())
        [pid] = self.running()
        self.assertEqual(os.stat('/proc/%d/exe' % pid).st_ino, foot.stat().st_ino)
        argv = Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\0')[:-1]
        self.assertEqual([part.decode() for part in argv],
                         ['/usr/bin/foot', '--config=/dev/null', '--', *self.installer_args()])
        environ = Path('/proc/%d/environ' % pid).read_bytes().split(b'\0')[:-1]
        self.assertEqual(dict(item.decode().split('=', 1) for item in environ), commands.tool_environment())
        for number in (0, 1, 2):
            self.assertEqual(os.readlink('/proc/%d/fd/%d' % (pid, number)), '/dev/null')
        self.assertEqual(os.getsid(pid), pid)

    def test_terminal_replaced_after_verification_cannot_redirect_launch(self):
        self.upgrade_layout()
        original = self.terminal()
        inode = original.stat().st_ino
        spawn = commands.subprocess.Popen

        def replace_then_spawn(*args, **kwargs):
            original.rename(self.base / 'verified-foot')
            self.terminal(waits=False)
            return spawn(*args, **kwargs)

        with patch.object(commands.subprocess, 'Popen', side_effect=replace_then_spawn):
            self.assertTrue(phone.action({'action': 'install-tools', 'group': 'core'})['ok'])
        [pid] = self.running()
        self.assertEqual(os.stat('/proc/%d/exe' % pid).st_ino, inode)

    def test_terminal_startup_failure_is_cleaned_up_with_manual_guidance(self):
        self.upgrade_layout()
        self.terminal(waits=False)
        self.assertTrue(phone.status()['dependencies']['installer'])
        started = time.monotonic()
        with self.assertRaises(phone.UserError) as error:
            phone.action({'action': 'install-tools', 'group': 'discovery'})
        self.assertLess(time.monotonic() - started, commands.TERMINAL_STARTUP + 1)
        self.assertIn('sudo pacman -S --needed avahi\nsudo systemctl enable --now avahi-daemon.service', str(error.exception))
        self.assertEqual(self.running(), [])
        self.assertFalse(self.marker.exists())

    def test_terminal_outlives_one_shot_backend_and_launcher_deadline(self):
        self.upgrade_layout()
        self.terminal()
        harness = ('import sys; sys.path.insert(0, sys.argv[1]); sys.argv[1:] = ["action"]\n'
                   'import safe_process as commands, phone_mirror as phone\n'
                   'commands.ROOT = ' + repr(str(self.root)) + '\n'
                   'commands.TRUSTED_UID = ' + str(os.getuid()) + '\n'
                   'phone.main()\n')
        started = time.monotonic()
        backend = subprocess.run([sys.executable, '-I', '-c', harness, str(Path(phone.__file__).parent)],
                                 input='{"action": "install-tools", "group": "core"}\n', capture_output=True,
                                 text=True, timeout=10, env={**os.environ})
        # The backend's stdout reached EOF and it exited: the terminal holds no pipe of ours.
        self.assertLess(time.monotonic() - started, commands.TERMINAL_STARTUP + 3)
        self.assertEqual(backend.returncode, 0, backend.stderr)
        self.assertTrue(json.loads(backend.stdout)['ok'], backend.stdout)
        [pid] = self.running()
        # The interactive window must survive past the launcher's 8-second limit.
        time.sleep(max(0, started + 9 - time.monotonic()))
        self.assertEqual(self.running(), [pid])
        self.assertFalse(self.marker.exists())

    def test_verified_packaged_launcher_is_preferred(self):
        self.packaged_launcher()
        self.terminal()
        self.assertTrue(phone.status()['dependencies']['installer'])
        self.assertTrue(phone.action({'action': 'install-tools', 'group': 'usb'})['ok'])
        self.assertEqual(self.record.read_text().splitlines(), self.installer_args('usb'))
        self.assertEqual(self.running(), [])

    def test_packaged_launcher_alone_still_offers_installation(self):
        self.packaged_launcher()
        self.assertTrue(phone.status()['dependencies']['installer'])
        self.assertTrue(phone.action({'action': 'install-tools', 'group': 'core'})['ok'])
        self.assertTrue(self.record.exists())

    def test_no_trusted_route_keeps_manual_fallback(self):
        self.upgrade_layout()
        self.assert_manual()

    def test_untrusted_terminals_are_refused(self):
        self.upgrade_layout()
        cases = {
            'group-writable': lambda: self.terminal(mode=0o775),
            'world-writable': lambda: self.terminal(mode=0o757),
            'setuid': lambda: self.terminal(mode=0o4755),
            'not executable': lambda: self.terminal(mode=0o644),
            'script': lambda: (self.root / FOOT).write_text('#!/bin/bash\ntouch ' + str(self.marker) + '\n'),
            'symlink': lambda: (self.terminal(path=CHECKOUT + '/bin/foot'), (self.root / FOOT).symlink_to(self.root / CHECKOUT / 'bin/foot')),
            'directory': lambda: (self.root / FOOT).mkdir(),
        }
        for name, make in cases.items():
            with self.subTest(name=name):
                make()
                if name == 'script':
                    (self.root / FOOT).chmod(0o755)
                self.assertFalse(commands.available('foot'))
                self.assert_manual()
                target = self.root / FOOT
                target.rmdir() if target.is_dir() and not target.is_symlink() else target.unlink()
        self.assertEqual(self.running(), [])

    def test_untrusted_terminal_owner_or_directory_is_refused(self):
        self.upgrade_layout()
        foot = self.terminal()
        fstat = os.fstat

        def foreign(inode):
            def owner(fd):
                info = fstat(fd)
                if info.st_ino != inode:
                    return info
                fields = list(info)
                fields[4] = commands.TRUSTED_UID + 1
                return os.stat_result(fields)
            return owner

        for name, inode in (('terminal', foot.stat().st_ino), ('/usr/bin', (self.root / 'usr/bin').stat().st_ino)):
            with self.subTest(owner=name), patch.object(commands.os, 'fstat', side_effect=foreign(inode)):
                self.assertFalse(commands.available('foot'))
        (self.root / 'usr/bin').chmod(0o775)
        try:
            self.assert_manual('usb', 'android-udev')
        finally:
            (self.root / 'usr/bin').chmod(0o755)
        self.assertTrue(commands.available('foot'))

    def test_install_group_is_never_passed_to_the_terminal_unchecked(self):
        self.upgrade_layout()
        self.terminal()
        with self.assertRaisesRegex(phone.UserError, 'Unknown dependency group'):
            phone.action({'action': 'install-tools', 'group': 'core; touch ' + str(self.marker)})
        self.assertEqual(self.running(), [])


class PackagedFootStartupTests(unittest.TestCase):
    """The real packaged foot, run from its verified descriptor without a compositor."""

    def test_real_foot_without_a_compositor_is_a_cleaned_up_startup_failure(self):
        if not commands.available('foot'):
            self.skipTest('No verified /usr/bin/foot on this system')
        with tempfile.TemporaryDirectory() as runtime:
            env = commands.tool_environment()
            for name in ('WAYLAND_DISPLAY', 'DISPLAY'):
                env.pop(name, None)
            # An empty private runtime directory has no Wayland socket to reach.
            env.update(XDG_RUNTIME_DIR=runtime, DBUS_SESSION_BUS_ADDRESS='unix:path=' + runtime + '/bus')
            spawn = commands.subprocess.Popen
            spawned = []

            def record(*args, **kwargs):
                spawned.append(spawn(*args, **kwargs))
                return spawned[-1]

            with patch.object(commands, 'tool_environment', return_value=env), \
                 patch.object(commands.subprocess, 'Popen', side_effect=record):
                with self.assertRaisesRegex(commands.ToolError, 'closed during startup'):
                    commands.open_terminal(['/usr/bin/true'])
            [process] = spawned
            self.assertEqual(process.returncode, 230)
            self.assertEqual(process.args, ['/usr/bin/foot', '--config=/dev/null', '--', '/usr/bin/true'])


if __name__ == '__main__':
    unittest.main()
