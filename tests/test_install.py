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

    def test_legacy_install_migrates_options_and_backs_up_without_running_both(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); config=home/'.config/omarchy'; config.mkdir(parents=True)
            legacy=config/'plugins'/install.LEGACY_PLUGIN_ID; legacy.mkdir(parents=True)
            (legacy/'Panel.qml').write_text('old plugin')
            previous={'bar':{'layout':{'left':[{'id':'other'}, {'id':install.LEGACY_PLUGIN_ID,'screenOff':True,'quality':'Sharp'}]}},'idle':{'lock':1200}}
            (config/'shell.json').write_text(json.dumps(previous))
            result=install.install_files(home,Path(install.__file__).parent)
            self.assertEqual(result['bar']['layout']['left'],[{'id':'other'},{'id':install.PLUGIN_ID,'screenOff':True,'quality':'Sharp'}])
            self.assertEqual(result['idle'],previous['idle'])
            self.assertIn(install.LEGACY_PLUGIN_ID,result['disabledPlugins'])
            self.assertNotIn(install.PLUGIN_ID,result['disabledPlugins'])
            self.assertEqual((legacy/'Panel.qml').read_text(),'old plugin')
            backups=list((config/'plugin-backups').glob(install.LEGACY_PLUGIN_ID+'-*'))
            self.assertEqual((backups[0]/'Panel.qml').read_text(),'old plugin')
            self.assertEqual(install.install_files(home,Path(install.__file__).parent),result)

    def test_legacy_disabled_preference_stays_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); config=home/'.config/omarchy'; config.mkdir(parents=True)
            previous={'disabledPlugins':[install.LEGACY_PLUGIN_ID]}
            (config/'shell.json').write_text(json.dumps(previous))
            result=install.install_files(home,Path(install.__file__).parent)
            self.assertIn(install.PLUGIN_ID,result['disabledPlugins'])

    def test_ambiguous_ids_refused_before_replacing_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); config=home/'.config/omarchy'; config.mkdir(parents=True)
            previous={'bar':{'layout':{'left':[{'id':install.LEGACY_PLUGIN_ID}], 'right':[{'id':install.PLUGIN_ID}]}}}
            raw=json.dumps(previous); (config/'shell.json').write_text(raw)
            with self.assertRaisesRegex(ValueError,'Both old and new'):
                install.install_files(home,Path(install.__file__).parent)
            self.assertEqual((config/'shell.json').read_text(),raw)
            self.assertFalse((config/'plugins'/install.PLUGIN_ID).exists())

    def test_legacy_tree_symlink_refused_before_configuration_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); config=home/'.config/omarchy'; plugins=config/'plugins'; plugins.mkdir(parents=True)
            outside=home/'outside'; outside.mkdir(); (outside/'keep').write_text('untouched')
            (plugins/install.LEGACY_PLUGIN_ID).symlink_to(outside,target_is_directory=True)
            raw=json.dumps({'bar':{'layout':{'right':[{'id':install.LEGACY_PLUGIN_ID}]}}})
            (config/'shell.json').write_text(raw)
            with self.assertRaises(ValueError):
                install.install_files(home,Path(install.__file__).parent)
            self.assertEqual((config/'shell.json').read_text(),raw)
            self.assertEqual((outside/'keep').read_text(),'untouched')
            self.assertFalse((plugins/install.PLUGIN_ID).exists())
