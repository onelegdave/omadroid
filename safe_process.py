"""Fixed distro executables, explicit environment, bounded pipes, group cleanup."""
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import pwd
import re
import selectors
import signal
import stat
import subprocess
import time

TOOLS = {name: '/usr/bin/' + name for name in (
    'adb', 'scrcpy', 'avahi-browse', 'busctl', 'kdeconnect-cli', 'kdeconnect-app',
    'notify-send', 'pacman', 'systemctl', 'systemd-run', 'bash', 'python3', 'omarchy-launch-terminal')}
STDOUT_LIMIT = 512 * 1024
STDERR_LIMIT = 128 * 1024


class ToolError(ValueError):
    pass


def tool_environment():
    home = pwd.getpwuid(os.getuid()).pw_dir
    runtime = '/run/user/' + str(os.getuid())
    env = {'PATH': '/usr/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'HOME': home,
           'XDG_RUNTIME_DIR': runtime, 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + runtime + '/bus',
           'ADB_SERVER_SOCKET': 'tcp:127.0.0.1:5037', 'ADB': '/usr/bin/adb',
           'SCRCPY_SERVER_PATH': '/usr/share/scrcpy/scrcpy-server'}
    # XDG file locations are validated separately by the nofollow storage layer.
    for name in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'):
        value = os.environ.get(name)
        if value and value.startswith('/') and '..' not in Path(value).parts:
            env[name] = value
    wayland = os.environ.get('WAYLAND_DISPLAY', '')
    if re.fullmatch(r'[A-Za-z0-9_.-]+', wayland):
        env['WAYLAND_DISPLAY'] = wayland
        env['XDG_SESSION_TYPE'] = 'wayland'
        env['SDL_VIDEODRIVER'] = 'wayland'
        env['QT_QPA_PLATFORM'] = 'wayland'
    display = os.environ.get('DISPLAY', '')
    if re.fullmatch(r':\d+(?:\.\d+)?', display):
        env['DISPLAY'] = display
    # No PATH, LD_*, PYTHON*, ADB routing, SCRCPY*, proxy, or shell startup overrides.
    return env


def trusted_node(info, directory=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != 0 or info.st_mode & (0o022 | stat.S_ISUID | stat.S_ISGID):
        raise ToolError('Tool must be a root-owned, non-writable system file')


@contextmanager
def verified_tool(name, script=False):
    path = TOOLS.get(name)
    if path is None:
        raise ToolError('Unknown system tool')
    # Python's distro-managed symlink is the sole allowed executable alias.
    if name == 'python3':
        target = os.readlink(path)
        if not re.fullmatch(r'python3\.\d+', target):
            raise ToolError('Unexpected Python interpreter alias')
        info = os.lstat(path)
        if info.st_uid != 0:
            raise ToolError('Untrusted Python interpreter alias')
        path = '/usr/bin/' + target
    parts = Path(path).parts
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    executable = None
    try:
        trusted_node(os.fstat(parent), directory=True)
        for part in parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            os.close(parent)
            parent = child
            trusted_node(os.fstat(parent), directory=True)
        executable = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        info = os.fstat(executable)
        trusted_node(info)
        magic = os.pread(executable, 16, 0)
        allowed_script = script and name == 'omarchy-launch-terminal' and magic.startswith(b'#!/bin/bash\n')
        if not info.st_mode & 0o111 or (magic[:4] != b'\x7fELF' and not allowed_script):
            raise ToolError('Unexpected executable format')
        yield path, executable
    finally:
        if executable is not None:
            os.close(executable)
        os.close(parent)


def available(name):
    try:
        with verified_tool(name, script=name == 'omarchy-launch-terminal'):
            return True
    except (OSError, ToolError):
        return False


def cleanup(process):
    if getattr(process, "_omadroid_cleaned", False):
        return
    process._omadroid_cleaned = True
    # Observe exit without reaping until the whole group is stopped (no PID reuse).
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream:
            stream.close()
    try:
        process.wait(timeout=.5)
    except subprocess.TimeoutExpired:
        pass


def exited(process):
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


@contextmanager
def launch(name, args, input=False, script=False):
    with ExitStack() as stack:
        path, fd = stack.enter_context(verified_tool(name, script=script))
        fds = [fd]
        argv = [path, *args]
        if script:
            bash, bash_fd = stack.enter_context(verified_tool('bash'))
            fds.append(bash_fd)
            argv = [bash, '--noprofile', '--norc', '/proc/self/fd/' + str(fd), *args]
            fd = bash_fd
        process = subprocess.Popen(argv, executable='/proc/self/fd/' + str(fd), pass_fds=tuple(fds),
                                   stdin=subprocess.PIPE if input else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=tool_environment(), cwd='/', start_new_session=True, close_fds=True)
        try:
            yield process
        finally:
            cleanup(process)


def collect(process, args, timeout, input=None, stdout_limit=STDOUT_LIMIT, stderr_limit=STDERR_LIMIT):
    data = (input or '').encode()
    if len(data) > 16384:
        raise ToolError('Command input is too large')
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    limits = {'stdout': stdout_limit, 'stderr': stderr_limit}
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        for stream, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if process.stdin:
            os.set_blocking(process.stdin.fileno(), False)
            if data:
                selector.register(process.stdin, selectors.EVENT_WRITE, 'stdin')
            else:
                process.stdin.close()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolError('Command exceeded its deadline')
            if not selector.get_map() and exited(process):
                break
            for key, _ in selector.select(min(remaining, .05)):
                name = key.data
                if name == 'stdin':
                    try:
                        written = os.write(key.fileobj.fileno(), data)
                        data = data[written:]
                    except BrokenPipeError:
                        data = b''
                    if not data:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                room = limits[name] - len(buffers[name])
                chunk = os.read(key.fileobj.fileno(), min(8192, room + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                elif len(chunk) > room:
                    raise ToolError('Command exceeded its ' + name + ' limit')
                else:
                    buffers[name].extend(chunk)
    # cleanup must kill descendants before wait() reaps the leader.
    cleanup(process)
    return subprocess.CompletedProcess(args, process.returncode,
                                       bytes(buffers['stdout']).decode(errors='replace'),
                                       bytes(buffers['stderr']).decode(errors='replace'))


def run(args, timeout=8, input=None):
    with launch(args[0], args[1:], input=input is not None) as process:
        return collect(process, args, timeout, input)
