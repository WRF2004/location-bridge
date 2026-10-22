#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert sys.version_info >= (3,9)' >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo '需要先安装 Python 3.9 或更新版本。下载页将打开；安装后再次双击 Start.command。'
  open 'https://www.python.org/downloads/macos/'
  read -r -p '按回车退出…' _
  exit 1
fi
"$PYTHON_BIN" bootstrap.py || { read -r -p '启动失败。请截图错误信息，按回车退出…' _; exit 1; }
