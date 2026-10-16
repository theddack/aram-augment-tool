#!/usr/bin/env python3
"""칼바람 증강 모드(KIWI) 데이터 수집기.

Data Dragon(챔피언·아이템)과 CommunityDragon(증강·모드 아이템 목록·문자열)을 받아
web/data.json 과 web/icons/ 를 만든다. 원본 응답은 .cache/ 에 저장해 재실행 시 재사용한다.

  python3 build_data.py            # 캐시 있으면 재사용
  python3 build_data.py --refresh  # 패치 후 전부 다시 받기
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from champion_data import (augment_effects, item_spell, parse_lua_table, passive_spell, process_champion,
                           spell_cc_seconds, text_effects, wiki_extras, wiki_growth_fix)
from lol_calc import Calc, expr_json, fmt_expr_ranks
from patch_check import check_augment, check_champion

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


DATA_FORMAT = 2  # data.json 형식 번호: 화면이 기대하는 칸이 바뀌면 올린다 (프로그램이 옛 형식의 받아 둔 데이터를 교체)
SAFE_ID = re.compile(r"[A-Za-z0-9_]{1,80}")  # 외부 데이터의 id 검사 (HTML 속성·파일 이름에 그대로 쓰인다)
UA = "kalbaram-addon (+https://github.com/theddack/kalbaram-addon)"  # 누가 받는지 밝힌다
FETCHED = {}
WIKI_FAILED = []  # 이번에 못 받은 위키 자료 (확인 이슈에 올림)  # 출처 기록: 캐시 이름 → (주소, 받은 시각)


DAMAGE_TYPE = {"kPhysical": "physical", "kMagic": "magic", "kMixed": "mixed"}


def fetch(url, name):
    path = CACHE / name
    FETCHED.setdefault(name, (url, time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
                                   if path.exists() and not REFRESH else time.strftime("%Y-%m-%d %H:%M")))
    if path.exists() and not REFRESH:
        return path.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except Exception as e:
        if path.exists():  # 받기 실패 → 전에 받아 둔 것으로 (갱신 전체가 멈추지 않게)
            print(f"  [받기 실패 → 이전 파일 사용] {name}: {e}")
            return path.read_bytes()
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")  # 받다 끊긴 파일이 캐시에 남지 않게
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return data


def fetch_wiki(url, name, parse=None, empty=None):
    """위키는 보강 자료라, 받기·해석에 실패해도 갱신 전체를 멈추지 않는다 (빈 값 + 경고)"""
    try:
        text = fetch(url, name).decode("utf-8")
        return parse(text) if parse else text
    except Exception as e:
        print(f"  [위키 실패 → 없이 진행] {name}: {e}")
        WIKI_FAILED.append(name)
        return empty


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
    "summon": r"티버|포탑을 (설치|배치|세)|분신|환각|식물|데이지|소환물|구울|안개 수행자|깜짝 상자|소환한 |새끼 거미|모래 병사|촉수를 소환|무리를 소환|묘목",
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


def patch_notes_url(ddragon_version, lang="ko-kr"):
    """Data Dragon 16.20.1 → 공식 패치 노트 26.20 주소 (2026년부터 연도 기준 번호)"""
    major, minor = ddragon_version.split(".")[:2]
    return (f"https://www.leagueoflegends.com/{lang}/news/game-updates/"
            f"league-of-legends-patch-{int(major) + 10}-{minor}-notes/"), f"{int(major) + 10}.{minor}"


def parse_mayhem_patch(page_html):
    """패치 노트의 '무작위 총력전: 아수라장' 섹션 → {intro, groups: [{title, entries: [{name, lines}]}]}"""
    import html as _html
    a = page_html.find('id="patch-aram:-mayhem"')
    if a < 0:
        return None
    b = page_html.find('<h2 id="patch-', a + 10)
    sec = page_html[a:b if b > 0 else None]
    txt = re.sub(r"<(h[2-4]|li|p|br|div)[^>]*>", "\n", sec)
    lines = [_html.unescape(x).strip() for x in re.sub(r"<[^>]+>", "", txt).split("\n")]
    lines = [x for x in lines if x and not x.startswith('id="')]
    groups, intro, cur, entry = [], [], None, None
    for ln in lines:
        if ln in ("챔피언", "증강", "아이템", "버그 수정", "룬"):
            cur = {"title": ln, "entries": []}
            groups.append(cur)
            entry = None
            continue
        if cur is None:
            intro.append(ln)
        elif cur["title"] == "버그 수정":
            cur["entries"].append({"name": "", "lines": [ln]})
        elif "⇒" in ln or ln.startswith(("삭제", "신규")) or ":" in ln:
            if entry is None:
                entry = {"name": "", "lines": []}
                cur["entries"].append(entry)
            entry["lines"].append(ln)
        else:
            entry = {"name": ln, "lines": []}
            cur["entries"].append(entry)
    return {"intro": intro[1:] if intro and intro[0].startswith("무작위") else intro, "groups": groups}


def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def mayhem_global_stats(page_text):
    """위키 ARAM: Mayhem 'Global stat changes' → 모든 챔피언이 받는 모드 능력치.
    {"hp": {"melee": [1레벨, 18레벨], "ranged": [...]}, "asCap": 공속 상한, "critAdaptive": 치명타 100% 초과 1%당 적응형}
    (2026-10-16: 베인·미스 포츈·케일 실제 체력이 원거리 20 + 380/17 × (레벨-1) 만큼 많은 것과 일치)"""
    m = re.search(r"=== Global stat changes ===(.*?)\n===", page_text, re.S)
    if not m:
        return {}
    sec, out = m.group(1), {}
    hp = re.search(r"formula1=(\d+) \+ \(\((\d+) - \d+\) / 17\).*?formula2=(\d+) \+ \(\((\d+) - \d+\) / 17\)", sec)
    if hp and "health" in sec.lower():
        out["hp"] = {"melee": [float(hp.group(1)), float(hp.group(2))], "ranged": [float(hp.group(3)), float(hp.group(4))]}
    cap = re.search(r"Attack speed cap changed to \{\{fd\|([\d.]+)\}\}", sec)
    if cap:
        out["asCap"] = float(cap.group(1))
    crit = re.search(r"in excess of .*?grants \{\{adaptive\|([\d.]+)\}\}", sec)
    if crit:
        out["critAdaptive"] = float(crit.group(1))
    return out


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


def _wiki_ranks(text, ranks):
    """'{{ap|18 to 12}}' / '10 seconds at all ranks' / '60' → 랭크별 값 목록"""
    m = re.search(r"\{\{ap\|([\d.]+) to ([\d.]+)\}\}", text)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return [round(a + (b - a) * i / max(1, ranks - 1), 2) for i in range(ranks)]
    m = re.search(r"\{\{ap\|([\d.]+(?:\|[\d.]+)+)\}\}", text)
    if m:
        vals = [float(x) for x in m.group(1).split("|")]
        return vals if len(vals) == ranks else None
    m = re.search(r"([\d.]+)\s*seconds?", text)
    return [float(m.group(1))] * ranks if m else None


def mayhem_cooldowns(page_text, wiki_champs):
    """위키 증강 모드 표의 스킬 조정 중 재사용 대기시간 → {DDragon id: {슬롯: '문구'}}
    정적 게임 데이터에는 모드별 쿨다운이 없어서(서버 적용) 위키에서 가져온다."""
    m = re.search(r"Champions=(.*?)(?:\|-\||</tabber>)", page_text, re.S)
    if not m:
        return {}
    api = {k: v.get("apiname") for k, v in wiki_champs.items() if isinstance(v, dict)}
    names = {}  # 위키 스킬 이름 → 슬롯
    for k, v in wiki_champs.items():
        if isinstance(v, dict):
            for slot in "qwer":
                for nm in (v.get(f"skill_{slot}") or {}).values() if isinstance(v.get(f"skill_{slot}"), dict) else (v.get(f"skill_{slot}") or []):
                    names[(k, nm)] = slot.upper()
    out = {}
    for row in m.group(1).split("\n|-")[1:]:
        cm = re.search(r"\{\{ci\|([^}|]+)", row)
        if not cm:
            continue
        champ = cm.group(1).strip()
        cid = api.get(champ)
        cur = None
        for line in row.split("\n"):
            am = re.search(r"\{\{c?ai\|([^|}]+)\|" + re.escape(champ), line)
            if am:
                ab = am.group(1).strip()
                cur = ab if ab in "QWER" else names.get((champ, ab))
                continue
            if cur and cid and re.search(r"^\*\s*Cooldown changed to", line.strip()):
                out.setdefault(cid, {})[cur] = line.strip()
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
            "eligible": champs if len(champs) >= 3 else None, "wikiName": name,
            "description": v.get("description") or ""}
    return out


# ---------------------------------------------------------------- 출처 목록 (화면 '데이터 출처' 페이지)
SOURCE_INFO = [  # (캐시 이름 앞부분, 이름, 라이선스·이용 조건, 쓰는 곳)
    ("versions", "Riot Data Dragon", "라이엇 공개 데이터 (팬 제작물 정책)", "패치 버전"),
    ("champion", "Riot Data Dragon 챔피언", "라이엇 공개 데이터", "챔피언 이름·기본 능력치·스킬 설명(한글)"),
    ("item.json", "Riot Data Dragon 아이템", "라이엇 공개 데이터", "아이템 이름·능력치·설명"),
    ("map12", "CommunityDragon map12", "게임 파일 추출 (공개)", "칼바람 증강 모드 상점 아이템 목록"),
    ("kiwi", "CommunityDragon 칼바람 증강 모드", "게임 파일 추출 (공개)", "증강 수치·계산식·비활성 여부"),
    ("augment-lists", "CommunityDragon 증강 목록", "게임 파일 추출 (공개)", "모드별 증강 목록"),
    ("items.cdtb", "CommunityDragon 아이템 데이터", "게임 파일 추출 (공개)", "아이템 고유 효과 수치·모드 전용 수치"),
    ("lol.stringtable.json", "CommunityDragon 한국어 문자열", "게임 파일 추출 (공개)", "증강·패시브 한글 설명"),
    ("lol.stringtable.en", "CommunityDragon 영어 문자열", "게임 파일 추출 (공개)", "위키 증강 이름 짝짓기"),
    ("bins/", "CommunityDragon 챔피언 데이터", "게임 파일 추출 (공개)", "스킬 수치·계산식·모드 전용 수치 (173명)"),
    ("wiki_championdata", "LoL 위키 Module:ChampionData", "CC BY-SA 4.0", "칼바람 보정·공격 속도 계수·레벨당 공격력(Data Dragon 값이 0일 때)"),
    ("wiki_mayhemaugments", "LoL 위키 Module:MayhemAugmentData", "CC BY-SA 4.0", "비활성 증강·적용 스킬 목록"),
    ("wiki_itemdata", "LoL 위키 Module:ItemData", "CC BY-SA 4.0", "아이템 같은 계열"),
    ("wiki_mayhem_page", "LoL 위키 ARAM: Mayhem", "CC BY-SA 4.0", "증강 모드 전용 챔피언 보정·재사용 대기시간"),
    ("patchnotes", "라이엇 공식 패치 노트 (한국어)", "라이엇 공개 게시물 (요약·링크)", "이번 패치 아수라장 변경"),
]


def build_sources(fetched, patch_url):
    out = []
    for prefix, name, lic, used in SOURCE_INFO:
        hit = [(k, v) for k, v in fetched.items() if k.startswith(prefix)]
        if not hit:
            continue
        url, when = hit[0][1]
        if prefix == "bins/":
            url = f"{CDRAGON}/game/data/characters/"
        if prefix == "champion" and len(hit) > 1:
            url = f"{DDRAGON}/cdn/"
        out.append({"name": name, "url": url.split("?action=raw")[0], "license": lic, "used": used,
                    "fetched": max(v[1] for _, v in hit), "count": len(hit)})
    out.append({"name": "LoL 위키 골드 효율", "url": f"{WIKI}/Gold_efficiency", "license": "CC BY-SA 4.0",
                "used": "골드·모루 → 능력치 환산 기준값", "fetched": "수동 확인 2026-10-14", "count": 1})
    return out


# ---------------------------------------------------------------- 출처 링크
def wiki_page(name):
    return f"{WIKI}/" + urllib.parse.quote(name.replace(" ", "_"), safe=":/'()")


def aug_refs(en_name, wa, patch_url):
    refs = [{"label": "게임 데이터", "url": "https://raw.communitydragon.org/latest/game/maps/modespecificdata/kiwi.bin.json"}]
    if wa:
        refs.append({"label": "위키", "url": f"{WIKI}/ARAM:_Mayhem/Augments"})
    if patch_url:
        refs.append({"label": "이번 패치 노트", "url": patch_url + "#patch-aram:-mayhem"})
    if en_name:  # 통계는 가져오지 않고 보러 가는 링크만
        slug = re.sub(r"[^a-z0-9]+", "-", en_name.lower()).strip("-")
        refs.append({"label": "통계 보기 (외부)", "url": f"https://arammayhem.com/augments/{slug}/", "external": True})
    return refs


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
            elif isinstance(v, dict) and "dv" in v:  # 게임 데이터 값 × 배수 (평균 중첩 같은 가정은 basis 에 이유)
                val = calc.value(v["dv"], 1)
                if val is None:
                    warn.append(f"{aid}.{key}: 데이터 값 '{v['dv']}' 없음")
                    continue
                out[key] = round(val * v.get("times", 1), 6)
            elif isinstance(v, dict) and "calc18" in v:  # 계산식의 18레벨 값 (중첩 계수는 stacks 개) × 배수
                e, _ = calc.calc(v["calc18"], 1)
                if e is None:
                    warn.append(f"{aid}.{key}: 계산식 '{v['calc18']}' 해석 실패")
                    continue
                if not hasattr(e, "ratios"):  # 곱셈 식은 여기서 숫자 하나로 못 줄인다
                    warn.append(f"{aid}.{key}: 계산식 '{v['calc18']}' 은 곱셈 식이라 18레벨 값으로 못 씀")
                    continue
                val = e.base + (e.lvl[1] if e.lvl else 0) + e.ratios.get("STACKS", 0) * v.get("stacks", 0)
                out[key] = round(val * v.get("times", 1), 6)
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


def rule_effects(rules, aug_spells, out_augs, warn):
    """rules.json effects: 설명에서 자동으로 못 뽑은 피해 효과를 게임 계산식 이름으로 붙인다 (패치 수치를 따라감)"""
    by_id = {a["id"]: a for a in out_augs}
    for aid, r in rules["augments"].items():
        if not r.get("effects") or aid not in by_id or aid not in aug_spells:
            continue
        calc = Calc(aug_spells[aid])
        for spec in r["effects"]:
            e, _ = calc.calc(spec["calc"], 1)
            if e is None:
                warn.append(f"{aid}: 효과 계산식 '{spec['calc']}' 해석 실패")
                continue
            trig = dict(spec.get("trigger") or {"type": "once"})
            if isinstance(trig.get("sec"), str):
                trig["sec"] = calc.value(trig["sec"], 1) or 0
            trig["from"] = spec.get("from", "rules.json")
            by_id[aid]["effects"].append({"kind": spec["kind"], "name": spec["calc"], **expr_json([e]), "trigger": trig})


# 자동 규칙: rules.json 에 없는 증강(새 증강 등)은 설명의 '조건 없는 능력치 문장'만 능력치로 읽는다.
# 설명은 매번 게임 데이터 수치로 다시 채우므로 패치로 숫자가 바뀌어도 따라간다. 화면에 '자동 추정'으로 표시.
AUTO_STAT = [  # (설명 속 능력치 이름, 키, % 일 때 키, 나눌 값). 긴 이름부터 (체력 회복 및 보호막 효과 > 체력)
    ("회복 및 보호막 효과", "HSP", "HSP", 100), ("모든 피해 흡혈", "OMNIVAMP", "OMNIVAMP", 100),
    ("생명력 흡수", "LS", "LS", 100), ("치명타 확률", "CRIT", "CRIT", 100), ("공격 속도", "AS", "AS", 100),
    ("스킬 가속", "HASTE", None, 1), ("적응형 능력치", "adaptive", "adaptiveMult", 100),
    ("물리 관통력", "LETHALITY", None, 1), ("마법 관통력", "MPEN", "MPENP", 100),
    ("이동 속도", "MS", "MSP", 100), ("마법 저항력", "MR", None, 1), ("방어력", "ARMOR", None, 1),
    ("주문력", "AP", "APmult", 100), ("공격력", "AD", "ADmult", 100), ("체력", "HP", "HPmult", 100),
]
AUTO_COND = re.compile(r"시 |마다|동안|하면|면 |할 때|경우|중첩|퀘스트|보상|보유|이상|이하|처치|사망|적중|타격|"
                       r"사용하|대상|아군|주변|감소|잃|레벨 비례|당 |초 후|전투")
AUTO_SKIP = re.compile(r"멈춥|에만|만 적용|대신|바뀝|변경")  # 설명 어딘가에 이런 말이 있으면 통째로 사람 판단
AUTO_QUAL = re.compile(r"(궁극기|눈덩이|기본|총|소환사 주문|아이템|[QWER]|의)\s*$")  # 특정 대상 한정 수식어


def auto_stats(desc_html):
    """설명에서 조건 없는 '능력치가 N(%) 증가/획득' 문장을 능력치로. → ({키: 값}, [근거 문장])"""
    text = re.sub(r"<br\s*/?>", "\n", desc_html)
    text = re.sub(r"<[^>]+>", "", text)
    if AUTO_SKIP.search(text):
        return {}, []
    out, used = {}, []
    for i, sent in enumerate(re.split(r"(?<=다\.)|\n", text)):
        if (sent if i == 0 else sent[1:]).startswith(" "):  # 앞의 아이콘·스킬 이름이 빠진 문장 (Q 스킬 가속 등)
            continue
        s = sent.strip()
        if not s or AUTO_COND.search(s):
            continue
        hit = False
        for name, key, pkey, div in AUTO_STAT:
            m = re.search(re.escape(name) + r"(?:이|가|을|를)\s*([\d.]+)(%?)\s*(?:증가|획득|얻)", s)
            if not m or AUTO_QUAL.search(s[:m.start()]):
                continue
            k = pkey if m.group(2) else key
            if not k or k in out:
                continue
            out[k] = float(m.group(1)) / (div if m.group(2) else 1)
            s = s.replace(m.group(0), "")
            hit = True
        if hit:
            used.append(sent.strip())
    return out, used


# 화면 '근거'용 능력치 이름 (rules.json stats 키)
STAT_LABEL = {
    "HP": "체력", "AD": "공격력", "AP": "주문력", "ARMOR": "방어력", "MR": "마법 저항력", "AS": "공격 속도",
    "CRIT": "치명타 확률", "HASTE": "스킬 가속", "MS": "이동 속도", "MSP": "이동 속도", "LS": "생명력 흡수",
    "OMNIVAMP": "모든 피해 흡혈", "LETHALITY": "물리 관통력", "MPEN": "마법 관통력", "MPENP": "마법 관통력",
    "HSP": "회복·보호막 효과", "HPmult": "체력", "ADmult": "공격력", "APmult": "주문력", "ASmult": "총 공격 속도",
    "adaptive": "적응형 능력치", "adaptiveMult": "적응형 능력치", "dmgDealtMult": "가하는 피해",
    "skillDmgMult": "스킬 피해", "effectDmgMult": "아이템·증강 피해", "goldValue": "골드 가치",
    "basicHaste": "기본 스킬 가속", "ultHaste": "궁극기 가속 효과", "ultHasteAdd": "궁극기 가속",
    "slotHaste": "스킬별 가속", "onhitCdr": "적중 시 재사용 대기시간 감소(초)", "refundPct": "재사용 대기시간 반환 비율",
    "refundChanceRanged": "반환 확률(원거리)", "refundChanceMelee": "반환 확률(근접)", "invulnSec": "무적 시간(초)",
    "drSec": "피해 감소 → 무적 환산(초)", "controlSec": "제어력(초)", "controlPerCast": "스킬당 제어력(초)",
    "fearPerCast": "스킬당 공포(초)", "selfHeal": "생명선 회복", "ehpPhysMult": "받는 물리 피해 감소",
    "ehpMagFlat": "마법 보호막", "bonusMANAtoAP": "추가 마나 → 주문력", "itemHPbonus": "아이템 체력 추가",
    "bonusResistMult": "추가 방어·마법 저항", "onhitRemainingPct": "남은 재사용 대기시간 감소",
    "refundAll": "모든 스킬에 반환 적용", "manaToHP": "최대 마나 → 체력", "skillVamp": "스킬 피해 → 회복",
    "healToDamage": "회복 → 피해", "ehpMult": "받는 피해 감소", "shieldMult": "스킬 보호막 강화", "ARPEN": "방어구 관통력", "AStoHASTE": "공격 속도 → 스킬 가속 전환", "critToHaste": "치명타 → 스킬 가속",
    "ultRefundEvery": "궁극기 추가 사용 주기(초)", "ultTriggerHaste": "궁극기 후 가속", "ultTriggerDur": "궁극기 후 지속(초)",
    "ultTriggerMs": "궁극기 후 이동 속도", "dashHaste": "돌진 스킬 가속", "fearCdDiv": "공포 시간 계산 나눗수",
}
# 0~1 비율로 저장하는 키 → 화면에 % 로
PCT_KEYS = {"AS", "CRIT", "MSP", "LS", "OMNIVAMP", "HSP", "MPENP", "HPmult", "ADmult", "APmult", "ASmult",
            "adaptiveMult", "dmgDealtMult", "skillDmgMult", "effectDmgMult", "refundPct", "refundChanceRanged",
            "refundChanceMelee", "ehpPhysMult", "itemHPbonus", "bonusResistMult", "onhitRemainingPct",
            "bonusMANAtoAP", "ultTriggerMs", "manaToHP", "skillVamp", "healToDamage", "ehpMult", "shieldMult", "ARPEN"}


def _fmt_val(v, key=None):
    if key in PCT_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{round(v * 100, 2):g}%"
    if isinstance(v, dict):
        return " · ".join(f"{k} {_fmt_val(x)}" for k, x in v.items()) if "base" not in v else "레벨 비례"
    if isinstance(v, bool):
        return "예" if v else "아니요"
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def rule_basis(rule, resolved, desc_html, warn, who):
    """규칙 수치마다 근거를 붙인다: 📊 게임 데이터 값 / 📖 설명 수치 / 🧮 계산 / ⚠️ 가정(이유). 근거 없는 가정값은 경고."""
    spec = rule.get("stats") or {}
    nums = {round(float(x), 4) for x in re.findall(r"\d+(?:\.\d+)?", re.sub(r"<[^>]+>", "", desc_html or ""))}
    out, unexplained = [], []
    if rule.get("auto"):
        out.append({"t": "auto", "text": rule.get("model", "").replace("자동 추정 (검토 필요): ", "설명 문장에서 자동으로 읽음: ")})
        wc = rule.get("wikiCheck") or {}
        ok = [STAT_LABEL.get(k, k) for k, v in wc.items() if v is True]
        bad = [STAT_LABEL.get(k, k) for k, v in wc.items() if v is False]
        if ok:
            out.append({"t": "wiki", "text": f"위키 설명과 일치: {', '.join(ok)}"})
        if bad:
            out.append({"t": "missing", "text": f"위키 설명과 다름 (확인 필요): {', '.join(bad)}"})
    for k, v in spec.items():
        label = STAT_LABEL.get(k, k)
        val = resolved.get(k, v)
        if isinstance(v, str):
            out.append({"t": "game", "text": f"{label} {_fmt_val(val, k)} — 게임 데이터 값 '{v.lstrip('-')}'"})
        elif isinstance(v, dict) and "dv" in v:
            out.append({"t": "game", "text": f"{label} {_fmt_val(val, k)} — 게임 데이터 값 '{v['dv']}'" + (f" × {v['times']:g}" if v.get("times", 1) != 1 else "")})
        elif isinstance(v, dict) and "calc18" in v:
            out.append({"t": "game", "text": f"{label} {_fmt_val(val, k)} — 게임 계산식 '{v['calc18']}' 18레벨 값"
                        + (f" (중첩 {v['stacks']}개)" if v.get("stacks") else "") + (f" × {v['times']:g}" if v.get("times", 1) != 1 else "")})
        elif isinstance(v, dict) and "calc" in v:
            out.append({"t": "game", "text": f"{label} — 게임 계산식 '{v['calc']}'"})
        elif isinstance(v, dict):
            out.append({"t": "game", "text": f"{label} {_fmt_val(val)} — 게임 데이터 값 {', '.join(v.values())}"})
        elif isinstance(v, bool):
            out.append({"t": "desc", "text": f"{label}: 설명 내용"})
        elif rule.get("basis"):
            continue  # 계산 근거(basis)가 설명한다. 설명 숫자와 우연히 같아도 '설명 수치'로 표시하지 않는다
        elif round(v, 4) in nums or round(v * 100, 4) in nums:
            out.append({"t": "desc", "text": f"{label} {_fmt_val(v, k)} — 설명에 나온 수치"})
        else:
            unexplained.append(f"{label} {_fmt_val(v, k)}")
    b = rule.get("basis") or {}
    if b.get("calc"):
        out.append({"t": "calc", "text": b["calc"]})
    for a in b.get("assume") or []:
        out.append({"t": "assume", "text": a["what"], "why": a["why"], "noData": a.get("noData", False)})
    if unexplained and not b:
        warn.append(f"{who}: 근거 없는 가정값 {', '.join(unexplained)} → rules.json basis 필요")
        out.append({"t": "missing", "text": "근거를 아직 적지 않은 값: " + ", ".join(unexplained)})
    return out


# 자동 추정 키 → 위키 영어 설명의 능력치 이름
WIKI_STAT = {
    "AS": "attack speed", "CRIT": "critical strike chance", "HASTE": "ability haste", "MS": "movement speed",
    "MSP": "movement speed", "AD": "attack damage", "ADmult": "attack damage", "AP": "ability power",
    "APmult": "ability power", "HP": "health", "HPmult": "health", "ARMOR": "armor", "MR": "magic resistance",
    "LS": "life steal", "OMNIVAMP": "omnivamp", "HSP": "heal and shield power", "adaptive": "adaptive force",
    "adaptiveMult": "adaptive force", "LETHALITY": "lethality", "MPEN": "magic penetration", "MPENP": "magic penetration",
}


def wiki_check(stats, wiki_text):
    """자동 추정 값마다 위키 설명에 같은 숫자 + 같은 능력치가 있는지: {키: True(일치)/False(다름)/None(위키에 없음)}"""
    text = re.sub(r"\{\{[a-z]+\|([^{}|]*\|)?", "", wiki_text or "", flags=re.I)
    text = re.sub(r"[{}'|\[\]]", " ", text).lower()
    out = {}
    for k, v in stats.items():
        word = WIKI_STAT.get(k)
        if not word or word not in text:
            out[k] = None
            continue
        want = round(v * 100, 2) if k in PCT_KEYS else round(v, 2)
        nums = set()
        for m in re.finditer(re.escape(word), text):
            nums.update(round(float(x), 2) for x in re.findall(r"\d+(?:\.\d+)?", text[max(0, m.start() - 40):m.start()]))
        out[k] = want in nums
    return out


def augment_category(plain):
    """숫자로 비교하기 어려운 증강의 종류 (화면에서 '왜 숫자가 없는지' 보여 주기)"""
    if re.search(r"퀘스트\s*:|조건\s*:.*보상\s*:", plain):
        return "quest"
    if re.search(r"무작위[^.]{0,12}(증강|영혼)|증강[^.]{0,8}무작위|일정 확률로 능력치 모루", plain):
        return "random"
    if re.search(r"소환사 주문|점멸 사용 가능 횟수|사용 시 커서 방향으로 돌진", plain):
        return "spell"
    return None


def augment_history(prev, out_augs, version):
    """이전 data.json 과 비교해 새 증강·설명 바뀐 증강을 찾는다.

    처음 본 패치(firstSeen)·설명이 바뀐 패치(descChanged)는 다음 갱신에도 이어 가서,
    같은 패치 동안은 매일 같은 목록이 나온다 (확인 이슈 내용이 하루 만에 사라지지 않게).
    """
    mm = lambda v: ".".join(str(v or "").split(".")[:2])
    old = {a["id"]: a for a in (prev or {}).get("augments", [])}
    for a in out_augs:
        # 게임 원문(자리표시자 채우기 전) 기준: 우리 해석 코드를 고쳐도 '바뀜'으로 잡히지 않게. 숫자는 빼고 문장만
        h = hashlib.sha1(re.sub(r"[\d.]+", "#", a.pop("_raw", "")).encode()).hexdigest()[:12]
        a["srcHash"] = h
        o = old.get(a["id"])
        if not prev:
            a["firstSeen"] = None  # 비교할 이전 데이터가 없으면 모름 (신규 표시 안 함)
        elif not o:
            a["firstSeen"] = version
        else:
            a["firstSeen"] = o.get("firstSeen")
            a["descChanged"] = version if o.get("srcHash") and o["srcHash"] != h else o.get("descChanged")
    new = [a for a in out_augs if a.get("firstSeen") and mm(a["firstSeen"]) == mm(version)]
    changed = [a for a in out_augs if a.get("descChanged") and mm(a["descChanged"]) == mm(version) and a not in new]
    return new, changed


PATCH_ARROW = re.compile(r"([\d.]+)\s*(?:%|초)?\s*(?:⇒|→|->)\s*([\d.]+)")


def patch_mismatches(out_augs, aug_spells):
    """패치 노트의 '이전 ⇒ 이후' 숫자가 게임 데이터(설명 또는 값 목록)에 있는지 확인. 없으면 게임 파일 반영 전이거나 다른 수치."""
    bad = []
    for a in out_augs:
        plain = re.sub(r"<[^>]+>", "", a["desc"])
        nums = {float(x) for x in re.findall(r"\d+(?:\.\d+)?", plain)}
        for dv in (aug_spells.get(a["id"]) or {}).get("DataValues") or []:
            for x in dv.get("values") or []:
                nums.update({round(x, 4), round(x * 100, 4)})
        for line in a.get("patch") or []:
            for _, after in PATCH_ARROW.findall(line):
                try:
                    v = float(after)
                except ValueError:
                    continue
                if v not in nums:
                    bad.append((a, line))
    return bad


def review_report(version, new, changed, mismatch, rule_warn, auto_rules, no_number, hand_rules=()):
    """사람이 확인할 것 목록 (Markdown). 갱신 워크플로가 GitHub 이슈로 올린다."""
    out = [f"# 확인 필요 — 패치 {version}", "",
           "매일 갱신 때 자동으로 만든 목록이에요. 확인 후 rules.json 을 고치거나 이 이슈를 닫아 주세요.", ""]
    def sec(title, rows):
        if rows:
            out.extend([f"## {title} ({len(rows)})", *[f"- {r}" for r in rows], ""])
    sec("✨ 새 증강", [f"**{a['name']}** (`{a['id']}`) — "
                     + ("자동 추정 능력치 있음" if a["id"] in auto_rules else "숫자 없음: 규칙 필요" if a["id"] in no_number else "효과 자동 추출됨")
                     for a in new])
    sec("✏️ 설명이 바뀐 증강 (숫자 말고 문장이 바뀜)", [f"**{a['name']}** (`{a['id']}`)" + (" — 직접 만든 규칙 있음, 다시 확인" if a["id"] in hand_rules else "")
                                                for a in changed])
    label = {"applied": "📄 패치 노트 값 임시 적용", "stale": "⚠️ 게임 데이터가 아직 이전 값 (자리 못 짚음, 계산은 이전 값)",
             "differ": "⚠️ 이전·이후 값 모두 데이터에 없음"}
    sec("패치 노트 대조", [f"{label[r['status']]} — **{x['name']}**: {r['line']}" + (f" ({r['note']})" if r.get("note") else "")
                       for x, r in mismatch])
    sec("🔧 규칙 수치 경고 (값 이름이 사라짐 등)", rule_warn)
    sec("🌐 이번에 못 받은 위키 자료 (그 부분 없이 만듦)", WIKI_FAILED)
    return "\n".join(out) if len(out) > 4 else ""


ICON_MAX = {"augment": 128}  # 증강 아이콘은 256px 이지만 화면에서는 64px 이하로만 보인다 (선명도용 2배)


def optimize_icons(icons_dir):
    """아이콘 PNG → WebP (약 7.4MB → 2MB). Pillow 가 있을 때만 (웹 사이트·저장소용 데이터).

    프로그램의 '데이터 갱신'에는 Pillow 를 넣지 않아서 PNG 그대로 쓴다 → data.json iconExt 로 화면이 따라간다.
    """
    try:
        from PIL import Image
    except ImportError:
        return "png" if any(icons_dir.rglob("*.png")) or not any(icons_dir.rglob("*.webp")) else "webp"
    for png in list(icons_dir.rglob("*.png")):
        try:
            im = Image.open(png)
            im.load()
            limit = ICON_MAX.get(png.parent.name)
            if limit and max(im.size) > limit:
                im = im.resize((limit, limit), Image.LANCZOS)
            im.save(png.with_suffix(".webp"), "WEBP", quality=85, method=6)
            png.unlink()
        except Exception as e:
            print("  아이콘 변환 실패:", png.name, e)
            return "png"
    return "webp"


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
    champs = {k: v for k, v in champs.items() if SAFE_ID.fullmatch(k)}  # id 가 캐시 파일 경로에 쓰이므로 먼저 거른다
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
    wiki_aug_text = fetch_wiki(f"{WIKI}/Module:MayhemAugmentData/data?action=raw", "wiki_mayhemaugments.lua", empty="")
    wiki_items = fetch_wiki(f"{WIKI}/Module:ItemData/data?action=raw", "wiki_itemdata.lua", parse_lua_table, {})
    wiki_mayhem_page = fetch_wiki(f"{WIKI}/ARAM:_Mayhem?action=raw", "wiki_mayhem_page.txt", empty="")
    pn_url, pn_patch = patch_notes_url(version)
    try:
        patch = parse_mayhem_patch(fetch(pn_url, f"patchnotes_{pn_patch}.html").decode("utf-8"))
    except Exception as e:
        print("  패치 노트 받기 실패:", e)
        patch = None
    if patch:
        patch.update({"patch": pn_patch, "url": pn_url})
    patch_lines = {}  # 이름(한글) → 변경 줄
    for g in (patch or {}).get("groups", []):
        for en in g["entries"]:
            if en["name"]:
                patch_lines[en["name"]] = en["lines"]
    wiki = fetch_wiki(f"{WIKI}/Module:ChampionData/data?action=raw", "wiki_championdata.lua", parse_lua_table, {})
    try:
        wiki_augs = wiki_mayhem_augments(wiki_aug_text, wiki) if wiki_aug_text else {}
    except Exception as e:
        print("  [위키 증강 해석 실패 → 없이 진행]", e)
        WIKI_FAILED.append("wiki_mayhemaugments.lua")
        wiki_augs = {}
    item_wiki = {str(v.get("id")): k for k, v in wiki_items.items() if isinstance(v, dict) and v.get("id")}
    item_limit = {str(v.get("id")): v["itemlimit"] for v in wiki_items.values()
                  if isinstance(v, dict) and v.get("itemlimit") and v.get("id")}

    # --- 아이템: 칼바람 증강 모드가 실제로 쓰는 목록만
    mode = map12[MODE_PATH]
    mode_ids = set()
    for list_key in mode["itemLists"]:
        for ref in map12[list_key]["mItems"]:
            mode_ids.add(ref.split("/")[-1])
    out_items, missing, unknown_stats = [], [], []
    mode_ids = {i for i in mode_ids if i.isdigit()}  # 아이템 id 는 숫자만 (화면 속성·파일 이름에 쓰임)
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
            "sell": it["gold"].get("sell"),  # 팔 때 돌려받는 골드 (교체 추천의 실제 비용)
            "tags": tags, "stats": stats, "desc": html, "effects": effects,
            "limit": item_limit.get(iid),  # 같은 계열 고유 효과(주문 검·히드라 …)는 하나만
            "refs": [r for r in [
                {"label": "게임 데이터", "url": f"{CDRAGON}/game/items.cdtb.bin.json"},
                {"label": "위키", "url": wiki_page(item_wiki[iid])} if iid in item_wiki else None,
            ] if r],
            "passiveHtml": passive_html, "modeValues": mode_used,
            "from": [c for c in it.get("from", []) if c.isdigit()],  # 하위 부품 (지금 골드로 살 것 안내)
        })

    # 부품: 완성 아이템의 하위 부품 전체 (이름·가격·그 아래 부품). 게임 중 '지금 골드로 살 것' 계산에 쓴다
    parts = {}

    def add_part(pid):
        p = items.get(pid)
        if not p or pid in parts or not pid.isdigit():
            return
        parts[pid] = {"name": p["name"], "gold": p["gold"]["total"], "from": [c for c in p.get("from", []) if c.isdigit()]}
        for c in parts[pid]["from"]:
            add_part(c)
    for i in out_items:
        for c in i["from"]:
            add_part(c)

    # --- 증강
    kiwi_names = next(g["augmentList"] for g in aug_lists if g["modeName"] == MODE)
    kiwi_names = [p.split("/")[-1] for p in kiwi_names]
    aug_data = {v["AugmentNameId"]: v for v in kiwi.values()
                if isinstance(v, dict) and v.get("__type") == "AugmentData"}
    rarity_name = {0: "실버", 1: "골드", 2: "프리즘"}
    out_augs, unresolved_total, disabled, disabled_ids, aug_spells = [], 0, [], set(), {}
    wiki_matched = 0
    wiki_desc = {}  # 증강 id → 위키 영어 설명 (자동 추정 대조용)
    for name in kiwi_names:
        if not SAFE_ID.fullmatch(name):  # 화면 속성·아이콘 파일 이름에 쓰이므로 영문·숫자·밑줄만
            print("  [건너뜀] 이상한 증강 id:", repr(name)[:60])
            continue
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
        wiki_desc[name] = wa.get("description", "")
        spell = kiwi.get(a.get("RootSpell", ""), {}).get("mSpell", {})
        raw = expand_templates(strings.get((a.get("AugmentTooltipTra") or "").lower()) or "", strings)
        # 툴팁이 다른 문구를 참조하는데 그 문구가 비어 있으면(가속 추구·위력 추구·압력솥·초장거리 저격) 기본 설명을 쓴다
        body = re.sub(r"<flavorText>.*?</flavorText>|<[^>]+>|\s+", "", raw, flags=re.S | re.I)
        if len(body) < 15:
            raw = expand_templates(strings.get((a.get("DescriptionTra") or "").lower()) or "", strings) or raw
        text, unresolved = resolve_values(raw, spell)
        ko_name = strings.get((a.get("NameTra") or "").lower(), "")
        # 패치 노트 대조: 게임 데이터가 아직 이전 값이고 자리가 확실하면 패치 노트 값으로 다시 계산
        p_check, patched = check_augment(spell, text, patch_lines.get(ko_name))
        if patched:
            spell = patched
            text, unresolved = resolve_values(raw, spell)
        aug_spells[name] = spell
        unresolved_total += len(unresolved)
        html, plain, names = to_html(text)
        icon = (a.get("AugmentLargeIconPath") or "").replace(".tex", ".png").lower()
        effects = augment_effects(raw, spell, plain)
        out_augs.append({
            "id": name,
            "effects": effects,
            "enName": en_name,
            "patch": patch_lines.get(ko_name),  # 이번 패치 변경
            "patchCheck": p_check or None,  # 줄마다 게임 데이터 대조 결과
            "refs": aug_refs(en_name, wa, pn_url if strings.get((a.get("NameTra") or "").lower(), "") in patch_lines else None),
            "eligible": wa.get("eligible"),  # 위키에 적용 스킬 목록이 있으면 그 챔피언들만 발동
            "name": strings.get((a.get("NameTra") or "").lower(), name),
            "rarity": a.get("rarity", 0),
            "rarityName": rarity_name.get(a.get("rarity", 0), "?"),
            "desc": html,
            "tags": tag_text(plain, names),
            "category": augment_category(plain),
            "unresolved": len(unresolved),
            "_icon": icon,
            "_raw": raw,
        })

    # --- 챔피언 (상세 데이터에서 스킬 특성 추출)
    (CACHE / "champion").mkdir(exist_ok=True)

    def detail(cid):
        for attempt in range(3):  # 챔피언 173명을 동시에 받아서 가끔 끊긴다
            try:
                return fetch_json(f"{DDRAGON}/cdn/{version}/data/ko_KR/champion/{cid}.json",
                                  f"champion/{cid}.json")["data"][cid]
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)

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

    (CACHE / "tactical").mkdir(exist_ok=True)

    def tactical(cid):
        """라이엇이 정한 챔피언 성향 (주 피해 유형 damageType, 평타↔스킬 style). 못 받으면 {} → 피해 유형 거르기 안 함"""
        try:
            return fetch_json(f"{CDRAGON}/plugins/rcp-be-lol-game-data/global/default/v1/champions/{champs[cid]['key']}.json",
                              f"tactical/{cid}.json").get("tacticalInfo") or {}
        except Exception:
            return {}

    print("챔피언 상세·스킬 데이터 받는 중...")
    with ThreadPoolExecutor(8) as ex:
        details = dict(zip(champs, ex.map(detail, champs)))
        bins = dict(zip(champs, ex.map(champ_bin, champs)))
        tacticals = dict(zip(champs, ex.map(tactical, champs)))
    out_champs, spell_unresolved, no_wiki, passive_found = [], 0, [], 0
    mode_global = mayhem_global_stats(wiki_mayhem_page)  # 모든 챔피언 공통 모드 능력치 (체력·공속 상한·치명타 초과분)
    print(f"  모드 공통 능력치 (위키): {mode_global or '못 읽음'}")
    growth_fixed = []  # Data Dragon 성장치가 0 이라 위키 값으로 채운 칸
    mayhem_mods = mayhem_champion_overrides(wiki_mayhem_page, wiki)
    mayhem_cd = mayhem_cooldowns(wiki_mayhem_page, wiki)
    cd_applied = []
    for cid, c in champs.items():
        if not SAFE_ID.fullmatch(cid) or not str(c.get("key", "")).isdigit():
            print("  [건너뜀] 이상한 챔피언 id:", repr(cid)[:60])
            continue
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
            line = mayhem_cd.get(cid, {}).get(sp["slot"])
            vals = _wiki_ranks(line, sp["ranks"]) if line else None
            if vals:  # 증강 모드 전용 재사용 대기시간 (위키)
                sp["cooldownBase"], sp["cooldown"] = sp["cooldown"], vals
                cd_applied.append(f"{c['name']} {sp['slot']} {sp['cooldownBase'][0]:g}→{vals[0]:g}")
        aram, as_ratio = wiki_extras(wiki, c["name"], cid)
        c["stats"], fixed = wiki_growth_fix(wiki, c["name"], cid, c["stats"])
        growth_fixed.extend(fixed)
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
            # 주 피해 유형 (기본 빌드에서 반대 유형 아이템을 빼는 기준). kPhysical/kMagic/kMixed → physical/magic/mixed
            "damageType": DAMAGE_TYPE.get(tacticals[cid].get("damageType")),
            "style": tacticals[cid].get("style"),
            "features": features + (["melee"] if c["stats"]["attackrange"] <= 250 else ["ranged"]),
            "stats": c["stats"], "asRatio": as_ratio or c["stats"]["attackspeed"], "aram": aram,
            "aramSource": aram_source,
            # 모드 공통 능력치: 근접·원거리별 기본 체력 [1레벨, 18레벨], 공속 상한, 치명타 초과분 → 적응형
            "modeStats": {k: v for k, v in {
                "hp": (mode_global.get("hp") or {}).get("melee" if c["stats"]["attackrange"] <= 250 else "ranged"),
                "asCap": mode_global.get("asCap"), "critAdaptive": mode_global.get("critAdaptive")}.items() if v} or None,
            "patch": patch_lines.get(c["name"]),
            "refs": [r for r in [
                {"label": "스킬 수치 (게임 데이터)", "url": f"{CDRAGON}/game/data/characters/{cid.lower()}/{cid.lower()}.bin.json"},
                {"label": "기본 능력치 (Data Dragon)", "url": f"{DDRAGON}/cdn/{version}/data/ko_KR/champion/{cid}.json"},
                {"label": "주 피해 유형 (게임 데이터)", "url": f"{CDRAGON}/plugins/rcp-be-lol-game-data/global/default/v1/champions/{c['key']}.json"},
                {"label": "칼바람 보정 (위키)", "url": f"{WIKI}/{urllib.parse.quote(c['id'])}/ARAM" if False else f"{WIKI}/ARAM:_Mayhem#List_of_mode_overrides"} if aram else None,
                {"label": "이번 패치 노트", "url": pn_url + "#patch-aram:-mayhem"} if patch_lines.get(c["name"]) else None,
            ] if r],
            "passive": {"name": d["passive"]["name"], "desc": p_html or to_html(d["passive"]["description"])[0],
                        "effects": p_effects,
                        "cc": spell_cc_seconds(re.sub(r"<[^>]+>", "", p_html or d["passive"]["description"]))},
            "spells": spells,
        })
    for c in out_champs:  # 패치 노트 대조 (모드 보정·재사용 대기시간은 확실하면 패치 노트 값으로)
        c["patchCheck"] = check_champion(c, c["patch"]) if c["patch"] else None
    # 챔피언 스킬·패시브의 '중첩당' 계수는 챔피언 중첩(CSTACKS)으로 (증강의 중첩과 섞이지 않게)
    def to_cstacks(o):
        if isinstance(o, dict):
            for key in ("ratios", "r"):
                if isinstance(o.get(key), dict) and "STACKS" in o[key]:
                    o[key]["CSTACKS"] = o[key].pop("STACKS")
            for v in o.values():
                to_cstacks(v)
        elif isinstance(o, list):
            for v in o:
                to_cstacks(v)
    rules_champs = json.loads((ROOT / "rules.json").read_text(encoding="utf-8")).get("champions", {})
    stack_warn = []
    for c in out_champs:
        to_cstacks(c["spells"])
        to_cstacks(c["passive"]["effects"])
        rc = rules_champs.get(c["id"])
        if rc:  # 중첩형 패시브·스킬: 게임 중반 평균 중첩
            c["stackStats"] = rc.get("stats") or {}
            c["stacks"] = rc.get("stacks", 0)
            text = c["passive"]["desc"] + " ".join(sp["tooltip"] for sp in c["spells"])
            c["stackBasis"] = rule_basis(rc, c["stackStats"], text, stack_warn, c["name"])
    out_champs.sort(key=lambda c: c["name"])

    # --- 아이콘
    jobs = [(f"{DDRAGON}/cdn/{version}/img/champion/{c['id']}.png", ICONS / "champion" / f"{c['id']}.png")
            for c in out_champs]
    jobs += [(f"{DDRAGON}/cdn/{version}/img/item/{i}.png", ICONS / "item" / f"{i}.png")
             for i in [x["id"] for x in out_items] + list(parts)]
    jobs += [(f"{CDRAGON}/game/{a['_icon']}", ICONS / "augment" / f"{a['id']}.png")
             for a in out_augs if a["_icon"]]
    jobs = [(u, p) for u, p in jobs if REFRESH or not (p.exists() or p.with_suffix(".webp").exists())]
    failed = []

    def dl(job):
        url, path = job
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                path.write_bytes(r.read())
        except Exception:
            failed.append(url)

    print(f"아이콘 {len(jobs)}개 받는 중...")
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(dl, jobs))
    icon_ext = optimize_icons(ICONS)
    # PNG 로 받는 경우(Pillow 없음)에도 PNG 받기에 실패해 WebP 만 남은 아이콘은 WebP 로 보여 준다
    webp_only = sorted(f"{p.parent.name}/{p.stem}" for p in ICONS.rglob("*.webp")
                       if not p.with_suffix(".png").exists()) if icon_ext == "png" else []
    for a in out_augs:
        a["icon"] = any((ICONS / "augment" / f"{a['id']}.{x}").exists() for x in ("png", "webp"))
        del a["_icon"]

    rules = json.loads((ROOT / "rules.json").read_text(encoding="utf-8"))
    aug_ids = {a["id"] for a in out_augs}
    item_names = {i["name"] for i in out_items}
    # 사용 효과가 있는 완성 아이템 (아이템 가속 증강 → items). 패치로 아이템이 바뀌어도 빌드할 때마다 다시 채운다
    active_items = [i["name"] for i in out_items if i["kind"] == "legendary" and "t-active" in (i["desc"] or "")]
    for r in rules["augments"].values():
        if r.get("itemsActive"):
            r["items"] = active_items
    for aid, r in rules["augments"].items():
        if aid not in aug_ids and aid not in disabled_ids:  # 비활성 증강 규칙은 다시 켜질 때를 위해 남겨 둔다
            print("  [규칙] 없는 증강:", aid)
        for n in r.get("items", []):
            if n not in item_names:
                print("  [규칙] 없는 아이템:", aid, n)
    rule_warn = resolve_rule_stats(rules, aug_spells)
    rule_effects(rules, aug_spells, out_augs, rule_warn)
    auto_rules = {}  # rules.json 에 능력치 규칙이 없는 증강 → 설명에서 자동 추정
    for a in out_augs:
        if (rules["augments"].get(a["id"]) or {}).get("stats"):
            continue
        st, used = auto_stats(a["desc"])
        if st:
            r = rules["augments"].setdefault(a["id"], {})
            r.update({"statsResolved": st, "auto": True, "model": "자동 추정 (검토 필요): " + " / ".join(used)})
            r["wikiCheck"] = wiki_check(st, wiki_desc.get(a["id"], ""))
            auto_rules[a["id"]] = st
            bad = [k for k, ok in r["wikiCheck"].items() if ok is False]
            if bad:
                rule_warn.append(f"{a['name']}: 자동 추정 {', '.join(bad)} 값이 위키 설명과 다름 → 확인 필요")
    aug_by_id = {a["id"]: a for a in out_augs}
    for aid, r in rules["augments"].items():  # 규칙 수치 근거 (화면 '근거')
        if aid in aug_by_id and (r.get("stats") or r.get("statsResolved")):
            r["basisView"] = rule_basis(r, r.get("statsResolved") or {}, aug_by_id[aid]["desc"], rule_warn, aug_by_id[aid]["name"])
    for item in out_items:  # 아이템 고유 효과 능력치
        ir = rules.get("items", {}).get(item["name"])
        if not ir:
            continue
        plain = re.sub(r"<[^>]+>", "", item["desc"])
        if ir.get("check") and ir["check"] not in plain:
            rule_warn.append(f"아이템 {item['name']}: 설명에 '{ir['check']}' 없음 (패치로 수치 변경?) → 미적용")
            continue
        item["passiveStats"] = ir["stats"]
        item["basisView"] = rule_basis(ir, ir["stats"], (item.get("passiveHtml") or "") + item["desc"], rule_warn, item["name"])
    try:
        prev = json.loads((WEB / "data.json").read_text(encoding="utf-8"))
    except Exception:
        prev = None
    new_augs, changed_augs = augment_history(prev, out_augs, version)
    mismatch = [(x, r) for x in out_augs + out_champs for r in (x.get("patchCheck") or [])
                if r["status"] in ("applied", "stale", "differ")]
    no_number = {a["id"] for a in out_augs
                 if not a["effects"] and not (rules["augments"].get(a["id"]) or {}).get("statsResolved")}
    report = review_report(version, new_augs, changed_augs, mismatch, rule_warn, auto_rules, no_number,
                           {k for k, v in rules["augments"].items() if not v.get("auto")})
    (WEB / "review.md").write_text(report, encoding="utf-8")
    sources = build_sources(FETCHED, pn_url if patch else None)
    data = {"version": version, "dataVersion": cd_version, "sources": sources, "patchNotes": patch,
            "builtAt": time.strftime("%Y-%m-%d %H:%M"), "mode": MODE, "iconExt": icon_ext, "iconWebpOnly": webp_only,
            "format": DATA_FORMAT, "tags": TAGS, "rules": rules,
            "disabled": disabled,
            "champions": out_champs, "items": out_items, "parts": parts, "augments": out_augs}
    tmp = WEB / "data.json.tmp"  # 쓰다 꺼져도 반쯤 쓰인 data.json 이 남지 않게
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, WEB / "data.json")

    n_leg = sum(i["kind"] == "legendary" for i in out_items)
    n_boots = sum(i["kind"] == "boots" for i in out_items)
    no_tag = sum(1 for a in out_augs if not a["tags"])
    with_q = sum(1 for a in out_augs if a["unresolved"])
    print(f"챔피언 {len(out_champs)} / 아이템 전설 {n_leg} + 신발 {n_boots} / 증강 {len(out_augs)}")
    print(f"  비활성(Enabled=False)이라 제외한 증강 {len(disabled)}개: {', '.join(disabled)}")
    print(f"  스킬 수치 못 채운 자리 {spell_unresolved}개 (스킬 {sum(len(c['spells']) for c in out_champs)}개 중)")
    print(f"  위키 데이터 없는 챔피언 {len(no_wiki)}명: {', '.join(no_wiki)}")
    print(f"  Data Dragon 성장치 0 → 위키 값: {len(growth_fixed)}칸 ({', '.join(sorted(set(growth_fixed)))})")
    print(f"  위키 증강 짝짓기 {wiki_matched}/{len(kiwi_names)}, 적용 스킬 목록 있는 증강 "
          f"{sum(1 for a in out_augs if a.get('eligible'))}개, 증강 모드 전용 보정 챔피언 {len(mayhem_mods)}명, "
          f"같은 계열 표시 아이템 {sum(1 for i in out_items if i.get('limit'))}개")
    print(f"  증강 모드 재사용 대기시간 조정 {len(cd_applied)}개: {', '.join(cd_applied)}")
    print(f"  패시브 수치 설명 찾음 {passive_found}명, 패시브 효과(피해·보호막·회복) 있는 챔피언 "
          f"{sum(1 for c in out_champs if c['passive']['effects'])}명")
    print(f"  고유 효과 수치 뽑은 아이템 {sum(1 for i in out_items if i['effects'])}개 / {len(out_items)}개, "
          f"칼바람 전용 수치 적용 {sum(1 for i in out_items if i['modeValues'])}개")
    print(f"  아이템 능력치 줄 해석 실패 {len(unknown_stats)}개: {unknown_stats[:5]}")
    for w in rule_warn:
        print("  [규칙 수치]", w)
    print(f"  자동 추정 능력치 {len(auto_rules)}개: {', '.join(a['name'] for a in out_augs if a['id'] in auto_rules)}")
    print(f"  새 증강 {len(new_augs)}개, 설명 바뀐 증강 {len(changed_augs)}개, 패치 노트 숫자 불일치 {len(mismatch)}건"
          + (" → web/review.md" if report else ""))
    from collections import Counter
    pc = Counter(r["status"] for x in out_augs + out_champs for r in (x.get("patchCheck") or []))
    print(f"  패치 노트 대조: {dict(pc)}")
    print(f"  증강 중 수치 일부 못 채움(?) {with_q}개, 효과 태그 분류 안 됨 {no_tag}개")
    print(f"  모드 목록에 있지만 데이터 없음 {len(missing)}개, 아이콘 실패 {len(failed)}개")
    for u in failed[:5]:
        print("   실패:", u)


if __name__ == "__main__":
    main()
