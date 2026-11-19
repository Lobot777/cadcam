#!/usr/bin/env bash
# 没有 make 的环境用这个（Windows Git Bash 默认不带 make）。
#   bash scripts/reproduce.sh                     # 用 PATH 里的 python
#   PY=/d/Anaconda3/python bash scripts/reproduce.sh
# 等价于 make reproduce：freeze → bench → figures → test
set -euo pipefail

PY="${PY:-python}"
cd "$(dirname "$0")/.."
export PYTHONPATH=src

echo "解释器：$("$PY" -c 'import sys; print(sys.executable)')"
"$PY" -c "import numpy, matplotlib, pytest" 2>/dev/null || {
  echo "缺依赖：需要 numpy / matplotlib / pytest。安装："
  echo "  $PY -m pip install numpy matplotlib pytest pillow"
  exit 1
}

echo
echo "=== 1/4 冻结数据集 ==="
"$PY" scripts/freeze_dataset.py

echo
echo "=== 2/4 重算论文数字 ==="
"$PY" scripts/bench.py

echo
echo "=== 3/4 生成主图 ==="
"$PY" scripts/figures.py

echo
echo "=== 4/4 全量测试 ==="
"$PY" -m pytest tests -q

echo
echo "复现完成。看 reports/bench.md 对照论文 §5。"
echo "可选补充（不计入标准复现，避免把全链路拖到半小时以上）："
echo "  make step-stl      STEP→STL→OCL 独立几何链路（约 10 秒，需 freecadcmd）"
echo "  make ocl-external  130 件外部集 OCL 交叉验证（约 18 分钟，需 opencamlib）"
echo "  make ocl-point-cutter 点刀半径敏感性（约 30 秒）"
