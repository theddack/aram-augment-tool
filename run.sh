#!/bin/sh
# 데이터가 없으면 먼저 받고, web/ 을 로컬 서버로 띄운다.
cd "$(dirname "$0")"
[ -f web/data.json ] || python3 build_data.py
PORT=${PORT:-8765}
echo "http://localhost:$PORT 에서 열기 (종료: Ctrl+C)"
python3 -m http.server "$PORT" --directory web
