"""챔피언 스킬 수치·기본 능력치·칼바람 밸런스 보정 추출.

- 스킬 수치: CommunityDragon 챔피언 bin (DataValues, mSpellCalculations, 재사용 대기시간)
- 스킬 설명: Data Dragon 한글 tooltip 의 {{ 자리표시자 }} 를 위 수치로 채움
- 효과(피해·보호막·회복): 자리표시자를 감싼 마크업 태그(<magicDamage> 등)로 종류를 판정
- 칼바람 밸런스: 리그 오브 레전드 위키 Module:ChampionData (CC BY-SA)
"""
import re

from lol_calc import PLACEHOLDER, Calc, expr_json, fmt_expr_ranks

SLOTS = "QWER"
# 자리표시자를 감싼 태그 → 효과 종류
EFFECT_TAGS = {
    "magicdamage": "magic", "physicaldamage": "physical", "truedamage": "true",
    "shield": "shield", "healing": "heal",
}


# 챔피언 상대 피해·회복이 아닌 값: 몬스터·미니언 전용, 소환물 체력
NOT_CHAMPION = re.compile(r"monster|minion|ghoul|turret|tower|structure|jungle|nonchamp", re.I)
# 자리표시자 바로 뒤가 '의 공격력을 얻습니다'처럼 능력치 획득이면 피해가 아니다 (베인 R 등)
STAT_GRANT = re.compile(r"^\s*(의\s*)?(추가\s*)?(공격력|주문력|방어력|마법 저항력|공격 속도|이동 속도)(을|를)?\s*(얻|증가|획득)")


def skip_effect(kind, name, after):
    if NOT_CHAMPION.search(name):
        return True
    if kind == "heal" and name.lower().endswith("health"):
        return True
    return kind in ("magic", "physical", "true", "adaptive") and bool(STAT_GRANT.search(after))


def find_spell(bin_json, spell_id):
    suffix = "/" + spell_id.lower()
    for k, v in bin_json.items():
        if isinstance(v, dict) and v.get("__type") == "SpellObject" and k.lower().endswith(suffix):
            return v.get("mSpell") or {}
    return {}


# 태그 없이 문장으로만 쓴 효과: 자리표시자 바로 뒤 문구로 판정 (증강 설명에 많다)
INFER_AFTER = [
    ("adaptive", re.compile(r"^\s*의\s*적응형\s*피해")),
    ("magic", re.compile(r"^\s*의\s*마법\s*피해")),
    ("physical", re.compile(r"^\s*의\s*물리\s*피해")),
    ("true", re.compile(r"^\s*의\s*고정\s*피해")),
    ("adaptive", re.compile(r"^\s*의\s*(추가\s*)?피해")),
    ("shield", re.compile(r"^\s*의\s*(피해를 흡수하는\s*)?보호막")),
    ("heal", re.compile(r"^\s*(의|만큼)\s*체력을\s*회복")),
]


# '%에 해당하는 마법 피해', '%만큼 추가 물리 피해' 같은 상대 체력 % 피해의 종류
PCT_KIND = [
    ("magic", re.compile(r"마법\s*피해")), ("physical", re.compile(r"물리\s*피해")),
    ("true", re.compile(r"고정\s*피해")), ("adaptive", re.compile(r"피해")),
]


def sentence_at(text, idx):
    """자리표시자가 들어 있는 문장(마침표·줄바꿈 사이)의 평문"""
    start = max(text.rfind(".", 0, idx), text.rfind("<br", 0, idx), text.rfind("</li>", 0, idx))
    ends = [e for e in (text.find(".", idx), text.find("<br", idx), text.find("</li>", idx)) if e >= 0]
    end = min(ends) if ends else len(text)
    return re.sub(r"<[^>]+>", "", text[start + 1:end])  # {{ 자리표시자 }}는 남겨서 나중에 숫자로 채운다


def resolve_tooltip(tooltip, calc, ranks, other_spell=None, infer=False):
    """자리표시자를 랭크별 수치로 채우고, 효과 목록을 뽑는다."""
    effects, unresolved = [], []
    out = []
    stack = []
    pos = 0
    for m in re.finditer(r"<(/?)([A-Za-z0-9]+)[^>]*>|" + PLACEHOLDER.pattern, tooltip):
        out.append(tooltip[pos:m.start()])
        pos = m.end()
        if m.group(2):  # 태그
            name = m.group(2).lower()
            if m.group(1):
                if name in stack:
                    while stack and stack.pop() != name:
                        pass
            elif not m.group(0).endswith("/>"):
                stack.append(name)
            out.append(m.group(0))
            continue
        expr_text = m.group(3)
        mm = re.fullmatch(r"([A-Za-z_][\w.:]*?)(?:\s*\*\s*(-?[\d.]+))?", expr_text)
        if not mm:
            unresolved.append(expr_text)
            out.append("?")
            continue
        name, mul = mm.group(1), float(mm.group(2)) if mm.group(2) else 1.0
        if name.lower() in ("spellmodifierdescriptionappend", "spellmodifiertitleappend"):
            continue
        exprs, pct = [], False
        target, key = calc, name
        ref = re.fullmatch(r"spell\.(\w+):(\w+)", name, re.I)  # 다른 스킬 수치 참조
        if ref and other_spell:
            target, key = Calc(other_spell(ref.group(1))), ref.group(2)
        if re.fullmatch(r"f\d+(\.\d+)?", name, re.I):  # 게임 중 계산되는 값
            out.append("(변동)")
            continue
        for r in range(1, ranks + 1):
            e, p = target.resolve(key, r)
            if e is None:
                exprs = None
                break
            pct = pct or p
            exprs.append(e.scale(mul))
        if exprs is None:
            unresolved.append(name)
            out.append("?")
            continue
        out.append(fmt_expr_ranks(exprs, pct and mul == 1))
        kind = next((EFFECT_TAGS[t] for t in reversed(stack) if t in EFFECT_TAGS), None)
        followed_by_pct = tooltip[pos:pos + 1] == "%"
        after = re.sub(r"<[^>]+>", "", tooltip[pos:pos + 40])
        before = re.sub(r"<[^>]+>", "", tooltip[max(0, m.start() - 40):m.start()])
        ctx = sentence_at(tooltip, m.start())
        # 상대 체력 % 피해: '최대 체력의 2%에 해당하는 마법 피해' → 계수 TGT_MAXHP 0.02
        if followed_by_pct and infer:
            hp = re.search(r"(최대|현재|잃은)\s*체력의\s*$", before.strip() + "")
            # '…%에 해당하는 피해를 입히면' 은 발동 조건이지 피해가 아니다 (폭풍 쇄도 등)
            is_condition = re.search(r"^%?[^.]{0,20}(입히면|입힐 경우|이상|미만|아래)", after)
            if hp and not is_condition and all(e.is_number() for e in exprs):
                hkind = kind or next((k for k, pat in PCT_KIND if pat.search(after)), None)
                if hkind:
                    # 회복·보호막이거나 '자신의 최대 체력'이면 내 체력 기준
                    own = hkind in ("heal", "shield") or re.search(r"(자신|내|본인)의?\s*(최대|현재|잃은)\s*체력의\s*$", before.strip())
                    if own and hp.group(1) == "잃은":
                        continue
                    key = ({"최대": "HP", "현재": "HP"} if own else
                           {"최대": "TGT_MAXHP", "현재": "TGT_CURHP", "잃은": "TGT_MISSINGHP"})[hp.group(1)]
                    effects.append({"kind": hkind, "name": name, "base": [0] * len(exprs),
                                    "ratios": {key: [round(e.base / 100, 5) for e in exprs]}, "_ctx": ctx})
                    continue
        if kind is None and infer:
            kind = next((k for k, pat in INFER_AFTER if pat.search(after)), None)
        if kind and not pct and mul == 1 and not followed_by_pct and not skip_effect(kind, name, after):
            effects.append({"kind": kind, "name": name, **expr_json(exprs), "_ctx": ctx})
    out.append(tooltip[pos:])
    return "".join(out), effects, unresolved


# 증강 효과 발동 빈도 (설명 문구에서 판정, 위에서부터 먼저 맞는 것)
TRIGGERS = [
    ("attacks", re.compile(r"(\d+)번째 기본 공격마다|(\d+)회 기본 공격")),
    # 충전 상태 공격(스태틱의 단검 등): 평타 약 8회에 1번으로 가정
    ("attacks", re.compile(r"충전 상태로 공격")),
    # 스킬 뒤 기본 공격 강화(주문 검), 스킬 표식 소모, 스킬 공격 화상 → 스킬 1회당 1번
    ("cast", re.compile(r"스킬을 사용한 후|스킬을 사용하고 나면|스킬 사용 후|표식을 (소모|터뜨)|스킬 공격이|스킬로 적|스킬이 적")),
    ("time", re.compile(r"(\d+(?:\.\d+)?)초마다")),
    ("time", re.compile(r"재사용 대기시간[:\s]*(\d+(?:\.\d+)?)초")),
    # 조건부 1회: 처치·사망·체력 기준·'다음' 기본 공격 (매 평타·매 스킬로 세면 과대평가)
    ("once", re.compile(r"처치|사망 시|체력이 \d+% ?(아래|이하|가 되)|다음 기본 공격|정수|충돌")),
    ("onhit", re.compile(r"기본 공격 시|적중 시|기본 공격으로 피해|기본 공격이")),
    ("cast", re.compile(r"스킬을 사용할 때마다|스킬[^.]{0,12}(사용 시|적중 시|피해를 입히면|맞히면)")),
]
TICKS = re.compile(r"(\d+(?:\.\d+)?)초\s*(동안|에 걸쳐)\s*(매초|초당)")


def find_trigger(text):
    for kind, pat in TRIGGERS:
        m = pat.search(text)
        if m:
            t = {"type": kind}
            if kind == "attacks":
                t["n"] = int(next((g for g in m.groups() if g), 8))
            elif kind == "time":
                t["sec"] = float(m.group(1))
            return t
    return None


def text_effects(raw_tooltip, spell, plain_text):
    """설명(@이름@ 자리표시자)에서 피해·보호막·회복 효과와 발동 빈도를 뽑는다. 증강·아이템·패시브 공용.

    발동 빈도는 효과가 들어 있는 문장에서 먼저 찾고, 없으면 설명 전체에서 찾는다.
    'N초 동안 매초' 피해는 N배(ticks)로 센다.
    """
    text = re.sub(r"@([^@]+)@", lambda m: "{{ " + m.group(1) + " }}", raw_tooltip)
    calc = Calc(spell)
    _, effects, _ = resolve_tooltip(text, calc, 1, infer=True)
    fallback = find_trigger(plain_text) or {"type": "once"}

    def numbers(ctx):  # 문장 안 자리표시자를 숫자로 채워야 'N초마다' 같은 문구를 읽을 수 있다
        def sub(m):
            e, _ = calc.resolve(m.group(1), 1)
            k = float(m.group(2)) if m.group(2) else 1
            return f"{e.base * k:g}" if e is not None and e.is_number() else ""
        return re.sub(r"{{\s*([\w.:]+)(?:\s*\*\s*(-?[\d.]+))?\s*}}", sub, ctx)

    # 최소~최대·1발/최대처럼 같은 종류 값이 여럿이면 'max' 이름의 것만 남긴다 (스킬 수치 처리와 같은 규칙)
    max_kinds = {e["kind"] for e in effects if "max" in e["name"].lower()}
    effects = [e for e in effects if e["kind"] not in max_kinds or "max" in e["name"].lower()]
    for e in effects:
        ctx = numbers(e.pop("_ctx", ""))
        e["trigger"] = find_trigger(ctx) or fallback
        if e["trigger"]["type"] == "cast" and "기본 공격" in ctx:
            e["needsAuto"] = True  # 스킬 후 기본 공격으로 터뜨리는 효과 (주문 검, 럭스 패시브 등)
        if re.search(r"치명타 확률과 (동일한|같은) 확률", ctx):
            e["chance"] = "CRIT"  # 치명타 확률만큼만 발동 (파멸의식 고서 보호막 등)
        tk = TICKS.search(ctx)
        if tk and e["kind"] != "shield":
            e["ticks"] = float(tk.group(1))
        elif "매초" in ctx and e["trigger"]["type"] == "once":
            e["trigger"] = {"type": "time", "sec": 1.0}
    return effects


def augment_effects(raw_tooltip, spell, plain_text):
    return text_effects(raw_tooltip, spell, plain_text)


def item_spell(item_bin, mode_keys=("Kiwi", "ARAM")):
    """아이템 데이터를 계산기가 읽는 스펠 형태로. 모드 전용 수치(칼바람 증강 모드 → 칼바람)를 덮어쓴다."""
    vals = {dv["mName"]: dv.get("mValue", 0) for dv in item_bin.get("mDataValues", []) or []}
    overrides = item_bin.get("DataValuesModeOverride") or {}
    used = None
    for mk in mode_keys:
        if mk in overrides:
            for dv in overrides[mk].get("DataValues", []) or []:
                vals[dv["mName"]] = dv.get("mValue", 0)
            used = mk
            break
    return {
        "DataValues": [{"name": n, "values": [v, v]} for n, v in vals.items()],
        "mSpellCalculations": item_bin.get("mItemCalculations") or {},
        "mEffectAmount": item_bin.get("mEffectAmount") or [],
    }, used


def passive_spell(bin_json, cid):
    """챔피언 패시브 스펠과 설명 키 (Data Dragon 패시브 설명에는 수치가 없어서 게임 파일에서 찾는다)"""
    root = next((v for k, v in bin_json.items() if k.endswith("/CharacterRecords/Root")), {})
    key = root.get("mCharacterPassiveSpell")
    obj = bin_json.get(key, {}) if key else {}
    spell = obj.get("mSpell") or {}
    keys = (spell.get("mClientData") or {}).get("mTooltipData", {}).get("mLocKeys", {})
    return spell, keys.get("keyTooltip") or keys.get("keySummary")


def process_champion(cid, detail, bin_json):
    spells, unresolved_total = [], 0
    for i, sp in enumerate(detail["spells"]):
        spell = find_spell(bin_json, sp["id"])
        ranks = sp.get("maxrank", 5)
        calc = Calc(spell)
        text, effects, unresolved = resolve_tooltip(sp["tooltip"], calc, ranks,
                                                    lambda sid: find_spell(bin_json, sid))
        for e in effects:
            e.pop("_ctx", None)
        unresolved_total += len(unresolved)
        cd = (spell.get("cooldownTime") or [])[1:ranks + 1] or sp.get("cooldown", [])
        cost = (spell.get("mana") or [])[1:ranks + 1] or sp.get("cost", [])
        spells.append({
            "slot": SLOTS[i], "name": sp["name"], "ranks": ranks,
            "cooldown": [round(c, 2) for c in cd], "cost": [round(c, 1) for c in cost],
            "tooltip": text, "effects": effects, "unresolved": unresolved,
        })
    return spells, unresolved_total


# ---------------------------------------------------------------- 위키 Lua 테이블
def parse_lua_table(text):
    """위키 Module:ChampionData/data 의 Lua 테이블을 dict 로. (이 모듈에 필요한 문법만 지원)"""
    # 지원: "문자열", '문자열', [[긴 문자열]], [=[긴 문자열]=], -- 주석, --[[ 긴 주석 ]]
    text = re.sub(r"--\[\[.*?\]\]", "", text, flags=re.S)
    tokens = [m.group(0) for m in re.finditer(r'\[(=*)\[.*?\]\1\]|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|--[^\n]*|[{}\[\]=,;]|-?\d+(?:\.\d+)?(?:[eE]-?\d+)?|[A-Za-z_]\w*', text, flags=re.S)]
    tokens = [t for t in tokens if not t.startswith("--")]
    pos = 0

    def value():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "{":
            return table()
        if tok.startswith('"') or tok.startswith("'"):
            return tok[1:-1].replace('\\"', '"').replace("\\'", "'")
        lb = re.match(r"\[(=*)\[", tok)
        if lb:  # [[ … ]] / [=[ … ]=] 긴 문자열
            n = len(lb.group(1)) + 2
            return tok[n:-n]
        if tok in ("true", "false"):
            return tok == "true"
        if tok == "nil":
            return None
        try:
            return float(tok) if "." in tok or "e" in tok.lower() else int(tok)
        except ValueError:
            return tok

    def table():
        nonlocal pos
        d, arr = {}, []
        while tokens[pos] != "}":
            if tokens[pos] == "[":
                pos += 1
                key = value()
                pos += 2  # ] =
                d[key] = value()
            elif pos + 1 < len(tokens) and tokens[pos + 1] == "=" and re.fullmatch(r"[A-Za-z_]\w*", tokens[pos]):
                key = tokens[pos]
                pos += 2
                d[key] = value()
            else:
                arr.append(value())
            if tokens[pos] in (",", ";"):
                pos += 1
        pos += 1
        return d if d or not arr else arr

    start = tokens.index("{")
    pos = start + 1
    return table()


def wiki_extras(wiki, ddragon_name, ddragon_id):
    """위키 데이터에서 칼바람 보정과 공격 속도 계수를 꺼낸다."""
    entry = None
    for name, e in wiki.items():
        if isinstance(e, dict) and (e.get("apiname") == ddragon_id or name == ddragon_name):
            entry = e
            break
    if not entry:
        return None, None
    stats = entry.get("stats", {})
    aram = stats.get("aram") or {}
    return ({k: v for k, v in aram.items() if isinstance(v, (int, float))} or None,
            stats.get("as_ratio"))
