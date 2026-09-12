"""Exercise real helper processes with fake ADB/scrcpy executables, no phone."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phone_mirror as phone


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = {**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                    "XDG_CACHE_HOME": str(self.root / "cache"), "XDG_STATE_HOME": str(self.root / "state"), "PHONE_TEST_ROOT": str(self.root)}
        self.driver = self.root / 'phone_mirror.py'
        self.driver.write_text("""import sys, os, subprocess
from pathlib import Path
from contextlib import contextmanager
sys.path.insert(0, REPO)
import phone_mirror as phone
phone.__file__ = __file__
original_launch = phone.commands.launch
original_env = phone.commands.tool_environment
@contextmanager
def fixture_launch(name, args, input=False, script=False):
    fixture = Path(FIXTURES) / name
    if fixture.exists():
        child = subprocess.Popen([sys.executable, str(fixture), *args], stdin=subprocess.PIPE if input else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env={**original_env(), 'PHONE_TEST_ROOT': FIXTURES})
        try: yield child
        finally: phone.commands.cleanup(child)
    else:
        with original_launch(name,args,input,script) as child: yield child
phone.commands.launch = fixture_launch
phone.commands.available = lambda name: name in ('adb','scrcpy','notify-send')
phone.main()
""".replace('REPO',repr(str(Path(phone.__file__).parent))).replace('FIXTURES',repr(str(self.root))))
        self.executable("adb", 'print("List of devices attached\\nABC device model:Test_Phone\\nDEF device model:Second_Phone")')

    def tearDown(self):
        self.temp.cleanup()

    def executable(self, name, source):
        file = self.root / name
        file.write_text("#!" + sys.executable + "\n" + source + "\n")
        file.chmod(0o700)

    def request(self, serial="ABC", **options):
        result = subprocess.run([sys.executable, str(self.driver), "action"],
                                input=json.dumps({"action": "mirror", "serial": serial, **options}) + "\n",
                                text=True, capture_output=True, env=self.env, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_launch_survives_caller_and_duplicate_is_prevented(self):
        self.executable("scrcpy", '''import json, os, sys, time
from pathlib import Path
root = Path(os.environ["PHONE_TEST_ROOT"])
with (root / "launches").open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
time.sleep(2.5)
(root / "finished").write_text("yes")''')
        first = self.request(audio=False)
        self.assertTrue(first["ok"])
        self.assertFalse((self.root / "finished").exists())
        second = self.request()
        self.assertIn("already mirroring", second["message"])
        calls = (self.root / "launches").read_text().splitlines()
        self.assertEqual(len(calls), 1)
        self.assertIn("--serial=ABC", json.loads(calls[0]))
        self.assertIn("--no-audio", json.loads(calls[0]))
        deadline = time.monotonic() + 5
        while not (self.root / "finished").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue((self.root / "finished").exists())

    def test_early_failure_reaches_panel(self):
        self.executable("scrcpy", 'import sys\nprint("ERROR: Test encoder unavailable", file=sys.stderr)\nsys.exit(1)')
        result = self.request()
        self.assertFalse(result["ok"])
        self.assertIn("Test encoder unavailable", result["message"])

    def test_later_failure_sends_notification(self):
        self.executable("scrcpy", 'import sys, time\ntime.sleep(1.2)\nprint("ERROR: Test connection lost", file=sys.stderr)\nsys.exit(1)')
        self.executable("notify-send", 'import os, sys\nfrom pathlib import Path\n(Path(os.environ["PHONE_TEST_ROOT"]) / "notification").write_text("\\n".join(sys.argv[1:]))')
        result = self.request()
        self.assertTrue(result["ok"])
        deadline = time.monotonic() + 5
        while not (self.root / "notification").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIn("Test connection lost", (self.root / "notification").read_text())

    def test_excessive_session_output_is_stopped_and_log_is_bounded(self):
        self.executable('scrcpy', 'import os,time\nos.write(1,b"x"*(2*1024*1024))\ntime.sleep(30)')
        result = self.request()
        self.assertFalse(result['ok'])
        self.assertIn('log exceeded', result['message'])
        logs = list((self.root / 'cache/phone-mirror').glob('*.log'))
        self.assertTrue(logs)
        self.assertLessEqual(logs[0].stat().st_size, 1024*1024)

    def test_stop_ends_only_the_selected_session(self):
        self.executable('scrcpy', 'import time\ntime.sleep(30)')
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.request(serial='DEF')['ok'])
        def stop(serial):
            result = subprocess.run([sys.executable, str(self.driver), 'action'],
                                    input=json.dumps({'action':'stop','serial':serial})+'\n',
                                    text=True, capture_output=True, env=self.env, timeout=6)
            self.assertTrue(json.loads(result.stdout)['ok'], result.stdout)
        stop('ABC')
        # The unrelated device must still be active and reject a duplicate.
        self.assertIn('already mirroring', self.request(serial='DEF')['message'])
        stop('DEF')


if __name__ == "__main__":
    unittest.main()
