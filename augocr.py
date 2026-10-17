"""증강 선택 화면 읽기 (윈도우 전용): 게임 창의 카드 이름 줄을 윈도우 기본 글자 인식(Windows.Media.Ocr)으로 읽는다.

- 라이엇 게임 중 데이터(Live Client Data)에는 증강이 없다 (2026-10-14 실제 기록 307초, 증강 고른 뒤에도 없음).
- 화면을 읽기만 한다 (게임 메모리·입력에 손대지 않음). 블리츠 같은 프로그램과 같은 방식.
- 고른 증강: 선택 화면이 떠 있는 동안 처음 클릭한 카드 (마우스 위치·왼쪽 버튼 상태만 읽는다, 게임에 입력을 보내지 않음).
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
SHOP_BAND = (0.06, 0.45)  # 상점 창 위쪽 탭('추천 · 모든 아이템 · 아이템 세트')을 찾는 범위 (실제 기록: 탭 줄 0.10~0.29.
# 2026-10-15 16:10 창(2520x1575)에선 상점 창 윗테두리가 0.31 → 탭이 0.32 밖이라 한 번도 못 찾음)
SHOP_PROBE_AREA = (0.25, 0.04, 0.85, 0.84)  # 못 알아봤을 때 남기는 그림 범위 (가로·세로 비율). 채팅(왼쪽 아래)은 빼고 가운데 상점 자리
# 상점 창 아래쪽 버튼 줄 '판매 · 되돌리기' (영어 Sell · Undo). 2026-10-15 베인 판 증강 칸 그림에 상점이 열린 채 찍혔다:
# 2520x1575 창에서 버튼 줄 y ≈ 0.81, x ≈ 0.35~0.63. 위쪽 탭보다 글자가 또렷하고 띠가 작아 가볍다 → 먼저 본다
SHOP_BOTTOM = ((0.25, 0.75), (0.70, 0.90))
SHOP_FIND_MAX = 4  # 상점에서 자리를 찾아볼 아이템 수 (다음 구매 3 + 바꿀 것 1)
# 상점 안 아이템 자리 찾기 (시험) 끔: 실제 판 4번 모두 확실한 결과 0개이고 한 번에 7.5초(사미라 판)라 게임을 무겁게 한다.
# 찾는 방식을 바꾸기 전까진 상점 그림만 남긴다
SHOP_FIND = False
SHOP_IMGS, SHOP_IMG_GAP = 2, 300.0  # 상점을 알아봤을 때 상점 창 그림: 한 판 최대 장수, 다음 장까지 최소 간격(초)  # (가로 범위, 세로 범위)
OCR_HEIGHT = 900  # 글자 인식은 게임 창 높이가 이보다 크면 줄여서 읽는다 (글자가 커서 지장 없고 훨씬 가볍다. 렉 지적 2026-10-14)
SHOP_PROBES = 3  # 한 판에 상점을 못 알아본 그림을 남기는 최대 횟수 (2026-10-15 실제 판: 상점을 한 번도 못 알아봄)
SHOP_EVERY = 2  # 상점 탭은 이 횟수마다 한 번만 읽는다 (1초마다 → 2초마다)
HUD_EVERY = 30.0  # 초. 증강 칸 찾기 기본 간격 (증강을 고른 직후엔 바로)
SLOW_MS = 250  # 글자 읽기 한 바퀴가 최근 평균 이만큼 넘으면 '느림' → 진단용 그림·덜 중요한 읽기부터 줄인다 (자동 보호)
SLOW_N = 20  # 최근 몇 바퀴로 보는지
HUD_SETTLED = 60.0  # 초. 고른 증강을 증강 칸에서 다 찾았으면 다음 고르기 전까지는 이만큼에 한 번만 (확인용)
MAX_AUGMENTS = 4  # 한 판에 고르는 증강 수. 다 찾으면 증강 칸은 다시 안 읽는다 (새로 고르면 그때만)
HUD_EVERY_MISSING = 60.0  # 초. 그 판 선택 화면을 하나도 못 봤으면(증강 215개 전부와 비교해 무겁다) 더 드물게
CARD_Y = (0.18, 0.66)  # 카드 몸통 (이 안을 클릭해야 고른 것. 그 밑 새로고침 버튼은 제외)
CARD_HALF_W = 0.09  # 카드 반폭 (게임 창 너비 비율)
MIN_RATIO = 0.72  # 이름 비슷한 정도 (OCR 이 한두 글자 틀려도 잡게)
INTERVAL = 1.0  # 초 (글자 읽기: 증강 선택 화면이 떠 있거나 막 닫혔을 때)
CALM_EVERY = 3  # 레벨이 그대로인 전투 중엔 증강 이름 줄을 이만큼에 한 번만 (2초 x 3 = 6초). 글자 인식이 CPU 를 가장 많이 쓴다
FAST_AFTER_LEVEL = 20  # 초. 레벨이 오르면 이만큼은 매번 (증강은 레벨에 맞춰 생긴다. 미뤘다 여는 화면은 6초 안에)
IDLE_INTERVAL = 2.0  # 초 (선택 화면이 아닐 때. 뜨면 2초 안에 알아채고 그때부터 1초마다)
CLICK_POLL = 0.03  # 초 (선택 화면이 떠 있을 때만 마우스 버튼 확인)
# 상점 여닫기 키·클릭을 보면 2초를 기다리지 않고 곧바로 상점 창을 읽는다 (2026-10-17 지적: 상점을 열고 닫을 때 상자가 뜨다 말다.
# 우르곳 판: 6:40 에 상점에서 두 번 샀는데 2초 간격 읽기 사이에 열고 닫아서 한 번도 못 알아봄). 키 상태만 읽고 게임에 입력은 안 보낸다
SHOP_KEYS = (0x50, 0x1B)  # P(상점 기본 키) · ESC(닫기)
SHOP_KEY_DELAY = 0.25  # 초. 키를 누른 뒤 상점 창이 그려질 때까지
SHOP_CLICK_GAP = 2.0  # 초. 상점이 열려 있을 때 왼쪽 클릭(닫기 ✕)으로 바로 읽기는 이 간격에 한 번만


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
        # 잘린 이름이 여러 증강 이름의 앞부분이면 정하지 않는다 (2026-10-17 사미라 판: 설명 창이 가려 '지옥불'만 읽혀
        # 지옥불 영혼으로 짐작 → 실제는 지옥불 난사 발동. 그 짐작 상태에서 카드를 눌러 고른 증강이 틀렸다)
        heads = {n for k, n in self.names.items() if len(k) > len(t) and k.startswith(t)}
        if len(heads) > 1:
            return None, 0.0
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


def shop_bottom_key(width, height):
    return (int(width), int(height))


def find_shop(words, width, height):
    """상점 창이 열려 있으면 위쪽 탭 줄의 위치 {left, right, top} (게임 창 비율), 아니면 None.
    탭 글자 '모든 아이템' 과 '아이템 세트'(또는 '추천')가 한 줄에 있으면 상점 (영어: All Items · Item Sets · Recommended)"""
    for row in rows_of(words):
        text = norm("".join(w[0] for w in sorted(row, key=lambda r: r[1]))).lower()
        ko = "모든아이템" in text and ("아이템세트" in text or "추천" in text)
        en = "allitems" in text and ("itemsets" in text or "recommended" in text)  # 영어 클라이언트·검증용 가상 PC
        # 아래쪽 버튼 줄 (판매 · 되돌리기). 판매는 아이템을 안 골랐으면 흐려서 못 읽는다 → 되돌리기만으로도.
        # 오른쪽 칸 제목 '상위 아이템' 은 탭 글자('모든'→'보든')보다 잘 읽힌다 (2026-10-17 카이사 판 놓친 3번 모두 읽힘)
        ko = ko or "되돌리기" in text or "상위아이템" in text
        en = en or ("sell" in text and "undo" in text)
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


OWN_BOX_RIGHT = 0.22  # 애드온 상자(게임 창 왼쪽 위 고정) 오른쪽 끝 (너비 비율). 이 안의 글자는 우리 상자라 증강 이름으로 보지 않는다


def find_offers(words, width, height, matcher):
    """카드 이름 덩어리 → [(증강 이름, 가운데 x, 이름 y)] 왼쪽부터. 2개 이상 맞아야 선택 화면으로 본다"""
    found = {}
    for text, cx, y in segments(words, gap=width * 0.04):
        if not (NAME_BAND[0] * height <= y <= NAME_BAND[1] * height):
            continue
        if cx < width * OWN_BOX_RIGHT:  # 우리 상자 글자 ('허리케인 > 몰락한 왕의 검' 을 읽었다, 2026-10-14 실제 기록)
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


SURE_SCORE = 0.93  # 선택 화면을 못 본 경우(게임 중간에 켬) 증강 칸만으로 인정할 점수
RECOVER_TRIES = 3  # 넓혀 찾기 횟수 (2초 간격)
VERIFY_TRIES = 3  # 클릭으로 잡은 고르기는 증강 칸을 이만큼만 본다 (2초 간격)
VERIFY_FOR = 25  # 초. 고른 뒤 이만큼 증강 칸에서 그 증강을 찾는다 (선택 화면이 사라지는 연출·상점 창에 가려질 때)


def resolve_groups(groups, id_names, offered, scores=None):
    """증강 칸에서 읽은 그림 묶음들 → (정한 증강 id 목록, 못 정한 묶음 [이름 목록]).
    - 그림이 같은 증강이 여럿이면(돌진=앙파상) 이번 게임 선택 화면에 떴던 이름으로 가린다
    - 선택 화면을 본 게임이면 거기 떴던 증강만 인정한다 (안 뜬 증강을 골랐을 리 없다. 엉뚱한 판정 막기)
    - 선택 화면을 못 봤으면(게임 중간에 켬) 점수가 SURE_SCORE 이상일 때만"""
    ids, unsure = [], []
    for i, g in enumerate(groups):
        g = list(g)
        sc = scores[i] if scores and i < len(scores) else 1.0
        seen = [a for a in g if id_names.get(a) in offered]
        if offered:
            if len(seen) == 1:
                ids.append(seen[0])
            elif len(seen) > 1:
                unsure.append([id_names.get(a, a) for a in seen])
            continue  # 선택 화면에 없던 그림은 무시
        if sc < SURE_SCORE:
            continue
        if len(g) == 1:
            ids.append(g[0])
        else:
            unsure.append([id_names.get(a, a) for a in g])
    return ids, unsure


class Tracker:
    """한 번 읽을 때마다 결과를 넣으면, 선택지·고른 증강을 정리해 state 로 내보낸다"""

    CLOSE_AFTER = 2  # 연속으로 이만큼 못 읽으면 선택 화면이 닫힌 걸로
    PARTIAL_HOLD = 25  # 초. 잘린 이름만 보여도 열린 걸로 보는 최대 시간 (게임 중 비슷한 글자로 안 닫히는 일 없게)
    # 초. 카드를 안 누르고 닫혔으면 이만큼 기다렸다가 '못 정함'으로 (선택 화면을 잠깐 내렸다 다시 띄우는 경우.
    # 2026-10-17 우르곳 판: 첫 선택 화면이 8초 만에 사라졌다가 2초 뒤 같은 선택지로 다시 떠서 '못 정함'을 먼저 알렸다)
    REOPEN_WAIT = 20

    def __init__(self):
        self.state = {"offers": None, "picked": None, "seq": 0, "pickSeq": 0}
        self._miss = 0
        self._click = None  # 이번 선택 화면에서 처음 클릭한 카드 칸
        self._last = None
        self._full_at = 0.0  # 이름을 2개 이상 읽은 마지막 시각
        self._pending = None  # 클릭 없이 닫힌 선택지 {"from": 선택지, "at": 시각} (REOPEN_WAIT 안에 다시 뜨면 이어서)

    @property
    def showing(self):
        return self._last is not None

    def click(self, slot):
        # 카드는 누르는 순간 골라진다 → 처음 누른 카드. 그 뒤 클릭은 사라지는 카드 위를 누른 것
        # (2026-10-17 카이사: 광휘의 검을 누르고 0.6초 뒤 옆 카드를 또 눌러 '빵과 치즈'로 잘못 알림. 예전 기록 3판도 첫 클릭이 맞았다)
        if self._last is not None and slot is not None and self._click is None:
            self._click = slot

    def feed(self, offers3, pos=None, partial=False):
        """partial: 이름이 하나라도 보이면 True → 아직 선택 화면 (마우스를 올려 카드가 바뀌는 동안 닫혔다고 착각하지 않게.
        v0.1.13 실제 PC: 마우스를 올리자 이름이 '포로'·'난사 발동'으로 잘려 읽혀 닫힘으로 판단, 클릭을 못 잡음)"""
        now = time.time()
        if not offers3 and partial and self._last is not None and now - self._full_at < self.PARTIAL_HOLD:
            self._miss = 0
            return
        if self._pending and (offers3 or now - self._pending["at"] >= self.REOPEN_WAIT):
            frm, self._pending = self._pending["from"], None
            if not offers3 or offers3 != frm:  # 안 돌아왔거나 다른 선택지(다음 선택 화면)가 떴다 → 그때 못 정함으로
                self.state = {**self.state, "picked": None, "pickedFrom": frm, "replaces": None, "by": "click",
                              "pickSeq": self.state["pickSeq"] + 1}
        if offers3:
            self._full_at = now
            self._miss = 0
            if pos and pos != self.state.get("pos"):
                self.state = {**self.state, "pos": pos}
            if (self._last is not None and offers3 != self._last and None in offers3
                    and all(n is None or n == old for n, old in zip(offers3, self._last))):
                return  # 일부 칸만 못 읽음(설명 창·마우스가 가림) → 같은 선택지 그대로 (클릭도 그대로)
            if offers3 != self._last:  # 새 선택지 (새로고침 포함): 그 전 클릭은 무효
                self._last, self._click = offers3, None
                self.state = {**self.state, "offers": offers3, "seq": self.state["seq"] + 1}
            return
        if self._last is None:
            return
        self._miss += 1
        if self._miss >= self.CLOSE_AFTER:
            if self._click is None:
                # 카드를 안 눌렀다: 화면만 내렸을 수 있다 → 카드 표시는 지우고, 고른 것은 REOPEN_WAIT 뒤에 정한다
                self._pending = {"from": self._last, "at": now}
                self.state = {**self.state, "offers": None, "pos": None}
            else:
                self.state = {**self.state, "offers": None, "pos": None, "picked": self._last[self._click], "pickedFrom": self._last,
                              "replaces": None, "by": "click", "pickSeq": self.state["pickSeq"] + 1}
            self._last, self._click, self._miss = None, None, 0


# ---------------------------------------------------------------- 윈도우: 화면 잡기 + 글자 인식
def lower_thread(level=-2):
    """지금 스레드의 우선순위를 낮춘다. -2 = 가장 낮게 (무거운 아이콘 비교가 게임·애드온 창보다 뒤에 돌게),
    -1 = 조금 낮게 (글자 읽기: 게임보다는 뒤에, 너무 낮으면 클릭을 놓친다)"""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetCurrentThread.restype = ctypes.c_void_p
        k.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        k.SetThreadPriority(k.GetCurrentThread(), level)  # -2 LOWEST, -1 BELOW_NORMAL
    except Exception:
        pass


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
        self.g.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        self.g.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self.g.DeleteDC.argtypes = [wintypes.HDC]

    def grab(self, x, y, w, h, tw=None, th=None):
        """화면 (x, y, w, h) → BGRA 바이트 (위에서 아래로). tw·th 를 주면 그 크기로 줄여서 (StretchBlt, 부드럽게)"""
        tw, th = tw or w, th or h
        ct = self.ct

        class BIH(ct.Structure):
            _fields_ = [("biSize", ct.c_uint32), ("biWidth", ct.c_int32), ("biHeight", ct.c_int32),
                        ("biPlanes", ct.c_uint16), ("biBitCount", ct.c_uint16), ("biCompression", ct.c_uint32),
                        ("biSizeImage", ct.c_uint32), ("biXPelsPerMeter", ct.c_int32), ("biYPelsPerMeter", ct.c_int32),
                        ("biClrUsed", ct.c_uint32), ("biClrImportant", ct.c_uint32)]
        sdc = self.u.GetDC(None)
        mdc = self.g.CreateCompatibleDC(sdc)
        bmp = self.g.CreateCompatibleBitmap(sdc, tw, th)
        old = self.g.SelectObject(mdc, bmp)
        try:
            if (tw, th) == (w, h):
                self.g.BitBlt(mdc, 0, 0, w, h, sdc, x, y, 0x00CC0020)  # SRCCOPY
            else:
                self.g.SetStretchBltMode(mdc, 4)  # HALFTONE (줄여도 글자가 덜 뭉개지게)
                self.g.StretchBlt(mdc, 0, 0, tw, th, sdc, x, y, w, h, 0x00CC0020)
            bih = BIH(ct.sizeof(BIH), tw, -th, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ct.create_string_buffer(tw * th * 4)
            self.g.GetDIBits(mdc, bmp, 0, th, buf, ct.byref(bih), 0)
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

    def pressed(self, vk):
        """키를 새로 누른 순간만 True (누르고 있는 동안은 한 번만)"""
        downs = self.__dict__.setdefault("keys", {})
        down = bool(self.u.GetAsyncKeyState(vk) & 0x8000)
        hit = down and not downs.get(vk, False)
        downs[vk] = down
        return hit

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
        self.rec = None  # 한 판 기록 (gamelog.GameRecorder)
        self.on_change = None  # 선택지·고른 증강·상점 열림이 바뀌면 부른다 (애드온 화면을 바로 깨워 카드 평가를 늦지 않게)
        self.hud_unsure = []  # 증강 칸에 있지만 묶음 중 어느 것인지 못 정한 것 [이름 목록]
        # 증강 칸은 그 판 선택 화면에 떴던 증강하고만 비교한다 (215개 전부와 비교하던 전체 모드보다 훨씬 가볍다.
        # 안 뜬 증강은 어차피 인정하지 않았다). 선택 화면을 하나도 못 봤으면(게임 중간에 켬) 전부와 · 60초마다.
        # numpy·Pillow 는 게임에서 처음 읽을 때 불러온다
        self.on_heavy = None  # 한 번 크게 쓰고 끝난 작업 뒤에 부른다 (메모리 반납)
        self.live_fn = None  # 게임 데이터 (레벨·죽음)를 돌려주는 함수
        self._level = None
        self._fast_until = 0.0
        self.size_hint = None  # 게임 창 높이 → 지난 게임에서 찾은 증강 칸 아이콘 크기 (함수)
        self.size_save = None  # (게임 창 높이, 크기) 를 저장하는 함수
        self._perf = {}  # 종류별 처리 시간(ms) 목록: 이름 줄·상점·증강 칸·넓혀 찾기 (한 판 기록에 요약)
        self._lock = threading.Lock()  # 증강 칸 스레드의 결과 반영 ↔ 새 게임 시작
        self._recover = None  # 넓혀 찾기 (선택지를 잘못 읽었을 때) {"from", "click", "tries", "known"}
        self._picked_names = set()  # 이번 게임에서 고른 증강 이름 (넓혀 찾기에서 빼려고)
        self._verify = None  # 확인 중인 고르기 {"from": [이름 3개], "click": 클릭으로 짐작한 이름, "until": 시각}
        self._fix = None  # 증강 칸으로 고친 결과 (증강 칸 스레드 → 읽기 스레드로 넘긴다)
        # 확인 시간 안에 증강 칸에서 못 찾은 클릭 짐작 [{"from", "click"}]. 나중에 증강 칸에 그 선택지 중 다른 것이
        # 나타나면 그걸로 고친다 (2026-10-15 케일 판: 확인은 2초 차이로 끝났고, 틀린 짐작 '극악무도'가 끝까지 남아
        # 고른 증강이 4개로 차서 마지막 선택 화면 카드가 안 떴다)
        self._unverified = []
        # 증강 칸 자리 → 그 자리에서 정해진 증강 id. 고른 증강은 칸 순서대로 들어가고 바뀌지 않는다.
        # 그림이 같은(색만 다른) 증강 쌍이 있어 넓혀 찾기가 이미 정한 칸을 다른 증강으로 읽었다
        # (2026-10-15 베인 판: 시작부터 끝까지 칸 → 포로 쇄도, 취약 칸 → 궁극의 히드라. 실제로 고른 부메랑 투척을 포로 쇄도로 '고침')
        self._slot_ids = {}
        self._shop_probe = 0.0  # 아이템을 산 직후(상점이 열려 있었을 것) 상점을 못 알아봤으면 이 시각까지 한 번 그림을 남긴다
        self._shop_probes = 0
        self._shop_imgs, self._shop_img_at = 0, 0.0
        self._inv = None
        self.shop_targets = []  # 상점에서 자리를 찾아볼 아이템 id (화면이 보낸 추천·바꿀 아이템)
        self._shop_icon = None  # 찾은 상점 아이콘 크기 (게임 창 높이 비율)
        self._last_rect = None
        self.protect = False  # 자동 보호 중 (느릴 때 덜 중요한 일부터 줄임)
        self._load = []  # 최근 글자 읽기 한 바퀴 시간(ms)
        self._counts = {}  # 한 판 동안 센 것: 상점 읽기·알아봄, 보호 켬 등 (기록 요약)
        self._bottom_ok = set()  # 상점 아래 버튼 줄로 상점을 알아본 적이 있는 게임 창 크기 (그러면 위쪽 띠는 안 읽는다)

    @property
    def state(self):
        return {**self.tracker.state, "status": self.status, "ms": self.last_ms, "near": self.near, "shop": self.shop,
                "hudPicked": list(self.hud_ids), "hudUnsure": [list(g) for g in self.hud_unsure]}

    def scan_hud(self, cap, rect, game_n=None):
        import numpy as np
        import hudicons
        gx, gy, gw, gh = rect
        l, t, r, b = hudicons.REGION
        x0, y0, x1, y1 = int(gw * l), int(gh * t), int(gw * r), int(gh * b)
        raw = cap.grab(gx + x0, gy + y0, x1 - x0, y1 - y0)
        rgb = np.frombuffer(raw, dtype=np.uint8).reshape(y1 - y0, x1 - x0, 4)[..., [2, 1, 0]]
        if self.rec and self.offered and not getattr(self, "_area_saved", False) and not self.protect:
            # 증강 칸이 화면 어디 있는지 보려고 한 판에 한 장: 아래 가운데만 (채팅·이름이 들어가지 않게 좌우 30% 는 뺀다)
            # 해상도에 따라 HUD 가 왼쪽으로 치우친다 (2520x1575: 초상화가 너비 33%) → 12~70%, 아래 20% 띠
            ax0, ax1, ay0 = int(gw * 0.12), int(gw * 0.70), int(gh * 0.80)
            self.rec.event("hudarea", {"image": self.rec.image("hudarea", cap.grab(gx + ax0, gy + ay0, ax1 - ax0, gh - ay0), ax1 - ax0, gh - ay0),
                                       "box": [0.12, 0.80, 0.70, 1.0], "window": [gw, gh]})
            self._area_saved = True
        groups = self.hud.scan(rgb, gh, (x0, y0))
        if game_n is not None and game_n != getattr(self, "_game_n", 0):
            return  # 읽는 동안 새 게임이 시작됐다 → 지난 게임 결과는 기록에도 남기지 않고 버린다
        best = {g: sc for g, sc, _, _ in self.hud.last}
        ids, unsure = resolve_groups(groups, self.id_names(), self.offered, [best.get(g, 0.0) for g in groups])
        if (ids, unsure) != (self.hud_ids, self.hud_unsure) or not getattr(self, "_hud_logged", False):
            names = self.id_names()
            data = {"picked": [names.get(i, i) for i in ids], "unsure": unsure, "size": self.hud.size,
                    "seen": [{"group": [names.get(a, a) for a in g], "score": round(sc, 3), "x": round(x / gw, 3), "y": round(y / gh, 3)}
                             for g, sc, x, y in self.hud.last]}
            if self.hud.last and self.rec:
                # 증강 칸 부분만 잘라서 (찾은 아이콘들 둘레)
                sz = (self.hud.size or 0.04) * gh
                xs = [x for _, _, x, _ in self.hud.last]
                ys = [y for _, _, _, y in self.hud.last]
                bx0, by0 = max(0, int(min(xs) - sz)), max(0, int(min(ys) - sz))
                bx1, by1 = min(gw, int(max(xs) + 3 * sz)), min(gh, int(max(ys) + 3 * sz))
                data["image"] = self.rec.image("hud", cap.grab(gx + bx0, gy + by0, bx1 - bx0, by1 - by0), bx1 - bx0, by1 - by0)
            if ids or unsure or self.hud.last:
                self._rec("hud", data)
                self._hud_logged = True
        with self._lock:  # 결과 반영 중에 새 게임이 시작되지 않게 (new_game 과 같은 잠금)
            if game_n is not None and game_n != getattr(self, "_game_n", 0):
                return
            sz = (self.hud.size or 0.04) * rect[3]
            pos = {g: (round(y / sz), round(x / sz)) for g, _, x, y in self.hud.last}
            self._apply_hud(groups, best, ids, unsure, pos)

    def _apply_hud(self, groups, best, ids, unsure, pos=None):
        pos = pos or {}
        names = self.id_names()
        # 이미 정해진 칸은 그 증강으로 (쌍둥이 그림으로 바뀌지 않게). 나머지는 새 칸
        fixed = {g: self._slot_ids[pos[g]] for g in groups if pos.get(g) in self._slot_ids}
        fresh = [g for g in groups if g not in fixed]
        rc = self._recover
        if rc and fixed:
            # 넓혀 찾기는 새 칸만 본다. 새 칸 그림 묶음에 클릭 짐작이 들어 있으면 짐작이 맞은 것
            by_name = {n: i for i, n in names.items()}
            hit = [g for g in fresh if by_name.get(rc["click"]) in g]
            if hit:
                self._recover = None
                self._rec("recover", {"from": rc["from"], "result": rc["click"], "slot": True})
                self._picked_names.add(rc["click"])
                rc = None
        if rc:
            rc["tries"] -= 1
            sure, _ = resolve_groups(fresh, names, set(), [best.get(g, 0.0) for g in fresh])  # 아주 확실한 것만 (SURE_SCORE)
            # 실패해도 왜인지 남기게: 아는 것 말고 가장 닮았던 점수·비교한 그림 수
            other = [sc for g, sc in best.items() if not any(a in rc["known"] for a in g)]
            rc["best"] = round(max([rc.get("best", 0.0)] + other), 3)
            rc["groups"] = len(self.hud.tpl.groups)
            new = [i for i in sure if i not in rc["known"] and names.get(i) not in self._picked_names]
            if len(new) == 1:
                self._recover = None
                self._rec("recover", {"from": rc["from"], "result": names.get(new[0])})
                self.offered.add(names.get(new[0]))
                self._picked_names.add(names.get(new[0]))
                if names.get(new[0]) != rc["click"]:
                    self._fix = (names.get(new[0]), rc["from"], rc["click"])
            ids, unsure = resolve_groups(groups, names, self.offered, [best.get(g, 0.0) for g in groups])
        if pos:
            # 칸 순서(위 줄 왼쪽부터)대로: 정해진 칸은 그 증강, 새 칸에서 하나로 정해지면 그 칸에 기록
            order = sorted(groups, key=lambda g: pos.get(g, (99, 99)))
            out, unsure2 = [], []
            for g in order:
                if g in fixed:
                    out.append(fixed[g])
                    continue
                r, u = resolve_groups([g], names, self.offered, [best.get(g, 0.0)])
                if r and r[0] not in out and r[0] not in self._slot_ids.values():
                    out.append(r[0])
                    if not self._recover and g in pos:
                        self._slot_ids[pos[g]] = r[0]
                unsure2 += u
            ids, unsure = out, unsure2
        v = self._verify
        if v:
            found = [names.get(i) for i in ids if names.get(i) in v["from"]]
            if len(found) == 1:
                self._verify = None
                self._rec("verify", {"from": v["from"], "click": v["click"], "result": found[0]})
                self._picked_names.add(found[0])
                if found[0] != v["click"]:
                    self._fix = (found[0], v["from"], v["click"])
        self._settle_unverified(ids)
        changed = ids != self.hud_ids
        self.hud_ids, self.hud_unsure = ids, unsure
        if changed:
            self._changed()

    def _settle_unverified(self, ids):
        """확인 못 한 클릭 짐작: 증강 칸에 짐작한 것이 나타나면 맞은 것, 그 선택지 중 다른 것이 새로 나타나면 그걸로 고친다"""
        if not self._unverified or self._fix:
            return
        names = self.id_names()
        hud = [names.get(i) for i in ids]
        for u in list(self._unverified):
            if u["click"] in hud:
                self._unverified.remove(u)
                continue
            others = self._picked_names - {u["click"]}
            new = [n for n in hud if n in u["from"] and n not in others]
            if len(new) == 1:
                self._unverified.remove(u)
                self._rec("verify", {"from": u["from"], "click": u["click"], "result": new[0], "late": True})
                self._picked_names.discard(u["click"])
                self._picked_names.add(new[0])
                self._fix = (new[0], u["from"], u["click"])
                return

    def run(self):
        if not IS_WINDOWS:
            self.status = "윈도우에서만 동작"
            return
        lower_thread(-1)  # 화면 잡기·글자 읽기가 게임 스레드를 밀어내지 않게
        try:
            cap, ocr, mouse = _Capture(), _Ocr(), _Mouse()
        except Exception as e:
            self.status = f"글자 인식을 쓸 수 없음: {str(e)[:120]}"
            return
        self.lang = ocr.lang
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
                self._watch_load(self.last_ms)
            except Exception as e:
                self.status = f"읽기 오류: {str(e)[:120]}"
                self._rec("error", {"error": str(e)[:200]}, dedupe=True)
            self.wait_clicks(mouse, rect)

    def _time(self, kind, t0):
        self._perf.setdefault(kind, []).append((time.time() - t0) * 1000)

    def perf_summary(self):
        """한 판 동안의 처리 시간 요약 {종류: {n, avg, p95, max}} (ms). 부르면 비운다"""
        out = {}
        for k, v in self._perf.items():
            if v:
                v = sorted(v)
                out[k] = {"n": len(v), "avg": round(sum(v) / len(v)), "p95": round(v[min(len(v) - 1, int(len(v) * 0.95))]),
                          "max": round(v[-1])}
        self._perf = {}
        if self._counts:
            out["횟수"] = dict(self._counts)
            self._counts = {}
        return out

    def _changed(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def _rec(self, kind, data, dedupe=False):
        if self.rec:
            self.rec.event(kind, data, dedupe=dedupe)

    def new_game(self):
        """새 게임: 증강 칸에서 읽은 것을 비운다 (게임 창을 내렸다 올린 것만으로는 비우지 않는다)"""
        with self._lock:
            self._new_game()

    def _new_game(self):
        self._hud_logged = False
        self._area_saved = False
        # 게임 시작 직후엔 첫 선택 화면을 기다린다 (그 전에 읽으면 증강 215개 전부와 비교해야 해서 무겁다)
        self._hud_at = time.time() + HUD_EVERY_MISSING
        self._game_n = getattr(self, "_game_n", 0) + 1
        self._level = None
        self._verify = None
        self._fix = None
        self._recover = None
        self._picked_names = set()
        self._unverified = []
        self._shop_probes = 0
        self._shop_probe = 0.0
        self._slot_ids = {}
        self._shop_imgs, self._shop_img_at = 0, 0.0
        self._inv = None
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
            # 선택 화면이 아니면 2초마다 (글자 인식이 CPU 를 가장 많이 쓴다). 직전 10초 안에 떴었으면 1초 (새로고침·다음 화면)
            recent = time.time() - getattr(self.tracker, "_full_at", 0) < 10
            self.wait_shop_keys(mouse, time.time() + (INTERVAL if recent else IDLE_INTERVAL), bool(rect))
            return
        gx, gy, gw, gh = rect
        while time.time() < end:
            hit = mouse.clicked()
            if hit:
                rx, ry = (hit[0] - gx) / gw, (hit[1] - gy) / gh
                slot = card_at(self.tracker.state.get("pos"), rx, ry)
                self.tracker.click(slot)
                self._rec("click", {"x": round(rx, 3), "y": round(ry, 3), "slot": slot})
            time.sleep(CLICK_POLL)

    def wait_shop_keys(self, mouse, end, active=True):
        """end 까지 쉬되, 상점 여닫기(P·ESC·왼쪽 클릭)를 보면 SHOP_KEY_DELAY 뒤 바로 돌아가 상점 창을 읽게 한다"""
        while time.time() < end:
            if active and mouse is not None:
                key = any(mouse.pressed(vk) for vk in SHOP_KEYS)
                # 클릭은 상점이 열려 있을 때만 (✕·바깥 클릭으로 닫기). 닫혀 있을 때 클릭마다 읽었더니 한 판 215번 (사미라 판, 싸울 때 클릭)
                click = (mouse.clicked() is not None and bool(self.shop)
                         and time.time() - getattr(self, "_shop_click_at", 0.0) >= SHOP_CLICK_GAP)
                if key or click:
                    if click:
                        self._shop_click_at = time.time()
                    self._shop_now = True
                    self._count("상점 키" if key else "상점 클릭")
                    time.sleep(SHOP_KEY_DELAY)
                    return
            time.sleep(CLICK_POLL)

    def read_band(self, cap, ocr, rect, band, xr=(0.0, 1.0)):
        """게임 창의 가로 띠를 (크면 줄여서) 읽는다 → (게임 창 좌표 단어들, 이미지 바이트, 이미지 너비, 높이). xr = 가로 범위"""
        gx, gy, gw, gh = rect
        y0, y1 = int(gh * band[0]), int(gh * band[1])
        x0, x1 = int(gw * xr[0]), int(gw * xr[1])
        k = min(1.0, OCR_HEIGHT / gh)
        tw, th = max(1, int((x1 - x0) * k)), max(1, int((y1 - y0) * k))
        t0 = time.time()
        img = cap.grab(gx + x0, gy + y0, x1 - x0, y1 - y0, tw, th)
        self._time("화면 잡기", t0)  # 게임이 끊기면 화면 잡기(화면 합성에서 읽어 오기)가 느려졌는지 기록으로 본다
        words = [(t, x / k + x0, y / k + y0, w / k, h / k) for t, x, y, w, h in ocr.words(img, tw, th)]
        return words, img, tw, th

    def apply_fix(self):
        """증강 칸이 고른 증강을 알려 주면 클릭으로 짐작한 것을 고친다 (못 정했으면 채운다)"""
        fix, self._fix = self._fix, None
        if not fix:
            return
        name, frm, click = fix
        st = self.tracker.state
        self.tracker.state = {**st, "picked": name, "pickedFrom": frm, "replaces": click, "by": "hud",
                              "pickSeq": st["pickSeq"] + 1}
        self._rec("picked", {"picked": name, "from": frm, "by": "hud", "replaces": click})
        self._changed()

    def scan(self, cap, ocr, matcher, rect):
        gx, gy, gw, gh = rect
        self.apply_fix()
        # 상점이 열려 있으면 증강 선택 화면은 아니다 → 이름 줄은 3번에 한 번만 (상점 아이템 이름을 증강 일부로 읽고 CPU 도 아끼게)
        self._name_n = getattr(self, "_name_n", 0) + 1
        if self.shop and not self.tracker.showing and self._name_n % 3:
            self.scan_shop_only(cap, ocr, rect)
            return
        if getattr(self, "_shop_now", False) and not self.tracker.showing:
            self.scan_shop_only(cap, ocr, rect)  # 상점 키를 봤다: 이름 줄은 건너뛰고 상점부터 (빨리 띄우게)
            return
        if not self.urgent() and self._name_n % CALM_EVERY:
            self._hud_tick(rect)
            self.scan_shop_only(cap, ocr, rect)
            return
        n0, n1 = int(gh * NAME_BAND[0]), int(gh * NAME_BAND[1])
        t0 = time.time()
        words, img, iw, ih = self.read_band(cap, ocr, rect, NAME_BAND)  # 이름 줄만 (빠르고 설명 글자와 덜 섞인다)
        self._time("이름 줄", t0)
        offers = find_offers(words, gw, gh, matcher)
        self.offered.update(n for n, _, _ in offers)
        partial = False
        segs = [t for t, cx, y in segments(words, gap=gw * 0.04)
                if NAME_BAND[0] * gh <= y <= NAME_BAND[1] * gh and cx >= gw * OWN_BOX_RIGHT]
        if not offers:
            # 이름이 하나라도 (일부라도) 맞으면 선택 화면이 아직 떠 있는 것 → 읽은 글자를 기록에 남긴다
            partial = any(matcher.match(t)[0] or matcher.partial(t) for t in segs)
            if partial:
                self.near = " | ".join(segs)[:160]
                self._rec("near", {"read": segs}, dedupe=True)
        before = dict(self.tracker.state)
        self.tracker.feed(slots(offers, gw) if offers else None, card_pos(offers, gw, gh) if offers else None, partial)
        after = self.tracker.state
        if (after.get("seq"), after.get("pickSeq"), bool(after.get("offers"))) != (before.get("seq"), before.get("pickSeq"), bool(before.get("offers"))):
            self._changed()
        if after.get("seq") != before.get("seq"):  # 새 선택지 (새로고침 포함): 읽은 글자 원문과 카드 이름 줄 그림
            self._rec("offers", {"read": segs, "offers": after.get("offers"), "pos": after.get("pos"),
                                 "image": self.rec.image("card", img, iw, ih) if self.rec else None})
        if after.get("pickSeq") != before.get("pickSeq"):
            self._rec("picked", {"picked": after.get("picked"), "from": after.get("pickedFrom"), "by": "click"})
            self._hud_at = time.time() + 2  # 고른 직후엔 증강 칸을 바로 확인
            self._hud_soon = True  # 지금 읽는 중이면 끝나고 바로 한 번 더
            if self.icon_dir and after.get("pickedFrom"):
                # 클릭으로 잡았으면 몇 번만 맞는지 보고, 못 잡았으면 VERIFY_FOR 초까지 (확인이 CPU 를 쓴다: 25초 내내면 평균 5→9%)
                self._verify = {"from": [n for n in after["pickedFrom"] if n], "click": after.get("picked"),
                                "until": time.time() + VERIFY_FOR, "tries": VERIFY_TRIES if after.get("picked") else 99,
                                # 이름을 다 못 읽었거나 클릭을 못 잡았으면, 못 찾을 때 증강 전체로 넓혀 찾는다
                                "unsure": not after.get("picked") or None in after["pickedFrom"]}
            if after.get("picked"):
                self._picked_names.add(after["picked"])
        self.status = ("증강 선택 화면 읽는 중" if offers else "증강 선택 화면 아님") + f" ({ocr.lang} · {ocr._make})"
        if offers or partial:
            return
        self._hud_tick(rect)
        self.scan_shop_only(cap, ocr, rect)

    def _hud_tick(self, rect):
        """증강 칸 아이콘 (고른 증강): 고른 직후와 HUD_EVERY 초마다만 (무거워서).
        따로 낮은 우선순위로 돈다: 같이 돌면 그동안 선택 화면 읽기·클릭 기록이 멈췄다 (2026-10-15 가상 PC 10초)"""
        now = time.time()
        if self._verify and (now > self._verify["until"] or self._verify.get("tries", 1) <= 0):
            self._rec("verify", {"from": self._verify["from"], "click": self._verify["click"], "result": "증강 칸에서 못 찾음"})
            if self._verify["click"]:
                self._unverified.append({"from": self._verify["from"], "click": self._verify["click"]})
            if self._verify.get("unsure"):
                # 선택지를 잘못(덜) 읽어 고른 증강이 비교 대상에 없었을 수 있다 → 증강 전체와 몇 번 (아주 확실한 그림만)
                self._recover = {"from": self._verify["from"], "click": self._verify["click"], "tries": RECOVER_TRIES,
                                 "known": set(self.hud_ids)}
                self._hud_at = 0
            self._verify = None
        if self._recover and self._recover["tries"] <= 0:
            self._rec("recover", {"from": self._recover["from"], "result": "못 찾음", "best": self._recover.get("best"),
                                  "groups": self._recover.get("groups"), "need": SURE_SCORE})
            self._recover = None
        if self.icon_dir and not getattr(self, "hud_error", None) and now >= getattr(self, "_hud_at", 0) \
                and not getattr(self, "_hud_busy", False):
            self._hud_busy = True
            threading.Thread(target=self._hud_job, args=(rect, getattr(self, "_game_n", 0)), daemon=True, name="증강 칸 읽기").start()

    def _hud_job(self, rect, game_n):
        try:
            lower_thread()
            if getattr(self, "_hud_cap", None) is None:
                self._hud_cap = _Capture()  # 화면 캡처 도구는 스레드마다 따로
            import hudicons
            offered = set(self.offered)
            # 넓혀 찾기 중이면 증강 전체와, 아니면 그 판 선택 화면에 뜬 증강하고만
            names = {i: n for i, n in self.id_names().items() if not offered or n in offered or self._recover}
            key = tuple(sorted(names))
            if getattr(self, "_tpl_key", None) != key:  # 새 선택지를 봤으면 비교 대상을 다시 만든다 (몇 개 안 돼 금방)
                tpl = hudicons.Templates(self.icon_dir, names)
                if self.hud is None:
                    self.hud = hudicons.HudReader(tpl)
                else:
                    self.hud.tpl, self.hud._seen = tpl, {}
                self._tpl_key = key
                if self.on_heavy:
                    self.on_heavy()  # 아이콘을 읽느라 한 번 크게 쓴 메모리를 돌려준다 (게임 중 주기적 반납은 안 한다)
            v = self._verify
            kind = "넓혀 찾기" if self._recover else "증강 칸"
            t0 = time.time()
            gh = rect[3]
            if self.hud.size is None and self.size_hint:
                self.hud.hint = self.size_hint(gh)
            self.scan_hud(self._hud_cap, rect, game_n)
            if self.hud.size and self.size_save and self.hud.size != self.hud.hint:
                self.size_save(gh, self.hud.size)
                self.hud.hint = self.hud.size
            if v is not None and "tries" in v:
                v["tries"] -= 1
            self._time(kind, t0)
        except ImportError as e:  # numpy·Pillow 가 없으면 이 기능만 끈다
            self.hud_error = str(e)[:120]
        except Exception as e:
            self._rec("error", {"error": "증강 칸: " + str(e)[:200]}, dedupe=True)
        finally:
            with self._lock:
                if game_n != getattr(self, "_game_n", 0):
                    # 읽는 동안 새 게임이 시작됐다: 지난 게임에서 본 아이콘을 지우고, 새 게임의 다음 읽기 시각은 건드리지 않는다
                    if self.hud:
                        self.hud.reset()
                else:
                    soon, self._hud_soon = getattr(self, "_hud_soon", False), False
                    # 확인 중이면 2초 뒤 한 번 더 (연속 2번 보여야 인정)
                    self._hud_at = time.time() + self._hud_wait(soon)
                self._hud_busy = False

    def _watch_load(self, ms):
        """자동 보호: 최근 SLOW_N 바퀴 평균이 SLOW_MS 를 넘으면 protect 를 켠다 (진단용 그림·상점 위쪽 띠를 건너뛴다).
        증강 선택 화면 읽기·고른 증강 확인·게임 정보는 그대로 둔다 (정확도는 줄이지 않는다). 평균이 절반 아래로 내려가면 끈다"""
        w = self._load
        w.append(ms)
        if len(w) > SLOW_N:
            del w[0]
        if len(w) < SLOW_N:
            return
        avg = sum(w) / len(w)
        on = avg > SLOW_MS if not self.protect else avg > SLOW_MS / 2
        if on != self.protect:
            self.protect = on
            self._count("보호 켬" if on else "보호 끔")
            self._rec("protect", {"on": on, "avgMs": round(avg)})

    def _count(self, kind, n=1):
        self._counts[kind] = self._counts.get(kind, 0) + n

    def _hud_wait(self, soon=False):
        """다음 증강 칸 읽기까지 몇 초. 증강 칸 읽기가 이 프로그램에서 가장 무거운 일이라(평균 0.6초, 길면 4초)
        이미 찾을 걸 다 찾았으면 드물게만 (2026-10-16 베인 판: 한 판 67번 중 대부분이 이미 아는 증강 다시 확인)"""
        if soon or self._verify or self._recover:
            return 2  # 확인 중: 연속 2번 보여야 인정
        if len(self.hud_ids) >= MAX_AUGMENTS:
            return float("inf")  # 다 골랐다. 새로 고르면(클릭) 그때 다시 읽는다
        if self.hud_ids and len(self.hud_ids) >= len(self._picked_names):
            return HUD_SETTLED
        return HUD_EVERY if self.offered else HUD_EVERY_MISSING

    def urgent(self, now=None):
        """증강 선택 화면이 곧 뜨거나 떠 있을 때: 선택 화면이 보임·고른 뒤 확인 중·레벨이 막 오름·죽어 있음"""
        now = now or time.time()
        live = None
        if self.live_fn:
            try:
                live = self.live_fn() or {}
            except Exception:
                live = {}
        if live:
            lv = live.get("level")
            if lv is not None and lv != self._level:
                # 게임 시작(첫 증강)은 60초, 레벨이 오르면 FAST_AFTER_LEVEL 초
                self._fast_until = now + (FAST_AFTER_LEVEL if self._level is not None else 60)
                self._level = lv
        return bool(self.tracker.showing or self._verify or now < self._fast_until
                    or (live and live.get("dead")) or not self.live_fn or not live or not live.get("inGame"))

    def _watch_inventory(self):
        """내 아이템이 바뀌면(샀거나 팔았음 = 상점이 열려 있었을 것) 다음 상점 읽기에서 못 알아봤을 때 그림을 남기게 한다"""
        if self._shop_probes >= SHOP_PROBES or self.shop:
            return
        try:
            live = (self.live_fn() or {}) if self.live_fn else {}
        except Exception:
            live = {}
        inv, gold = live.get("items"), live.get("gold")
        if inv is None:
            return
        inv = tuple(inv)
        # 산 것만 (골드가 줄었음). 물약을 마셔 아이템이 사라진 건 상점과 무관 (2026-10-15 기록: 3번 중 1번이 물약)
        prev_inv, prev_gold = self._inv if self._inv else (None, None)
        if prev_inv is not None and inv != prev_inv and gold is not None and prev_gold is not None and gold < prev_gold:
            self._shop_probe = time.time()
        self._inv = (inv, gold)

    def _find_shop_items(self, img, iw, ih, gh, image_name):
        """시험: 찍은 상점 그림에서 추천·바꿀 아이템 아이콘 자리를 찾아 기록만 한다 (화면 표시는 실제 그림으로 맞춘 뒤).
        가장 낮은 우선순위로 따로 돈다. 찾을 아이템이 몇 개뿐이라 가볍다 (합성 시험 9가지 크기 2.5초 → 크기를 알면 1가지)"""
        ids = [str(i) for i in (self.shop_targets or [])][:SHOP_FIND_MAX]
        if not ids or not self.icon_dir or getattr(self, "_shop_find_busy", False):
            return
        import os
        item_dir = os.path.join(os.path.dirname(self.icon_dir), "item")
        paths = {i: os.path.join(item_dir, f"{i}.webp") for i in ids if os.path.exists(os.path.join(item_dir, f"{i}.webp"))}
        if not paths:
            return
        k = iw / max(1, int(self.rect_w_for_area(iw)))  # 그림이 게임 창보다 줄어든 비율
        self._shop_find_busy = True
        game_n = getattr(self, "_game_n", 0)

        def job():
            try:
                lower_thread()
                import numpy as np
                import hudicons
                rgb = np.frombuffer(img, dtype=np.uint8).reshape(ih, iw, 4)[..., [2, 1, 0]]
                sizes = [self._shop_icon] if self._shop_icon else list(hudicons.SHOP_SIZES)
                t0 = time.time()
                found = hudicons.locate_items(paths, rgb, [s * gh * k for s in sizes])
                self._time("상점 아이템 찾기", t0)
                if game_n != getattr(self, "_game_n", 0):
                    return  # 찾는 동안 새 게임이 시작됐다: 지난 게임 그림의 결과를 새 게임 기록에 남기지 않는다
                sure = {i: f for i, f in found.items() if f["score"] >= hudicons.SHOP_SURE}
                if sure and not self._shop_icon:
                    # 찾은 크기를 기억한다 (다음엔 그 크기로만: 9배 가볍다)
                    self._shop_icon = round(sorted(f["size"] for f in sure.values())[len(sure) // 2] / (gh * k), 4)
                self._count("상점 아이템 찾기")
                self._count("상점 아이템 찾음", len(sure))
                self._rec("shopfind", {"image": image_name, "want": list(paths), "found": found, "sure": list(sure),
                                       "iconRatio": self._shop_icon, "scale": round(k, 4)})
            except ImportError:
                pass
            except Exception as e:
                self._rec("error", {"error": "상점 아이템 찾기: " + str(e)[:200]}, dedupe=True)
            finally:
                self._shop_find_busy = False
        threading.Thread(target=job, daemon=True, name="상점 아이템 찾기").start()

    def rect_w_for_area(self, iw):
        """상점 그림 영역의 원래 너비 (게임 창 기준 픽셀)"""
        rect = self._last_rect
        return rect[2] * (SHOP_PROBE_AREA[2] - SHOP_PROBE_AREA[0]) if rect else iw

    def _grab_area(self, cap, rect, area, max_w):
        """게임 창의 (x0, y0, x1, y1) 비율 영역을 너비 max_w 이하로 줄여 찍는다 → (이미지, 너비, 높이)"""
        gx, gy, gw, gh = rect
        ax0, ay0, ax1, ay1 = area
        aw, ah = int(gw * (ax1 - ax0)), int(gh * (ay1 - ay0))
        k = min(1.0, max_w / max(1, aw))
        tw, th = max(1, int(aw * k)), max(1, int(ah * k))
        return cap.grab(gx + int(gw * ax0), gy + int(gh * ay0), aw, ah, tw, th), tw, th

    def scan_shop_only(self, cap, ocr, rect):
        """상점 창: 열려 있을 때만 게임 화면 상자를 띄운다 (평소엔 게임을 가리지 않게. 2026-10-14 지적). SHOP_EVERY 번에 한 번만"""
        gx, gy, gw, gh = rect
        self._last_rect = rect
        self._watch_inventory()
        self._shop_n = getattr(self, "_shop_n", 0) + 1
        now_due, self._shop_now = getattr(self, "_shop_now", False), False
        if self._shop_n % SHOP_EVERY and not self._shop_probe and not now_due:
            return
        t0 = time.time()
        # 아래 버튼 줄(작은 띠) 먼저, 못 찾으면 위쪽 탭 줄은 두 번에 한 번 (또는 막 샀을 때)
        (bx0, bx1), bb = SHOP_BOTTOM
        sw, _, _, _ = self.read_band(cap, ocr, rect, bb, (bx0, bx1))
        shop = find_shop(sw, gw, gh)
        self._top_n = getattr(self, "_top_n", 0) + 1
        if shop and shop_bottom_key(gw, gh) not in self._bottom_ok:
            self._bottom_ok.add(shop_bottom_key(gw, gh))
        # 이 창 크기에서 아래 버튼 줄로 알아본 적이 있으면 위쪽 탭 줄(큰 띠)은 막 샀을 때만 읽는다 (화면 잡기·글자 읽기를 줄인다)
        top_due = self._top_n % 2 == 0 and shop_bottom_key(gw, gh) not in self._bottom_ok and not self.protect
        if not shop and (self._shop_probe or top_due or (now_due and not self.protect)):
            tw_, _, _, _ = self.read_band(cap, ocr, rect, SHOP_BAND)
            sw = sw + tw_
            shop = find_shop(tw_, gw, gh)
        self._time("상점", t0)
        self._count("상점 읽기")
        if shop:
            self._count("상점 알아봄")
        if self._shop_probe:
            self._count("산 직후 읽기")
            if shop:
                self._count("산 직후 알아봄")  # 상점 인식 성공률 = 산 직후 알아봄 / 산 직후 읽기
        if self._shop_probe and not shop and self.rec and not self.protect:
            # 아이템을 막 샀는데 상점 창을 못 알아봤다 → 읽은 글자와 가운데 넓은 그림을 남긴다 (상점이 어디 어떻게 보이는지)
            self._shop_probes += 1
            wide, ww, wh = self._grab_area(cap, rect, SHOP_PROBE_AREA, 1280)
            self._rec("shopmiss", {"read": [t for t, _, _ in segments(sw, gap=gw * 0.02)][:40],
                                   "image": self.rec.image("shopmiss", wide, ww, wh)})
        self._shop_probe = 0.0
        if bool(shop) != bool(self.shop):
            data = {"open": bool(shop), "pos": shop}
            now = time.time()
            if shop and self.rec and self._shop_imgs < SHOP_IMGS and now >= self._shop_img_at and not self.protect:
                # 상점 창 안 아이템 아이콘에 표시하려면 상점이 실제로 어떻게 생겼는지 봐야 한다 → 가운데 넓게 (채팅 제외)
                # 처음 열었을 때 + SHOP_IMG_GAP 초 뒤 한 번 더 (아이템이 바뀐 모습)
                img, iw, ih = self._grab_area(cap, rect, SHOP_PROBE_AREA, 1600)
                data["image"] = self.rec.image("shop", img, iw, ih)
                data["area"], data["window"] = list(SHOP_PROBE_AREA), [gw, gh]
                self._shop_imgs += 1
                self._shop_img_at = now + SHOP_IMG_GAP
                if SHOP_FIND:
                    self._find_shop_items(img, iw, ih, gh, data["image"])
            self._rec("shop", data)
            self.shop = shop
            self._changed()
        self.shop = shop
