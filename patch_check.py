"""패치 노트 '이전 ⇒ 이후' 줄을 게임 데이터·위키 값과 대조하고, 확실할 때만 패치 노트 값을 임시로 쓴다.

판정 (줄마다 하나):
  ok       게임 데이터(또는 위키)에 '이후' 값이 이미 들어 있다 → 그대로 계산
  applied  게임 데이터가 아직 '이전' 값이고, 바꿀 자리가 하나로 확실하다 → 패치 노트 값으로 계산 (📄 표시)
  stale    게임 데이터가 '이전' 값인데 바꿀 자리를 확실히 못 짚는다 → 계산은 그대로, 확인 필요
  differ   이전·이후 둘 다 데이터에 없다 → 확인 필요
  unknown  숫자가 없거나(삭제·신규·문장 변경) 비교할 데이터가 없다

임시 적용은 세 가지만 한다 (값과 자리가 1:1 로 정해지는 경우):
  1. 챔피언 모드 보정 (받는 피해량·가하는 피해량·체력 회복량·보호막 흡수량 …) — 위키 표 값과 비교
  2. 챔피언 스킬 재사용 대기시간 — 게임 파일에 모드 조정이 없어 위키 값과 비교
  3. 증강 수치 — '이전' 값과 같은 DataValue 가 정확히 하나일 때 그 값을 바꾼다
"""
import re

ARROW = "⇒"
NUM = r"\d+(?:\.\d+)?"

# 패치 노트 문구 → 위키 모드 보정 키
MOD_LABELS = {
    "받는 피해량": "dmg_taken", "가하는 피해량": "dmg_dealt", "체력 회복량": "healing",
    "보호막 흡수량": "shielding", "스킬 가속": "ability_haste", "공격 속도": "total_as", "강인함": "tenacity",
}
SLOT_RE = re.compile(r"^(Q|W|E|R)\d?\s*-\s*")
PASSIVE_RE = re.compile(r"^기본 지속 효과\s*-\s*")


def split_line(line):
    """'W - 용기 재사용 대기시간: 22~12초 ⇒ 18~12초' → (slot, label, before, after). 화살표가 없으면 None"""
    if ARROW not in line or ":" not in line:
        return None
    head, rest = line.split(":", 1)
    before, after = (s.strip() for s in rest.split(ARROW, 1))
    head = head.strip()
    slot = None
    m = SLOT_RE.match(head)
    if m:
        slot, head = m.group(1), head[m.end():]
    elif PASSIVE_RE.match(head):
        slot, head = "P", PASSIVE_RE.sub("", head)
    return slot, head.strip(), before, after


def nums(s):
    return [float(x) for x in re.findall(NUM, s)]


def shape(s):
    """'18~12초' → ('range', [18, 12]), '19/18/17초' → ('list', [...]), '45%' → ('one', [45])"""
    n = nums(s)
    if not n:
        return None, []
    if "~" in s and len(n) == 2:
        return "range", n
    if "/" in s:
        return "list", n
    return ("one", n) if len(n) == 1 else ("many", n)


def close(a, b, tol=1e-3):
    return abs(a - b) <= tol * max(1, abs(a), abs(b))


def matches(values, kind, n):
    """데이터의 랭크별 값(values)이 패치 노트 표기(kind, n)와 같은지"""
    if not values:
        return False
    if kind == "one":
        return all(close(v, n[0]) for v in values)
    if kind == "list":
        return len(n) == len(values) and all(close(a, b) for a, b in zip(values, n))
    if kind == "range":
        return close(values[0], n[0]) and close(values[-1], n[1])
    return False


def is_linear(values):
    if len(values) < 3:
        return True
    step = (values[-1] - values[0]) / (len(values) - 1)
    return all(close(v, values[0] + step * i) for i, v in enumerate(values))


def expand(kind, n, count):
    """패치 노트 '이후' 표기를 랭크 수만큼 값으로. 확실하지 않으면 None"""
    if kind == "one":
        return [n[0]] * count
    if kind == "list" and len(n) == count:
        return list(n)
    if kind == "range":  # 처음~끝: 균등 간격일 때만 (호출하는 쪽이 지금 값이 균등인지 확인)
        if count == 1:
            return [n[0]]
        step = (n[1] - n[0]) / (count - 1)
        return [round(n[0] + step * i, 4) for i in range(count)]
    return None


def text_numbers(text):
    return {round(float(x), 4) for x in re.findall(NUM, re.sub(r"<[^>]+>", "", text or ""))}


def presence(text_nums, before, after, label="", text=""):
    """설명 문장 숫자로 판정: 이후 숫자가 다 있으면 ok, 이전 숫자만 다 있으면 stale, 둘 다 없으면 differ.

    표기 차이는 이렇게 맞춘다 (패치 노트 ↔ 게임 설명):
      0.75 ↔ 75% (계수)
      'N초 동안' 합계 ↔ 매초 값 × N
      30/40/50 ↔ 30~50(레벨 비례)
    """
    m = re.search(r"(\d+)초 동안", label + " " + after)
    per = float(m.group(1)) if m else None

    def found(vals):
        if not vals:
            return False
        for cand in (vals, [v / per for v in vals] if per else None):
            if cand and all(round(x, 4) in text_nums or round(x * 100, 4) in text_nums for x in cand):
                return True
        if len(vals) >= 3 and re.search(rf"{vals[0]:g}\s*~\s*{vals[-1]:g}", text):  # 레벨 비례 범위
            return True
        return False

    a, b = nums(after), nums(before)
    if not a:
        return "unknown"
    if found(a):
        return "ok"
    if found(b):
        return "stale"
    return "differ"


def near_numbers(text, label):
    """설명에서 항목 이름(예: '재사용 대기시간') 앞 12자·뒤 30자 안의 숫자. 이름이 없으면 None

    '5초의 재사용 대기시간'(앞) · '재사용 대기시간이 5초'(뒤) 둘 다 있어서 양쪽을 본다.
    """
    words = [label] + ([label.split()[-1]] if " " in label else [])
    for w in words:
        hits = list(re.finditer(re.escape(w), text))
        if hits:
            out = set()
            for m in hits:
                out.update(round(float(x), 4) for x in re.findall(NUM, text[max(0, m.start() - 12):m.start()]))
                out.update(round(float(x), 4) for x in re.findall(NUM, text[m.end():m.end() + 30]))
            return out or None
    return None


def check_champion(champ, lines):
    """챔피언 줄 판정. 모드 보정·재사용 대기시간은 확실하면 champ 를 직접 고친다."""
    out = []
    spells = {sp["slot"]: sp for sp in champ["spells"]}
    for line in lines or []:
        parts = split_line(line)
        if not parts:
            out.append({"line": line, "status": "unknown"})
            continue
        slot, label, before, after = parts
        ka, na = shape(after)
        kb, nb = shape(before)
        res = {"line": line, "status": "unknown"}
        if slot is None and label in MOD_LABELS and ka == "one" and "%" in after:
            key = MOD_LABELS[label]
            cur = (champ.get("aram") or {}).get(key, 1) * 100
            res["source"] = "위키 증강 모드 보정 표"
            if close(cur, na[0]):
                res["status"] = "ok"
            elif kb == "one" and close(cur, nb[0]):
                champ["aram"] = {**(champ.get("aram") or {}), key: na[0] / 100}
                res.update(status="applied", note=f"위키 {cur:g}% → 패치 노트 {na[0]:g}%")
            else:
                res.update(status="differ", note=f"위키 {cur:g}%")
        elif slot in spells and "재사용 대기시간" in label and ka:
            sp = spells[slot]
            cur = sp["cooldown"]
            res["source"] = "위키 증강 모드 표" if sp.get("cooldownBase") else "게임 데이터"
            if matches(cur, ka, na):
                res["status"] = "ok"
            elif kb and matches(cur, kb, nb):
                new = expand(ka, na, len(cur)) if (ka != "range" or is_linear(cur)) else None
                if new:
                    sp.setdefault("cooldownBase", list(cur))
                    sp["cooldown"] = new
                    res.update(status="applied", note=f"{'/'.join(f'{v:g}' for v in cur)} → {'/'.join(f'{v:g}' for v in new)}")
                else:
                    res["status"] = "stale"
            else:
                res.update(status="differ", note="데이터 " + "/".join(f"{v:g}" for v in cur))
        else:
            text = champ["passive"]["desc"] if slot == "P" else (spells[slot]["tooltip"] if slot in spells else
                   " ".join([champ["passive"]["desc"]] + [sp["tooltip"] for sp in champ["spells"]]))
            res["status"] = presence(text_numbers(text), before, after, label, re.sub(r"<[^>]+>", "", text))
            res["source"] = "스킬 설명 (게임 데이터)"
        out.append(res)
    return out


def check_augment(spell, desc_text, lines):
    """증강 줄 판정. '이전' 값과 같은 DataValue 가 하나뿐이면 그 값을 '이후'로 바꾼 spell 을 돌려준다.

    → (판정 목록, 고친 spell 또는 None). 고친 spell 로 설명·효과를 다시 계산하면 된다.
    """
    out, changed = [], None
    dvs = (spell or {}).get("DataValues") or []
    for line in lines or []:
        parts = split_line(line)
        if not parts:
            out.append({"line": line, "status": "unknown"})
            continue
        _, label, before, after = parts
        ka, na = shape(after)
        kb, nb = shape(before)
        pct = "%" in after
        res = {"line": line, "status": "unknown", "source": "게임 데이터"}
        if not na:
            out.append(res)
            continue
        scale = 100 if pct else 1
        have = text_numbers(desc_text)
        for dv in dvs:
            for v in dv.get("values") or []:
                have.update({round(v, 4), round(v * 100, 4)})
        plain = re.sub(r"<[^>]+>", "", desc_text or "")
        near = near_numbers(plain, label) if ka == "one" else None
        if near:  # 항목 이름 옆 숫자로 판정 (다른 수치와 우연히 같은 숫자를 피한다)
            st = "ok" if round(na[0], 4) in near else "stale" if kb == "one" and round(nb[0], 4) in near else "differ"
            res["source"] = "게임 데이터 (설명의 같은 항목)"
        else:
            st = presence(have, before, after, label, plain)
        res["status"] = st
        if st == "stale" and ka == "one" and kb == "one":
            # '이전' 값을 가진 DataValue 가 정확히 하나면 그 자리
            hits = [dv for dv in dvs if dv.get("values") and all(close(v * scale, nb[0]) for v in dv["values"])]
            if len(hits) == 1:
                changed = changed or {**spell, "DataValues": [dict(d) for d in dvs]}
                for d in changed["DataValues"]:
                    if d["name"] == hits[0]["name"]:
                        d["values"] = [na[0] / scale] * len(d["values"])
                res.update(status="applied", note=f"{hits[0]['name']}: {nb[0]:g} → {na[0]:g}")
        out.append(res)
    return out, changed
