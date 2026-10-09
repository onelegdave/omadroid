"""Offline daemon model: record intended dials; never open network sockets.

The service-first lookup models AOSP adb_wifi_pair_device() and
socket_spec_connect(). See SECURITY.md for pinned upstream source and limits.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import phone_mirror as phone
import safe_process as commands


FIXTURE = '''import json, os, sys
from pathlib import Path
root = Path(os.environ['OMADROID_TEST_ROOT'])
state = json.loads((root / 'daemon.json').read_text())
args = sys.argv[1:]
if args == ['server-status']:
    print(state['status'])
    sys.exit(state.get('status_code', 0))
if args[0] in ('pair', 'connect'):
    # A pre-existing server retains its registry even when the client has
    # ADB_MDNS=0. Numeric IP:port is also a possible instance name.
    address = args[1]
    destination = state['registry'].get(address, address) if state['enabled'] else address
    with (root / 'dials.jsonl').open('a') as stream:
        stream.write(json.dumps({'args': args, 'destination': destination}) + '\\n')
    if args[0] == 'pair':
        code = sys.stdin.read()
        if state.get('pair_failure'):
            print('Failed: ' + code.strip())
            sys.exit(1)
        assert code == '123456\\n'
        print('Successfully paired to ' + address)
    else:
        print('connected to ' + address)
elif args == ['devices', '-l']:
    print('List of devices attached\\n' + state['address'] + ' device model:Fixture_Phone')
else:
    raise SystemExit('Unexpected fixture command')
'''


class AdbDestinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = self.root / 'adb.py'
        self.fixture.write_text(FIXTURE)
        self.calls = []

        @contextmanager
        def launch(name, args, input=False, script=False):
            self.assertEqual(name, 'adb')
            self.calls.append((list(args), input))
            env = commands.tool_environment()
            env['OMADROID_TEST_ROOT'] = str(self.root)
            process = subprocess.Popen([sys.executable, '-I', str(self.fixture), *args],
                                       stdin=subprocess.PIPE if input else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=env, start_new_session=True)
            try:
                yield process
            finally:
                commands.cleanup(process)

        launcher = patch.object(commands, 'launch', launch)
        launcher.start()
        self.addCleanup(launcher.stop)

    def daemon(self, address='192.168.1.20:37123', enabled=True, destination='203.0.113.10:443', **extra):
        self.calls.clear()
        self.root.joinpath('dials.jsonl').unlink(missing_ok=True)
        state = {'enabled': enabled, 'address': address, 'registry': {address: destination},
                 'status': 'mdns_enabled: ' + str(enabled).lower(), **extra}
        self.root.joinpath('daemon.json').write_text(json.dumps(state))

    def dials(self):
        path = self.root / 'dials.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_service_first_model_demonstrates_numeric_substitution_before_auth(self):
        # This is a model of the audited resolver, not a live mDNS experiment.
        address = phone.endpoint('192.168.1.20:37123')
        for action in ('pair', 'connect'):
            for destination in ('203.0.113.10:443', '127.0.0.1:4444', '[::1]:4444'):
                with self.subTest(action=action, destination=destination):
                    self.daemon(address, destination=destination, pair_failure=True)
                    # Exercise the unguarded fixture to demonstrate why input
                    # validation and a client-only environment change fail.
                    with commands.launch('adb', [action, address], input=action == 'pair') as process:
                        commands.collect(process, [], 2, '123456\n' if action == 'pair' else None)
                    self.assertEqual(self.dials()[0]['destination'], destination)

    def test_existing_mdns_daemon_is_blocked_before_any_dial_or_code_submission(self):
        for address in ('192.168.1.20:37123', '[fd00::20]:37123', '[fe80::20%eth0]:37123'):
            for action in ('pair', 'connect'):
                for destination in ('203.0.113.10:443', '127.0.0.1:4444', '[::1]:4444'):
                    with self.subTest(address=address, action=action, destination=destination):
                        self.daemon(address, destination=destination)
                        with self.assertRaisesRegex(phone.UserError, 'mDNS disabled') as error:
                            phone.action({'action': action, 'address': address, 'code': '123456'})
                        self.assertNotIn('123456', str(error.exception))
                        self.assertEqual(self.calls, [(['server-status'], False)])
                        self.assertEqual(self.dials(), [])

    def test_unknown_failed_and_ambiguous_daemon_status_fail_closed(self):
        for status, rc in (('', 0), ('mdns_backend: OPENSCREEN', 0),
                           ('mdns_enabled: false', 1), ('mdns_enabled: true', 0),
                           ('mdns_enabled: false\nmdns_enabled: true', 0),
                           ('mdns_enabled: false\nmdns_enabled: false', 0)):
            for action in ('pair', 'connect'):
                with self.subTest(status=status, rc=rc, action=action):
                    self.daemon(status=status, status_code=rc)
                    with self.assertRaises(phone.UserError):
                        phone.action({'action': action, 'address': '192.168.1.20:37123', 'code': '123456'})
                    self.assertEqual(self.calls, [(['server-status'], False)])
                    self.assertEqual(self.dials(), [])

    def test_disabled_daemon_uses_literal_endpoint_and_preserves_stdin_pairing(self):
        for address in ('192.168.1.20:37123', '[fd00::20]:37123', '[fe80::20%eth0]:37123'):
            for action in ('pair', 'connect'):
                with self.subTest(address=address, action=action):
                    self.daemon(address, enabled=False)
                    with patch.object(phone, 'finish_pairing', return_value={'paired': True}), \
                         patch.object(phone, 'remember'), patch.object(phone, 'discover', return_value=[]), \
                         patch.object(phone, 'identity_cache', return_value={}), \
                         patch.object(phone, 'connection_state', return_value={}), \
                         patch.object(phone, 'write_connection_state'):
                        result = phone.action({'action': action, 'address': address, 'code': '123456'})
                    self.assertTrue(result.get('paired') or result.get('connected'))
                    self.assertEqual(self.calls[:2], [(['server-status'], False), ([action, address], action == 'pair')])
                    self.assertEqual(self.dials(), [{'args': [action, address], 'destination': address}])
                    self.assertNotIn('123456', json.dumps(result))

    def test_pair_failure_does_not_reveal_echoed_code(self):
        self.daemon(enabled=False, pair_failure=True)
        with self.assertRaises(phone.UserError) as error:
            phone.action({'action': 'pair', 'address': '192.168.1.20:37123', 'code': '123456'})
        self.assertNotIn('123456', str(error.exception))

    def test_server_state_is_rechecked_for_every_connection(self):
        self.daemon(enabled=False)
        commands.run(['adb', 'connect', '192.168.1.20:37123'])
        self.assertEqual(len(self.dials()), 1)
        self.daemon(enabled=True)
        with self.assertRaises(commands.ToolError):
            commands.run(('adb', 'connect', '192.168.1.20:37123'))
        self.assertEqual(self.calls, [(['server-status'], False)])
        self.assertEqual(self.dials(), [])

    def test_service_names_and_disallowed_literal_destinations_never_reach_adb(self):
        for address in ('fixture._adb-tls-pairing._tcp', 'fixture._adb-tls-connect._tcp',
                        '203.0.113.10:443', '127.0.0.1:4444', '[::1]:4444',
                        '100.64.0.1:4444', '0.0.0.0:4444', '[::]:4444', '224.0.0.1:4444'):
            for action in ('pair', 'connect'):
                with self.subTest(address=address, action=action):
                    with self.assertRaises(phone.UserError):
                        phone.action({'action': action, 'address': address, 'code': '123456'})
        self.assertEqual(self.calls, [])

    def test_empty_native_discovery_uses_avahi_and_validates_advertised_destination(self):
        output = '\n'.join('=;eth0;IPv4;192.168.1.20:37123;_adb-tls-connect._tcp;local;fixture.local;'
                           + address + ';37123;' for address in ('203.0.113.10', '127.0.0.1', '192.168.1.20'))

        def checked(args, *rest):
            if args[0] == 'adb':
                return 'List of discovered mdns services\n'
            return output if args[-1] == '_adb-tls-connect._tcp' else ''

        with patch.object(phone, 'checked', side_effect=checked), \
             patch.object(commands, 'available', return_value=True):
            services = phone.discover()
        self.assertEqual([service['address'] for service in services], ['192.168.1.20:37123'])
        self.daemon(enabled=False)
        with patch.object(phone, 'remember'), patch.object(phone, 'identity_cache', return_value={}), \
             patch.object(phone, 'connection_state', return_value={}), patch.object(phone, 'write_connection_state'):
            self.assertTrue(phone.connect_phone(services[0]['address'], services)['connected'])
        self.assertEqual(self.dials()[0]['destination'], '192.168.1.20:37123')

    def test_native_discovery_checks_addresses_instead_of_trusting_instance_names(self):
        output = '\n'.join('192.168.1.20:37123 _adb-tls-' + kind + '._tcp. ' + destination
                           for kind in ('pairing', 'connect')
                           for destination in ('203.0.113.10:443', '127.0.0.1:4444', '[::1]:4444',
                                               '192.168.1.20:37123'))
        with patch.object(phone, 'checked', return_value=output), \
             patch.object(commands, 'available') as available:
            services = phone.discover()
        available.assert_not_called()
        self.assertEqual([(service['kind'], service['address']) for service in services],
                         [('pair', '192.168.1.20:37123'), ('connect', '192.168.1.20:37123')])
