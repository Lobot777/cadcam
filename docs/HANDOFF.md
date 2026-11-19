# 项目交接文档

> **当前接手入口：[REVISION-HANDOFF-2026-11-13.md](REVISION-HANDOFF-2026-11-13.md)**，内含可直接复制的接手提示词、验证记录和未解问题。以下正文保留为历史快照。
>
> **2026-11-13 修订覆盖声明**：用户明确授权重新评估冻结边界并修改实验与论文。下文原快照中的“内容不再改动”“没有技术缺口”“只等审稿”不再作为当前结论。原始报告保留；新增 `reports/revision_archive_audit.json` 记录归档指标审计，独立参考几何复核已完成一部分（contract 完整、visible 与 script 各有一题未解），状态与边界见该交接文档的「续修记录」。当前英文稿以 `latex/` 为准，原标题和旧数字解释须按修订稿核对。修改前投稿包保存在 `build/submission_before_revision_20261113.zip`。
>
> 已核实的纠错：20 工单与 10 件直接 G-code 不是同题配对；`exec_ok` 不等于语义正确；`legal` 仅检查工具名和必填参数、在提交之前计数；15 模型中 Llama 有 1 次 JSON 失败；旧参考几何经过同一契约生成，存在共同遗漏特征的风险。外部有限刀具判据的 71.104 mm 深度不能用缩小点探针实验消去；128/130 件至少一次外部违规、94/130 件有 >0.5 mm 违规，半格余量后仍为 19/130、8/130。下文与这些事实冲突的旧结论仅作历史记录。

> 生成：2026-11-13｜接手对象：另一个 AI 模型 / 新合作者｜工作目录：`E:\CAD2CAM`
> 基线（2026-11-13 下午）：`latex/main.pdf`（23 页，英文修订稿）、`211 passed`、零后台服务、零未提交实验。
> 本文件是**导航 + 红线 + 可执行命令**，不是项目历史。历史在各里程碑报告里，按需再读。

---

## 0. 三十秒上手

**项目是什么**：一个把大语言模型接到 CAD/CAM 链路上的可验证架构。模型只下中文工单、调类型化工具契约；几何计算、刀路生成、G-code 输出、过切校验全部由确定性代码完成。论文投 *Intelligent Computing* 特刊 "Foundation Models Beyond Text: Towards General Multimodal Intelligence"。

**核心结论（一句话）**：动作空间决定可靠性，不是模型规模。同一个模型逐行写 G-code 是 0/10，通过封闭契约下工单是 20/20（去除体积 IoU 0.985）。系统能全自动生成 G-code，只是不由模型逐行手写。

**现在到哪一步**：M0–M6 全部完成，实验数据已冻结，英文投稿包已编译通过。**内容层面不再改动**；剩下的是只有作者本人能提供的身份 / 合规信息。deadline 2027-06-30，没有时间压力。

**你第一件事**：读下面 §1 的文档可信度层级。仓库里有几份 11-06 之前的历史快照，写满了「还剩什么没做」，**全部已过时**。照着做会重复已完成的工作，甚至把已结案的论点重新打开。

**一条命令验环境**：

```bash
cd /e/CAD2CAM && PYTHONPATH=src /d/Anaconda3/python -m pytest -q 2>&1 | tail -3
# 期望：177 passed in ~21s
```

---

## 1. 文档可信度层级（最重要的一节）

### 1.1 权威口径（以此为准）

| 文件 | 管什么 |
|---|---|
| `latex/main.pdf` + `latex/sections/01–05*.tex` + `latex/main.tex` | **唯一对外英文稿**。任何表述争议以这里为准 |
| `latex/cover-letter.tex` / `.pdf` | 特刊归属声明、AI 使用声明、未发表声明 |
| `latex/refs.bib` | 20 篇文献，逐篇核实过作者全名 |
| `latex/README.md` | 期刊格式合规对照表 + **提交前必须替换的占位清单** |
| `docs/DECISIONS.md` | D1–D11 决策台账，含每次「为什么不补实验」的理由 |
| `docs/REVIEW-READINESS.md` | 8 条审稿弱点的逐条裁决、证据指针、答辩顺序、不做清单 |
| `docs/PAPER-DRAFT.md` | 中文工作稿 v0.3。§9.1 是真待办，§9.2 是已结案 |
| `docs/NEXT-ACTIONS.md` | 待办三档（A 作者侧 / B 已答问题 / C 可代做） |
| `docs/REPRODUCE.md`、`docs/DATA-CARD.md` | 复现步骤、数据许可与禁止用途 |
| `reports/bench.json`、`reports/*.json` | **所有数字的唯一真源**，论文不手抄、不重估 |
| `README.md`（根） | 工程入口、目录说明、**已知坑**（很值钱，别跳过） |

### 1.2 历史快照（可读，禁止当待办）

| 文件 | 陷阱 |
|---|---|
| `docs/PREP-CHECKLIST.md` | 11-06 前写的「要装 FreeCAD、给 API Key、给 5 个 STEP」，全部完成 |
| `docs/PLAN-VS-ACTUAL.md` §5 | 11-06 的「还剩什么没做」，结论已被后续工作推翻 |
| `docs/DEVELOPMENT-PLAN.md` | 目标仍写 NeurIPS 2027，已被 D8 改为特刊；验收定义已被 ADR-001 取代 |
| `docs/DECISIONS.md` D1 / D2 | 已被 D8 取代，文档内已加删除线和标注 |
| `docs/decisions/OPEN-DECISIONS.md` | 最后那条「v1 人工标注重做还是降级」实际已按降级结案，登记册没同步关闭 |
| `docs/M*-PLAN.md`、`M*-REPORT.md` | 里程碑归档，描述的是当时状态，不是当前状态 |
| 根 `README.md` 里残留的 "Fusion 360" 字样 | 是**历史记录**（"免费非 GUI 的 OpenCAMLib 替代 Fusion 360"），不是投稿口径 |

### 1.3 一句话判别法

看到「还需要 / 待做 / 阻塞」字样，先查文件日期。**2026-11-09 之后生成的才可能是真待办**；其余按已结案处理，去 `docs/PAPER-DRAFT.md` §9.2 和 `docs/DECISIONS.md` 找关闭记录。

---

## 2. 项目结构

```
E:\CAD2CAM\
  README.md                 工程入口、快速开始、已知坑；顶部有本文档的入口链接
  Makefile / pyproject.toml  构建与包定义（本机没有 make，见 §4）
  src/cadcam/               47 个 Python 文件，核心实现
    adapters/               FreeCAD 子进程 + JSON、mock 内核
    modalities/brep/        B-Rep 摘要 L0/L1/L2
    modalities/mfr/         面级特征识别 GNN + 规则交叉校验
    modalities/cam/         工艺库、工序 DAG、G-code、区域掩码刀路、NGC 解析
    verify/                 Z-dexel 去除仿真、DFM、Wilson 区间
    orchestrator/           状态机、黑板、六角色、有界修复、情节记忆
    gateway/                MCP stdio server、契约、事务、快照、回滚
    agents/                 LLMClient + prompts + LLM 角色
  scripts/                  111 个文件：验收、评测、出图、复现
    reproduce.sh            全链路（无 make 时的入口）
    bench.py                重算论文全部数字 → reports/bench.{json,md}
    figures.py              6 张主图
    m3_* / m4_* / m5_* / m6_*   各里程碑脚本
  tests/                    177 条用例
  reports/                  828 个文件：数字产物、G-code、STL、图件
    bench.json / bench.md   台账，引用数字前先看这里
    figures/fig1–6.{png,pdf}
  data/                     锚点零件、面级标注、外部集、数据集 sha256 清单
  docs/                     43 份文档：计划、ADR、里程碑、论文、数据卡、答辩
  latex/                    投稿包（源 + 两份 PDF + 6 张矢量图）
  CESHI/                    人工作答题包（已降级为 sanity check）
  .workbuddy/memory/        7 份按日工作日志 + pitfalls.jsonl
```

**仓库状态**：`git` 尚未初始化，没有 `LICENSE` 文件。这是待办 A4，需要作者定远端与账号后才能做，不要擅自 `git init` 然后推送。

---

## 3. 当前状态快照（2026-11-13）

| 维度 | 状态 |
|---|---|
| 工程 | M0–M6 全部完成；`177 passed`；`bash scripts/reproduce.sh` 全链路通过 |
| 实验 | 四族：10 锚点件 + 20 编排任务 + 15 模型 × 3 任务集 + 130 外部件；两个第三方内核交叉验证 |
| 数据 | `data/DATASET-MANIFEST.json` 冻结 sha256；`make bench` 连跑两次数字逐位一致 |
| 投稿包 | `main.pdf` 21 页、`cover-letter.pdf` 2 页；Title 77 字符、Abstract 236 词、20 篇文献、6 图 + 4 表 |
| 编译 | `bash latex/build.sh` → 零 undefined citation、零 LaTeX Warning、零 Overfull |
| 外部条件 | deadline 已确认 **2027-06-30**；当前 APC、双 proposal 关系仍待作者确认 |
| 资源 | Ollama 服务 11-09 已停止；无 python / freecad / camsim 残留进程 |
| 版本管理 | 非 git 仓库，无 LICENSE，无远端，无 Zenodo DOI |

---

## 4. 环境与可执行命令

### 4.1 本机事实（Windows + Git Bash）

| 事实 | 说明 |
|---|---|
| **没有 `make`** | `make: command not found`。用 `bash scripts/reproduce.sh` 或直接 `python scripts/xxx.py` |
| **`PYTHONPATH=src` 必须显式设置** | 不带会报 7 个 collection error，不是代码坏了 |
| Python | `/d/Anaconda3/python`（有 numpy / matplotlib / pytest）。PATH 里第一个 `python`（WorkBuddy 自带）没有这些包 |
| torch | `/d/Anaconda3/envs/cadcam`，只有 `m2_demo.py` / `eval_mfr.py` / `train_mfr.py` 需要 |
| FreeCAD | 1.1.4 于 `E:\FreeCAD_1.1.4-Windows-x86_64-py311`，`freecadcmd.exe` 可用 |
| TeXLive | `latexmk` / `pdflatex` / `biber` / `pdftotext` / `pdfinfo` 全部可用 |
| 本地 LLM | Ollama 可执行在 `E:/tools/ollama/ollama.exe`，模型文件在 `E:/tools/ollama/models`。**服务已停止**，所有 `m5_*` 本地模型脚本需要它；不要为「验证一下」擅自启动 |
| 云端 LLM | WorkBuddy 云服务数据面，keyless、按账号积分。配置在 `.workbuddy/cloud.json`（已 gitignore），换机器要重新拿 `endpoint` / `publishableKey`。调用会花积分 |
| GPU | RTX 4090 24GB。当前无占用 |

### 4.2 三条最常用的命令

```bash
cd /e/CAD2CAM

# 1. 全量测试（必须带 PYTHONPATH）
PYTHONPATH=src /d/Anaconda3/python -m pytest -q          # 期望 177 passed

# 2. 重算论文全部数字
PYTHONPATH=src /d/Anaconda3/python scripts/bench.py       # 出 reports/bench.{json,md}

# 3. 重编译投稿包
cd latex && bash build.sh                                 # 出 main.pdf / cover-letter.pdf
```

### 4.3 全链路复现（约 2 分钟，不碰 GPU / 不碰模型）

```bash
PY=/d/Anaconda3/python bash scripts/reproduce.sh
# = freeze_dataset → bench → figures → pytest
```

**不要**把 `make ocl-external`（约 18 分钟）塞进标准复现；它的报告已存档在 `reports/m6_ocl_external.json`，`bench.py` 只做汇总。

### 4.4 编译后的合规自检

```bash
cd latex
printf "pages=%s bib=%s fig=%s tab=%s\n" \
  "$(pdfinfo main.pdf | awk '/Pages/{print $2}')" \
  "$(grep -c '^@' refs.bib)" \
  "$(grep -rh '\\begin{figure}' sections | wc -l)" \
  "$(grep -rh '\\begin{table}' sections | wc -l)"
# 期望：pages=21 bib=20 fig=6 tab=4
grep -E 'undefined|LaTeX Warning|Overfull' main.log     # 期望无输出
```

---

## 5. 不可触碰的红线

这一节是交接的核心。接手后最容易发生的错误，是「好心」地改数字、补实验、或把已结案的论点重新翻出来。

### 5.1 数字与代码

| 红线 | 原因 |
|---|---|
| 所有数字从 `reports/bench.json` / `reports/*.json` 取，不重估、不四舍五入改口径 | 论文、图、cover letter、答辩表全绑定同一份归档 |
| `heightmap.generate(..., guard_mm=0.0)` 默认值**不能改** | 改成 `res/2` 会推翻 M3 全部数字；半格余量的代价已量化披露（残留 ×4.42、双线 107/130） |
| `inflate=True` 是 B2 正路，`False` 是 B3 基线（0/10） | 两者是对照组，不要合并 |
| 双线判据 = `max_gouge_depth ≤ 0.5 mm` **且** `mean_abs_deviation ≤ 0.5 mm` | ADR-001，用户回复「按 C 改」后定案，见 `docs/decisions/ADR-001-reproduction-criterion.md` |
| 通过率一律写 `k/n`，不写百分比；附 Wilson 95% CI | 10/10 → [0.7225, 1.0000]，130/130 → [0.9713, 1.0000]。例外：第三方过切率是采样刀心点的比例，不是通过率 |
| 6 图 + 4 表 = 10，已用满期刊上限 | 补任何数据都要挤掉现有图表并重跑 reproduce / figures / DATA-CARD |

### 5.2 论文口径

| 红线 | 正确表述 |
|---|---|
| **「零过切」必须限定** | 只在「Z-dexel 单值高度场 + 生成器和验证器共用同一离散圆盘 + 给定网格分辨率」口径内成立。**绝不写连续几何零过切**。全文已有 6 处限定 |
| **Siemens NX / Fusion 360 在投稿包里一个字都不出现** | 用户裁定「NX 直接不选，就当没有」：不提、不解释、不主动致歉。`latex/*.tex` / `.bib` 扫描结果必须为 0 |
| 不写「与商用 CAM 对比」 | 第三方是 CAMotics 1.2.0 + OpenCAMLib 2023.1.11，两个独立开源内核 |
| STEP→STL 链路不是第二 CAD 内核 | 措辞固定为「独立几何链路复核输入网格」，B-rep 解析仍用 OCC |
| **自主性是 relocated, not reduced** | 系统全自动生成 G-code、全程无人干预；模型不下低级控制码。不要写成「不能自主生成」或「自主性被降级」；也不要写 unconstrained autonomy |
| 20/20 是 constructed result | 三处主动写明，并把注意力引到 2/20 无修复基线与 0.90→0.15 平均修复轮数 |
| 不宣称 general multimodal intelligence | 单域、2.5D、单值高度场、合成规则件，limitation 已主动披露 |
| AI 使用须在 cover letter 与 Acknowledgments **各声明一次** | 期刊明文要求，区分「模型当实验对象」与「辅助英文写作」 |
| 去 AI 腔 | 英文稿禁用词表列在 `latex/README.md` §English style，照那张表执行；禁三段式排比与空洞总结句。改完跑一次该表的扫描命令 |

### 5.3 已结案，不要再翻出来当待办

真实铣削（用户裁定无意义）、FreeCAD Path 负结果、人工构念效度实验（红色图例泄漏使二值题平凡，已降级 sanity check）、商用 CAM 对照、跨内核一致性（D3 与 `DECISIONS.md` §6）、3D dexel 倒扣、参数化中间动作空间、SolidCraft 安装、B16 云端多次采样。

**唯一允许补实验的触发条件**：审稿意见逐字点名要某项。那时只答被点名的那一条，rebuttal 里性价比最高。收到意见先查 `docs/REVIEW-READINESS.md` §1，不要凭印象重做。

---

## 6. 决策索引（细节在 `docs/DECISIONS.md`）

| 编号 | 一句话 |
|---|---|
| D1 / D2 | 历史：原定 NeurIPS 2027 与 24 周排期，已被 D8 取代 |
| D3 | 纯 FreeCAD / OCC 单内核，不做跨内核一致性 |
| D4 | 本地 Ollama（Windows 原生）+ 云端双栈；`wsl.exe` 在代理侧黑名单，但 WSL2 localhost 转发可行 |
| D6 / D7 | 不绑定控制器，输出通用 G-code 子集；代码 Apache-2.0 |
| D8 | 改投 *Intelligent Computing* 特刊 Research Article；主线重构为「可判定接口 + 动作空间实证」 |
| D9 | NX 不作承诺；自主性由「降级」改为「上移」，附离线上界（投影后仍 0/10，缺的是覆盖规划） |
| D10 | SolidCraft 只作文献引用与 limitation 说明，不进实验链（其自身 oracle 有 21 例期望值由 FreeCAD 1.1.4 计算，不具独立性） |
| D11 | 8 条审稿弱点全部不补实验；唯一稿件改动是结论段补三句厘清 claim 层级 |
| ADR-001 | 双线验收判据，详见 `docs/decisions/ADR-001-reproduction-criterion.md` |

另有 `docs/adr/0001-tool-contract-gateway.md`（契约式网关设计理由）。

---

## 7. 剩余待办：全部是作者侧信息

**没有技术缺口。** 完整占位清单在 `latex/README.md` §Replace before submission。

| # | 事项 | 落点 |
|---|---|---|
| 1 | 作者姓名、单位、通讯邮箱 | `latex/main.tex` |
| 2 | 一般致谢、作者贡献声明（initial + 全姓格式）、基金号或期刊规定的无基金句 | `latex/sections/05-backmatter.tex` |
| 3 | 仓库 URL + Zenodo DOI（数据可用性声明里是占位） | `05-backmatter.tex`；先需 A4 |
| 4 | 确认知情同意措辞后删掉占位 | `latex/sections/02-methods.tex` 的 Human and Animal Research |
| 5 | 投稿日期、全体作者邮箱 / ORCID、通讯作者、推荐审稿人、相关在投或在印论文副本 | `latex/cover-letter.tex` |
| 6 | 当前 APC（旧「2025 年底免」已过期）、两个 proposal 是否同一份数据（防重复发表） | 外部确认，CFP 全文未列 |
| 7 | 英文全文通读，拍板 `region-masked toolpath` / `dual-line acceptance` / `verification-grounded` 三个译名与文风 | 作者 |
| 8 | `git init` + `.gitignore` + `LICENSE`（代码 Apache-2.0 / 数据 CC-BY 4.0）+ 首次提交 | 本地可代做，但**推哪个远端、用哪个账号必须作者定** |
| 9 | 投稿时勾选 Special Issue 栏目（若有） | CFP 唯一明文要求是 cover letter 声明特刊归属，已写入首段 |

**标题取舍**：现标题 *A Verification-Grounded Architecture for Spatial and Manufacturing Modalities*（77 字符）为符合「≤100 字符且无缩写」被迫去掉了 `Foundation Models`；该词出现在摘要首句与 cover letter。上一轮结论是**不换**，别主动提议改标题。

---

## 8. 常见问题路由

| 用户问 | 去哪找，怎么答 |
|---|---|
| 「还差什么 / 到哪一步了」 | 先跑 §10 接手自检 + 看 `latex/README.md` 占位清单，再答。不要照 `PREP-CHECKLIST.md` 列 |
| 「能不能自主生成 G-code」 | 结论是**能**，系统全自动、无人干预，模型只下工单。0/10 是「模型逐行写坐标」这一动作空间失败。见 `docs/PAPER-DRAFT.md` §0.2、`scripts/m5_autonomy_probe.py` |
| 「要不要补实验 / 顶会够不够」 | `docs/REVIEW-READINESS.md` §4（明确不做）、`docs/DECISIONS.md` D8（改投理由）。默认答案是不补 |
| 「这个弱点会不会被拒」 | `docs/REVIEW-READINESS.md` §1 逐条裁决 + §2 五步证据链。注意 3.275% 是**过切标记**（316,398 / 9,660,095 采样刀心点），不是「刀具穿越」，是论文自审计的产出 |
| 「零过切是不是吹了」 | 全文 6 处限定；机理、分辨率收敛 1.332→0.432→0.143%、半格余量代价都已量化。见 `REVIEW-READINESS.md` §2 |
| 「某数字哪来的」 | `reports/bench.json` 的 `headline` / `wilson_95ci`，以及 `reports/m6_*.json`。不重估 |
| 「装个新工具 / 接个新数据集」 | 先查 `docs/DECISIONS.md` 是否已有类似裁决（D10 是范本）。图表已满、不启动后台服务、不花积分，默认不做 |
| 「重跑某实验」 | 先确认是否需要 Ollama / 云积分 / FreeCAD；能离线复现的走 `scripts/reproduce.sh` |
| 「改论文措辞」 | 只改 `latex/sections/*.tex` 与 `cover-letter.tex`，改完 `bash build.sh` 并跑 §4.4 自检。中文工作稿同步可选，但**不得改数字** |

---

## 9. 沟通与协作口径（用户偏好）

- **结论先行**，用能听懂的话，不要铺陈过程。用户多次明确表示「整了半天都跑偏了」。
- **不要重复已结案的事**，不要把 limitation 重新包装成待办。
- **不擅自启动后台服务、不花云端积分、不改实验数字**。此前用户专门要求清理资源占用，Ollama 常驻 71 小时后已停。
- **不夸大**：不写成商用 CAM 对比、不写成连续几何零过切、不把 15 模型黑盒对照吹成模型贡献。
- 用户裁定过的口径要记住：红色图例**就是过切**（不是题序泄漏）；真实物理切削无意义；FreeCAD 只作负结果；Fusion 360 能不用就不用；NX 就当没有。
- 提「可选方案」时，同时给出**为什么不建议现在做**，别把选择成本全丢给用户。
- 工作日志在 `.workbuddy/memory/YYYY-MM-DD.md`（append-only）。做完实质工作追加一条，不要整篇重写。

---

## 10. 接手自检（做完这些才算接住）

按顺序，全部应通过：

1. `PYTHONPATH=src /d/Anaconda3/python -m pytest -q` → `177 passed`
2. `PY=/d/Anaconda3/python bash scripts/reproduce.sh` → 四步全绿，`reports/bench.md` 与论文 §5 数字一致
3. `cd latex && bash build.sh` → 两份 PDF 生成，`main.log` 无 undefined / LaTeX Warning / Overfull
4. `pdfinfo main.pdf` → 21 页；`grep -c '^@' refs.bib` → 20；6 图 + 4 表
5. `grep -riE 'NX|Siemens|Fusion 360' --include='*.tex' --include='*.bib' latex/` → 无输出
6. 按 `latex/README.md` §English style 的禁用词表扫描 `latex/*.tex` 与 `latex/*.bib` → 无输出
7. 检查无 `python` / `ollama` / `freecad` / `camsim` 常驻进程
8. 读完 `docs/REVIEW-READINESS.md` §1 与 `docs/DECISIONS.md` D8–D11，能复述「为什么不补实验」

**门禁结论**：只要 1–8 全绿且 §5 红线未被触碰，项目处于可提交状态；此后任何改动都应先问「这是作者侧信息，还是又要开一条实验线」。

---

## 11. 一句话交接

> 实验和代码已经收口，英文稿已编译通过，别再改内容、别补实验、别碰数字；
> 接下来只做两件事——把作者信息填进占位符，以及等审稿意见回来按 `REVIEW-READINESS.md` 逐条答。
