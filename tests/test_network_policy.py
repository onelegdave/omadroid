"""Regression tripwires: changes to runtime networking require explicit review."""
import ast
from pathlib import Path
import unittest
from unittest.mock import patch
import phone_mirror as phone
import safe_process as commands

ROOT=Path(__file__).resolve().parents[1]

class NetworkPolicyTests(unittest.TestCase):
    def test_runtime_has_no_network_client_imports(self):
        forbidden={'socket','http','urllib','requests','httpx','aiohttp','ftplib','smtplib','telnetlib','websockets'}
        for name in ('phone_mirror.py','safe_process.py','safe_files.py','install.py'):
            tree=ast.parse((ROOT/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):modules=[item.name for item in node.names]
                elif isinstance(node,ast.ImportFrom):modules=[node.module or '']
                else:continue
                self.assertFalse(forbidden.intersection(module.split('.')[0] for module in modules),name)
        qml=(ROOT/'Panel.qml').read_text()
        # The author credit may open this fixed URL only from its click handler.
        author_link='onClicked: Qt.openUrlExternally("https://www.onelegdave.dev/")'
        self.assertEqual(qml.count(author_link),1)
        qml=qml.replace(author_link,'')
        for token in ('XMLHttpRequest','WebSocket','WebEngine','https://','http://','execDetached','openUrlExternally'):
            self.assertNotIn(token,qml)
    def test_helper_allowlist_cannot_silently_gain_downloaders(self):
        expected={'adb','scrcpy','avahi-browse','busctl','kdeconnect-cli','kdeconnect-app','notify-send','pacman','systemctl','systemd-run','bash','python3','omarchy-launch-terminal'}
        self.assertEqual(set(commands.TOOLS),expected)
        self.assertTrue(all(path=='/usr/bin/'+name for name,path in commands.TOOLS.items()))
    def test_public_target_is_rejected_before_connect_or_pair(self):
        with patch.object(phone,'run') as run,patch.object(phone,'checked') as checked:
            for action in ('connect','pair'):
                with self.assertRaises(phone.UserError):
                    phone.action({'action':action,'address':'8.8.8.8:1234','code':'123456'})
        run.assert_not_called();checked.assert_not_called()
    def test_public_connected_transport_is_not_probed_for_identity(self):
        with patch.object(phone,'checked') as checked:
            result=phone.identify_devices([{'serial':'8.8.8.8:1234','state':'device','name':'Phone','wireless':True}],[])
        checked.assert_not_called()
        self.assertEqual(result[0]['state'],'unsupported address')

    def test_shared_range_gets_vpn_guidance_without_running_adb(self):
        with patch.object(phone,'run') as run, patch.object(phone,'checked') as checked:
            for address in ('100.64.0.1:33371', '100.89.1.2:33371', '100.127.255.254:33371'):
                for action in ('pair', 'connect'):
                    with self.subTest(address=address, action=action), self.assertRaisesRegex(phone.UserError, "Tailscale.*Wi-Fi"):
                        phone.action({'action': action, 'address': address, 'code': '123456'})
        run.assert_not_called(); checked.assert_not_called()

    def test_shared_range_guidance_does_not_widen_address_policy(self):
        for address in ('100.63.255.254:33371', '100.128.0.1:33371'):
            with self.assertRaisesRegex(phone.UserError, 'private local-network'):
                phone.endpoint(address)
        self.assertEqual(phone.endpoint('192.168.1.2:33371'), '192.168.1.2:33371')
