#!/bin/sh
# 맥 앱 빌드: dist/AramAugment.app
# 처음이면 가상환경과 도구(pywebview, pyinstaller)를 설치한다.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q pywebview pyinstaller
[ -f web/data.json ] || .venv/bin/python build_data.py
.venv/bin/pyinstaller --noconfirm --windowed --name AramAugment \
  --osx-bundle-identifier kr.juvis.aramaugment \
  --add-data "web:web" --add-data "rules.json:." \
  --hidden-import build_data --hidden-import champion_data --hidden-import lol_calc \
  desktop.py
echo "완료: dist/AramAugment.app (응용 프로그램 폴더로 옮겨서 쓰면 됩니다)"
