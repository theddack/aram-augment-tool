"""매일 갱신한 데이터가 망가지지 않았는지: 챔피언·증강·아이템 수가 갑자기 크게 줄면 반영하지 않는다.

  python tools/data_sanity.py <이전 data.json> <새 data.json>   → 괜찮으면 종료 코드 0, 이상하면 1 (이유 출력)
"""
import json
import sys

KEYS = ("champions", "augments", "items")
MIN_RATIO = 0.9  # 이전의 90% 밑으로 줄면 받은 데이터가 덜 온 것으로 본다 (패치로 몇 개 빠지는 건 괜찮다)


def problems(old, new):
    out = []
    for k in KEYS:
        a, b = len(old.get(k) or []), len(new.get(k) or [])
        if b == 0 or (a and b < a * MIN_RATIO):
            out.append(f"{k} {a} → {b}개")
    for c in new.get("champions") or []:
        if not c.get("id") or not c.get("name"):
            out.append("이름 없는 챔피언")
            break
    return out


if __name__ == "__main__":
    old, new = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])
    bad = problems(old, new)
    print("데이터 이상: " + ", ".join(bad) if bad else "데이터 정상")
    sys.exit(1 if bad else 0)
