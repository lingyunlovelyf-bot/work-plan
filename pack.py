# Build a folder the other person can unzip and open, without installing Python.
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist" / "工作计划"
ZIP_PATH = HERE / "dist" / "工作计划.zip"
EMBED_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
EMBED_ZIP = HERE / "dist" / "python-embed.zip"
FILES = ("server.py", "index.html", "launch.py", "update.json", "README.md")

SETTINGS = {
    "vault": "",
    "browser": "edge",
    "name_keywords": ["接下来规划"],
    "global_hotkey": "Ctrl+Alt+K",
    "global_show": "Ctrl+Alt+J",
    "global_loose": "Ctrl+Alt+F",
    "free_scope": "today",
    "record_store": "timebook",
}

README = """解压后双击「打开工作计划.bat」。

第一次打开后，进设置，选择自己的笔记文件夹。
浏览器默认是 Edge。设置里可以改成 Chrome，或系统默认浏览器，也可以填别的 exe 路径。
时间书的地址和令牌在设置里自己填。这个压缩包里没有别人的令牌。
不需要安装 Python。
打开几秒后会检查 GitHub 上有没有新版本。有的话会自己换程序并重新打开，设置和令牌会留下。
"""

BAT = """@echo off
cd /d "%~dp0"
start "" "%~dp0runtime\\pythonw.exe" "%~dp0launch.py"
"""


def main() -> None:
    DIST.parent.mkdir(parents=True, exist_ok=True)
    if not EMBED_ZIP.exists() or EMBED_ZIP.stat().st_size < 1_000_000:
        print("download python", flush=True)
        urllib.request.urlretrieve(EMBED_URL, EMBED_ZIP)
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    runtime = DIST / "runtime"
    runtime.mkdir()
    with zipfile.ZipFile(EMBED_ZIP) as packed:
        packed.extractall(runtime)
    for name in FILES:
        shutil.copy2(HERE / name, DIST / name)
    (DIST / "settings.json").write_text(json.dumps(SETTINGS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DIST / "打开工作计划.bat").write_text(BAT, encoding="gbk")
    (DIST / "使用说明.txt").write_text(README, encoding="utf-8")
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as packed:
        for path in DIST.rglob("*"):
            if path.is_file():
                packed.write(path, Path("工作计划") / path.relative_to(DIST))
    print(ZIP_PATH, ZIP_PATH.stat().st_size)


if __name__ == "__main__":
    main()
