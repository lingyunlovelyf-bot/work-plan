# Fast Note Sync is the task source when url, token, and vault are set.
# Local files are a cache. A newer server copy is never overwritten.

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "fns.local.json"
INDEX = HERE / "fns-index.json"
MIRROR = HERE / "fns-cache"


def read_config() -> dict:
    try:
        saved = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return saved if isinstance(saved, dict) else {}


def enabled() -> bool:
    saved = read_config()
    return bool(str(saved.get("url") or "").strip() and str(saved.get("token") or "").strip() and str(saved.get("vault") or "").strip())


def public() -> dict:
    saved = read_config()
    return {
        "fns_url": str(saved.get("url") or ""),
        "fns_vault": str(saved.get("vault") or ""),
        "has_fns_token": bool(str(saved.get("token") or "").strip()),
        "fns_on": enabled(),
    }


def save_config(data: dict) -> None:
    watched = ("fns_url", "fns_token", "fns_vault")
    if not any(key in data for key in watched):
        return
    saved = read_config()
    if "fns_url" in data:
        url = str(data.get("fns_url") or "").strip().rstrip("/")
        if url:
            if not url.startswith(("http://", "https://")) or len(url) > 200:
                raise ValueError("同步地址要以 http:// 或 https:// 开头")
            saved["url"] = url
        elif "fns_url" in data:
            saved.pop("url", None)
    token = str(data.get("fns_token") or "").strip()
    if token:
        if len(token) > 400:
            raise ValueError("同步令牌太长")
        saved["token"] = token
    if "fns_vault" in data:
        vault = str(data.get("fns_vault") or "").strip()
        if vault:
            if len(vault) > 80 or "/" in vault or "\\" in vault:
                raise ValueError("库名写保险库的名字，不要带路径")
            saved["vault"] = vault
        else:
            saved.pop("vault", None)
    if not saved.get("url") and not saved.get("token") and not saved.get("vault"):
        try:
            CONFIG.unlink()
        except OSError:
            pass
        return
    CONFIG.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mirror() -> Path:
    MIRROR.mkdir(parents=True, exist_ok=True)
    return MIRROR


def handles(path: Path) -> bool:
    if not enabled():
        return False
    try:
        path.resolve().relative_to(mirror().resolve())
    except (OSError, ValueError):
        return False
    return True


def _load_index() -> dict:
    try:
        saved = json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    notes = saved.get("notes") if isinstance(saved, dict) else None
    return notes if isinstance(notes, dict) else {}


def _store_index(notes: dict) -> None:
    INDEX.write_text(json.dumps({"notes": notes}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _request(method: str, route: str, query: dict | None = None, body: dict | None = None) -> dict:
    saved = read_config()
    base = str(saved.get("url") or "").rstrip("/")
    token = str(saved.get("token") or "")
    if query:
        route = route + "?" + urllib.parse.urlencode({key: value for key, value in query.items() if value is not None})
    data = None
    headers = {"token": token, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + route, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(_message(detail) or f"同步服务返回 {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise OSError("连不上同步服务") from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ValueError("同步服务没有返回笔记") from exc
    if isinstance(payload, dict) and payload.get("status") is False:
        raise ValueError(str(payload.get("message") or "同步服务拒绝了这次操作"))
    if isinstance(payload, dict) and "data" in payload:
        data_body = payload.get("data")
        return data_body if isinstance(data_body, dict) else {"list": data_body}
    return payload if isinstance(payload, dict) else {}


def _message(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("message") or "")
    return ""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _list_notes(vault: str) -> list[dict]:
    rows = []
    page = 1
    while page < 40:
        data = _request("GET", "/api/notes", {"vault": vault, "page": page, "pageSize": 100, "sortBy": "mtime", "sortOrder": "desc"})
        batch = data.get("list") if isinstance(data, dict) else None
        if not isinstance(batch, list):
            break
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 100:
            break
        page += 1
    return rows


def _get_note(vault: str, path: str) -> dict | None:
    try:
        data = _request("GET", "/api/note", {"vault": vault, "path": path})
    except ValueError as exc:
        if "404" in str(exc) or "不存在" in str(exc) or "not found" in str(exc).lower():
            return None
        raise
    if not isinstance(data, dict) or "content" not in data:
        return None
    return data


def _wanted(name: str, words: list[str], suffixes: set[str]) -> bool:
    lower = name.lower()
    if not any(lower.endswith(suffix) for suffix in suffixes):
        return False
    return any(word in name for word in words)


def _mirror_path(rel: str) -> Path:
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError("笔记路径不对")
    path = (mirror() / rel).resolve()
    root = mirror().resolve()
    if path != root and root not in path.parents:
        raise ValueError("笔记路径不对")
    return path


def _write_cache(rel: str, content: str) -> None:
    path = _mirror_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def refresh(words: list[str], suffixes: set[str]) -> bool:
    """Download notes whose server mtime changed. Returns True when the cache is stale."""
    if not enabled():
        return False
    saved = read_config()
    vault = str(saved.get("vault") or "")
    try:
        listed = _list_notes(vault)
    except (OSError, ValueError):
        return True
    index = _load_index()
    keep = set()
    try:
        for item in listed:
            rel = str(item.get("path") or "").replace("\\", "/").lstrip("/")
            name = Path(rel).name
            if not rel or not _wanted(name, words, suffixes):
                continue
            keep.add(rel)
            stamp = item.get("mtime")
            known = index.get(rel) if isinstance(index.get(rel), dict) else {}
            cached = _mirror_path(rel)
            if known.get("mtime") == stamp and known.get("contentHash") and cached.is_file():
                continue
            detail = _get_note(vault, rel)
            if detail is None:
                continue
            content = str(detail.get("content") or "")
            _write_cache(rel, content)
            index[rel] = {
                "mtime": detail.get("mtime", stamp),
                "contentHash": str(detail.get("contentHash") or _digest(content)),
            }
        for rel in list(index):
            if rel not in keep:
                index.pop(rel, None)
                try:
                    _mirror_path(rel).unlink()
                except OSError:
                    pass
        _store_index(index)
    except (OSError, ValueError):
        return True
    return False


def commit(path: Path, body: str) -> None:
    """Write the server first. Refuse when the server copy is newer than the one we read."""
    saved = read_config()
    vault = str(saved.get("vault") or "")
    rel = path.resolve().relative_to(mirror().resolve()).as_posix()
    try:
        remote = _get_note(vault, rel)
    except OSError as exc:
        raise ValueError("连不上同步服务，这次没写入") from exc
    index = _load_index()
    known = index.get(rel) if isinstance(index.get(rel), dict) else {}
    base = str(known.get("contentHash") or "")
    if remote is not None:
        content = str(remote.get("content") or "")
        remote_hash = str(remote.get("contentHash") or _digest(content))
        if base and remote_hash != base:
            _write_cache(rel, content)
            index[rel] = {"mtime": remote.get("mtime"), "contentHash": remote_hash}
            _store_index(index)
            raise ValueError("这篇刚被别的设备改过")
        base = remote_hash
    payload = {"path": rel, "vault": vault, "content": body, "contentHash": _digest(body)}
    if base:
        payload["baseHash"] = base
    try:
        saved_note = _request("POST", "/api/note", body=payload)
    except OSError as exc:
        raise ValueError("连不上同步服务，这次没写入") from exc
    content_hash = str(saved_note.get("contentHash") or _digest(body)) if isinstance(saved_note, dict) else _digest(body)
    mtime = saved_note.get("mtime") if isinstance(saved_note, dict) else None
    index[rel] = {"mtime": mtime, "contentHash": content_hash}
    _store_index(index)
