"""게임 화면 표시 (윈도우 전용): 롤 게임 창 위에 투명한 표시 창을 겹친다.

- 게임에 끼어들지 않는다. 다른 창 하나를 게임 창 위치에 맞춰 띄울 뿐이라 안티치트와 무관하다.
- 클릭은 그대로 게임으로 통과한다 (WS_EX_TRANSPARENT). 표시 창을 건드릴 수 없어서 조작에 영향이 없다.
- 게임 창이 맨 앞일 때만 보인다. 전체 화면(독점) 모드에서는 다른 창이 위에 못 올라가서 보이지 않는다.
- tkinter 로 그린다 (파이썬 기본 포함, 투명 색 지정 가능). 자체 스레드에서 돈다.

표시 내용은 화면(app.js)이 api.set_overlay(payload) 로 넘긴다:
  {"cards": [ {name, label, icon, color, reason} | null ] x3,   # 왼쪽·가운데·오른쪽 증강 카드 밑
   "hud": ["고른 증강: …", "다음 구매: …"],                     # 오른쪽 위 작은 상자
   "timers": [{id, name, kind: "auto"|"hp", period|cd, base, readyAt, below}],  # 증강 쿨타임 아이콘 (예상, 표시 창이 매초 센다)
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
# 카드 밑 표시는 이름 줄 + 이만큼 위에서 끝낸다 (그 밑은 새로고침 버튼. 실제 화면: 이름 0.46 · 새로고침 0.70).
# 이유가 길면 글자를 줄여 이 안에 맞춘다 (2026-10-15 지적: 설명이 커서 새로고침 버튼이 안 보임)
CARD_BOTTOM = 0.22
FONT_PX = (20, 15, 11)  # 글자 크기(픽셀, 1080p 기준): 제목, 이유·상자, 가장 작게.
# 포인트로 주면 화면 배율(150% 등)만큼 한 번 더 커졌다 (게임 창 크기 비례 + 배율 = 두 번 키움)
BOX_X, BOX_Y = 16, 0.08  # 상점 옆 상자 자리: 게임 창 왼쪽 위 (픽셀, 높이 비율)
TIMER_X, TIMER_Y = 16, 0.34  # 증강 쿨타임 자리: 게임 창 왼쪽, 상점 상자 밑 (실제 화면 확인 전 추정)

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
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.hwnd = None

    def place(self, x, y, w, h):
        """표시 창을 옮기고 맨 위로 — 활성화하지 않는다 (게임이 키 입력을 잃지 않게)"""
        if not self.hwnd:
            return False
        HWND_TOPMOST = self.wt.HWND(-1)
        SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_NOOWNERZORDER = 0x10, 0x40, 0x200
        return bool(self.user32.SetWindowPos(self.hwnd, HWND_TOPMOST, int(x), int(y), int(w), int(h),
                                             SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER))

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
        self.hwnd = hwnd
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
    # 상자는 상점 창이 열려 있을 때만, 게임 창 왼쪽 위 고정 자리에.
    # 오른쪽 위에 늘 띄우면 게임을 가렸고, 상점 위치를 따라가면 읽을 때마다 위치가 달라 너무 움직였다 (2026-10-14 지적)
    if hud and payload.get("shop"):
        items.append(("hud", BOX_X, int(h * BOX_Y), (hud, "nw")))
        sw = payload.get("swap")
        if sw and sw.get("sell") and sw.get("buy"):
            # 아이템 칸이 다 찼을 때 바꾸기: 상자 바로 밑에 아이콘 두 개 (판다 ✕ → 산다). 자리는 상자를 그린 뒤 정한다
            items.append(("swap", BOX_X, None, (sw["sell"], sw["buy"])))
    st = timer_state(payload.get("timers"))
    if st:
        items.append(("timers", TIMER_X, int(h * TIMER_Y), tuple(st)))
    return items


TIMER_ICON = (24, 32, 40)  # 쿨타임 아이콘 크기(픽셀): (가장 작게, 1080p 기준, 가장 크게). 게임 창 높이에 비례 (창 모드면 작아진다)
TIMER_STEPS = 36  # 덮개 각도 단계 (10도씩): 그림을 이 수만큼만 만들어 두고 다시 쓴다


def timer_size(h):
    lo, base, hi = TIMER_ICON
    return max(lo, min(hi, round(base * h / 1080)))


def timer_state(timers, now=None):
    """증강 쿨타임 상태: 표시 창이 스스로 1초마다 센다 (화면과 매초 주고받지 않게). 초가 바뀔 때만 다시 그려진다 (layout 비교).
    모두 예상값: 게임 연동은 증강 발동을 알려 주지 않아서 고른 시각·내 체력으로 짐작한다.
    (id, 이름, 남은 초, 남은 비율 단계 0~TIMER_STEPS, 준비됨) 목록"""
    if not timers:
        return []
    now = time.time() if now is None else now
    out = []
    for t in timers:
        if t.get("kind") == "auto" and t.get("period"):
            total = t["period"]
            left = total - ((now - t.get("base", now)) % total)
        elif t.get("kind") == "hp" and t.get("cd"):
            total = t["cd"]
            left = max(0.0, (t.get("readyAt") or 0) - now)
        else:
            continue
        secs = int(left + 0.999)
        out.append((t.get("id"), t.get("name", "?"), secs, round(TIMER_STEPS * min(1.0, left / total)), secs == 0))
    return out


_ICON_CACHE = {}


def timer_image(path, size, step):
    """증강 아이콘 + 남은 만큼 어두운 덮개 (게임 스킬 쿨 표시처럼 12시부터 시계 방향으로 걷힘). Pillow 그림(RGB)"""
    from PIL import Image, ImageDraw
    key = (path, size, step)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    img = Image.new("RGBA", (size, size), (17, 24, 32, 255))
    try:
        img.alpha_composite(Image.open(path).convert("RGBA").resize((size, size)))
    except (OSError, ValueError):
        pass  # 아이콘이 없으면 어두운 칸에 숫자만
    if step > 0:
        shade = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        start = -90 + 360 * (1 - step / TIMER_STEPS)  # 지나간 만큼 12시부터 걷힌다
        ImageDraw.Draw(shade).pieslice([-size, -size, size * 2, size * 2], start=start, end=270, fill=(0, 0, 0, 170))
        img.alpha_composite(shade)
    out = img.convert("RGB")
    if len(_ICON_CACHE) > 400:
        _ICON_CACHE.clear()
    _ICON_CACHE[key] = out
    return out


def draw(canvas, payload, w, h, scale=1.0, icon_dir=None):
    canvas.delete("all")
    canvas.images = []  # 그림을 붙잡아 둔다 (안 그러면 Tk 그림이 지워진다)
    font = "Malgun Gothic" if IS_WINDOWS else "Apple SD Gothic Neo"
    # 글자 크기는 음수 = 픽셀 (Tk). 게임 창 높이에만 비례하고 윈도우 화면 배율은 타지 않는다
    big, small, tiny = (-max(1, round(px * scale)) for px in FONT_PX)
    pos = payload.get("pos") or {}
    bottom = int(h * pos["bottom"]) if pos.get("bottom") else None
    box_bottom = None
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
            # 새로고침 버튼을 가리지 않게: 넘치면 이유 글자를 한 픽셀씩 줄인다 (가장 작게까지)
            size = small
            while bottom and rb and rb[3] + 4 > bottom and size < tiny:
                size += 1
                canvas.itemconfig(reason, font=(font, size))
                rb = canvas.bbox(reason)
            # 가장 작게 해도 넘치면 뒤 문장부터 덜어 낸다 (첫 문장 = 평가 이유는 남긴다)
            text = c.get("reason", "")
            while bottom and rb and rb[3] + 4 > bottom:
                cut = max(text.rfind(". ", 0, len(text) - 2), text.rfind(" · ", 0, len(text) - 2))
                if cut <= 0:
                    break
                text = text[:cut].rstrip(" .·") + "…"
                canvas.itemconfig(reason, text=text)
                rb = canvas.bbox(reason)
            if rb:
                canvas.create_rectangle(rb[0] - 8, rb[1] - 4, rb[2] + 8, rb[3] + 4, fill="#111820", outline=color)
            canvas.tag_raise(title)
            canvas.tag_raise(reason)
        elif kind == "timers":
            draw_timers(canvas, c, x, y, h, icon_dir)
        elif kind == "hud":
            lines_list, anchor = c if isinstance(c, tuple) else (c, "ne")
            lines = "\n".join(lines_list)
            t = canvas.create_text(x - 10 if anchor == "ne" else x + 10, y, text=lines, anchor=anchor, font=(font, small),
                                   fill="#e6e9ec", width=int(w * 0.22))
            hb = canvas.bbox(t)
            if hb:
                canvas.create_rectangle(hb[0] - 10, hb[1] - 8, hb[2] + 10, hb[3] + 8, fill="#111820", outline="#c8aa6e")
                box_bottom = hb[3] + 8
            canvas.tag_raise(t)
        elif kind == "swap":
            draw_swap(canvas, c, x, (box_bottom if box_bottom is not None else int(h * BOX_Y)) + 6, h, icon_dir, (font, small))


def swap_image(sell_path, buy_path, size):
    """바꾸기 표시: [팔 아이템: 어둡게 + 빨간 ✕] → [살 아이템: 초록 테두리]. Pillow 그림(RGB)"""
    from PIL import Image, ImageDraw
    gap = size // 2
    out = Image.new("RGB", (size * 2 + gap, size), (17, 24, 32))
    for i, path in enumerate((sell_path, buy_path)):
        try:
            icon = Image.open(path).convert("RGB").resize((size, size))
        except (OSError, ValueError):
            icon = Image.new("RGB", (size, size), (40, 48, 56))
        if i == 0:
            icon = Image.blend(icon, Image.new("RGB", (size, size), (0, 0, 0)), 0.45)
        out.paste(icon, (i * (size + gap), 0))
    d = ImageDraw.Draw(out)
    lw = max(2, size // 12)
    d.line([(3, 3), (size - 4, size - 4)], fill=(229, 57, 53), width=lw)
    d.line([(3, size - 4), (size - 4, 3)], fill=(229, 57, 53), width=lw)
    bx = size + gap
    d.rectangle([bx, 0, bx + size - 1, size - 1], outline=(76, 175, 80), width=lw)
    ax, ay = size + gap // 2, size // 2  # 화살표
    d.polygon([(ax - gap // 3, ay - size // 5), (ax + gap // 3, ay), (ax - gap // 3, ay + size // 5)], fill=(200, 170, 110))
    return out


def draw_swap(canvas, ids, x, y, h, icon_dir, font):
    """아이템 칸이 다 찼을 때: 팔 것 ✕ → 살 것 (글 대신 아이콘. 2026-10-15 요청)"""
    import os
    sell, buy = ids
    size = round(timer_size(h) * 1.25)
    item_dir = os.path.join(os.path.dirname(icon_dir), "item") if icon_dir else ""
    path = lambda i: os.path.join(item_dir, f"{i}.webp") if item_dir else ""
    t = canvas.create_text(x + 10, y + size // 2, text="🔁", font=font, fill="#e6e9ec", anchor="w")
    tb = canvas.bbox(t)
    x0 = (tb[2] if tb else x + 30) + 6
    try:
        from PIL import ImageTk
        img = ImageTk.PhotoImage(swap_image(path(sell), path(buy), size), master=canvas)
        canvas.images.append(img)
        canvas.create_image(x0, y, image=img, anchor="nw")
    except Exception:
        canvas.create_text(x0, y + size // 2, text="✕ → ✓", font=font, fill="#e6e9ec", anchor="w")
    r = canvas.create_rectangle(x, y - 4, x0 + size * 2 + size // 2 + 8, y + size + 4, fill="#111820", outline="#c8aa6e")
    canvas.tag_lower(r)


def draw_timers(canvas, states, x, y, h, icon_dir):
    """쿨타임 아이콘 한 줄: 아이콘 + 덮개 + 가운데 남은 초. 준비되면 초록 테두리, 돌고 있으면 점선 테두리(예상값)"""
    import os
    size = timer_size(h)
    gap = max(4, size // 6)
    font = ("Malgun Gothic" if IS_WINDOWS else "Apple SD Gothic Neo", -max(12, size * 2 // 5), "bold")  # 픽셀
    try:
        from PIL import ImageTk
    except ImportError:
        ImageTk = None
    for i, (aid, name, secs, step, ready) in enumerate(states):
        x0 = x + i * (size + gap)
        path = os.path.join(icon_dir, f"{aid}.webp") if icon_dir and aid else ""
        if path and not os.path.exists(path):
            path = os.path.join(icon_dir, f"{aid}.png")
        if ImageTk:
            try:
                img = ImageTk.PhotoImage(timer_image(path, size, step), master=canvas)
                canvas.images.append(img)
                canvas.create_image(x0, y, image=img, anchor="nw")
            except Exception:
                canvas.create_rectangle(x0, y, x0 + size, y + size, fill="#111820", outline="")
        else:
            canvas.create_rectangle(x0, y, x0 + size, y + size, fill="#111820", outline="")
        if ready:
            canvas.create_rectangle(x0, y, x0 + size, y + size, outline="#4caf50", width=2)
        else:
            canvas.create_rectangle(x0, y, x0 + size, y + size, outline="#c8aa6e", width=1, dash=(3, 2))
            cx, cy = x0 + size / 2, y + size / 2
            canvas.create_text(cx + 1, cy + 1, text=str(secs), font=font, fill="#000000")  # 그림자 (밝은 아이콘 위에서도 보이게)
            canvas.create_text(cx, cy, text=str(secs), font=font, fill="#ffffff")


KIND_NAME = {"card": "카드", "hud": "상점 상자", "timers": "쿨타임", "swap": "바꾸기"}
PARK = (-4000, -4000)  # 보여 줄 게 없을 때 표시 창을 치워 두는 자리 (화면 밖)


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
        self.icon_dir = None  # 증강 아이콘 폴더 (쿨타임 표시)

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
        root.title("칼바람 표시")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", KEY)
        root.configure(bg=KEY)
        canvas = tk.Canvas(root, bg=KEY, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        # 창은 켤 때(게임 전) 한 번만 띄워 두고, 보여 줄 게 없으면 화면 밖 1픽셀로 치운다.
        # 게임 중에 숨겼다 다시 띄우면(deiconify) 창이 활성화돼 게임이 키 입력을 잃을 수 있다
        root.geometry(f"1x1+{PARK[0]}+{PARK[1]}")
        root.update_idletasks()
        win.click_through(root)
        root.update()
        shown, last, placed = False, None, None
        self._running = True

        def tick():
            nonlocal shown, last, placed
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
            nonlocal shown, last, placed
            while not self.q.empty():
                k, v = self.q.get_nowait()
                if k == "payload":
                    self.payload = v or {}
                elif k == "enabled":
                    self.enabled = v
                elif k == "preview":
                    self.preview = v
                # 같은 내용을 다시 받으면 다시 그리지 않는다 (아래 key 로 비교)
            rect, front = win.game_rect()
            # 게임 창이 새로 잡히면 화면 모드를 한 번 읽는다 (게임 중 설정을 바꿔도 다음 게임에 반영)
            if rect and not self._mode_checked:
                self.mode, self.cfg_path = read_window_mode(game_install_dirs())
                self._mode_checked = True
            if not rect:
                self.mode = None
                self._mode_checked = False
            # 실제로 그릴 것(카드 평가·상점 상자)이 있을 때만 띄운다. 예전엔 그릴 게 없어도 게임 크기의 투명 창이
            # 게임 위에 늘 떠 있어서, 화면 합성(DWM)이 게임 프레임마다 그 창까지 섞느라 렉이 생길 수 있었다
            entries = layout(self.payload, rect[2], rect[3]) if rect else []
            visible = self.enabled and rect and (front or self.preview) and entries and self.mode != "전체 화면"
            if not visible:
                if shown:
                    canvas.delete("all")
                    # Tk 가 기억하는 크기·위치도 같이 바꾼다 (안 그러면 Tk 가 나중에 예전 자리로 되돌린다)
                    root.geometry(f"1x1+{PARK[0]}+{PARK[1]}")
                    win.place(PARK[0], PARK[1], 1, 1)
                    shown, last, placed = False, None, None
                if rect and self.mode == "전체 화면":
                    self.status = "전체 화면이라 표시할 수 없어요 → 롤 설정 > 비디오 > 창 모드를 '테두리 없음'으로"
                else:
                    self.status = "게임 창 없음" if not rect else ("게임 창이 뒤에 있음" if not front else "표시할 내용 없음")
            else:
                x, y, w, h = rect
                key = (w, h, repr(entries))
                if key != last:  # 내용·게임 창 크기가 바뀌었을 때만 다시 그린다
                    canvas.config(width=w, height=h)
                    draw(canvas, self.payload, w, h, scale=h / 1080, icon_dir=self.icon_dir)
                    # 창은 그린 것 둘레만큼만 (게임 크기 투명 창 X): 겹치는 영역이 작을수록 게임 화면 합성이 가볍다
                    bb = canvas.bbox("all") or (0, 0, 1, 1)
                    bx0, by0 = max(0, bb[0] - 4), max(0, bb[1] - 4)
                    bw, bh = min(w, bb[2] + 4) - bx0, min(h, bb[3] + 4) - by0
                    canvas.move("all", -bx0, -by0)
                    canvas.config(width=bw, height=bh)
                    self._box = (bx0, by0, bw, bh)
                    last = key
                    placed = None
                bx0, by0, bw, bh = self._box
                where = (x + bx0, y + by0, bw, bh)
                if where != placed:  # 게임 창이 움직이면 위치만 옮긴다 (활성화 없이 맨 위로)
                    root.geometry(f"{bw}x{bh}+{where[0]}+{where[1]}")
                    win.place(*where)
                    placed = where
                shown = True
                # 무엇을 어디에 그렸는지 (게임 창 비율, 0.05 단위): 실제 게임에서 무엇을 가리는지 기록으로 보려고.
                # 0.05 단위라 초가 바뀌어도 상태 글은 그대로 → 진단 기록에 매초 남지 않는다
                kinds = "·".join(sorted({KIND_NAME.get(k, k) for k, *_ in entries}))
                r5 = lambda v: f"{round(v * 20) / 20:.2f}"
                where_txt = f"가로 {r5(bx0 / w)}~{r5((bx0 + bw) / w)} 세로 {r5(by0 / h)}~{r5((by0 + bh) / h)}"
                self.status = f"표시 중 · {self.mode or '화면 모드 확인 못 함'} {w}×{h} · {kinds} ({where_txt})"
            return rect

        root.after(200, tick)
        root.mainloop()
