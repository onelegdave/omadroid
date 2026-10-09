# Changelog

## 0.3.7 — 2026-10-08

- Prevent ADB mDNS from substituting an advertised destination for a validated numeric endpoint. Disable mDNS in the helper environment and require the running daemon to report mDNS disabled before Pair/Connect; otherwise refuse with manual recovery guidance.
- Keep validated wireless discovery through Avahi. Preserve pairing-code confidentiality and the existing address/process protections.
- Add focused offline destination and daemon-state regressions. Thanks to [@HANCORE-linux](https://github.com/HANCORE-linux) for reporting the resolver bypass during marketplace review.

## 0.3.6 — 2026-10-07

- Disable unavailable desktop installation actions and show selectable manual commands for each missing tool group. Direct installer requests return useful guidance instead of a raw launcher error. Launcher symlink rejection remains intact; the OmarchyMac layout investigation in [#2](https://github.com/onelegdave/omadroid/issues/2) remains open.
- Check that ADB can start before marking the desktop ready, keeping setup available when a system library is missing. Thanks to [@HermeticOrmus](https://github.com/HermeticOrmus) for [#1](https://github.com/onelegdave/omadroid/pull/1).
- Recommend a full system update for shared-library failures, and skip device enumeration and wireless discovery when ADB cannot start.

## 0.3.5 - 2026-09-15

- Expand Help > About with maintainer, AI-assistance, and upstream license credits.
- Add website, GitHub, X, and optional Buy Me a Coffee links. Links open only on a click in the external browser.
- Preserve phone pairing, connection settings, and mirroring behavior.

## 0.3.4 — 2026-09-13

- Explain how to pair over Wi-Fi when Android displays a Tailscale or other VPN address, including returning to the VPN after pairing. Shared-range addresses remain unsupported and now get actionable guidance.
- Explain live nearby-phone discovery, per-computer authorization, and how Connect resumes an intentional Disconnect without pairing again. Connection failures now include recovery steps for VPN/network changes.
- Fix automatic startup of the local ADB daemon on a fresh desktop. No separate startup service is required.

## 0.3.3 — 2026-09-12

- Rename the plugin ID and shell command target to `onelegdave.omadroid`.
- Migrate existing bar placement, options, and enabled/disabled preferences with the local installer; back up and disable the old copy.
- Preserve saved phone connections and pairing. Refuse ambiguous configurations containing both plugin IDs.

## 0.3.2 — 2026-09-12

First public GitHub Release. Includes the final marketplace screenshot with the local phone address redacted, complete screenshot gallery, and installation documentation. Runtime behavior is unchanged from 0.3.1; version labels are synchronized.

## 0.3.1 — 2026-09-12

Initial published source snapshot.

- Mirror and control Android phones over USB or paired Wi-Fi using scrcpy and ADB.
- Guided desktop setup, phone preparation, pairing, and connection instructions, with explicit dependency installation buttons.
- Theme-aware Phones, Connect, Settings, and Help pages with optional KDE Connect battery status and Ring.
- Group confirmed USB, IPv4, and IPv6 transports into one phone card using the Android custom name.
- Stop, Disconnect, and Wake / unlock controls; keep the phone awake during mirroring without changing its saved timeout.
- Adaptive status polling: 10 seconds while open, 30 seconds while closed, and a bounded faster interval during setup.
- Hardened file access, verified helper executables, clean child environments, bounded output and session logs, and private-address phone connection policy.
- No telemetry or background website requests. The OneLegDave Help credit opens the author's website only when clicked.

Versions through 0.3.2 used the original development plugin ID; 0.3.3 includes its settings migration.
