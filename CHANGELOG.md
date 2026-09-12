# Changelog

## 0.3.1 — 2026-09-12

First public OmaDroid release.

- Mirror and control Android phones over USB or paired Wi-Fi using scrcpy and ADB.
- Guided desktop setup, phone preparation, pairing, and connection instructions, with explicit dependency installation buttons.
- Theme-aware Phones, Connect, Settings, and Help pages with optional KDE Connect battery status and Ring.
- Group confirmed USB, IPv4, and IPv6 transports into one phone card using the Android custom name.
- Stop, Disconnect, and Wake / unlock controls; keep the phone awake during mirroring without changing its saved timeout.
- Adaptive status polling: 10 seconds while open, 30 seconds while closed, and a bounded faster interval during setup.
- Hardened file access, verified helper executables, clean child environments, bounded output and session logs, and private-address phone connection policy.
- No telemetry or background website requests. The OneLegDave Help credit opens the author's website only when clicked.

The internal ID remains `onelegdave.phone-mirror` to preserve development users' settings and bar placement.
