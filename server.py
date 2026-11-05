# Local viewer for Obsidian tasks in a chosen folder.
# Run: python server.py

from __future__ import annotations

import calendar
import json
import re
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = 8876
HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "settings.json"
TIMEBOOK = HERE / "timebook.local.json"
DEFAULT_VAULT = r"E:\my_note\03工作"
PLAN_WORDS = ("接下来", "工作规划", "本周工作", "日计划", "周计划")
TASK_RE = re.compile(r"^([\t ]*)- \[([ xX/\-!>~*?#])\](.*)$")
DONE_RE = re.compile(r"\s*✅\s*\d{4}-\d{2}-\d{2}")
CANCEL_RE = re.compile(r"\s*❌\s*\d{4}-\d{2}-\d{2}")
DUE_TOKEN = re.compile(r"\s*📅\s*\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?")
ID_RE = re.compile(r"\s*🆔\s*\S+")
PRIORITY_MARK = re.compile(r"[🔺⏫🔼🔽⏬]")
REPEAT_RE = re.compile(
    r"🔁\s+every(?:\s+(\d+))?\s+(day|days|week|weeks|month|months|year|years)"
    r"(?:\s+on\s+([A-Za-z]+))?",
    re.I,
)
META_RE = re.compile(
    r"\s*(?:🆔\s*\S+|✅\s*\d{4}-\d{2}-\d{2}|❌\s*\d{4}-\d{2}-\d{2}|📅\s*\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?"
    r"|➕\s*\d{4}-\d{2}-\d{2}|🛫\s*\d{4}-\d{2}-\d{2}|⏳\s*\d{4}-\d{2}-\d{2}"
    r"|🔁\s+every(?:\s+\d+)?\s+\w+(?:\s+on\s+[A-Za-z]+)?|👤\S+|[⏫🔺🔼🔽⏬])",
    re.I,
)
PEOPLE_RE = re.compile(r"👤([^\s👤]+)")
TAG_RE = re.compile(r"(?<!\S)#([^\s#]+)")
CTX_RE = re.compile(r"(?<!\S)@([^\s@]+)")
PROJ_RE = re.compile(r"(?<!\S)\+(\[\[[^\]]+\]\]|\S+)")
SCHED_TOKEN = re.compile(r"\s*⏳\s*\d{4}-\d{2}-\d{2}")
START_TOKEN = re.compile(r"\s*🛫\s*\d{4}-\d{2}-\d{2}")
CREATED_TOKEN = re.compile(r"\s*➕\s*\d{4}-\d{2}-\d{2}")
REMOVE_RE = re.compile(r"(?<!\S)-([#@+])(\[\[[^\]]+\]\]|[^\s]+)")
PRIORITY_EMOJI = {"highest": "🔺", "high": "⏫", "medium": "🔼", "low": "🔽", "lowest": "⏬"}
EMOJI_PRIORITY = {emoji: name for name, emoji in PRIORITY_EMOJI.items()}
PRIORITY_WORD = {"最高": "highest", "高": "high", "中": "medium", "低": "low", "最低": "lowest"}
STATUS_WORD = {
    "待做": " ",
    "完成": "x",
    "取消": "-",
    "进行中": "/",
    "搁置": "!",
    "计划": ">",
    "暂缓": "~",
    "阻塞": "#",
    "将来": "?",
    "就绪": "*",
}
STATUS_LABEL = {mark: word for word, mark in STATUS_WORD.items()}
STATUS_LABEL["X"] = "完成"
WEEKDAY_NAME = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
WEEK_CHAR = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
DATE_PHRASE = re.compile(
    r"(截止|排期|开始)?\s*"
    r"(?:"
    r"(大后天|今天|明天|后天)"
    r"|((?:下下|下|这)?)(周|星期|礼拜)([一二三四五六日天])"
    r"|(?:📅|\^)?\s*(\d{4}-\d{2}-\d{2})"
    r"|(?:(\d{4})\s*年\s*)?(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})(?:\s*(?:日|号))?"
    r")"
    r"(?:\s+(\d{1,2})[:：](\d{2}))?"
)


def file_sort_key(name: str) -> tuple:
    iso = re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", name)
    if iso:
        return (int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), name)
    md = re.match(r"(\d{2})(\d{2})_", name)
    if md:
        month, day = int(md.group(1)), int(md.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return (2026, month, day, name)
    return (0, 0, 0, name)


def read_settings() -> dict:
    try:
        saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            return saved
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def name_keywords() -> list[str]:
    raw = read_settings().get("name_keywords")
    words = []
    if isinstance(raw, list):
        for item in raw:
            word = str(item).strip()
            if word and word not in words:
                words.append(word)
    return words or list(PLAN_WORDS)


def browser_choice() -> str:
    text = str(read_settings().get("browser") or "edge").strip()
    return text or "edge"


def vault_root() -> Path | None:
    saved = read_settings()
    if "vault" not in saved:
        raw = DEFAULT_VAULT
    else:
        raw = str(saved.get("vault") or "").strip()
        if not raw:
            return None
    path = Path(raw)
    try:
        return path.resolve()
    except OSError:
        return path


def archive_file() -> Path:
    root = vault_root()
    if root is None:
        raise ValueError("先选择笔记文件夹")
    return root.parent / "TaskForge-Archive.md"


def note_files():
    root = vault_root()
    if root is None or not root.is_dir():
        return
    try:
        found = list(root.rglob("*.md"))
    except OSError:
        return
    for path in found:
        yield path


def parse_hotkey(text: str) -> tuple[int, int]:
    mods = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
    parts = [part.strip() for part in str(text or "").split("+") if part.strip()]
    mod = 0
    key = 0
    for part in parts:
        low = part.lower()
        if low in mods:
            mod |= mods[low]
        elif len(part) == 1 and part.isascii() and part.isalnum():
            key = ord(part.upper())
        else:
            raise ValueError("快捷键写成 Ctrl+Alt+Shift+K")
    if not mod or not key:
        raise ValueError("快捷键写成 Ctrl+Alt+Shift+K")
    return mod, key


def global_hotkey() -> str:
    text = str(read_settings().get("global_hotkey") or "").strip()
    return text or "Ctrl+Alt+Shift+K"


def show_hotkey() -> str:
    text = str(read_settings().get("global_show") or "").strip()
    return text or "Ctrl+Alt+J"


def loose_hotkey() -> str:
    text = str(read_settings().get("global_loose") or "").strip()
    return text or "Ctrl+Alt+F"


def hotkey_specs() -> list[tuple[int, str, str]]:
    return [
        (1, "create", global_hotkey()),
        (2, "show", show_hotkey()),
        (3, "loose", loose_hotkey()),
    ]


def write_settings(data: dict) -> None:
    SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Hotkeys:
    def __init__(self) -> None:
        self.action = ""
        self.thread = None
        self.thread_id = 0
        self.registered = False
        self.ready = False
        self.bound = {}
        self.fail_text = ""

    def restart(self) -> bool:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        if self.thread and self.thread.is_alive() and self.thread_id:
            self.ready = False
            if not user32.PostThreadMessageW(self.thread_id, 0x8000, 0, 0):
                self.fail_text = "快捷键没有重新挂上"
                return False
            for _ in range(40):
                if self.ready:
                    return self.registered
                __import__("time").sleep(0.05)
            return False
        self.ready = False
        self.registered = False
        self.thread = __import__("threading").Thread(target=self.loop, daemon=True)
        self.thread.start()
        for _ in range(40):
            if self.ready:
                return self.registered
            __import__("time").sleep(0.05)
        return False

    def loop(self) -> None:
        import ctypes
        import subprocess
        import sys
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        self.thread_id = kernel32.GetCurrentThreadId()
        bound = {}

        def register() -> None:
            for hot_id in list(bound):
                user32.UnregisterHotKey(None, hot_id)
            bound.clear()
            failed = []
            seen = {}
            for hot_id, action, combo in hotkey_specs():
                try:
                    mod, vk = parse_hotkey(combo)
                except ValueError:
                    failed.append(combo)
                    continue
                if (mod, vk) in seen:
                    failed.append(combo + " 和另一组重复了")
                    continue
                if not user32.RegisterHotKey(None, hot_id, mod | 0x4000, vk):
                    failed.append(combo)
                    continue
                seen[(mod, vk)] = combo
                bound[hot_id] = action
            self.bound = bound
            self.fail_text = "、".join(failed) + " 被别的软件占用了" if failed else ""
            self.registered = not failed and 1 in bound
            self.ready = True

        register()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x8000:
                register()
                continue
            if msg.message == 0x0312:
                action = bound.get(int(msg.wParam), "")
                if action in ("create", "loose"):
                    POP_MODE["mode"] = action
                    subprocess.Popen([sys.executable, str(HERE / "launch.py"), action])
                elif action == "show":
                    subprocess.Popen([sys.executable, str(HERE / "launch.py")])
                elif action:
                    self.action = action
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        for hot_id in list(bound):
            user32.UnregisterHotKey(None, hot_id)


HOTKEYS = Hotkeys()
POP_MODE = {"mode": ""}


def take_pop() -> dict:
    mode = POP_MODE["mode"]
    POP_MODE["mode"] = ""
    return {"mode": mode}


APP_KEY_DEFAULTS = {
    "search": "Ctrl+K",
    "quick": "d",
    "focus": "s",
    "loose": "f",
    "child": "c",
    "pin": "p",
    "today": "t",
    "tomorrow": "m",
    "listDown": "↓",
    "listDown2": "j",
    "listUp": "↑",
    "listUp2": "k",
    "parent": "←",
    "siblingUp": "Shift+↑",
    "siblingDown": "Shift+↓",
    "toggle": "x",
    "indentOut": "[",
    "indentIn": "]",
    "close": "Alt+U",
    "save": "Ctrl+S",
    "pause": "Ctrl+Enter",
    "finish": "Ctrl+Shift+Enter",
    "send": "Alt+Enter",
    "create": "Ctrl+Enter",
    "find": "n",
    "view1": "1",
    "view2": "2",
    "view3": "3",
    "view4": "4",
    "depth": "a",
    "view5": "5",
    "view6": "6",
}
CHORD_RE = re.compile(r"^(?:(?:Ctrl|Alt|Shift)\+)*(?:[a-z0-9\[\]#@+]|Enter|Space|[↑↓←→])$")


def app_keys_from(raw) -> dict:
    found = dict(APP_KEY_DEFAULTS)
    if isinstance(raw, dict):
        for key in APP_KEY_DEFAULTS:
            value = str(raw.get(key) or "").strip()
            if CHORD_RE.fullmatch(value):
                found[key] = value
    return found


def app_keys() -> dict:
    return app_keys_from(read_settings().get("app_keys"))


def vault_rev() -> str:
    words = name_keywords()
    parts = []
    try:
        stat = SETTINGS.stat()
        parts.append(f"settings:{stat.st_mtime_ns}:{stat.st_size}")
    except OSError:
        parts.append("settings:0")
    for path in note_files():
        if not path.is_file() or "TaskForge" in path.parts or not is_plan_name(path.name, words):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        root = vault_root()
        rel = path.relative_to(root).as_posix()
        parts.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
    parts.sort()
    return "|".join(parts)


UPDATE_STATUS = {"text": ""}


def take_signal() -> dict:
    action = HOTKEYS.action
    HOTKEYS.action = ""
    return {"action": action, "rev": vault_rev(), "update": UPDATE_STATUS["text"]}


def save_settings(data: dict) -> dict:
    current = read_settings()
    if "name_keywords" in data:
        raw = data.get("name_keywords")
        if isinstance(raw, str):
            parts = re.split(r"[\n,，]", raw)
        elif isinstance(raw, list):
            parts = [str(item) for item in raw]
        else:
            raise ValueError("写下文件名里的词")
        words = []
        for part in parts:
            word = part.strip()
            if not word:
                continue
            if len(word) > 40 or "/" in word or "\\" in word:
                raise ValueError("一个词写文件名里的一段，40 字以内")
            if word not in words:
                words.append(word)
        if not words:
            raise ValueError("至少留一个词，不然什么都不收")
        if len(words) > 20:
            raise ValueError("最多 20 个词")
        current["name_keywords"] = words
    if "global_hotkey" in data:
        text = str(data.get("global_hotkey") or "").strip() or "Ctrl+Alt+Shift+K"
        parse_hotkey(text)
        current["global_hotkey"] = "+".join(part.strip() for part in text.split("+") if part.strip())
    for field, default in (("global_show", "Ctrl+Alt+J"), ("global_loose", "Ctrl+Alt+F")):
        if field in data:
            text = str(data.get(field) or "").strip() or default
            parse_hotkey(text)
            current[field] = "+".join(part.strip() for part in text.split("+") if part.strip())
    if "create_shortcuts" in data:
        rows = parse_shortcut_text(data.get("create_shortcuts"))
        if rows:
            current["create_shortcuts"] = rows
        else:
            current.pop("create_shortcuts", None)
    if "free_scope" in data:
        scope = str(data.get("free_scope") or "today").strip()
        if scope not in ("today", "open", "inbox"):
            raise ValueError("搜索范围不对")
        current["free_scope"] = scope
    if "app_keys" in data:
        current["app_keys"] = app_keys_from(data.get("app_keys"))
    if "record_store" in data:
        store = str(data.get("record_store") or "timebook").strip()
        if store not in ("timebook", "obsidian"):
            raise ValueError("记录去向不对")
        current["record_store"] = store
    if "colors" in data:
        current["colors"] = colors_from(data.get("colors"))
    if "marks" in data:
        current["marks"] = marks_from(data.get("marks"))
    if "vault" in data:
        raw = str(data.get("vault") or "").strip()
        if not raw:
            current["vault"] = ""
        else:
            folder = Path(raw)
            if not folder.is_dir():
                raise ValueError("这个文件夹不存在")
            current["vault"] = str(folder)
    if "browser" in data:
        text = str(data.get("browser") or "edge").strip() or "edge"
        if text not in ("edge", "chrome", "default") and Path(text).suffix.lower() != ".exe":
            raise ValueError("浏览器选 Edge、Chrome、系统默认，或填写 exe 路径")
        current["browser"] = text
    save_local_timebook(data)
    previous = read_settings()
    write_settings(current)
    if not HOTKEYS.restart():
        write_settings(previous)
        raise ValueError(HOTKEYS.fail_text or "这个快捷键被别的软件占用了")
    return {
        "ok": True,
        "name_keywords": name_keywords(),
        "global_hotkey": global_hotkey(),
        "global_show": show_hotkey(),
        "global_loose": loose_hotkey(),
        "create_shortcuts": create_shortcuts(),
        "colors": colors(),
        "marks": marks(),
        "record_store": record_store(),
        "app_keys": app_keys(),
        "vault": "" if vault_root() is None else str(vault_root()),
        "browser": browser_choice(),
    }


def cached_people() -> list[str]:
    try:
        cached = json.loads((HERE / "timebook-cache.json").read_text(encoding="utf-8"))
        people = (cached.get("options") or {}).get("peoples") or []
        return [str(name) for name in people if isinstance(name, str) and name]
    except (OSError, json.JSONDecodeError):
        return []


def free_scope() -> str:
    scope = str(read_settings().get("free_scope") or "today").strip()
    return scope if scope in ("today", "open", "inbox") else "today"


COLOR_DEFAULTS = {
    "highest": "#d23b34",
    "high": "#e06a00",
    "medium": "#b8860b",
    "low": "#2f6fad",
    "lowest": "#6b5b95",
}


def colors_from(raw) -> dict:
    picked = dict(COLOR_DEFAULTS)
    if isinstance(raw, dict):
        for key in COLOR_DEFAULTS:
            value = str(raw.get(key) or "").strip()
            if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                picked[key] = value.lower()
    return picked


def colors() -> dict:
    return colors_from(read_settings().get("colors"))


def clean_mark(value: str) -> str:
    name = re.sub(r"^[\#@+＃＠＋]+", "", str(value or "").strip())
    if not name or any(ch.isspace() for ch in name) or len(name) > 40:
        return ""
    return name


def marks_from(raw) -> dict:
    found = {"tags": [], "contexts": [], "projects": []}
    if not isinstance(raw, dict):
        return found
    for key in found:
        values = raw.get(key) or []
        if isinstance(values, str):
            values = re.split(r"[\n,，、]+", values)
        if not isinstance(values, list):
            continue
        for item in values:
            name = clean_mark(item)
            if name and name not in found[key]:
                found[key].append(name)
        found[key] = found[key][:80]
    return found


def marks() -> dict:
    return marks_from(read_settings().get("marks"))


def is_plan_name(name: str, words: list[str] | None = None) -> bool:
    return any(word in name for word in (words if words is not None else name_keywords()))


def resolve_md(rel: str) -> Path:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError("bad path")
    root = vault_root()
    if root is None:
        raise ValueError("先选择笔记文件夹")
    path = (root / rel).resolve()
    if path != root and root not in path.parents:
        raise ValueError("outside vault")
    if path.suffix.lower() != ".md" or not path.is_file():
        raise ValueError("not a note")
    if "TaskForge" in path.parts:
        raise ValueError("skipped")
    return path


def indent_of(prefix: str) -> int:
    return prefix.count("\t") + prefix.count(" ") // 2


def clean_rest(rest: str) -> str:
    rest = re.sub(r"[ \t]{2,}", " ", rest).rstrip()
    if rest and not rest.startswith(" "):
        rest = " " + rest
    return rest


def title_of(body: str) -> str:
    text = META_RE.sub(" ", body)
    text = TAG_RE.sub(" ", text)
    text = CTX_RE.sub(" ", text)
    text = PROJ_RE.sub(" ", text)
    return " ".join(text.split())


def field_dates(body: str) -> dict:
    found = {}
    for key, mark in (("scheduled", "⏳"), ("start", "🛫"), ("doneOn", "✅")):
        match = re.search(mark + r"\s*(\d{4}-\d{2}-\d{2})", body)
        found[key] = match.group(1) if match else None
    due = re.search(r"📅\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}))?", body)
    found["due"] = due.group(1) if due else None
    found["dueTime"] = due.group(2) if due and due.group(2) else None
    priority = PRIORITY_MARK.search(body)
    found["priority"] = EMOJI_PRIORITY.get(priority.group(0)) if priority else None
    found.update(repeat_info(body))
    found["people"] = PEOPLE_RE.findall(body)
    return found


def repeat_info(body: str) -> dict:
    found = REPEAT_RE.search(body)
    if not found:
        return {"repeat": "", "repeatSpec": ""}
    count = int(found.group(1) or 1)
    unit = found.group(2).lower()
    if unit.endswith("s"):
        unit = unit[:-1]
    on = (found.group(3) or "").lower()
    names = {"day": "天", "week": "周", "month": "月", "year": "年"}
    days = {"monday": "一", "tuesday": "二", "wednesday": "三", "thursday": "四", "friday": "五", "saturday": "六", "sunday": "日"}
    label = "每" + ("" if count == 1 else str(count)) + names.get(unit, unit)
    if unit == "week" and on in days:
        label += days[on]
    spec = ""
    if count == 1 and unit in ("day", "week", "month"):
        spec = f"week:{on}" if unit == "week" and on else unit
    return {"repeat": label, "repeatSpec": spec}


def weekday_on(today: date, which: str, day_char: str) -> date:
    weekday = WEEK_CHAR[day_char]
    monday = today - timedelta(days=today.weekday())
    if which == "这":
        return monday + timedelta(days=weekday)
    if which == "下":
        return monday + timedelta(days=7 + weekday)
    if which == "下下":
        return monday + timedelta(days=14 + weekday)
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def phrase_date(match: re.Match, today: date) -> date:
    if match.group(2):
        return today + timedelta(days={"今天": 0, "明天": 1, "后天": 2, "大后天": 3}[match.group(2)])
    if match.group(5):
        return weekday_on(today, match.group(3) or "", match.group(5))
    if match.group(6):
        return date.fromisoformat(match.group(6))
    month = int(match.group(8))
    day = int(match.group(9))
    year = int(match.group(7)) if match.group(7) else today.year
    found = date(year, month, day)
    if not match.group(7) and found < today:
        found = date(year + 1, month, day)
    return found


def parse_capture(text: str, today: date) -> dict:
    raw = (text or "").strip()
    tags = list(dict.fromkeys(TAG_RE.findall(raw)))
    contexts = list(dict.fromkeys(CTX_RE.findall(raw)))
    projects = list(dict.fromkeys(PROJ_RE.findall(raw)))
    raw = PROJ_RE.sub(" ", raw)
    raw = TAG_RE.sub(" ", raw)
    raw = CTX_RE.sub(" ", raw)
    found = {"due": None, "scheduled": None, "start": None, "dueTime": None}
    spans = []
    for match in DATE_PHRASE.finditer(raw):
        try:
            day = phrase_date(match, today).isoformat()
        except ValueError:
            continue
        role = {"排期": "scheduled", "开始": "start"}.get(match.group(1) or "", "due")
        found[role] = day
        if role == "due":
            found["dueTime"] = None
            if match.group(10):
                hour, minute = int(match.group(10)), int(match.group(11))
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    found["dueTime"] = f"{hour:02d}:{minute:02d}"
        spans.append((match.start(), match.end()))
    for start, end in reversed(spans):
        raw = raw[:start] + " " + raw[end:]
    return {
        "title": " ".join(raw.split()),
        "due": found["due"],
        "scheduled": found["scheduled"],
        "start": found["start"],
        "dueTime": found["dueTime"],
        "tags": tags,
        "contexts": contexts,
        "projects": projects,
    }


def build_task_line(captured: dict, today: date) -> str:
    parts = [f"- [ ] {captured['title']}"]
    if captured.get("priority"):
        parts.append(PRIORITY_EMOJI[captured["priority"]])
    parts.extend(f"#{tag}" for tag in captured["tags"])
    parts.extend(f"@{ctx}" for ctx in captured["contexts"])
    parts.extend(f"+{proj}" for proj in captured["projects"])
    parts.append(f"➕ {today.isoformat()}")
    if captured.get("start"):
        parts.append(f"🛫 {captured['start']}")
    if captured.get("scheduled"):
        parts.append(f"⏳ {captured['scheduled']}")
    if captured.get("due"):
        due = captured["due"]
        if captured.get("dueTime"):
            due += " " + captured["dueTime"]
        parts.append(f"📅 {due}")
    return " ".join(parts)


def read_note(path: Path) -> tuple[str, list[str], bool]:
    text = path.read_text(encoding="utf-8")
    return text, text.splitlines(), text.endswith("\n")


def write_note(path: Path, lines: list[str], ended: bool) -> None:
    body = "\n".join(lines)
    if ended:
        body += "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)


def finish(line: str) -> str:
    match = TASK_RE.match(line)
    if not match:
        return ID_RE.sub("", line)
    prefix, mark, rest = match.group(1), match.group(2), match.group(3)
    return f"{prefix}- [{mark}]{clean_rest(ID_RE.sub(' ', rest))}"


def completion_mode() -> str:
    root = vault_root()
    if root is None:
        return "keep"
    try:
        settings = json.loads((root / "TaskForge" / "Settings" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "keep"
    mode = settings.get("taskDefaults", {}).get("default_on_completion") or "keep"
    return mode if mode in ("keep", "archive", "delete") else "keep"


def shift_day(day: date, count: int, unit: str, weekday: int | None) -> date:
    unit = unit.lower()
    if unit.startswith("day"):
        return day + timedelta(days=count)
    if unit.startswith("week"):
        nxt = day + timedelta(weeks=count)
        if weekday is not None:
            monday = nxt - timedelta(days=nxt.weekday())
            nxt = monday + timedelta(days=weekday)
            if nxt <= day:
                nxt += timedelta(weeks=count)
        return nxt
    if unit.startswith("month"):
        month_index = day.month - 1 + count
        year = day.year + month_index // 12
        month = month_index % 12 + 1
        last = calendar.monthrange(year, month)[1]
        return date(year, month, min(day.day, last))
    try:
        return day.replace(year=day.year + count)
    except ValueError:
        return day.replace(year=day.year + count, month=2, day=28)


def next_occurrence(line: str, today: date) -> str:
    found = REPEAT_RE.search(line)
    if not found:
        raise ValueError("这行的循环写法认不出来，没有改")
    count = int(found.group(1) or 1)
    unit = found.group(2)
    weekday = WEEKDAY_NAME.get((found.group(3) or "").lower())
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    rest = DONE_RE.sub("", match.group(3))

    def replacer(token: re.Match) -> str:
        moved = shift_day(date.fromisoformat(token.group(2)), count, unit, weekday)
        return f"{token.group(1)} {moved.isoformat()}"

    updated = re.sub(r"(📅|⏳|🛫)\s*(\d{4}-\d{2}-\d{2})", replacer, rest)
    if not re.search(r"📅|⏳|🛫", updated):
        updated = updated.rstrip() + f" 📅 {shift_day(today, count, unit, weekday).isoformat()}"
    return finish(f"{match.group(1)}- [ ]{clean_rest(updated)}")


def set_status_line(line: str, mark: str, today: str) -> str:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    prefix, rest = match.group(1), match.group(3)
    rest = DONE_RE.sub("", rest)
    rest = CANCEL_RE.sub("", rest)
    if mark.lower() == "x":
        rest = rest.rstrip() + f" ✅ {today}"
    elif mark == "-":
        rest = rest.rstrip() + f" ❌ {today}"
    return finish(f"{prefix}- [{mark}]{clean_rest(rest)}")


def mark_done(line: str, today: str) -> tuple[str, str | None]:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    if match.group(2).lower() == "x":
        return finish(line), None
    if "🔁" in line and not REPEAT_RE.search(line):
        raise ValueError("这行的循环写法认不出来，没有改")
    done = set_status_line(line, "x", today)
    nxt = next_occurrence(line, date.fromisoformat(today)) if REPEAT_RE.search(line) else None
    return done, nxt


def flip_line(line: str, today: str) -> tuple[str, str | None]:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    if match.group(2).lower() == "x":
        return set_status_line(line, " ", today), None
    return mark_done(line, today)


def set_field_date(line: str, emoji: str, pattern: re.Pattern, day: str) -> str:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    prefix, mark, rest = match.group(1), match.group(2), match.group(3)
    rest = pattern.sub("", rest)
    if day:
        bits = str(day).split()
        try:
            date.fromisoformat(bits[0])
        except ValueError:
            raise ValueError("日期格式不对") from None
        if len(bits) > 1 and not re.fullmatch(r"\d{2}:\d{2}", bits[1]):
            raise ValueError("时间不对")
        token = f"{emoji} {' '.join(bits[:2])}"
        if "✅" in rest:
            rest = rest.replace("✅", f"{token} ✅", 1)
        elif "❌" in rest:
            rest = rest.replace("❌", f"{token} ❌", 1)
        else:
            rest = rest.rstrip() + " " + token
    return finish(f"{prefix}- [{mark}]{clean_rest(rest)}")


def set_due_line(line: str, due: str) -> str:
    return set_field_date(line, "📅", DUE_TOKEN, due)


def set_people_line(line: str, names: list[str]) -> str:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    prefix, mark, rest = match.group(1), match.group(2), match.group(3)
    rest = PEOPLE_RE.sub("", rest)
    for name in names:
        rest = rest.rstrip() + " 👤" + name
    return finish(f"{prefix}- [{mark}]{clean_rest(rest)}")


def set_repeat_line(line: str, spec: str) -> str:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    prefix, mark, rest = match.group(1), match.group(2), match.group(3)
    rest = REPEAT_RE.sub("", rest)
    spec = (spec or "").strip()
    if spec:
        if spec == "day":
            token = "🔁 every day"
        elif spec == "week":
            token = "🔁 every week"
        elif spec == "month":
            token = "🔁 every month"
        elif spec.startswith("week:"):
            day_name = spec.split(":", 1)[1]
            if day_name not in WEEKDAY_NAME:
                raise ValueError("不认识的循环")
            token = f"🔁 every week on {day_name}"
        else:
            raise ValueError("不认识的循环")
        rest = rest.rstrip() + " " + token
    return finish(f"{prefix}- [{mark}]{clean_rest(rest)}")


def ensure_token(rest: str, token: str) -> str:
    if re.search(r"(?<!\S)" + re.escape(token) + r"(?!\S)", rest):
        return rest
    for mark in ("🆔", "📅", "⏳", "🛫", "✅", "➕"):
        index = rest.find(mark)
        if index >= 0:
            return rest[:index] + token + " " + rest[index:]
    return rest.rstrip() + " " + token


def apply_line(line: str, captured: dict) -> str:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    prefix, mark, rest = match.group(1), match.group(2), match.group(3)
    for tag in captured["tags"]:
        rest = ensure_token(rest, "#" + tag)
    for ctx in captured["contexts"]:
        rest = ensure_token(rest, "@" + ctx)
    for proj in captured["projects"]:
        rest = ensure_token(rest, "+" + proj)
    line = finish(f"{prefix}- [{mark}]{clean_rest(rest)}")
    if captured.get("start"):
        line = set_field_date(line, "🛫", START_TOKEN, captured["start"])
    if captured.get("scheduled"):
        line = set_field_date(line, "⏳", SCHED_TOKEN, captured["scheduled"])
    if captured.get("due"):
        line = set_field_date(line, "📅", DUE_TOKEN, captured["due"])
    return line


def task_rows(lines: list[str], open_only: bool) -> list[dict]:
    rows = []
    for number, line in enumerate(lines, start=1):
        match = TASK_RE.match(line)
        if not match:
            continue
        title = title_of(match.group(3))
        if not title:
            continue
        mark = match.group(2)
        done = mark.lower() == "x"
        cancelled = mark == "-"
        if open_only and (done or cancelled):
            continue
        body = match.group(3)
        rows.append(
            {
                "line": number,
                "indent": indent_of(match.group(1)),
                "done": done,
                "cancelled": cancelled,
                "status": STATUS_LABEL.get(mark, mark),
                "title": title,
                "raw": line,
                "tags": TAG_RE.findall(body),
                "contexts": CTX_RE.findall(body),
                "projects": PROJ_RE.findall(body),
                **field_dates(body),
            }
        )
    return rows


def note_span(lines: list[str], index: int) -> tuple[int, int]:
    end = index + 1
    while end < len(lines):
        if not lines[end].strip():
            end += 1
            continue
        if TASK_RE.match(lines[end]):
            break
        end += 1
    while end > index + 1 and not lines[end - 1].strip():
        end -= 1
    return index + 1, end


def attach_notes(lines: list[str], rows: list[dict]) -> None:
    for row in rows:
        index = row["line"] - 1
        match = TASK_RE.match(lines[index]) if 0 <= index < len(lines) else None
        if not match:
            row["note"] = ""
            continue
        start, end = note_span(lines, index)
        prefix = match.group(1) + "\t"
        chunks = []
        for line in lines[start:end]:
            chunks.append(line[len(prefix):] if line.startswith(prefix) else line.lstrip(" \t"))
        row["note"] = "\n".join(chunks).strip("\n")


def set_note(rel: str, line_no: int, expected: str, text: str) -> dict:
    path = resolve_md(rel)
    _, lines, ended = read_note(path)
    index = line_no - 1
    if index < 0 or index >= len(lines) or lines[index] != expected:
        raise ValueError("这行已经变了，请刷新")
    match = TASK_RE.match(lines[index])
    if not match:
        raise ValueError("not a task")
    body = (text or "").replace("\r\n", "\n").strip("\n")
    if len(body) > 4000:
        raise ValueError("备注太长")
    start, end = note_span(lines, index)
    prefix = match.group(1) + "\t"
    fresh = [prefix + line for line in body.split("\n")] if body else []
    lines[start:end] = fresh
    write_note(path, lines, ended)
    return {"ok": True}


def collect() -> dict:
    rev = vault_rev()
    words = name_keywords()
    notes = []
    for path in note_files():
        if not path.is_file() or "TaskForge" in path.parts:
            continue
        if not is_plan_name(path.name, words):
            continue
        rel = path.relative_to(vault_root()).as_posix()
        try:
            _, lines, _ = read_note(path)
        except OSError:
            continue
        rows = task_rows(lines, open_only=False)
        attach_notes(lines, rows)
        notes.append((path.name, rel, lines, rows))

    plans = [item for item in notes if is_plan_name(item[0])]
    plans.sort(key=lambda item: file_sort_key(item[0]), reverse=True)
    sections = []
    primary = plans[0][1] if plans else None
    for name, rel, _lines, rows in plans:
        open_rows = [row for row in rows if not row["done"] and not row["cancelled"]]
        if rel != primary and not rows:
            continue
        sections.append(
            {
                "file": rel,
                "name": name,
                "open": len(open_rows),
                "primary": rel == primary,
                "tasks": rows,
            }
        )

    other = []
    for name, rel, _, rows in notes:
        if is_plan_name(name):
            continue
        open_rows = [row for row in rows if not row["done"] and not row["cancelled"]]
        if not open_rows:
            continue
        other.append(
            {
                "file": rel,
                "name": name,
                "open": len(open_rows),
                "primary": False,
                "tasks": open_rows,
            }
        )
    other.sort(key=lambda item: (-item["open"], item["name"]))
    root = vault_root()
    return {
        "root": "" if root is None else str(root),
        "vault_ok": bool(root and root.is_dir()),
        "browser": browser_choice(),
        "today": date.today().isoformat(),
        "primary": primary,
        "plans": sections,
        "other": other,
        "name_keywords": words,
        "global_hotkey": global_hotkey(),
        "global_show": show_hotkey(),
        "global_loose": loose_hotkey(),
        "create_shortcuts": create_shortcuts(),
        "free_scope": free_scope(),
        "colors": colors(),
        "marks": marks(),
        "record_store": record_store(),
        "app_keys": app_keys(),
        "peoples": cached_people(),
        **timebook_public(),
        **deepseek_public(),
        "rev": rev,
    }


def load_groups(items: list) -> tuple[dict[str, list], dict]:
    if not items:
        raise ValueError("先选中一行")
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(item.get("file") or "", []).append(item)
    loaded = {}
    for rel, group in grouped.items():
        path = resolve_md(rel)
        _, lines, ended = read_note(path)
        loaded[rel] = [path, lines, ended]
        for item in group:
            index = int(item.get("line") or 0) - 1
            if index < 0 or index >= len(lines) or lines[index] != (item.get("raw") or ""):
                raise ValueError("这行已经变了，请刷新")
    return grouped, loaded


def save_loaded(loaded: dict) -> None:
    for path, lines, ended in loaded.values():
        write_note(path, lines, ended)


def place_completion(lines: list[str], index: int, done: str, nxt: str | None, archived: list[str]) -> None:
    mode = completion_mode()
    opening = TASK_RE.match(done) and TASK_RE.match(done).group(2) == " "
    if opening or mode == "keep":
        lines[index] = done
        if nxt:
            lines.insert(index + 1, nxt)
        return
    if nxt:
        lines[index] = nxt
    else:
        lines.pop(index)
    if mode == "archive":
        archived.append(done)


def task_span(lines: list[str], index: int) -> tuple[int, int]:
    """The task line plus the note under it, stopping before the next task."""
    return index, note_span(lines, index)[1]


def append_archive(rows: list[str], source_rel: str) -> None:
    rows = [line for line in rows if line.strip()]
    if not rows:
        return
    archive = archive_file()
    if archive.exists():
        text = archive.read_text(encoding="utf-8")
        existing = text.splitlines()
    else:
        existing = []
    header = f"## From: {vault_root().name}/{source_rel}"
    if header not in existing:
        if existing and existing[-1] != "":
            existing.append("")
        existing.extend([header, ""])
        insert_at = len(existing)
    else:
        insert_at = existing.index(header) + 1
        while insert_at < len(existing) and not existing[insert_at].startswith("## "):
            insert_at += 1
    existing[insert_at:insert_at] = rows
    write_note(archive, existing, True)


def drop_token(rest: str, token: str) -> str:
    return re.sub(r"(?<!\S)" + re.escape(token) + r"(?!\S)", " ", rest)


def rename_line(line: str, title: str) -> str:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    rest = match.group(3)
    cut = len(rest)
    for pattern in (
        TAG_RE, CTX_RE, PROJ_RE, DUE_TOKEN, SCHED_TOKEN, START_TOKEN,
        DONE_RE, CANCEL_RE, CREATED_TOKEN, ID_RE, PRIORITY_MARK, REPEAT_RE,
    ):
        found = pattern.search(rest)
        if found:
            cut = min(cut, found.start())
    suffix = rest[cut:].strip()
    body = f" {title}" + (f" {suffix}" if suffix else "")
    return finish(f"{match.group(1)}- [{match.group(2)}]{body}")


def set_priority_line(line: str, name: str) -> str:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    rest = PRIORITY_MARK.sub("", match.group(3))
    emoji = PRIORITY_EMOJI.get(name)
    if emoji:
        if "📅" in rest:
            rest = rest.replace("📅", f"{emoji} 📅", 1)
        else:
            rest = rest.rstrip() + " " + emoji
    return finish(f"{match.group(1)}- [{match.group(2)}]{clean_rest(rest)}")


def shift_indent(line: str, delta: int) -> str:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    prefix = match.group(1)
    if delta < 0:
        if prefix.startswith("\t"):
            prefix = prefix[1:]
        elif prefix.startswith("  "):
            prefix = prefix[2:]
        elif prefix.startswith(" "):
            prefix = prefix[1:]
    elif delta > 0:
        prefix = "\t" + prefix
    return finish(prefix + line[len(match.group(1)):])


def clear_line(line: str, what: str) -> str:
    if what in ("due", "dates"):
        line = set_field_date(line, "📅", DUE_TOKEN, "")
    if what in ("scheduled", "dates"):
        line = set_field_date(line, "⏳", SCHED_TOKEN, "")
    if what in ("start", "dates"):
        line = set_field_date(line, "🛫", START_TOKEN, "")
    if what == "priority":
        line = set_priority_line(line, "")
    return line


def untag_line(line: str, command: dict) -> str:
    match = TASK_RE.match(line)
    if not match:
        raise ValueError("not a task")
    rest = match.group(3)
    for tag in command["tags"]:
        rest = drop_token(rest, "#" + tag)
    for ctx in command["contexts"]:
        rest = drop_token(rest, "@" + ctx)
    for proj in command["projects"]:
        rest = drop_token(rest, "+" + proj)
    return finish(f"{match.group(1)}- [{match.group(2)}]{clean_rest(rest)}")


def parse_command(text: str) -> dict | None:
    raw = (text or "").strip()
    if raw == "删":
        return {"op": "delete"}
    if raw == "归档":
        return {"op": "archive"}
    if raw == "上移":
        return {"op": "indent", "delta": -1}
    if raw == "下移":
        return {"op": "indent", "delta": 1}
    if raw == "清截止":
        return {"op": "clear", "what": "due"}
    if raw == "清排期":
        return {"op": "clear", "what": "scheduled"}
    if raw == "清开始":
        return {"op": "clear", "what": "start"}
    if raw == "清日期":
        return {"op": "clear", "what": "dates"}
    if raw == "清优先级":
        return {"op": "clear", "what": "priority"}
    if raw in PRIORITY_WORD:
        return {"op": "priority", "value": PRIORITY_WORD[raw]}
    if raw in STATUS_WORD:
        return {"op": "status", "mark": STATUS_WORD[raw]}
    if raw.startswith("改 "):
        title = raw[2:].strip()
        if not title or len(title) > 200:
            raise ValueError("改后面写上新标题，200 字以内")
        return {"op": "rename", "title": title}
    if raw.startswith("移到"):
        target = raw[2:].strip()
        if not target:
            raise ValueError("移到后面写规划文件名里的一段，例如 1019")
        return {"op": "move", "target": target}
    removals = list(REMOVE_RE.finditer(raw))
    if removals and not REMOVE_RE.sub(" ", raw).strip():
        tags, contexts, projects = [], [], []
        for found in removals:
            kind, value = found.group(1), found.group(2)
            if kind == "#":
                tags.append(value)
            elif kind == "@":
                contexts.append(value)
            else:
                projects.append(value)
        return {"op": "untag", "tags": tags, "contexts": contexts, "projects": projects}
    return None


def plan_target(needle: str) -> str:
    matches = []
    for path in note_files():
        if not path.is_file() or "TaskForge" in path.parts or not is_plan_name(path.name):
            continue
        rel = path.relative_to(vault_root()).as_posix()
        if needle in path.stem or needle in rel:
            matches.append(rel)
    if len(matches) != 1:
        raise ValueError("移到哪一篇规划？写文件名里唯一的一段，例如 1019")
    return matches[0]


def toggle(rel: str, line_no: int, expected: str) -> dict:
    return apply_bulk([{"file": rel, "line": line_no, "raw": expected}], "toggle", "")


def line_level(line: str) -> tuple[int, str]:
    index = 0
    level = 0
    while index < len(line):
        if line[index] == "\t":
            level += 1
            index += 1
        elif line[index:index + 2] == "  ":
            level += 1
            index += 2
        else:
            break
    return level, line[index:].strip()


def next_weekday(today: date, weekday: int) -> date:
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta or 7)


SHORTCUT_MEANINGS = {
    "今天": ("day", 0),
    "明天": ("day", 1),
    "后天": ("day", 2),
    "大后天": ("day", 3),
    "下周一": ("week", 0),
    "下周二": ("week", 1),
    "下周三": ("week", 2),
    "下周四": ("week", 3),
    "下周五": ("week", 4),
    "下周六": ("week", 5),
    "下周日": ("week", 6),
    "最高": ("priority", "highest"),
    "高": ("priority", "high"),
    "中": ("priority", "medium"),
    "低": ("priority", "low"),
    "最低": ("priority", "lowest"),
    "无": ("priority", ""),
}
DEFAULT_SHORTCUTS = [
    {"key": "t", "label": "今天"},
    {"key": "tt", "label": "明天"},
    {"key": "ttt", "label": "后天"},
    {"key": "w1", "label": "下周一"},
    {"key": "w2", "label": "下周二"},
    {"key": "w3", "label": "下周三"},
    {"key": "w4", "label": "下周四"},
    {"key": "w5", "label": "下周五"},
    {"key": "w6", "label": "下周六"},
    {"key": "w7", "label": "下周日"},
    {"key": "1", "label": "最高"},
    {"key": "2", "label": "高"},
    {"key": "3", "label": "中"},
    {"key": "4", "label": "低"},
    {"key": "5", "label": "最低"},
    {"key": "0", "label": "无"},
]


def parse_shortcut_text(text: str) -> list[dict]:
    rows = []
    seen = set()
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError("对照写成 t=今天")
        key, label = line.split("=", 1)
        key = key.strip().lower()
        label = label.strip()
        if not key or len(key) > 12 or any(ch.isspace() for ch in key):
            raise ValueError("左边写一串不带空格的记号")
        if label not in SHORTCUT_MEANINGS:
            raise ValueError("右边只能写今天、明天、后天、大后天、下周一到下周日、最高、高、中、低、最低、无")
        if key in seen:
            raise ValueError("记号重复了：" + key)
        seen.add(key)
        rows.append({"key": key, "label": label})
    if len(rows) > 40:
        raise ValueError("对照最多 40 行")
    return rows


def create_shortcuts() -> list[dict]:
    raw = read_settings().get("create_shortcuts")
    if not isinstance(raw, list) or not raw:
        return [dict(item) for item in DEFAULT_SHORTCUTS]
    rows = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        label = str(item.get("label") or "").strip()
        if not key or label not in SHORTCUT_MEANINGS or key in seen:
            continue
        seen.add(key)
        rows.append({"key": key, "label": label})
    return rows or [dict(item) for item in DEFAULT_SHORTCUTS]


def peel_quick(body: str, today: date) -> tuple[str, dict]:
    parts = body.split()
    actions = {}
    for item in create_shortcuts():
        actions[item["key"]] = SHORTCUT_MEANINGS[item["label"]]
    actions.setdefault("m", SHORTCUT_MEANINGS["明天"])
    actions.setdefault("w", SHORTCUT_MEANINGS["下周一"])
    extra = {}
    kept_marks = []
    while parts:
        token = parts[-1]
        if len(token) > 1 and token[0] in "#＃@＠+＋":
            kept_marks.append(parts.pop())
            continue
        action = actions.get(token.lower())
        if not action:
            break
        parts.pop()
        kind, value = action
        if kind == "day" and "due" not in extra:
            extra["due"] = (today + timedelta(days=value)).isoformat()
        elif kind == "week" and "due" not in extra:
            extra["due"] = next_weekday(today, value).isoformat()
        elif kind == "priority" and "priority" not in extra:
            extra["priority"] = value
    if kept_marks:
        parts.extend(reversed(kept_marks))
    return " ".join(parts), extra


def outline_items(text: str, today: date) -> list[tuple[int, dict]]:
    rows = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        level, body = line_level(line)
        body, quick = peel_quick(body, today)
        if parse_command(body):
            raise ValueError("先选中一行")
        captured = parse_capture(body, today)
        if "due" in quick:
            captured["due"] = quick["due"] or None
        if quick.get("priority"):
            captured["priority"] = quick["priority"]
        if not captured["title"] or len(captured["title"]) > 200:
            raise ValueError("前面写上要做的事，200 字以内")
        rows.append([level, captured])
    if not rows:
        raise ValueError("前面写上要做的事，200 字以内")
    base = min(level for level, _captured in rows)
    previous = 0
    fixed = []
    for level, captured in rows:
        level = max(0, level - base)
        if fixed and level > previous + 1:
            level = previous + 1
        fixed.append((level, captured))
        previous = level
    return fixed


def task_lines_for(items: list[tuple[int, dict]], base_prefix: str, today: date) -> list[str]:
    return [base_prefix + ("\t" * level) + build_task_line(captured, today) for level, captured in items]


def add_task(rel: str, text: str) -> dict:
    today = date.today()
    items = outline_items(text, today)
    path = resolve_md(rel)
    _, lines, _ended = read_note(path)
    start = len(lines) + 1
    lines.extend(task_lines_for(items, "", today))
    write_note(path, lines, True)
    return {"ok": True, "line": start, "count": len(items)}


def apply_bulk(items: list, op: str, due: str) -> dict:
    grouped, loaded = load_groups(items)
    today = date.today().isoformat()
    count = 0
    date_ops = {"due": ("📅", DUE_TOKEN), "scheduled": ("⏳", SCHED_TOKEN), "start": ("🛫", START_TOKEN)}
    for rel, group in grouped.items():
        _path, lines, _ended = loaded[rel]
        archived = []
        for item in sorted(group, key=lambda row: int(row.get("line") or 0), reverse=True):
            index = int(item.get("line") or 0) - 1
            if op == "toggle":
                done, nxt = flip_line(lines[index], today)
                place_completion(lines, index, done, nxt, archived)
            elif op in date_ops:
                emoji, pattern = date_ops[op]
                lines[index] = set_field_date(lines[index], emoji, pattern, (due or "").strip())
            else:
                raise ValueError("不认识的操作")
            count += 1
        append_archive(archived, rel)
    save_loaded(loaded)
    return {"ok": True, "count": count}


def insert_under(rel: str, line_no: int, expected: str, text: str) -> dict:
    today = date.today()
    items = outline_items(text, today)
    path = resolve_md(rel)
    _, lines, ended = read_note(path)
    index = line_no - 1
    if index < 0 or index >= len(lines) or lines[index] != expected:
        raise ValueError("这行已经变了，请刷新")
    match = TASK_RE.match(lines[index])
    if not match:
        raise ValueError("not a task")
    fresh = task_lines_for(items, match.group(1) + "\t", today)
    _start, end = note_span(lines, index)
    lines[end:end] = fresh
    write_note(path, lines, ended)
    return {"ok": True, "line": end + 1, "count": len(fresh)}


def reorder_task(rel: str, line_no: int, expected: str, delta: int) -> dict:
    path = resolve_md(rel)
    _, lines, ended = read_note(path)
    index = line_no - 1
    if index < 0 or index >= len(lines) or lines[index] != expected:
        raise ValueError("这行已经变了，请刷新")
    match = TASK_RE.match(lines[index])
    if not match:
        raise ValueError("not a task")
    indent = indent_of(match.group(1))

    def outline_end(start: int) -> int:
        end = start + 1
        while end < len(lines):
            found = TASK_RE.match(lines[end])
            if found and indent_of(found.group(1)) <= indent:
                break
            end += 1
        return end

    if delta < 0:
        other = None
        cursor = index - 1
        while cursor >= 0:
            found = TASK_RE.match(lines[cursor])
            if found:
                level = indent_of(found.group(1))
                if level < indent:
                    break
                if level == indent:
                    other = cursor
                    break
            cursor -= 1
        if other is None:
            raise ValueError("没有上一个")
        first, second = other, index
    else:
        end = outline_end(index)
        found = TASK_RE.match(lines[end]) if end < len(lines) else None
        if not found or indent_of(found.group(1)) != indent:
            raise ValueError("没有下一个")
        first, second = index, end
    first_end = outline_end(first)
    second_end = outline_end(second)
    block_first = lines[first:first_end]
    block_second = lines[second:second_end]
    lines[first:second_end] = block_second + block_first
    new_index = first if delta < 0 else first + len(block_second)
    write_note(path, lines, ended)
    return {"ok": True, "line": new_index + 1}


def apply_command(items: list, command: dict) -> dict:
    if command["op"] == "move":
        return move_items(items, command["target"])
    grouped, loaded = load_groups(items)
    today = date.today()
    today_s = today.isoformat()
    count = 0
    for rel, group in grouped.items():
        _path, lines, _ended = loaded[rel]
        archived_blocks = []
        completed = []
        for item in sorted(group, key=lambda row: int(row.get("line") or 0), reverse=True):
            index = int(item.get("line") or 0) - 1
            op = command["op"]
            if op == "delete":
                _start, end = task_span(lines, index)
                del lines[index:end]
            elif op == "archive":
                _start, end = task_span(lines, index)
                archived_blocks.append(lines[index:end])
                del lines[index:end]
            elif op == "indent":
                lines[index] = shift_indent(lines[index], command["delta"])
            elif op == "clear":
                lines[index] = clear_line(lines[index], command["what"])
            elif op == "priority":
                lines[index] = set_priority_line(lines[index], command["value"])
            elif op == "rename":
                lines[index] = rename_line(lines[index], command["title"])
            elif op == "untag":
                lines[index] = untag_line(lines[index], command)
            elif op == "status" and command["mark"].lower() == "x":
                done, nxt = mark_done(lines[index], today_s)
                place_completion(lines, index, done, nxt, completed)
            elif op == "status":
                lines[index] = set_status_line(lines[index], command["mark"], today_s)
            else:
                raise ValueError("不认识的操作")
            count += 1
        archived = list(completed)
        for block in reversed(archived_blocks):
            archived.extend(block)
        append_archive(archived, rel)
    save_loaded(loaded)
    return {"applied": True, "count": count}


def move_items(items: list, needle: str) -> dict:
    target = plan_target(needle)
    grouped, loaded = load_groups(items)
    if target in grouped and len(grouped) == 1:
        raise ValueError("已经在这篇里")
    if target not in loaded:
        path = resolve_md(target)
        _, lines, ended = read_note(path)
        loaded[target] = [path, lines, ended]
    moving = []
    for rel, group in grouped.items():
        if rel == target:
            continue
        lines = loaded[rel][1]
        indexes = sorted(int(item.get("line") or 0) - 1 for item in group)
        blocks = []
        for index in reversed(indexes):
            if index < 0 or index >= len(lines) or not TASK_RE.match(lines[index]):
                continue
            _start, end = task_span(lines, index)
            blocks.append(lines[index:end])
            del lines[index:end]
        for block in reversed(blocks):
            moving.extend(block)
    loaded[target][1].extend(moving)
    save_loaded(loaded)
    return {"applied": True, "count": len(moving)}


def apply_text(items: list, text: str) -> dict:
    command = parse_command(text)
    if command:
        return apply_command(items, command)
    captured = parse_capture(text, date.today())
    extra = parse_command(captured["title"]) if captured["title"] else None
    has_fields = bool(
        captured["due"]
        or captured["scheduled"]
        or captured["start"]
        or captured["tags"]
        or captured["contexts"]
        or captured["projects"]
    )
    if extra and extra["op"] in ("priority", "status") and has_fields:
        grouped, loaded = load_groups(items)
        today_s = date.today().isoformat()
        count = 0
        for rel, group in grouped.items():
            _path, lines, _ended = loaded[rel]
            archived = []
            for item in sorted(group, key=lambda row: int(row.get("line") or 0), reverse=True):
                index = int(item.get("line") or 0) - 1
                lines[index] = apply_line(lines[index], captured)
                if extra["op"] == "priority":
                    lines[index] = set_priority_line(lines[index], extra["value"])
                elif extra["mark"].lower() == "x":
                    done, nxt = mark_done(lines[index], today_s)
                    place_completion(lines, index, done, nxt, archived)
                else:
                    lines[index] = set_status_line(lines[index], extra["mark"], today_s)
                count += 1
            append_archive(archived, rel)
        save_loaded(loaded)
        return {"applied": True, "count": count}
    if extra and not has_fields:
        return apply_command(items, extra)
    if captured["title"]:
        return {"applied": False}
    if not (
        captured["due"]
        or captured["scheduled"]
        or captured["start"]
        or captured["tags"]
        or captured["contexts"]
        or captured["projects"]
    ):
        raise ValueError("写上要做的事，或写 下周一、#标签、@场景、+项目、改 标题、删")
    if not items:
        raise ValueError("先选中一行")
    grouped, loaded = load_groups(items)
    count = 0
    for rel, group in grouped.items():
        _path, lines, _ended = loaded[rel]
        for item in group:
            index = int(item.get("line") or 0) - 1
            lines[index] = apply_line(lines[index], captured)
            count += 1
    save_loaded(loaded)
    return {"applied": True, "count": count}


def ensure_task_line(line: str) -> str:
    line = line.replace("\r", "")
    if TASK_RE.match(line):
        return line
    indent = re.match(r"^[\t ]*", line).group(0)
    text = line.strip()
    if not text:
        raise ValueError("空行先删掉")
    return f"{indent}- [ ] {text}"


def apply_outline(lines: list[str], indexed: list[dict], new_lines: list[str]) -> list[str]:
    from difflib import SequenceMatcher

    lines = list(lines)
    old = [item["raw"] for item in indexed]
    ops = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=old, b=new_lines, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            for offset in range(i1, i2):
                ops.append((indexed[offset]["index"], "delete", None))
        elif tag == "insert":
            anchor = indexed[i1 - 1]["index"] if i1 else -1
            ops.append((anchor, "insert", new_lines[j1:j2]))
        else:
            coupled = min(i2 - i1, j2 - j1)
            for offset in range(coupled):
                ops.append((indexed[i1 + offset]["index"], "set", new_lines[j1 + offset]))
            if j2 - j1 > coupled:
                anchor = indexed[i1 + coupled - 1]["index"] if coupled else (indexed[i1 - 1]["index"] if i1 else -1)
                ops.append((anchor, "insert", new_lines[j1 + coupled:j2]))
            elif i2 - i1 > coupled:
                for offset in range(coupled, i2 - i1):
                    ops.append((indexed[i1 + offset]["index"], "delete", None))
    kind_order = {"delete": 2, "set": 1, "insert": 0}
    for index, kind, payload in sorted(ops, key=lambda op: (op[0], kind_order[op[1]]), reverse=True):
        if kind == "set":
            lines[index] = payload
        elif kind == "delete":
            _start, end = note_span(lines, index)
            del lines[index:end]
        else:
            at = indexed[0]["index"] if index < 0 and indexed else (0 if index < 0 else note_span(lines, index)[1])
            lines[at:at] = payload
    return lines


def replace_page(items: list, new_lines: list[str] | None = None) -> dict:
    if not items:
        raise ValueError("这一页没有要改的")
    if new_lines is None:
        raise ValueError("没有要写的内容")
    cleaned = []
    for line in new_lines:
        if not str(line).strip():
            continue
        cleaned.append(ensure_task_line(str(line)))
    grouped, loaded = load_groups(items)
    ordered = []
    for item in items:
        rel = item.get("file") or ""
        _path, lines, _ended = loaded[rel]
        index = int(item.get("line") or 0) - 1
        ordered.append({"file": rel, "index": index, "raw": lines[index]})
    by_file: dict[str, list[dict]] = {}
    for item in ordered:
        by_file.setdefault(item["file"], []).append(item)
    from difflib import SequenceMatcher

    old = [item["raw"] for item in ordered]
    assigned: dict[str, list[str]] = {rel: [] for rel in by_file}
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=old, b=cleaned, autojunk=False).get_opcodes():
        if tag == "equal":
            for item, line in zip(ordered[i1:i2], cleaned[j1:j2]):
                assigned[item["file"]].append(line)
            continue
        if tag == "delete":
            continue
        if tag == "insert":
            rel = ordered[i1 - 1]["file"] if i1 else ordered[0]["file"]
            assigned[rel].extend(cleaned[j1:j2])
            continue
        rels = []
        for item in ordered[i1:i2]:
            if item["file"] not in rels:
                rels.append(item["file"])
        if len(rels) > 1:
            raise ValueError("这一页跨了几篇笔记，先在同一篇里改")
        rel = rels[0] if rels else (ordered[i1 - 1]["file"] if i1 else ordered[0]["file"])
        assigned[rel].extend(cleaned[j1:j2])
    count = 0
    for rel, indexed in by_file.items():
        _path, lines, _ended = loaded[rel]
        updated = apply_outline(lines, indexed, assigned.get(rel, []))
        if updated != lines:
            loaded[rel][1] = updated
            count += 1
    save_loaded(loaded)
    return {"ok": True, "count": count}


def apply_form(items: list, fields: dict) -> dict:
    grouped, loaded = load_groups(items)
    today_s = date.today().isoformat()
    single = sum(len(group) for group in grouped.values()) == 1
    count = 0
    for rel, group in grouped.items():
        _path, lines, _ended = loaded[rel]
        archived = []
        for item in sorted(group, key=lambda row: int(row.get("line") or 0), reverse=True):
            index = int(item.get("line") or 0) - 1
            line = lines[index]
            title = (fields.get("title") or "").strip() if single else ""
            if title:
                if len(title) > 200:
                    raise ValueError("标题 200 字以内")
                line = rename_line(line, title)
            for key, emoji, pattern in (
                ("due", "📅", DUE_TOKEN),
                ("scheduled", "⏳", SCHED_TOKEN),
                ("start", "🛫", START_TOKEN),
            ):
                if key in fields and fields[key] is not None:
                    line = set_field_date(line, emoji, pattern, str(fields[key]))
            if "priority" in fields and fields["priority"] is not None:
                line = set_priority_line(line, fields["priority"] or "")
            if "people" in fields and fields["people"] is not None:
                names = []
                for name in fields["people"]:
                    name = str(name).strip()
                    if not name:
                        continue
                    if len(name) > 20 or any(ch.isspace() for ch in name):
                        raise ValueError("人物写成一个名字")
                    if name not in names:
                        names.append(name)
                if len(names) > 8:
                    raise ValueError("一条最多 8 个人物")
                line = set_people_line(line, names)
            if "repeat" in fields:
                line = set_repeat_line(line, str(fields.get("repeat") or ""))
            remove = fields.get("remove") or {}
            if any(remove.get(name) for name in ("tags", "contexts", "projects")):
                line = untag_line(line, {
                    "tags": remove.get("tags") or [],
                    "contexts": remove.get("contexts") or [],
                    "projects": remove.get("projects") or [],
                })
            extra = fields.get("add") or {}
            added = {"tags": [], "contexts": [], "projects": []}
            for key in added:
                for item in extra.get(key) or []:
                    text = str(item or "").strip()
                    if not text:
                        continue
                    name = clean_mark(text)
                    if not name:
                        raise ValueError("标记写成一个词，40 字以内")
                    if name not in added[key]:
                        added[key].append(name)
            if any(added.values()):
                line = apply_line(line, {
                    "title": "",
                    "due": None,
                    "scheduled": None,
                    "start": None,
                    **added,
                })
            status = fields.get("status") or ""
            if status:
                mark = STATUS_WORD.get(status)
                if mark is None:
                    raise ValueError("不认识的状态")
                if mark.lower() == "x":
                    lines[index] = line
                    done, nxt = mark_done(lines[index], today_s)
                    place_completion(lines, index, done, nxt, archived)
                    count += 1
                    continue
                line = set_status_line(line, mark, today_s)
            lines[index] = line
            count += 1
        append_archive(archived, rel)
    save_loaded(loaded)
    return {"ok": True, "count": count}


def set_due(rel: str, line_no: int, expected: str, due: str) -> dict:
    path = resolve_md(rel)
    _, lines, ended = read_note(path)
    index = line_no - 1
    if index < 0 or index >= len(lines) or lines[index] != expected:
        raise ValueError("这行已经变了，请刷新")
    lines[index] = set_due_line(lines[index], (due or "").strip())
    write_note(path, lines, ended)
    return {"ok": True, "raw": lines[index]}


def open_timebook(url: str = "", token: str = ""):
    import urllib.error
    import urllib.request

    saved = read_local_timebook()
    url = (url or str(saved.get("url") or "")).strip()
    token = (token or str(saved.get("token") or "")).strip()
    if not url or not token:
        raise ValueError("时间书还没配好，到设置里填地址和令牌")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "timebook-mcp-token": token,
    }

    def post(payload: dict, extra: dict | None = None) -> tuple[dict, str]:
        body = json.dumps(payload).encode("utf-8")
        req_headers = dict(headers)
        if extra:
            req_headers.update(extra)
        request = urllib.request.Request(url, data=body, method="POST", headers=req_headers)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
                session = response.headers.get("mcp-session-id") or ""
        except urllib.error.HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace")
            raise ValueError("时间书没有收下：" + detail[:120]) from None
        except OSError:
            raise ValueError("连不上时间书") from None
        if not raw:
            return {}, session
        if raw.lstrip().startswith("{"):
            return json.loads(raw), session
        data = ""
        for line in raw.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
        return (json.loads(data) if data else {}), session

    opened, session = post({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "work-plan", "version": "0.1"},
        },
    })
    if not session:
        raise ValueError("时间书没有开始会话")
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, {"Mcp-Session-Id": session})
    counter = {"n": 2}

    def call(name: str, arguments: dict) -> dict:
        result, _session = post({
            "jsonrpc": "2.0",
            "id": counter["n"],
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }, {"Mcp-Session-Id": session})
        counter["n"] += 1
        if result.get("error"):
            raise ValueError("时间书没有收下")
        payload = result.get("result") or {}
        if payload.get("isError"):
            text = ""
            for item in payload.get("content") or []:
                text = item.get("text") or text
            raise ValueError((text or "时间书没有收下").strip()[:120])
        return payload

    return call


def timebook_call(name: str, arguments: dict) -> dict:
    return open_timebook()(name, arguments)


def tool_text(payload: dict) -> str:
    for item in payload.get("content") or []:
        text = item.get("text") or ""
        if text.strip():
            return text
    return ""


def tool_json(payload: dict) -> dict:
    text = tool_text(payload).strip()
    if text.startswith("{"):
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def timebook_categories() -> list[dict]:
    parsed = tool_json(timebook_call("list_time_categories", {}))
    nodes = parsed.get("data") or []
    rows = []

    def walk(items: list, prefix: str) -> None:
        for node in items:
            if not isinstance(node, dict) or not node.get("active", True):
                continue
            name = node.get("name") or ""
            label = f"{prefix} / {name}" if prefix else name
            children = node.get("children") or []
            if children:
                walk(children, label)
            elif node.get("id"):
                rows.append({"id": int(node["id"]), "name": label})

    if isinstance(nodes, list):
        walk(nodes, "")
    return rows


def timebook_now() -> datetime:
    text = tool_text(timebook_call("get_current_datetime", {}))
    found = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", text)
    if not found:
        raise ValueError("时间书没有给出当前时间")
    return datetime.strptime(found.group(1) + " " + found.group(2), "%Y-%m-%d %H:%M")


def read_local_timebook() -> dict:
    try:
        saved = json.loads(TIMEBOOK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return saved if isinstance(saved, dict) else {}


def timebook_config() -> dict:
    saved = read_local_timebook()
    if not saved.get("url") or not saved.get("token"):
        raise ValueError("时间书还没配好，到设置里填地址和令牌")
    return saved


def timebook_public() -> dict:
    saved = read_local_timebook()
    return {
        "timebook_url": str(saved.get("url") or ""),
        "has_timebook_token": bool(str(saved.get("token") or "").strip()),
    }


def deepseek_public() -> dict:
    saved = read_local_timebook()
    return {
        "deepseek_base": str(saved.get("deepseek_base") or "https://api.deepseek.com"),
        "deepseek_model": str(saved.get("deepseek_model") or "deepseek-flash"),
        "has_deepseek_key": bool(str(saved.get("deepseek_key") or "").strip()),
    }


def save_local_timebook(data: dict) -> None:
    watched = ("timebook_url", "timebook_token", "deepseek_key", "deepseek_base", "deepseek_model")
    if not any(key in data for key in watched):
        return
    saved = read_local_timebook()
    if "timebook_url" in data:
        url = str(data.get("timebook_url") or "").strip()
        if url:
            if not url.startswith(("http://", "https://")) or len(url) > 200:
                raise ValueError("时间书地址要以 http:// 或 https:// 开头")
            saved["url"] = url
    token = str(data.get("timebook_token") or "").strip()
    if token:
        if len(token) > 400:
            raise ValueError("令牌太长")
        saved["token"] = token
    base = str(data.get("deepseek_base") or "").strip()
    model = str(data.get("deepseek_model") or "").strip()
    key = str(data.get("deepseek_key") or "").strip()
    if base:
        saved["deepseek_base"] = base
    if model:
        saved["deepseek_model"] = model
    if key:
        saved["deepseek_key"] = key
    if not saved:
        return
    TIMEBOOK.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def remember_category(category_id: int) -> None:
    saved = timebook_config()
    saved["category_id"] = int(category_id)
    TIMEBOOK.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mentioned(text: str, name: str) -> bool:
    name = (name or "").strip()
    if len(name) < 2:
        return False
    return name.lower() in text.lower()


def flatten_categories(nodes: list) -> list[dict]:
    rows = []

    def walk(items: list, prefix: str) -> None:
        for node in items:
            if not isinstance(node, dict) or not node.get("active", True):
                continue
            name = str(node.get("name") or "")
            label = f"{prefix} / {name}" if prefix else name
            children = node.get("children") or []
            if children:
                walk(children, label)
            elif node.get("id"):
                rows.append({"id": int(node["id"]), "name": label, "leaf": name})

    walk(nodes, "")
    return rows


def occupied_spans(rows: list, day: datetime) -> list[tuple[datetime, datetime]]:
    spans = []
    for row in rows:
        if not row.get("id"):
            continue
        try:
            start = datetime.strptime(day.strftime("%Y-%m-%d ") + str(row.get("start") or ""), "%Y-%m-%d %H:%M")
            end = datetime.strptime(day.strftime("%Y-%m-%d ") + str(row.get("end") or ""), "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if end > start:
            spans.append((start, end))
    spans.sort()
    return spans


def free_tail(start: datetime, end: datetime, spans: list[tuple[datetime, datetime]]) -> tuple[datetime, datetime] | None:
    cursor = start
    for left, right in spans:
        if right <= cursor or left >= end:
            continue
        if left <= cursor < right:
            cursor = right
    if end - cursor >= timedelta(minutes=1):
        return cursor, end
    return None


def record_store() -> str:
    value = str(read_settings().get("record_store") or "timebook").strip()
    return value if value in ("timebook", "obsidian") else "timebook"


LOCAL_LOG = "时间记录.md"
LOCAL_ROW = re.compile(r"^- (\d{2}:\d{2})–(\d{2}:\d{2})\s+\S+｜")


def local_log_path() -> Path:
    root = vault_root()
    if root is None:
        raise ValueError("先选择笔记文件夹")
    return root / LOCAL_LOG


def local_spans(lines: list[str], day: datetime) -> list[tuple[datetime, datetime]]:
    heading = f"## {day.strftime('%Y-%m-%d')}"
    inside = False
    spans = []
    for line in lines:
        if line.startswith("## "):
            inside = line.strip() == heading
            continue
        if not inside:
            continue
        match = LOCAL_ROW.match(line)
        if not match:
            continue
        start = datetime.strptime(day.strftime("%Y-%m-%d ") + match.group(1), "%Y-%m-%d %H:%M")
        end = datetime.strptime(day.strftime("%Y-%m-%d ") + match.group(2), "%Y-%m-%d %H:%M")
        if end > start:
            spans.append((start, end))
    spans.sort()
    return spans


def local_block(start: str, end: str, category: str, text: str) -> list[str]:
    category = re.sub(r"\s+", "", (category or "").strip()).replace("｜", "/") or "记录"
    rows = [line.strip() for line in (text or "记一笔").splitlines() if line.strip()] or ["记一笔"]
    block = [f"- {start}–{end} {category}｜{rows[0]}"]
    block.extend("  " + line for line in rows[1:])
    return block


def place_local_block(lines: list[str], day: str, block: list[str]) -> list[str]:
    lines = list(lines)
    if not lines:
        lines = ["# 时间记录", ""]
    heading = f"## {day}"
    if heading not in lines:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend([heading, ""])
    at = lines.index(heading) + 1
    while at < len(lines) and not lines[at].startswith("## "):
        at += 1
    while at > 0 and lines[at - 1] == "":
        at -= 1
    lines[at:at] = block + [""]
    return lines


def local_now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def finish_local(title: str, note: str = "", hints: str = "", started: str = "") -> dict:
    title = (title or "").strip()
    if not title or len(title) > 200:
        raise ValueError("先有一条任务")
    now = local_now()
    path = local_log_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    spans = local_spans(lines, now)
    started = (started or "").strip()
    if started:
        mark = re.fullmatch(r"(\d{1,2}):(\d{2})", started)
        if not mark:
            raise ValueError("开始时间不对")
        hour, minute = int(mark.group(1)), int(mark.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("开始时间不对")
        start_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now - start_dt < timedelta(minutes=1):
            raise ValueError("专注还不到一分钟")
    else:
        last_end = max((right for _left, right in spans), default=None)
        if last_end and last_end < now:
            start_dt = last_end
        else:
            raise ValueError("今天还没有上一条记录，先在笔记里有一段之后再记")
    asked = start_dt
    slot = free_tail(start_dt, now, spans)
    if not slot or (started and slot[0] != asked):
        raise ValueError("这段时间和已有记录重叠了")
    start_dt, end_dt = slot
    text = record_text(title, "\n".join(part for part in (note or "", hints or "") if str(part).strip()))
    block = local_block(start_dt.strftime("%H:%M"), end_dt.strftime("%H:%M"), "记录", text)
    write_note(path, place_local_block(lines, now.strftime("%Y-%m-%d"), block), True)
    return {
        "ok": True,
        "title": title,
        "date": now.strftime("%Y-%m-%d"),
        "start": start_dt.strftime("%H:%M"),
        "end": end_dt.strftime("%H:%M"),
        "summary": f"{start_dt.strftime('%H:%M')}–{end_dt.strftime('%H:%M')} · 记录",
        "where": "obsidian",
    }


def finish_local_segments(text: str) -> dict:
    rows = segment_lines(text)
    if not rows:
        raise ValueError("先写时间，例如 0:47，工作，说明")
    now = local_now()
    path = local_log_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    spans = local_spans(lines, now)
    last_end = max((right for _left, right in spans), default=None)
    if not last_end or last_end >= now:
        raise ValueError("今天还没有上一条记录，先在笔记里有一段之后再记")
    cursor = last_end
    block = []
    summaries = []
    for parts in rows:
        end = parse_mark(parts[0], now, now)
        if len(parts) >= 3:
            category, desc = parts[1], note_after_category(parts[1], parts[2])
        elif len(parts) == 2:
            category, desc = "", parts[1]
        else:
            category, desc = "", ""
        if end <= cursor:
            raise ValueError(parts[0] + " 得晚于上一段")
        slot = free_tail(cursor, end, spans)
        if not slot:
            raise ValueError("这段时间和已有记录重叠了")
        start_dt, end_dt = slot
        category = category or "记录"
        block.extend(local_block(start_dt.strftime("%H:%M"), end_dt.strftime("%H:%M"), category, desc or "记一笔"))
        spans.append((start_dt, end_dt))
        spans.sort()
        cursor = end_dt
        summaries.append(f"{start_dt.strftime('%H:%M')}–{end_dt.strftime('%H:%M')} {category}")
    write_note(path, place_local_block(lines, now.strftime("%Y-%m-%d"), block), True)
    return {"ok": True, "count": len(summaries), "summary": f"{len(summaries)} 段 · " + " · ".join(summaries), "where": "obsidian"}


def record_text(title: str, note: str) -> str:
    title = title.strip()
    note = (note or "").strip()
    if note and note not in title:
        return f"{title}\n{note}"
    return title


def load_catalog(call, refresh: bool = False) -> dict:
    today = date.today().isoformat()
    path = HERE / "timebook-cache.json"
    if not refresh:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("date") == today and cached.get("categories") and cached.get("options"):
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    categories = flatten_categories(tool_json(call("list_time_categories", {})).get("data") or [])
    options = tool_json(call("get_time_record_options", {})).get("data") or {}
    cached = {"date": today, "categories": categories, "options": options}
    path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    return cached


def timebook_snapshot(refresh: bool = False) -> dict:
    call = open_timebook()
    catalog = load_catalog(call, refresh)
    options = catalog.get("options") or {}
    names = {int(item["id"]): item.get("name") or "" for item in catalog.get("categories") or [] if item.get("id")}
    clock = tool_text(call("get_current_datetime", {}))
    found = re.search(r"(\d{4}-\d{2}-\d{2})", clock or "")
    day = found.group(1) if found else date.today().isoformat()
    raw = tool_json(call("get_time_line", {"date": day})).get("data") or []
    rows = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        try:
            category_id = int(row.get("category_id") or 0)
        except (TypeError, ValueError):
            category_id = 0
        category = str(row.get("category") or "") or names.get(category_id, "")
        rows.append({
            "start": str(row.get("start") or ""),
            "end": str(row.get("end") or ""),
            "category": category,
            "description": str(row.get("description") or ""),
            "people": [str(item) for item in row.get("people") or [] if item],
            "tags": [str(item) for item in row.get("tag") or [] if item],
            "locations": [str(item) for item in row.get("location") or [] if item],
            "things": [str(item) for item in row.get("thing") or [] if item],
        })
    rows.sort(key=lambda item: item["start"])

    def listed(key: str) -> list[str]:
        values = options.get(key) or []
        if key == "tags":
            return [str(item.get("full_name") or "") for item in values if isinstance(item, dict) and item.get("full_name")]
        return [str(item) for item in values if isinstance(item, str) and item]

    return {
        "date": day,
        "cache_date": str(catalog.get("date") or ""),
        "timeline": rows,
        "categories": [str(item.get("name") or "") for item in catalog.get("categories") or []],
        "tags": listed("tags"),
        "peoples": listed("peoples"),
        "locations": listed("locations"),
        "things": listed("things"),
    }


def work_category(categories: list[dict]) -> dict | None:
    for item in categories:
        if item.get("name") == "事业 / 工作":
            return item
    for item in categories:
        if item.get("leaf") == "工作" and str(item.get("name") or "").startswith("事业"):
            return item
    return None


def leaf_hit(text: str, categories: list[dict]) -> dict | None:
    hits = [item for item in categories if mentioned(text, item.get("leaf") or "")]
    hits.sort(key=lambda item: len(item.get("leaf") or ""), reverse=True)
    return hits[0] if hits else None


def lock_category(text: str, categories: list[dict]) -> dict | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    leaves = sorted(categories, key=lambda item: len(item.get("leaf") or ""), reverse=True)
    for item in leaves:
        leaf = item.get("leaf") or ""
        if leaf and stripped.startswith(leaf):
            return item
    return None


def ask_deepseek(text: str, categories: list[dict]) -> dict | None:
    import urllib.error
    import urllib.request

    saved = timebook_config()
    key = str(saved.get("deepseek_key") or "").strip()
    if not key:
        return None
    base = str(saved.get("deepseek_base") or "https://api.deepseek.com").rstrip("/")
    model = str(saved.get("deepseek_model") or "deepseek-flash").strip() or "deepseek-flash"
    listing = "\n".join(f"{item['id']} {item['name']}" for item in categories)
    prompt = (
        "根据备注从分类里选一个 id。多数时候是工作，拿不准就选「事业 / 工作」。"
        "只回复一个数字。\n"
        + listing
        + "\n备注：\n"
        + text[:2000]
    )
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "max_tokens": 16,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        answer = payload["choices"][0]["message"]["content"]
    except (OSError, urllib.error.HTTPError, KeyError, IndexError, json.JSONDecodeError):
        return None
    digits = re.search(r"\d+", answer or "")
    if not digits:
        return None
    chosen = int(digits.group(0))
    return next((item for item in categories if item["id"] == chosen), None)


def finish_timebook(title: str, category_id, note: str = "", hints: str = "", started: str = "") -> dict:
    title = (title or "").strip()
    if not title or len(title) > 200:
        raise ValueError("先有一条任务")
    call = open_timebook()
    clock = tool_text(call("get_current_datetime", {}))
    found = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", clock)
    if not found:
        raise ValueError("时间书没有给出当前时间")
    end_dt = datetime.strptime(found.group(1) + " " + found.group(2), "%Y-%m-%d %H:%M")
    catalog = load_catalog(call)
    categories = catalog["categories"]
    options = catalog["options"]
    timeline = tool_json(call("get_time_line", {"date": end_dt.strftime("%Y-%m-%d")})).get("data") or []
    blob = "\n".join(part for part in (title, note or "", hints or "") if part)
    lead = re.match(r"^(\S+?)\s*[，,、]", title)
    lead_item = match_leaf(lead.group(1), categories) if lead else None
    chosen_item = lock_category(hints or "", categories) or lock_category(blob, categories) or lead_item
    if not chosen_item:
        chosen_item = ask_deepseek(blob, categories) or leaf_hit(blob, categories) or work_category(categories)
    chosen = chosen_item["id"] if chosen_item else (category_id or timebook_config().get("category_id"))
    if not chosen:
        return {"need_category": True, "categories": [{"id": item["id"], "name": item["name"]} for item in categories]}
    chosen_name = chosen_item["name"] if chosen_item else next((item["name"] for item in categories if item["id"] == int(chosen)), "")
    tags = []
    tag_names = []
    for tag in options.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        full = str(tag.get("full_name") or "")
        leaf = full.split("/")[-1]
        if mentioned(blob, leaf) or mentioned(blob, full):
            tags.append(int(tag["id"]))
            tag_names.append(full)
            if len(tags) >= 8:
                break
    peoples = [name for name in options.get("peoples") or [] if isinstance(name, str) and mentioned(blob, name)][:8]
    if "小伟" in blob and "晓伟" in (options.get("peoples") or []) and "晓伟" not in peoples:
        peoples.append("晓伟")
    locations = [name for name in options.get("locations") or [] if isinstance(name, str) and mentioned(blob, name)][:8]
    things = [name for name in options.get("things") or [] if isinstance(name, str) and mentioned(blob, name)][:8]
    clauses = set(clause_list(blob))
    ups = [name for name in options.get("positives") or [] if isinstance(name, str) and name in clauses][:8]
    downs = [name for name in options.get("negatives") or [] if isinstance(name, str) and name in clauses][:8]
    weathers = [name for name in options.get("weathers") or [] if isinstance(name, str) and name in clauses][:8]
    spans = occupied_spans(timeline if isinstance(timeline, list) else [], end_dt)
    started = (started or "").strip()
    if started:
        mark = re.fullmatch(r"(\d{1,2}):(\d{2})", started)
        if not mark:
            raise ValueError("开始时间不对")
        hour, minute = int(mark.group(1)), int(mark.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("开始时间不对")
        start_dt = end_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if end_dt - start_dt < timedelta(minutes=1):
            raise ValueError("专注还不到一分钟")
    else:
        last_end = max((right for _left, right in spans), default=None)
        if last_end and last_end < end_dt:
            start_dt = last_end
        else:
            raise ValueError("今天还没有上一条记录，先在时间书里有一段之后再记")
    asked = start_dt
    slot = free_tail(start_dt, end_dt, spans)
    if not slot or (started and slot[0] != asked):
        raise ValueError("这段时间和已有记录重叠了")
    start_dt, end_dt = slot
    arguments = {
        "date": end_dt.strftime("%Y-%m-%d"),
        "start": start_dt.strftime("%H:%M"),
        "end": end_dt.strftime("%H:%M"),
        "category_id": int(chosen),
        "description": strip_category_lead(
            record_text(title, "\n".join(part for part in (note or "", hints or "") if str(part).strip())),
            chosen_item,
        ) or "记一笔",
    }
    if tags:
        arguments["tags"] = tags
    if peoples:
        arguments["peoples"] = peoples
    if locations:
        arguments["locations"] = locations
    if things:
        arguments["things"] = things
    if ups:
        arguments["ups"] = ups
    if downs:
        arguments["downs"] = downs
    if weathers:
        arguments["weathers"] = weathers
    peeled = peel_record(record_text(title, "\n".join(part for part in (note or "", hints or "") if str(part).strip())), chosen_item, options)
    arguments["description"] = peeled["description"] or "记一笔"
    if peeled["money"]:
        arguments["money"] = peeled["money"]
    if peeled["income"]:
        arguments["income"] = peeled["income"]
    parsed = tool_json(call("add_time_record", arguments))
    if parsed.get("code") not in (0, None):
        raise ValueError(parsed.get("msg") or "时间书没有收下")
    remember_category(int(chosen))
    bits = [f"{arguments['start']}–{arguments['end']}", chosen_name]
    if arguments.get("money"):
        bits.append("支出 " + str(arguments["money"]))
    if arguments.get("income"):
        bits.append("收入 " + str(arguments["income"]))
    if tag_names:
        bits.append("标签 " + "、".join(tag_names[:4]))
    if peoples:
        bits.append("人物 " + "、".join(peoples[:4]))
    if things:
        bits.append("物品 " + "、".join(things[:4]))
    if locations:
        bits.append("地点 " + "、".join(locations[:4]))
    return {
        "ok": True,
        "title": title,
        "date": arguments["date"],
        "start": arguments["start"],
        "end": arguments["end"],
        "category_id": int(chosen),
        "summary": " · ".join(bit for bit in bits if bit),
    }


TIME_HEAD = re.compile(r"^(?:现在|now|\d{1,2}\s*[:：]\s*\d{2}|\d{3,4})$", re.I)


def clock_parts(token: str) -> tuple[int, int] | None:
    token = (token or "").strip()
    found = re.fullmatch(r"(\d{1,2})\s*[:：]\s*(\d{2})", token)
    if not found and re.fullmatch(r"\d{3,4}", token):
        found = re.fullmatch(r"(\d{1,2})(\d{2})", token)
    if not found:
        return None
    return int(found.group(1)), int(found.group(2))


def note_after_category(category: str, desc: str) -> str:
    text = (desc or "").strip()
    word = (category or "").strip()
    if word and text.startswith(word):
        text = text[len(word):].lstrip(" ，,、")
    return text


def strip_category_lead(text: str, item: dict | None) -> str:
    text = (text or "").strip()
    if not text or not item:
        return text
    full = str(item.get("name") or "").strip()
    leaf = str(item.get("leaf") or "").strip() or (full.split("/")[-1].strip() if "/" in full else full)
    words = [word for word in (leaf, full) if word]
    lines = text.splitlines()
    first = lines[0].strip()
    tail = "\n".join(line for line in lines[1:] if line.strip())
    found = re.match(r"^(.+?)\s*[，,、]\s*(.*)$", first)
    if not found:
        return text
    token, rest = found.group(1).strip(), found.group(2).strip()
    if any(token == word or (len(token) >= 2 and word.startswith(token)) for word in words):
        body = "\n".join(part for part in (rest, tail) if part.strip())
        return body
    return text


def segment_lines(text: str) -> list[list[str]] | None:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return None
    rows = []
    for line in lines:
        parts = [part.strip() for part in re.split(r"[，,]", line, maxsplit=2)]
        parts = [part for part in parts if part]
        if not parts or not TIME_HEAD.match(parts[0]):
            return None
        rows.append(parts)
    return rows


def parse_mark(token: str, day: datetime, now: datetime) -> datetime:
    token = token.strip()
    if token.lower() in ("现在", "now"):
        return now.replace(second=0, microsecond=0)
    found = clock_parts(token)
    if not found:
        raise ValueError("认不出时间：" + token)
    hour, minute = found
    if hour > 23 or minute > 59:
        raise ValueError("时间不对：" + token)
    stamp = datetime(day.year, day.month, day.day, hour, minute)
    if stamp > now:
        raise ValueError(token + " 还没到")
    return stamp


def match_leaf(word: str, categories: list[dict]) -> dict | None:
    word = (word or "").strip()
    if not word:
        return None
    exact = [item for item in categories if item.get("leaf") == word or item.get("name") == word]
    if exact:
        for item in exact:
            if str(item.get("name") or "").startswith("事业"):
                return item
        return exact[0]
    tails = []
    for item in categories:
        leaf = str(item.get("leaf") or "")
        tail = re.split(r"[：:]", leaf)[-1].strip()
        if tail == word:
            tails.append(item)
    if tails:
        tails.sort(key=lambda item: (-len(str(item.get("leaf") or "")), 0 if str(item.get("name") or "").startswith("事业") else 1))
        return tails[0]
    prefix = [item for item in categories if str(item.get("leaf") or "").startswith(word)]
    prefix.sort(key=lambda item: (len(item.get("leaf") or ""), 0 if str(item.get("name") or "").startswith("事业") else 1))
    return prefix[0] if prefix else None


def clause_list(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[，,、\n]", text or "") if part.strip()]


def money_clause(token: str) -> tuple[str, int | float] | None:
    raw = (token or "").strip()
    kind = "money"
    if raw.startswith("收入"):
        kind = "income"
        raw = raw[2:].strip()
    elif raw.startswith("支出"):
        raw = raw[2:].strip()
    raw = raw.strip("+").replace("￥", "").replace("¥", "")
    raw = re.sub(r"(元|块钱|块)$", "", raw).strip()
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", raw):
        return None
    if "." in raw:
        whole, frac = raw.split(".", 1)
        frac = frac.rstrip("0")
        amount = int(whole or "0") if not frac else float(f"{int(whole or '0')}.{frac}")
    else:
        amount = int(raw)
    if amount <= 0 or amount > 1000000:
        return None
    return kind, amount


def peel_record(text: str, item: dict | None, options: dict | None = None) -> dict:
    known = set()
    if item:
        leaf = str(item.get("leaf") or "")
        full = str(item.get("name") or "")
        known.update(part.strip() for part in re.split(r"[/／:：]", leaf) if part.strip())
        known.add(leaf.strip())
        known.add(full.strip())
        if "/" in full:
            known.add(full.split("/")[-1].strip())
    known.discard("")
    leaf = str((item or {}).get("leaf") or "")
    tails = [part.strip() for part in re.split(r"[/／:：]", leaf) if part.strip()]
    money = 0
    income = 0
    lines_out = []
    for raw_line in (text or "").replace("\r\n", "\n").split("\n"):
        if not raw_line.strip():
            lines_out.append("")
            continue
        kept = []
        for clause in [part.strip() for part in re.split(r"[，,、]", raw_line) if part.strip()]:
            hit = money_clause(clause)
            if hit:
                if hit[0] == "income":
                    income = hit[1]
                elif not money:
                    money = hit[1]
            if clause in known or (len(clause) >= 2 and any(part.startswith(clause) for part in tails)):
                continue
            kept.append(clause)
        lines_out.append("，".join(kept))
    return {"description": "\n".join(lines_out).strip("\n"), "money": money, "income": income}


def attach_names(blob: str, options: dict) -> dict:
    tags = []
    tag_names = []
    for tag in options.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        full = str(tag.get("full_name") or "")
        leaf = full.split("/")[-1]
        if mentioned(blob, leaf) or mentioned(blob, full):
            tags.append(int(tag["id"]))
            tag_names.append(full)
            if len(tags) >= 8:
                break
    peoples = [name for name in options.get("peoples") or [] if isinstance(name, str) and mentioned(blob, name)][:8]
    if "小伟" in blob and "晓伟" in (options.get("peoples") or []) and "晓伟" not in peoples:
        peoples.append("晓伟")
    locations = [name for name in options.get("locations") or [] if isinstance(name, str) and mentioned(blob, name)][:8]
    things = [name for name in options.get("things") or [] if isinstance(name, str) and mentioned(blob, name)][:8]
    clauses = set(clause_list(blob))
    ups = [name for name in options.get("positives") or [] if isinstance(name, str) and name in clauses][:8]
    downs = [name for name in options.get("negatives") or [] if isinstance(name, str) and name in clauses][:8]
    weathers = [name for name in options.get("weathers") or [] if isinstance(name, str) and name in clauses][:8]
    extra = {}
    if tags:
        extra["tags"] = tags
    if peoples:
        extra["peoples"] = peoples
    if locations:
        extra["locations"] = locations
    if things:
        extra["things"] = things
    if ups:
        extra["ups"] = ups
    if downs:
        extra["downs"] = downs
    if weathers:
        extra["weathers"] = weathers
    return {"extra": extra, "tag_names": tag_names, "peoples": peoples, "things": things, "locations": locations}


def finish_segments(text: str) -> dict:
    rows = segment_lines(text)
    if not rows:
        raise ValueError("先写时间，例如 0:47，工作，说明")
    call = open_timebook()
    clock = tool_text(call("get_current_datetime", {}))
    found = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", clock)
    if not found:
        raise ValueError("时间书没有给出当前时间")
    now = datetime.strptime(found.group(1) + " " + found.group(2), "%Y-%m-%d %H:%M")
    catalog = load_catalog(call)
    categories = catalog["categories"]
    options = catalog["options"]
    timeline = tool_json(call("get_time_line", {"date": now.strftime("%Y-%m-%d")})).get("data") or []
    spans = occupied_spans(timeline if isinstance(timeline, list) else [], now)
    last_end = max((right for _left, right in spans), default=None)
    if not last_end or last_end >= now:
        raise ValueError("今天还没有上一条记录，先在时间书里有一段之后再记")
    cursor = last_end
    summaries = []
    for parts in rows:
        end = parse_mark(parts[0], now, now)
        if len(parts) >= 3:
            category_word, desc = parts[1], note_after_category(parts[1], parts[2])
        elif len(parts) == 2:
            category_word, desc = "", parts[1]
        else:
            category_word, desc = "", ""
        if end <= cursor:
            raise ValueError(parts[0] + " 得晚于上一段")
        slot = free_tail(cursor, end, spans)
        if not slot:
            raise ValueError("这段时间和已有记录重叠了")
        start_dt, end_dt = slot
        blob = desc
        if category_word:
            chosen_item = match_leaf(category_word, categories)
            if not chosen_item:
                raise ValueError("没有对上分类：" + category_word)
        else:
            chosen_item = lock_category(blob, categories) or ask_deepseek(blob, categories) or leaf_hit(blob, categories) or work_category(categories)
        if not chosen_item:
            raise ValueError("没有对上分类")
        names = attach_names(blob, options)
        peeled = peel_record(desc, chosen_item, options)
        arguments = {
            "date": end_dt.strftime("%Y-%m-%d"),
            "start": start_dt.strftime("%H:%M"),
            "end": end_dt.strftime("%H:%M"),
            "category_id": int(chosen_item["id"]),
            "description": peeled["description"] or "记一笔",
        }
        if peeled["money"]:
            arguments["money"] = peeled["money"]
        if peeled["income"]:
            arguments["income"] = peeled["income"]
        arguments.update(names["extra"])
        parsed = tool_json(call("add_time_record", arguments))
        if parsed.get("code") not in (0, None):
            raise ValueError(parsed.get("msg") or "时间书没有收下")
        spans.append((start_dt, end_dt))
        spans.sort()
        cursor = end_dt
        summaries.append(f"{arguments['start']}–{arguments['end']} {chosen_item.get('name') or ''}")
    return {"ok": True, "count": len(summaries), "summary": f"{len(summaries)} 段 · " + " · ".join(summaries)}


def pin_main_window(on: bool) -> dict:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    def visit(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.replace("\u200b", "")
        if "Microsoft" in title or "Edge" in title or "Chrome" in title:
            return True
        if title == "工作计划" or title.endswith("— 工作计划") or title.endswith("- 工作计划"):
            found.append(hwnd)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    if not found:
        raise ValueError("没找到工作计划窗口")
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, ctypes.c_void_p,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    flags = 0x0001 | 0x0002 | 0x0010
    if not user32.SetWindowPos(found[-1], ctypes.c_void_p(-1 if on else -2), 0, 0, 0, 0, flags):
        raise ValueError("没钉住")
    global WINDOW_PINNED
    WINDOW_PINNED = bool(on)
    if PIN_HWND:
        user32.InvalidateRect(PIN_HWND, None, True)
    return {"ok": True, "on": bool(on)}


WINDOW_PINNED = False
PIN_HWND = None
DESK = {"tick": 0, "action": "", "locked": False, "hideDone": True}
CHROME = {"hide": 78, "label": 52}


def find_main_window():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    def visit(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.replace("\u200b", "")
        if "Microsoft" in title or "Edge" in title or "Chrome" in title:
            return True
        if title == "工作计划" or title.endswith("— 工作计划") or title.endswith("- 工作计划"):
            found.append(hwnd)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return found[-1] if found else None


def start_pin_overlay() -> None:
    import ctypes
    import threading
    from ctypes import wintypes

    def loop() -> None:
        global PIN_HWND
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        dwmapi = ctypes.windll.dwmapi
        kernel32 = ctypes.windll.kernel32
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetWindow.restype = wintypes.HWND
        dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        gdi32.CreateFontW.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
            wintypes.LPCWSTR,
        ]
        gdi32.CreateFontW.restype = wintypes.HFONT
        gdi32.CreateSolidBrush.argtypes = [ctypes.c_uint]
        gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        user32.FillRect.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.HBRUSH]
        user32.FillRect.restype = ctypes.c_int
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        user32.DrawTextW.restype = ctypes.c_int
        user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wintypes.HDC),
                ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", ctypes.c_byte * 32),
            ]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        owner = {"hwnd": None}
        shown = {"on": False}

        def follow() -> None:
            global PIN_HWND
            hwnd = PIN_HWND
            if not hwnd:
                return
            main = find_main_window()
            if not main:
                if shown["on"]:
                    user32.ShowWindow(hwnd, 0)
                    shown["on"] = False
                return
            bounds = wintypes.RECT()
            if dwmapi.DwmGetWindowAttribute(main, 5, ctypes.byref(bounds), ctypes.sizeof(bounds)) != 0:
                return
            height = bounds.bottom - bounds.top
            if height < 16 or bounds.right <= bounds.left:
                return
            frame = wintypes.RECT()
            visible = wintypes.RECT()
            user32.GetWindowRect(main, ctypes.byref(frame))
            if dwmapi.DwmGetWindowAttribute(main, 9, ctypes.byref(visible), ctypes.sizeof(visible)) != 0:
                visible = frame
            pin_w = height
            width = CHROME["hide"] + CHROME["label"] + pin_w
            x = visible.left + bounds.left - width
            y = visible.top + bounds.top
            above = user32.GetWindow(main, 3)
            user32.SetWindowPos(hwnd, above or 0, x, y, width, height, 0x0010 | 0x0040)
            shown["on"] = True

        @ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == 0x0202:
                x = int(lparam) & 0xFFFF
                hide_w = CHROME["hide"]
                label_end = hide_w + CHROME["label"]
                if x < hide_w:
                    DESK["tick"] += 1
                    DESK["action"] = "hide"
                    DESK["hideDone"] = not DESK["hideDone"]
                elif x < label_end:
                    DESK["tick"] += 1
                    DESK["action"] = "ratio"
                else:
                    try:
                        pin_main_window(not WINDOW_PINNED)
                    except ValueError:
                        pass
                user32.InvalidateRect(hwnd, None, True)
                return 0
            if msg == 0x0113:
                follow()
                return 0
            if msg == 0x000F:
                paint = PAINTSTRUCT()
                hdc = user32.BeginPaint(hwnd, ctypes.byref(paint))
                client = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(client))
                hide_w = min(CHROME["hide"], client.right)
                label_w = min(CHROME["label"], max(0, client.right - hide_w))
                whole = gdi32.CreateSolidBrush(0x00F3F3F3)
                user32.FillRect(hdc, ctypes.byref(client), whole)
                gdi32.DeleteObject(whole)
                if DESK["hideDone"]:
                    hide_box = wintypes.RECT(0, 0, hide_w, client.bottom)
                    mark = gdi32.CreateSolidBrush(0x00F3E6D4)
                    user32.FillRect(hdc, ctypes.byref(hide_box), mark)
                    gdi32.DeleteObject(mark)
                if DESK["locked"]:
                    lock_box = wintypes.RECT(hide_w, 0, hide_w + label_w, client.bottom)
                    mark = gdi32.CreateSolidBrush(0x00F3E6D4)
                    user32.FillRect(hdc, ctypes.byref(lock_box), mark)
                    gdi32.DeleteObject(mark)
                if WINDOW_PINNED:
                    pin_box = wintypes.RECT(hide_w + label_w, 0, client.right, client.bottom)
                    mark = gdi32.CreateSolidBrush(0x00F3E6D4)
                    user32.FillRect(hdc, ctypes.byref(pin_box), mark)
                    gdi32.DeleteObject(mark)
                gdi32.SetBkMode(hdc, 1)
                gdi32.SetTextColor(hdc, 0x004A4A4A)
                text_font = gdi32.CreateFontW(-13, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Microsoft YaHei")
                old = gdi32.SelectObject(hdc, text_font)
                hide_rect = wintypes.RECT(0, 0, hide_w, client.bottom)
                word = wintypes.RECT(hide_w, 0, hide_w + label_w, client.bottom)
                user32.DrawTextW(hdc, "已完成", -1, ctypes.byref(hide_rect), 0x0001 | 0x0004 | 0x0020)
                user32.DrawTextW(hdc, "锁定" if DESK["locked"] else "比例", -1, ctypes.byref(word), 0x0001 | 0x0004 | 0x0020)
                icon_font = gdi32.CreateFontW(-(max(client.bottom, 16) * 2 // 3), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe MDL2 Assets")
                gdi32.SelectObject(hdc, icon_font)
                icon = wintypes.RECT(hide_w + label_w, 0, client.right, client.bottom)
                user32.DrawTextW(hdc, "\uE840" if WINDOW_PINNED else "\uE718", -1, ctypes.byref(icon), 0x0001 | 0x0004 | 0x0020)
                gdi32.SelectObject(hdc, old)
                gdi32.DeleteObject(text_font)
                gdi32.DeleteObject(icon_font)
                user32.EndPaint(hwnd, ctypes.byref(paint))
                return 0
            if msg == 0x0014:
                return 1
            return user32.DefWindowProcW(hwnd, msg, ctypes.c_void_p(int(wparam) & ((1 << 64) - 1)), ctypes.c_void_p(int(lparam) & ((1 << 64) - 1)))

        loop.wndproc = wndproc
        instance = kernel32.GetModuleHandleW(None)
        klass = WNDCLASSW()
        klass.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p)
        klass.hInstance = instance
        klass.lpszClassName = "WorkPlanPin"
        klass.hCursor = user32.LoadCursorW(None, 32512)
        user32.RegisterClassW(ctypes.byref(klass))
        user32.CreateWindowExW.argtypes = [
            ctypes.c_uint, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        hwnd = user32.CreateWindowExW(
            0x00000080, "WorkPlanPin", "", 0x80000000,
            0, 0, 32, 32, None, None, instance, None,
        )
        PIN_HWND = hwnd
        user32.SetTimer(hwnd, 1, 200, None)
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    threading.Thread(target=loop, name="pin-overlay", daemon=True).start()


def close_pop_windows() -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    titles = {"自由", "创建"}

    def visit(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.replace("\u200b", "")
        if title in titles:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)


def pick_folder() -> str:
    import subprocess

    command = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.Description = '选择笔记文件夹'; "
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $dialog.SelectedPath }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", command],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("没有打开文件夹选择") from None
    return (result.stdout or "").strip()


def probe_timebook(data: dict) -> dict:
    call = open_timebook(str(data.get("timebook_url") or ""), str(data.get("timebook_token") or ""))
    text = tool_text(call("get_current_datetime", {}))
    if not re.search(r"\d{4}-\d{2}-\d{2}", text or ""):
        raise ValueError("时间书没有给出当前时间")
    return {"ok": True, "message": "测试成功"}


def probe_deepseek(data: dict) -> dict:
    import urllib.error
    import urllib.request

    saved = read_local_timebook()
    key = str(data.get("deepseek_key") or saved.get("deepseek_key") or "").strip()
    if not key:
        raise ValueError("先填密钥")
    base = str(data.get("deepseek_base") or saved.get("deepseek_base") or "https://api.deepseek.com").rstrip("/")
    model = str(data.get("deepseek_model") or saved.get("deepseek_model") or "deepseek-flash").strip() or "deepseek-flash"
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "max_tokens": 32,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": "只回复这四个字：测试成功"}],
    }).encode("utf-8")
    request = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        answer = payload["choices"][0]["message"].get("content") or ""
    except urllib.error.HTTPError as exc:
        detail = exc.read(180).decode("utf-8", "replace")
        raise ValueError("分类接口没有通：" + detail[:80]) from None
    except (OSError, KeyError, IndexError, json.JSONDecodeError):
        raise ValueError("分类接口没有通") from None
    if "测试成功" in answer:
        return {"ok": True, "message": "测试成功"}
    raise ValueError("模型回了别的内容")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/tasks":
            self.send_json(200, collect())
            return
        if path == "/api/signal":
            self.send_json(200, take_signal())
            return
        if path == "/api/pop":
            self.send_json(200, take_pop())
            return
        if path == "/api/desk":
            self.send_json(200, {"tick": DESK["tick"], "action": DESK["action"], "locked": DESK["locked"], "hideDone": DESK["hideDone"]})
            return
        if path == "/api/timebook-view":
            refresh = parse_qs(urlparse(self.path).query).get("refresh", ["0"])[0] == "1"
            try:
                self.send_json(200, timebook_snapshot(refresh))
            except (ValueError, OSError) as exc:
                self.send_json(409, {"error": str(exc)})
            return
        if path in ("/", "/index.html"):
            page = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length > 100_000:
            self.send_json(400, {"error": "太大"})
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json(400, {"error": "读不懂"})
            return
        try:
            if path == "/api/toggle":
                result = toggle(data.get("file", ""), int(data.get("line") or 0), data.get("raw") or "")
            elif path == "/api/add":
                result = add_task(data.get("file", ""), data.get("text") or "")
            elif path == "/api/due":
                result = set_due(
                    data.get("file", ""),
                    int(data.get("line") or 0),
                    data.get("raw") or "",
                    data.get("due") or "",
                )
            elif path == "/api/bulk":
                result = apply_bulk(data.get("items") or [], data.get("op") or "", data.get("due") or "")
            elif path == "/api/apply":
                result = apply_text(data.get("items") or [], data.get("text") or "")
            elif path == "/api/page":
                result = replace_page(data.get("items") or [], data.get("lines") or [])
            elif path == "/api/form":
                result = apply_form(data.get("items") or [], data)
            elif path == "/api/note":
                result = set_note(
                    data.get("file", ""),
                    int(data.get("line") or 0),
                    data.get("raw") or "",
                    data.get("text") or "",
                )
            elif path == "/api/settings":
                result = save_settings(data)
            elif path == "/api/pick-folder":
                result = {"path": pick_folder()}
            elif path == "/api/pin-window":
                result = pin_main_window(bool(data.get("on")))
            elif path == "/api/desk":
                if "locked" in data:
                    DESK["locked"] = bool(data.get("locked"))
                if "hideDone" in data:
                    DESK["hideDone"] = bool(data.get("hideDone"))
                if PIN_HWND:
                    import ctypes
                    ctypes.windll.user32.InvalidateRect(PIN_HWND, None, True)
                result = {"tick": DESK["tick"], "action": DESK["action"], "locked": DESK["locked"], "hideDone": DESK["hideDone"]}
            elif path == "/api/close-pop":
                close_pop_windows()
                result = {"ok": True}
            elif path == "/api/time-categories":
                result = {"categories": timebook_categories()}
            elif path == "/api/finish":
                local = record_store() == "obsidian"
                if str(data.get("segments") or "").strip():
                    result = finish_local_segments(data.get("segments") or "") if local else finish_segments(data.get("segments") or "")
                elif local:
                    result = finish_local(
                        data.get("title") or "",
                        data.get("note") or "",
                        data.get("hints") or "",
                        data.get("started") or "",
                    )
                else:
                    result = finish_timebook(
                        data.get("title") or "",
                        data.get("category_id"),
                        data.get("note") or "",
                        data.get("hints") or "",
                        data.get("started") or "",
                    )
            elif path == "/api/probe-timebook":
                result = probe_timebook(data)
            elif path == "/api/probe-deepseek":
                result = probe_deepseek(data)
            elif path == "/api/insert":
                result = insert_under(
                    data.get("file", ""),
                    int(data.get("line") or 0),
                    data.get("raw") or "",
                    data.get("text") or "",
                )
            elif path == "/api/reorder":
                result = reorder_task(
                    data.get("file", ""),
                    int(data.get("line") or 0),
                    data.get("raw") or "",
                    int(data.get("delta") or 0),
                )
            else:
                self.send_error(404)
                return
        except (ValueError, OSError) as exc:
            self.send_json(409, {"error": str(exc)})
            return
        self.send_json(200, result)


def read_update_config() -> dict:
    try:
        saved = json.loads((HERE / "update.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": "0", "repo": ""}
    if not isinstance(saved, dict):
        return {"version": "0", "repo": ""}
    return saved


def version_key(text: str) -> tuple:
    nums = [int(item) for item in re.findall(r"\d+", text or "")]
    return tuple(nums[:3] or [0])


def newer_release(repo: str, current: str) -> dict | None:
    import urllib.request

    request = urllib.request.Request(
        "https://api.github.com/repos/" + repo.strip().strip("/") + "/releases/latest",
        headers={"User-Agent": "work-plan", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latest = str(payload.get("tag_name") or "")
    if version_key(latest) <= version_key(current):
        return None
    assets = payload.get("assets") or []
    chosen = None
    for asset in assets:
        name = str(asset.get("name") or "")
        if name == "工作计划.zip":
            chosen = asset
            break
        if chosen is None and name.lower().endswith(".zip"):
            chosen = asset
    if not chosen or not chosen.get("browser_download_url"):
        return None
    return {"version": latest.lstrip("vV"), "url": chosen["browser_download_url"]}


def stage_release(url: str) -> None:
    import shutil
    import urllib.request
    import zipfile

    folder = HERE / "updates"
    incoming = folder / "incoming.zip"
    dest = folder / "next"
    folder.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    urllib.request.urlretrieve(url, incoming)
    with zipfile.ZipFile(incoming) as packed:
        packed.extractall(dest)


def write_updater() -> Path:
    bat = HERE / "更新.bat"
    bat.write_text(
        "\r\n".join([
            "@echo off",
            "cd /d \"%~dp0\"",
            "ping 127.0.0.1 -n 6 >nul",
            "robocopy \"%~dp0updates\\next\\工作计划\" \"%~dp0.\" /E /NFL /NDL /NJH /NJS /XD updates /XF settings.json timebook.local.json timebook-cache.json 更新.bat",
            "start \"\" \"%~dp0runtime\\pythonw.exe\" \"%~dp0launch.py\"",
            "del /f /q \"%~dp0更新.bat\"",
            "",
        ]),
        encoding="gbk",
    )
    return bat


def watch_updates(httpd) -> None:
    import os
    import subprocess
    import time
    import zipfile

    if not (HERE / "runtime" / "pythonw.exe").is_file():
        return
    config = read_update_config()
    repo = str(config.get("repo") or "").strip()
    if not repo or "/" not in repo:
        return
    time.sleep(4)
    try:
        found = newer_release(repo, str(config.get("version") or "0"))
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return
    if not found:
        return
    UPDATE_STATUS["text"] = "正在更新到 " + found["version"] + "，马上重新打开"
    try:
        stage_release(found["url"])
        bat = write_updater()
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            cwd=str(HERE),
            creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile):
        UPDATE_STATUS["text"] = ""
        return
    time.sleep(1.5)
    httpd.shutdown()
    os._exit(0)


if __name__ == "__main__":
    import os

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"工作计划  http://{HOST}:{PORT}", flush=True)
    if not os.environ.get("WORK_PLAN_NO_BROWSER"):
        import webbrowser

        webbrowser.open(f"http://{HOST}:{PORT}")
    HOTKEYS.restart()
    import threading
    threading.Thread(target=watch_updates, args=(httpd,), daemon=True).start()
    start_pin_overlay()
    httpd.serve_forever()
