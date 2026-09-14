#!/usr/bin/env python3
"""Phone Mirror backend. JSON over stdin/stdout; no shell interpolation."""
import concurrent.futures
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import selectors
from contextlib import contextmanager
import safe_files as files
import safe_process as commands
import time


class UserError(Exception):
    pass


def run(args, timeout=8, input=None):
    try:
        return commands.run(args, timeout, input)
    except (OSError, commands.ToolError) as error:
        raise UserError(f"{args[0]}: {error}") from None


def checked(args, timeout=8, input=None):
    result = run(args, timeout, input)
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode:
        raise UserError(output[-1200:] or f"{args[0]} failed.")
    return result.stdout.strip()


def endpoint(value):
    """Only literal IP:port endpoints, never options, commands or implicit port 5555."""
    value = str(value).strip()
    match = re.fullmatch(r"(?:\[([^\]]+)\]|([^:\s]+)):(\d{1,5})", value)
    if not match:
        raise UserError("Enter the IP address and port shown on your phone, for example 192.168.1.20:37123.")
    try:
        address = ipaddress.ip_address(match[1] or match[2])
    except ValueError:
        raise UserError("Use the numeric IP address shown on your phone.") from None
    if address in ipaddress.ip_network('100.64.0.0/10'):
        raise UserError("This shared-range address may belong to Tailscale or another VPN. "
                        "Use the phone's Wi-Fi address or Use detected address. If Android shows only "
                        "the VPN address, temporarily pause the phone VPN and reopen the pairing dialog "
                        "for its current Wi-Fi address, pairing port, and code.")
    allowed = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16', 'fc00::/7', 'fe80::/10')
    if not any(address in ipaddress.ip_network(network) for network in allowed):
        raise UserError('Use a private local-network phone address. Public Internet, loopback, and multicast addresses are not supported.')
    scope = getattr(address, 'scope_id', None)
    if scope and not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', scope):
        raise UserError('Invalid IPv6 network interface name.')
    port = int(match[3])
    if not 1 <= port <= 65535:
        raise UserError("The port must be between 1 and 65535.")
    return f"[{address}]:{port}" if address.version == 6 else f"{address}:{port}"


def parse_devices(output):
    devices = []
    for line in output.splitlines():
        if not line.strip() or line.startswith(("List of devices", "*")):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        serial, state = fields[:2]
        if state not in ("device", "offline", "unauthorized", "no"):
            continue
        props = dict(part.split(":", 1) for part in fields[2:] if ":" in part)
        devices.append({"serial": serial, "state": "no permissions" if state == "no" else state,
                        "name": props.get("model", serial).replace("_", " "),
                        "wireless": ":" in serial or "._adb-tls-connect." in serial})
    return devices[:32]


def parse_services(output):
    services = []
    seen = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or "_adb-tls-" not in fields[1]:
            continue
        kind = "pair" if "_adb-tls-pairing." in fields[1] else "connect"
        try:
            address = endpoint(fields[2])
        except UserError:
            continue
        if (kind, address) not in seen:
            seen.add((kind, address))
            services.append({"name": fields[0], "kind": kind, "address": address})
    return services


def parse_avahi(output, kind):
    services = []
    seen = set()
    for line in output.splitlines():
        fields = line.split(";")
        if len(fields) < 9 or fields[0] != "=":
            continue
        host = fields[7]
        try:
            address = endpoint(f"[{host}]:{fields[8]}" if ":" in host else f"{host}:{fields[8]}")
        except UserError:
            continue
        if address not in seen:
            seen.add(address)
            metadata = dict(re.findall(r'"([a-z_]+)=([^"\n]*)"', ";".join(fields[9:])))
            services.append({"name": fields[3], "kind": kind, "address": address,
                             "identity": metadata.get("serial", fields[3]),
                             "label": metadata.get("given_name") or metadata.get("name") or fields[3]})
    return services


def discover():
    try:
        return parse_services(checked(["adb", "mdns", "services"]))
    except UserError:
        # Some distributions compile ADB without mDNS; Avahi provides the same
        # discovery without enabling or changing any ADB network listener.
        if not commands.available("avahi-browse"):
            raise UserError("Automatic discovery is unavailable. Enter the address shown on your phone.") from None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {kind: pool.submit(checked, ["avahi-browse", "--parsable", "--resolve", "--terminate", service], 5)
                    for kind, service in (("pair", "_adb-tls-pairing._tcp"), ("connect", "_adb-tls-connect._tcp"))}
            return [item for kind, job in jobs.items() for item in parse_avahi(job.result(), kind)]


def bus(path, interface, method, *args):
    output = checked(["busctl", "--user", "--timeout=2", "--json=short", "call",
                      "org.kde.kdeconnect", path, interface, method, *args], timeout=3)
    return json.loads(output)["data"][0]


def kde_devices():
    if not commands.available("kdeconnect-cli"):
        return {"devices": [], "error": "Install KDE Connect for battery status, file sharing and Ring."}
    try:
        ids = bus("/modules/kdeconnect", "org.kde.kdeconnect.daemon", "devices", "bb", "false", "true")
        devices = []
        for device_id in ids[:32]:
            if not re.fullmatch(r"[A-Za-z0-9_]+", device_id):
                continue
            path = "/modules/kdeconnect/devices/" + device_id
            props = bus(path, "org.freedesktop.DBus.Properties", "GetAll", "s", "org.kde.kdeconnect.device")
            props = {key: value["data"] for key, value in props.items()}
            if props.get("type") not in ("phone", "tablet"):
                continue
            battery = None
            charging = False
            if props.get("isReachable"):
                try:
                    values = bus(path + "/battery", "org.freedesktop.DBus.Properties", "GetAll", "s", "org.kde.kdeconnect.device.battery")
                    battery = values.get("charge", {}).get("data")
                    charging = values.get("isCharging", {}).get("data", False)
                    if battery is not None and not 0 <= battery <= 100:
                        battery = None
                except (UserError, KeyError, ValueError):
                    pass
            devices.append({"id": device_id, "name": props.get("name", "Android phone"),
                            "reachable": props.get("isReachable", False), "battery": battery, "charging": charging})
        return {"devices": devices, "error": ""}
    except (UserError, ValueError, KeyError, TypeError) as error:
        return {"devices": [], "error": "KDE Connect is unavailable. Open KDE Connect to pair your phone.", "detail": str(error)}


@contextmanager
def storage(kind):
    key, default = ('XDG_CACHE_HOME', '.cache') if kind == 'cache' else ('XDG_STATE_HOME', '.local/state')
    base = Path(os.environ.get(key) or str(Path.home() / default))
    with files.home_directory(base, create=True) as parent:
        with files.directory(parent, 'phone-mirror', create=True) as directory:
            yield directory


def cache_dir():
    with storage('cache'):
        return Path(os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')) / 'phone-mirror'


def saved_path():
    with storage('state'):
        return Path(os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local/state')) / 'phone-mirror/connections.json'


def read_json(kind, name, fallback):
    with storage(kind) as directory:
        raw, _ = files.read_file(directory, name, 65536)
    try:
        return json.loads(raw) if raw is not None else fallback
    except ValueError:
        return fallback


def write_json(kind, name, data):
    raw = json.dumps(data).encode()
    if len(raw) > 65536:
        raise UserError('Saved phone metadata exceeds its size limit.')
    with storage(kind) as directory:
        files.atomic_write(directory, name, raw, expected=files.inspect(directory, name))


def remove_cache(name):
    with storage('cache') as directory:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass


def session_key(serial):
    return hashlib.sha256(serial.encode()).hexdigest()[:24]


def active(serial):
    with storage('cache') as directory, files.private_file(directory, session_key(serial) + '.lock') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def saved():
    data = read_json('state', 'connections.json', [])
    result = []
    if isinstance(data, list):
        for value in data[:12]:
            try:
                result.append(endpoint(value))
            except UserError:
                pass
    return result


def remember(address):
    write_json('state', 'connections.json', ([address] + [item for item in saved() if item != address])[:12])


def connection_state():
    state = read_json('state', 'devices.json', {})
    if not isinstance(state, dict):
        return {'phones': []}
    phones = state.get('phones', [])
    state['phones'] = [p for p in phones[:48] if isinstance(p, dict) and all(isinstance(p.get(k), str) for k in ('identity', 'name', 'address'))] if isinstance(phones, list) else []
    if not isinstance(state.get('pending', {}), dict):
        state.pop('pending', None)
    return state


def write_connection_state(state):
    write_json('state', 'devices.json', state)


def host(address):
    return endpoint(address).rsplit(":", 1)[0]


def connection_candidates(services, devices, state):
    connected = {d["serial"] for d in devices if d["state"] == "device"}
    pending = state.get("pending", {})
    phones = state.get("phones", [])
    candidates = []
    for service in services:
        if service["kind"] != "connect" or service["address"] in connected:
            continue
        if any(service["name"] in serial for serial in connected):
            continue
        identity = service.get("identity", service["name"])
        if any(d.get('hardwareId') == identity for d in devices if d['state'] == 'device'):
            continue
        known = next((p for p in phones if p["identity"] == identity), None)
        if known and known.get("paused"):
            continue
        just_paired = pending.get("expires", 0) > time.time() and pending.get("host") == host(service["address"])
        if known or just_paired:
            candidates.append(service)
    return candidates


def identify_devices(devices, services):
    """Read identity from authorized devices; never merge by model or nickname."""
    def identify(device):
        device = dict(device)
        service = next((s for s in services if s['kind'] == 'connect' and
                        (s['address'] == device['serial'] or s['name'] in device['serial'])), {})
        if service.get('identity') and service['identity'] != service['name']:
            device['hardwareId'] = service['identity']
        if service.get('label') and service['label'] != service['name']:
            device['customName'] = service['label']
        if device['wireless'] and ':' in device['serial']:
            try:
                endpoint(device['serial'])
            except UserError:
                device['state'] = 'unsupported address'
                return device
        if device['state'] == 'device':
            try:
                output = checked(['adb', '-s', device['serial'], 'shell',
                                  'printf "serial="; getprop ro.serialno; printf "boot="; getprop ro.boot.serialno; printf "name="; settings get global device_name'], timeout=3)
                values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
                identity = next((v.strip() for v in (values.get('serial', ''), values.get('boot', ''))
                                 if re.fullmatch(r'[A-Za-z0-9._-]{4,128}', v.strip())
                                 and v.strip().lower() not in ('unknown', 'null', 'none', '0123456789abcdef')
                                 and v.strip().strip('0')), '')
                if identity:
                    device['hardwareId'] = identity
                name = values.get('name', '').strip()
                if name.lower() not in ('', 'null', 'unknown'):
                    device['customName'] = name[:128]
            except UserError:
                pass  # Keep an unresolved transport separate when a phone refuses identity queries.
        return device
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(identify, devices))


def group_devices(devices):
    groups = {}
    for device in devices:
        key = ('phone', device['hardwareId']) if device.get('hardwareId') else ('transport', device['serial'])
        groups.setdefault(key, []).append(device)
    result = []
    for members in groups.values():
        members.sort(key=lambda d: (not d.get('mirroring', False), d['state'] != 'device', d['wireless'], d['serial']))
        selected = members[0]
        name = next((d['customName'] for d in members if d.get('customName')), selected['name'])
        modes = [label for wireless, label in ((False, 'USB'), (True, 'Wi-Fi')) if any(d['wireless'] == wireless for d in members)]
        result.append({**selected, 'name': name, 'transports': members,
                       'wireless': any(d['wireless'] for d in members),
                       'mirroring': any(d.get('mirroring', False) for d in members),
                       'connectionSummary': ' + '.join(modes) + (f' · {len(members)} connections' if len(members) > 1 else '')})
    return result


def identity_cache():
    data = read_json('state', 'identities.json', {})
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in list(data.items())[:48] if isinstance(value, dict)
            and isinstance(value.get('hardwareId'), str) and isinstance(value.get('name'), str)}


def learn_identities(devices):
    cache = identity_cache()
    previous = dict(cache)
    for device in devices:
        if device.get('hardwareId'):
            cache[device['serial']] = {'hardwareId': device['hardwareId'], 'name': device.get('customName', device['name'])}
    cache = dict(list(cache.items())[-48:])
    if cache != previous:
        write_json('state', 'identities.json', cache)
    return cache


def known_phones(state, cache):
    return [{**profile, **cache.get(profile['address'], {})} for profile in state.get('phones', [])]


def connect_phone(address, services=None):
    hint = (" Check that Wireless debugging is still on, refresh discovery, and choose Connect again. "
            "A VPN or network change can briefly interrupt the connection or change its port. "
            "Use the phone's Wi-Fi connection address, not the pairing port; a disconnect alone does not require pairing again.")
    try:
        output = checked(["adb", "connect", address], timeout=15)
    except UserError as error:
        raise UserError(str(error) + hint) from None
    if not re.search(r"(?:already )?connected to ", output, re.I):
        raise UserError((output or "Connection failed.") + hint)
    devices = parse_devices(checked(["adb", "devices", "-l"]))
    device = next((d for d in devices if d["serial"] == address and d["state"] == "device"), None)
    if not device:
        raise UserError("The phone accepted the connection but is not ready yet. Keep Wireless debugging enabled and try Connect again.")
    remember(address)
    if services is None:
        try:
            services = discover()
        except UserError:
            services = []
    service = next((s for s in services if s["kind"] == "connect" and s["address"] == address), {})
    identity = service.get("identity", service.get("name", address))
    metadata = identity_cache().get(address, {})
    hardware_id = metadata.get('hardwareId') or (service.get('identity') if service.get('identity') != service.get('name') else None)
    label = service.get("label") or metadata.get('name') or device["name"]
    state = connection_state()
    profiles = known_phones(state, identity_cache())
    profile = {"identity": identity, "name": label, "address": address}
    if hardware_id:
        profile['hardwareId'] = hardware_id
    state["phones"] = [profile] + [p for p in profiles if p["identity"] != identity and p["address"] != address
                                  and not (hardware_id and p.get('hardwareId') == hardware_id)]
    state["phones"] = state["phones"][:12]
    if state.get("pending", {}).get("host") == host(address):
        state.pop("pending", None)
    write_connection_state(state)
    return {"ok": True, "connected": True, "serial": address, "message": "Ready to mirror. Click Mirror beside your phone."}


def finish_pairing(address):
    state = connection_state()
    state["pending"] = {"host": host(address), "expires": time.time() + 600}
    write_connection_state(state)
    try:
        services = discover()
        candidates = connection_candidates(services, [], state)
        for service in candidates:
            if host(service["address"]) == host(address):
                try:
                    return {**connect_phone(service["address"], services), "paired": True}
                except UserError:
                    continue
    except UserError:
        pass
    return {"ok": True, "paired": True, "message": "Pairing complete. Return to the main Wireless debugging screen on your phone; the plugin will connect when it appears. You can also enter that screen’s connection address below."}


def adb_can_start():
    """Presence of /usr/bin/adb is not readiness. A protobuf mismatch leaves
    the ELF on disk and unusable; the panel would then say the desktop is
    ready while every adb call fails."""
    if not commands.available("adb"):
        return False, None
    try:
        result = run(["adb", "version"], timeout=5)
    except UserError as error:
        return False, str(error)
    if result.returncode == 0:
        return True, None
    text = (result.stderr or result.stdout or "adb failed.").strip()[-1200:]
    if "shared libraries" in text or "libprotobuf" in text:
        return False, (
            "adb is installed but cannot start because a system library is missing or too old. "
            "Upgrade protobuf so it matches android-tools, then reopen this panel.\n" + text
        )
    return False, text


def status():
    dependencies = {key: bool(commands.available(value)) for key, value in
                    {"adb": "adb", "scrcpy": "scrcpy", "kdeconnect": "kdeconnect-cli", "avahi": "avahi-browse", "installer": "omarchy-launch-terminal"}.items()}
    dependencies['usbRules'] = commands.available('pacman') and run(['pacman', '-Qq', 'android-udev']).returncode == 0
    dependencies['discoveryReady'] = dependencies['avahi'] and commands.available('systemctl') and run(['systemctl', 'is-active', '--quiet', 'avahi-daemon.service']).returncode == 0
    adb_ok, adb_error = adb_can_start()
    dependencies["adb"] = adb_ok
    result = {"dependencies": dependencies, "devices": [], "services": [], "saved": saved(), "errors": []}
    if adb_error:
        result["errors"].append(adb_error)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        kde = pool.submit(kde_devices)
        adb = pool.submit(checked, ["adb", "devices", "-l"]) if dependencies["adb"] else None
        mdns = pool.submit(discover) if dependencies["adb"] else None
        if adb:
            try:
                result["devices"] = parse_devices(adb.result())
                for device in result["devices"]:
                    device["mirroring"] = active(device["serial"])
            except UserError as error:
                result["errors"].append(str(error))
        if mdns:
            try:
                result["services"] = mdns.result()
            except UserError as error:
                result["errors"].append("Wireless discovery unavailable; you can enter the address manually. " + str(error))
        result["kde"] = kde.result()
    result['devices'] = identify_devices(result['devices'], result['services'])
    cache = learn_identities(result['devices'])
    state = connection_state()
    state['phones'] = known_phones(state, cache)
    result["reconnect"] = connection_candidates(result["services"], result["devices"], state)
    result["remembered"] = []
    for phone in state.get("phones", []):
        service = next((s for s in result["services"] if s["kind"] == "connect" and s.get("identity", s["name"]) == phone["identity"]), None)
        current_address = service["address"] if service else phone["address"]
        device = next((d for d in result["devices"] if d["serial"] == current_address or (service and service["name"] in d["serial"]) or (phone.get("hardwareId") and d.get("hardwareId") == phone["hardwareId"])), None)
        if device:
            device["name"] = phone["name"]
        else:
            result["remembered"].append({**phone, "address": current_address, "nearby": service is not None})
    result['devices'] = group_devices(result['devices'])
    remembered = {}
    for profile in result['remembered']:
        key = profile.get('hardwareId', profile['identity'])
        if key not in remembered or (profile['nearby'] and not remembered[key]['nearby']):
            remembered[key] = profile
    result['remembered'] = list(remembered.values())
    return result


def scrcpy_command(serial, options):
    quality = {"Balanced": (1600, "8M", 60), "Sharp": (2560, "16M", 60), "Low bandwidth": (1024, "3M", 30)}
    size, rate, fps = quality.get(options.get("quality"), quality["Balanced"])
    args = ["scrcpy", "--serial=" + serial, "--window-title=OmaDroid", "--max-size=" + str(size),
            "--video-bit-rate=" + rate, "--max-fps=" + str(fps), "--no-clipboard-autosync", "--verbosity=warn"]
    if options.get("audio") is False:
        args.append("--no-audio")
    if options.get("screenOff") is True:
        args.append("--turn-screen-off")
    if options.get("keepAwake") is True:
        args.append("--keep-active")
    return args


def notify_error(message):
    if commands.available("notify-send"):
        run(["notify-send", "--app-name=OmaDroid", "OmaDroid", message[:500]])


def process_identity(pid):
    stat = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
    return stat[19]


def session_process(serial):
    """Find only a scrcpy child of this plugin, including pre-metadata sessions."""
    try:
        record = read_json("cache", session_key(serial) + ".session.json", {})
        candidates = [int(record["pid"])]
    except (OSError, ValueError, KeyError):
        record = None
        candidates = [int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit()]
    for pid in candidates:
        try:
            process = Path('/proc') / str(pid)
            if process.stat().st_uid != os.getuid():
                continue
            args = process.joinpath('cmdline').read_bytes().split(b'\0')
            if ('--serial=' + serial).encode() not in args or not any(Path(os.fsdecode(arg)).name == 'scrcpy' for arg in args if arg):
                continue
            ppid = int(process.joinpath('stat').read_text().rpartition(')')[2].split()[1])
            parent_args = Path(f'/proc/{ppid}/cmdline').read_bytes().split(b'\0')
            if b'session' not in parent_args or not any(Path(os.fsdecode(arg)).name == 'phone_mirror.py' for arg in parent_args if arg):
                continue
            identity = process_identity(pid)
            if record and identity != str(record.get('start')):
                continue
            return pid, identity
        except (OSError, ValueError, IndexError):
            continue
    return None


def stop_session(serial):
    process = session_process(serial)
    if process is None:
        if active(serial):
            raise UserError("Close this phone’s mirror window to stop the older session.")
        return
    pid, identity = process
    try:
        descriptor = os.pidfd_open(pid)
        try:
            if process_identity(pid) != identity:
                raise UserError("The mirror process changed. Refresh and try again.")
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        finally:
            os.close(descriptor)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 4
    while active(serial) and time.monotonic() < deadline:
        time.sleep(0.05)
    if active(serial):
        raise UserError("The mirror is still closing. Try again in a moment.")


def session(request):
    """Explicit long-lived session; bounded logs, verified scrcpy, full cleanup."""
    serial = str(request['serial'])
    if ':' in serial:
        endpoint(serial)
    key = session_key(serial)
    announced = False
    tail = bytearray()
    def announce(ok, message):
        nonlocal announced
        if not announced:
            print(json.dumps({'ok': ok, 'message': message}), flush=True)
            announced = True
            sys.stdout.close()
    def cancelled(signum, frame):
        raise UserError('Mirror session cancelled.')
    signal.signal(signal.SIGTERM, cancelled)
    with storage('cache') as directory, files.private_file(directory, key + '.lock') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            announce(True, 'This phone is already mirroring.')
            return
        try:
            with files.private_file(directory, key + '.log', truncate=True) as log:
                with commands.launch('scrcpy', scrcpy_command(serial, request)[1:]) as child:
                    write_json('cache', key + '.session.json', {'pid': child.pid, 'start': process_identity(child.pid)})
                    started = time.monotonic()
                    total = 0
                    with selectors.DefaultSelector() as selector:
                        for stream in (child.stdout, child.stderr):
                            os.set_blocking(stream.fileno(), False)
                            selector.register(stream, selectors.EVENT_READ)
                        while True:
                            if not announced and time.monotonic() - started >= 1 and not commands.exited(child):
                                announce(True, 'Opening phone display…')
                            if not selector.get_map() and commands.exited(child):
                                break
                            for event, _ in selector.select(.05):
                                chunk = os.read(event.fileobj.fileno(), 8192)
                                if not chunk:
                                    selector.unregister(event.fileobj)
                                    continue
                                total += len(chunk)
                                if total > 1024 * 1024:
                                    raise UserError('Mirroring stopped because its diagnostic log exceeded 1 MiB.')
                                os.write(log, chunk)
                                tail.extend(chunk)
                                del tail[:-1500]
                    commands.cleanup(child)
                    if child.returncode and child.returncode != -signal.SIGTERM:
                        raise UserError(tail.decode(errors='replace') or 'Mirroring stopped. Check the phone connection.')
                    announce(True, 'Mirror closed.')
        except (OSError, ValueError, UserError) as error:
            if announced:
                notify_error(str(error))
            else:
                announce(False, str(error))
        finally:
            remove_cache(key + '.session.json')


def start_session(request):
    # The session intentionally outlives this action, unlike short helper calls.
    with commands.verified_tool('python3') as (python, fd):
        child = subprocess.Popen([python, '-E', '-s', str(Path(__file__).absolute()), 'session'],
                                 executable='/proc/self/fd/' + str(fd), pass_fds=(fd,),
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 env=commands.tool_environment(), cwd='/', start_new_session=True)
    try:
        payload = (json.dumps(request) + '\n').encode()
        if len(payload) > 8192:
            raise UserError('Mirror request is too large.')
        child.stdin.write(payload)
        child.stdin.close()
        response = bytearray()
        deadline = time.monotonic() + 6
        with selectors.DefaultSelector() as selector:
            os.set_blocking(child.stdout.fileno(), False)
            selector.register(child.stdout, selectors.EVENT_READ)
            while b'\n' not in response:
                if time.monotonic() >= deadline:
                    raise UserError('The mirror did not finish starting.')
                if not selector.select(.05):
                    continue
                chunk = os.read(child.stdout.fileno(), 4096 - len(response))
                if not chunk or len(response) + len(chunk) >= 4096:
                    raise UserError('Invalid mirror startup response.')
                response.extend(chunk)
        child.stdout.close()
        return json.loads(response.split(b'\n', 1)[0])
    except Exception:
        # Give the supervisor time to run its scrcpy cleanup handler.
        try:
            os.killpg(child.pid, signal.SIGTERM)
            until = time.monotonic() + .5
            while not commands.exited(child) and time.monotonic() < until:
                time.sleep(.01)
        finally:
            commands.cleanup(child)
        raise


def open_desktop_app(kind, group=None):
    if kind == 'install-tools':
        if group not in ('core', 'companion', 'discovery', 'usb'):
            raise UserError('Unknown dependency group.')
        source = Path(__file__).absolute().parent
        with files.home_directory(source) as directory:
            script, _ = files.read_file(directory, 'install-deps.sh', 16384)
            if script is None:
                raise UserError('The dependency installer is missing.')
        # A desktop launcher can start a new systemd session. Reapply the clean
        # environment inside that session before starting the fixed installer.
        env = commands.tool_environment()
        args = ['/usr/bin/env', '-i', *[key + '=' + value for key, value in env.items()],
                '/usr/bin/bash', '--noprofile', '--norc', str(source / 'install-deps.sh'), group]
        with commands.launch('omarchy-launch-terminal', args, script=True) as process:
            result = commands.collect(process, args, 8)
    else:
        # A user-requested GUI runs as its own transient desktop service.
        args = ['systemd-run', '--user', '--collect', '--quiet', '/usr/bin/env', '-i',
                *[key + '=' + value for key, value in commands.tool_environment().items()], '/usr/bin/kdeconnect-app']
        result = commands.run(args, 8)
    if result.returncode:
        raise UserError(result.stderr[-1000:] or 'The desktop application could not open.')
    return {'ok': True, 'message': 'Installer opened. It lists the packages and asks for your desktop password if needed.' if kind == 'install-tools' else 'KDE Connect opened.'}


def action(request):
    kind = request.get("action")
    if kind in ("install-tools", "open-companion"):
        return open_desktop_app(kind, request.get("group"))
    if kind in ('stop', 'disconnect') and request.get('scope') == 'phone':
        serial = str(request.get('serial', ''))
        devices = parse_devices(checked(['adb', 'devices', '-l']))
        # Destructive-to-session operations use freshly queried identity, not saved aliases.
        devices = identify_devices(devices, [])
        selected = next((group for group in group_devices(devices)
                         if any(d['serial'] == serial for d in group['transports'])), None)
        if not selected:
            raise UserError('The phone connection changed. Refresh and select it again.')
        targets = [d for d in selected['transports'] if kind == 'stop' or d['wireless']]
        if not targets:
            raise UserError('For USB, stop the mirror and unplug the cable.')
        for device in targets:
            action({'action': kind, 'serial': device['serial']})
        message = 'Mirrors closed. Your phone stays connected.' if kind == 'stop' else 'Wi-Fi disconnected. Automatic reconnect is paused until you choose Connect.'
        if kind == 'disconnect' and any(not d['wireless'] for d in selected['transports']):
            message += ' The USB connection is still available; unplug the cable to disconnect it.'
        return {'ok': True, 'message': message}
    if kind == "stop":
        stop_session(str(request.get('serial', '')))
        return {'ok': True, 'message': 'Mirror closed. Your phone stays connected for next time.'}
    if kind == "disconnect":
        serial = str(request.get('serial', ''))
        devices = parse_devices(checked(['adb', 'devices', '-l']))
        device = next((d for d in devices if d['serial'] == serial), None)
        if device is None or not device['wireless']:
            raise UserError('For USB, stop the mirror and unplug the cable. For Wi-Fi, refresh and select a connected phone.')
        stop_session(serial)
        state = connection_state()
        try:
            service = next((s for s in discover() if s['kind'] == 'connect' and (s['address'] == serial or s['name'] in serial)), {})
        except UserError:
            service = {}
        identity = service.get('identity', service.get('name', serial))
        matched = False
        for phone in state.get('phones', []):
            if phone['address'] == serial or phone['identity'] == identity:
                phone['paused'] = True
                matched = True
        if not matched:
            state.setdefault('phones', []).append({'identity': identity, 'name': device['name'], 'address': service.get('address', serial), 'paused': True})
        state.pop('pending', None)
        write_connection_state(state)
        checked(['adb', 'disconnect', serial])
        return {'ok': True, 'message': 'Disconnected. Automatic reconnect is paused for this connection until you choose Connect.'}
    if kind == "pair":
        address = endpoint(request.get("address", ""))
        code = str(request.get("code", "")).strip()
        if not re.fullmatch(r"\d{6}", code):
            raise UserError("Enter the six-digit pairing code shown on your phone.")
        # Pairing codes never enter argv, state files or logs.
        result = run(["adb", "pair", address], timeout=35, input=code + "\n")
        output = result.stdout + "\n" + result.stderr
        if result.returncode or "successfully paired" not in output.lower():
            raise UserError("Pairing failed. Keep the pairing-code screen open and use its current address and code.")
        return finish_pairing(address)
    if kind == "connect":
        address = endpoint(request.get("address", ""))
        return connect_phone(address)
    if kind in ("mirror", "wake"):
        serial = str(request.get("serial", ""))
        devices = parse_devices(checked(["adb", "devices", "-l"]))
        if not any(item["serial"] == serial and item["state"] == "device" for item in devices):
            raise UserError("This phone is not ready. Unlock it, authorize debugging, and refresh the list.")
        if ':' in serial:
            endpoint(serial)
        if kind == "wake":
            # WAKEUP is idempotent, unlike POWER, which could put an awake phone
            # back to sleep. Android itself still enforces secure keyguard auth.
            checked(["adb", "-s", serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"])
            time.sleep(0.25)
            result = run(["adb", "-s", serial, "shell", "wm", "dismiss-keyguard"])
            prompt = "Enter your PIN or use your normal unlock method if requested." if result.returncode == 0 else "Swipe up in the mirror and unlock normally."
            return {"ok": True, "message": "Wake requested. " + prompt}
        if not commands.available("scrcpy"):
            raise UserError("Install scrcpy to mirror your phone.")
        if request.get("keepAwake") is True and "--keep-active" not in checked(["scrcpy", "--help"]):
            raise UserError("This scrcpy version cannot keep a wireless phone awake. Upgrade scrcpy, or turn off ‘Keep phone awake while mirroring’ in Options.")
        return start_session(request)
    if kind == "ring":
        device_id = str(request.get("id", ""))
        devices = kde_devices()["devices"]
        if not any(item["id"] == device_id and item["reachable"] for item in devices):
            raise UserError("This phone is not reachable in KDE Connect.")
        checked(["kdeconnect-cli", "--device", device_id, "--ring"])
        return {"ok": True, "message": "Ringing your phone."}
    raise UserError("Unknown action.")


def main():
    os.umask(0o077)
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else "status"
        if mode == "status":
            result = status()
        elif mode in ("action", "session"):
            request = json.loads(sys.stdin.readline(16384))
            if not isinstance(request, dict):
                raise UserError("Expected an action object.")
            if mode == "session":
                session(request)
                return
            result = action(request)
        else:
            raise UserError("Unknown command.")
    except (UserError, OSError, ValueError, KeyError, TypeError) as error:
        result = {"ok": False, "message": str(error)}
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
