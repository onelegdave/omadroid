# OmaDroid security and network behavior

Audit target: OmaDroid 0.3.1, 2026-09-12. The 0.3.1 change adds an explicit author website button to the audited 0.3.0 runtime. This describes the source shipped in this folder and the installed distro tools tested on the development desktop.

## No phone-home functionality

OmaDroid contains no telemetry, analytics, advertising, cloud account, cloud relay, remote configuration, automatic update check, crash upload, HTTP client, or remote-code download path. It does not transmit diagnostics or phone identities to the author, GitHub, Omarchy, or another reporting service. Opening the panel or Help does not fetch any website. The OneLegDave credit opens a fixed HTTPS address in the default browser only when clicked, with no query parameters or phone data. There is no prefetch, remote image, or embedded web view. After that click, the browser makes a normal website connection; its cookies and the site's own behavior are outside this plugin's control.

Runtime network activity is limited by the implemented interfaces and address policy:

| Operation | Communication |
| --- | --- |
| Read status, connect, pair, mirror, wake | Distro ADB client talks to the local ADB server at `127.0.0.1:5037`. ADB communicates with the authorized phone over USB or its private network address. |
| Discover nearby phones | ADB mDNS, or Avahi through the system D-Bus. Discovery uses the local network. |
| KDE Connect status and Ring | Local session D-Bus / distro KDE Connect client. KDE Connect handles its own paired-device traffic. |
| Desktop error notification | Local desktop notification service. Nothing is uploaded. |
| Author website | Only after clicking OneLegDave in Help: the default browser opens `https://www.onelegdave.dev/`. |
| Install dependencies | Only after the user chooses an Install button: a visible terminal runs `sudo /usr/bin/pacman -S --needed` with a fixed package group. Pacman uses the user's configured repositories/mirrors and signature policy. |
| Enable wireless discovery | Only after the user chooses discovery installation/enabling: `sudo /usr/bin/systemctl enable --now avahi-daemon.service`. |

Phone endpoints must be literal addresses in RFC 1918 IPv4, IPv4 link-local, IPv6 ULA, or IPv6 link-local ranges. Public Internet addresses, hostnames, loopback, unspecified, multicast, and limited-broadcast endpoints are rejected. Public IPv6 phone connections and remote ADB-server overrides are intentionally unsupported. Private addresses can still be routed through a user-configured VPN; this is an address restriction, not a firewall or physical-network guarantee.

ADB's remote-server environment overrides are replaced with the fixed loopback server address. Proxy variables, executable overrides, `LD_*`, `PYTHON*`, shell startup overrides, and ambient PATH are not passed to helpers. The scrcpy server resource comes from `/usr/share/scrcpy/scrcpy-server`, not an environment-selected download or script.

Regression tests reject network-client imports in runtime Python, remote-content/network APIs in QML except the exact click handler for the fixed author URL, changes to the executable allowlist without updating the test, and public-address pairing/connection requests before any command runs. These are review tripwires, not a proof against deliberately malicious future changes.

## Earlier marketplace review findings

The audit used the actual [installer review](https://github.com/omacom/omarchy-plugin-marketplace/issues/6136#issuecomment-5624957322) and [process-boundary follow-up](https://github.com/omacom/omarchy-plugin-marketplace/issues/6136#issuecomment-5630746029) on System QuikView. Its later approval applies to System QuikView's exact reviewed snapshot, not OmaDroid.

| Previous finding | OmaDroid control |
| --- | --- |
| Installer writes could follow symlinks or unsafe path components | `safe_files.py` opens directories by descriptor with `O_NOFOLLOW`, checks owners/types/modes, rejects hard-linked or writable files, bounds reads/backups, and replaces files atomically relative to validated directories. Source files receive the same checks. |
| PATH lookup differed from executed file | `safe_process.py` has a fixed `/usr/bin` allowlist, verifies root-owned path components and executable format, then executes the opened inode through `/proc/self/fd`. The distro-owned Python version alias is validated; other executable symlinks are refused. |
| Unbounded helper stdout/stderr | Short commands have separate 512 KiB stdout / 128 KiB stderr limits and deadlines. Both streams are drained without waiting on unbounded `communicate()` output. |
| Descendants could survive a helper timeout | Helper process groups are killed before the leader is reaped, including on success; repeated cleanup cannot signal a reused PID. |
| Installer automatically ran ambient shell commands | `install.py` launches no subprocesses and performs no downloads. It prints a manual shell-restart instruction. |

Runtime state, identity metadata, session metadata, locks, and logs also use descriptor-relative nofollow operations. Metadata reads/writes are limited to 64 KiB. Per-session diagnostic output is capped at 1 MiB; exceeding it stops that mirror. Pairing input, startup messages, and helper output are bounded. A mirror's lifetime is deliberately controlled by the user; it has no arbitrary short helper timeout. Its supervisor stops the scrcpy process group on errors or cancellation.

Opening a terminal or KDE Connect is an explicit user action and creates a separate desktop session/service, which may remain open. The terminal reestablishes the controlled environment before launching the dependency installer. Package names and service names are fixed; phone text never becomes a local shell command. The fixed Android identity query uses a shell on the phone with no interpolated input.

## Phone data and authorization

- Pairing codes go only through standard input, are cleared from the panel, and are neither saved nor put in process arguments or logs.
- The plugin saves connection addresses, reported hardware identities, custom names, and reconnect preferences locally under `~/.local/state/phone-mirror`. Diagnostics and session locks are under `~/.cache/phone-mirror`. XDG overrides are supported and path-validated. New directories/files are private to the desktop user.
- OmaDroid does not record video or audio to disk. `--no-clipboard-autosync` disables automatic clipboard sharing. Left Alt+V can explicitly paste the computer clipboard into the phone.
- Android retains responsibility for authorization and screen-lock authentication. Wake does not bypass the PIN or other lock credentials.
- Reported hardware identities group transports; they are not cryptographic authorization. Android/ADB's pairing mechanism grants access. Unknown phones are never automatically paired, and mirrors start only on an explicit Mirror action.
- Disconnect pauses reconnection; it does not revoke pairing. Forget the computer in the phone's Wireless debugging settings to revoke that authorization. KDE Connect pairing is separate.

## Validation and limits

The audit includes hostile filesystem fixtures, PATH/environment poisoning, executable replacement after verification, output flooding, timeout/descendant cleanup, session log overflow, pairing-code handling, public endpoint rejection, and functional session tests. See [VALIDATION.md](VALIDATION.md) for the final count and live checks.

A syscall tracer followed the backend and its child helpers during a real status refresh and a real mirror session. Observed `connect`/`sendto` destinations were Unix sockets and `127.0.0.1:5037`; no direct Internet destination appeared. The real phone display opened, and Stop closed it. The original disconnected state and reconnect preferences were restored. The dependency terminal was tested with a harmless environment probe, not a package installation.

That trace does not include traffic from the already-running ADB, Avahi, KDE Connect, package-manager services, or phone apps. Source review supplies the command/network inventory above; the trace is a sampled runtime check, not a universal egress proof. The upstream [scrcpy project](https://github.com/Genymobile/scrcpy) documents USB/TCP mirroring without an account or Internet requirement.

This is a code audit with regression tests, not an independent security certification or a guarantee covering future dependency versions. The operating system, distro packages, local desktop services, and user-owned plugin source are trusted. Same-user code can modify this plugin; root can replace system binaries or network routing. OmaDroid does not sandbox the whole desktop or prevent unrelated phone applications from accessing the Internet.

Before a release, rerun the tests, QML lint, and manifest validation, inspect dependency/permission changes against this document, and synchronize the manifest, archive/tag, release, and marketplace version. Never describe a new snapshot as marketplace-approved until its actual review is complete.
