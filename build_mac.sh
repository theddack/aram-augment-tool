#!/bin/sh
# 맥 앱 빌드: dist/칼바람증강Addon.app
# 처음이면 가상환경과 도구(pywebview, pyinstaller)를 설치한다.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements-build.txt
[ -f web/data.json ] || .venv/bin/python build_data.py
# 실행 파일 이름은 영어로 (한글이면 codesign 이 서명 도중 멈춰 '손상된 앱'이 된다), 보이는 이름·폴더만 한글
.venv/bin/pyinstaller --noconfirm --windowed --name KalbaramAddon \
  --osx-bundle-identifier io.github.theddack.aramaugment \
  --add-data "web:web" --add-data "rules.json:." \
  --hidden-import build_data --hidden-import champion_data --hidden-import lol_calc --hidden-import lcu --hidden-import patch_check --hidden-import overlay --exclude-module PIL \
  desktop.py
APP="dist/칼바람증강Addon.app"
rm -rf "$APP" && mv dist/KalbaramAddon.app "$APP"
/usr/libexec/PlistBuddy -c "Set :CFBundleName 칼바람증강Addon" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string 칼바람증강Addon" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName 칼바람증강Addon" "$APP/Contents/Info.plist"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
echo "완료: $APP (응용 프로그램 폴더로 옮겨서 쓰면 됩니다)"
