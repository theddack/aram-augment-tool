"""게임 화면 아래 증강 칸(초상화 왼쪽 2x2)의 아이콘으로 '고른 증강'을 알아낸다.

선택 화면에서 클릭한 카드로 짐작하는 방식은 실제 게임에서 틀렸다 (2026-10-14 나피리 판: 고른 증강을 잘못 잡고,
새로고침 뒤 고른 것은 못 잡음). 증강 칸에는 실제로 고른 증강 아이콘이 계속 떠 있으니 그걸 정답으로 쓴다.

방법: 화면 아래쪽 가운데를 잘라 작게(12x12) 줄인 창들을 증강 아이콘 215개와 비교한다 (아이콘의 투명한 바깥은 빼고,
밝기 차이는 무시하는 정규화 상관). HUD 크기는 설정마다 달라서 처음엔 여러 크기로 찾고, 찾으면 그 크기로 고정한다.
numpy·Pillow 가 필요하다 (없으면 이 기능만 꺼진다).
"""
import os

try:
    import numpy as _np
    LUM = _np.array([0.299, 0.587, 0.114], dtype=_np.float32)
except ImportError:  # numpy 가 없으면 이 기능만 꺼진다 (Templates 만들 때 오류)
    LUM = None

GRID = 12  # 위치 찾기 크기 (흑백 12x12: 빠르게 후보 자리만)
FINE = 24  # 어떤 증강인지 가리기 크기 (흑백 24x24 가운데: 12x12 는 비슷한 아이콘끼리 헷갈렸다)
INNER = 0.12  # 가리기는 가장자리를 이만큼씩 빼고 가운데 그림만 (공통 어두운 테두리 때문에 '미친 과학자'가 모든 아이콘과 닮아 보였다)
CHUNK = 4096  # 한 번에 비교하는 창 수 (메모리 상한: 4096 x 아이콘 174 x 4바이트 ≈ 3MB)
FIND_SCORE = 0.55  # 위치 후보로 볼 닮은 정도 (느슨하게)
REGION = (0.20, 0.62, 0.80, 1.0)  # 찾는 범위 (게임 창 비율: 왼쪽, 위, 오른쪽, 아래)
# 아이콘 크기 후보 (게임 창 높이 비율, 8%씩). 간격이 넓으면 크기가 엇나가 다른 증강으로 읽었다 (맥 실험)
SIZES = tuple(round(0.024 * 1.08 ** i, 4) for i in range(13))
MIN_SCORE = 0.90  # 이만큼 닮아야 같은 아이콘 (흑백 24x24 가운데. 실제 HUD: 정답 0.966~0.972)
MARGIN = 0.01  # 1등이 2등보다 이만큼은 나아야 (점수 문턱을 높인 대신 차이는 작게 허용)


def _prep(np, vecs, wts):
    """가중 정규화 상관 준비: 아이콘에서 가중 평균을 빼 두면 창의 평균은 곱셈에서 상쇄된다"""
    W = np.array(wts, dtype=np.float32)
    T = np.array(vecs, dtype=np.float32)
    sw = W.sum(1, keepdims=True)
    mt = (W * T).sum(1, keepdims=True) / sw
    A = W * (T - mt)
    return A.T, W.T, sw.ravel(), np.sqrt((W * (T - mt) ** 2).sum(1))


MIN_STD = 0.04  # 창의 밝기 표준편차가 이보다 작으면(거의 단색: 바닥·빈 칸) 비교하지 않는다


def _score(np, X, prep):
    """가중 정규화 상관 (-1~1). 거의 단색인 창은 0.
    v0.1.15 실제 게임: 단색 영역에서 0 에 가까운 수로 나눠 점수가 7.4 까지 터졌고, 바닥을 '존야 업그레이드'로 읽었다"""
    A, W, sw, tnorm = prep
    num = X @ A
    sx = X @ W
    sxx = (X * X) @ W
    var = np.maximum(sxx - sx * sx / sw, 0.0)  # 가중 분산 × 가중치 합
    std = np.sqrt(var / sw)
    out = num / (np.sqrt(var) * tnorm + 1e-6)
    out[std < MIN_STD] = 0.0
    return np.clip(out, -1.0, 1.0)


class Templates:
    """증강 아이콘들을 비교용으로 준비. 게임 증강 칸 아이콘은 등급 색(분홍·보라)으로 칠한 단색 그림이라 색 말고 모양(밝기)으로 비교한다
    (2026-10-14 실제 HUD 캡처: 컬러 비교는 틀리고 밝기 비교는 골드 강탈 0.966 · 앙파상 0.972 로 정답).
    같은 그림을 쓰는 증강이 많아서(돌진=앙파상, 강타자=불을 밝혀 …) 그림이 같은 것끼리 한 묶음(group)으로 비교한다.
    위치 찾기: 흑백 12x12 전체, 가리기: 흑백 24x24 가운데(테두리 INNER 만큼 빼고)"""

    def __init__(self, icon_dir, names):
        """icon_dir: icons/augment 폴더, names: {증강 id: 이름}"""
        import numpy as np
        from PIL import Image
        self.np = np
        groups, g_v, f_v, keys = [], [], [], {}
        for aid, name in names.items():
            path = next((os.path.join(icon_dir, f"{aid}{ext}") for ext in (".webp", ".png")
                         if os.path.exists(os.path.join(icon_dir, f"{aid}{ext}"))), None)
            if not path:
                continue
            im = Image.open(path).convert("RGBA")
            flat = Image.new("RGBA", im.size, (0, 0, 0, 255))  # 투명한 바깥은 검정 (게임 칸 바탕도 검정)
            flat.alpha_composite(im)
            a = np.asarray(flat.convert("RGB").resize((GRID, GRID), Image.BILINEAR), dtype=np.float32) @ LUM / 255.0
            m = int(flat.size[0] * INNER)
            b = np.asarray(flat.convert("RGB").crop((m, m, flat.size[0] - m, flat.size[1] - m)).resize((FINE, FINE), Image.BILINEAR),
                           dtype=np.float32) @ LUM / 255.0
            if a.std() < 0.03:  # 거의 비어 있는 아이콘은 비교 못 함
                continue
            key = np.round(b, 2).tobytes()
            if key in keys:  # 같은 그림 → 같은 묶음
                groups[keys[key]].append(aid)
                continue
            keys[key] = len(groups)
            groups.append([aid])
            g_v.append(a.ravel())
            f_v.append(b.ravel())
        self.groups = [tuple(g) for g in groups]
        self.ids = self.groups  # find_icons 결과의 '증강' 자리에 묶음이 들어간다
        self.names = names
        self.coarse = _prep(np, g_v, [np.ones(GRID * GRID, np.float32)] * len(g_v))
        self.fine = _prep(np, f_v, [np.ones(FINE * FINE, np.float32)] * len(f_v))

    def scores(self, X):
        """X: (창 수, 144) 흑백 → (창 수, 묶음 수) 닮은 정도 (-1~1)"""
        return _score(self.np, X, self.coarse)

    def identify(self, X):
        """X: (창 수, 24*24) 흑백 가운데 → (창 수, 묶음 수)"""
        return _score(self.np, X, self.fine)


def _windows(np, lum):
    """밝기 이미지 → 모든 12x12 창 (창 수, 144)과 각 창의 왼쪽 위 좌표"""
    h, w = lum.shape
    if h < GRID or w < GRID:
        return np.zeros((0, GRID * GRID), np.float32), []
    v = np.lib.stride_tricks.sliding_window_view(lum, (GRID, GRID))  # (h-11, w-11, 12, 12)
    ys, xs = v.shape[:2]
    return v.reshape(ys * xs, GRID * GRID), (ys, xs)


def find_icons(tpl, rgb, size_px):
    """rgb: (H, W, 3) 0~255 화면 조각, size_px: 아이콘 한 변 픽셀 → [(증강 id, 점수, x, y)] (조각 기준 좌표)
    1) 흑백 12x12 로 아이콘이 있을 만한 자리 후보 → 2) 그 자리를 컬러 24x24 로 몇 픽셀·크기 흔들어 가며 어떤 증강인지"""
    np = tpl.np
    from PIL import Image
    f = GRID / size_px
    H, W = rgb.shape[:2]
    img = Image.fromarray(rgb.astype(np.uint8))
    small = img.resize((max(GRID, int(W * f)), max(GRID, int(H * f))), Image.BILINEAR)
    lum = np.asarray(small, dtype=np.float32) @ LUM / 255.0
    # 창들을 한꺼번에 펼치면 큰 화면에서 수백 MB 를 잡았다 놓아 PC 가 순간 멈췄다 (측정: 순간 900MB).
    # 줄 단위로 나눠서 각 창의 '가장 닮은 점수'만 남긴다 (결과는 같다)
    h, w = lum.shape
    if h < GRID or w < GRID:
        return []
    v = np.lib.stride_tricks.sliding_window_view(lum, (GRID, GRID))
    ys, xs = v.shape[:2]
    top = np.empty(ys * xs, np.float32)
    rows_per = max(1, CHUNK // max(1, xs))
    for y0 in range(0, ys, rows_per):
        block = v[y0:y0 + rows_per].reshape(-1, GRID * GRID)
        top[y0 * xs: y0 * xs + len(block)] = tpl.scores(block).max(1)
    cands = []
    for i in np.argsort(-top)[:200]:
        if top[i] < FIND_SCORE:
            break
        y, x = divmod(int(i), xs)
        if any(abs(x - cx) < GRID * 0.7 and abs(y - cy) < GRID * 0.7 for cx, cy in cands):
            continue
        cands.append((x, y))
        if len(cands) >= 5:  # 증강 칸은 최대 4~6칸 (후보를 줄여 가볍게)
            break
    hits = []
    step = max(1, round(size_px / 24))  # 위치를 아이콘 크기의 1/24 씩 (1/12 씩이면 몇 픽셀 엇나가 점수가 확 떨어졌다)
    for cx, cy in cands:
        ox, oy = cx / f, cy / f  # 조각 기준 원래 픽셀
        crops, where = [], []
        for k in (0.93, 1.0, 1.07):
            sp = size_px * k
            for dy in range(-3 * step, 3 * step + 1, step):
                for dx in range(-3 * step, 3 * step + 1, step):
                    x0 = ox + dx - (sp - size_px) / 2
                    y0 = oy + dy - (sp - size_px) / 2
                    if x0 < 0 or y0 < 0 or x0 + sp > W or y0 + sp > H:
                        continue
                    m = sp * INNER
                    c = img.crop((int(x0 + m), int(y0 + m), int(x0 + sp - m), int(y0 + sp - m))).resize((FINE, FINE), Image.BILINEAR)
                    crops.append((np.asarray(c, dtype=np.float32) @ LUM).ravel() / 255.0)
                    where.append((x0, y0))
        if not crops:
            continue
        S = tpl.identify(np.array(crops))
        j = np.unravel_index(int(S.argmax()), S.shape)
        sc = float(S[j])
        row = S[j[0]].copy()
        row[j[1]] = -1
        if sc >= MIN_SCORE and sc - float(row.max()) >= MARGIN:
            x0, y0 = where[j[0]]
            aid = tpl.ids[j[1]]
            if not any(h[0] == aid for h in hits):
                hits.append((aid, sc, x0, y0))
    return hits


class HudReader:
    """증강 칸 읽기. scan(rgb_of_game_window) 을 몇 초마다 부르면 picked(고른 증강 id 목록, 칸 순서)를 갱신한다"""

    CONFIRM = 2  # 연속으로 이만큼 보여야 고른 것으로

    def __init__(self, tpl):
        self.tpl = tpl
        self.size = None  # 찾은 아이콘 크기 (게임 창 높이 비율). 찾기 전엔 여러 크기로
        self.picked = []
        self._seen = {}
        self.last = []

    def scan(self, crop, H, offset=(0, 0)):
        """crop: 게임 창의 REGION 부분 (h, w, 3), H: 게임 창 높이, offset: crop 의 게임 창 기준 왼쪽 위"""
        np = self.tpl.np
        sizes = [self.size] if self.size else SIZES
        best, best_size = [], None
        best_mean = 0.0
        for s in sizes:
            hits = find_icons(self.tpl, crop, max(GRID, s * H))
            m = float(np.mean([h[1] for h in hits])) if hits else 0.0
            # 크기가 맞으면 점수가 확 오른다 → 평균 점수가 가장 높은 크기 (개수로 고르면 엇나간 크기의 오답이 이겼다)
            if m > best_mean:
                best, best_size, best_mean = hits, s, m
        if best and not self.size:
            self.size = best_size
        if self.size:
            # 찾은 크기 앞뒤로도 한 번씩 (아이콘마다 몇 % 씩 크기가 달라 한 크기로는 하나를 놓쳤다) → 증강마다 가장 닮은 것
            merged = {h[0]: h for h in best}
            for k in (0.92, 1.08):
                for h in find_icons(self.tpl, crop, max(GRID, self.size * k * H)):
                    if h[0] not in merged or h[1] > merged[h[0]][1]:
                        merged[h[0]] = h
            best = list(merged.values())
        x0, y0 = offset
        self.last = [(aid, sc, x + x0, y + y0) for aid, sc, x, y in best]
        now = {aid for aid, *_ in self.last}
        for aid in list(self._seen):
            if aid not in now:
                del self._seen[aid]
        for aid in now:
            self._seen[aid] = self._seen.get(aid, 0) + 1
        confirmed = [h for h in self.last if self._seen.get(h[0], 0) >= self.CONFIRM]
        # 칸 순서: 위 줄 왼쪽부터 (2x2 칸)
        confirmed.sort(key=lambda h: (round(h[3] / max(1.0, (self.size or 0.04) * H)), h[2]))
        ids = [h[0] for h in confirmed]
        # 한 번 확인된 증강은 잠깐 가려져도(죽음·상점 창) 지우지 않는다. 새로 보이면 더한다
        for aid in ids:
            if aid not in self.picked:
                self.picked.append(aid)
        return self.picked

    def reset(self):
        self.size = None
        self.picked = []
        self._seen = {}
