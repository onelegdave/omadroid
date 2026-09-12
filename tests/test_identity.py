import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import phone_mirror as phone


def device(serial, identity=None, name='Same Model', wireless=True, mirroring=False):
    result = dict(serial=serial, name=name, wireless=wireless, mirroring=mirroring, state='device')
    if identity:
        result['hardwareId'] = identity
    return result


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, XDG_STATE_HOME=self.temp.name, XDG_CACHE_HOME=self.temp.name)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_ipv4_ipv6_usb_share_one_named_phone_and_keep_active_transport(self):
        devices = [device('USB1234', 'REAL1234', wireless=False),
                   device('[fd00:db8::1]:32100', 'REAL1234'),
                   device('192.168.1.2:32100', 'REAL1234', mirroring=True)]
        devices[1]['customName'] = 'My Fold'
        groups = phone.group_devices(devices)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['name'], 'My Fold')
        self.assertEqual(groups[0]['serial'], '192.168.1.2:32100')
        self.assertEqual(len(groups[0]['transports']), 3)
        self.assertEqual(groups[0]['connectionSummary'], 'USB + Wi-Fi · 3 connections')

    def test_identical_models_and_unresolved_identities_are_never_merged(self):
        devices = [device('a', 'REAL1234'), device('b', 'REAL5678'), device('c'), device('d')]
        self.assertEqual(len(phone.group_devices(devices)), 4)

    def test_missing_serial_cannot_turn_custom_name_into_identity(self):
        with patch.object(phone, 'checked', return_value='serial=\nboot=\nname=MyPhone'):
            devices = phone.identify_devices([device('a'), device('b')], [])
        self.assertTrue(all(d['customName'] == 'MyPhone' for d in devices))
        self.assertTrue(all('hardwareId' not in d for d in devices))
        self.assertEqual(len(phone.group_devices(devices)), 2)

    def test_authorized_identity_and_name_are_read_on_each_transport(self):
        def query(args, **kwargs):
            self.assertEqual(args[:2], ['adb', '-s'])
            return 'serial=REAL1234\nboot=REAL1234\nname=My Fold'
        with patch.object(phone, 'checked', side_effect=query):
            devices = phone.identify_devices([device('a'), device('b')], [])
        self.assertEqual(len(phone.group_devices(devices)), 1)
        self.assertEqual(devices[0]['hardwareId'], 'REAL1234')

    def test_alias_metadata_preserves_pause_and_survives_disconnection(self):
        state = {'phones': [dict(identity='a', address='192.168.1.2:32100', name='Model', paused=True)]}
        phone.write_connection_state(state)
        d = device('192.168.1.2:32100', 'REAL1234')
        d['customName'] = 'My Fold'
        phone.learn_identities([d])
        self.assertEqual(phone.connection_state(), state)
        profile = phone.known_phones(state, phone.identity_cache())[0]
        self.assertEqual(profile['hardwareId'], 'REAL1234')
        self.assertEqual(profile['name'], 'My Fold')
        self.assertTrue(profile['paused'])
        self.assertEqual(phone.saved_path().with_name('identities.json').stat().st_mode & 0o777, 0o600)

    def test_disconnected_aliases_have_one_saved_card(self):
        addresses = ['192.168.1.2:32100', '[fd00:db8::1]:32100']
        phone.write_connection_state({'phones': [dict(identity=a, address=a, name='Model', paused=True) for a in addresses]})
        devices = [device(a, 'REAL1234') for a in addresses]
        for d in devices:
            d['customName'] = 'My Fold'
        phone.learn_identities(devices)
        with patch.object(phone.commands, 'available', return_value=None):
            result = phone.status()
        self.assertEqual(len(result['remembered']), 1)
        self.assertEqual(result['remembered'][0]['name'], 'My Fold')
        self.assertTrue(result['remembered'][0]['paused'])

    def test_connected_phone_does_not_autoconnect_second_ip(self):
        services = [dict(name='adb-phone', identity='REAL1234', kind='connect', address='192.168.1.2:32100')]
        state = {'phones': [dict(identity='REAL1234', name='Fold', address='192.168.1.2:32100')]}
        self.assertEqual(phone.connection_candidates(services, [device('[fd00:db8::1]:32100', 'REAL1234')], state), [])

    def test_stop_phone_uses_fresh_identity_and_excludes_same_model_other_phone(self):
        listing = 'a device model:Same_Model\nb device model:Same_Model\nc device model:Same_Model'
        def query(args, **kwargs):
            if args == ['adb', 'devices', '-l']:
                return listing
            return 'serial=' + ('REAL5678' if args[2] == 'c' else 'REAL1234') + '\nboot=\nname=My Fold'
        with patch.object(phone, 'checked', side_effect=query), patch.object(phone, 'stop_session') as stop:
            phone.action(dict(action='stop', serial='a', scope='phone'))
        self.assertEqual([call.args[0] for call in stop.call_args_list], ['a', 'b'])

    def test_group_disconnect_closes_both_ips_and_preserves_other_phone(self):
        a, b, c = '192.168.1.2:32100', '[fd00:db8::1]:32100', '192.168.1.3:32100'
        listing = '\n'.join(f'{serial} device model:Same_Model' for serial in (a,b,c))
        disconnected = []
        def query(args, **kwargs):
            if args == ['adb', 'devices', '-l']:
                return listing
            if args[:2] == ['adb', 'disconnect']:
                disconnected.append(args[2])
                return 'disconnected'
            return 'serial=' + ('REAL5678' if args[2] == c else 'REAL1234') + '\nboot=\nname=My Fold'
        with patch.object(phone, 'checked', side_effect=query), patch.object(phone, 'stop_session'), patch.object(phone, 'discover', return_value=[]):
            phone.action(dict(action='disconnect', serial=a, scope='phone'))
        self.assertEqual(set(disconnected), {a,b})
        self.assertTrue(all(p['paused'] for p in phone.connection_state()['phones']))

    def test_manual_connect_resumes_one_phone_and_removes_paused_aliases(self):
        a, b = '192.168.1.2:32100', '[fd00:db8::1]:32100'
        phone.write_connection_state({'phones': [dict(identity=s, address=s, name='Model', paused=True) for s in (a,b)]})
        phone.learn_identities([device(a, 'REAL1234'), device(b, 'REAL1234')])
        with patch.object(phone, 'checked', side_effect=['connected to '+a, a+' device model:Fold']):
            phone.connect_phone(a, [])
        profiles = phone.connection_state()['phones']
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]['hardwareId'], 'REAL1234')
        self.assertFalse(profiles[0].get('paused', False))


if __name__ == '__main__':
    unittest.main()
