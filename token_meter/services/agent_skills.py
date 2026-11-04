"""Install the Token Meter skill package into a client's user skills folder."""

import contextlib
import hashlib
import os
import re
import secrets
import stat
import threading


PACKAGE_DIR = "agent-skills"
MANAGED_MARKER = b"<!-- token-meter-managed-skill:"
SKILL_FILE = "SKILL.md"
MAX_SKILL_BYTES = 256 * 1024
_NAME_RE = re.compile(r"^token-meter-[a-z0-9-]{1,64}$")
# A file is Token Meter's only if its bytes match a version Token Meter shipped, so a
# hand-edited copy stays the user's. Every shipped version's sha256 stays listed; a
# contract test fails when a skill changes without adding its new hash.
RELEASED_SKILL_HASHES = {
    "token-meter-usage-review": frozenset({
        "cfd82e1531311c5271cf879f81325ee3aa71b9b390750bd972e05f5e826c294f",
        "938f1e38f8e9666c025896a7a87363bcb4840fb768add85b15d110733190e3a0",
        "064d01799aabbd921a9daa70c9b2c2d2cc47fdf95cf1202aff72d1a201a955ca",
    }),
    "token-meter-budget-pacing": frozenset({
        "9eb945b77380f1c54658e897810e738621c423c61d57734d3b8e41712db61dbd",
        "051a4b6fc25fecb48c2b7efd3c3dbebf581183134f84335004dc7765b45ffe8f",
    }),
    "token-meter-skill-hygiene": frozenset({
        "98b57e2127285f1855a75b72bfc42ccf8ffa704f48019e8d3d3b56590c671cef",
        "67a92b2800a0eb3e052a232e6fec68145bd9935538709ae47ff7442e1a4fdde5",
    }),
}
_IGNORABLE_ENTRIES = frozenset({".DS_Store"})
_LOCK = threading.Lock()
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_BINARY = getattr(os, "O_BINARY", 0)


def _sha256(data):
    # Line endings are normalized so a CRLF checkout (Git for Windows) hashes like LF.
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _read_regular(path):
    """Read a regular file without following a final symlink or blocking on a FIFO."""
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _BINARY)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        data = os.read(fd, MAX_SKILL_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_SKILL_BYTES:
        raise OSError("skill file too large")
    return data


def package_skills(source_root):
    """Name and content of every managed skill shipped in this install."""
    root = os.path.join(source_root, PACKAGE_DIR)
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return {}
    skills = {}
    for name in names:
        if not _NAME_RE.fullmatch(name):
            continue
        try:
            data = _read_regular(os.path.join(root, name, SKILL_FILE))
        except OSError:
            continue
        if MANAGED_MARKER in data:
            skills[name] = data
    return skills


def skill_state(skills_root, name, source):
    """missing, current, outdated, or unmanaged for one installed skill.

    The skills root itself may be a symlink (dotfile-managed ~/.claude/skills); only the
    per-skill folder and file must be real, so Token Meter never writes through a link.
    """
    target_dir = os.path.join(skills_root, name)
    target = os.path.join(target_dir, SKILL_FILE)
    try:
        dir_mode = os.lstat(target_dir).st_mode
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unmanaged"
    if not stat.S_ISDIR(dir_mode):
        return "unmanaged"
    try:
        os.lstat(target)
    except FileNotFoundError:
        try:
            leftovers = set(os.listdir(target_dir)) - _IGNORABLE_ENTRIES
        except OSError:
            return "unmanaged"
        return "unmanaged" if leftovers else "missing"
    except OSError:
        return "unmanaged"
    try:
        installed = _read_regular(target)
    except OSError:
        return "unmanaged"
    if installed == source:
        return "current"
    if _sha256(installed) in RELEASED_SKILL_HASHES.get(name, ()):
        return "outdated"
    return "unmanaged"


def package_status(skills_root, source_root):
    skills = package_skills(source_root)
    states = {name: skill_state(skills_root, name, data) for name, data in skills.items()}
    current = [name for name, state in states.items() if state == "current"]
    outdated = [name for name, state in states.items() if state == "outdated"]
    missing = [name for name, state in states.items() if state == "missing"]
    unmanaged = [name for name, state in states.items() if state == "unmanaged"]
    if not skills:
        state = "unavailable"
    elif len(current) == len(skills):
        state = "installed"
    elif outdated and len(current) + len(outdated) == len(skills):
        state = "outdated"
    elif current or outdated:
        state = "partial"
    elif unmanaged:
        state = "conflict"
    else:
        state = "missing"
    return {
        "available": bool(skills),
        "state": state,
        "names": list(skills),
        "total": len(skills),
        "installed": len(current),
        "managed": len(current) + len(outdated),
        "outdated": outdated,
        "missing": missing,
        "unmanaged": unmanaged,
    }


def _atomic_write_bytes(path, data):
    directory = os.path.dirname(path)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.token-meter-{os.getpid()}-{secrets.token_hex(8)}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def _install(skills_root, source_root, only_outdated=False):
    changed, ok = [], True
    with _LOCK:
        for name, data in package_skills(source_root).items():
            state = skill_state(skills_root, name, data)
            if not (state == "outdated" or (state == "missing" and not only_outdated)):
                continue
            target_dir = os.path.join(skills_root, name)
            created = not os.path.lexists(target_dir)
            try:
                if created:
                    os.makedirs(target_dir)
                if skill_state(skills_root, name, data) != state:
                    raise OSError("skill changed during install")
                _atomic_write_bytes(os.path.join(target_dir, SKILL_FILE), data)
                changed.append(name)
            except OSError:
                ok = False
                if created:
                    with contextlib.suppress(OSError):
                        os.rmdir(target_dir)
    return {"ok": ok, "changed": changed}


def install_package(skills_root, source_root):
    """Write missing or outdated managed skills; never touch a same-named user skill."""
    return _install(skills_root, source_root)


def refresh_package(skills_root, source_root):
    """Update managed skills already installed; add nothing new."""
    return _install(skills_root, source_root, only_outdated=True)


def remove_package(skills_root, source_root):
    """Delete only managed skill files, then each folder when it is left empty."""
    changed, ok = [], True
    with _LOCK:
        for name, data in package_skills(source_root).items():
            if skill_state(skills_root, name, data) not in ("current", "outdated"):
                continue
            target_dir = os.path.join(skills_root, name)
            try:
                # Re-check right before deleting; the folder and file are known real here.
                if skill_state(skills_root, name, data) not in ("current", "outdated"):
                    continue
                os.unlink(os.path.join(target_dir, SKILL_FILE))
                changed.append(name)
            except FileNotFoundError:
                continue
            except OSError:
                ok = False
                continue
            with contextlib.suppress(OSError):
                os.rmdir(target_dir)
    return {"ok": ok, "changed": changed}
