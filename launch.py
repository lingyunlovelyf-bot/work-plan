# Open the work-plan window. Start the local server if it is not already up.
import json
import os
import socket
import subprocess
import sys
import time
import ctypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8876/"
PROFILE = Path(os.environ.get("LOCALAPPDATA") or ".") / "work-plan-app"
POP_PROFILE = Path(os.environ.get("LOCALAPPDATA") or ".") / "work-plan-pop"


def listening() -> bool:
    probe = socket.socket()
    probe.settimeout(0.3)
    try:
        probe.connect(("127.0.0.1", 8876))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def read_choice() -> str:
    try:
        saved = json.loads((HERE / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    text = str(saved.get("browser") or "edge").strip()
    return text or "edge"


def browser_candidates(kind: str) -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA") or "")
    program = Path(os.environ.get("PROGRAMFILES") or r"C:\Program Files")
    program86 = Path(os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)")
    if kind == "chrome":
        return [
            program / "Google/Chrome/Application/chrome.exe",
            program86 / "Google/Chrome/Application/chrome.exe",
            local / "Google/Chrome/Application/chrome.exe",
        ]
    return [
        program86 / "Microsoft/Edge/Application/msedge.exe",
        program / "Microsoft/Edge/Application/msedge.exe",
        local / "Microsoft/Edge/Application/msedge.exe",
    ]


def browser_exe(choice: str) -> Path | None:
    if choice in ("edge", "chrome"):
        for path in browser_candidates(choice):
            if path.is_file():
                return path
        return None
    path = Path(choice)
    return path if path.is_file() else None


def matching_hwnds(match) -> list:
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
        if match(title):
            found.append(hwnd)
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return found


def work_area() -> tuple[int, int, int, int]:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    if not user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
        return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return rect.left, rect.top, max(rect.right - rect.left, 1), max(rect.bottom - rect.top, 1)


def center_of(width: int, height: int) -> tuple[int, int]:
    left, top, area_w, area_h = work_area()
    return left + max(0, (area_w - width) // 2), top + max(0, (area_h - height) // 2)


def center_hwnd(hwnd) -> None:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 200 or height < 160:
        width, height = 560, 440
    x, y = center_of(width, height)
    user32.SetWindowPos(hwnd, 0, x, y, width, height, 0x0004)


def near_center(hwnd) -> bool:
    from ctypes import wintypes

    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    width = max(rect.right - rect.left, 560)
    height = max(rect.bottom - rect.top, 440)
    x, y = center_of(width, height)
    return abs(rect.left - x) <= 24 and abs(rect.top - y) <= 24


def remember_pop_center() -> None:
    path = POP_PROFILE / "Default" / "Preferences"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    apps = saved.get("browser", {}).get("app_window_placement")
    if not isinstance(apps, dict):
        return
    left, top, area_w, area_h = work_area()
    changed = False

    def walk(node) -> None:
        nonlocal changed
        if not isinstance(node, dict):
            return
        if {"left", "top", "right", "bottom", "maximized"} <= node.keys():
            width = int(node["right"]) - int(node["left"])
            height = int(node["bottom"]) - int(node["top"])
            if width < 200:
                width = 560
            if height < 160:
                height = 440
            x = left + max(0, (area_w - width) // 2)
            y = top + max(0, (area_h - height) // 2)
            node["left"] = x
            node["top"] = y
            node["right"] = x + width
            node["bottom"] = y + height
            node["maximized"] = False
            node["work_area_left"] = left
            node["work_area_top"] = top
            node["work_area_right"] = left + area_w
            node["work_area_bottom"] = top + area_h
            changed = True
            return
        for value in node.values():
            walk(value)

    walk(apps)
    if not changed:
        return
    path.write_text(json.dumps(saved, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def focus_hwnd(hwnd) -> None:
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)


def focus_matching(match) -> bool:
    found = matching_hwnds(match)
    if not found:
        return False
    focus_hwnd(found[-1])
    return True


def place_pop() -> bool:
    found = matching_hwnds(lambda title: title in ("自由", "创建"))
    if not found:
        return False
    hwnd = found[-1]
    center_hwnd(hwnd)
    focus_hwnd(hwnd)
    return True


def focus_window() -> bool:
    return focus_matching(lambda title: title == "工作计划" or title.endswith("— 工作计划") or title.endswith("- 工作计划"))


def open_default(url: str) -> None:
    os.startfile(url)


def open_app(url: str, profile: Path, size: str, position: str = "") -> None:
    choice = read_choice()
    if choice == "default":
        open_default(url)
        return
    exe = browser_exe(choice if choice in ("edge", "chrome") else choice)
    if exe is None and choice in ("edge", "chrome"):
        exe = browser_exe("chrome" if choice == "edge" else "edge")
    if exe is None:
        open_default(url)
        return
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            str(exe),
            "--app=" + url,
            "--user-data-dir=" + str(profile),
            "--window-size=" + size,
            "--no-first-run",
        ] + (["--window-position=" + position] if position else [])
    )


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    if not listening():
        env = os.environ.copy()
        env["WORK_PLAN_NO_BROWSER"] = "1"
        subprocess.Popen([sys.executable, str(HERE / "server.py")], cwd=str(HERE), env=env)
        for _ in range(50):
            if listening():
                break
            time.sleep(0.15)
    if kind in ("loose", "create"):
        if place_pop():
            return
        remember_pop_center()
        x, y = center_of(560, 440)
        open_app(URL + "?pop=" + kind, POP_PROFILE, "560,440", str(x) + "," + str(y))
        for _ in range(16):
            time.sleep(0.12)
            found = matching_hwnds(lambda title: title in ("自由", "创建"))
            if not found:
                continue
            hwnd = found[-1]
            if not near_center(hwnd):
                center_hwnd(hwnd)
        place_pop()
        return
    if focus_window():
        return
    open_app(URL, PROFILE, "1120,780")


if __name__ == "__main__":
    main()
