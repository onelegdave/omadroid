#!/usr/bin/python3
"""Install OmaDroid locally; no subprocesses, downloads, or post-install execution."""
import copy
import json
import os
from pathlib import Path
import stat
import secrets
from contextlib import ExitStack
from safe_files import (UnsafePath, MAX_CONFIG, home_directory, directory, read_file,
                        inspect, snapshot_tree, write_tree, atomic_write, validate)

PLUGIN_ID = 'onelegdave.omadroid'
LEGACY_PLUGIN_ID = 'onelegdave.phone-mirror'
FILES = ('manifest.json', 'phone_mirror.py', 'safe_files.py', 'safe_process.py',
         'install-deps.sh', 'README.md', 'VALIDATION.md', 'SECURITY.md', 'LICENSE', 'Panel.qml')

def migrate_settings(settings):
    migrated = copy.deepcopy(settings)
    entries = [entry for section in migrated.get('bar', {}).get('layout', {}).values() for entry in section]
    entries += migrated.get('plugins', [])
    ids = {entry.get('id') for entry in entries}
    if LEGACY_PLUGIN_ID in ids and PLUGIN_ID in ids:
        raise UnsafePath('Both old and new OmaDroid IDs are configured; keep the desired entry before installing')
    for entry in entries:
        if entry.get('id') == LEGACY_PLUGIN_ID:
            entry['id'] = PLUGIN_ID
    for key in ('disabledPlugins', 'cloneSourceRestores'):
        if key in migrated and PLUGIN_ID not in ids:
            migrated[key] = list(dict.fromkeys(PLUGIN_ID if name == LEGACY_PLUGIN_ID else name for name in migrated[key]))
    if migrated.get('bar', {}).get('centerAnchor') == LEGACY_PLUGIN_ID:
        migrated['bar']['centerAnchor'] = PLUGIN_ID
    return migrated


def install_files(home, source, config_home=None):
    # Preload the fixed deliverable with bounded, nofollow reads.
    with home_directory(source) as source_fd:
        deliverable = {}
        for name in FILES:
            data, info = read_file(source_fd, name)
            if info is None:
                raise UnsafePath('Missing plugin file: ' + name)
            deliverable[name] = (data, stat.S_IMODE(info.st_mode))
    if json.loads(deliverable['manifest.json'][0])['id'] != PLUGIN_ID:
        raise UnsafePath('Manifest ID does not match installer')

    with ExitStack() as stack:
        home_fd = stack.enter_context(home_directory(home))
        if config_home is None:
            config_parent = stack.enter_context(directory(home_fd, '.config', create=True))
        else:
            config_parent = stack.enter_context(home_directory(config_home))
        config = stack.enter_context(directory(config_parent, 'omarchy', create=True))
        plugins = stack.enter_context(directory(config, 'plugins', create=True))
        backups = stack.enter_context(directory(config, 'plugin-backups', create=True))
        raw, shell_info = read_file(config, 'shell.json', MAX_CONFIG)
        settings = json.loads(raw) if raw is not None else {}
        migrated = migrate_settings(settings)
        entries = [entry for section in migrated.get('bar', {}).get('layout', {}).values() for entry in section]
        if not any(entry.get('id') == PLUGIN_ID for entry in entries):
            migrated.setdefault('bar', {}).setdefault('layout', {}).setdefault('right', []).append({'id': PLUGIN_ID})
            if 'disabledPlugins' in migrated and LEGACY_PLUGIN_ID not in settings.get('disabledPlugins', []):
                migrated['disabledPlugins'] = [name for name in migrated['disabledPlugins'] if name != PLUGIN_ID]
        # Validate ALL existing trees before overwriting any installed file.
        trees = {}
        targets = {}
        for name in (PLUGIN_ID, LEGACY_PLUGIN_ID):
            if inspect(plugins, name, directory=True) is not None:
                targets[name] = stack.enter_context(directory(plugins, name))
                trees[name] = snapshot_tree(targets[name])
                if name == PLUGIN_ID:
                    for filename in FILES:
                        inspect(targets[name], filename)
        if LEGACY_PLUGIN_ID in targets:
            # Keep the validated old tree untouched and backed up, but never run
            # both polling widgets. Removal remains a separate Omarchy action.
            disabled = migrated.setdefault('disabledPlugins', [])
            if LEGACY_PLUGIN_ID not in disabled:
                disabled.append(LEGACY_PLUGIN_ID)
        stamp = secrets.token_hex(16)
        for name, tree in trees.items():
            backup_name = name + '-' + stamp
            os.mkdir(backup_name, 0o700, dir_fd=backups)
            with directory(backups, backup_name) as backup:
                write_tree(backup, tree)
        if raw is not None:
            atomic_write(config, 'shell.json.bak-omadroid-' + stamp, raw,
                         mode=stat.S_IMODE(shell_info.st_mode))
        if PLUGIN_ID not in targets:
            # Refuse a concurrent/pre-positioned target instead of opening it.
            os.mkdir(PLUGIN_ID, 0o700, dir_fd=plugins)
            targets[PLUGIN_ID] = stack.enter_context(directory(plugins, PLUGIN_ID))
        target = targets[PLUGIN_ID]
        for name in [n for n in deliverable if n != 'Panel.qml'] + ['Panel.qml']:
            data, mode = deliverable[name]
            atomic_write(target, name, data, expected=inspect(target, name), mode=mode)
        if migrated != settings:
            atomic_write(config, 'shell.json', (json.dumps(migrated, indent=2) + '\n').encode(),
                         expected=shell_info, mode=stat.S_IMODE(shell_info.st_mode) if shell_info else 0o600)
        return migrated


def main():
    config_home = os.environ.get('XDG_CONFIG_HOME') or None
    install_files(Path.home(), Path(__file__).absolute().parent, config_home)
    print('Installed OmaDroid. To load the update, run: omarchy restart shell')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('Installation refused: ' + str(error)) from error
