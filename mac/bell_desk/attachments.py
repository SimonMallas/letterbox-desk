"""Desk-owned, inert image copies. No path in an incoming letter is opened here."""
import base64
import hashlib
import fcntl
from contextlib import contextmanager
import json
import os
import stat
import subprocess
import tempfile
import uuid
import time
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 16_000_000
PROBE_TTL = 24 * 60 * 60


class PictureError(ValueError):
    pass


def read_regular(path, expected=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if expected is not None and (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
            raise PictureError('picture_changed')
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise PictureError('picture_size')
        data = os.read(fd, MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise PictureError('picture_size')
        return data
    finally:
        os.close(fd)


def folder(config, bus):
    rec = config.get('attachments') or {}
    raw = rec.get('dir', '')
    if not isinstance(raw, str) or not os.path.isabs(raw):
        raise PictureError('pictures_not_configured')
    root = Path(raw)
    if str(root.resolve(strict=True)) != raw or not root.is_dir():
        raise PictureError('picture_folder_changed')
    if root == Path(bus).resolve() or Path(bus).resolve() in root.parents:
        raise PictureError('picture_folder_in_letterbox')
    return root, rec


def validate(data):
    if not data or len(data) > MAX_BYTES:
        raise PictureError('picture_size')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        kind, ext = 'image/png', '.png'
    elif data.startswith(b'\xff\xd8\xff'):
        kind, ext = 'image/jpeg', '.jpg'
    else:
        raise PictureError('picture_type')
    with tempfile.TemporaryDirectory(prefix='desk-image-') as scratch:
        src = Path(scratch) / ('image' + ext)
        src.write_bytes(data)
        p = subprocess.run(['/usr/bin/sips', '-g', 'pixelWidth', '-g', 'pixelHeight', str(src)],
                           capture_output=True, text=True, timeout=10)
        try:
            values = dict(line.strip().split(': ', 1) for line in p.stdout.splitlines() if ': ' in line)
            w, h = int(values['pixelWidth']), int(values['pixelHeight'])
        except (KeyError, ValueError):
            raise PictureError('picture_decode') from None
        if p.returncode or min(w, h) < 1 or w * h > MAX_PIXELS:
            raise PictureError('picture_dimensions')
        p = subprocess.run(['/usr/bin/sips', '-s', 'format', 'png', str(src), '--out', str(Path(scratch) / 'decoded.png')],
                           capture_output=True, timeout=10)
        if p.returncode:
            raise PictureError('picture_decode')
    return kind, ext


def verified(config, bus, seat):
    try:
        root, rec = folder(config, bus)
        probe = (rec.get('probes') or {}).get(seat) or {}
        digest = (rec.get('readers') or {}).get(seat)
        name = probe.get('name', '')
        if not name.startswith('probe-') or Path(name).name != name:
            return False
        return bool(probe.get('dir', str(root)) == str(root) and digest and digest == probe.get('sha256') and hashlib.sha256(read_regular(root / name)).hexdigest() == digest)
    except (OSError, ValueError):
        return False


def copy_picture(config, bus, draft):
    root, _ = folder(config, bus)
    try:
        if isinstance(draft.get('path'), str):
            source = Path(draft['path'])
            info = source.lstat()
            if not source.is_absolute() or not stat.S_ISREG(info.st_mode):
                raise PictureError('picture_symlink_or_traversal')
            data = read_regular(source.resolve(strict=True), expected=info)
        else:
            raw = draft.get('data', '')
            if not isinstance(raw, str) or len(raw) > MAX_BYTES * 4 // 3 + 8:
                raise PictureError('picture_size')
            data = base64.b64decode(raw, validate=True)
        kind, ext = validate(data)
        name = 'picture-' + uuid.uuid4().hex + ext
        dest = root / name
        staging = root / ('.' + name + '.part')
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("picture write failed")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        stored = read_regular(staging)
        if stored != data:
            raise PictureError('picture_changed')
        os.link(staging, dest, follow_symlinks=False)
        os.unlink(staging)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {'path': str(dest), 'type': kind, 'size': len(stored), 'sha256': hashlib.sha256(stored).hexdigest()}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise PictureError(str(exc) if isinstance(exc, PictureError) else 'picture_copy_failed') from None


def line(record):
    # JSON encoding prevents paths from introducing new lines or envelope syntax.
    return 'Desk picture: ' + json.dumps(record, ensure_ascii=True, separators=(',', ':'))


def inspect_copy(config, bus, record):
    if not isinstance(record, dict):
        return "missing"
    try:
        root, _ = folder(config, bus)
        path = Path(record['path'])
        if path.parent != root or not path.name.startswith('picture-'):
            return 'missing'
        data = read_regular(path)
        return 'ok' if len(data) == record['size'] and hashlib.sha256(data).hexdigest() == record['sha256'] else 'changed'
    except (OSError, ValueError, KeyError, TypeError):
        return 'missing'


@contextmanager
def access_lock(state):
    # Setup, re-probes and automatic acceptance share a lock, including separate CLI processes.
    with state.lock:
        fd = os.open(state.path('attachments.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def save_access(state, rec):
    state.update_config({'attachments': rec})


def reply_fields(path):
    """Read proof without the display reader's control-character removal or case folding."""
    from .watcher import HEADER_MAX, KEY_RE
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > HEADER_MAX + 256:
                return None
            raw = os.read(fd, HEADER_MAX + 257)
        finally:
            os.close(fd)
        text = raw.decode('utf-8')
    except (OSError, UnicodeError):
        return None
    lines = text.split('\n')
    if not lines or lines[0].rstrip('\r') != '---':
        return None
    fields = {}
    for i, line in enumerate(lines[1:], 1):
        if line.rstrip('\r') == '---':
            if len('\n'.join(lines[:i+1]).encode()) > HEADER_MAX:
                return None
            return fields, '\n'.join(lines[i+1:]).strip()
        key, sep, value = line.rstrip('\r').partition(':')
        key = key.strip()
        if sep and KEY_RE.fullmatch(key) and key not in fields:
            fields[key] = value.strip()
    return None


def check_reply(cfg, bus, seat, reply_id):
    from .server import ENVELOPE_ID_RE
    if not ENVELOPE_ID_RE.fullmatch(reply_id):
        return False
    root, rec = folder(cfg, bus)
    probe = (rec.get('probes') or {}).get(seat) or {}
    digest, name = probe.get('sha256'), probe.get('name', '')
    if (not digest or not probe.get('letter') or not name.startswith('probe-') or Path(name).name != name
            or probe.get('dir') != str(root) or probe.get('accepted_reply')
            or probe.get('expires_at', 0) <= time.time()):
        return False
    if hashlib.sha256(read_regular(root / name)).hexdigest() != digest:
        return False
    for sub in ('inbox', 'processed'):
        directory = Path(bus) / 'desk' / sub
        if directory.is_symlink():
            continue
        parsed = reply_fields(directory / (reply_id + '.md'))
        if parsed:
            fields, body = parsed
            if (fields.get('id') == reply_id and fields.get('from') == seat and fields.get('to') == 'desk'
                    and fields.get('re') == probe['letter'] and body == digest):
                return True
    return False


def accept_replies(state, log=lambda message: None):
    """Also scan existing replies after restart; watcher baselines must not hide proofs."""
    with access_lock(state):
        cfg = state.config()
        bus = cfg.get('bus') or cfg.get('letterbox_dir') or ''
        if not cfg.get('attachments'):
            return []
        try:
            _, rec = folder(cfg, bus)
        except (OSError, ValueError):
            return []
        outstanding = [s for s in cfg.get('seats', [])
                       if (rec.get('probes') or {}).get(s, {}).get('letter') and not verified(cfg, bus, s)]
        if not outstanding:
            return []
        candidates = set()
        for sub in ('inbox', 'processed'):
            directory = Path(bus) / 'desk' / sub
            if directory.is_symlink():
                continue
            try:
                candidates.update(p.stem for p in directory.iterdir() if p.name.endswith('.md'))
            except OSError:
                pass
        accepted = []
        for seat in outstanding:
            for reply_id in sorted(candidates):
                try:
                    valid = check_reply(cfg, bus, seat, reply_id)
                except (OSError, ValueError, KeyError):
                    valid = False
                if valid:
                    rec['probes'][seat]['accepted_reply'] = reply_id
                    rec.setdefault('readers', {})[seat] = rec['probes'][seat]['sha256']
                    save_access(state, rec)
                    accepted.append(seat)
                    log(f'pictures: {seat} verified')
                    break
        return accepted


def safe_destination(raw, bus, configured=None):
    raw = Path(raw)
    if not raw.is_absolute() or raw.is_symlink():
        raise PictureError('absolute non-symlink picture folder required')
    canonical = raw.resolve()
    root = Path(bus).resolve()
    if not bus or canonical == root or root in canonical.parents:
        raise PictureError('picture folder must be outside the letterbox')
    if canonical.exists():
        info = canonical.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PictureError('picture folder must be a private directory owned by you')
        if str(canonical) != configured and any(canonical.iterdir()):
            raise PictureError('picture folder is not empty; choose a dedicated folder')
    parent = canonical if canonical.exists() else canonical.parent
    while not parent.exists():
        parent = parent.parent
    if not os.access(parent, os.W_OK | os.X_OK):
        raise PictureError('picture folder parent is not writable')
    return canonical


def command(state, action, value='', reply_id=''):
    with access_lock(state):
        return _command(state, action, value, reply_id)


def _command(state, action, value='', reply_id=''):
    """Operator-requested setup; verification still requires each seat's actual read proof."""
    from .sender import from_config
    cfg = state.config()
    bus = cfg.get('bus') or cfg.get('letterbox_dir') or ''
    if action in ('setup', 'verify', 'request') and cfg.get('helper'):
        from_config(cfg, bus)  # Reject unsupported saved helpers before creating files.
    if action == 'setup':
        if not bus:
            raise PictureError('set up your letterbox first')
        current = (cfg.get('attachments') or {}).get('dir')
        desired = value or current or str(Path(bus).resolve().parent / 'desk-attachments')
        canonical = safe_destination(Path(desired).expanduser(), bus, current)
        if current != str(canonical):
            _command(state, 'configure', str(canonical))
        else:
            folder(cfg, bus)  # Never silently repair a changed configured destination.
            safe_destination(Path(current), bus, current)
        failures = []
        for seat in cfg.get('seats', []):
            fresh = state.config()
            if verified(fresh, bus, seat):
                continue
            root, rec = folder(fresh, bus)
            probe = (rec.get('probes') or {}).get(seat) or {}
            name = probe.get('name', '')
            try:
                pending = (bool(probe.get('letter')) and probe.get('dir') == str(root)
                           and not probe.get('accepted_reply') and probe.get('expires_at', 0) > time.time()
                           and name.startswith('probe-') and Path(name).name == name
                           and hashlib.sha256(read_regular(root / name)).hexdigest() == probe.get('sha256'))
            except (OSError, ValueError):
                pending = False
            if not pending:
                try:
                    _command(state, 'verify', seat)
                except (OSError, ValueError, KeyError) as exc:
                    failures.append(f'{seat}: {exc}')
        status = _command(state, 'status')
        if failures:
            raise PictureError(status + '\n' + '\n'.join(failures))
        return 'Picture folder: ' + str(canonical) + '\n' + status
    if action == 'configure':
        raw = Path(value).expanduser()
        if not raw.is_absolute():
            raise PictureError('absolute picture folder required')
        canonical = safe_destination(raw, bus, (cfg.get('attachments') or {}).get('dir'))
        canonical.mkdir(mode=0o700, parents=True, exist_ok=True)
        save_access(state, {'dir': str(canonical), 'readers': {}, 'probes': {}})
        return 'Picture folder configured; no seat is verified yet.'
    if action == 'request':
        if value not in cfg.get('seats', []):
            raise PictureError('unknown seat')
        if not cfg.get('attachments'):
            if not bus:
                raise PictureError('set up your letterbox first')
            _command(state, 'configure', str(Path(bus).resolve().parent / 'desk-attachments'))
        return _command(state, 'verify', value)
    root, rec = folder(cfg, bus)
    if action == 'status':
        return '\n'.join(f'{seat}: {"verified" if verified(cfg, bus, seat) else "not verified"}' for seat in cfg.get('seats', []))
    seat = value
    if seat not in cfg.get('seats', []):
        raise PictureError('unknown seat')
    if action == 'verify':
        rec.setdefault('readers', {}).pop(seat, None)
        rec.setdefault('probes', {}).pop(seat, None)
        save_access(state, rec)
        name = 'probe-' + uuid.uuid4().hex
        data = os.urandom(128)
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, data); os.fsync(fd)
        finally:
            os.close(fd)
        helper = from_config(cfg, bus)
        result = helper.send(seat, 'Please read this probe inside your normal seat sandbox and reply with only its SHA256: ' + json.dumps(str(root / name)))
        if result.get('status') != 'sent':
            raise PictureError('probe letter not confirmed; file retained')
        rec.setdefault('readers', {}).pop(seat, None)
        rec.setdefault('probes', {})[seat] = {'name': name, 'sha256': hashlib.sha256(data).hexdigest(), 'letter': result['id'],
                                                    'dir': str(root), 'expires_at': int(time.time()) + PROBE_TTL}
        save_access(state, rec)
        return 'Probe sent. Pictures will be ready when ' + seat + ' replies with its read proof.'
    if action == 'accept':
        if not check_reply(cfg, bus, seat, reply_id):
            raise PictureError('probe reply or stored bytes do not match')
        rec['probes'][seat]['accepted_reply'] = reply_id
        rec.setdefault('readers', {})[seat] = rec['probes'][seat]['sha256']
        save_access(state, rec)
        return seat + ' verified for this canonical folder and probe.'
    raise PictureError('unknown attachment action')
