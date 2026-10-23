#!/usr/bin/env bash
# 这个仓库的验证门。一条命令跑完，红就退出。
#
# 为什么需要这个文件：
#   仓库的测试前置是「pytest 已经装在当前环境里」。实际存在两套环境——
#   本地是 .venv，CI 是 setup-python 装好的 runner。同一句 `pytest -q`
#   在本地这套里根本跑不起来（PATH 上没有 pytest），于是「验证」这件事
#   会得到「不合格」这个结论，而代码其实是好的。
#
#   把「用哪个 Python」的判断写进文件，而不是写进谁的命令历史：
#   换一台机器、换一个 agent，这句话还是同一个意思。
#
# 用法：
#   bash verify.sh
set -uo pipefail

cd "$(dirname "$0")" || exit 1

PY=""
for cand in .venv/Scripts/python.exe .venv/bin/python python3 python; do
  if ! command -v "$cand" >/dev/null 2>&1; then
    continue
  fi
  if "$cand" -c "import pytest" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "verify: 找不到装了 pytest 的 Python，测试根本没跑。" >&2
  echo "  本地修法：python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt pytest" >&2
  echo "  （POSIX 上是 .venv/bin/python）" >&2
  # 2 而不是 1：1 会被读成「测试跑了但没过」，而这里一条都没跑。
  exit 2
fi

echo "verify: $PY -m pytest -q"
exec "$PY" -m pytest -q
