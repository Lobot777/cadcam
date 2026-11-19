# 格式合规核对（2026-11-13）

> 对照对象：`latex/template.txt`（官方 LaTeX 模板）与
> <https://spj.science.org/page/icomputing/for-authors>（2026-11-13 读取）。
> 被核对文件：`latex/main.pdf`（23 页）与 `latex/sections/*.tex`、`latex/refs.bib`。
> 结论：**结构与计数全部合规；作者信息类占位仍待填写。**

## 1 硬性计数

| 要求 | 实测 | 状态 |
|---|---|---|
| Research Article ≤ 15 000 words | 约 7 700 词（LaTeX 剥离后） | 合规 |
| Abstract ≤ 250 words，单段 | 219 词，单段 | 合规 |
| Title ≤ 100 characters，无缩写，title case | 63 字符，*Auditing Foundation Model Grounding in Design and Manufacturing* | 合规 |
| 图表合计 ≤ 10 | 6 图 + 4 表 = 10 | 合规（正好用满） |
| 参考文献 ≤ 60 | 21 | 合规 |
| 必须恰好四个一级节，且仅用指定标题 | INTRODUCTION / MATERIALS AND METHODS / RESULTS AND DISCUSSION / CONCLUSION | 合规 |
| 单栏 + 行号 | `\documentclass{article}`、`\linenumbers`、1.25 in 边距 | 合规 |
| 图/表均需在正文按序引用至少一次 | Fig 1→6、Table 1→4 均按序出现 | 合规 |
| 子标题统一为短语，< 10 词，无句号 | 19 个子标题，最长 9 词，全部名词/动名词短语 | 合规 |
| 图注以短标题开头，≤ 200 词 | 35–93 词，均以短标题开头 | 合规 |
| 表格无竖线，单位在列头 | `booktabs`，无竖线；Table 1 列头含 mm³/mm | 合规 |
| 多面板图用 A/B/C 标注并在图注解释 | Fig 1–6 均用 (A)/(B)，图注逐面板说明 | 合规 |

## 2 必需声明与位置

| 要求 | 落点 | 状态 |
|---|---|---|
| Acknowledgments | `\section*{Acknowledgments}` | 有 |
| General（模板要求的子节） | `\subsection*{General}`，含 AI 使用披露 | 有 |
| Author Contributions | `\subsection*{Author Contributions}` | 有，占位待填 |
| Funding | `\subsection*{Funding}` | 有，占位待填 |
| Conflicts of Interest（模板标题） | `\subsection*{Conflicts of Interest}`，用模板建议的否认句式 | 有 |
| Data Availability | `\subsection*{Data Availability}` | 有；仓库 URL / Zenodo DOI 占位 |
| AI 使用在 cover letter 与 Acknowledgments **各声明一次** | 两处独立段落 | 合规 |
| Cover letter 声明投给该 Special Issue | `cover-letter.tex` 首段 | 合规 |
| Cover letter 含标题、意义、新颖性、未发表声明、作者/ORCID | 有，作者/ORCID 占位 | 部分待填 |
| 潜在审稿人、相关在投论文 | cover letter 占位 | 待作者提供 |

## 3 本轮修正的格式偏差

1. `Competing Interests` → `Conflicts of Interest`：与官方 LaTeX 模板的子节标题一致；
   声明正文仍用模板给出的 “no conflict of interest” 句式。
2. Acknowledgments 下补 `General` 子节，恢复模板的层级。
3. 两个带谓语的子标题改为名词短语：
   `Why a Single Maximum-Deviation Criterion Fails` → `Failure of a Single Maximum-Deviation Criterion`；
   `What Fifteen Models Do at the Decision Points` → `Fifteen Models at the Decision Points`。
4. 打开 `articledoi=true`：NEJM 样式默认 `articledoi=false`，会吞掉 `@article` 的 DOI。
5. 修正三条文献元数据：
   - Wilson 1927：*Biometrika* 19:207–212 → *JASA* 22(158):209–212，DOI 10.1080/01621459.1927.10502953；
   - DeepCAD：页码 6772–6782 → 6752–6762；补 DOI 10.1109/ICCV48922.2021.00670；
   - ABC：补 DOI 10.1109/CVPR.2019.00983。
6. 图 1 图注补 `M2–M4`、`L0–L2`、`DFM`、`E_GOUGE` 的解释；图 1 坐标区扩到整幅，
   使印刷时框内标签可读。
7. `hyperref[hidelinks]`：去掉图表/公式引用的红色边框。

## 4 仍需作者填写（不填不能投稿）

- `latex/main.tex`：作者姓名、单位、通讯邮箱。
- `latex/sections/05-backmatter.tex`：General 致谢、Author Contributions（initial + 全姓）、
  Funding（含 grant number 或模板规定的无基金句）、仓库 URL、Zenodo DOI。
- `latex/sections/02-methods.tex`：Human and Animal Research 的知情同意措辞确认。
- `latex/cover-letter.tex`：投稿日期、作者邮箱/ORCID、通讯作者、潜在审稿人、
  相关在投/在印论文、两个 proposal 的数据重叠说明。
- 当前 APC 与双 proposal 关系：编辑部/原邮件确认。

## 5 复核命令

```bash
cd /e/CAD2CAM
PYTHONPATH=src /d/Anaconda3/python -m pytest -q          # 211 passed
cd latex && bash build.sh                                # 23 pages, zero warning
pdftotext main.pdf - | /d/Anaconda3/python -c "import sys,re; print(len(re.findall(r'[\u4e00-\u9fff]', sys.stdin.read())))"  # 0
```
