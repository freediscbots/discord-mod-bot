import json
import time
from pathlib import Path

import config

DATA_FILE = Path(__file__).parent / "warnings.json"
HASH_FILE = Path(__file__).parent / "image_blacklist.json"
PROTECTED_FILE = Path(__file__).parent / "protected_users.json"


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _normalize_entry(entry) -> dict:
    if isinstance(entry, str):
        return {"reason": entry, "timestamp": time.time()}
    return entry


def _active_entries(entries: list) -> list:
    entries = [_normalize_entry(e) for e in entries]
    if not config.WARNING_DECAY_DAYS:
        return entries
    cutoff = time.time() - (config.WARNING_DECAY_DAYS * 86400)
    return [e for e in entries if e.get("timestamp", time.time()) >= cutoff]


def add_warning(guild_id: int, user_id: int, reason: str) -> int:
    data = _load(DATA_FILE)
    key = f"{guild_id}:{user_id}"
    entries = data.setdefault(key, [])
    entries.append({"reason": reason, "timestamp": time.time()})
    _save(DATA_FILE, data)
    return len(_active_entries(entries))


def get_warnings(guild_id: int, user_id: int) -> list[str]:
    data = _load(DATA_FILE)
    entries = data.get(f"{guild_id}:{user_id}", [])
    return [e["reason"] for e in _active_entries(entries)]


def get_warning_count(guild_id: int, user_id: int) -> int:
    return len(get_warnings(guild_id, user_id))


def clear_warnings(guild_id: int, user_id: int) -> None:
    data = _load(DATA_FILE)
    key = f"{guild_id}:{user_id}"
    if key in data:
        del data[key]
        _save(DATA_FILE, data)


def add_image_hash(guild_id: int, image_hash: str, reason: str) -> None:
    data = _load(HASH_FILE)
    key = str(guild_id)
    entries = data.setdefault(key, [])
    if not any(e["hash"] == image_hash for e in entries):
        entries.append({"hash": image_hash, "reason": reason, "timestamp": time.time()})
        _save(HASH_FILE, data)


def get_image_hashes(guild_id: int) -> list[dict]:
    data = _load(HASH_FILE)
    return data.get(str(guild_id), [])


def add_protected_user(guild_id: int, user_id: int) -> bool:
    data = _load(PROTECTED_FILE)
    key = str(guild_id)
    users = data.setdefault(key, [])
    if user_id in users:
        return False
    users.append(user_id)
    _save(PROTECTED_FILE, data)
    return True


def remove_protected_user(guild_id: int, user_id: int) -> bool:
    data = _load(PROTECTED_FILE)
    key = str(guild_id)
    users = data.get(key, [])
    if user_id not in users:
        return False
    users.remove(user_id)
    _save(PROTECTED_FILE, data)
    return True


def get_protected_users(guild_id: int) -> list[int]:
    data = _load(PROTECTED_FILE)
    return data.get(str(guild_id), [])
