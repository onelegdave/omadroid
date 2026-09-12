import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import phone_mirror as phone

class StorageSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,XDG_STATE_HOME=str(self.root/'state'),XDG_CACHE_HOME=str(self.root/'cache'));self.env.start()
        (self.root/'state').mkdir();(self.root/'cache').mkdir()
        self.sentinel=self.root/'sentinel';self.sentinel.write_text('untouched')
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_symlink_state_directory_rejected(self):
        (self.root/'state/phone-mirror').symlink_to(self.root)
        with self.assertRaises((ValueError,OSError)):phone.remember('192.168.1.2:32100')
        self.assertFalse((self.root/'connections.json').exists())
    def test_symlink_and_hardlink_state_files_never_overwritten(self):
        for hard in (False,True):
            path=phone.saved_path()
            if hard:os.link(self.sentinel,path)
            else:path.symlink_to(self.sentinel)
            with self.assertRaises((ValueError,OSError)):phone.remember('192.168.1.2:32100')
            self.assertEqual(self.sentinel.read_text(),'untouched');path.unlink()
    def test_fifo_read_refused_without_blocking(self):
        path=phone.saved_path();os.mkfifo(path)
        with self.assertRaises((ValueError,OSError)):phone.saved()
    def test_oversized_state_refused(self):
        path=phone.saved_path();path.write_bytes(b'x'*65537)
        with self.assertRaises(ValueError):phone.saved()
    def test_symlink_lock_refused(self):
        path=phone.cache_dir()/(phone.session_key('ABC')+'.lock');path.symlink_to(self.sentinel)
        with self.assertRaises((OSError,ValueError)):phone.active('ABC')
        self.assertEqual(self.sentinel.read_text(),'untouched')
    def test_public_and_loopback_endpoints_rejected(self):
        for address in ('8.8.8.8:1234','1.1.1.1:5037','127.0.0.1:5037','[::1]:1234','[2606:4700:4700::1111]:1234','224.0.0.251:5353'):
            with self.subTest(address=address),self.assertRaises(phone.UserError):phone.endpoint(address)
    def test_clipboard_autosync_is_disabled(self):
        self.assertIn('--no-clipboard-autosync',phone.scrcpy_command('ABC',{}))
