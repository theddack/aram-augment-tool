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
NOT_CHAMPION = re.compile(r"monster|minion|ghoul|turret|tower|structure|jungle", re.I)
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
        if kind is None and infer:
            kind = next((k for k, pat in INFER_AFTER if pat.search(after)), None)
        if kind and not pct and mul == 1 and not followed_by_pct and not skip_effect(kind, name, after):
            effects.append({"kind": kind, "name": name, **expr_json(exprs)})
    out.append(tooltip[pos:])
    return "".join(out), effects, unresolved


# 증강 효과 발동 빈도 (설명 문구에서 판정, 위에서부터 먼저 맞는 것)
TRIGGERS = [
    ("attacks", re.compile(r"(\d+)번째 기본 공격마다")),
    ("time", re.compile(r"(\d+(?:\.\d+)?)초마다")),
    ("time", re.compile(r"재사용 대기시간[:\s]*(\d+(?:\.\d+)?)초")),
    # 조건부 1회: 처치·사망·체력 기준·'다음' 기본 공격 (매 평타·매 스킬로 세면 과대평가)
    ("once", re.compile(r"처치|사망 시|체력이 \d+% ?(아래|이하|가 되)|다음 기본 공격|정수|충돌")),
    ("onhit", re.compile(r"기본 공격 시|적중 시|기본 공격으로 피해")),
    ("cast", re.compile(r"스킬을 사용할 때마다|스킬[^.]{0,12}(사용 시|적중 시|피해를 입히면|맞히면)")),
]


def augment_effects(raw_tooltip, spell, plain_text):
    """증강 설명(@이름@ 자리표시자)에서 피해·보호막·회복 효과와 발동 빈도를 뽑는다."""
    text = re.sub(r"@([^@]+)@", lambda m: "{{ " + m.group(1) + " }}", raw_tooltip)
    _, effects, _ = resolve_tooltip(text, Calc(spell), 1, infer=True)
    trigger = {"type": "once"}
    for kind, pat in TRIGGERS:
        m = pat.search(plain_text)
        if m:
            trigger = {"type": kind}
            if kind == "attacks":
                trigger["n"] = int(m.group(1))
            elif kind == "time":
                trigger["sec"] = float(m.group(1))
            break
    # 최소~최대 값이 같이 있으면(잃은 체력 비례 등) 최대만 남긴다. 스킬 수치 처리와 같은 규칙
    names = [e["name"].lower() for e in effects]
    if any("max" in n for n in names):
        effects = [e for e in effects if not re.search(r"min|floor", e["name"], re.I)]
    for e in effects:
        e["trigger"] = trigger
    return effects


def process_champion(cid, detail, bin_json):
    spells, unresolved_total = [], 0
    for i, sp in enumerate(detail["spells"]):
        spell = find_spell(bin_json, sp["id"])
        ranks = sp.get("maxrank", 5)
        calc = Calc(spell)
        text, effects, unresolved = resolve_tooltip(sp["tooltip"], calc, ranks,
                                                    lambda sid: find_spell(bin_json, sid))
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
    text = re.sub(r"--[^\n]*", "", text)
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[{}\[\]=,]|-?\d+(?:\.\d+)?(?:[eE]-?\d+)?|[A-Za-z_]\w*', text)
    pos = 0

    def value():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "{":
            return table()
        if tok.startswith('"'):
            return tok[1:-1].replace('\\"', '"')
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
            if tokens[pos] == ",":
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
