#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# latexmk detects biblatex and runs biber automatically.
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
latexmk -pdf -interaction=nonstopmode -halt-on-error cover-letter.tex

echo "Built main.pdf and cover-letter.pdf"
