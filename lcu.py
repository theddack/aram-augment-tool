"""롤 클라이언트 로컬 API(LCU): 챔피언 선택 감지 + 아이템 세트 등록 (상점 추천 칸에 우리 빌드 표시).

- LCU 는 라이엇이 '서드파티 공식 지원 아님'으로 둔 클라이언트 내부 API다. 블리츠·OP.GG 등도 아이템 세트에 쓴다.
- 접속 정보는 클라이언트 설치 폴더의 lockfile (이름:pid:포트:비밀번호:프로토콜) 에 있다. 클라이언트가 켜져 있을 때만 있다.
- 우리가 만든 세트만 uid 접두어로 구분해서 교체한다. 사용자가 직접 만든 세트는 건드리지 않는다.
"""
import base64
import json
import os
import re
import ssl
import time
import urllib.request

UID_PREFIX = "aram-augment-tool-"
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
        with urllib.request.urlopen(r, timeout=5, context=_insecure) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def phase(self):
        return self.req("GET", "/lol-gameflow/v1/gameflow-phase")

    def current_champion(self):
        """챔피언 선택 중 내 챔피언 key (숫자). 아직 없으면 0"""
        try:
            return self.req("GET", "/lol-champ-select/v1/current-champion") or 0
        except Exception:
            return 0

    def summoner_id(self):
        s = self.req("GET", "/lol-summoner/v1/current-summoner")
        return s.get("summonerId"), s.get("accountId")

    def write_item_set(self, champion_key, title, blocks, map_id=12):
        """우리 세트를 교체 등록한다. blocks = [{"type": 블록 이름, "items": [아이템 ID, …]}]"""
        sid, account = self.summoner_id()
        path = f"/lol-item-sets/v1/item-sets/{sid}/sets"
        cur = self.req("GET", path) or {}
        sets = [s for s in cur.get("itemSets", []) if not str(s.get("uid", "")).startswith(UID_PREFIX)]
        sets.insert(0, {
            "uid": f"{UID_PREFIX}{champion_key}",
            "title": title,
            "type": "custom", "map": "any", "mode": "any", "sortrank": 0, "startedFrom": "blank",
            "associatedChampions": [int(champion_key)], "associatedMaps": [map_id],
            "preferredItemSlots": [],
            "blocks": [{"type": b["type"], "items": [{"id": str(i), "count": 1} for i in b["items"]]} for b in blocks],
        })
        self.req("PUT", path, {"accountId": cur.get("accountId", account), "itemSets": sets,
                               "timestamp": int(time.time() * 1000)})
        return len(blocks)
