#!/usr/bin/env python3
"""윈도우 실제 흐름 재현 (GitHub Actions 윈도우 러너용): 가짜 롤 클라이언트·가짜 게임으로 한 판을 돌리며
애드온 창이 '응답 없음'이 되는지, 게임 화면 표시가 뜨는지 확인한다.

  대기실(0~15초) → 챔피언 선택(15~35, 20초부터 미스 포츈) → 게임 시작(35~45) → 게임 중(45~)
  게임 중: 가짜 게임 창(League of Legends (TM) Client)을 띄우고 Live Client 데이터를 3초마다 바꾼다.

결과: repro_out/ 에 화면 캡처·diag.log·스레드 덤프·타임라인. 창이 멈추면 종료 코드 1.
  python tools/win_repro.py [--exe dist/칼바람증강Addon/칼바람증강Addon.exe] [--seconds 150]
"""
import argparse
import ctypes
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "repro_out"
TITLE = "칼바람 증강 Addon"
GAME_TITLE = "League of Legends (TM) Client"
LIVE_PORT, LCU_PORT = 29990, 29991
SAMPLE = ROOT / "tests" / "data" / "live_missfortune_4s.json"
T0 = time.time()


def now():
    return time.time() - T0


def phase():
    t = now()
    return "Lobby" if t < 15 else "ChampSelect" if t < 35 else "GameStart" if t < 45 else "InProgress"


def live_data():
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    g = max(0.0, now() - 45)
    raw["gameData"]["gameTime"] = g
    ap = raw["activePlayer"]
    ap["level"] = min(18, 3 + int(g // 15))
    ap["currentGold"] = 1400 + 20 * g
    me = next(p for p in raw["allPlayers"] if p["riotId"] == ap["riotId"])
    # 15초마다 아이템이 하나씩 늘어난다 (화면 전체 다시 계산되는 경우)
    buys = ["1001", "1036", "3006", "3031", "3046", "3072"][: int(g // 15)]
    me["items"] += [{"itemID": int(i), "displayName": i, "count": 1, "slot": n} for n, i in enumerate(buys)]
    return raw


ITEM_SETS = {"accountId": 1, "itemSets": [{"uid": "user-1", "title": "내 세트"}], "timestamp": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path
        if self.server.server_port == LIVE_PORT:
            return self.send(200, live_data()) if phase() == "InProgress" and now() > 48 else self.send(404, {})
        if p == "/lol-gameflow/v1/gameflow-phase":
            return self.send(200, phase())
        if p == "/lol-champ-select/v1/current-champion":
            return self.send(200, 21) if phase() in ("ChampSelect", "GameStart") and now() > 20 else self.send(404, {})
        if p == "/lol-summoner/v1/current-summoner":
            return self.send(200, {"summonerId": 7, "accountId": 1})
        if p.startswith("/lol-item-sets/"):
            return self.send(200, ITEM_SETS)
        if p.startswith("/lol-end-of-game/"):
            return self.send(404, {})
        self.send(404, {})

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        log("LCU 아이템 세트 저장 받음")
        self.send(200, {})


EVENTS = []


def log(msg):
    line = f"{now():6.1f}s {msg}"
    EVENTS.append(line)
    print(line, flush=True)


u32 = ctypes.windll.user32
u32.FindWindowW.restype = wintypes.HWND
u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u32.IsHungAppWindow.argtypes = [wintypes.HWND]
u32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
u32.SendMessageTimeoutW.restype = ctypes.c_ssize_t


def responds(hwnd, ms=3000):
    res = ctypes.c_size_t()
    return bool(u32.SendMessageTimeoutW(hwnd, 0, 0, 0, 0x0002, ms, ctypes.byref(res)))  # WM_NULL, SMTO_ABORTIFHUNG


def shot(name):
    try:
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(OUT / f"{name}.png")
    except Exception as e:
        log(f"캡처 실패 {name}: {e}")


GAME_WINDOW = r"""
import tkinter as tk, ctypes
try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception: pass
r = tk.Tk(); r.title("League of Legends (TM) Client"); r.geometry("1280x720+0+0"); r.overrideredirect(True)
r.configure(bg="#203040")
tk.Label(r, text="가짜 게임 화면 (증강 선택 화면 자리)", fg="white", bg="#203040", font=("Malgun Gothic", 20)).pack(expand=True)
r.after(300, lambda: (r.lift(), r.focus_force()))
r.mainloop()
"""

APP_WRAPPER = r"""
import faulthandler, runpy, sys, os
f = open(os.environ["REPRO_DUMP"], "w", encoding="utf-8")
faulthandler.dump_traceback_later(20, repeat=True, file=f)  # 20초마다 모든 스레드 위치 (GIL 이 막혀도 C 에서 찍힌다)
sys.argv = ["desktop.py"]
runpy.run_path("desktop.py", run_name="__main__")
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe")
    ap.add_argument("--seconds", type=int, default=150)
    ap.add_argument("--settings", default="{}", help="시작 전 settings.json (예: '{\"on_top\": true}')")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    for port in (LIVE_PORT, LCU_PORT):
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    lock = OUT / "lockfile"
    lock.write_text(f"LeagueClient:1:{LCU_PORT}:pw:http", encoding="utf-8")
    cfg = pathlib.Path(r"C:\Riot Games\League of Legends\Config")
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "game.cfg").write_text("[General]\nWindowMode=2\n", encoding="utf-8")
    udir = pathlib.Path(os.environ["APPDATA"]) / "AramAugment"
    udir.mkdir(parents=True, exist_ok=True)
    (udir / "settings.json").write_text(a.settings, encoding="utf-8")

    env = {**os.environ, "ARAM_LIVE_URL": f"http://127.0.0.1:{LIVE_PORT}/liveclientdata/allgamedata",
           "ARAM_LCU_LOCKFILE": str(lock), "REPRO_DUMP": str(OUT / "thread_dump.txt")}
    cmd = [a.exe] if a.exe else [sys.executable, "-c", APP_WRAPPER]
    app = subprocess.Popen(cmd, cwd=ROOT, env=env)
    log(f"애드온 시작: {cmd[0]}")
    game = None
    hwnd = None
    hung_since = None
    worst = 0.0
    shots = {10: "1_lobby", 30: "2_champselect", 60: "3_ingame", 100: "4_ingame_items"}
    try:
        while now() < a.seconds:
            if app.poll() is not None:
                log(f"애드온이 꺼짐 (코드 {app.returncode})")
                break
            if phase() == "InProgress" and not game:
                game = subprocess.Popen([sys.executable, "-c", GAME_WINDOW])
                log("가짜 게임 창 띄움")
            hwnd = hwnd or u32.FindWindowW(None, TITLE)
            if hwnd:
                t = time.time()
                ok = responds(hwnd)
                lag = time.time() - t
                worst = max(worst, lag)
                if not ok or u32.IsHungAppWindow(hwnd):
                    if hung_since is None:
                        hung_since = now()
                        log(f"⚠ 애드온 창 응답 없음 (단계 {phase()})")
                        shot("hung")
                elif hung_since is not None:
                    log(f"애드온 창 다시 응답 ({now() - hung_since:.1f}초 멈췄음)")
                    hung_since = None
                elif lag > 0.5:
                    log(f"애드온 창 응답 느림 {lag:.2f}초 (단계 {phase()})")
            for t, name in list(shots.items()):
                if now() >= t:
                    shot(name)
                    del shots[t]
            time.sleep(0.5)
    finally:
        shot("9_end")
        for p in (app, game):
            if p and p.poll() is None:
                p.kill()
        log(f"가장 느린 응답 {worst:.2f}초")
        for f in ("diag.log",):
            src = udir / "logs" / f
            if src.exists():
                (OUT / f).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"---- {f}\n" + src.read_text(encoding="utf-8"))
        (OUT / "timeline.txt").write_text("\n".join(EVENTS), encoding="utf-8")
    hung = any("응답 없음" in e for e in EVENTS)
    print("결과:", "멈춤 재현됨" if hung else "멈춤 없음")
    sys.exit(1 if hung else 0)


if __name__ == "__main__":
    main()
