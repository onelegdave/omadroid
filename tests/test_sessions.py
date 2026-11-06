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
    if name == 'scrcpy' and (Path(FIXTURES) / 'launch-refused').exists():
        raise phone.UserError('Test scrcpy launch refused')
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
        self.executable("adb", '''import json, os, sys
from pathlib import Path
root = Path(os.environ["PHONE_TEST_ROOT"])
args = sys.argv[1:]
with (root / "adb-calls").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args == ["devices", "-l"]:
    print("List of devices attached\\nABC device model:Test_Phone\\nDEF device model:Second_Phone")
elif args[3:] == ["input", "keyevent", "KEYCODE_SLEEP"]:
    pid_file = root / ('mirror-pid-' + args[1])
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), 0)
            (root / "sleep-while-mirroring").write_text("yes")
        except ProcessLookupError:
            pass
elif args[3:] == ["dumpsys", "window"]:
    print("isKeyguardShowing=" + ("false" if (root / "unconfirmed").exists() else "true"))''')

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
        self.wait_closed()

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

    def adb_calls(self, command):
        calls = [json.loads(line) for line in (self.root / 'adb-calls').read_text().splitlines()]
        return [args for args in calls if args[3:] == command]

    def wait_closed(self, serial='ABC'):
        metadata = self.root / 'cache/phone-mirror' / (phone.session_key(serial) + '.session.json')
        deadline = time.monotonic() + 8
        while metadata.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertFalse(metadata.exists(), 'Session did not finish cleanup')

    def test_normal_window_close_sleeps_only_after_scrcpy_exits(self):
        self.executable('scrcpy', '''import os, sys, time
from pathlib import Path
serial = next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--serial='))
(Path(os.environ['PHONE_TEST_ROOT']) / ('mirror-pid-' + serial)).write_text(str(os.getpid()))
time.sleep(1.2)''')
        self.assertTrue(self.request(screenOff=True, keepAwake=False)['ok'])
        self.wait_closed()
        self.assertEqual(self.adb_calls(['input', 'keyevent', 'KEYCODE_SLEEP']),
                         [['-s', 'ABC', 'shell', 'input', 'keyevent', 'KEYCODE_SLEEP']])
        self.assertEqual(len(self.adb_calls(['dumpsys', 'window'])), 1)
        self.assertFalse((self.root / 'sleep-while-mirroring').exists())

    def test_launch_refusal_does_not_send_sleep(self):
        (self.root / 'launch-refused').touch()
        result = self.request()
        self.assertFalse(result['ok'])
        self.assertIn('launch refused', result['message'])
        self.assertEqual(self.adb_calls(['input', 'keyevent', 'KEYCODE_SLEEP']), [])
        self.assertEqual(self.adb_calls(['dumpsys', 'window']), [])

    def test_supervisor_cancellation_cleans_scrcpy_before_sleep(self):
        import signal
        self.executable('scrcpy', '''import os, time
from pathlib import Path
root = Path(os.environ['PHONE_TEST_ROOT'])
(root / 'mirror-pid-ABC').write_text(str(os.getpid()))
(root / 'supervisor-pid').write_text(str(os.getppid()))
time.sleep(30)''')
        self.executable('notify-send', 'pass')
        self.assertTrue(self.request()['ok'])
        os.kill(int((self.root / 'supervisor-pid').read_text()), signal.SIGTERM)
        self.wait_closed()
        self.assertEqual(len(self.adb_calls(['input', 'keyevent', 'KEYCODE_SLEEP'])), 1)
        self.assertFalse((self.root / 'sleep-while-mirroring').exists())

    def test_lock_opt_out_sends_no_sleep_or_verification(self):
        self.executable('scrcpy', 'pass')
        self.assertTrue(self.request(lockOnClose=False)['ok'])
        self.assertEqual(self.adb_calls(['input', 'keyevent', 'KEYCODE_SLEEP']), [])
        self.assertEqual(self.adb_calls(['dumpsys', 'window']), [])

    def test_late_unconfirmed_lock_notifies_without_claiming_unlocked(self):
        (self.root / 'unconfirmed').touch()
        self.executable('scrcpy', 'import time\ntime.sleep(1.2)')
        self.executable('notify-send', 'import os, sys\nfrom pathlib import Path\n(Path(os.environ["PHONE_TEST_ROOT"]) / "notification").write_text("\\n".join(sys.argv[1:]))')
        self.assertTrue(self.request()['ok'])
        self.wait_closed()
        deadline = time.monotonic() + 5
        while not (self.root / 'notification').exists() and time.monotonic() < deadline:
            time.sleep(.05)
        message = (self.root / 'notification').read_text()
        self.assertIn('Could not confirm the phone locked', message)
        self.assertNotIn('unlocked', message)
        self.assertEqual(len(self.adb_calls(['dumpsys', 'window'])), 3)

    def test_early_unconfirmed_lock_reaches_panel_with_original_error(self):
        (self.root / 'unconfirmed').touch()
        self.executable('scrcpy', 'import sys\nprint("Test encoder failed", file=sys.stderr)\nsys.exit(1)')
        result = self.request()
        self.assertFalse(result['ok'])
        self.assertIn('Could not confirm the phone locked', result['message'])
        self.assertIn('Test encoder failed', result['message'])

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
        self.assertEqual([args[1] for args in self.adb_calls(['input', 'keyevent', 'KEYCODE_SLEEP'])], ['ABC'])
        # The unrelated device must still be active and reject a duplicate.
        self.assertIn('already mirroring', self.request(serial='DEF')['message'])
        stop('DEF')
        self.assertEqual([args[1] for args in self.adb_calls(['input', 'keyevent', 'KEYCODE_SLEEP'])], ['ABC', 'DEF'])


if __name__ == "__main__":
    unittest.main()
