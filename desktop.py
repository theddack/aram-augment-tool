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
import re
import shutil
import ssl
import sys
import threading
import time
import urllib.request
from pathlib import Path

import webview

import lcu
import overlay

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
    """번들의 화면 파일은 매번 덮어쓴다. 데이터는 아래 규칙으로 복사한다."""
    src, dst = resource_dir() / "web", user_dir() / "web"
    dst.mkdir(parents=True, exist_ok=True)
    for name in CODE_FILES:
        shutil.copy2(src / name, dst / name)
    # 데이터: 없거나, 앱에 든 데이터와 같은 패치면 앱 것으로 교체 (앱 업데이트로 데이터 형식이 바뀔 수 있어서).
    # '데이터 갱신'으로 더 최신 패치를 받아둔 경우만 그대로 둔다.
    mine, bundled = dst / "data.json", src / "data.json"
    if not mine.exists() or data_version(mine) == data_version(bundled):
        shutil.copy2(bundled, mine)
        shutil.copytree(src / "icons", dst / "icons", dirs_exist_ok=True)
    return dst


# ---------------------------------------------------------------- 게임 연동
# 롤 게임 중 데이터 API: 게임 중에만 내 PC(127.0.0.1:2999)에서 열린다. 자체 서명 인증서라 검증을 끈다.
LIVE_URL = os.environ.get("ARAM_LIVE_URL", "https://127.0.0.1:2999/liveclientdata/allgamedata")
LIVE_INTERVAL = 3  # 초
SNAPSHOT_EVERY = 300  # 같은 게임에서 원본을 다시 저장하는 간격(초). 증강 필드 확인용
_insecure = ssl.create_default_context()
_insecure.check_hostname = False
_insecure.verify_mode = ssl.CERT_NONE


def find_keys(obj, pattern, path=""):
    """응답 안에서 이름이 pattern 에 맞는 키 경로 목록 (증강 필드가 숨어 있는지 찾기)"""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if re.search(pattern, k, re.I):
                found.append(p)
            found += find_keys(v, pattern, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:12]):
            found += find_keys(v, pattern, f"{path}[{i}]")
    return found


def parse_live(raw):
    """게임 중 데이터 → 화면에 필요한 것만"""
    ap = raw.get("activePlayer") or {}
    me = ap.get("riotId") or ap.get("summonerName")
    players = raw.get("allPlayers") or []
    mine = next((p for p in players if me and (p.get("riotId") == me or p.get("summonerName") == me)), None)
    champ_id = None
    if mine:
        m = re.search(r"displayname_(\w+)", mine.get("rawChampionName") or "", re.I)
        champ_id = m.group(1) if m else None
    gd = raw.get("gameData") or {}
    return {
        "inGame": True,
        "champion": champ_id,
        "championName": mine.get("championName") if mine else None,
        "level": ap.get("level"),
        "items": [str(i.get("itemID")) for i in (mine.get("items") if mine else []) if i.get("itemID")],
        "stats": ap.get("championStats") or {},
        "gameMode": gd.get("gameMode"), "gameTime": gd.get("gameTime"),
        "augmentFields": find_keys(raw, r"augment"),
    }


class LiveWatcher(threading.Thread):
    """프로그램이 켜져 있는 동안 게임 중 데이터를 주기적으로 읽는다. 게임마다 원본을 logs/ 에 저장."""

    def __init__(self, log_dir):
        super().__init__(daemon=True)
        self.log_dir = log_dir
        self.state = {"inGame": False}
        self.enabled = True
        self._game_key = None
        self._last_snap = 0

    def run(self):
        while True:
            if self.enabled:
                self.poll()
            time.sleep(LIVE_INTERVAL)

    def poll(self):
        try:
            with urllib.request.urlopen(LIVE_URL, timeout=2, context=_insecure) as r:
                raw = json.loads(r.read())
        except Exception:
            self.state = {"inGame": False}
            self._game_key = None
            return
        try:
            st = parse_live(raw)
        except Exception as e:
            st = {"inGame": True, "error": str(e)}
        self.state = st
        self.snapshot(raw, st)

    def snapshot(self, raw, st):
        # 게임 시작 시각으로 게임을 구분 (gameTime 이 줄어들면 새 게임)
        t = st.get("gameTime") or 0
        key = (st.get("champion"), st.get("gameMode"))
        now = time.time()
        if key != self._game_key or now - self._last_snap >= SNAPSHOT_EVERY:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            name = time.strftime("game_%Y%m%d_%H%M%S") + f"_{st.get('champion') or 'unknown'}_{int(t)}s.json"
            (self.log_dir / name).write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
            self._game_key, self._last_snap = key, now
            st["savedSnapshot"] = name


class LcuWatcher(threading.Thread):
    """롤 클라이언트: 챔피언 선택 단계와 내 챔피언을 읽는다 (아이템 세트 등록 시점 판단용)"""

    def __init__(self):
        super().__init__(daemon=True)
        self.state = {"connected": False}
        self.client = None

    def run(self):
        while True:
            self.poll()
            time.sleep(LIVE_INTERVAL)

    def poll(self):
        try:
            if not self.client:
                path = os.environ.get("ARAM_LCU_LOCKFILE") or lcu.find_lockfile(overlay.game_install_dirs())
                if not path:
                    self.state = {"connected": False}
                    return
                self.client = lcu.Lcu(path)
            phase = self.client.phase()
            champ = self.client.current_champion() if phase in ("ChampSelect", "InProgress", "GameStart") else 0
            self.state = {"connected": True, "phase": phase, "championKey": champ}
        except Exception as e:
            self.client = None  # 클라이언트를 껐다 켜면 포트·비밀번호가 바뀐다
            self.state = {"connected": False, "error": str(e)[:120]}


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

    def __init__(self, settings, watcher=None, ov=None, lcu_watcher=None):
        self.settings = settings
        self.window = None
        self.watcher = watcher
        self.ov = ov
        self.lcu = lcu_watcher
        self._lock = threading.Lock()

    # ---- 게임 화면 표시 (윈도우)
    def set_overlay(self, payload):
        if self.ov:
            payload = dict(payload or {})
            payload["pos"] = self.settings.get("overlay_pos") or overlay.DEFAULT_POS
            self.ov.update(payload)
        return True

    def set_overlay_enabled(self, on):
        if self.ov:
            self.ov.set_enabled(on)
        self.settings.set(overlay=bool(on))
        return bool(on)

    def set_overlay_pos(self, y, spread):
        self.settings.set(overlay_pos={"y": float(y), "spread": float(spread)})
        return True

    def set_overlay_preview(self, on):
        if self.ov:
            self.ov.set_preview(on)
        return bool(on)

    def overlay_status(self):
        return {"supported": overlay.IS_WINDOWS, "status": self.ov.status if self.ov else "꺼짐"}

    # ---- 롤 클라이언트 (아이템 세트)
    def get_lcu(self):
        return self.lcu.state if self.lcu else {"connected": False}

    def write_item_set(self, champion_key, title, blocks):
        """상점 추천 칸에 우리 빌드 등록. 게임 시작 전(챔피언 선택 중)에 해야 상점에 반영된다"""
        if not self.lcu or not self.lcu.client:
            return {"ok": False, "error": "롤 클라이언트에 연결되지 않았어요"}
        try:
            n = self.lcu.client.write_item_set(champion_key, title, blocks)
            return {"ok": True, "blocks": n}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def get_live(self):
        """게임 연동 상태 (화면이 몇 초마다 부른다)"""
        if not self.watcher:
            return {"inGame": False, "disabled": True}
        return {**self.watcher.state, "enabled": self.watcher.enabled, "logDir": str(self.watcher.log_dir)}

    def set_live(self, on):
        if self.watcher:
            self.watcher.enabled = bool(on)
            self.settings.set(live=bool(on))
        return bool(on)

    def get_state(self):
        return {"onTop": bool(self.settings.get("on_top", False)), "desktop": True,
                "live": bool(self.settings.get("live", True)),
                "overlay": bool(self.settings.get("overlay", True)), "overlaySupported": overlay.IS_WINDOWS,
                "overlayPos": self.settings.get("overlay_pos") or overlay.DEFAULT_POS,
                "itemSets": bool(self.settings.get("item_sets", True))}

    def set_item_sets(self, on):
        self.settings.set(item_sets=bool(on))
        return bool(on)

    def set_on_top(self, on):
        self.window.on_top = bool(on)
        self.settings.set(on_top=bool(on))
        return bool(on)

    def set_mini(self, on):
        """작게 보기: 창 크기를 줄이고(게임 옆에 띄우기), 끄면 원래 크기로"""
        if on:
            if not self.settings.get("mini"):
                self.settings.set(full_size=[self.window.width, self.window.height])
            w, h = self.settings.get("mini_size") or (440, 760)
            self.window.resize(int(w), int(h))
        else:
            w, h = self.settings.get("full_size") or (1400, 900)
            self.window.resize(int(w), int(h))
        self.settings.set(mini=bool(on))
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
    overlay.set_dpi_aware()  # 창을 만들기 전에 (화면 배율에서 좌표 맞추기)
    settings = Settings()
    web = prepare_web()
    watcher = LiveWatcher(user_dir() / "logs")
    watcher.enabled = bool(settings.get("live", True))
    watcher.start()
    ov = overlay.Overlay()
    ov.enabled = bool(settings.get("overlay", True))
    ov.start()
    lw = LcuWatcher()
    lw.start()
    api = Api(settings, watcher, ov, lw)
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
