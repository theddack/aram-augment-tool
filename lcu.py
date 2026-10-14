"""롤 클라이언트 로컬 API(LCU): 챔피언 선택 감지 + 아이템 세트 등록 (상점 추천 칸에 우리 빌드 표시).

- LCU 는 라이엇이 '서드파티 공식 지원 아님'으로 둔 클라이언트 내부 API다. 블리츠·OP.GG 등도 아이템 세트에 쓴다.
- 접속 정보는 클라이언트 설치 폴더의 lockfile (이름:pid:포트:비밀번호:프로토콜) 에 있다. 클라이언트가 켜져 있을 때만 있다.
- 우리가 만든 세트만 uid 접두어로 구분해서 교체한다. 사용자가 직접 만든 세트는 건드리지 않는다.
  목록을 못 읽으면 저장하지 않고, 저장 전 목록은 사용자 폴더 itemset_backup/ 에 남긴다.
"""
import base64
import json
import os
import re
import ssl
import time
import urllib.request

UID_PREFIX = "kalbaram-addon-"
OLD_UID_PREFIXES = ("aram-augment-tool-",)  # 예전 이름으로 등록한 세트도 우리 것 (교체해서 두 개씩 남지 않게)
OUR_PREFIXES = (UID_PREFIX,) + OLD_UID_PREFIXES
INSTALL_CANDIDATES = [
    r"C:\Riot Games\League of Legends",
    r"D:\Riot Games\League of Legends",
    r"C:\Program Files\Riot Games\League of Legends",
    r"C:\Program Files (x86)\Riot Games\League of Legends",
]
PRODUCT_SETTINGS = r"C:\ProgramData\Riot Games\Metadata\league_of_legends.live\league_of_legends.live.product_settings.yaml"

_insecure = ssl.create_default_context()
_insecure.check_hostname = False
_insecure.verify_mode = ssl.CERT_NONE
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_insecure))  # 프록시 안 거침


def install_dirs(extra=()):
    dirs = list(extra)
    try:  # 라이엇 클라이언트가 적어 둔 실제 설치 위치
        text = open(PRODUCT_SETTINGS, encoding="utf-8", errors="ignore").read()
        m = re.search(r'product_install_full_path:\s*"?([^"\n]+)"?', text)
        if m:
            dirs.append(m.group(1).strip().replace("/", "\\"))
    except OSError:
        pass
    return dirs + INSTALL_CANDIDATES


def find_lockfile(extra=()):
    for d in install_dirs(extra):
        p = os.path.join(d, "lockfile")
        if os.path.exists(p):
            return p
    return None


class Lcu:
    def __init__(self, lockfile_path):
        name, pid, port, password, proto = open(lockfile_path, encoding="utf-8").read().strip().split(":")
        self.base = f"{proto}://127.0.0.1:{port}"
        self.auth = "Basic " + base64.b64encode(f"riot:{password}".encode()).decode()

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Authorization": self.auth, "Content-Type": "application/json",
                                            "Accept": "application/json"})
        with _local.open(r, timeout=5) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def phase(self):
        return self.req("GET", "/lol-gameflow/v1/gameflow-phase")

    def current_champion(self):
        """챔피언 선택 중 내 챔피언 key (숫자). 아직 없으면 0

        세션의 내 칸(localPlayerCellId)을 먼저 본다: 칼바람은 대기석에서 바꾸는데, current-champion 은
        바꾼 뒤에도 앞 챔피언으로 남는 경우가 있었다 (2026-10-14 이렐리아 → 암베사, 상점 추천이 이렐리아로 남음)."""
        try:
            sess = self.req("GET", "/lol-champ-select/v1/session")
            cell = sess.get("localPlayerCellId")
            me = next((p for p in sess.get("myTeam") or [] if p.get("cellId") == cell), None)
            if me and me.get("championId"):
                return int(me["championId"])
        except Exception:
            pass
        try:
            return self.req("GET", "/lol-champ-select/v1/current-champion") or 0
        except Exception:
            return 0

    def summoner_id(self):
        s = self.req("GET", "/lol-summoner/v1/current-summoner")
        return s.get("summonerId"), s.get("accountId")

    def write_item_set(self, champion_key, title, blocks, map_id=12, backup_dir=None):
        """우리 세트를 교체 등록한다. blocks = [{"type": 블록 이름, "items": [아이템 ID, …]}]

        LCU 는 세트 목록을 통째로 저장(PUT)하는 방식이라, 지금 목록을 읽어 우리 세트만 바꿔 끼운다.
        읽기에 실패했거나 응답이 이상하면 저장하지 않는다 (빈 목록으로 저장하면 사용자 세트가 전부 지워진다).
        저장 전 목록은 backup_dir 에 남긴다 (최근 10개).
        """
        sid, account = self.summoner_id()
        if not sid:
            raise RuntimeError("소환사 정보를 못 읽어서 저장하지 않았어요")
        path = f"/lol-item-sets/v1/item-sets/{int(sid)}/sets"
        cur = self.req("GET", path)
        if not isinstance(cur, dict) or not isinstance(cur.get("itemSets"), list):
            raise RuntimeError("지금 아이템 세트 목록을 못 읽어서 저장하지 않았어요 (사용자 세트 보호)")
        if backup_dir:
            save_backup(backup_dir, cur)
        sets = [s for s in cur["itemSets"] if not str(s.get("uid", "")).startswith(OUR_PREFIXES)]
        sets.insert(0, {
            "uid": f"{UID_PREFIX}{champion_key}",
            "title": title,
            "type": "custom", "map": "any", "mode": "any", "sortrank": 0, "startedFrom": "blank",
            "associatedChampions": [int(champion_key)], "associatedMaps": [map_id],
            "preferredItemSlots": [],
            "blocks": [{"type": b["type"], "items": [{"id": str(i), "count": 1} for i in b["items"]]} for b in blocks],
        })
        users_before = [s.get("uid") for s in cur["itemSets"] if not str(s.get("uid", "")).startswith(OUR_PREFIXES)]
        users_after = [s.get("uid") for s in sets if not str(s.get("uid", "")).startswith(OUR_PREFIXES)]
        if users_after != users_before:  # 사용자 세트가 하나도 빠지거나 바뀌지 않았는지 (코드 실수 방지)
            raise RuntimeError("사용자 세트 수가 달라져서 저장하지 않았어요")
        self.req("PUT", path, {"accountId": cur.get("accountId", account), "itemSets": sets,
                               "timestamp": int(time.time() * 1000)})
        return len(blocks)


def save_backup(backup_dir, item_sets):
    """저장하기 전 아이템 세트 목록 (되돌릴 때 쓰기). 최근 10개만 남긴다."""
    os.makedirs(backup_dir, exist_ok=True)
    name = os.path.join(backup_dir, time.strftime("itemsets_%Y%m%d_%H%M%S.json"))
    with open(name, "w", encoding="utf-8") as f:
        json.dump(item_sets, f, ensure_ascii=False)
    files = sorted((os.path.join(backup_dir, x) for x in os.listdir(backup_dir) if x.startswith("itemsets_")),
                   key=os.path.getmtime, reverse=True)
    for old in files[10:]:
        try:
            os.remove(old)
        except OSError:
            pass


def clean_item_set(champion_key, title, blocks):
    """화면(JS)에서 받은 아이템 세트 값을 검사한다. 숫자 ID·짧은 글자만 통과. 이상하면 ValueError"""
    try:
        return _clean_item_set(champion_key, title, blocks)
    except (TypeError, AttributeError) as e:
        raise ValueError(f"형식: {e}") from None


# 롤 상점은 이모지를 못 그리고 '?' 로 보여 준다 (2026-10-14 실제 화면 확인) → 글자 판 밖(이모지)·변형 선택자는 뺀다
_NO_GLYPH = re.compile("[\U00010000-\U0010FFFF\u2600-\u27BF\uFE0E\uFE0F\u200D]")


def shop_text(s, limit=60):
    return re.sub(r"\s+", " ", _NO_GLYPH.sub("", str(s))).strip()[:limit]


def _clean_item_set(champion_key, title, blocks):
    key = int(champion_key)
    if not 0 < key < 100000:
        raise ValueError("챔피언 번호")
    title = shop_text(title)
    if not isinstance(blocks, list) or not 0 < len(blocks) <= 10:
        raise ValueError("블록 수")
    out = []
    for b in blocks:
        raw = b.get("items") or []
        if not isinstance(raw, list):
            raise ValueError("아이템 목록")
        items = [int(i) for i in raw][:12]
        if not all(0 < i < 10_000_000 for i in items):
            raise ValueError("아이템 ID")
        out.append({"type": shop_text(b.get("type", "")), "items": items})
    return key, title, out
