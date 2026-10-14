#!/usr/bin/env python3
"""칼바람 증강 계산기 데스크톱 프로그램 (맥·윈도우 공용).

web/ 화면을 pywebview 창으로 띄운다. 데이터(data.json, 아이콘)는 사용자 폴더에 두어서
프로그램을 다시 빌드하지 않아도 '데이터 갱신' 버튼으로 패치 데이터를 받을 수 있다.

  사용자 폴더: 맥 ~/Library/Application Support/AramAugment
              윈도우 %APPDATA%\\AramAugment
"""
import contextlib
import io
import json
import os
import shutil
import sys
import threading
import urllib.request
from pathlib import Path

import webview

APP = "AramAugment"
TITLE = "칼바람 증강 계산기"
CODE_FILES = ("index.html", "app.js", "scoring.js", "stats.js", "style.css")


def resource_dir():
    # PyInstaller 로 묶이면 sys._MEIPASS, 아니면 이 파일 위치
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def user_dir():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".local" / "share"
    d = base / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_version(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("dataVersion", "")
    except Exception:
        return ""


def prepare_web():
    """번들의 화면 파일은 매번 덮어쓰고, 데이터는 사용자 폴더에 없을 때만 복사한다."""
    src, dst = resource_dir() / "web", user_dir() / "web"
    dst.mkdir(parents=True, exist_ok=True)
    for name in CODE_FILES:
        shutil.copy2(src / name, dst / name)
    if not (dst / "data.json").exists():
        shutil.copy2(src / "data.json", dst / "data.json")
        shutil.copytree(src / "icons", dst / "icons", dirs_exist_ok=True)
    return dst


class Settings:
    def __init__(self):
        self.path = user_dir() / "settings.json"
        try:
            self.v = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.v = {}

    def get(self, k, default=None):
        return self.v.get(k, default)

    def set(self, **kw):
        self.v.update(kw)
        self.path.write_text(json.dumps(self.v, ensure_ascii=False), encoding="utf-8")


class Api:
    """화면(JS)에서 window.pywebview.api.* 로 부르는 함수들"""

    def __init__(self, settings):
        self.settings = settings
        self.window = None
        self._lock = threading.Lock()

    def get_state(self):
        return {"onTop": bool(self.settings.get("on_top", False)), "desktop": True}

    def set_on_top(self, on):
        self.window.on_top = bool(on)
        self.settings.set(on_top=bool(on))
        return bool(on)

    def check_update(self):
        """설치된 데이터 패치 버전과 최신 패치 버전 비교"""
        cur = data_version(user_dir() / "web" / "data.json")
        try:
            req = urllib.request.Request("https://raw.communitydragon.org/latest/content-metadata.json",
                                         headers={"User-Agent": APP})
            latest = json.loads(urllib.request.urlopen(req, timeout=15).read())["version"]
        except Exception as e:
            return {"current": cur, "latest": None, "error": str(e)}
        return {"current": cur, "latest": latest, "outdated": cur != latest}

    def update_data(self):
        """최신 데이터를 사용자 폴더로 다시 받는다 (수 분 걸림). 끝나면 화면을 새로 고친다."""
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "log": "이미 갱신 중이에요."}
        buf = io.StringIO()
        try:
            import build_data
            build_data.configure(out=user_dir() / "web", cache=user_dir() / "cache", refresh=True)
            with contextlib.redirect_stdout(buf):
                build_data.main()
            ok = True
        except Exception as e:
            buf.write(f"\n실패: {e}")
            ok = False
        finally:
            self._lock.release()
        if ok:
            self.window.evaluate_js("location.reload()")
        return {"ok": ok, "log": buf.getvalue()[-2000:]}


def main():
    settings = Settings()
    web = prepare_web()
    api = Api(settings)
    geo = settings.get("geometry") or {}
    window = webview.create_window(
        TITLE, url=str(web / "index.html"), js_api=api,
        width=geo.get("w", 1400), height=geo.get("h", 900), x=geo.get("x"), y=geo.get("y"),
        min_size=(420, 500), on_top=bool(settings.get("on_top", False)),
    )
    api.window = window

    def on_closing():
        try:
            settings.set(geometry={"w": window.width, "h": window.height, "x": window.x, "y": window.y})
        except Exception:
            pass

    window.events.closing += on_closing
    # http_server: 로컬 파일을 http 로 서빙해야 화면의 fetch("data.json") 이 동작한다
    webview.start(http_server=True, debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
