import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import install


class InstallerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'home'
        self.source = self.base / 'source'
        self.home.mkdir(mode=0o700)
        self.source.mkdir(mode=0o700)
        for name in install.FILES:
            (self.source / name).write_text('new content')
        (self.source / 'manifest.json').write_text(json.dumps({'id': install.PLUGIN_ID}))
        self.config = self.home / '.config/omarchy'
        self.plugins = self.config / 'plugins'
        self.plugins.mkdir(parents=True)
        self.target = self.plugins / install.PLUGIN_ID
        self.outside = self.base / 'outside'
        self.outside.mkdir()
        (self.outside / 'sentinel').write_text('untouched')

    def run_install(self):
        return install.install_files(self.home, self.source)

    def refuse(self):
        with self.assertRaises((OSError, ValueError)):
            self.run_install()
        self.assertEqual((self.outside / 'sentinel').read_text(), 'untouched')

    def test_fresh_install_and_update_backup(self):
        result = self.run_install()
        self.assertEqual(result['bar']['layout']['right'][0]['id'], install.PLUGIN_ID)
        shell = self.config / 'shell.json'
        data = json.loads(shell.read_text())
        data['idle'] = {'lock': 1200}
        data['bar']['layout']['right'][0]['metric'] = 'Memory'
        shell.write_text(json.dumps(data))
        shell.chmod(0o640)
        (self.target / 'Panel.qml').write_text('old content')
        self.run_install()
        self.assertEqual(json.loads(shell.read_text()), data)
        self.assertEqual(stat.S_IMODE(shell.stat().st_mode), 0o640)
        copies = list((self.config / 'plugin-backups').glob('*/Panel.qml'))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_text(), 'old content')
        self.assertEqual((self.target / 'Panel.qml').read_text(), 'new content')

    def test_symlink_target(self):
        self.target.symlink_to(self.outside, target_is_directory=True)
        self.refuse()
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ['sentinel'])

    def test_dangling_symlink_target(self):
        self.target.symlink_to(self.outside / 'missing')
        self.refuse()
        self.assertFalse((self.outside / 'missing').exists())

    def test_symlink_destination_file(self):
        self.target.mkdir()
        (self.target / 'Panel.qml').symlink_to(self.outside / 'sentinel')
        self.refuse()

    def test_symlink_config_file(self):
        (self.config / 'shell.json').symlink_to(self.outside / 'sentinel')
        self.refuse()
        self.assertFalse(self.target.exists())

    def test_symlink_backup_root(self):
        (self.config / 'plugin-backups').symlink_to(self.outside, target_is_directory=True)
        self.refuse()

    def test_symlink_config_component(self):
        self.plugins.rmdir()
        self.config.rmdir()
        self.config.symlink_to(self.outside, target_is_directory=True)
        self.refuse()

    def test_nested_backup_symlink(self):
        nested = self.target / 'nested'
        nested.mkdir(parents=True)
        (nested / 'escape').symlink_to(self.outside, target_is_directory=True)
        self.refuse()

    def test_fifo_config_refused_without_blocking(self):
        os.mkfifo(self.config / 'shell.json')
        self.refuse()

    def test_directory_instead_of_runtime_file(self):
        (self.target / 'Panel.qml').mkdir(parents=True)
        self.refuse()

    def test_hardlinked_destination(self):
        self.target.mkdir()
        os.link(self.outside / 'sentinel', self.target / 'Panel.qml')
        self.refuse()

    def test_oversized_config(self):
        with (self.config / 'shell.json').open('wb') as f:
            f.truncate(install.MAX_CONFIG + 1)
        self.refuse()
        self.assertFalse(self.target.exists())

    def test_group_writable_target(self):
        self.target.mkdir(mode=0o777)
        self.target.chmod(0o777)
        self.refuse()

    def test_foreign_owner(self):
        info = list(self.home.stat())
        info[4] = os.getuid() + 1
        with self.assertRaises(install.UnsafePath):
            install.validate(os.stat_result(info), directory=True)

    def test_atomic_write_detects_changed_destination(self):
        self.target.mkdir()
        path = self.target / 'Panel.qml'
        path.write_text('old')
        with install.home_directory(self.target) as fd:
            old = install.inspect(fd, 'Panel.qml')
            path.write_text('changed by someone else')
            with self.assertRaises(install.UnsafePath):
                install.atomic_write(fd, 'Panel.qml', b'new', expected=old)
        self.assertEqual(path.read_text(), 'changed by someone else')

    def test_symlink_substituted_during_replace_cannot_redirect_write(self):
        self.target.mkdir()
        path = self.target / 'Panel.qml'
        path.write_text('old')
        replace = os.replace

        def race(src, dst, **kwargs):
            path.unlink()
            path.symlink_to(self.outside / 'sentinel')
            replace(src, dst, **kwargs)

        with install.home_directory(self.target) as fd:
            old = install.inspect(fd, 'Panel.qml')
            with patch.object(install.os, 'replace', side_effect=race):
                install.atomic_write(fd, 'Panel.qml', b'new', expected=old)
        self.assertEqual((self.outside / 'sentinel').read_text(), 'untouched')
        self.assertFalse(path.is_symlink())
        self.assertEqual(path.read_text(), 'new')

    def test_prepositioned_backup_file(self):
        (self.config / 'shell.json').write_text('{}')
        (self.config / ('shell.json.bak-omadroid-' + 'a' * 32)).symlink_to(self.outside / 'sentinel')
        with patch.object(install.secrets, 'token_hex', return_value='a' * 32):
            self.refuse()

    def test_prepositioned_backup_directory(self):
        self.target.mkdir()
        root = self.config / 'plugin-backups'
        root.mkdir()
        (root / (install.PLUGIN_ID + '-' + 'a' * 32)).symlink_to(self.outside, target_is_directory=True)
        with patch.object(install.secrets, 'token_hex', return_value='a' * 32):
            self.refuse()

    def test_symlink_home_component(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            install.install_files(alias, self.source)
        self.assertFalse(self.target.exists())

    def test_config_replaced_during_install_is_preserved(self):
        shell = self.config / 'shell.json'
        shell.write_text('{}')
        original = install.atomic_write
        def change_config(parent, name, data, **kwargs):
            if name == 'Panel.qml':
                shell.write_text('{"concurrent": true}')
            return original(parent, name, data, **kwargs)
        with patch.object(install, 'atomic_write', side_effect=change_config):
            with self.assertRaises(install.UnsafePath):
                self.run_install()
        self.assertEqual(json.loads(shell.read_text()), {'concurrent': True})

    def test_installer_entrypoint_does_not_launch_processes(self):
        with patch.object(install.Path, 'home', return_value=self.home), patch.object(install, '__file__', str(self.source / 'install.py')), patch('subprocess.Popen') as launch, patch('builtins.print'):
            install.main()
        launch.assert_not_called()
        self.assertTrue((self.target / 'Panel.qml').is_file())

    def test_source_symlink(self):
        (self.source / 'Panel.qml').unlink()
        (self.source / 'Panel.qml').symlink_to(self.outside / 'sentinel')
        self.refuse()


if __name__ == '__main__':
    unittest.main()
