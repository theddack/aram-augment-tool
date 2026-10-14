"""폴더를 zip 으로 묶는다. 파이썬 zipfile 은 한글 파일 이름에 UTF-8 표시를 붙여서 윈도우 탐색기·맥에서 안 깨진다.

  python tools/make_zip.py <폴더> <결과.zip>   (zip 안에 폴더 이름까지 포함)
"""
import sys
import zipfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in sorted(src.rglob("*")):
        if p.is_file():
            z.write(p, Path(src.name) / p.relative_to(src))
print(out, out.stat().st_size // 1024, "KB")
