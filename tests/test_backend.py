import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phone_mirror as phone


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"XDG_CACHE_HOME": self.temp.name, "XDG_STATE_HOME": self.temp.name})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_addresses_reject_commands_options_and_missing_ports(self):
        for value in ["--help", "192.168.1.5", "host:5555", "192.168.1.5:0", "1.2.3.4:65536", "$(touch /tmp/no):12", "1.2.3.4:12;id", "1.2.3.4:12\n--help"]:
            with self.subTest(value=value), self.assertRaises(phone.UserError):
                phone.endpoint(value)
        self.assertEqual(phone.endpoint("192.168.1.2:32100"), "192.168.1.2:32100")
        self.assertEqual(phone.endpoint("[fd00:db8::1]:32100"), "[fd00:db8::1]:32100")

    def test_adb_states_and_distinct_devices(self):
        output = """List of devices attached
ABC device usb:1-3 product:foo model:Galaxy_Z_Fold8 device:q8 transport_id:1
192.168.1.2:32100 device product:bar model:Pixel_9 transport_id:2
adb-DEF-random._adb-tls-connect._tcp device model:Pixel_Tablet
UNAUTHORIZED unauthorized usb:1-4 transport_id:3
OFFLINE offline transport_id:4
PERMISSIONS no permissions (user in plugdev group; are your udev rules wrong?)
"""
        devices = phone.parse_devices(output)
        self.assertEqual(len(devices), 6)
        self.assertEqual(devices[0]["name"], "Galaxy Z Fold8")
        self.assertFalse(devices[0]["wireless"])
        self.assertTrue(devices[2]["wireless"])
        self.assertEqual([d["state"] for d in devices[3:]], ["unauthorized", "offline", "no permissions"])

    def test_unrunnable_adb_is_not_ready(self):
        broken = subprocess.CompletedProcess(
            ["adb", "version"], 127, "",
            "/usr/bin/adb: error while loading shared libraries: libprotobuf.so.36.1.0: cannot open shared object file",
        )

        def fake_run(args, timeout=8, input=None):
            if args[:2] == ["adb", "version"]:
                return broken
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch.object(phone.commands, "available", side_effect=lambda name: name != "kdeconnect-cli"), \
             patch.object(phone, "run", side_effect=fake_run), \
             patch.object(phone, "kde_devices", return_value={"devices": [], "error": ""}), \
             patch.object(phone, "saved", return_value=[]):
            result = phone.status()
        self.assertFalse(result["dependencies"]["adb"])
        self.assertTrue(any("libprotobuf" in error for error in result["errors"]))
        self.assertEqual(result["devices"], [])

    def test_mdns_keeps_pairing_and_connection_ports_separate(self):
        output = """List of discovered mdns services
adb-test _adb-tls-pairing._tcp. 192.168.1.2:32100
adb-test _adb-tls-connect._tcp. 192.168.1.2:43200
adb-test _adb-tls-connect._tcp. 192.168.1.2:43200
bad _adb-tls-connect._tcp. --help
legacy _adb._tcp. 192.168.1.3:5555
"""
        result = phone.parse_services(output)
        self.assertEqual([(x["kind"], x["address"]) for x in result], [("pair", "192.168.1.2:32100"), ("connect", "192.168.1.2:43200")])

    def test_pair_code_only_on_stdin_and_not_error_output(self):
        with patch.object(phone, "finish_pairing", return_value={"paired": True}) as finish, patch.object(phone, "run", return_value=subprocess.CompletedProcess([], 0, "Successfully paired to 192.168.1.2:32100", "")) as command:
            result = phone.action({"action": "pair", "address": "192.168.1.2:32100", "code": "123456"})
            self.assertTrue(result["paired"])
            args, kwargs = command.call_args
            self.assertNotIn("123456", args[0])
            self.assertEqual(kwargs["input"], "123456\n")
            finish.assert_called_once_with("192.168.1.2:32100")
        with patch.object(phone, "run", return_value=subprocess.CompletedProcess([], 1, "bad code 123456", "")):
            with self.assertRaises(phone.UserError) as error:
                phone.action({"action": "pair", "address": "192.168.1.2:32100", "code": "123456"})
            self.assertNotIn("123456", str(error.exception))

    def test_avahi_fallback_parses_and_deduplicates(self):
        output = """+;enp1s0;IPv4;adb-phone;_adb-tls-connect._tcp;local
=;enp1s0;IPv4;adb-phone;_adb-tls-connect._tcp;local;phone.local;192.168.1.2;32100;
=;wlan0;IPv4;adb-phone;_adb-tls-connect._tcp;local;phone.local;192.168.1.2;32100;
=;enp1s0;IPv6;adb-phone;_adb-tls-connect._tcp;local;phone.local;fd00:db8::1;32100;
"""
        self.assertEqual([s["address"] for s in phone.parse_avahi(output, "connect")], ["192.168.1.2:32100", "[fd00:db8::1]:32100"])
        def command(args, *rest):
            if args[0] == "adb":
                raise phone.UserError("mdns unsupported")
            return output if args[-1] == "_adb-tls-connect._tcp" else ""
        with patch.object(phone, "checked", side_effect=command), patch.object(phone.commands, "available", return_value="installed"):
            self.assertEqual(len(phone.discover()), 2)

    def test_adb_connect_error_even_when_exit_code_is_zero(self):
        with patch.object(phone, "checked", return_value="failed to connect to 192.168.1.2:32100"):
            with self.assertRaises(phone.UserError):
                phone.action({"action": "connect", "address": "192.168.1.2:32100"})
            self.assertEqual(phone.saved(), [])
        with patch.object(phone, "checked", side_effect=["already connected to 192.168.1.2:32100", "List of devices attached\n192.168.1.2:32100 device model:Pixel"]), patch.object(phone, "discover", return_value=[]):
            self.assertTrue(phone.action({"action": "connect", "address": "192.168.1.2:32100"})["ok"])
        self.assertEqual(phone.saved(), ["192.168.1.2:32100"])

    def test_connect_does_not_claim_ready_when_device_is_offline(self):
        with patch.object(phone, "checked", side_effect=["connected to 192.168.1.2:32100", "List of devices attached\n192.168.1.2:32100 offline"]):
            with self.assertRaisesRegex(phone.UserError, "not ready"):
                phone.connect_phone("192.168.1.2:32100")
        self.assertEqual(phone.saved(), [])

    def test_failed_reconnect_preserves_pairing_profile_and_can_resume(self):
        address = '192.168.1.2:43200'
        original = {'phones': [dict(identity='KNOWN', name='Phone', address=address, paused=True)]}
        phone.write_connection_state(original)
        for failure in ('failed to connect', phone.UserError('Connection refused')):
            with patch.object(phone, 'checked', side_effect=[failure]) if isinstance(failure, Exception) else patch.object(phone, 'checked', return_value=failure):
                with self.assertRaisesRegex(phone.UserError, 'choose Connect again'):
                    phone.action({'action': 'connect', 'address': address})
            self.assertEqual(phone.connection_state(), original)
        services = [dict(name='advertised-phone', identity='KNOWN', label='Phone', kind='connect', address=address)]
        with patch.object(phone, 'checked', side_effect=['connected to ' + address, address + ' device model:Phone']), patch.object(phone, 'discover', return_value=services):
            self.assertTrue(phone.action({'action': 'connect', 'address': address})['connected'])
        profiles = phone.connection_state()['phones']
        self.assertEqual(len(profiles), 1)
        self.assertFalse(profiles[0].get('paused', False))

    def test_pairing_automatically_uses_connection_port_on_same_phone(self):
        services = [{"name": "phone", "kind": "connect", "address": "192.168.1.2:43200"}, {"name": "other", "kind": "connect", "address": "192.168.1.3:56789"}]
        with patch.object(phone, "discover", return_value=services), patch.object(phone, "connect_phone", return_value={"ok": True, "connected": True}) as connect:
            result = phone.finish_pairing("192.168.1.2:32100")
        self.assertTrue(result["connected"])
        self.assertTrue(result["paired"])
        connect.assert_called_once_with("192.168.1.2:43200", services)

    def test_pairing_waits_for_discovery_and_survives_panel_reload(self):
        with patch.object(phone, "discover", return_value=[]):
            result = phone.finish_pairing("192.168.1.2:32100")
        self.assertTrue(result["paired"])
        self.assertNotIn("connected", result)
        services = [{"name": "phone", "kind": "connect", "address": "192.168.1.2:43200"}]
        self.assertEqual(phone.connection_candidates(services, [], phone.connection_state()), services)

    def test_reconnect_only_known_identities_and_current_ports(self):
        services = [{"name": "new-random-suffix", "identity": "KNOWN", "kind": "connect", "address": "192.168.1.2:43200"}, {"name": "other", "identity": "UNKNOWN", "kind": "connect", "address": "192.168.1.2:56789"}]
        state = {"phones": [{"identity": "KNOWN", "name": "Pixel", "address": "192.168.1.2:11111"}]}
        self.assertEqual(phone.connection_candidates(services, [], state), [services[0]])
        self.assertEqual(phone.connection_candidates(services, [{"serial": "192.168.1.2:43200", "state": "device"}], state), [])
        self.assertEqual(phone.connection_candidates(services, [{"serial": "new-random-suffix._adb-tls-connect._tcp", "state": "device"}], state), [])
        self.assertEqual(phone.connection_candidates(services, [], {"pending": {"host": "192.168.1.2", "expires": 1}}), [])

    def test_avahi_reads_stable_identity_and_phone_name(self):
        services = phone.parse_avahi('=;eth0;IPv4;adb-random;_adb-tls-connect._tcp;local;phone.local;192.168.1.2;43200;"given_name=My Phone" "serial=SERIAL123"', "connect")
        self.assertEqual(services[0]["identity"], "SERIAL123")
        self.assertEqual(services[0]["label"], "My Phone")

    def test_missing_and_unauthorized_devices_cannot_receive_mirror(self):
        with patch.object(phone, "checked", return_value="List of devices attached\nABC unauthorized\n"):
            for serial in ["ABC", "OTHER", "--help"]:
                with self.subTest(serial=serial), self.assertRaises(phone.UserError):
                    phone.action({"action": "mirror", "serial": serial})

    def test_explicit_device_and_options(self):
        args = phone.scrcpy_command("phone with spaces;$(id)", {"quality": "Low bandwidth", "audio": False, "screenOff": True})
        self.assertIn("--serial=phone with spaces;$(id)", args)
        self.assertIn("--max-size=1024", args)
        self.assertIn("--no-audio", args)
        self.assertIn("--turn-screen-off", args)
        self.assertNotIn("--turn-screen-off", phone.scrcpy_command("ABC", {}))

    def test_keep_awake_works_with_screen_off_without_changing_timeout(self):
        args = phone.scrcpy_command("ABC", {"keepAwake": True, "screenOff": True})
        self.assertIn("--keep-active", args)
        self.assertIn("--turn-screen-off", args)
        self.assertNotIn("--stay-awake", args)
        self.assertFalse(any("screen-off-timeout" in arg for arg in args))
        self.assertNotIn("--keep-active", phone.scrcpy_command("ABC", {"keepAwake": False}))

    def test_wake_targets_ready_device_and_requests_normal_keyguard(self):
        ready = "List of devices attached\nABC device model:Pixel"
        with patch.object(phone, "checked", side_effect=[ready, ""]) as command, patch.object(phone, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as dismiss, patch.object(phone.time, "sleep"):
            result = phone.action({"action": "wake", "serial": "ABC"})
        self.assertTrue(result["ok"])
        self.assertIn("PIN", result["message"])
        self.assertEqual(command.call_args_list[-1].args[0], ["adb", "-s", "ABC", "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
        dismiss.assert_called_once_with(["adb", "-s", "ABC", "shell", "wm", "dismiss-keyguard"])
        with patch.object(phone, "checked", return_value="List of devices attached\nABC unauthorized"), patch.object(phone, "run") as run:
            with self.assertRaises(phone.UserError):
                phone.action({"action": "wake", "serial": "ABC"})
            run.assert_not_called()

    def test_old_scrcpy_has_actionable_keep_awake_error(self):
        with patch.object(phone, "checked", side_effect=["List of devices attached\nABC device model:Pixel", "scrcpy --stay-awake"]), patch.object(phone.commands, "available", return_value="installed"):
            with self.assertRaisesRegex(phone.UserError, "Upgrade scrcpy"):
                phone.action({"action": "mirror", "serial": "ABC", "keepAwake": True})

    def test_lock_is_specific_to_device_and_released(self):
        import fcntl
        lock_path = phone.cache_dir() / (phone.session_key("ABC") + ".lock")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(phone.active("ABC"))
            self.assertFalse(phone.active("DEF"))
        self.assertFalse(phone.active("ABC"))

    def test_status_works_without_optional_kde_connect(self):
        with patch.object(phone.commands, "available", return_value=None):
            result = phone.status()
        self.assertEqual(result["devices"], [])
        self.assertFalse(result["dependencies"]["kdeconnect"])
        self.assertIn("Install KDE Connect", result["kde"]["error"])

    def test_kde_phones_only_and_battery(self):
        def bus(path, interface, method, *args):
            if method == "devices":
                return ["phone1", "desktop1"]
            if path.endswith("battery"):
                return {"charge": {"data": 73}, "isCharging": {"data": True}}
            return {"name": {"data": "Pixel 9"}, "type": {"data": "phone" if path.endswith("phone1") else "desktop"}, "isReachable": {"data": True}}
        with patch.object(phone, "bus", side_effect=bus), patch.object(phone.commands, "available", return_value="installed"):
            devices = phone.kde_devices()["devices"]
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["battery"], 73)
        self.assertTrue(devices[0]["charging"])

    def test_unknown_actions_rejected(self):
        with self.assertRaises(phone.UserError):
            phone.action({"action": "shell", "command": "id"})

    def test_disconnect_pauses_known_identity_and_only_target_transport(self):
        phone.write_connection_state({'phones':[{'identity':'KNOWN','name':'Pixel','address':'192.168.1.2:43200'}]})
        service = {'name':'adb-phone','identity':'KNOWN','kind':'connect','address':'192.168.1.2:43200'}
        with patch.object(phone,'checked',side_effect=['List of devices attached\nadb-phone._adb-tls-connect._tcp device model:Pixel', 'disconnected']), patch.object(phone,'stop_session') as stop, patch.object(phone,'discover',return_value=[service]):
            result = phone.action({'action':'disconnect','serial':'adb-phone._adb-tls-connect._tcp'})
        self.assertTrue(result['ok'])
        stop.assert_called_once_with('adb-phone._adb-tls-connect._tcp')
        self.assertTrue(phone.connection_state()['phones'][0]['paused'])
        self.assertEqual(phone.connection_candidates([service],[],phone.connection_state()), [])


if __name__ == "__main__":
    unittest.main()
