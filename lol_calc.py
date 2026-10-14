"""게임 계산식(mSpellCalculations) 해석기.

스킬·증강 수치를 '기본값 + 레벨 비례 + 능력치 계수' 구조(Expr)로 평가한다.
스킬 레벨(rank)마다 따로 평가할 수 있어서 '40/55/70/85/100 + 40% 주문력' 같은 표시와
화면에서의 실제 수치 계산(최종 능력치 대입)에 같이 쓴다.
"""
import re

# 계산식의 mStat 번호 → (키, 표시 이름).
# 173명 스킬에서 번호별 사용처를 뽑아 실제 계수와 대조해 확정했다 (README 참고).
STAT = {
    0: ("AP", "주문력"), 1: ("ARMOR", "방어력"), 2: ("AD", "공격력"), 4: ("AS", "공격 속도"),
    6: ("MR", "마법 저항력"), 7: ("MS", "이동 속도"), 8: ("CRIT", "치명타 확률"),
    9: ("CRITDMG", "치명타 피해"), 12: ("HP", "최대 체력"), 14: ("CURHP", "현재 체력"),
    16: ("MISSINGHP", "잃은 체력"), 29: ("LETHALITY", "물리 관통력"), 31: ("RANGE", "사거리"),
    -1: ("MANA", "최대 마나"),  # 번호 없음: AbilityResourceByCoefficient 전용
}
# mStatFormula: 없음/0 = 전체, 1 = 기본, 2 = 추가
FORMULA_PREFIX = {0: "", 1: "base", 2: "bonus"}
PREFIX_NAME = {"": "", "base": "기본 ", "bonus": "추가 "}


def stat_key(p):
    sid = p.get("mStat", 0)
    if sid not in STAT:
        return None
    return FORMULA_PREFIX.get(p.get("mStatFormula", 0), "") + STAT[sid][0]


def stat_label(key):
    for prefix in ("bonus", "base"):
        if key.startswith(prefix):
            base = key[len(prefix):]
            return PREFIX_NAME[prefix] + next(n for k, n in STAT.values() if k == base)
    return next(n for k, n in STAT.values() if k == key)


def fmt_num(v):
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    if abs(v) >= 10:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{v:.2f}".rstrip("0").rstrip(".")


class Expr:
    """base + 레벨 비례(lvl: 1레벨값, 18레벨값) + Σ ratios[키] × 능력치"""

    __slots__ = ("base", "lvl", "ratios")

    def __init__(self, base=0.0, lvl=None, ratios=None):
        self.base, self.lvl, self.ratios = base, lvl, dict(ratios or {})

    def add(self, o):
        lvl = self.lvl
        if o.lvl:
            lvl = (lvl[0] + o.lvl[0], lvl[1] + o.lvl[1]) if lvl else o.lvl
        r = dict(self.ratios)
        for k, v in o.ratios.items():
            r[k] = r.get(k, 0) + v
        return Expr(self.base + o.base, lvl, r)

    def scale(self, k):
        return Expr(self.base * k, (self.lvl[0] * k, self.lvl[1] * k) if self.lvl else None,
                    {s: v * k for s, v in self.ratios.items()})

    def is_number(self):
        return not self.lvl and not self.ratios


class Calc:
    def __init__(self, spell, effect_amounts=None):
        self.values = {}
        for dv in spell.get("DataValues", []) or []:
            self.values[dv["name"].lower()] = dv.get("values") or [0]
        self.calcs = {k.lower(): v for k, v in (spell.get("mSpellCalculations") or {}).items()}
        self.effects = effect_amounts or spell.get("mEffectAmount") or []

    def value(self, name, rank):
        vals = self.values.get((name or "").lower())
        if vals is None:
            return None
        return vals[rank] if rank < len(vals) else vals[-1]

    def part(self, p, rank, depth=0):
        if depth > 10 or not isinstance(p, dict):
            return None
        t = p.get("__type")
        if t == "NumberCalculationPart":
            return Expr(p.get("mNumber", 0))
        if t == "NamedDataValueCalculationPart":
            v = self.value(p.get("mDataValue"), rank)
            return None if v is None else Expr(v)
        if t == "EffectValueCalculationPart":
            idx = p.get("mEffectIndex", 1) - 1
            if 0 <= idx < len(self.effects):
                vals = self.effects[idx].get("value") or [0]
                return Expr(vals[rank] if rank < len(vals) else vals[-1])
            return None
        if t in ("StatByCoefficientCalculationPart", "StatByNamedDataValueCalculationPart",
                 "StatBySubPartCalculationPart"):
            key = stat_key(p)
            if key is None:
                return None
            if t == "StatByCoefficientCalculationPart":
                coef = p.get("mCoefficient", 0)
            elif t == "StatByNamedDataValueCalculationPart":
                coef = self.value(p.get("mDataValue"), rank)
            else:
                # 계수가 레벨에 따라 변하면 18레벨 값을 쓴다 (빌드 비교는 풀템 기준이라)
                sub = self.part(p.get("mSubpart"), rank, depth + 1)
                coef = None if sub is None or sub.ratios else sub.base + (sub.lvl[1] if sub.lvl else 0)
            return None if coef is None else Expr(ratios={key: coef})
        if t == "AbilityResourceByCoefficientCalculationPart":  # 마나 계수 (라이즈 등)
            prefix = FORMULA_PREFIX.get(p.get("mStatFormula", 0), "")
            return Expr(ratios={prefix + "MANA": p.get("mCoefficient", 0)})
        if t == "ByCharLevelFormulaCalculationPart":  # 레벨별 값 표
            vals = p.get("values") or [0]
            return Expr(lvl=(vals[0], vals[min(17, len(vals) - 1)]))
        if "StartDataValue" in p and "EndDataValue" in p:  # 시작값~끝값 레벨 비례
            a, b = self.value(p["StartDataValue"], rank), self.value(p["EndDataValue"], rank)
            return None if a is None or b is None else Expr(lvl=(a, b))
        if t == "ByCharLevelInterpolationCalculationPart":
            return Expr(lvl=(p.get("mStartValue", 0), p.get("mEndValue", 0)))
        if t == "ByCharLevelBreakpointsCalculationPart":
            start = p.get("mLevel1Value", 0)
            end = start + p.get("mInitialBonusPerLevel", 0) * 17
            for bp in p.get("mBreakpoints", []) or []:
                end += bp.get("mAdditionalBonusAtThisLevel", 0) * (19 - bp.get("mLevel", 18))
            return Expr(lvl=(start, end))
        if t in ("SumOfSubPartsCalculationPart", "ClampSubPartsCalculationPart"):
            total = Expr()
            for sp in p.get("mSubparts", []) or []:
                r = self.part(sp, rank, depth + 1)
                if r is None:
                    return None
                total = total.add(r)
            return total
        if t == "ProductOfSubPartsCalculationPart":
            a = self.part(p.get("mPart1"), rank, depth + 1)
            b = self.part(p.get("mPart2"), rank, depth + 1)
            if a is None or b is None:
                return None
            if b.is_number():
                return a.scale(b.base)
            if a.is_number():
                return b.scale(a.base)
            return None
        if "mSpellCalculationKey" in p:  # 다른 계산식 참조
            return self.calc(p["mSpellCalculationKey"], rank, depth + 1)[0]
        return None

    def calc(self, name, rank=1, depth=0):
        """(Expr | None, 백분율 표시 여부)"""
        c = self.calcs.get((name or "").lower())
        if c is None or depth > 10:
            return None, False
        t = c.get("__type")
        if t == "GameCalculationModified":
            base, pct = self.calc(c.get("mModifiedGameCalculation", ""), rank, depth + 1)
            mul = self.part(c.get("mMultiplier"), rank, depth + 1)
            if base is None or mul is None or not mul.is_number():
                return None, False
            return base.scale(mul.base), pct
        if t == "GameCalculationConditional":
            return self.calc(c.get("mDefaultGameCalculation", ""), rank, depth + 1)
        total = Expr()
        for p in c.get("mFormulaParts", []) or []:
            r = self.part(p, rank, depth + 1)
            if r is None:
                return None, False
            total = total.add(r)
        if c.get("mMultiplier"):
            m = self.part(c["mMultiplier"], rank, depth + 1)
            if m is None or not m.is_number():
                return None, False
            total = total.scale(m.base)
        return total, bool(c.get("mDisplayAsPercent"))

    def resolve(self, name, rank=1):
        """이름을 값(DataValue)·계산식·구버전 효과값(e1, e2 …)으로 평가. (Expr | None, pct)"""
        v = self.value(name, rank)
        if v is not None:
            return Expr(v), False
        m = re.fullmatch(r"e(\d+)", name or "", re.I)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(self.effects):
                vals = self.effects[idx].get("value") or [0]
                return Expr(vals[rank] if rank < len(vals) else vals[-1]), False
        return self.calc(name, rank)


def fmt_expr_ranks(exprs, pct=False):
    """랭크별 Expr 목록을 '40/55/70 + 40% 주문력' 형태로."""
    k = 100 if pct else 1
    unit = "%" if pct else ""
    bases = [e.base * k for e in exprs]
    parts = []
    if any(abs(b) > 1e-9 for b in bases) or not any(e.lvl or e.ratios for e in exprs):
        uniq = []
        for b in bases:
            s = fmt_num(b)
            if not uniq or uniq[-1] != s:
                uniq.append(s)
        parts.append("/".join(uniq) + unit if len(set(uniq)) > 1 else uniq[0] + unit)
    lvl = exprs[-1].lvl
    if lvl:
        parts.append(f"{fmt_num(lvl[0] * k)}~{fmt_num(lvl[1] * k)}{unit}(레벨 비례)")
    keys = []
    for e in exprs:
        for key in e.ratios:
            if key not in keys:
                keys.append(key)
    for key in keys:
        vals = [fmt_num(e.ratios.get(key, 0) * 100) for e in exprs]
        uniq = list(dict.fromkeys(vals))
        parts.append(f"{'/'.join(vals) if len(uniq) > 1 else uniq[0]}% {stat_label(key)}")
    return " + ".join(parts)


def expr_json(exprs):
    """화면 계산용: 랭크별 base, 레벨 비례, 계수(랭크별)."""
    keys = sorted({k for e in exprs for k in e.ratios})
    out = {"base": [round(e.base, 4) for e in exprs]}
    if exprs and exprs[-1].lvl:
        out["lvl"] = [round(exprs[-1].lvl[0], 4), round(exprs[-1].lvl[1], 4)]
    if keys:
        out["ratios"] = {k: [round(e.ratios.get(k, 0), 4) for e in exprs] for k in keys}
    return out


PLACEHOLDER = re.compile(r"{{\s*([^}]+?)\s*}}")
