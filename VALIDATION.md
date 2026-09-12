# Validation — 0.3.2

Additional verification for 0.3.2 on 2026-09-12:

- Included the locally updated marketplace preview with the private phone address redacted. Visually checked the final image. Runtime behavior is unchanged; synchronized version labels and release documentation. QML lint, manifest validation, and four network-policy regression tests pass.

Additional verification for 0.3.1 on 2026-09-12:

- Release preparation reran all 85 Python tests, QML lint, and Omarchy plugin validation successfully from the final `omadroid` project folder.
- Captured and visually inspected the actual live mirror and phone controls for root `preview.png`, plus the connection guide, settings, and Help. See docs/screenshots.md for capture details. The temporary mirror was stopped and the original desktop workspace restored; the existing ADB connection remained available.

- Added a theme-aware OneLegDave button at the bottom of Help. Its click handler opens only the fixed author HTTPS URL in the default browser; opening Help makes no website request. No phone data or tracking parameters are added.
- All four network-policy regression tests pass with a narrow exception for the exact author-link click handler. QML lint and manifest JSON validation pass. Backend behavior is unchanged from 0.3.0.

Additional verification for 0.3.0 on 2026-09-12:

- Read all comments and the linked event on marketplace issue 6136. Applied the installer and process-boundary lessons to OmaDroid; details and exact review links are in SECURITY.md. No marketplace messages or publication were performed.
- Hardened installer and runtime filesystem access, fixed/verified executable identities, clean helper environments, bounded command streams and deadlines, process-group cleanup, bounded mirror startup and 1 MiB session logs. Removed automatic installer subprocesses. Dependency installation remains an explicit terminal action with fixed package groups.
- Restricted phone endpoints to private network address ranges, fixed the ADB client to its loopback server, disabled automatic clipboard sync, and added regression tripwires for network-client imports, QML network APIs, the command allowlist, and public targets.
- The complete Python suite includes 85 tests. A test initially inherited the real XDG configuration path; the installed 0.2.2 files were restored from the automatically created backup, the fixture was corrected to isolate XDG_CONFIG_HOME, and subsequent complete runs used the corrected fixture.
- A live network syscall trace of status collection and a real mirror session observed only Unix sockets and loopback 127.0.0.1:5037 from the backend and traced child helpers. The already-running ADB/Avahi/KDE services were outside this trace. The phone display was visually verified; Stop closed the mirror, and the prior disconnected state/preferences were restored.
- A harmless visible-terminal probe verified the new installer launch route and its clean child environment without installing any packages. QML lint and manifest validation pass. This is not an independent audit of the OS, every dependency, or phone applications.

Additional verification for 0.2.2 on 2026-09-12:

- Normal polling is 10 seconds with the panel open and 30 seconds closed. The connection guide uses 3-second polling during preparation/pairing, bounded to two minutes after setup interaction. Ready phones return to the normal cadence. Opening the panel and completing an action still request a status check.
- Replaced the once-per-second age counter with a fixed last-check timestamp. Background checks no longer flash the Refresh button disabled; repeated clicks remain guarded against overlapping status processes. Stale-status wording allows for the slower background interval.
- QML lint and manifest validation pass. Live IPC observation measured normal completion intervals and checked the setup and closed-panel timer settings. The backend and its 42 passing tests are unchanged from 0.2.1.

Additional verification for 0.2.1 on 2026-09-12:

- Reproduced the duplicate on the real Samsung phone: IPv4 and IPv6 ADB transports reported the same Android hardware serial and custom name. Both connections now render as one `oldsprime` card with a two-connection summary. After disconnecting both, one remembered card remains. The verification restored the original disconnected state and did not change pairing or automatic reconnect pause settings.
- The new grouped Disconnect action was exercised against both real addresses and closed both connections while preserving the existing paused profiles.
- Identity queries run only on authorized devices and use fixed commands. Grouping never relies on model names or nicknames. Saved metadata is written separately from pause settings, privately and atomically. Group Stop/Disconnect resolves identity afresh before selecting connections.
- All 42 tests pass, including identity fallback, identical-model phones, IPv4/IPv6/USB grouping, preserving the active mirror, avoiding redundant automatic connections, offline aliases, resuming a paused phone, and limiting Stop/Disconnect to the selected physical phone.
- Removed fixed status/button text colors. Popup colors, theme accents, named typography tokens, control fills, and corner rounding follow the shell. A temporary live Catppuccin Latte palette verified light backgrounds, dark text, blue accents, readable selected buttons, and the Phones, Settings, and Connect layouts. The original black/magenta palette was restored through the shell theme API. The user's font-size override remained effective during the preview.
- QML lint and manifest validation pass. This checks the current dark theme and a light palette, not every third-party theme or monitor scale.

Additional verification for 0.2.0 on 2026-09-12:

- Renamed the app OmaDroid while preserving its plugin ID and saved settings. Installed the new Phones, Connect, Settings, and Help interface in the live Omarchy shell and inspected it in the current desktop theme at 1920×1080. Existing mirroring continued across the shell restart.
- Added an in-app four-step setup guide, separate IP/port fields, expandable instructions, and package installation buttons. The terminal installer uses fixed package groups, reports failures, and shows the privilege prompt in the terminal. No new packages were needed for the current active wireless mirror.
- All 32 tests pass. New coverage checks exact dependency package groups, discovery service activation, failure propagation, and rejection of unknown groups without running commands. Installer tests use isolated fake commands; they do not claim fresh installation on a clean desktop.
- Stop is covered by subprocess integration tests for ending only the selected supervised mirror. Disconnect regression tests verify that automatic reconnection is paused for both literal-address and discovered mDNS transports. A fresh manual connection clears that pause.
- Keyboard navigation reached the Phone and Pair steps in the live panel. Submitting an invalid IP with a dummy pairing code showed the expected validation error and cleared the code.
- QML lint and manifest validation pass. Status polling retains unchanged device models to preserve focus. The GUI exposes status age and distinguishes ADB connections from KDE Connect reachability.
- The user reports the real phone mirror and controls work well. Cross-device coverage remains limited to this development phone; broader compatibility checks below are still outstanding.

Additional verification for 0.1.2 on 2026-09-12:

- Reproduced the reported state: the Fold8 was in `Dozing` with a 30,000 ms screen timeout, connected over Wi-Fi on battery while physical screen-off mode was enabled. ADB's wake command changed the power state to `Awake`; Android's normal `wm dismiss-keyguard` command was available.
- Added Wake / unlock to each connected phone and a default-on Keep phone awake option using scrcpy's `--keep-active`. The mirror was restarted with both keep-active and physical screen-off enabled. The saved Android screen timeout remains unchanged.
- The real Fold8 remained `Awake` at 0, 15, 30 and 45 seconds of observation with its 30,000 ms timeout unchanged. The active scrcpy process was verified to include both `--keep-active` and `--turn-screen-off`. The Wake / unlock button was visually verified in the installed panel.
- All 26 tests pass, including explicit wake device selection, secure-keyguard prompt invocation, rejecting unauthorized devices, keep-active with physical screen-off, and actionable errors on older scrcpy versions. QML lint and manifest validation pass.
- Wake requests leave Android responsible for authentication. PIN/biometric handling on every vendor/Android version has not been validated.

Additional verification for 0.1.1 on 2026-09-12:

- Diagnosed successful pairing without the separate ADB connection. Completed that connection against the advertised connection port, launched scrcpy, and visually verified the Fold8 home screen in a native Wayland mirror window. The connection remained present over several minutes of development checks and a shell restart.
- Added automatic completion after pairing, saved discovery identities for reconnection, explicit mirroring status, persistent disconnected-device rows, and separate KDE Connect online/offline wording.
- All 23 tests pass, including regressions for connection-port selection after pairing, deferred discovery, offline devices not being reported ready, remembered-identity reconnection after port changes, and excluding unknown devices.
- QML lint and manifest validation pass. Live shell rendering was inspected after restarting the shell to discard cached QML. Physical video was verified; automatic reconnection and pairing completion are covered by regression tests but have not yet been exercised through a fresh physical pairing/reboot cycle.

Verified on the development desktop on 2026-09-12:

- Omarchy manifest validation passes.
- Qt 6 qmllint passes with Omarchy's real shell imports and no warnings.
- Backend unit tests cover USB states, device selection, pairing/connection port separation, malformed input, code secrecy, ADB failures with zero exit status, saved connections, battery data, and the Avahi fallback.
- Subprocess integration tests use fake ADB/scrcpy executables to verify early errors, later error notifications, session survival after the launcher exits, and duplicate-session prevention.
- The installed plugin was loaded by the live shell. Overview and wireless setup layouts were visually inspected at 1920×1080, with no plugin QML errors in the shell log. Keyboard Tab focus is visible. Submitting an invalid pairing address through the real form produced the expected error and cleared the dummy code.
- Live KDE Connect discovery recognized a Galaxy Z Fold8, observed offline-to-online transition, and read its 71% battery charge. The battery API was also checked against [KDE's source](https://github.com/KDE/kdeconnect-kde/blob/master/plugins/battery/batteryplugin.h).
- scrcpy 4.1-2 and android-tools 37.0.0-5 were installed. This Arch ADB build reports no native mDNS support; discovery falls back to the installed Avahi tools without error.

Still requires physical-device validation:

- A new physical pairing and automatic connection through the revised guide (existing video and control are confirmed by the user).
- USB authorization and input on a real phone.
- Audio, fold/unfold transitions, device rotation, and reconnects after phone reboot or network changes.
- Multiple simultaneous physical phones, other vendors/Android releases, and other monitor scales/bar positions.

This is a development build, not a claim of completed cross-device compatibility testing. The original 0.1.0 checks preceded phone authorization; 0.1.1 added the real wireless video verification described above.
