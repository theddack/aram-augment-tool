"""게임 화면 표시 (윈도우 전용): 롤 게임 창 위에 투명한 표시 창을 겹친다.

- 게임에 끼어들지 않는다. 다른 창 하나를 게임 창 위치에 맞춰 띄울 뿐이라 안티치트와 무관하다.
- 클릭은 그대로 게임으로 통과한다 (WS_EX_TRANSPARENT). 표시 창을 건드릴 수 없어서 조작에 영향이 없다.
- 게임 창이 맨 앞일 때만 보인다. 전체 화면(독점) 모드에서는 다른 창이 위에 못 올라가서 보이지 않는다.
- tkinter 로 그린다 (파이썬 기본 포함, 투명 색 지정 가능). 자체 스레드에서 돈다.

표시 내용은 화면(app.js)이 api.set_overlay(payload) 로 넘긴다:
  {"cards": [ {name, label, icon, color, reason} | null ] x3,   # 왼쪽·가운데·오른쪽 증강 카드 밑
   "hud": ["고른 증강: …", "다음 구매: …"],                     # 오른쪽 위 작은 상자
   "pos": {"y": 0.80, "spread": 0.17}}                          # 카드 밑 표시 세로 위치, 카드 간격 (게임 창 비율)
"""
import queue
import sys
import threading
import time

GAME_TITLE = "League of Legends (TM) Client"
KEY = "#ff00ff"  # 이 색은 투명하게 처리된다
TIER_COLORS = {"S": "#ff7043", "A": "#ffb300", "B": "#9ccc65", "C": "#90a4ae", "D": "#78909c", "X": "#546e7a"}
# 기본 위치: 위키의 칼바람 증강 선택 화면 스크린샷 기준 추정 (카드 안 설명 밑 빈 곳, 카드 간격 ≈ 화면 폭 25%).
# 실제 화면에서 '위치 맞추기' 슬라이더로 조정한다
DEFAULT_POS = {"y": 0.70, "spread": 0.246}
# 증강 인식으로 카드 이름 줄 위치를 알면: 이름 줄 + 이만큼 (게임 창 높이 비율) 밑에 붙인다.
# 실제 화면(2026-10-14): 이름 0.46 · 설명 0.53~0.57 · 빈 곳 0.59~0.69 · 블리츠 배지 0.70~ → 설명 밑 빈 곳 위쪽
BELOW_NAME = 0.15

IS_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------- 윈도우 API (ctypes)
class _Win:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.user32 = u = ctypes.windll.user32
        # 64비트 창 번호(HWND)가 int 로 잘리지 않게 인자·반환 형식을 정해 둔다
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.GetForegroundWindow.restype = wintypes.HWND
        u.IsIconic.argtypes = [wintypes.HWND]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        u.GetParent.restype = wintypes.HWND
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

    def game_rect(self):
        """롤 게임 창의 화면 좌표 (x, y, w, h) 와 맨 앞 여부. 없거나 최소화면 None"""
        u = self.user32
        hwnd = u.FindWindowW(None, GAME_TITLE)
        if not hwnd or u.IsIconic(hwnd) or not u.IsWindowVisible(hwnd):
            return None, False
        rc = self.wt.RECT()
        u.GetClientRect(hwnd, self.ct.byref(rc))
        pt = self.wt.POINT(0, 0)
        u.ClientToScreen(hwnd, self.ct.byref(pt))
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w < 200 or h < 200:
            return None, False
        return (pt.x, pt.y, w, h), u.GetForegroundWindow() == hwnd

    def click_through(self, tk_root):
        """표시 창을 클릭 통과·작업 표시줄 숨김·포커스 안 받게"""
        u = self.user32
        hwnd = u.GetParent(tk_root.winfo_id()) or tk_root.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
        WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x80, 0x8000000, 0x8
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
                         | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)


# 롤 화면 설정 파일 (설치 위치가 다르면 여러 후보를 본다)
GAME_CFG_CANDIDATES = [
    r"C:\Riot Games\League of Legends\Config\game.cfg",
    r"D:\Riot Games\League of Legends\Config\game.cfg",
    r"C:\Program Files\Riot Games\League of Legends\Config\game.cfg",
    r"C:\Program Files (x86)\Riot Games\League of Legends\Config\game.cfg",
]
WINDOW_MODE = {0: "전체 화면", 1: "창 모드", 2: "테두리 없음"}


def read_window_mode(extra_dirs=()):
    """롤 화면 모드를 game.cfg 의 [General] WindowMode 에서 읽는다. (모드 이름, 파일 경로) 또는 (None, None)"""
    import os
    import re as _re
    # 실행 중인 게임의 설치 폴더를 먼저 본다 (기본 위치에 남은 옛 설정 파일을 잘못 읽지 않게)
    cands = [os.path.join(d, "Config", "game.cfg") for d in extra_dirs] + list(GAME_CFG_CANDIDATES)
    for path in cands:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        m = _re.search(r"^\s*WindowMode\s*=\s*(\d+)", text, _re.M)
        if m:
            return WINDOW_MODE.get(int(m.group(1)), f"알 수 없음({m.group(1)})"), path
    return None, None


def game_install_dirs():
    """실행 중인 롤 게임 프로세스 경로에서 설치 폴더를 찾는다 (기본 위치가 아닐 때 대비)"""
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes
    try:
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.FindWindowW.restype = wintypes.HWND
        hwnd = u.FindWindowW(None, GAME_TITLE)
        if not hwnd:
            return []
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return []
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ok = k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k.CloseHandle(h)
        if not ok:
            return []
        import os
        game_dir = os.path.dirname(buf.value)  # …\League of Legends\Game
        return [os.path.dirname(game_dir)]
    except Exception:
        return []


def set_dpi_aware():
    """화면 배율(125%·150%)에서도 게임 창 좌표와 표시 창 좌표가 맞도록. 창을 만들기 전에 불러야 한다"""
    if not IS_WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------------------------------------------------------------- 그리기 (OS 무관, 맥에서도 확인 가능)
def layout(payload, w, h):
    """그릴 항목 목록: (종류, x, y, 내용). 좌표는 표시 창 기준"""
    pos = {**DEFAULT_POS, **(payload.get("pos") or {})}
    items = []
    cy = int(h * pos["y"])
    xs = pos.get("xs")
    for i, card in enumerate((payload.get("cards") or [None] * 3)[:3]):
        if not card:
            continue
        cx = round(w * xs[i]) if xs and i < len(xs) and xs[i] is not None else int(w * (0.5 + (i - 1) * pos["spread"]))
        items.append(("card", cx, cy, card))
    hud = payload.get("hud") or []
    if hud:
        items.append(("hud", w - 16, 56, hud))
    return items


def draw(canvas, payload, w, h, scale=1.0):
    canvas.delete("all")
    font = "Malgun Gothic" if IS_WINDOWS else "Apple SD Gothic Neo"
    big, small = max(11, int(15 * scale)), max(9, int(11 * scale))
    for kind, x, y, c in layout(payload, w, h):
        if kind == "card":
            color = c.get("color") or TIER_COLORS.get(c.get("tier"), "#78909c")
            # 평가 상자 바로 밑에서부터 이유를 써 내려간다 (두 줄이 돼도 위를 덮지 않게)
            title = canvas.create_text(x, y, text=f"{c.get('icon', '')} {c.get('label', '')}".strip(),
                                       font=(font, big, "bold"), fill="#111111", anchor="n")
            tb = canvas.bbox(title)  # 글자가 비면 None
            y2 = tb[3] if tb else y
            reason = canvas.create_text(x, y2 + 12, text=c.get("reason", ""), width=int(w * 0.18),
                                        font=(font, small), fill="#ffffff", anchor="n")
            if tb:
                canvas.create_rectangle(tb[0] - 10, tb[1] - 4, tb[2] + 10, tb[3] + 4, fill=color, outline="")
            rb = canvas.bbox(reason)
            if rb:
                canvas.create_rectangle(rb[0] - 8, rb[1] - 4, rb[2] + 8, rb[3] + 4, fill="#111820", outline=color)
            canvas.tag_raise(title)
            canvas.tag_raise(reason)
        elif kind == "hud":
            lines = "\n".join(c)
            t = canvas.create_text(x - 10, y, text=lines, anchor="ne", font=(font, small), fill="#e6e9ec",
                                   width=int(w * 0.24))
            hb = canvas.bbox(t)
            if hb:
                canvas.create_rectangle(hb[0] - 10, hb[1] - 8, hb[2] + 10, hb[3] + 8, fill="#111820", outline="#c8aa6e")
            canvas.tag_raise(t)


# ---------------------------------------------------------------- 표시 창 스레드 (윈도우)
class Overlay(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.payload = {}
        self.enabled = True
        self.preview = False  # 위치 맞추기: 게임이 맨 앞이 아니어도 보이게
        self.status = "대기"
        self.mode = None  # 롤 화면 모드 (game.cfg)
        self.cfg_path = None
        self._running = False
        self.beat = None  # 표시 창이 마지막으로 확인한 시각 (멈춤 진단용, 돌기 시작하면 채운다)
        self._mode_checked = False  # 이번 게임 창에서 화면 모드를 읽어 봤는지 (없으면 0.2초마다 다시 찾지 않게)

    # 표시 창이 돌고 있지 않으면(맥, Tk 실패) 큐에 쌓지 않고 값만 바꾼다 (끝없이 쌓이지 않게)
    def _send(self, k, v):
        if self._running:
            self.q.put((k, v))
        elif k == "payload":
            self.payload = v or {}
        else:
            setattr(self, k, v)

    def update(self, payload):
        self._send("payload", payload)

    def set_enabled(self, on):
        self._send("enabled", bool(on))

    def set_preview(self, on):
        self._send("preview", bool(on))

    def run(self):
        if not IS_WINDOWS:
            self.status = "윈도우에서만 동작"
            return
        import tkinter as tk
        try:
            win = _Win()
            root = tk.Tk()
        except Exception as e:  # 표시 창을 못 만들면 이유를 상태로 보여 준다
            self.status = f"표시 창을 만들 수 없음: {str(e)[:80]}"
            return
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", KEY)
        root.configure(bg=KEY)
        canvas = tk.Canvas(root, bg=KEY, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        root.update_idletasks()
        win.click_through(root)
        root.withdraw()
        shown, last = False, None
        self._running = True

        def tick():
            nonlocal shown, last
            self.beat = time.time()
            rect = None
            try:
                rect = step()
            except Exception as e:  # 한 번 오류가 나도 표시 창이 멈추지 않게 (다음 tick 은 그대로 예약)
                self.status = f"표시 오류: {str(e)[:80]}"
            finally:
                # 게임 창이 있으면 0.2초마다(창 이동·전환 따라가기), 없으면 1초마다만 확인
                root.after(200 if rect else 1000, tick)

        def step():
            nonlocal shown, last
            while not self.q.empty():
                k, v = self.q.get_nowait()
                if k == "payload":
                    self.payload = v or {}
                elif k == "enabled":
                    self.enabled = v
                elif k == "preview":
                    self.preview = v
                last = None  # 다시 그리기
            rect, front = win.game_rect()
            # 게임 창이 새로 잡히면 화면 모드를 한 번 읽는다 (게임 중 설정을 바꿔도 다음 게임에 반영)
            if rect and not self._mode_checked:
                self.mode, self.cfg_path = read_window_mode(game_install_dirs())
                self._mode_checked = True
            if not rect:
                self.mode = None
                self._mode_checked = False
            visible = self.enabled and rect and (front or self.preview) and self.payload and self.mode != "전체 화면"
            if not visible:
                if shown:
                    root.withdraw()
                    shown = False
                if rect and self.mode == "전체 화면":
                    self.status = "전체 화면이라 표시할 수 없어요 → 롤 설정 > 비디오 > 창 모드를 '테두리 없음'으로"
                else:
                    self.status = "게임 창 없음" if not rect else ("게임 창이 뒤에 있음" if not front else "표시할 내용 없음")
            else:
                x, y, w, h = rect
                key = (rect, repr(self.payload))
                if key != last:
                    root.geometry(f"{w}x{h}+{x}+{y}")
                    canvas.config(width=w, height=h)
                    draw(canvas, self.payload, w, h, scale=h / 1080)
                    last = key
                if not shown:
                    root.deiconify()
                    root.attributes("-topmost", True)
                    shown = True
                self.status = f"표시 중 · {self.mode or '화면 모드 확인 못 함'} {w}×{h}"
            return rect

        root.after(200, tick)
        root.mainloop()
