"""Install the Token Meter skill package into a client's user skills folder."""

import contextlib
import os
import re
import secrets


PACKAGE_DIR = "agent-skills"
MANAGED_MARKER = b"<!-- token-meter-managed-skill:"
SKILL_FILE = "SKILL.md"
MAX_SKILL_BYTES = 256 * 1024
_NAME_RE = re.compile(r"^token-meter-[a-z0-9-]{1,64}$")


def _read(path):
    with open(path, "rb") as fh:
        data = fh.read(MAX_SKILL_BYTES + 1)
    return data if len(data) <= MAX_SKILL_BYTES else b""


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
            data = _read(os.path.join(root, name, SKILL_FILE))
        except OSError:
            continue
        if MANAGED_MARKER in data:
            skills[name] = data
    return skills


def skill_state(skills_root, name, source):
    """missing, current, outdated, or unmanaged for one installed skill."""
    target_dir = os.path.join(skills_root, name)
    target = os.path.join(target_dir, SKILL_FILE)
    if not os.path.lexists(target_dir):
        return "missing"
    if os.path.islink(target_dir) or not os.path.isdir(target_dir) or os.path.islink(target):
        return "unmanaged"
    if not os.path.lexists(target):
        try:
            return "unmanaged" if os.listdir(target_dir) else "missing"
        except OSError:
            return "unmanaged"
    try:
        installed = _read(target)
    except OSError:
        return "unmanaged"
    if MANAGED_MARKER not in installed:
        return "unmanaged"
    return "current" if installed == source else "outdated"


def package_status(skills_root, source_root):
    skills = package_skills(source_root)
    states = {name: skill_state(skills_root, name, data) for name, data in skills.items()}
    current = [name for name, state in states.items() if state == "current"]
    outdated = [name for name, state in states.items() if state == "outdated"]
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
        "unmanaged": unmanaged,
    }


def _atomic_write_bytes(path, data):
    directory = os.path.dirname(path)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.token-meter-{os.getpid()}-{secrets.token_hex(8)}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def _install(skills_root, source_root, only_outdated=False):
    changed, ok = [], True
    for name, data in package_skills(source_root).items():
        state = skill_state(skills_root, name, data)
        if state == "outdated" or (state == "missing" and not only_outdated):
            try:
                os.makedirs(os.path.join(skills_root, name), exist_ok=True)
                _atomic_write_bytes(os.path.join(skills_root, name, SKILL_FILE), data)
                changed.append(name)
            except OSError:
                ok = False
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
    for name, data in package_skills(source_root).items():
        if skill_state(skills_root, name, data) not in ("current", "outdated"):
            continue
        target_dir = os.path.join(skills_root, name)
        try:
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
