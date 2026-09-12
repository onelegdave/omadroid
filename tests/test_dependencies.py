import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'install-deps.sh'

class DependencyInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.log=Path(self.temp.name)/'calls';self.exit=0
    def tearDown(self):
        self.temp.cleanup()
    def install(self,group):
        # Test-only shell functions intercept absolute sudo; production has no test override.
        code='/usr/bin/sudo() { printf "%s\\n" "$*" >> '+shlex.quote(str(self.log))+'; return '+str(self.exit)+'; }; source '+shlex.quote(str(SCRIPT))+' '+shlex.quote(group)
        return subprocess.run(['/usr/bin/bash','--noprofile','--norc','-c',code],capture_output=True,text=True,timeout=5)
    def test_only_fixed_requested_packages(self):
        self.assertEqual(self.install('core').returncode,0)
        self.assertEqual(self.log.read_text().strip(),'/usr/bin/pacman -S --needed scrcpy android-tools')
    def test_discovery_explicitly_enables_service(self):
        self.assertEqual(self.install('discovery').returncode,0)
        self.assertEqual(self.log.read_text().splitlines(),['/usr/bin/pacman -S --needed avahi','/usr/bin/systemctl enable --now avahi-daemon.service'])
    def test_unrecognized_group_never_executes(self):
        self.assertEqual(self.install('core; touch /tmp/not-a-command').returncode,2)
        self.assertFalse(self.log.exists())
    def test_failure_preserves_status_and_stops_service_enable(self):
        self.exit=7;result=self.install('discovery')
        self.assertEqual(result.returncode,7)
        self.assertIn('Installation did not finish',result.stdout)
        self.assertNotIn('systemctl',self.log.read_text())
