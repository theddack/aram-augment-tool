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
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

import webview

import lcu
import overlay

APP = "AramAugment"  # 사용자 폴더 이름 (설정 유지를 위해 바꾸지 않는다)
TITLE = "칼바람 증강 Addon"
APP_VERSION = "0.1.11"  # 릴리스 태그(v0.1.5)와 같아야 한다 (빌드 워크플로가 확인)
RELEASES_API = "https://api.github.com/repos/theddack/kalbaram-addon/releases/latest"
RELEASES_PAGE = "https://github.com/theddack/kalbaram-addon/releases/latest"


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])
CODE_FILES = ("index.html", "app.js", "scoring.js", "stats.js", "style.css", "changelog.html", "sources.html", "sources.js")


def resource_dir():
    # PyInstaller 로 묶이면 sys._MEIPASS, 아니면 이 파일 위치
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def atomic_write(path, text):
    """반쯤 쓰인 파일이 남지 않게: 임시 파일에 다 쓴 뒤 한 번에 바꿔 끼운다"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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


def data_patch(path):
    """data.json 의 (패치 버전 튜플, 데이터 형식 번호). 읽을 수 없으면 None (반쯤 쓰인 파일 등)"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return version_tuple(d.get("version", "")), int(d.get("format", 1))
    except Exception:
        return None


def prepare_web():
    """번들의 화면 파일은 매번 덮어쓴다. 데이터는 아래 규칙으로 복사한다."""
    src, dst = resource_dir() / "web", user_dir() / "web"
    dst.mkdir(parents=True, exist_ok=True)
    for name in CODE_FILES:
        shutil.copy2(src / name, dst / name)
    # 데이터: 없거나, 앱에 든 데이터와 같은 패치면 앱 것으로 교체 (앱 업데이트로 데이터 형식이 바뀔 수 있어서).
    # '데이터 갱신'으로 더 최신 패치를 받아둔 경우만 그대로 둔다.
    mine, bundled = dst / "data.json", src / "data.json"
    m, b = data_patch(mine), data_patch(bundled)
    # 교체: 없거나 · 읽을 수 없거나(갱신 중 꺼짐) · 앱에 든 것과 같은/더 새 패치이거나 · 데이터 형식이 옛것이면
    # 그대로: '데이터 갱신'으로 앱보다 새 패치를 받아 둔 경우만
    if m is None or (b and (m[0] <= b[0] or m[1] < b[1])):
        shutil.copy2(bundled, mine)
        shutil.copytree(src / "icons", dst / "icons", dirs_exist_ok=True)
    return dst


# ---------------------------------------------------------------- 게임 연동
# 롤 게임 중 데이터 API: 게임 중에만 내 PC(127.0.0.1:2999)에서 열린다. 자체 서명 인증서라 검증을 끈다.
LIVE_URL_DEFAULT = "https://127.0.0.1:2999/liveclientdata/allgamedata"


def live_url():
    """게임 중 데이터 주소. 시험용 ARAM_LIVE_URL 은 이 컴퓨터(127.0.0.1·localhost) 주소만 받는다.

    인증서 검증을 끄고 접속하므로(롤이 자체 서명 인증서를 씀) 다른 컴퓨터 주소로는 보내지 않는다.
    """
    url = os.environ.get("ARAM_LIVE_URL")
    if url:
        host = urllib.parse.urlsplit(url).hostname
        if host in ("127.0.0.1", "localhost", "::1"):
            return url
        print("ARAM_LIVE_URL 무시: 이 컴퓨터 주소가 아님 →", host)
    return LIVE_URL_DEFAULT


LIVE_URL = live_url()
LOG_KEEP = 30  # 게임 기록 파일은 최근 30개만 남긴다
LIVE_INTERVAL = 3  # 초 (게임 중·챔피언 선택 중)
IDLE_INTERVAL = 10  # 초 (게임 밖·롤 클라이언트 꺼짐: 자주 볼 필요가 없어서 줄인다)
SNAPSHOT_EVERY = 300  # 같은 게임에서 원본을 다시 저장하는 간격(초). 증강 필드 확인용
_insecure = ssl.create_default_context()
_insecure.check_hostname = False
_insecure.verify_mode = ssl.CERT_NONE
# 이 컴퓨터 주소는 프록시를 거치지 않게 (윈도우 시스템 프록시가 127.0.0.1 까지 가로채면 게임 연동이 조용히 실패한다)
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_insecure))


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
        "gold": ap.get("currentGold"),  # 지금 골드 ('지금 살 것' 안내)
        "stats": ap.get("championStats") or {},
        "gameMode": gd.get("gameMode"), "gameTime": gd.get("gameTime"),
        "augmentFields": find_keys(raw, r"augment"),
    }


NAME_KEYS = ("riotId", "riotIdGameName", "riotIdTagLine", "summonerName", "riotIdTagline")


def redact_names(raw):
    """기록 파일용: 소환사 이름(라이엇 ID)을 '플레이어0'(나)·'플레이어1'… 로 바꾼다. 같은 사람은 같은 번호.

    riotId("이름#태그")·summonerName 은 번호로, riotIdGameName 은 같은 번호로, 태그는 지운다.
    이벤트 목록의 KillerName 같은 다른 칸에 이름이 나와도 같은 번호로 바꾼다.
    """
    ap = raw.get("activePlayer") or {}
    order = [ap.get("riotId") or ap.get("summonerName")]
    order += [p.get("riotId") or p.get("summonerName") for p in raw.get("allPlayers") or []]
    names = {}
    for n in order:
        if n and n not in names:
            names[n] = f"플레이어{len(names)}"
    for full, label in list(names.items()):  # '이름#태그' 의 이름만 나오는 칸
        names.setdefault(full.split("#")[0], label)

    def walk(o, key=None):
        if isinstance(o, dict):
            return {k: walk(v, k) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, str):
            if key in ("riotIdTagLine", "riotIdTagline"):
                return ""
            if o in names:
                return names[o]
            if key in NAME_KEYS:
                return "플레이어?"
        return o
    return walk(raw)


def prune_logs(log_dir, keep):
    """오래된 게임 기록 지우기 (최근 keep 개만)"""
    files = sorted(log_dir.glob("game_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        try:
            p.unlink()
        except OSError:
            pass


class LiveWatcher(threading.Thread):
    """프로그램이 켜져 있는 동안 게임 중 데이터를 주기적으로 읽는다. 게임마다 원본을 logs/ 에 저장."""

    def __init__(self, log_dir):
        super().__init__(daemon=True)
        self.log_dir = log_dir
        self.state = {"inGame": False}
        self.enabled = True
        self._game_key = None
        self._last_snap = 0
        self.beat = time.time()  # 마지막으로 한 바퀴 돈 시각 (멈춤 진단용)

    def run(self):
        while True:
            self.beat = time.time()
            if self.enabled:
                self.poll()
            # 게임 중이면 3초마다, 아니면 10초마다 (게임 시작은 최대 10초 늦게 알아챈다)
            time.sleep(LIVE_INTERVAL if self.state.get("inGame") else IDLE_INTERVAL)

    def poll(self):
        try:
            with _local.open(LIVE_URL, timeout=2) as r:
                raw = json.loads(r.read())
            if not isinstance(raw, dict):
                raise ValueError("게임 데이터 형식이 아님")
        except Exception:
            # 한 번 끊긴 걸로 게임이 끝났다고 보지 않는다 (3번 연속 실패해야 끝, 기록 파일이 쓸데없이 늘지 않게)
            self._fails = getattr(self, "_fails", 0) + 1
            if self._fails >= 3:
                self.state = {"inGame": False}
                self._game_key = None
            return
        self._fails = 0
        try:
            st = parse_live(raw)
        except Exception as e:
            st = {"inGame": True, "error": str(e)}
        try:
            self.snapshot(raw, st)  # 기록 저장이 실패해도(디스크·백신 잠금) 감시는 계속
        except Exception as e:
            st["snapshotError"] = str(e)[:120]
        self.state = st  # 다 채운 뒤에 내보낸다 (화면이 읽는 도중 바뀌지 않게)

    def snapshot(self, raw, st):
        # 게임 시작 시각으로 게임을 구분 (gameTime 이 줄어들면 새 게임)
        t = st.get("gameTime") or 0
        key = (st.get("champion"), st.get("gameMode"))
        now = time.time()
        # 증강 칸이 새로 보이면 바로 저장 (게임 데이터에 고른 증강이 들어오는지 확인용, 5분을 기다리지 않게)
        aug = tuple(st.get("augmentFields") or ())
        if key != self._game_key or now - self._last_snap >= SNAPSHOT_EVERY or aug != getattr(self, "_last_aug", ()):
            self._last_aug = aug
            self.log_dir.mkdir(parents=True, exist_ok=True)
            name = time.strftime("game_%Y%m%d_%H%M%S") + f"_{st.get('champion') or 'unknown'}_{int(t)}s.json"
            (self.log_dir / name).write_text(json.dumps(redact_names(raw), ensure_ascii=False, indent=1), encoding="utf-8")
            prune_logs(self.log_dir, LOG_KEEP)
            self._game_key, self._last_snap = key, now
            st["savedSnapshot"] = name


# ---------------------------------------------------------------- 실측 (경기 결과)
# 게임이 끝나면 롤 클라이언트의 경기 결과(eog-stats-block)를 저장해, 계산 가정값을 실측과 비교한다.
# 형식은 라이엇이 문서로 보장하지 않는 클라이언트 내부 API 라서, 없는 칸은 건너뛰고 원본도 같이 남긴다.
MEASURE_KEEP = 100
EOG_STATS = {  # 경기 결과 칸 → (우리 이름, 단위)
    "TOTAL_DAMAGE_DEALT_TO_CHAMPIONS": "가한 피해(챔피언)",
    "PHYSICAL_DAMAGE_DEALT_TO_CHAMPIONS": "물리 피해(챔피언)",
    "MAGIC_DAMAGE_DEALT_TO_CHAMPIONS": "마법 피해(챔피언)",
    "TRUE_DAMAGE_DEALT_TO_CHAMPIONS": "고정 피해(챔피언)",
    "TOTAL_DAMAGE_TAKEN": "받은 피해",
    "TOTAL_HEAL": "회복",
    "TOTAL_DAMAGE_SHIELDED_ON_TEAMMATES": "아군 보호막",
    "TIME_CCING_OTHERS": "CC 점수",
    "TOTAL_TIME_CROWD_CONTROL_DEALT": "CC 건 시간(초)",
}
EOG_NAME_KEYS = ("summonerName", "riotIdGameName", "riotIdTagLine", "gameName", "tagLine", "puuid", "summonerId", "accountId")


def redact_eog(eog):
    """경기 결과의 이름·계정 칸을 지운다 (나는 '플레이어0', 나머지는 차례로)"""
    names = {}

    def walk(o):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if k in EOG_NAME_KEYS:
                    if isinstance(v, str) and v and k in ("summonerName", "riotIdGameName", "gameName"):
                        names.setdefault(v, f"플레이어{len(names)}")
                        out[k] = names[v]
                    else:
                        out[k] = ""
                else:
                    out[k] = walk(v)
            return out
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o
    lp = (eog or {}).get("localPlayer") or {}
    for k in ("summonerName", "riotIdGameName", "gameName"):
        if lp.get(k):
            names[lp[k]] = "플레이어0"
    return walk(eog)


def eog_summary(eog):
    """경기 결과 → 내 기록 한 줄 {분, 챔피언, 칸별 값}. 형식이 다르면 None"""
    if not isinstance(eog, dict):
        return None
    lp = eog.get("localPlayer") or {}
    stats = lp.get("stats") or {}
    length = eog.get("gameLength") or 0
    if not stats or length <= 60:
        return None
    return {"gameId": eog.get("gameId"), "mode": eog.get("gameMode"), "minutes": round(length / 60, 2),
            "championId": lp.get("championId"), "level": lp.get("level"),
            "stats": {k: stats[k] for k in EOG_STATS if isinstance(stats.get(k), (int, float))}}


def measure_summary(measure_dir):
    """저장한 경기 결과 평균 (분당). 화면 '계산 가정값' 옆 '내 실측'에 보인다"""
    rows = []
    for p in sorted(measure_dir.glob("eog_*.json")):
        try:
            s = eog_summary(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            s = None
        if s and s["stats"]:
            rows.append(s)
    out = {"games": len(rows), "perMinute": {}}
    for k, label in EOG_STATS.items():
        vals = [r["stats"][k] / r["minutes"] for r in rows if k in r["stats"]]
        if vals:
            out["perMinute"][k] = {"label": label, "avg": round(sum(vals) / len(vals), 1), "n": len(vals)}
    return out


class LcuWatcher(threading.Thread):
    """롤 클라이언트: 챔피언 선택 단계와 내 챔피언을 읽는다 (아이템 세트 등록 시점 판단용).
    게임이 끝나면 경기 결과를 실측 폴더에 저장한다."""

    def __init__(self, measure_dir=None):
        super().__init__(daemon=True)
        self.state = {"connected": False}
        self.client = None
        self.measure_dir = measure_dir
        self._saved_games = set()
        self.beat = time.time()

    def save_eog(self):
        """경기 결과 저장 (게임마다 한 번). 실패해도 다른 기능에는 영향 없게"""
        if not self.measure_dir:
            return
        try:
            eog = self.client.req("GET", "/lol-end-of-game/v1/eog-stats-block")
        except Exception:
            return
        gid = (eog or {}).get("gameId") if isinstance(eog, dict) else None
        if not gid or gid in self._saved_games or not eog_summary(eog):
            return
        self.measure_dir.mkdir(parents=True, exist_ok=True)
        path = self.measure_dir / f"eog_{int(gid)}.json"
        if not path.exists():
            path.write_text(json.dumps(redact_eog(eog), ensure_ascii=False), encoding="utf-8")
            files = sorted(self.measure_dir.glob("eog_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[MEASURE_KEEP:]:
                try:
                    old.unlink()
                except OSError:
                    pass
        self._saved_games.add(gid)

    def run(self):
        while True:
            self.beat = time.time()
            self.poll()
            # 챔피언 선택 중에만 3초마다 (상점 추천 등록 시점), 그 밖에는 10초마다
            busy = self.state.get("connected") and self.state.get("phase") in ("ChampSelect", "GameStart", "PreEndOfGame", "WaitingForStats")
            time.sleep(LIVE_INTERVAL if busy else IDLE_INTERVAL)

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
            if phase in ("EndOfGame", "PreEndOfGame", "WaitingForStats"):
                self.save_eog()
        except Exception as e:
            self.client = None  # 클라이언트를 껐다 켜면 포트·비밀번호가 바뀐다
            self.state = {"connected": False, "error": str(e)[:120]}


class Diag(threading.Thread):
    """멈춤 진단: 감시 기능마다 마지막으로 돈 시각을 보고, 오래 멈춘 게 있으면 그때 모든 스레드가 어느 줄에 있는지 diag.log 에 남긴다.
    게임 화면 표시 상태가 바뀔 때도 남긴다 (윈도우에서 왜 안 보이는지 확인용). 이름·사용자 폴더 경로는 남기지 않는다."""

    CHECK = 5  # 초
    MAX_BYTES = 512 * 1024  # 넘으면 diag.old.log 로 돌린다

    def __init__(self, path, beats, status=None):
        super().__init__(daemon=True)
        self.path = path
        self.beats = beats  # {이름: (마지막 시각을 돌려주는 함수, 멈춤으로 볼 초)}
        self.status = status  # 게임 화면 표시 상태를 돌려주는 함수
        self._stale = set()
        self._last_status = None

    def write(self, text):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self.MAX_BYTES:
                os.replace(self.path, self.path.with_name("diag.old.log"))
            home = str(Path.home())
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.replace(home, "~") + "\n")
        except OSError:
            pass

    def stacks(self):
        names = {t.ident: t.name for t in threading.enumerate()}
        out = []
        for ident, frame in sys._current_frames().items():
            out.append(f"  [{names.get(ident, ident)}]")
            out += ["    " + ln.rstrip().replace("\n", "\n    ") for ln in traceback.format_stack(frame)[-6:]]
        return "\n".join(out)

    def check(self, now=None):
        now = now or time.time()
        stale = {}
        for name, (get, limit) in self.beats.items():
            t = get()
            if t and now - t > limit:
                stale[name] = int(now - t)
        new = set(stale) - self._stale
        if new:
            self.write("멈춤: " + ", ".join(f"{n} {stale[n]}초째" for n in sorted(stale)) + "\n" + self.stacks())
        for n in sorted(self._stale - set(stale)):
            self.write(f"다시 움직임: {n}")
        self._stale = set(stale)
        if self.status:
            st = re.sub(r"\d+×\d+", "", str(self.status())).strip()  # 창 크기만 바뀐 건 남기지 않는다
            if st != self._last_status:
                self.write(f"게임 화면 표시: {st}")
                self._last_status = st

    def run(self):
        self.write(f"시작 v{APP_VERSION} · {sys.platform} {platform_name()} · pywebview {getattr(webview, '__version__', '?')}")
        while True:
            time.sleep(self.CHECK)
            try:
                self.check()
            except Exception as e:
                self.write(f"진단 오류: {str(e)[:120]}")


def platform_name():
    import platform
    return f"{platform.system()} {platform.release()} {platform.version()}"


class Settings:
    def __init__(self):
        self.path = user_dir() / "settings.json"
        try:
            self.v = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.v = {}

    def get(self, k, default=None):
        return self.v.get(k, default)

    _lock = threading.Lock()

    def set(self, **kw):
        # 화면 함수는 호출마다 다른 스레드에서 돈다 (위치 슬라이더는 연달아 부른다) → 잠그고 임시 파일에 쓴 뒤 바꿔 끼운다
        with self._lock:
            self.v.update(kw)
            atomic_write(self.path, json.dumps(self.v, ensure_ascii=False))


class Api:
    """화면(JS)에서 window.pywebview.api.* 로 부르는 함수들

    pywebview 는 이 객체의 밑줄 없는 속성을 재귀적으로 전부 JS 에 내보낸다 (속성 객체의 메서드까지).
    그래서 내부 객체(설정·창·롤 클라이언트 등)는 반드시 밑줄 이름으로 둔다. 밑줄 없는 건 아래 메서드뿐이어야 한다.
    (예전에는 settings.path.write_text, lcu.client.req 같은 함수까지 500개 넘게 노출됐다)
    """

    def __init__(self, settings, watcher=None, ov=None, lcu_watcher=None):
        self._settings = settings
        self._window = None
        self._watcher = watcher
        self._ov = ov
        self._lcu = lcu_watcher
        self._lock = threading.Lock()
        self._ui_beat = None  # 화면(JS)이 마지막으로 게임 상태를 물어본 시각 (3초마다). 멈추면 화면 쪽이 멈춘 것

    # ---- 게임 화면 표시 (윈도우)
    def set_overlay(self, payload):
        if self._ov:
            payload = dict(payload or {})
            payload["pos"] = self._settings.get("overlay_pos") or overlay.DEFAULT_POS
            self._ov.update(payload)
        return True

    def set_overlay_enabled(self, on):
        if self._ov:
            self._ov.set_enabled(on)
        self._settings.set(overlay=bool(on))
        return bool(on)

    def set_overlay_pos(self, y, spread):
        self._settings.set(overlay_pos={"y": float(y), "spread": float(spread)})
        return True

    def set_overlay_preview(self, on):
        if self._ov:
            self._ov.set_preview(on)
        return bool(on)

    def overlay_status(self):
        return {"supported": overlay.IS_WINDOWS, "status": self._ov.status if self._ov else "꺼짐"}

    # ---- 롤 클라이언트 (아이템 세트)
    def get_lcu(self):
        return self._lcu.state if self._lcu else {"connected": False}

    def write_item_set(self, champion_key, title, blocks):
        """상점 추천 칸에 우리 빌드 등록. 게임 시작 전(챔피언 선택 중)에 해야 상점에 반영된다"""
        if not self._lcu or not self._lcu.client:
            return {"ok": False, "error": "롤 클라이언트에 연결되지 않았어요"}
        try:
            champion_key, title, blocks = lcu.clean_item_set(champion_key, title, blocks)
        except ValueError as e:
            return {"ok": False, "error": f"잘못된 아이템 세트: {e}"}
        try:
            n = self._lcu.client.write_item_set(champion_key, title, blocks, backup_dir=str(user_dir() / "itemset_backup"))
            return {"ok": True, "blocks": n}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    def get_live(self):
        """게임 연동 상태 (화면이 몇 초마다 부른다)"""
        self._ui_beat = time.time()
        if not self._watcher:
            return {"inGame": False, "disabled": True}
        return {**self._watcher.state, "enabled": self._watcher.enabled, "logDir": str(self._watcher.log_dir),
                "overlayStatus": self._ov.status if self._ov else None}

    def set_live(self, on):
        if self._watcher:
            self._watcher.enabled = bool(on)
            self._settings.set(live=bool(on))
        return bool(on)

    def get_state(self):
        return {"onTop": bool(self._settings.get("on_top", False)), "desktop": True,
                "live": bool(self._settings.get("live", True)),
                "overlay": bool(self._settings.get("overlay", True)), "overlaySupported": overlay.IS_WINDOWS,
                "overlayPos": self._settings.get("overlay_pos") or overlay.DEFAULT_POS,
                "itemSets": bool(self._settings.get("item_sets", True))}

    def set_item_sets(self, on):
        self._settings.set(item_sets=bool(on))
        return bool(on)

    def set_on_top(self, on):
        self._window.on_top = bool(on)
        self._settings.set(on_top=bool(on))
        return bool(on)

    def set_mini(self, on):
        """작게 보기: 창 크기를 줄이고(게임 옆에 띄우기), 끄면 원래 크기로"""
        if on:
            if not self._settings.get("mini"):
                self._settings.set(full_size=[self._window.width, self._window.height])
            w, h = self._settings.get("mini_size") or (440, 760)
            self._window.resize(int(w), int(h))
        else:
            w, h = self._settings.get("full_size") or (1400, 900)
            self._window.resize(int(w), int(h))
        self._settings.set(mini=bool(on))
        return bool(on)

    def get_measurements(self):
        """내 실측: 저장한 경기 결과의 분당 평균 (데이터 출처 → 계산 가정값 옆에 보임)"""
        return measure_summary(user_dir() / "measure")

    def check_app_update(self):
        """새 프로그램 버전이 있는지만 알려 준다 (받기는 공식 릴리스 페이지에서 직접).

        프로그램이 스스로 파일을 받아 바꾸지 않는다: 코드 서명이 없어서, 자동 교체는 변조된 파일을 실행하는 통로가 된다.
        """
        try:
            req = urllib.request.Request(RELEASES_API, headers={"User-Agent": APP, "Accept": "application/vnd.github+json"})
            rel = json.loads(urllib.request.urlopen(req, timeout=8).read())
            latest = str(rel.get("tag_name", "")).lstrip("v")
        except Exception as e:
            return {"current": APP_VERSION, "latest": None, "error": str(e)[:120]}
        newer = bool(latest) and version_tuple(latest) > version_tuple(APP_VERSION)
        return {"current": APP_VERSION, "latest": latest, "newer": newer, "url": RELEASES_PAGE}

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
            self._window.evaluate_js("location.reload()")
        return {"ok": ok, "log": buf.getvalue()[-2000:]}


def main():
    overlay.set_dpi_aware()  # 창을 만들기 전에 (화면 배율에서 좌표 맞추기)
    settings = Settings()
    web = prepare_web()
    watcher = LiveWatcher(user_dir() / "logs")
    watcher.enabled = bool(settings.get("live", True))
    watcher.name = "게임 연동"
    watcher.start()
    ov = overlay.Overlay()
    ov.name = "게임 화면 표시"
    ov.enabled = bool(settings.get("overlay", True))
    ov.start()
    lw = LcuWatcher(user_dir() / "measure")
    lw.name = "롤 클라이언트"
    lw.start()
    api = Api(settings, watcher, ov, lw)
    geo = settings.get("geometry") or {}
    window = webview.create_window(
        TITLE, url=str(web / "index.html"), js_api=api,
        # 위치(x, y)는 창이 뜬 뒤에 옮긴다. 만들 때 넘기면 맥에서 화면이 정해지기 전에 옮기려다 멈춘다
        width=geo.get("w", 1400), height=geo.get("h", 900),
        min_size=(420, 500), on_top=bool(settings.get("on_top", False)),
    )
    api._window = window
    # 기준 초: 게임 연동 최대 10초 쉬고 2초 대기, 롤 클라이언트 10초 + 요청 3번 x 5초, 표시 창 최대 1초,
    # 화면은 3초마다 묻지만 창을 내려 두면 브라우저가 타이머를 늦추므로 넉넉히
    Diag(user_dir() / "logs" / "diag.log", {
        "게임 연동": (lambda: watcher.beat, 30),
        "롤 클라이언트": (lambda: lw.beat, 60),
        "게임 화면 표시": (lambda: ov.beat, 15),
        "애드온 화면": (lambda: api._ui_beat, 90),
    }, status=lambda: ov.status).start()

    def on_closing():
        try:
            settings.set(geometry={"w": window.width, "h": window.height, "x": window.x, "y": window.y})
        except Exception:
            pass

    window.events.closing += on_closing

    def on_shown():
        # 저장된 위치가 지금 연결된 화면 안일 때만 옮긴다 (보조 모니터를 뺐으면 기본 위치 그대로)
        x, y = geo.get("x"), geo.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return
        try:
            inside = any(sc.x - 40 <= x < sc.x + sc.width - 80 and sc.y - 40 <= y < sc.y + sc.height - 80
                         for sc in webview.screens)
            if inside:
                window.move(int(x), int(y))
        except Exception:
            pass

    window.events.shown += on_shown
    # http_server: 로컬 파일을 http 로 서빙해야 화면의 fetch("data.json") 이 동작한다
    webview.start(http_server=True, debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
