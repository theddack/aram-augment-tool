"""업데이트내역.md 하나로 화면 페이지와 릴리스 설명을 만든다.

  python tools/changelog.py html                 → web/changelog.html (프로그램·웹의 '업데이트 내역' 페이지)
  python tools/changelog.py release v0.1.2       → 릴리스 설명 (받기 안내 + 그 버전 내역), 표준 출력
"""
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "업데이트내역.md"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def section(text, tag):
    m = re.search(rf"^## {re.escape(tag)}\b.*?(?=^## |\Z)", text, re.M | re.S)
    return m.group(0).strip() if m else f"## {tag}"


def inline(s):
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return re.sub(r"\[(.+?)\]\((https?://[^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)


def to_html(md):
    out, in_list, depth = [], False, 0
    for line in md.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^(\s*)- (.*)", line)
        if m:
            d = len(m.group(1)) // 2 + 1
            while depth < d:
                out.append("<ul>")
                depth += 1
            while depth > d:
                out.append("</ul>")
                depth -= 1
            out.append(f"<li>{inline(m.group(2))}</li>")
            continue
        while depth:
            out.append("</ul>")
            depth -= 1
        if line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
        elif line.strip() == "---":
            out.append("<hr>")
        else:
            out.append(f"<p>{inline(line)}</p>")
    while depth:
        out.append("</ul>")
        depth -= 1
    return "\n".join(out)


PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>업데이트 내역 · 칼바람 증강 Addon</title>
<style>
:root {{ --bg:#0f1419; --panel:#171e26; --line:#26313d; --text:#e6e9ec; --muted:#8b96a3; --accent:#c8aa6e; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.7 -apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif; }}
main {{ max-width:820px; margin:0 auto; padding:16px; }}
a {{ color:var(--accent); }} h1 {{ color:var(--accent); font-size:22px; }} h2 {{ margin-top:28px; border-bottom:1px solid var(--line); padding-bottom:4px; }}
h3 {{ font-size:15px; margin:16px 0 4px; }} blockquote {{ margin:8px 0; padding:8px 12px; background:var(--panel); border-left:3px solid var(--accent); }}
code {{ background:var(--panel); padding:0 4px; border-radius:4px; }} hr {{ border:0; border-top:1px solid var(--line); margin:24px 0; }}
.back {{ display:inline-block; margin-bottom:8px; }}
</style></head><body><main>
<a class="back" href="index.html">← 칼바람 증강 Addon으로 돌아가기</a>
{body}
<p style="color:var(--muted);font-size:12px">라이엇 게임즈와 무관한 팬 제작 도구입니다.</p>
</main></body></html>
"""

DOWNLOAD = """## 받기
| 컴퓨터 | 파일 |
|---|---|
| 윈도우 10/11 | `KalbaramAddon-Windows.zip` → 압축 풀고 `칼바람증강Addon` 폴더의 `칼바람증강Addon.exe` |
| 맥 (M1~M4, macOS 11 이상) | `KalbaramAddon-Mac.zip` → 압축 풀고 `칼바람증강Addon.app` |

처음 실행 시 경고가 뜨면 [사용 설명서](https://github.com/theddack/aram-augment-tool/blob/main/사용설명서.md#처음-실행할-때-경고가-뜨면)를 보세요. 롤 화면 모드는 **테두리 없음**으로 해 주세요.
"""

if __name__ == "__main__":
    text = SRC.read_text(encoding="utf-8")
    if sys.argv[1] == "html":
        (ROOT / "web" / "changelog.html").write_text(PAGE.format(body=to_html(text)), encoding="utf-8")
        print("web/changelog.html")
    elif sys.argv[1] == "release":
        print(DOWNLOAD + "\n" + section(text, sys.argv[2]) + "\n\n전체 내역: [업데이트내역.md](https://github.com/theddack/aram-augment-tool/blob/main/업데이트내역.md)\n\n라이엇 게임즈와 무관한 팬 제작 도구입니다.")
