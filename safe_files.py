"""Descriptor-relative, bounded filesystem operations for OmaDroid (Linux)."""
import os
from pathlib import Path
import stat
import secrets
from contextlib import contextmanager

# Linux-only installer, like the telemetry collector. Every destination is
# addressed relative to an opened directory, never through a joined write path.
MAX_CONFIG = 1024 * 1024
MAX_FILE = 32 * 1024 * 1024
MAX_BACKUP = 128 * 1024 * 1024
MAX_ENTRIES = 4096


class UnsafePath(ValueError):
    pass


def validate(info, directory=False, ancestor=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    owners = (0, os.getuid()) if ancestor else (os.getuid(),)
    if not expected(info.st_mode) or info.st_uid not in owners:
        raise UnsafePath('Refusing a path with an unexpected type or owner')
    if directory and info.st_mode & 0o022:
        raise UnsafePath('Refusing a group/world-writable directory')
    if not directory and (info.st_nlink != 1 or info.st_mode & 0o022):
        raise UnsafePath('Refusing a hard-linked or group/world-writable file')


def component(name):
    if not name or name in ('.', '..') or '/' in name:
        raise UnsafePath('Invalid path component')


def inspect(parent, name, directory=False):
    component(name)
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    validate(info, directory)
    return info


@contextmanager
def directory(parent, name, create=False):
    component(name)
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        validate(os.fstat(fd), directory=True)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def home_directory(path, create=False):
    # Do not resolve(): resolving would silently accept symlinked components.
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise UnsafePath('Home must be an absolute path without traversal')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            # Ancestors may be root-owned. A sticky /tmp is permitted only as
            # an ancestor (for isolated installs/tests), never as the home.
            info = os.fstat(fd)
            if info.st_uid == 0 and info.st_mode & stat.S_ISVTX:
                if not stat.S_ISDIR(info.st_mode):
                    raise UnsafePath('Invalid ancestor')
            else:
                validate(info, directory=True, ancestor=True)
        validate(os.fstat(fd), directory=True)
        yield fd
    finally:
        os.close(fd)


def signature(info):
    if info is None:
        return None
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_file(parent, name, limit=MAX_FILE):
    info = inspect(parent, name)
    if info is None:
        return None, None
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    try:
        current = os.fstat(fd)
        validate(current)
        if signature(current) != signature(info):
            raise UnsafePath('File changed while opening: ' + name)
        if current.st_size > limit:
            raise UnsafePath('File exceeds size limit: ' + name)
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b''.join(chunks)
        if len(data) > limit or signature(os.fstat(fd)) != signature(info):
            raise UnsafePath('File grew or changed while reading: ' + name)
        return data, info
    finally:
        os.close(fd)


def atomic_write(parent, name, data, expected=None, mode=0o600):
    if signature(inspect(parent, name)) != signature(expected):
        raise UnsafePath('Destination changed before writing: ' + name)
    temporary = '.omadroid-' + secrets.token_hex(16)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            validate(os.fstat(output.fileno()))
            output.write(data)
            output.flush()
            os.fchmod(output.fileno(), mode & 0o777 & ~0o022)
            os.fsync(output.fileno())
        if signature(inspect(parent, name)) != signature(expected):
            raise UnsafePath('Destination changed before replacement: ' + name)
        # rename replaces the directory entry itself; it cannot follow a
        # symlink substituted after the validation above.
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def snapshot_tree(parent, budget=None, depth=0):
    if budget is None:
        budget = [MAX_BACKUP, MAX_ENTRIES]
    if depth > 32:
        raise UnsafePath('Backup nesting limit exceeded')
    result = {}
    for name in os.listdir(parent):
        budget[1] -= 1
        if budget[1] < 0:
            raise UnsafePath('Too many backup entries')
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            with directory(parent, name) as child:
                result[name] = snapshot_tree(child, budget, depth + 1)
        else:
            data, info = read_file(parent, name, min(MAX_FILE, budget[0]))
            if info is None:
                raise UnsafePath('Backup entry disappeared')
            budget[0] -= len(data)
            result[name] = (data, stat.S_IMODE(info.st_mode))
    return result


def write_tree(parent, tree):
    for name, value in tree.items():
        if isinstance(value, dict):
            # Backup destinations must be new; never merge into a preexisting tree.
            os.mkdir(name, 0o700, dir_fd=parent)
            with directory(parent, name) as child:
                write_tree(child, value)
        else:
            atomic_write(parent, name, value[0], mode=value[1])



@contextmanager
def private_file(parent, name, truncate=False):
    component(name)
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 0o600, dir_fd=parent)
    try:
        validate(os.fstat(fd))
        if truncate:
            os.ftruncate(fd, 0)
        yield fd
    finally:
        os.close(fd)
