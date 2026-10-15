"""한 판 기록: 게임 하나 동안 무엇을 읽고 무엇을 보여 줬는지를 폴더 하나에 모은다 (끝나면 zip 도 만든다).

data/logs/games/날짜_시간_챔피언/
  events.json  순서별 기록 (선택 화면 글자 원문·맞춘 이름·클릭·증강 칸 판정·상점·산 아이템·적 구성·보여 준 추천)
  card_*.png   증강 선택 화면의 카드 이름 줄 (선택 화면마다)
  hud_*.png    증강 칸 부분 (새 증강이 생길 때마다)
  shop_*.png   상점 창 위쪽 탭 줄 (처음 한 번)

글자 결과만 남기면 빗나갔을 때 왜 빗나갔는지 알 수 없었다 (증강 칸 읽기는 스크린샷 한 장으로 맞췄다).
게임 화면에서 그 부분만 잘라서 채팅·소환사 이름은 들어가지 않는다. 그림은 한 판에 MAX_IMAGES 장, 최근 KEEP 판만.
"""
import json
import os
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

KEEP = 15  # 최근 몇 판 기록을 남길지 (실게임 확인 기간: 지인들과 여러 판 한 뒤 한꺼번에 보내도 남게)
MAX_IMAGES = 20


class GameRecorder:
    def __init__(self, base_dir, keep=KEEP, max_images=MAX_IMAGES):
        self.base = Path(base_dir)
        self.keep = keep
        self.max_images = max_images
        self.dir = None
        self.events = []
        self.images = 0
        self._t0 = 0.0
        self._lock = threading.Lock()
        self._last = {}  # 같은 내용이 되풀이되면 남기지 않으려고 (종류별 마지막 값)

    @property
    def active(self):
        return self.dir is not None

    def start(self, champion, info=None):
        """새 게임 시작: 폴더를 만들고 앞 게임은 마무리"""
        with self._lock:
            self._finish_locked()
            safe = re.sub(r"[^A-Za-z0-9_-]", "", str(champion or "unknown"))[:30] or "unknown"
            self.dir = self.base / (time.strftime("%Y%m%d_%H%M%S") + f"_{safe}")
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.dir = None
                return
            self.events, self.images, self._last = [], 0, {}
            self._t0 = time.time()
            self._add("start", {"champion": champion, **(info or {})})
            self._prune()

    def event(self, kind, data=None, dedupe=False):
        """kind: 기록 종류, data: 내용(dict). dedupe=True 면 같은 종류의 직전 내용과 같을 때 남기지 않는다"""
        with self._lock:
            if not self.dir:
                return
            if dedupe:
                key = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
                if self._last.get(kind) == key:
                    return
                self._last[kind] = key
            self._add(kind, data or {})

    def image(self, name, bgra, w, h):
        """화면 조각 저장 (BGRA 바이트). Pillow 가 없거나 한도를 넘으면 건너뛴다. 저장한 파일 이름을 돌려준다"""
        with self._lock:
            if not self.dir or self.images >= self.max_images or w <= 0 or h <= 0:
                return None
            try:
                from PIL import Image
                im = Image.frombuffer("RGBA", (w, h), bgra, "raw", "BGRA", 0, 1).convert("RGB")
                fname = f"{name}_{self.images + 1:02d}.png"
                im.save(self.dir / fname, optimize=True)
            except Exception:
                return None
            self.images += 1
            return fname

    def finish(self):
        with self._lock:
            self._finish_locked()

    # ---- 안쪽
    def _add(self, kind, data):
        self.events.append({"t": round(time.time() - self._t0, 1), "kind": kind, **data})
        self._flush()

    def _flush(self):
        try:
            tmp = self.dir / "events.json.tmp"
            tmp.write_text(json.dumps(self.events, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, self.dir / "events.json")
        except OSError:
            pass

    def _finish_locked(self):
        """끝난 게임은 zip 하나로도 묶는다 (보내기 쉽게)"""
        if not self.dir:
            return
        self._add("end", {})
        try:
            with zipfile.ZipFile(str(self.dir) + ".zip", "w", zipfile.ZIP_DEFLATED) as z:
                for p in sorted(self.dir.iterdir()):
                    z.write(p, f"{self.dir.name}/{p.name}")
        except OSError:
            pass
        self.dir = None

    def _prune(self):
        """최근 KEEP 판만 (폴더와 zip)"""
        try:
            games = sorted((p for p in self.base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
            for old in games[self.keep:]:
                shutil.rmtree(old, ignore_errors=True)
                zp = Path(str(old) + ".zip")
                if zp.exists():
                    zp.unlink()
        except OSError:
            pass
