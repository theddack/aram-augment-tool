#!/usr/bin/env python3
"""칼바람 증강 계산기 데스크톱 프로그램 (맥·윈도우 공용).

web/ 화면을 pywebview 창으로 띄운다. 데이터(data.json, 아이콘)는 데이터 폴더에 두어서
프로그램을 다시 빌드하지 않아도 '데이터 갱신' 버튼으로 패치 데이터를 받을 수 있다.

  데이터 폴더: 윈도우 프로그램 폴더\\data (쓸 수 없으면 %APPDATA%\\AramAugment)
              맥 ~/Library/Application Support/AramAugment
"""
import os

# 계산 라이브러리(numpy 의 OpenBLAS)는 기본으로 CPU 코어 수만큼 스레드를 띄우고, 그 스레드들은 보통 우선순위로 돈다.
# 증강 칸 아이콘 비교 때 모든 코어를 잠깐씩 다 써서 게임 프레임이 끊길 수 있다 (2026-10-16 '간혹 프리징').
# 비교 한 번은 작은 행렬 곱이라 스레드 1개로도 걸리는 시간이 같다 → numpy 를 불러오기 전에 1개로 묶는다
for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import contextlib
import json
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

import webview

import augocr
import gamelog
import lcu
import overlay

APP = "AramAugment"  # 사용자 폴더 이름 (설정 유지를 위해 바꾸지 않는다)
TITLE = "칼바람 증강 Addon"
APP_VERSION = "0.1.33"  # 릴리스 태그(v0.1.5)와 같아야 한다 (빌드 워크플로가 확인)
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


_user_dir = None


def program_dir():
    """프로그램이 있는 폴더 (exe 로 실행하면 exe 옆, 소스로 실행하면 desktop.py 옆)"""
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def user_dir():
    """설정·기록·받은 데이터를 두는 곳.

    윈도우: 프로그램 폴더 안 data\\ (사용자 요청 2026-10-14: AppData 에 두지 말고 실행한 곳에).
            그 폴더에 쓸 수 없으면(Program Files 등) 예전처럼 %APPDATA%\\AramAugment.
    맥: 앱 안은 쓸 수 없어서 ~/Library/Application Support/AramAugment
    """
    global _user_dir
    if _user_dir:
        return _user_dir
    old = None
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP
    elif os.name == "nt":
        old = Path(os.environ.get("APPDATA", Path.home())) / APP
        d = program_dir() / "data"
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / ".write_test").write_text("", encoding="utf-8")
            (d / ".write_test").unlink()
        except OSError:
            d, old = old, None
    else:
        d = Path.home() / ".local" / "share" / APP
    d.mkdir(parents=True, exist_ok=True)
    if old and old.is_dir() and old != d:
        move_old_data(old, d)
    _user_dir = d
    return d


def move_old_data(old, new):
    """예전 위치(%APPDATA%\\AramAugment)의 설정·기록·받은 데이터를 새 위치로 옮기고 예전 폴더를 지운다.
    새 위치에 이미 있는 건 덮어쓰지 않는다."""
    for item in old.iterdir():
        target = new / item.name
        try:
            if not target.exists():
                shutil.move(str(item), str(target))
            elif item.is_dir():
                for sub in item.rglob("*"):
                    t = target / sub.relative_to(item)
                    if sub.is_file() and not t.exists():
                        t.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(sub), str(t))
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
        except OSError:
            pass
    try:
        shutil.rmtree(old)
    except OSError:
        pass


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
STALL_EXTRA = 7  # 초. 게임 연동 한 바퀴가 이만큼 더 늦으면 '멈춤'으로 기록 (읽기 제한 2초 + 여유)
RES_EVERY = 30  # 초. 게임 중 자원 기록 간격
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
    def cid(p):
        m = re.search(r"displayname_(\w+)", p.get("rawChampionName") or "", re.I)
        return m.group(1) if m else None
    champ_id = cid(mine) if mine else None
    # 적 챔피언: 팀이 다른 사람 (적 구성에 맞춘 추천용. 연습 모드처럼 혼자면 빈 목록)
    foes = [p for p in players if mine and p.get("team") and p.get("team") != mine.get("team") and cid(p)]
    enemies = [cid(p) for p in foes]
    # 적이 산 아이템·레벨 (게임 안 Tab 점수판에 보이는 정보): 상대 능력치·관통·치유 감소 판단에 쓴다
    enemy_builds = [{"champion": cid(p), "level": p.get("level"),
                     "items": [str(i.get("itemID")) for i in p.get("items") or [] if i.get("itemID")],
                     "augments": spell_augments(p)} for p in foes]
    gd = raw.get("gameData") or {}
    return {
        "inGame": True,
        "champion": champ_id,
        "championName": mine.get("championName") if mine else None,
        "level": ap.get("level"),
        "dead": bool(mine.get("isDead")) if mine else False,  # 죽어 있는 동안 증강을 고르는 일이 많다 (증강 인식을 자주)
        "items": [str(i.get("itemID")) for i in (mine.get("items") if mine else []) if i.get("itemID")],
        "gold": ap.get("currentGold"),  # 지금 골드 ('지금 살 것' 안내)
        "stats": ap.get("championStats") or {},
        "gameMode": gd.get("gameMode"), "gameTime": gd.get("gameTime"),
        "augmentFields": find_keys(raw, r"augment"),
        "enemies": enemies,
        "enemyBuilds": enemy_builds,
        "spellAugments": spell_augments(mine) if mine else [],
    }


def spell_augments(player):
    """소환사 주문 칸에 보이는 증강 (공식 게임 데이터에서 증강이 보이는 유일한 곳).
    소환사 주문을 주거나 바꾸는 증강만 보인다 (2026-10-15 베인 판: 적 모데카이저 'Augment_GrowthSpurt', 자야 'Augment_ARAM_FlashySpell').
    → ['GrowthSpurt', 'ARAM_FlashySpell'] (증강 id 와 맞추는 건 화면 쪽)"""
    out = []
    for sp in ((player or {}).get("summonerSpells") or {}).values():
        m = re.search(r"Augment_(\w+?)_DisplayName", (sp or {}).get("rawDisplayName") or "")
        if m:
            out.append(m.group(1))
    return out


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
            now = time.time()
            gap, self.beat = now - self.beat, now
            if self.enabled:
                self.watch_resources(now, gap)
                self.poll()
            # 게임 중이면 3초마다, 아니면 10초마다 (게임 시작은 최대 10초 늦게 알아챈다)
            time.sleep(LIVE_INTERVAL if self.state.get("inGame") else IDLE_INTERVAL)

    def watch_resources(self, now, gap):
        """게임 중 자원 기록 (2026-10-15 실게임 '중간에 컴퓨터가 멈춤': 그땐 기록이 없어 원인을 못 봤다).
        - stall: 이 감시가 한 바퀴(3초+읽기 2초)보다 훨씬 늦게 돌아왔다 = 컴퓨터(또는 이 프로그램)가 그동안 멈췄다
        - res: RES_EVERY 초마다 애드온 CPU(코어 몇 개분)·메모리, 컴퓨터 전체 메모리 사용률"""
        rec = getattr(self, "rec", None)
        if not (rec and rec.active and self.state.get("inGame")):
            self._res = None
            return
        if gap > LIVE_INTERVAL + STALL_EXTRA:
            rec.event("stall", {"gap": round(gap, 1), "res": resource_sample()})
        last = getattr(self, "_res", None)
        if last and now - last[0] < RES_EVERY:
            return
        smp = resource_sample()
        if last and smp.get("cpu") is not None and last[1].get("cpu") is not None:
            rec.event("res", {"cores": round((smp["cpu"] - last[1]["cpu"]) / max(1e-6, now - last[0]), 2),
                              **{k: v for k, v in smp.items() if k != "cpu"}})
        self._res = (now, smp)

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
                if self.state.get("inGame") and getattr(self, "on_game_end", None):
                    self.on_game_end()  # 한 판 기록 마무리 (zip)
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
        if getattr(self, "rec", None) and self.rec.active and self._items_changed(st):
            # 산 아이템 (바뀔 때만). 레벨·골드·게임 시간은 그때 값을 함께
            self.rec.event("items", {"items": st.get("items"), "level": st.get("level"), "gold": round(st.get("gold") or 0),
                                     "gameTime": round(st.get("gameTime") or 0)})
        self.state = st  # 다 채운 뒤에 내보낸다 (화면이 읽는 도중 바뀌지 않게)

    def _items_changed(self, st):
        cur = tuple(st.get("items") or ())
        if cur != getattr(self, "_last_items", None):
            self._last_items = cur
            return True
        return False

    def snapshot(self, raw, st):
        # 게임 시작 시각으로 게임을 구분 (gameTime 이 줄어들면 새 게임)
        t = st.get("gameTime") or 0
        key = (st.get("champion"), st.get("gameMode"))
        now = time.time()
        # 증강 칸이 새로 보이면 바로 저장 (게임 데이터에 고른 증강이 들어오는지 확인용, 5분을 기다리지 않게)
        aug = tuple(st.get("augmentFields") or ())
        if key != self._game_key and getattr(self, "on_new_game", None):
            self.on_new_game(st)  # 새 게임: 증강 칸 읽기 초기화, 한 판 기록 시작
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

    def __init__(self, path, beats, status=None, aug=None):
        super().__init__(daemon=True, name="진단")
        self.in_game = None  # 게임 중인지 돌려주는 함수
        self.aug = aug  # 증강 인식 상태를 돌려주는 함수 (선택지·고른 증강·인식 상태가 바뀌면 남긴다)
        self._last_aug = {}
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

    names_of = staticmethod(lambda ids: [augment_id_names().get(i, i) for i in ids])

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
        a = (self.aug() if self.aug else None) or {}
        if a.get("status") != self._last_aug.get("status"):
            self.write(f"증강 인식: {a.get('status')}" + (f" ({a.get('ms')}ms)" if a.get("ms") else ""))
        if a.get("seq") != self._last_aug.get("seq") and a.get("offers"):
            self.write("증강 선택지: " + " / ".join(n or "?" for n in a["offers"]))
        if a.get("near") and a.get("near") != self._last_aug.get("near"):
            self.write(f"증강 인식 못 맞춘 글자: {a['near']}")
        if bool(a.get("shop")) != bool(self._last_aug.get("shop")):
            self.write("상점 창: " + ("열림 → 옆에 상자 표시" if a.get("shop") else "닫힘"))
        if a.get("hudPicked") != self._last_aug.get("hudPicked") or a.get("hudUnsure") != self._last_aug.get("hudUnsure"):
            names = self.names_of(a.get("hudPicked") or [])
            unsure = " / ".join(" 또는 ".join(g) for g in a.get("hudUnsure") or [])
            if names or unsure:
                self.write("증강 칸: " + ", ".join(names) + (f" (못 정함: {unsure})" if unsure else ""))
        if a.get("pickSeq") != self._last_aug.get("pickSeq") and a.get("pickSeq"):
            if a.get("by") == "hud":
                self.write(f"고른 증강: {a.get('picked')} (증강 칸 그림으로 확인"
                           + (f" · 클릭 짐작 {a['replaces']} 를 고침)" if a.get("replaces") else ")"))
            else:
                self.write(f"고른 증강: {a.get('picked') or '못 정함 (화면에서 직접 선택)'}")
        self._last_aug = a

    FREEZE_AFTER = 45  # 초. 진단 스레드조차 이만큼 못 돌면 프로그램 전체가 멈춘 것
    TRIM_EVERY = 120  # 초. 안 쓰는 메모리를 윈도우에 돌려주는 간격 (메모리 최소화)

    def run(self):
        gb = total_ram_gb()
        self.write(f"시작 v{APP_VERSION} · {sys.platform} {platform_name()} · pywebview {getattr(webview, '__version__', '?')}"
                   + (f" · 메모리 {gb:.0f}GB" if gb else "") 
                   + (f" · CPU 상한 게임 중 코어 {CPU_CAP['game']}개분" if getattr(self, "cpu_cap", None) else " · CPU 상한 없음"))
        # 프로그램 전체가 멈추면(파이썬 잠금 교착) 이 스레드도 못 돌아서 위의 '멈춤' 기록을 못 남긴다 (v0.1.12 실제 PC:
        # 20:21:06 뒤로 한 줄도 없음). faulthandler 는 파이썬 잠금 없이 도는 감시라, 다시 못 맞추면 모든 스레드 위치를 쓴다
        import faulthandler
        fpath = self.path.with_name("freeze_stack.txt")
        try:
            fh = open(fpath, "a", encoding="utf-8")
        except OSError:
            fh = None
        seen = fpath.stat().st_size if fpath.exists() else 0
        while True:
            if fh:
                faulthandler.dump_traceback_later(self.FREEZE_AFTER, repeat=False, file=fh)  # 다시 부르면 앞 예약은 취소된다
            time.sleep(self.CHECK)
            self._n = getattr(self, "_n", 0) + 1
            # 게임 중엔 하지 않는다: 메모리를 통째로 내보내면 다시 쓸 때 페이지를 읽어 오느라 2분마다 잠깐 끊길 수 있다
            # (게임이 끝나면 따로 한 번 돌려준다)
            if self._n % (self.TRIM_EVERY // self.CHECK) == 0 and not (self.in_game and self.in_game()):
                trim_memory()
            if getattr(self, "cpu_cap", None) and self._n % 6 == 1:  # 30초마다 새로 뜬 화면 엔진 프로세스도 상한에
                try:
                    added = cap_children()
                    if added:
                        self.write(f"CPU 상한 묶음에 화면 엔진 프로세스 {added}개 추가")
                        if self.in_game and self.in_game():
                            child_priority(True)  # 게임 중 새로 뜬 화면 엔진 프로세스도 우선순위를 내린다
                except Exception as e:
                    self.write(f"CPU 상한 묶기 실패: {str(e)[:100]}")
            try:
                self.check()
                size = fpath.stat().st_size if fpath.exists() else 0
                if size > seen:
                    self.write(f"전체 멈춤이 있었어요 ({self.FREEZE_AFTER}초 넘게) → 그때 위치는 logs/freeze_stack.txt")
                    seen = size
            except Exception as e:
                self.write(f"진단 오류: {str(e)[:120]}")


def platform_name():
    import platform
    return f"{platform.system()} {platform.release()} {platform.version()}"


def augment_id_names():
    """{증강 id: 이름} (진단 기록용)"""
    try:
        return {a["id"]: a["name"] for a in json.loads((user_dir() / "web" / "data.json").read_text(encoding="utf-8"))["augments"]}
    except (OSError, ValueError, KeyError):
        return {}


_aug_names = {"mtime": None, "names": []}


def augment_names():
    """증강 이름 목록 (데이터를 갱신하면 다시 읽는다). 증강 인식이 화면 글자와 맞춰 본다"""
    path = user_dir() / "web" / "data.json"
    try:
        m = path.stat().st_mtime
        if m != _aug_names["mtime"]:
            # 영어 이름도 같이: 한국어 글자 인식이 없는 윈도우(검증용 가상 PC 등)에서도 맞출 수 있게
            names = [(a["name"], a.get("enName")) for a in json.loads(path.read_text(encoding="utf-8")).get("augments", [])]
            _aug_names.update(mtime=m, names=names)
    except (OSError, ValueError):
        pass
    return _aug_names["names"]


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


TIMER_AREA = (0.0, 0.25, 0.25, 0.47)  # 기록용: 쿨타임 아이콘 둘레 게임 화면 (게임 창 비율: 왼, 위, 오른, 아래)


class Api:
    """화면(JS)에서 window.pywebview.api.* 로 부르는 함수들

    pywebview 는 이 객체의 밑줄 없는 속성을 재귀적으로 전부 JS 에 내보낸다 (속성 객체의 메서드까지).
    그래서 내부 객체(설정·창·롤 클라이언트 등)는 반드시 밑줄 이름으로 둔다. 밑줄 없는 건 아래 메서드뿐이어야 한다.
    (예전에는 settings.path.write_text, lcu.client.req 같은 함수까지 500개 넘게 노출됐다)
    """

    def __init__(self, settings, watcher=None, ov=None, lcu_watcher=None, reader=None):
        self._settings = settings
        self._reader = reader
        self._rec = None  # 한 판 기록 (gamelog.GameRecorder)
        self._update = None  # 데이터 받기 별도 실행 (subprocess.Popen)
        self._window = None
        self._watcher = watcher
        self._ov = ov
        self._calc_ms, self._calc_seq = [], None  # 화면 쪽 추천 다시 계산 시간(ms) (한 판 기록 요약)
        self._statcheck_key = None  # 계산 vs 게임 실제 능력치: 레벨·아이템·증강이 바뀔 때만 기록
        self._lcu = lcu_watcher
        self._lock = threading.Lock()
        self._ui_beat = None  # 화면(JS)이 마지막으로 게임 상태를 물어본 시각 (3초마다). 멈추면 화면 쪽이 멈춘 것

    # ---- 게임 화면 표시 (윈도우)
    def set_overlay(self, payload):
        if self._ov:
            payload = dict(payload or {})
            payload["pos"] = self._settings.get("overlay_pos") or overlay.DEFAULT_POS
            # 증강 인식이 카드 위치를 읽었으면 그 자리에 붙인다 (짐작한 비율·블리츠 같은 다른 표시와 안 겹치게)
            # 상점 창이 열려 있으면 그 옆에 상자를 (평소엔 상자를 안 띄운다: 게임을 가린다)
            payload["shop"] = self._reader.state.get("shop") if self._reader else None
            # 상점 인식이 안 돼도 죽어 있을 때·시작 직후엔 상자를 띄운다 (화면이 정해 보낸다: box = "dead"·"start")
            if not payload["shop"] and payload.get("box"):
                payload["shop"] = payload["box"]
            cards = (self._reader.state.get("pos") if self._reader else None)
            if cards:
                payload["pos"] = {**payload["pos"], "xs": cards["xs"], "y": cards["nameY"] + overlay.BELOW_NAME,
                                  "bottom": cards["nameY"] + overlay.CARD_BOTTOM}
            self._ov.update(payload)
        if self._reader is not None and payload.get("shopMarks") is not None:
            self._reader.shop_targets = list(payload.get("shopMarks") or [])  # 상점 안 아이템 자리 찾기(시험) 대상
        ms = payload.get("calcMs")
        if isinstance(ms, (int, float)) and len(self._calc_ms) < 5000 and payload.get("calcSeq") != self._calc_seq:
            self._calc_seq = payload.get("calcSeq")  # 같은 계산 결과를 여러 번 보내도 한 번만 센다
            self._calc_ms.append(float(ms))
        sc = payload.get("statCheck")
        if self._rec and isinstance(sc, dict):
            # 계산한 능력치 vs 게임이 알려 준 실제 능력치 (계산이 틀린 곳 찾기: 데이터·룬·중첩).
            # 실제 값은 싸움 중 버프로 매번 흔들리니 레벨·아이템·증강이 바뀔 때만 남긴다
            key = (str(getattr(self._rec, "dir", "")), json.dumps([sc.get("level"), sc.get("items"), sc.get("augs")]))
            if key != self._statcheck_key:
                self._statcheck_key = key
                self._rec.event("statcheck", sc)
        if self._rec:
            # 보여 준 것 (카드 평가·상자 글) — 추천이 상황에 맞았는지 나중에 보려고. 같은 내용은 한 번만
            cards = [c and f"{c.get('name')}: {c.get('label')}" for c in (payload.get("cards") or [])]
            # 카드 밑 이유(연계·재미 픽·쓰는 법 문장)도: 체감과 맞았는지 나중에 대조
            reasons = [c and (c.get("reason") or "")[:120] for c in (payload.get("cards") or [])]
            # box = 상자를 띄운 이유 (shop·dead·start), src = 화면의 어느 갱신이 보냈는지 (카드가 깜박이면 누가 지웠는지)
            self._rec.event("shown", {"cards": cards, "reasons": reasons, "box": payload.get("hud"),
                                      "shop": bool(self._reader and self._reader.state.get("shop")),
                                      "boxWhy": payload.get("box"), "src": payload.get("src")}, dedupe=True)
            # 증강 쿨타임: 언제 골랐다고/발동했다고 봤는지 (실제 발동과 대조). 시각은 컴퓨터 시각(초) 그대로 —
            # 이 판 기록의 start.wall 을 빼면 기록 시각(t)과 맞는다. 바뀔 때만 남는다 (남은 초를 남기면 매번 달라진다)
            # 상대 아이템 반영: 적이 산 아이템으로 잡은 상대 능력치와 조언 (바뀔 때만)
            if payload.get("enemy"):
                self._rec.event("enemy", payload["enemy"], dedupe=True)
            timers = [{"name": t.get("name"), "kind": t.get("kind"), "picked": round(t.get("base") or 0),
                       "readyAt": round(t.get("readyAt") or 0), "hp": t.get("trigHp")} for t in (payload.get("timers") or [])]
            if timers:
                self._rec.event("timers", {"timers": timers}, dedupe=True)
                # 쿨타임 아이콘 자리 밑의 게임 화면을 판마다 한 번 남긴다 (아이콘이 무엇을 가리는지 보려고).
                # 화면 조각에는 표시 창이 안 찍힌다 (게임 화면 그대로). 채팅(왼쪽 아래)과는 떨어진 자리
                if self._reader and getattr(self, "_timer_snap", None) != self._rec.dir:
                    self._timer_snap = self._rec.dir
                    threading.Timer(2.0, self._snap_timer_area).start()
        return True

    def _snap_timer_area(self):
        try:
            rect, front = self._reader.game_rect()
            if not rect or not front or not self._rec:
                return
            gx, gy, gw, gh = rect
            l, t, r, b = TIMER_AREA
            x0, y0, w, h = int(gw * l), int(gh * t), int(gw * (r - l)), int(gh * (b - t))
            img = self._rec.image("timerarea", augocr._Capture().grab(gx + x0, gy + y0, w, h), w, h)
            self._rec.event("timerarea", {"image": img, "box": list(TIMER_AREA), "window": [gw, gh],
                                          "icon": overlay.timer_size(gh), "at": [overlay.TIMER_X, overlay.TIMER_Y]})
        except Exception as e:  # 기록용이라 실패해도 다른 기능엔 영향 없게
            if self._rec:
                self._rec.event("timerarea", {"error": str(e)[:100]})

    def calc_summary(self):
        """한 판 동안 화면 쪽 추천 다시 계산 시간 {n, avg, p95, max} (ms). 부르면 비운다"""
        v, self._calc_ms = sorted(self._calc_ms), []
        if not v:
            return None
        return {"n": len(v), "avg": round(sum(v) / len(v)), "p95": round(v[min(len(v) - 1, int(len(v) * 0.95))]), "max": round(v[-1])}

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
                "overlayStatus": self._ov.status if self._ov else None,
                "aug": self._reader.state if self._reader else None}

    def set_live(self, on):
        if self._watcher:
            self._watcher.enabled = bool(on)
            self._settings.set(live=bool(on))
        if self._reader:
            self._reader.enabled = bool(on)  # 증강 인식도 게임 연동을 따른다
        return bool(on)

    def get_state(self):
        return {"desktop": True,
                "live": bool(self._settings.get("live", True)),
                "overlay": bool(self._settings.get("overlay", True)), "overlaySupported": overlay.IS_WINDOWS,
                "overlayPos": self._settings.get("overlay_pos") or overlay.DEFAULT_POS,
                "itemSets": bool(self._settings.get("item_sets", True))}

    def set_item_sets(self, on):
        self._settings.set(item_sets=bool(on))
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
        """최신 데이터를 사용자 폴더로 다시 받기 시작한다 (수 분 걸림). 진행은 update_status 로 본다.

        별도 프로그램(같은 exe 를 --update-data 로)에서 받는다: 이 프로그램 안에서 받으면 35MB 파일 해석·대량 계산이
        파이썬을 몇 분 동안 붙잡아 창이 '응답 없음'이 된다 (창의 모든 동작이 파이썬을 거친다).
        """
        with self._lock:
            if self._update and self._update.poll() is None:
                return {"ok": True, "running": True}
            log = user_dir() / "logs" / "update.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("", encoding="utf-8")
            cmd = [sys.executable, "--update-data"] if getattr(sys, "frozen", False) \
                else [sys.executable, os.path.abspath(__file__), "--update-data"]
            flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW (검은 창 안 뜨게)
            try:
                self._update = subprocess.Popen(cmd, creationflags=flags, stdin=subprocess.DEVNULL,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as e:
                return {"ok": False, "log": f"시작 실패: {e}"}
            self._update_t0 = time.time()
            return {"ok": True, "running": True}

    def update_status(self):
        """데이터 받기 진행: 지금 단계 한 줄, 걸린 시간. 끝나면 성공 여부와 기록 끝부분"""
        p = self._update
        if not p:
            return {"running": False}
        try:
            text = (user_dir() / "logs" / "update.log").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        steps = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith(" ")]
        out = {"running": p.poll() is None, "step": update_step(steps[-1] if steps else "시작하는 중"),
               "seconds": int(time.time() - getattr(self, "_update_t0", time.time()))}
        if not out["running"]:
            out.update(ok=p.returncode == 0, log=text[-2000:])
        return out


def update_step(line):
    """받기 기록 한 줄 → 버튼에 보일 짧은 글 ('… 받는 중...' → '… 받는 중')"""
    return re.sub(r"\s*\(.*?\)|\.{2,}|…", "", line).strip()[:40]


def run_update_data():
    """--update-data: 데이터만 받고 끝나는 별도 실행. 진행 기록은 logs/update.log (화면이 읽어 간다)"""
    import build_data
    log = user_dir() / "logs" / "update.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8", buffering=1) as f, contextlib.redirect_stdout(f):
        try:
            build_data.configure(out=user_dir() / "web", cache=user_dir() / "cache", refresh=True)
            build_data.main()
            print("완료")
            return 0
        except Exception as e:
            print(f"실패: {e}")
            return 1

def lower_priority():
    """윈도우: 이 프로그램을 '낮은 우선순위'로 → 게임이 늘 CPU 를 먼저 쓴다 (렉 지적 2026-10-14)"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
    except Exception:
        pass


# 화면 엔진(WebView2) 설정: 애드온 화면은 글자·아이콘뿐이라 GPU 가 필요 없다. GPU 프로세스를 끄면 메모리가 줄고
# 게임이 쓰는 GPU 를 나눠 쓰지 않는다. 화면 프로세스도 하나로 (메모리 최소화 요청 2026-10-14)
WEBVIEW2_ARGS = ("--disable-gpu --disable-gpu-compositing --in-process-gpu --renderer-process-limit=1 "
                 "--enable-low-end-device-mode --disable-background-networking --disable-component-update "
                 "--js-flags=--optimize-for-size --enable-features=NetworkServiceInProcess2 "
                 "--disable-features=msSmartScreenProtection,Translate,AutofillServerCommunication,MediaRouter,"
                 "SpareRendererForSitePerProcess,BackForwardCache")


def resource_sample():
    """윈도우: {cpu: 애드온 전체(작업 개체: 화면 엔진 포함, 없으면 이 프로세스)가 지금까지 쓴 CPU 초,
    mem: 이 프로세스 메모리(MB), sysMem: 컴퓨터 전체 메모리 사용률(%), availGB: 남은 메모리(GB)}. 못 읽은 값은 빠진다"""
    out = {}
    if sys.platform != "win32":
        return out
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = wintypes.HANDLE
        if _job:
            class Acct(ctypes.Structure):
                _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                            ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                            ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                            ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]
            a = Acct()
            k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
            if k.QueryInformationJobObject(_job, 1, ctypes.byref(a), ctypes.sizeof(a), None):  # JobObjectBasicAccountingInformation
                out["cpu"] = (a.TotalUserTime + a.TotalKernelTime) / 1e7
                out["procs"] = a.ActiveProcesses
        if "cpu" not in out:
            c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
            if k.GetProcessTimes(k.GetCurrentProcess(), ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
                ft = lambda f: (f.dwHighDateTime << 32 | f.dwLowDateTime) / 1e7
                out["cpu"] = ft(kt) + ft(ut)

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pm = PMC()
        pm.cb = ctypes.sizeof(PMC)
        k.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if k.K32GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(pm), pm.cb):
            out["mem"] = round(pm.WorkingSetSize / 1024 ** 2)

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32), ("ullTotalPhys", ctypes.c_uint64),
                        ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if k.GlobalMemoryStatusEx(ctypes.byref(m)):
            out["sysMem"] = m.dwMemoryLoad
            out["availGB"] = round(m.ullAvailPhys / 1024 ** 3, 1)
    except Exception as e:
        out["error"] = str(e)[:80]
    return out


def total_ram_gb():
    """윈도우 전체 메모리 (GB). 모르면 None"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32), ("ullTotalPhys", ctypes.c_uint64),
                        ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys / 1024 ** 3
    except Exception:
        return None


# 모드는 하나 (2026-10-15 사용자 요청: 가벼운/전체로 나누지 말고 최적화로). 애드온 전체가 쓸 수 있는 CPU (코어 몇 개분):
# 게임 중엔 게임 몫을 뺏지 않게 반 개분. 게임 밖(켤 때·챔피언 선택 상점 추천 계산)은 묶지 않는다:
# 상한이 차면 윈도우가 애드온 창 스레드까지 통째로 세워서 켤 때 창이 최대 3초 굳었다 (가상 PC, 1개분으로 묶었을 때도)
CPU_CAP = {"idle": float(os.cpu_count() or 4), "game": 0.5}
_job = None  # 작업 개체 핸들 (프로그램이 끝날 때까지 들고 있어야 상한이 유지된다)
_job_cores = None


def _set_rate(job, cores):
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]

    class CpuRate(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]
    ncpu = os.cpu_count() or 4
    rate = max(100, min(10000, int(cores * 10000 / ncpu)))  # 전체 CPU 의 1/100 % 단위
    info = CpuRate(0x1 | 0x4, rate)  # JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | HARD_CAP
    return bool(k.SetInformationJobObject(job, 15, ctypes.byref(info), ctypes.sizeof(info)))  # JobObjectCpuRateControlInformation


def cap_cpu(cores):
    """윈도우: 이 프로그램과 자식(화면 엔진 WebView2)이 함께 쓸 수 있는 CPU 를 코어 cores 개분으로 묶는다 (작업 개체 하드 캡).
    무거운 계산이 순간적으로 몰려도 게임 몫을 뺏지 않는다 (CPU 순간 최대 지적 2026-10-14). 실패해도 다른 기능엔 영향 없음.
    이미 묶여 있으면 상한만 바꾼다 (게임 시작·끝)"""
    global _job, _job_cores
    if sys.platform != "win32":
        return False
    try:
        if _job:
            if cores != _job_cores and _set_rate(_job, cores):
                _job_cores = cores
            return True
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.GetCurrentProcess.restype = wintypes.HANDLE
        job = k.CreateJobObjectW(None, None)
        if not job or not _set_rate(job, cores):
            return False
        if not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
            return False
        _job, _job_cores = job, cores
        return True
    except Exception:
        return False


def child_pids(root_pid):
    """윈도우: root_pid 의 자손 프로세스 PID 목록 (Toolhelp 스냅샷)"""
    import ctypes
    from ctypes import wintypes

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    parent = {}
    try:
        e = PE()
        e.dwSize = ctypes.sizeof(PE)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            parent[e.th32ProcessID] = e.th32ParentProcessID
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    out, frontier = [], {root_pid}
    while frontier:
        nxt = {pid for pid, pp in parent.items() if pp in frontier and pid not in out and pid != root_pid}
        out += sorted(nxt)
        frontier = nxt
    return out


def child_priority(low):
    """윈도우: 화면 엔진(WebView2 자손 프로세스)의 우선순위. 게임 중엔 '보통 아래'로 내려서
    같은 CPU 상한(0.5개분) 안에서 증강 이름 읽기가 먼저 돌게 한다. 게임 시작 직후 화면 다시 계산이 몰리면
    글자 인식 한 번이 11초 걸려 선택 화면을 15초 늦게 읽었다 (2026-10-15 윈도우 측정). 바꾼 개수를 돌려준다"""
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    n = 0
    for pid in child_pids(os.getpid()):
        h = k.OpenProcess(0x0200, False, pid)  # PROCESS_SET_INFORMATION
        if not h:
            continue
        try:
            if k.SetPriorityClass(h, 0x4000 if low else 0x20):  # BELOW_NORMAL / NORMAL
                n += 1
        finally:
            k.CloseHandle(h)
    return n


def cap_children():
    """화면 엔진(WebView2)이 상한 묶음 밖에서 따로 뜨는 경우가 있어서 (측정: 상한 0.5개분인데 순간 77~93%),
    뜬 자손 프로세스를 찾아 같은 작업 개체에 넣는다. 넣은 개수를 돌려준다"""
    if sys.platform != "win32" or not _job:
        return 0
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    n = 0
    for pid in child_pids(os.getpid()):
        h = k.OpenProcess(0x0100 | 0x0001 | 0x1000, False, pid)  # SET_QUOTA | TERMINATE | QUERY_LIMITED
        if not h:
            continue
        try:
            inside = wintypes.BOOL()
            if k.IsProcessInJob(h, _job, ctypes.byref(inside)) and inside.value:
                continue
            if k.AssignProcessToJobObject(_job, h):
                n += 1
        finally:
            k.CloseHandle(h)
    return n


def trim_memory():
    """윈도우: 안 쓰는 메모리를 돌려준다 (한 번 크게 쓰고 끝나는 작업 뒤·게임이 끝날 때)"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import gc
        gc.collect()
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
        k.SetProcessWorkingSetSize(k.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
    except Exception:
        pass


def quiet_resource_hooks():
    """윈도우: pywebview 가 화면 엔진의 모든 파일 요청·응답마다 파이썬을 부르는 것을 끈다 (우리는 안 쓴다).
    켤 때 아이콘 수백 개를 받는 동안 창 스레드가 요청마다 파이썬 차례를 기다려 창이 몇 초 굳었다 (가상 PC 최대 3초)"""
    if sys.platform != "win32":
        return False
    try:
        from webview.platforms import edgechromium as ec
        noop = lambda self, sender, args: None  # noqa: E731
        ec.EdgeChrome.on_web_resource_request = noop
        ec.EdgeChrome.on_web_resource_response = noop
        orig = ec.EdgeChrome.on_webview_ready

        def ready(self, sender, args):
            orig(self, sender, args)
            try:
                sender.CoreWebView2.RemoveWebResourceRequestedFilter("*", ec.CoreWebView2WebResourceContext.All)
            except Exception:
                pass
            try:
                sender.CoreWebView2.WebResourceResponseReceived -= self.on_web_resource_response
            except Exception:
                pass
        ec.EdgeChrome.on_webview_ready = ready
        return True
    except Exception:
        return False


def _safe(fn):
    try:
        fn()
    except Exception:
        pass


class WebMemory:
    """화면 엔진(WebView2) 메모리 절약: 게임 중이거나 창을 내려 두면 '적게 쓰기'로 (캐시를 비우고 덜 붙잡는다).
    게임 밖에서 창을 보고 있을 땐 보통으로 되돌린다 (적게 쓰기는 화면 반응이 조금 느려질 수 있다)"""

    def __init__(self, log=None):
        self.window = None
        self.in_game = False
        self.minimized = False
        self.applied = None
        self.log = log

    def set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, bool(v))
        self.apply()

    def apply(self):
        low = self.in_game or self.minimized
        if sys.platform != "win32" or low == self.applied:
            return
        wv = getattr(getattr(self.window, "native", None), "webview", None)
        if wv is None:
            return  # 창이 아직 안 떴다 → 뜨면 다시 (loaded)
        try:
            from System import Action
            from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel as Level

            def run():
                try:
                    if wv.CoreWebView2 is not None:
                        wv.CoreWebView2.MemoryUsageTargetLevel = Level.Low if low else Level.Normal
                except Exception as e:
                    if self.log:
                        self.log(f"화면 엔진 메모리 절약 실패: {str(e)[:100]}")

            # 창 스레드에서 바꿔야 한다. 기다리지 않고 맡긴다 (Invoke 로 기다리면 창 스레드와 서로 기다릴 수 있다)
            wv.BeginInvoke(Action(run))
            self.applied = low
            if self.log:
                self.log("화면 엔진 메모리: " + ("적게 쓰기" if low else "보통") + (" (게임 중)" if self.in_game else " (창 내림)" if low else ""))
        except Exception as e:
            if self.log:
                self.log(f"화면 엔진 메모리 절약 못 씀: {str(e)[:100]}")


def main():
    lower_priority()
    if sys.platform == "win32":
        os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", WEBVIEW2_ARGS)
    if "--update-data" in sys.argv:
        sys.exit(run_update_data())
    overlay.set_dpi_aware()  # 창을 만들기 전에 (화면 배율에서 좌표 맞추기)
    settings = Settings()
    capped = cap_cpu(CPU_CAP["idle"])  # 화면 엔진을 띄우기 전에 (자식 프로세스도 같이 묶이게)
    web = prepare_web()
    watcher = LiveWatcher(user_dir() / "logs")
    watcher.enabled = bool(settings.get("live", True))
    watcher.name = "게임 연동"
    watcher.start()
    ov = overlay.Overlay()
    ov.name = "게임 화면 표시"
    ov.icon_dir = str(user_dir() / "web" / "icons" / "augment")  # 쿨타임 아이콘
    ov.enabled = bool(settings.get("overlay", True))
    ov.start()
    lw = LcuWatcher(user_dir() / "measure")
    lw.name = "롤 클라이언트"
    lw.start()
    reader = None
    if overlay.IS_WINDOWS:
        win = overlay._Win()
        reader = augocr.Reader(win.game_rect, augment_names)
        reader.enabled = watcher.enabled
        # 증강 칸 아이콘 읽기: 받은 데이터의 증강 아이콘과 비교 (고른 증강의 정답).
        # 그 판 선택 화면에 떴던 증강하고만 비교해 가볍다 (numpy·Pillow 는 게임에서 처음 읽을 때 불러온다)
        reader.icon_dir = str(user_dir() / "web" / "icons" / "augment")
        try:
            reader._id_names = {a["id"]: a["name"] for a in json.loads((user_dir() / "web" / "data.json").read_text(encoding="utf-8"))["augments"]}
        except (OSError, ValueError, KeyError):
            reader._id_names = {}
        # 증강 칸 아이콘 크기를 해상도(게임 창 높이)별로 기억: 다음 게임부터 13가지 크기로 다 찾지 않는다
        reader.size_hint = lambda gh: (settings.get("hud_size") or {}).get(str(gh))
        reader.size_save = lambda gh, sz: settings.set(hud_size={**(settings.get("hud_size") or {}), str(gh): sz})
        reader.on_heavy = trim_memory
        reader.live_fn = lambda: watcher.state  # 레벨·죽음 → 증강 선택 화면을 자주 읽을 때
        watcher.on_new_game = lambda st: reader.new_game()
        reader.start()
    api = Api(settings, watcher, ov, lw, reader)
    # 한 판 기록: logs/games/날짜_챔피언/ (끝나면 zip). 빗나간 인식·추천을 고치려고 읽은 화면 조각까지 남긴다
    rec = gamelog.GameRecorder(user_dir() / "logs" / "games")
    api._rec = rec
    watcher.rec = rec
    if reader:
        reader.rec = rec
    prev_new = getattr(watcher, "on_new_game", None)
    webmem = WebMemory()

    def on_new_game(st):
        if prev_new:
            prev_new(st)
        webmem.set(in_game=True)
        if capped:
            cap_cpu(CPU_CAP["game"])
        try:
            diag.write(f"화면 엔진 우선순위: 낮춤 ({child_priority(True)}개 프로세스, 게임 중 증강 읽기 먼저)")
        except Exception as e:
            diag.write(f"화면 엔진 우선순위 바꾸기 실패: {str(e)[:100]}")
        rec.start(st.get("champion"), {"mode": st.get("gameMode"), "enemies": st.get("enemies"), "version": APP_VERSION,
                                       "wall": round(time.time(), 1)})  # 기록 시각(t)의 기준 (윈도우 재현 도구가 지연을 잰다)

    watcher.on_new_game = on_new_game

    def on_game_end():
        if reader:
            # 한 판 동안 글자 인식·증강 칸 처리 시간 (평균·p95·최대): 실제 게임에서 느린 구간을 찾으려고
            perf = reader.perf_summary()
            # 표시 창 그리기·화면 쪽 추천 계산 시간도 같이 (게임 끊김 원인을 숫자로 보려고)
            draw = ov.perf_summary() if ov else {}
            calc = api.calc_summary()
            perf.update({f"표시 {k}": v for k, v in draw.items()})
            if calc:
                perf["추천 계산"] = calc
            rec.event("perf", {"ms": perf})
            if perf:
                diag.write("한 판 처리 시간(ms): " + " · ".join(f"{k} {v['n']}번 평균 {v['avg']} p95 {v['p95']} 최대 {v['max']}" for k, v in perf.items()))
        rec.finish()
        webmem.set(in_game=False)
        if capped:
            cap_cpu(CPU_CAP["idle"])
        try:
            child_priority(False)
        except Exception:
            pass
        trim_memory()

    watcher.on_game_end = on_game_end
    geo = settings.get("geometry") or {}
    window = webview.create_window(
        TITLE, url=str(web / "index.html"), js_api=api,
        # 위치(x, y)는 창이 뜬 뒤에 옮긴다. 만들 때 넘기면 맥에서 화면이 정해지기 전에 옮기려다 멈춘다
        width=geo.get("w", 1400), height=geo.get("h", 900),
        # '항상 위'는 없앴다: 켠 판에서만 프로그램 전체가 멈췄다 (2026-10-14 두 번, 사용자 요청으로 기능 삭제)
        min_size=(420, 500),
    )
    api._window = window
    webmem.window = window
    if reader:
        def poke():
            # 기다리지 않고 따로: 증강 인식이 창 스레드를 기다리며 서지 않게
            threading.Thread(target=lambda: _safe(lambda: window.run_js("window.livePoke && window.livePoke()")),
                             daemon=True, name="화면 깨우기").start()
        reader.on_change = poke
    # 기준 초: 게임 연동 최대 10초 쉬고 2초 대기, 롤 클라이언트 10초 + 요청 3번 x 5초, 표시 창 최대 1초,
    # 화면은 3초마다 묻지만 창을 내려 두면 브라우저가 타이머를 늦추므로 넉넉히
    diag = Diag(user_dir() / "logs" / "diag.log", {
        "게임 연동": (lambda: watcher.beat, 30),
        "롤 클라이언트": (lambda: lw.beat, 60),
        "게임 화면 표시": (lambda: ov.beat, 15),
        "애드온 화면": (lambda: api._ui_beat, 90),
        "증강 인식": (lambda: reader.beat if reader else None, 30),
    }, status=lambda: ov.status, aug=lambda: reader.state if reader else None)
    diag.cpu_cap = capped
    diag.in_game = lambda: bool(watcher.state.get("inGame"))
    diag.start()
    webmem.log = diag.write
    window.events.loaded += lambda: webmem.apply()
    window.events.minimized += lambda: webmem.set(minimized=True)
    window.events.restored += lambda: webmem.set(minimized=False)
    window.events.maximized += lambda: webmem.set(minimized=False)

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
    if not quiet_resource_hooks() and sys.platform == "win32":
        diag.write("파일 요청 알림 끄기 실패 (화면이 켜질 때 조금 느릴 수 있음)")
    webview.start(http_server=True, debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
