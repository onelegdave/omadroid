import json
import os
from pathlib import Path
import tempfile
import unittest
import install


class InstallerTests(unittest.TestCase):
    def test_xdg_install_preserves_existing_settings_and_bar_position(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)/'home';home.mkdir()
            config=Path(temp)/'config';config.mkdir()
            omarchy=config/'omarchy';omarchy.mkdir()
            previous={'bar':{'layout':{'left':[{'id':install.PLUGIN_ID,'screenOff':True}]}},'idle':{'lock':1200}}
            (omarchy/'shell.json').write_text(json.dumps(previous))
            install.install_files(home,Path(install.__file__).parent,config)
            self.assertEqual(json.loads((omarchy/'shell.json').read_text()),previous)
            self.assertTrue((omarchy/'plugins'/install.PLUGIN_ID/'Panel.qml').is_file())

    def test_invalid_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);config=home/'.config/omarchy';config.mkdir(parents=True)
            (config/'shell.json').write_text('invalid JSON')
            with self.assertRaises(ValueError):
                install.install_files(home,Path(install.__file__).parent)
            self.assertEqual((config/'shell.json').read_text(),'invalid JSON')
            self.assertFalse((config/'plugins'/install.PLUGIN_ID).exists())
