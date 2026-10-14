"""증강 선택 화면 읽기 (윈도우 전용): 게임 창의 카드 이름 줄을 윈도우 기본 글자 인식(Windows.Media.Ocr)으로 읽는다.

- 라이엇 게임 중 데이터(Live Client Data)에는 증강이 없다 (2026-10-14 실제 기록 307초, 증강 고른 뒤에도 없음).
- 화면을 읽기만 한다 (게임 메모리·입력에 손대지 않음). 블리츠 같은 프로그램과 같은 방식.
- 고른 증강: 선택 화면이 떠 있는 동안 마지막으로 클릭한 카드 (마우스 위치·왼쪽 버튼 상태만 읽는다, 게임에 입력을 보내지 않음).
  카드 테두리 색은 등급(실버·골드·프리즘)이라 고른 표시로 쓸 수 없다 (2026-10-14 실제 화면: 세 장 모두 금색인 판).
  클릭을 못 봤으면 None → 화면(app.js)이 '이걸로 선택'을 눌러 달라고 안내한다.

화면 비율 (실제 화면 2026-10-14): 카드 위 테두리 ≈ 0.20, 이름 줄 ≈ 0.46, 새로고침 버튼 ≈ 0.70 (게임 창 높이 비율)
"""
import difflib
import re
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"
NAME_BAND = (0.38, 0.55)  # 이 안의 글자만 카드 이름으로 본다 (게임 창 높이 비율)
SHOP_BAND = (0.04, 0.45)  # 상점 창 위쪽 탭('추천 · 모든 아이템 · 아이템 세트')을 찾는 범위
CARD_Y = (0.18, 0.66)  # 카드 몸통 (이 안을 클릭해야 고른 것. 그 밑 새로고침 버튼은 제외)
CARD_HALF_W = 0.09  # 카드 반폭 (게임 창 너비 비율)
MIN_RATIO = 0.72  # 이름 비슷한 정도 (OCR 이 한두 글자 틀려도 잡게)
INTERVAL = 1.0  # 초 (글자 읽기)
CLICK_POLL = 0.03  # 초 (선택 화면이 떠 있을 때만 마우스 버튼 확인)


def norm(s):
    return re.sub(r"[\s\W_]+", "", str(s))


class Matcher:
    """증강 이름 목록과 OCR 글자를 맞춘다"""

    def __init__(self, names):
        """names: 증강 이름 목록. (이름, 다른 이름…) 묶음도 받는다 → 다른 이름(영어)으로 읽혀도 이름으로 돌려준다"""
        self.names = {}
        for n in names:
            main, *alts = n if isinstance(n, (tuple, list)) else (n,)
            for a in (main, *alts):
                if a and norm(a) and norm(a) not in self.names:
                    self.names[norm(a)] = main

    def partial(self, text):
        """이름의 일부(2글자 이상)가 보이는지: '포로'·'난사 발동' 처럼 잘려 읽힌 이름. 설명 문장은 길어서 걸러진다"""
        t = norm(text)
        return 2 <= len(t) <= 10 and any(t in k for k in self.names if len(k) > len(t))

    def match(self, text):
        t = norm(text)
        if len(t) < 2:
            return None, 0.0
        if t in self.names:
            return self.names[t], 1.0
        # 이름이 다른 글자와 붙어 읽힌 경우 ('포로 쇄도 저항'): 3글자 이상 이름이 통째로 들어 있으면 그 이름 (가장 긴 것)
        inside = [k for k in self.names if len(k) >= 3 and k in t and len(t) <= len(k) + 8]
        if inside:
            k = max(inside, key=len)
            return self.names[k], 0.9
        # 짧은 이름(3~5글자)은 한 글자만 틀려도 비율이 확 떨어진다 → 한 글자 차이까지 같은 이름 ('양파상' → 앙파상, 2026-10-14 실제)
        if 3 <= len(t) <= 5:
            near = [k for k in self.names if len(k) == len(t) and sum(a != b for a, b in zip(k, t)) == 1]
            if len(near) == 1:
                return self.names[near[0]], 0.8
        best, score = None, 0.0
        for k, n in self.names.items():
            if abs(len(k) - len(t)) > 3:
                continue
            r = difflib.SequenceMatcher(None, t, k).ratio()
            if r > score:
                best, score = n, r
        return (best, score) if score >= MIN_RATIO else (None, score)


def rows_of(words):
    """같은 줄 = 세로 가운데가 가까운 단어끼리 (글자 높이로 나누면 'Heavy'·'Hitter'·'to' 처럼 높이가 다른 단어가
    서로 다른 줄로 갈라졌다: 2026-10-14 윈도우 가상 PC 실측)"""
    rows = []
    for wd in sorted(words, key=lambda r: r[2] + r[4] / 2):
        cy = wd[2] + wd[4] / 2
        row = next((r for r in rows if abs(r["cy"] - cy) <= 0.6 * max(r["h"], wd[4])), None)
        if row:
            row["words"].append(wd)
            row["h"] = max(row["h"], wd[4])
        else:
            rows.append({"cy": cy, "h": wd[4], "words": [wd]})
    return [r["words"] for r in rows]


def find_shop(words, width, height):
    """상점 창이 열려 있으면 위쪽 탭 줄의 위치 {left, right, top} (게임 창 비율), 아니면 None.
    탭 글자 '모든 아이템' 과 '아이템 세트'(또는 '추천')가 한 줄에 있으면 상점 (영어: All Items · Item Sets · Recommended)"""
    for row in rows_of(words):
        text = norm("".join(w[0] for w in sorted(row, key=lambda r: r[1]))).lower()
        ko = "모든아이템" in text and ("아이템세트" in text or "추천" in text)
        en = "allitems" in text and ("itemsets" in text or "recommended" in text)  # 영어 클라이언트·검증용 가상 PC
        if ko or en:
            x0 = min(w[1] for w in row)
            x1 = max(w[1] + w[3] for w in row)
            y0 = min(w[2] for w in row)
            return {"left": round(x0 / width, 4), "right": round(x1 / width, 4), "top": round(y0 / height, 4)}
    return None


def segments(words, gap):
    """OCR 단어들 [(글자, x, y, w, h)] → 같은 줄에서 간격이 gap 보다 크면 나눈 덩어리 [(글자, 가운데 x, y)]
    세 카드 이름이 한 줄로 합쳐져 나와도 카드별로 나눈다"""
    out = []
    for row in rows_of(words):
        row.sort(key=lambda r: r[1])
        cur = [row[0]]
        for r in row[1:]:
            prev = cur[-1]
            if r[1] - (prev[1] + prev[3]) > gap:
                out.append(cur)
                cur = [r]
            else:
                cur.append(r)
        out.append(cur)
    res = []
    for seg in out:
        x0, x1 = seg[0][1], seg[-1][1] + seg[-1][3]
        res.append((" ".join(s[0] for s in seg), (x0 + x1) / 2, min(s[2] for s in seg)))
    return res


def find_offers(words, width, height, matcher):
    """카드 이름 덩어리 → [(증강 이름, 가운데 x, 이름 y)] 왼쪽부터. 2개 이상 맞아야 선택 화면으로 본다"""
    found = {}
    for text, cx, y in segments(words, gap=width * 0.04):
        if not (NAME_BAND[0] * height <= y <= NAME_BAND[1] * height):
            continue
        name, score = matcher.match(text)
        if name and (name not in found or found[name][2] < score):
            found[name] = (cx, y, score)
    offers = sorted(((n, v[0], v[1]) for n, v in found.items()), key=lambda o: o[1])
    return offers if len(offers) >= 2 else []


def slots(offers, width):
    """[(이름, x, y)] → 왼쪽·가운데·오른쪽 3칸. 3개면 순서대로, 2개면 화면 위치(3등분)로"""
    return [o[0] if o else None for o in _slot_items(offers, width)]


def _slot_items(offers, width):
    if len(offers) == 3:
        return list(offers)
    out = [None, None, None]
    for o in offers:
        out[min(2, max(0, int(o[1] / width * 3)))] = o
    return out


def card_pos(offers, width, height):
    """게임 화면 표시가 카드에 정확히 붙도록: 칸별 카드 가운데 x, 이름 줄 y (게임 창 비율). 못 읽은 칸은 옆 칸 간격으로 추정"""
    items = _slot_items(offers, width)
    xs = [o[1] / width if o else None for o in items]
    known = [(i, x) for i, x in enumerate(xs) if x is not None]
    if len(known) >= 2:
        (i0, x0), (i1, x1) = known[0], known[-1]
        step = (x1 - x0) / (i1 - i0)
        xs = [x if x is not None else x0 + (i - i0) * step for i, x in enumerate(xs)]
    ys = [o[2] / height for o in offers]
    return {"xs": [round(x, 4) for x in xs], "nameY": round(sum(ys) / len(ys), 4)}


def card_at(pos, x, y):
    """클릭 위치 (게임 창 비율) → 카드 칸 번호. 카드 밖(새로고침 버튼 등)이면 None"""
    if not pos or not (CARD_Y[0] <= y <= CARD_Y[1]):
        return None
    for i, cx in enumerate(pos.get("xs") or []):
        if cx is not None and abs(x - cx) <= CARD_HALF_W:
            return i
    return None


def resolve_groups(groups, id_names, offered):
    """증강 칸에서 읽은 그림 묶음들 → (정한 증강 id 목록, 못 정한 묶음 [이름 목록]).
    그림이 같은 증강이 여럿이면(돌진=앙파상) 이번 게임 선택 화면에 떴던 이름으로 가린다"""
    ids, unsure = [], []
    for g in groups:
        g = list(g)
        if len(g) == 1:
            ids.append(g[0])
            continue
        seen = [a for a in g if id_names.get(a) in offered]
        if len(seen) == 1:
            ids.append(seen[0])
        else:
            unsure.append([id_names.get(a, a) for a in (seen or g)])
    return ids, unsure


class Tracker:
    """한 번 읽을 때마다 결과를 넣으면, 선택지·고른 증강을 정리해 state 로 내보낸다"""

    CLOSE_AFTER = 2  # 연속으로 이만큼 못 읽으면 선택 화면이 닫힌 걸로
    PARTIAL_HOLD = 25  # 초. 잘린 이름만 보여도 열린 걸로 보는 최대 시간 (게임 중 비슷한 글자로 안 닫히는 일 없게)

    def __init__(self):
        self.state = {"offers": None, "picked": None, "seq": 0, "pickSeq": 0}
        self._miss = 0
        self._click = None  # 이번 선택 화면에서 마지막으로 클릭한 카드 칸
        self._last = None
        self._full_at = 0.0  # 이름을 2개 이상 읽은 마지막 시각

    @property
    def showing(self):
        return self._last is not None

    def click(self, slot):
        if self._last is not None and slot is not None:
            self._click = slot

    def feed(self, offers3, pos=None, partial=False):
        """partial: 이름이 하나라도 보이면 True → 아직 선택 화면 (마우스를 올려 카드가 바뀌는 동안 닫혔다고 착각하지 않게.
        v0.1.13 실제 PC: 마우스를 올리자 이름이 '포로'·'난사 발동'으로 잘려 읽혀 닫힘으로 판단, 클릭을 못 잡음)"""
        now = time.time()
        if not offers3 and partial and self._last is not None and now - self._full_at < self.PARTIAL_HOLD:
            self._miss = 0
            return
        if offers3:
            self._full_at = now
            self._miss = 0
            if pos and pos != self.state.get("pos"):
                self.state = {**self.state, "pos": pos}
            if offers3 != self._last:  # 새 선택지 (새로고침 포함): 그 전 클릭은 무효
                self._last, self._click = offers3, None
                self.state = {**self.state, "offers": offers3, "seq": self.state["seq"] + 1}
            return
        if self._last is None:
            return
        self._miss += 1
        if self._miss >= self.CLOSE_AFTER:
            p = self._last[self._click] if self._click is not None else None
            self.state = {**self.state, "offers": None, "pos": None, "picked": p, "pickedFrom": self._last,
                          "pickSeq": self.state["pickSeq"] + 1}
            self._last, self._click, self._miss = None, None, 0


# ---------------------------------------------------------------- 윈도우: 화면 잡기 + 글자 인식
class _Capture:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.u, self.g = ctypes.windll.user32, ctypes.windll.gdi32
        self.u.GetDC.restype = wintypes.HDC
        self.u.GetDC.argtypes = [wintypes.HWND]
        self.u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.g.CreateCompatibleDC.restype = wintypes.HDC
        self.g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        self.g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        self.g.SelectObject.restype = wintypes.HGDIOBJ
        self.g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.g.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        self.g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                     ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        self.g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.g.DeleteDC.argtypes = [wintypes.HDC]

    def grab(self, x, y, w, h):
        """화면 (x, y, w, h) → BGRA 바이트 (위에서 아래로)"""
        ct = self.ct

        class BIH(ct.Structure):
            _fields_ = [("biSize", ct.c_uint32), ("biWidth", ct.c_int32), ("biHeight", ct.c_int32),
                        ("biPlanes", ct.c_uint16), ("biBitCount", ct.c_uint16), ("biCompression", ct.c_uint32),
                        ("biSizeImage", ct.c_uint32), ("biXPelsPerMeter", ct.c_int32), ("biYPelsPerMeter", ct.c_int32),
                        ("biClrUsed", ct.c_uint32), ("biClrImportant", ct.c_uint32)]
        sdc = self.u.GetDC(None)
        mdc = self.g.CreateCompatibleDC(sdc)
        bmp = self.g.CreateCompatibleBitmap(sdc, w, h)
        old = self.g.SelectObject(mdc, bmp)
        try:
            self.g.BitBlt(mdc, 0, 0, w, h, sdc, x, y, 0x00CC0020)  # SRCCOPY
            bih = BIH(ct.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ct.create_string_buffer(w * h * 4)
            self.g.GetDIBits(mdc, bmp, 0, h, buf, ct.byref(bih), 0)
            return buf.raw
        finally:
            self.g.SelectObject(mdc, old)
            self.g.DeleteObject(bmp)
            self.g.DeleteDC(mdc)
            self.u.ReleaseDC(None, sdc)


class _Ocr:
    def __init__(self):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        eng = None
        for tag in ("ko", "ko-KR"):
            lang = Language(tag)
            if OcrEngine.is_language_supported(lang):
                eng = OcrEngine.try_create_from_language(lang)
                break
        self.lang = "ko"
        if eng is None:
            # 한국어 글자 인식이 없으면 윈도우 언어로 (영어 이름으로 맞춘다. 검증용 윈도우 가상 PC도 이 경우)
            eng = OcrEngine.try_create_from_user_profile_languages()
            self.lang = "윈도우 언어"
        if eng is None:
            raise RuntimeError("윈도우 글자 인식이 없어요 (설정 > 시간 및 언어 > 언어 > 한국어 > 언어 옵션 > 광학 문자 인식)")
        self.engine = eng
        self._make = None  # 이미지 만들기에 성공한 방법 (한 번 찾으면 계속 쓴다)

    def bitmap(self, bgra, w, h):
        """BGRA 바이트 → SoftwareBitmap. pywinrt 버전마다 받는 인자가 달라서 되는 방법을 찾는다
        (v0.1.12 실제 PC: 'Invalid parameter count' 로 모두 실패했음)"""
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
        fmt, alpha = BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED

        def buffer():
            dw = DataWriter()
            dw.write_bytes(bgra)
            return dw.detach_buffer()

        ways = [
            ("버퍼 4인자", lambda: SoftwareBitmap.create_copy_from_buffer(buffer(), fmt, w, h)),
            ("버퍼 5인자", lambda: SoftwareBitmap.create_copy_from_buffer(buffer(), fmt, w, h, alpha)),
            ("빈 이미지에 복사", lambda: _copy_into(SoftwareBitmap(fmt, w, h, alpha), buffer())),
            ("바이트 바로", lambda: SoftwareBitmap.create_copy_from_buffer(bgra, fmt, w, h)),
        ]
        if self._make:
            ways = [x for x in ways if x[0] == self._make] + [x for x in ways if x[0] != self._make]
        errors = []
        for name, make in ways:
            try:
                bmp = make()
                self._make = name
                return bmp
            except Exception as e:
                errors.append(f"{name}: {str(e)[:60]}")
        raise RuntimeError("이미지 만들기 실패 — " + " / ".join(errors))

    def words(self, bgra, w, h):
        import asyncio
        bmp = self.bitmap(bgra, w, h)
        try:
            res = asyncio.run(self._recognize(bmp))
        except Exception as e:
            raise RuntimeError(f"글자 인식 실패 ({self._make}): {str(e)[:100]}") from None
        out = []
        for line in res.lines:
            for wd in line.words:
                r = wd.bounding_rect
                out.append((wd.text, r.x, r.y, r.width, r.height))
        return out

    async def _recognize(self, bmp):
        return await self.engine.recognize_async(bmp)


def _copy_into(bmp, buf):
    bmp.copy_from_buffer(buf)
    return bmp


class _Mouse:
    """왼쪽 버튼을 새로 누른 순간의 화면 좌표 (GetAsyncKeyState·GetCursorPos 로 읽기만 한다)"""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.u = ctypes.windll.user32
        self.u.GetAsyncKeyState.restype = ctypes.c_short
        self.down = False

    def clicked(self):
        down = bool(self.u.GetAsyncKeyState(0x01) & 0x8000)  # VK_LBUTTON
        hit = None
        if down and not self.down:
            pt = self.wt.POINT()
            self.u.GetCursorPos(self.ct.byref(pt))
            hit = (pt.x, pt.y)
        self.down = down
        return hit


class Reader(threading.Thread):
    """게임 창이 맨 앞일 때 1초마다 카드 이름 줄을 읽는다. game_rect() → ((x, y, w, h) | None, 맨 앞 여부)"""

    def __init__(self, game_rect, names_fn):
        super().__init__(daemon=True, name="증강 인식")
        self.game_rect = game_rect
        self.names_fn = names_fn  # 증강 이름 목록 (데이터 갱신 후 바뀔 수 있어서 함수)
        self.tracker = Tracker()
        self.enabled = True
        self.status = "대기"
        self.beat = None
        self.last_ms = None
        self.near = None  # 반쯤 읽은 글자 (진단 기록용)
        self.shop = None  # 상점 창 위치 (열려 있을 때만)
        self.hud = None  # 증강 칸 아이콘 읽기 (hudicons.HudReader). numpy·Pillow 가 없으면 None
        self.hud_ids = []  # 증강 칸에서 읽은 고른 증강 id (칸 순서)
        self.icon_dir = None
        self.offered = set()  # 이번 게임 선택 화면에서 본 증강 이름 (같은 그림 묶음을 가릴 때)
        self.hud_unsure = []  # 증강 칸에 있지만 묶음 중 어느 것인지 못 정한 것 [이름 목록]

    @property
    def state(self):
        return {**self.tracker.state, "status": self.status, "ms": self.last_ms, "near": self.near, "shop": self.shop,
                "hudPicked": list(self.hud_ids), "hudUnsure": [list(g) for g in self.hud_unsure]}

    def scan_hud(self, cap, rect):
        import numpy as np
        import hudicons
        gx, gy, gw, gh = rect
        l, t, r, b = hudicons.REGION
        x0, y0, x1, y1 = int(gw * l), int(gh * t), int(gw * r), int(gh * b)
        raw = cap.grab(gx + x0, gy + y0, x1 - x0, y1 - y0)
        rgb = np.frombuffer(raw, dtype=np.uint8).reshape(y1 - y0, x1 - x0, 4)[..., [2, 1, 0]]
        groups = self.hud.scan(rgb, gh, (x0, y0))
        self.hud_ids, self.hud_unsure = resolve_groups(groups, self.id_names(), self.offered)

    def run(self):
        if not IS_WINDOWS:
            self.status = "윈도우에서만 동작"
            return
        try:
            cap, ocr, mouse = _Capture(), _Ocr(), _Mouse()
        except Exception as e:
            self.status = f"글자 인식을 쓸 수 없음: {str(e)[:120]}"
            return
        self.lang = ocr.lang
        if self.icon_dir:
            try:
                import hudicons
                self.hud = hudicons.HudReader(hudicons.Templates(self.icon_dir, self.id_names()))
            except Exception as e:  # numpy·Pillow 가 없거나 아이콘을 못 읽으면 이 기능만 끈다
                self.hud_error = str(e)[:120]
        matcher, names = None, None
        while True:
            self.beat = time.time()
            rect = None
            try:
                rect, front = self.game_rect()
                if not (self.enabled and rect and front):
                    self.status = "게임 창 기다리는 중" if self.enabled else "꺼짐"
                    if rect is None:
                        self.tracker.feed(None)
                        self.shop = None
                    time.sleep(INTERVAL)
                    continue
                cur = self.names_fn()
                if cur is not names:
                    names, matcher = cur, Matcher(cur)
                t0 = time.time()
                self.scan(cap, ocr, matcher, rect)
                self.last_ms = int((time.time() - t0) * 1000)
            except Exception as e:
                self.status = f"읽기 오류: {str(e)[:120]}"
            self.wait_clicks(mouse, rect)

    def new_game(self):
        """새 게임: 증강 칸에서 읽은 것을 비운다 (게임 창을 내렸다 올린 것만으로는 비우지 않는다)"""
        if self.hud:
            self.hud.reset()
        self.hud_ids = []
        self.hud_unsure = []
        self.offered = set()

    def id_names(self):
        """{증강 id: 이름} (아이콘 파일 이름이 id)"""
        return getattr(self, "_id_names", {}) or {}

    def wait_clicks(self, mouse, rect):
        """다음 읽기까지 쉬는 동안, 선택 화면이 떠 있으면 클릭한 카드를 기록한다"""
        end = time.time() + INTERVAL
        if not (rect and self.tracker.showing):
            time.sleep(INTERVAL)
            return
        gx, gy, gw, gh = rect
        while time.time() < end:
            hit = mouse.clicked()
            if hit:
                self.tracker.click(card_at(self.tracker.state.get("pos"), (hit[0] - gx) / gw, (hit[1] - gy) / gh))
            time.sleep(CLICK_POLL)

    def scan(self, cap, ocr, matcher, rect):
        gx, gy, gw, gh = rect
        n0, n1 = int(gh * NAME_BAND[0]), int(gh * NAME_BAND[1])
        img = cap.grab(gx, gy + n0, gw, n1 - n0)  # 이름 줄만 (빠르고 설명 글자와 덜 섞인다)
        words = [(t, x, y + n0, w, h) for t, x, y, w, h in ocr.words(img, gw, n1 - n0)]
        offers = find_offers(words, gw, gh, matcher)
        self.offered.update(n for n, _, _ in offers)
        partial = False
        if not offers:
            # 이름이 하나라도 (일부라도) 맞으면 선택 화면이 아직 떠 있는 것 → 읽은 글자를 기록에 남긴다
            segs = [t for t, _, y in segments(words, gap=gw * 0.04) if NAME_BAND[0] * gh <= y <= NAME_BAND[1] * gh]
            partial = any(matcher.match(t)[0] or matcher.partial(t) for t in segs)
            if partial:
                self.near = " | ".join(segs)[:160]
        self.tracker.feed(slots(offers, gw) if offers else None, card_pos(offers, gw, gh) if offers else None, partial)
        self.status = ("증강 선택 화면 읽는 중" if offers else "증강 선택 화면 아님") + f" ({ocr.lang} · {ocr._make})"
        if offers or partial:
            return
        # 상점 창: 열려 있을 때만 게임 화면 상자를 그 옆에 띄운다 (평소엔 게임을 가리지 않게. 2026-10-14 지적)
        s0, s1 = int(gh * SHOP_BAND[0]), int(gh * SHOP_BAND[1])
        sw = [(t, x, y + s0, w, h) for t, x, y, w, h in ocr.words(cap.grab(gx, gy + s0, gw, s1 - s0), gw, s1 - s0)]
        self.shop = find_shop(sw, gw, gh)
        # 증강 칸 아이콘 (고른 증강의 정답): 5번에 한 번 (무거워서)
        self._hud_n = getattr(self, "_hud_n", 0) + 1
        if self.hud and self._hud_n % 5 == 1:
            self.scan_hud(cap, rect)
