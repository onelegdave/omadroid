# Working on OmaDroid

Created and maintained by OneLegDave. Report issues with your Omarchy, scrcpy, and Android versions and the action that failed. Remove pairing codes, device identifiers, addresses, private notifications, and personal app content from public diagnostics.

## Verify a change

```bash
/usr/bin/python3 -E -s -m unittest discover -s tests -v
bash lint-qml.sh
omarchy plugin validate .
```

Run these on an Omarchy development desktop. The security tests check the real ownership of distro helpers; an environment that remaps root-owned files will fail those checks. Tests must isolate HOME and XDG paths before running installer fixtures.

Install locally with `/usr/bin/python3 -E -s install.py`, then separately run `omarchy restart shell`. Preserve existing settings and plugin identity. Edit user-owned plugin source, never packaged files under `/usr/share/omarchy/`.

Check changed behavior in the actual panel. For visual changes, inspect dark and light theme behavior. Update SECURITY.md whenever network destinations, commands, permissions, installation, or storage behavior changes. The fixed author website link must remain click-only.

## Release

1. Synchronize the version in `manifest.json`, Panel.qml's status and Help credit, CHANGELOG.md, README release links, and release documentation.
2. Run the checks above and validate any changed runtime behavior. Review the tracked file list for private data or generated state.
3. Capture and visually inspect the actual app. Keep the selected marketplace screenshot at root as `preview.png` and other captures under `docs/screenshots/`.
4. Commit using the maintainer's Git identity, push, and create an immutable matching `vX.Y.Z` tag and GitHub Release. Attach the source archive and its SHA-256 checksum.
5. Follow the marketplace's current submission or update process. Include the same release URL and exact commit; never claim a new commit inherits an older snapshot's approval.
6. Verify remote HEAD, tag, release assets, author attribution, repository About metadata, and marketplace reports before announcing completion.
