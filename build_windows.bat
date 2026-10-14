@echo off
rem 윈도우 실행 파일 빌드: dist\칼바람증강Addon\칼바람증강Addon.exe
rem 필요: Python 3.12 (python.org 설치본, 설치 시 "Add python.exe to PATH" 체크)
rem 화면 엔진: Microsoft Edge WebView2 (윈도우 10/11 대부분 기본 설치)
chcp 65001 > nul
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\pip install -q -r requirements-build.txt
if not exist web\data.json .venv\Scripts\python build_data.py
.venv\Scripts\pyinstaller --noconfirm --windowed --name 칼바람증강Addon ^
  --add-data "web;web" --add-data "rules.json;." ^
  --hidden-import build_data --hidden-import champion_data --hidden-import lol_calc --hidden-import lcu --hidden-import patch_check --hidden-import overlay --hidden-import augocr --collect-all winrt --hidden-import hudicons --hidden-import gamelog ^
  desktop.py
echo 완료: dist\칼바람증강Addon\칼바람증강Addon.exe
pause
