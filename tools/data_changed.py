"""데이터가 '실제로' 바뀌었는지 (받은 시각·만든 시각만 다르면 안 바뀐 것으로).

  python tools/data_changed.py <이전 data.json> <새 data.json>   → 바뀌었으면 종료 코드 0, 아니면 1
"""
import json
import sys


def strip(d):
    d = dict(d)
    d.pop("builtAt", None)
    d["sources"] = [{k: v for k, v in s.items() if k != "fetched"} for s in d.get("sources", [])]
    return d


old, new = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])
changed = strip(old) != strip(new)
print("바뀜" if changed else "그대로")
sys.exit(0 if changed else 1)
