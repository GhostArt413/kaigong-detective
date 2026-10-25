#!/bin/sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 standalone/server.py
else
  echo '请先安装 Python 3.10 或更新版本：https://www.python.org/downloads/'
fi
printf '\n按回车关闭窗口…'
read -r kaigong_exit
