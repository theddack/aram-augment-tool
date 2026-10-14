"""증강 선택 화면 읽기 (윈도우 전용): 게임 창의 카드 이름 줄을 윈도우 기본 글자 인식(Windows.Media.Ocr)으로 읽는다.

- 라이엇 게임 중 데이터(Live Client Data)에는 증강이 없다 (2026-10-14 실제 기록 307초, 증강 고른 뒤에도 없음).
- 화면을 읽기만 한다 (게임 메모리·입력에 손대지 않음). 블리츠 같은 프로그램과 같은 방식.
- 고른 증강: 선택 화면이 닫히기 직전에 금색 테두리(마우스를 올린 카드)였던 카드. 하나로 정해지지 않으면 None
  → 화면(app.js)이 '어떤 걸 골랐나요' 버튼을 띄운다.

화면 비율 (실제 화면 2026-10-14, 카드 이름 줄은 게임 창 높이의 약 46%):
"""
import difflib
import re
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"
BAND = (0.15, 0.58)  # 잡는 세로 범위 (게임 창 높이 비율): 카드 위 테두리 ~ 이름 줄
NAME_BAND = (0.38, 0.55)  # 이 안의 글자만 카드 이름으로 본다
FRAME_ABOVE = 0.255  # 이름 줄에서 카드 위 테두리까지 (게임 창 높이 비율)
MIN_RATIO = 0.72  # 이름 비슷한 정도 (OCR 이 한두 글자 틀려도 잡게)
INTERVAL = 1.0  # 초


def norm(s):
    return re.sub(r"[\s\W_]+", "", str(s))


class Matcher:
    """증강 이름 목록과 OCR 글자를 맞춘다"""

    def __init__(self, names):
        self.names = {norm(n): n for n in names if norm(n)}

    def match(self, text):
        t = norm(text)
        if len(t) < 2:
            return None, 0.0
        if t in self.names:
            return self.names[t], 1.0
        best, score = None, 0.0
        for k, n in self.names.items():
            if abs(len(k) - len(t)) > 3:
                continue
            r = difflib.SequenceMatcher(None, t, k).ratio()
            if r > score:
                best, score = n, r
        return (best, score) if score >= MIN_RATIO else (None, score)


def segments(words, gap):
    """OCR 단어들 [(글자, x, y, w, h)] → 같은 줄에서 간격이 gap 보다 크면 나눈 덩어리 [(글자, 가운데 x, y)]
    세 카드 이름이 한 줄로 합쳐져 나와도 카드별로 나눈다"""
    out = []
    rows = {}
    for t, x, y, w, h in words:
        key = round((y + h / 2) / max(h, 1))  # 비슷한 높이끼리
        rows.setdefault(key, []).append((t, x, y, w, h))
    for row in rows.values():
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


def gold_score(pixel_at, cx, frame_y, width):
    """카드 위 테두리 근처의 금색 정도 (0~1). pixel_at(x, y) → (r, g, b)
    마우스를 올린 카드는 금색(빨강 > 파랑), 나머지는 은색(빨강 ≈ 파랑)"""
    best = 0.0
    for dx in range(-int(width * 0.01), int(width * 0.01) + 1, 2):
        for dy in range(-12, 13, 2):
            r, g, b = pixel_at(int(cx + dx), int(frame_y + dy))
            if r > 150:
                best = max(best, (r - b) / 255)
    return best


def picked(history):
    """선택 화면이 닫히기 직전 몇 번의 금색 기록 [{이름: 점수}] → 고른 증강 이름. 정해지지 않으면 None"""
    for g in reversed(history):
        if not g:
            continue
        top = sorted(g.items(), key=lambda kv: -kv[1])
        if top[0][1] >= 0.25 and (len(top) == 1 or top[0][1] - top[1][1] >= 0.15):
            return top[0][0]
        return None
    return None


class Tracker:
    """한 번 읽을 때마다 결과를 넣으면, 선택지·고른 증강을 정리해 state 로 내보낸다"""

    CLOSE_AFTER = 2  # 연속으로 이만큼 못 읽으면 선택 화면이 닫힌 걸로

    def __init__(self):
        self.state = {"offers": None, "picked": None, "seq": 0, "pickSeq": 0}
        self._miss = 0
        self._gold = []
        self._last = None

    def feed(self, offers3, gold, pos=None):
        if offers3:
            self._miss = 0
            if pos and pos != self.state.get("pos"):
                self.state = {**self.state, "pos": pos}
            self._gold = (self._gold + [gold])[-3:]
            if offers3 != self._last:  # 새 선택지 (새로고침 포함)
                self._last = offers3
                self.state = {**self.state, "offers": offers3, "seq": self.state["seq"] + 1}
            return
        if self._last is None:
            return
        self._miss += 1
        if self._miss >= self.CLOSE_AFTER:
            p = picked(self._gold)
            self.state = {**self.state, "offers": None, "pos": None, "picked": p, "pickedFrom": self._last,
                          "pickSeq": self.state["pickSeq"] + 1}
            self._last, self._gold, self._miss = None, [], 0


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
        if eng is None:
            raise RuntimeError("윈도우 한국어 글자 인식이 없어요 (설정 > 시간 및 언어 > 언어 > 한국어 > 언어 옵션 > 광학 문자 인식)")
        self.engine = eng

    def words(self, bgra, w, h):
        import asyncio
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
        dw = DataWriter()
        dw.write_bytes(bgra)
        bmp = SoftwareBitmap.create_copy_from_buffer(dw.detach_buffer(), BitmapPixelFormat.BGRA8, w, h,
                                                     BitmapAlphaMode.PREMULTIPLIED)
        res = asyncio.run(self.engine.recognize_async(bmp))
        out = []
        for line in res.lines:
            for wd in line.words:
                r = wd.bounding_rect
                out.append((wd.text, r.x, r.y, r.width, r.height))
        return out


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

    @property
    def state(self):
        return {**self.tracker.state, "status": self.status, "ms": self.last_ms, "near": self.near}

    def run(self):
        if not IS_WINDOWS:
            self.status = "윈도우에서만 동작"
            return
        try:
            cap, ocr = _Capture(), _Ocr()
        except Exception as e:
            self.status = f"글자 인식을 쓸 수 없음: {str(e)[:120]}"
            return
        matcher, names = None, None
        while True:
            self.beat = time.time()
            try:
                rect, front = self.game_rect()
                if not (self.enabled and rect and front):
                    self.status = "게임 창 기다리는 중" if self.enabled else "꺼짐"
                    if rect is None:
                        self.tracker.feed(None, {})
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
            time.sleep(INTERVAL)

    def scan(self, cap, ocr, matcher, rect):
        gx, gy, gw, gh = rect
        top = int(gh * BAND[0])
        bh = int(gh * (BAND[1] - BAND[0]))
        img = cap.grab(gx, gy + top, gw, bh)
        # 이름 줄만 글자 인식 (빠르고 설명 글자와 덜 섞인다)
        n0, n1 = int(gh * NAME_BAND[0]) - top, int(gh * NAME_BAND[1]) - top
        words = ocr.words(img[n0 * gw * 4: n1 * gw * 4], gw, n1 - n0)
        words = [(t, x, y + n0 + top, w, h) for t, x, y, w, h in words]
        offers = find_offers(words, gw, gh, matcher)
        if not offers:
            # 이름이 하나만 맞으면 선택 화면을 반쯤 읽은 것 → 읽은 글자를 기록에 남긴다 (실제 게임에서 왜 못 읽었는지 보려고)
            segs = [t for t, _, y in segments(words, gap=gw * 0.04) if NAME_BAND[0] * gh <= y <= NAME_BAND[1] * gh]
            if sum(1 for t in segs if matcher.match(t)[0]) == 1:
                self.near = " | ".join(segs)[:160]

        def pixel_at(x, y):
            y -= top
            if not (0 <= x < gw and 0 <= y < bh):
                return (0, 0, 0)
            i = (y * gw + x) * 4
            return img[i + 2], img[i + 1], img[i]
        gold = {n: gold_score(pixel_at, x, y - gh * FRAME_ABOVE, gw) for n, x, y in offers}
        self.tracker.feed(slots(offers, gw) if offers else None, gold, card_pos(offers, gw, gh) if offers else None)
        self.status = "증강 선택 화면 읽는 중" if offers else "증강 선택 화면 아님"
