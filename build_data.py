#!/usr/bin/env python3
"""칼바람 증강 모드(KIWI) 데이터 수집기.

Data Dragon(챔피언·아이템)과 CommunityDragon(증강·모드 아이템 목록·문자열)을 받아
web/data.json 과 web/icons/ 를 만든다. 원본 응답은 .cache/ 에 저장해 재실행 시 재사용한다.

  python3 build_data.py            # 캐시 있으면 재사용
  python3 build_data.py --refresh  # 패치 후 전부 다시 받기
"""
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from champion_data import (augment_effects, item_spell, parse_lua_table, passive_spell, process_champion,
                           text_effects, wiki_extras)
from lol_calc import Calc, expr_json, fmt_expr_ranks

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
WEB = ROOT / "web"
ICONS = WEB / "icons"

DDRAGON = "https://ddragon.leagueoflegends.com"
CDRAGON = "https://raw.communitydragon.org/latest"
WIKI = "https://wiki.leagueoflegends.com/en-us"
MODE = "KIWI"  # 칼바람 증강 모드 내부 이름
MODE_PATH = "Maps/Shipping/Map12/Modes/KIWI"

REFRESH = "--refresh" in sys.argv


def fetch(url, name):
    path = CACHE / name
    if path.exists() and not REFRESH:
        return path.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "aram-augment-tool"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    path.write_bytes(data)
    return data


def fetch_json(url, name):
    return json.loads(fetch(url, name))


# ---------------------------------------------------------------- 효과 태그
# 점수 계산에 쓰는 공통 태그. 챔피언 가중치·증강·아이템이 모두 이 이름을 쓴다.
TAGS = ["AD", "AP", "AS", "CRIT", "ONHIT", "LETHAL", "MPEN", "HP", "ARMOR", "MR",
        "HEALSHIELD", "VAMP", "HASTE", "ULT", "MANA", "MS", "CC", "TRUE"]

# 툴팁 마크업 태그 → 효과 태그
MARKUP_TAGS = {
    # 피해 유형(physicalDamage/magicDamage)은 계수가 아니라서 태그로 쓰지 않는다
    "scalead": "AD", "scaleap": "AP",
    "attackspeed": "AS", "onhit": "ONHIT", "scalehealth": "HP", "scalearmor": "ARMOR",
    "scalemr": "MR", "healing": "HEALSHIELD", "shield": "HEALSHIELD", "lifesteal": "VAMP",
    "omnivamp": "VAMP", "speed": "MS", "truedamage": "TRUE", "status": "CC",
    "crit": "CRIT", "scaleabilityhaste": "HASTE", "scalemana": "MANA", "health": "HP",
}

# 본문 키워드 → 효과 태그 (정규식)
KEYWORDS = {
    "AD": r"공격력", "AP": r"주문력", "AS": r"공격 속도", "CRIT": r"치명타",
    "ONHIT": r"적중 시|기본 공격", "LETHAL": r"물리 관통|방어구 관통|물리 관통력",
    "MPEN": r"마법 관통", "HP": r"최대 체력|추가 체력|체력이 (?!회복)",
    "ARMOR": r"방어력", "MR": r"마법 저항력", "HEALSHIELD": r"회복|보호막",
    "VAMP": r"생명력 흡수|모든 피해 흡혈", "HASTE": r"스킬 가속|재사용 대기시간",
    "ULT": r"궁극기", "MANA": r"마나", "MS": r"이동 속도",
    "CC": r"기절|둔화|속박|에어본|공중으로 띄|군중 제어|도발", "TRUE": r"고정 피해",
}

# 아이템 스탯 → (태그, 기준값). 기준값만큼 있으면 강도 1.0
ITEM_STATS = {
    "FlatPhysicalDamageMod": ("AD", 45), "FlatMagicDamageMod": ("AP", 80),
    "PercentAttackSpeedMod": ("AS", 0.35), "FlatCritChanceMod": ("CRIT", 0.25),
    "FlatHPPoolMod": ("HP", 400), "FlatArmorMod": ("ARMOR", 50),
    "FlatSpellBlockMod": ("MR", 50), "PercentLifeStealMod": ("VAMP", 0.1),
    "FlatMPPoolMod": ("MANA", 500), "PercentMovementSpeedMod": ("MS", 0.05),
    "FlatMovementSpeedMod": ("MS", 45),
}
# 아이템 설명 문구에서 뽑아도 되는 효과성 태그 (스탯 태그는 설명에 '체력' 등이 나와도 무시)
ITEM_DESC_TAGS = {"HEALSHIELD", "CC", "ONHIT", "TRUE", "ULT", "VAMP", "LETHAL", "MPEN", "HASTE"}
ITEM_DDRAGON_TAGS = {
    "CooldownReduction": "HASTE", "AbilityHaste": "HASTE", "OnHit": "ONHIT",
    "ArmorPenetration": "LETHAL", "MagicPenetration": "MPEN", "LifeSteal": "VAMP",
    "SpellVamp": "VAMP", "Slow": "CC", "ManaRegen": "MANA",
}


def tag_text(plain, markup_names):
    tags = {}
    for name in markup_names:
        t = MARKUP_TAGS.get(name.lower())
        if t:
            tags[t] = max(tags.get(t, 0), 0.8)
    for t, pat in KEYWORDS.items():
        if re.search(pat, plain):
            tags[t] = max(tags.get(t, 0), 0.6)
    return tags


# ---------------------------------------------------------------- 툴팁 처리
SAFE_SPAN = set(MARKUP_TAGS) | {"keywordmajor", "keyword", "rarityprismatic", "raritylegendary",
                                "raritygeneric", "scalelevel", "spellname", "passive", "active",
                                "attention", "gold", "magicdamage", "physicaldamage", "recast",
                                "spellpassive", "spellactive", "magicpen", "armorpen", "lifesteal"}
BLOCK_TAGS = {"rules", "li", "flavortext", "maintext", "stats"}  # 줄을 바꿔서 표시


def expand_templates(text, strings, depth=0):
    """{{ 키 }} 를 문자열 테이블 값으로 바꾼다. {{SpellName}} 같은 제목용 키는 지운다."""
    def sub(m):
        key = re.sub(r"@[^@]*@", "", m.group(1)).strip().lower()
        if key in ("spellname", "spellnameonly") or depth > 3:
            return ""
        return expand_templates(strings.get(key, ""), strings, depth + 1)
    return re.sub(r"{{\s*([^}]+?)\s*}}", sub, text)


def resolve_values(text, spell):
    """@Name@, @Name*100@ 자리표시자를 DataValues·계산식으로 채운다. 못 채우면 ?"""
    unresolved = []
    calc = Calc(spell)

    def sub(m):
        expr = m.group(1)
        if re.fullmatch(r"f\d+(\.\d+)?(\*[\d.]+)?", expr, re.I):
            return "(게임 중 집계)"  # 플레이 중 누적되는 값이라 정적 수치가 없음
        mm = re.fullmatch(r"([A-Za-z_][\w.]*?)(?:\.0)?(?:\*(-?[\d.]+))?", expr)
        if mm:
            name, k = mm.group(1), float(mm.group(2)) if mm.group(2) else 1
            e, pct = calc.resolve(name, 1)
            if e is not None:
                return fmt_expr_ranks([e.scale(k)], pct and k == 1)
        unresolved.append(expr)
        return "?"

    return re.sub(r"@([^@]+)@", sub, text), unresolved


def to_html(text):
    """게임 마크업을 안전한 HTML 로. 알려진 태그만 span 으로 남기고 나머지는 지운다."""
    out, names = [], []
    text = re.sub(r"%i:[^%]+%\??", "", text)  # 게임 내 인라인 아이콘 토큰
    for tok in re.split(r"(<[^>]*>)", text):
        m = re.fullmatch(r"<(/?)([A-Za-z0-9]+)[^>]*>", tok)
        if not m:
            out.append(tok.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue
        closing, name = m.group(1), m.group(2).lower()
        if name == "br" or (name in BLOCK_TAGS and not closing):
            out.append("<br>")
        elif name in SAFE_SPAN:
            names.append(name)
            out.append("</span>" if closing else f'<span class="t-{name}">')
    html = "".join(out)
    plain = re.sub(r"<[^>]+>", "", html)
    return html, plain, names


# ---------------------------------------------------------------- 아이템 능력치
# 아이템 설명 <stats> 줄의 이름 → 능력치 키. %가 붙은 값은 0~1 비율로 저장한다.
ITEM_STAT_NAMES = {
    "체력": "HP", "공격력": "AD", "주문력": "AP", "방어력": "ARMOR", "마법 저항력": "MR",
    "스킬 가속": "HASTE", "마나": "MANA", "물리 관통력": "LETHALITY", "마법 관통력": "MPEN",
    "이동 속도": "MS", "공격 속도%": "AS", "치명타 확률%": "CRIT", "치명타 피해량%": "CRITDMG",
    "이동 속도%": "MSP", "생명력 흡수%": "LS", "모든 피해 흡혈%": "OMNIVAMP",
    "방어구 관통력%": "ARPEN", "마법 관통력%": "MPENP", "체력 회복 및 보호막%": "HSP",
    "강인함%": "TENACITY", "기본 체력 재생%": "HP5P", "기본 마나 재생%": "MP5P",
}


def item_stats(description):
    stats, unknown = {}, []
    block = re.search(r"<stats>(.*?)</stats>", description)
    if not block:
        return stats, unknown
    for line in re.split(r"<br\s*/?>", block.group(1)):
        line = re.sub(r"<(?!/?attention)[^>]+>", "", line).strip()
        if not line:
            continue
        m = re.match(r"(.+?)\s*<attention>\s*([\d.]+)\s*(%?)\s*</attention>", line) \
            or re.match(r"<attention>\s*([\d.]+)(%?)</attention>\s*(.+)", line)
        if not m:
            m2 = re.match(r"(\D+?)\s*([\d.]+)\s*(%?)$", re.sub(r"</?attention>", "", line))
            if not m2:
                unknown.append(line)
                continue
            m = m2
        g = m.groups()
        if g[0].replace(".", "").isdigit():
            num, pct, name = g
        else:
            name, num, pct = g
        key = ITEM_STAT_NAMES.get(name.strip() + ("%" if pct else ""))
        if not key:
            unknown.append(line)
            continue
        val = float(num) / (100 if pct else 1)
        stats[key] = round(stats.get(key, 0) + val, 4)
    return stats, unknown


# ---------------------------------------------------------------- 챔피언 스킬 특성
# 증강 조건(rules.json 의 requires)과 맞춰 보는 스킬 특징
FEATURES = {
    "dash": r"돌진|도약|순간이동|뛰어올|질주해|구르|돌격",
    "knockback": r"밀어내|밀쳐|밀려|뒤로 밀|넉백|날려 ?보내",
    "terrain": r"지형을 생성|벽을 세|장벽을|지형을 만",
    "spin": r"회전|회오리|휘둘러",
    "summon": r"티버|포탑을 (설치|배치|세)|분신|환각|식물|데이지|소환물|구울|안개 수행자|깜짝 상자|소환한 ",
    "channel": r"정신 집중",
    "stealth": r"투명|은신|위장",
    "immobilize": r"기절|속박|에어본|공중으로|도발|매혹|공포|제압|변이|끌어당",
}
TRAIT_TAGS = {"HEALSHIELD", "CC", "ONHIT", "MS", "TRUE", "VAMP", "HASTE", "ULT", "LETHAL", "MPEN", "HP", "AS", "CRIT"}


def champion_traits(detail):
    """패시브 + 스킬 4개 설명에서 특성 태그와 피해 유형 비율을 뽑는다.

    특성 강도 = 그 태그가 나온 스킬 수 × 0.4 (최대 1). 아군 대상 회복·보호막은 1.0으로 본다.
    """
    abilities = [detail["passive"]["description"]] + [sp["tooltip"] for sp in detail["spells"]]
    counts, phys, magic, ally_support, features = {}, 0, 0, False, set()
    for raw in abilities:
        raw = re.sub(r"{{[^}]*}}", "", raw)
        html, plain, names = to_html(raw)
        lower = [n.lower() for n in re.findall(r"<([A-Za-z]+)", raw)]
        phys += lower.count("physicaldamage")
        magic += lower.count("magicdamage")
        for t in tag_text(plain, names):
            if t in TRAIT_TAGS:
                counts[t] = counts.get(t, 0) + 1
        if re.search(r"아군", plain) and re.search(r"회복|보호막", plain):
            ally_support = True
        for f, pat in FEATURES.items():
            if re.search(pat, plain):
                features.add(f)
    traits = {t: round(min(1.0, c * 0.4), 2) for t, c in counts.items()}
    if ally_support:
        traits["HEALSHIELD"] = 1.0
    elif "HEALSHIELD" in traits:
        traits["HEALSHIELD"] = min(traits["HEALSHIELD"], 0.4)  # 자기 회복만 있으면 약하게
    if ally_support:
        features.add("allysupport")
    dmg = phys + magic
    return traits, (round(magic / dmg, 2) if dmg else None), sorted(features)


# ---------------------------------------------------------------- 아이템 고유 효과
def item_passive(item_bin, strings):
    """아이템 고유 효과 설명을 수치로 채우고 피해·보호막·회복 효과를 뽑는다. (효과, 표시용 html, 적용한 모드)"""
    if not item_bin:
        return [], None, None
    spell, mode_used = item_spell(item_bin)
    keys = (item_bin.get("mItemDataClient") or {}).get("mTooltipData", {}).get("mLocKeys", {})
    raw = strings.get((keys.get("keyTooltip") or "").lower(), "")
    if not raw:
        return [], None, mode_used
    vals = {d["name"]: d["values"][0] for d in spell["DataValues"]}
    ranged_scale = None
    if "{{ Item_Melee_Ranged_Split_Dynamic }}" in raw.replace("{{Item", "{{ Item"):
        # 근접/원거리 값이 다른 효과: 근접 값으로 계산하고 원거리 비율을 따로 저장
        if vals.get("MeleeValue"):
            ranged_scale = vals.get("RangedValue", vals["MeleeValue"]) / vals["MeleeValue"]
        raw = re.sub(r"{{\s*Item_Melee_Ranged_Split_Dynamic\s*}}", "@MeleeValue*100@%", raw)
    raw = expand_templates(raw, strings)
    text, _ = resolve_values(raw, spell)
    html, plain, _ = to_html(text)
    effects = text_effects(raw, spell, plain)
    # 아이템 재사용 대기시간은 그 쿨다운 표시가 있는 고유 효과 구역(<passive>…) 안의 효과에만 건다
    # (몰락한 왕의 검: 쿨은 둔화 효과 것이고 적중 시 피해에는 없다)
    original = strings.get((keys.get("keyTooltip") or "").lower(), "")
    cd_names = set()
    if vals.get("Cooldown"):
        for sec in re.split(r"<passive>|<active>", original):
            if re.search(r"Item_Cooldown", sec):
                cd_names |= {n.split("*")[0].strip().lower() for n in re.findall(r"@([^@]+)@", sec)}
    for e in effects:
        if ranged_scale is not None:
            e["rangedScale"] = round(ranged_scale, 4)
        if e["name"].lower() in cd_names:
            e["cooldown"] = vals["Cooldown"]  # 발동 횟수 상한
    return effects, html, mode_used


# ---------------------------------------------------------------- 위키 보강 데이터 (CC BY-SA)
MAYHEM_MOD_KEYS = {  # 위키 '증강 모드 전용 보정' 문구 → 우리 키
    "damage dealt": "dmg_dealt", "damage received": "dmg_taken", "healing done": "healing",
    "shielding done": "shielding", "ability haste": "ability_haste", "tenacity": "tenacity",
    "total attack speed": "total_as", "attack speed": "total_as", "energy regen": "energyregen_mod",
}


def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def mayhem_champion_overrides(page_text, wiki_champs):
    """위키 ARAM: Mayhem 페이지의 챔피언 표 → {DDragon id: {키: 값}}. '… changed to X% from Y%' 의 X 를 쓴다."""
    m = re.search(r"Champions=(.*?)(?:\|-\||</tabber>)", page_text, re.S)
    if not m:
        return {}
    api = {k: v.get("apiname") for k, v in wiki_champs.items() if isinstance(v, dict)}
    out = {}
    for row in m.group(1).split("\n|-")[1:]:
        cm = re.search(r"\{\{ci\|([^}|]+)", row)
        if not cm:
            continue
        cid = api.get(cm.group(1).strip())
        mods = {}
        for line in re.findall(r"\*\s*([A-Za-z ]+?) changed to ([+-]?\d+(?:\.\d+)?)(%?)", row):
            key = MAYHEM_MOD_KEYS.get(line[0].strip().lower())
            if not key:
                continue
            v = float(line[1])
            mods[key] = v if key == "ability_haste" or not line[2] else round(1 + v / 100, 4)
        if cid and mods:
            out[cid] = mods
    return out


def wiki_mayhem_augments(module_text, wiki_champs):
    """위키 증강 모듈 → {정규화 영어 이름: {"disabled": bool, "eligible": [DDragon id …]}}"""
    data = parse_lua_table(module_text)
    api = {k: v.get("apiname") for k, v in wiki_champs.items() if isinstance(v, dict)}
    out = {}
    for name, v in data.items():
        if not isinstance(v, dict):
            continue
        notes = v.get("notes", "") or ""
        champs = sorted({api[c] for c in re.findall(r"\{\{c?ai\|[^|}]+\|([^|}]+)", notes) if c in api})
        # 위키는 퀘스트 증강 이름 앞에 'Quest: ' 를 붙인다 (게임 이름에는 없음)
        out[norm_name(re.sub(r"^Quest:\s*", "", name))] = {
            "disabled": "currently disabled" in (v.get("description") or ""),
            "eligible": champs if len(champs) >= 3 else None, "wikiName": name}
    return out


# ---------------------------------------------------------------- 증강 능력치 (rules.json stats)
def resolve_rule_stats(rules, aug_spells):
    """rules.json 의 stats 값을 숫자로 바꾼다.

    값 형식: 숫자 / "DataValue이름" / "-DataValue이름"(부호 반대) / {"calc": "계산식이름"}(레벨 비례 가능)
    게임 데이터 이름으로 적어 두면 패치로 수치가 바뀌어도 자동으로 따라간다.
    """
    warn = []
    for aid, r in rules["augments"].items():
        spec = r.get("stats")
        if not spec or aid not in aug_spells:
            continue
        calc = Calc(aug_spells[aid])
        out = {}
        for key, v in spec.items():
            if isinstance(v, (int, float, bool)):
                out[key] = v
            elif isinstance(v, str):
                neg = v.startswith("-")
                val = calc.value(v.lstrip("-"), 1)
                if val is None:
                    warn.append(f"{aid}.{key}: 데이터 값 '{v}' 없음")
                    continue
                out[key] = -val if neg else val
            elif isinstance(v, dict) and "calc" in v:
                e, _ = calc.calc(v["calc"], 1)
                if e is None:
                    warn.append(f"{aid}.{key}: 계산식 '{v['calc']}' 해석 실패")
                    continue
                out[key] = expr_json([e])
            elif isinstance(v, dict):  # 스킬별 가속 {"Q": "QAbilityHaste"}
                sub = {}
                for slot, name in v.items():
                    val = calc.value(name, 1)
                    if val is None:
                        warn.append(f"{aid}.{key}.{slot}: 데이터 값 '{name}' 없음")
                    else:
                        sub[slot] = val
                out[key] = sub
        r["statsResolved"] = out
    return warn


# ---------------------------------------------------------------- 수집
def configure(out=None, cache=None, refresh=None):
    """저장 위치를 바꾼다. 데스크톱 프로그램은 사용자 폴더에 받는다 (앱 번들 안은 쓰기 불가)."""
    global CACHE, WEB, ICONS, REFRESH
    if out:
        WEB = Path(out)
        ICONS = WEB / "icons"
    if cache:
        CACHE = Path(cache)
    if refresh is not None:
        REFRESH = refresh


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    for d in ("champion", "item", "augment"):
        (ICONS / d).mkdir(parents=True, exist_ok=True)

    version = fetch_json(f"{DDRAGON}/api/versions.json", "versions.json")[0]
    cd_version = fetch_json(f"{CDRAGON}/content-metadata.json", "content-metadata.json")["version"]
    print("패치 버전:", version, "/ 게임 데이터:", cd_version)
    champs = fetch_json(f"{DDRAGON}/cdn/{version}/data/ko_KR/champion.json", "champion.json")["data"]
    items = fetch_json(f"{DDRAGON}/cdn/{version}/data/ko_KR/item.json", "item.json")["data"]
    map12 = fetch_json(f"{CDRAGON}/game/data/maps/shipping/map12/map12.bin.json", "map12.bin.json")
    kiwi = fetch_json(f"{CDRAGON}/game/maps/modespecificdata/kiwi.bin.json", "kiwi.bin.json")
    aug_lists = fetch_json(f"{CDRAGON}/plugins/rcp-be-lol-game-data/global/ko_kr/v1/augment-lists.json",
                           "augment-lists.json")
    items_bin = fetch_json(f"{CDRAGON}/game/items.cdtb.bin.json", "items.cdtb.bin.json")
    print("문자열 테이블 받는 중 (약 35MB, 첫 실행만)...")
    strings = fetch_json(f"{CDRAGON}/game/ko_kr/data/menu/en_us/lol.stringtable.json",
                         "lol.stringtable.json")["entries"]
    # 위키 증강 이름(영어)과 짝짓기용 영어 문자열
    strings_en = fetch_json(f"{CDRAGON}/game/en_us/data/menu/en_us/lol.stringtable.json",
                            "lol.stringtable.en.json")["entries"]
    wiki_aug_text = fetch(f"{WIKI}/Module:MayhemAugmentData/data?action=raw", "wiki_mayhemaugments.lua").decode("utf-8")
    wiki_items = parse_lua_table(fetch(f"{WIKI}/Module:ItemData/data?action=raw", "wiki_itemdata.lua").decode("utf-8"))
    wiki_mayhem_page = fetch(f"{WIKI}/ARAM:_Mayhem?action=raw", "wiki_mayhem_page.txt").decode("utf-8")
    wiki = parse_lua_table(fetch(f"{WIKI}/Module:ChampionData/data?action=raw",
                                 "wiki_championdata.lua").decode("utf-8"))
    wiki_augs = wiki_mayhem_augments(wiki_aug_text, wiki)
    item_limit = {str(v.get("id")): v["itemlimit"] for v in wiki_items.values()
                  if isinstance(v, dict) and v.get("itemlimit") and v.get("id")}

    # --- 아이템: 칼바람 증강 모드가 실제로 쓰는 목록만
    mode = map12[MODE_PATH]
    mode_ids = set()
    for list_key in mode["itemLists"]:
        for ref in map12[list_key]["mItems"]:
            mode_ids.add(ref.split("/")[-1])
    out_items, missing, unknown_stats = [], [], []
    for iid in sorted(mode_ids, key=int):
        it = items.get(iid)
        if not it:
            missing.append(iid)
            continue
        if it.get("requiredChampion") or it.get("requiredAlly") or not it["gold"].get("purchasable"):
            continue
        is_boots = "Boots" in it.get("tags", [])
        depth = it.get("depth", 1)
        if is_boots and depth >= 2:
            kind = "boots"
        elif iid.startswith(("22", "99")) and len(iid) == 6:
            kind = "special"  # 증강 강화·퀘스트 보상 아이템 (22xxxx 6000G 강화템, 994403 황금 뒤집개). 상점 구매 불가
        elif not it.get("into") and it["gold"]["total"] >= 2000 and it.get("inStore", True):
            kind = "legendary"  # 상위 아이템이 없는 2000G 이상 완성템 (depth 로 판정하면 무한의 대검 등이 빠짐)
        else:
            continue  # 재료·소모품은 추천 대상 아님
        tags = {}
        for stat, val in it.get("stats", {}).items():
            if stat in ITEM_STATS and val:
                t, base = ITEM_STATS[stat]
                tags[t] = round(max(tags.get(t, 0), min(abs(val) / base, 1.5)), 2)
        for dt in it.get("tags", []):
            t = ITEM_DDRAGON_TAGS.get(dt)
            if t:
                tags[t] = max(tags.get(t, 0), 0.6)
        html, plain, names = to_html(it.get("description", ""))
        for t, s in tag_text(plain, names).items():
            if t == "HEALSHIELD" and "아군" not in plain:
                continue  # 자기 회복은 서포터용 '회복·보호막 강화'와 다르다
            if t in ITEM_DESC_TAGS:  # 스탯성 태그는 stats 값만 믿는다
                tags.setdefault(t, round(s * 0.7, 2))
        stats, unknown = item_stats(it.get("description", ""))
        unknown_stats.extend(f"{it['name']}: {u}" for u in unknown)
        effects, passive_html, mode_used = item_passive(items_bin.get(f"Items/{iid}"), strings)
        out_items.append({
            "id": iid, "name": it["name"], "kind": kind, "gold": it["gold"]["total"],
            "tags": tags, "stats": stats, "desc": html, "effects": effects,
            "limit": item_limit.get(iid),  # 같은 계열 고유 효과(주문 검·히드라 …)는 하나만
            "passiveHtml": passive_html, "modeValues": mode_used,
        })

    # --- 증강
    kiwi_names = next(g["augmentList"] for g in aug_lists if g["modeName"] == MODE)
    kiwi_names = [p.split("/")[-1] for p in kiwi_names]
    aug_data = {v["AugmentNameId"]: v for v in kiwi.values()
                if isinstance(v, dict) and v.get("__type") == "AugmentData"}
    rarity_name = {0: "실버", 1: "골드", 2: "프리즘"}
    out_augs, unresolved_total, disabled, disabled_ids, aug_spells = [], 0, [], set(), {}
    wiki_matched = 0
    for name in kiwi_names:
        a = aug_data.get(name)
        if not a:
            missing.append(name)
            continue
        en_name = strings_en.get((a.get("NameTra") or "").lower(), "")
        wa = wiki_augs.get(norm_name(en_name)) or {}
        if wa:
            wiki_matched += 1
        # 비활성: 게임 파일(Enabled=False) + 위키 '현재 비활성' 표시 (패치 없이 서버에서 끈 것까지)
        if a.get("Enabled") is False or wa.get("disabled"):
            label = strings.get((a.get("NameTra") or "").lower(), name)
            disabled.append(label + ("" if a.get("Enabled") is False else " (위키 기준)"))
            disabled_ids.add(name)
            continue
        spell = kiwi.get(a.get("RootSpell", ""), {}).get("mSpell", {})
        aug_spells[name] = spell
        raw = strings.get((a.get("AugmentTooltipTra") or "").lower()) \
            or strings.get((a.get("DescriptionTra") or "").lower()) or ""
        raw = expand_templates(raw, strings)
        text, unresolved = resolve_values(raw, spell)
        unresolved_total += len(unresolved)
        html, plain, names = to_html(text)
        icon = (a.get("AugmentLargeIconPath") or "").replace(".tex", ".png").lower()
        effects = augment_effects(raw, spell, plain)
        out_augs.append({
            "id": name,
            "effects": effects,
            "enName": en_name,
            "eligible": wa.get("eligible"),  # 위키에 적용 스킬 목록이 있으면 그 챔피언들만 발동
            "name": strings.get((a.get("NameTra") or "").lower(), name),
            "rarity": a.get("rarity", 0),
            "rarityName": rarity_name.get(a.get("rarity", 0), "?"),
            "desc": html,
            "tags": tag_text(plain, names),
            "unresolved": len(unresolved),
            "_icon": icon,
        })

    # --- 챔피언 (상세 데이터에서 스킬 특성 추출)
    (CACHE / "champion").mkdir(exist_ok=True)

    def detail(cid):
        return fetch_json(f"{DDRAGON}/cdn/{version}/data/ko_KR/champion/{cid}.json",
                          f"champion/{cid}.json")["data"][cid]

    (CACHE / "bins").mkdir(exist_ok=True)

    def champ_bin(cid):
        for attempt in range(3):
            try:
                return fetch_json(f"{CDRAGON}/game/data/characters/{cid.lower()}/{cid.lower()}.bin.json",
                                  f"bins/{cid}.bin.json")
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)

    print("챔피언 상세·스킬 데이터 받는 중...")
    with ThreadPoolExecutor(8) as ex:
        details = dict(zip(champs, ex.map(detail, champs)))
        bins = dict(zip(champs, ex.map(champ_bin, champs)))
    out_champs, spell_unresolved, no_wiki, passive_found = [], 0, [], 0
    mayhem_mods = mayhem_champion_overrides(wiki_mayhem_page, wiki)
    for cid, c in champs.items():
        d = details[cid]
        traits, magic_share, features = champion_traits(d)
        spells, unresolved = process_champion(cid, d, bins[cid])
        p_spell, p_key = passive_spell(bins[cid], cid)
        p_raw = expand_templates(strings.get((p_key or "").lower(), ""), strings)
        p_effects, p_html = [], None
        if p_raw:
            p_text, _ = resolve_values(p_raw, p_spell)
            p_html, p_plain, _ = to_html(p_text)
            p_effects = text_effects(p_raw, p_spell, p_plain)
            passive_found += 1
        spell_unresolved += unresolved
        for sp in spells:
            sp["tooltip"], _, _ = to_html(sp["tooltip"])
        aram, as_ratio = wiki_extras(wiki, c["name"], cid)
        # 칼바람 증강 모드: 일반 칼바람 보정 위에 모드 전용 보정을 덮어쓴다 (위키)
        aram_source = "칼바람" if aram else None
        if cid in mayhem_mods:
            aram = {**(aram or {}), **mayhem_mods[cid]}
            aram_source = "칼바람 + 증강 모드"
        if as_ratio is None:
            no_wiki.append(cid)
        out_champs.append({
            "id": c["id"], "key": c["key"], "name": c["name"], "title": c["title"],
            "tags": c["tags"], "partype": c["partype"], "info": c["info"],
            "range": c["stats"]["attackrange"], "melee": c["stats"]["attackrange"] <= 250,
            "traits": traits, "magicShare": magic_share,
            "features": features + (["melee"] if c["stats"]["attackrange"] <= 250 else ["ranged"]),
            "stats": c["stats"], "asRatio": as_ratio or c["stats"]["attackspeed"], "aram": aram,
            "aramSource": aram_source,
            "passive": {"name": d["passive"]["name"], "desc": p_html or to_html(d["passive"]["description"])[0],
                        "effects": p_effects},
            "spells": spells,
        })
    out_champs.sort(key=lambda c: c["name"])

    # --- 아이콘
    jobs = [(f"{DDRAGON}/cdn/{version}/img/champion/{c['id']}.png", ICONS / "champion" / f"{c['id']}.png")
            for c in out_champs]
    jobs += [(f"{DDRAGON}/cdn/{version}/img/item/{i['id']}.png", ICONS / "item" / f"{i['id']}.png")
             for i in out_items]
    jobs += [(f"{CDRAGON}/game/{a['_icon']}", ICONS / "augment" / f"{a['id']}.png")
             for a in out_augs if a["_icon"]]
    jobs = [(u, p) for u, p in jobs if REFRESH or not p.exists()]
    failed = []

    def dl(job):
        url, path = job
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "aram-augment-tool"})
            with urllib.request.urlopen(req, timeout=60) as r:
                path.write_bytes(r.read())
        except Exception:
            failed.append(url)

    print(f"아이콘 {len(jobs)}개 받는 중...")
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(dl, jobs))
    for a in out_augs:
        a["icon"] = (ICONS / "augment" / f"{a['id']}.png").exists()
        del a["_icon"]

    rules = json.loads((ROOT / "rules.json").read_text(encoding="utf-8"))
    aug_ids = {a["id"] for a in out_augs}
    item_names = {i["name"] for i in out_items}
    for aid, r in rules["augments"].items():
        if aid not in aug_ids and aid not in disabled_ids:  # 비활성 증강 규칙은 다시 켜질 때를 위해 남겨 둔다
            print("  [규칙] 없는 증강:", aid)
        for n in r.get("items", []):
            if n not in item_names:
                print("  [규칙] 없는 아이템:", aid, n)
    rule_warn = resolve_rule_stats(rules, aug_spells)
    for item in out_items:  # 아이템 고유 효과 능력치
        ir = rules.get("items", {}).get(item["name"])
        if not ir:
            continue
        plain = re.sub(r"<[^>]+>", "", item["desc"])
        if ir.get("check") and ir["check"] not in plain:
            rule_warn.append(f"아이템 {item['name']}: 설명에 '{ir['check']}' 없음 (패치로 수치 변경?) → 미적용")
            continue
        item["passiveStats"] = ir["stats"]
    data = {"version": version, "dataVersion": cd_version, "mode": MODE, "tags": TAGS, "rules": rules,
            "disabled": disabled,
            "champions": out_champs, "items": out_items, "augments": out_augs}
    (WEB / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    n_leg = sum(i["kind"] == "legendary" for i in out_items)
    n_boots = sum(i["kind"] == "boots" for i in out_items)
    no_tag = sum(1 for a in out_augs if not a["tags"])
    with_q = sum(1 for a in out_augs if a["unresolved"])
    print(f"챔피언 {len(out_champs)} / 아이템 전설 {n_leg} + 신발 {n_boots} / 증강 {len(out_augs)}")
    print(f"  비활성(Enabled=False)이라 제외한 증강 {len(disabled)}개: {', '.join(disabled)}")
    print(f"  스킬 수치 못 채운 자리 {spell_unresolved}개 (스킬 {sum(len(c['spells']) for c in out_champs)}개 중)")
    print(f"  위키 데이터 없는 챔피언 {len(no_wiki)}명: {', '.join(no_wiki)}")
    print(f"  위키 증강 짝짓기 {wiki_matched}/{len(kiwi_names)}, 적용 스킬 목록 있는 증강 "
          f"{sum(1 for a in out_augs if a.get('eligible'))}개, 증강 모드 전용 보정 챔피언 {len(mayhem_mods)}명, "
          f"같은 계열 표시 아이템 {sum(1 for i in out_items if i.get('limit'))}개")
    print(f"  패시브 수치 설명 찾음 {passive_found}명, 패시브 효과(피해·보호막·회복) 있는 챔피언 "
          f"{sum(1 for c in out_champs if c['passive']['effects'])}명")
    print(f"  고유 효과 수치 뽑은 아이템 {sum(1 for i in out_items if i['effects'])}개 / {len(out_items)}개, "
          f"칼바람 전용 수치 적용 {sum(1 for i in out_items if i['modeValues'])}개")
    print(f"  아이템 능력치 줄 해석 실패 {len(unknown_stats)}개: {unknown_stats[:5]}")
    for w in rule_warn:
        print("  [규칙 수치]", w)
    print(f"  증강 중 수치 일부 못 채움(?) {with_q}개, 효과 태그 분류 안 됨 {no_tag}개")
    print(f"  모드 목록에 있지만 데이터 없음 {len(missing)}개, 아이콘 실패 {len(failed)}개")
    for u in failed[:5]:
        print("   실패:", u)


if __name__ == "__main__":
    main()
