# CAD2CAM

> **接手本项目先读 `docs/HANDOFF.md`**：文档可信度层级、不可触碰的红线、环境与
> 可执行命令、剩余待办、常见问题路由、接手自检都在那份里。仓库里有若干 11-06 前的
> 历史快照写满「还剩什么没做」，已全部过时，不要照着做。

LLM 驱动的 CAD/CAM 自动推理框架：以 MCP 网关为中枢，把大模型的能力从文本扩展到
**空间模态**（B-Rep 几何/拓扑）与**制造模态**（工艺决策 / G 代码）。

> 状态：**M0–M6 全部完成**（2026-11-07 补充）。**211 passed**，`make bench` 一键重算全部数字。
>
> | 里程碑 | 结果 |
> |---|---|
> | M0 环境与基线 | 10 件锚点集解析 0 失败；G-code 解析器 v1.2 |
> | M1 MCP 网关 | 13/13 验收，7 类错误 100% 拦截、失败调用零副作用 |
> | M2 空间模态 | MFCAD++ 面级识别 **96.60% / mIoU 90.42%**（0.35M 参数） |
> | M3 制造模态 | 区域掩码刀路 **零过切**（196 726.7 → 0 mm³），最坏平均偏差 **0.206 mm**；双线口径 B2 **10/10**、A 路 2/10 |
> | M4 编排 | 20 任务端到端 **0.100 → 1.000**，平均修复轮次 **0.90 → 0.15** |
> | M5 基准 | 数据集冻结（sha256）+ `make bench` 台账 + 6 张主图 + **LLM 接入：15 个模型、三套任务集（56 条）** |
> | M6 外部验证 | **MFCAD 130 件全通过**（双线）+ CAMotics（体积差 1.15%）+ **OpenCAMLib 逐点几何交叉验证**（免费非 GUI，替代 Fusion 360）+ B3 基线（无膨胀 0/10）+ **130 件第三方判据扩展 + STEP→STL 独立几何链路 + Wilson 95% CI** |
> | M6 论文与开源 | 论文初稿 `docs/PAPER-DRAFT.md`、数据卡、复现指南、端到端 demo |
>
> LLM 部分：**15 个模型**——5 个本地 Q4 量化（Qwen3-8B/14B、Llama-3.1-8B、GLM-4-9B、Gemma3-12B，
> RTX 4090）＋10 个云端模型（DeepSeek-V4-Pro、GLM-5.3、Kimi-K2.8、MiniMax、Step-5、MiMo、
> 混元等，按账号积分调用）。**语言层交给模型、几何层留给确定性代码**：
> 20 条中文工单 → 契约调用，执行 20/20、特征去除体积 IoU **0.985**；同一模型直接生成
> G-code 则 0/10。结论与取舍见 `docs/M5-LLM.md`、`docs/AGENT-CASE.md`。
>
> 收尾报告：`docs/M1–M4-REPORT/FINAL`｜论文初稿 `docs/PAPER-DRAFT.md`｜复现 `docs/REPRODUCE.md`
> ｜数据卡 `docs/DATA-CARD.md`｜待办 `docs/NEXT-ACTIONS.md`。
> 几何内核：FreeCAD 1.1.4（纯开源单内核）。投稿目标（2026-11-09 定）：
> *Intelligent Computing*（IF 6.4 / JCR Q1）特刊 "Foundation Models Beyond Text:
> Towards General Multimodal Intelligence"；论文 §0 有提案对齐说明，§0.1 有英文 Title/Abstract。

## 快速开始

```bash
# 0. 三条命令看全貌（不需要 FreeCAD、不需要 GPU）
make demo      # 单件端到端：几何 → 计划 → G-code → 仿真 → 编排判定（约 20 秒）
make test      # 全量测试（177 条，约 25 秒）
make bench     # 重算论文全部数字 → reports/bench.md（约 2 分钟）

# 1. 依赖：FreeCAD 1.1+（只有 M1 网关与 A 路刀路复算需要）
#    自动探测 E:\FreeCAD*\bin\freecadcmd.exe，也可显式指定：
export FREECAD_CMD="E:/FreeCAD_1.1.4-Windows-x86_64-py311/bin/freecadcmd.exe"
make doctor    # 打印当前生效的内核与版本

# 2. 几何摘要（L0）/ 面表（L2）/ 参考 G 代码解析
PYTHONPATH=src python -m cadcam.cli summary data/zenodo_rptu/1.step
PYTHONPATH=src python -m cadcam.cli faces data/zenodo_rptu/1.step --page 1
PYTHONPATH=src python -m cadcam.cli gcode data/zenodo_rptu/1.ngc

# 3. 各里程碑验收
make m1        # 建块→挖腔→快照→钻孔→回滚 + 7 类错误拦截（需 FreeCAD）
make m2        # 标签对齐 + 合成集识别 + 真实件零样本
make m3        # 工艺库钳制 → 工序 DAG → G-code → 仿真 → 注入缺陷检出
make m4        # 三组对照：不修复 / 修复+冷记忆 / 修复+热记忆

# 4. 复现与出图
make freeze    # 数据集 v1 的 sha256 清单
make figures   # 6 张主图 → reports/figures/
make reproduce # freeze + bench + figures + test
# Windows 的 Git Bash 默认没有 make：等价命令
PY=<装了 numpy/matplotlib/pytest 的 python> bash scripts/reproduce.sh

# 5. 以 MCP stdio 服务器方式启动网关（给智能体/客户端接入）
PYTHONPATH=src python -m cadcam.gateway.server
```

## M1 网关：契约式 CAD 动作

模型不能写任意脚本，只能调用以下封闭动作集。每个动作在网关侧做参数校验、
几何前置条件检查、体积守恒后置校验，失败自动回滚。

| 工具 | 说明 |
|---|---|
| `cad.create_box` | 建毛坯 `[0,L]×[0,W]×[0,H]`，顶面 z=H |
| `cad.pocket` | 矩形型腔（中心 + 长宽 + 深度） |
| `cad.hole` | 圆柱孔（盲孔 depth / 通孔 through） |
| `cad.inspect` | L0 摘要（尺寸、体积、曲面构成、孔与凸台半径） |
| `cad.faces` | L2 面表分页 |
| `cad.snapshot` / `cad.rollback` / `cad.snapshots` | 快照与回滚（回滚用几何指纹校验） |
| `cad.export_step` | 导出 STEP |
| `feature.recognize` | 图神经网络面级特征识别 + 实例聚合 + 几何交叉校验（只读） |

错误码：`E_PARAM_INVALID` / `E_PRECONDITION` / `E_GEOM_FAIL` / `E_POSTCONDITION` /
`E_STATE` / `E_ROLLBACK` / `E_UNKNOWN_TOOL`。设计理由见 `docs/adr/0001-tool-contract-gateway.md`。

## M2 特征识别

MFCAD++ 面级识别，三层 GraphSAGE（0.35M 参数），test 面级准确率 96.60%、mIoU 90.42%。
三档对照见 `docs/M2-REPORT.md`：仅面特征 69.69% → 手工邻域 92.24% → 多跳消息传递 96.60%。

识别器需要单独的 conda 环境（torch 与 FreeCAD 内置 Python 隔离）：

```bash
conda create -n cadcam python=3.11
conda run -n cadcam pip install torch --index-url https://download.pytorch.org/whl/cu124
```

```bash
# 训练（8000 件，40 epoch，约 147 秒）
PYTHONPATH=src python scripts/train_mfr.py --epochs 40 --hidden 256

# 评测 + 对照组
PYTHONPATH=src python scripts/eval_mfr.py
PYTHONPATH=src python scripts/baseline_mfr_sklearn.py

# 对任意 STEP 直接推理
PYTHONPATH=src python -m cadcam.modalities.mfr.predict data/zenodo_rptu/9.step
```

**关键设计**：模型输出不直接采信。圆柱面的凹/凸由「法向·径向」的符号唯一确定，
`verify.cross_check` 用它复核模型判断，冲突时以几何为准——实测在真实零件上
纠正了 4 处「外轮廓圆角被判成 Ø10 孔」的错误，而模型对这些面的置信度是 0.967。

## 目录

```
src/cadcam/
  adapters/            # 几何内核适配：freecad（子进程 + JSON）、mock
  modalities/brep/     # B-Rep 摘要 L0/L1/L2，渐进式几何披露
  modalities/mfr/      # 面级特征识别：GNN + 规则交叉校验 + 实例聚合
  modalities/cam/      # 工艺库、工序 DAG、受限 G-code、区域掩码刀路、G-code 解析
  verify/              # Z-dexel 材料去除仿真、DFM 检查、结构化错误对象
  orchestrator/        # M4：状态机、黑板、六角色、有界修复、情节记忆
  gateway/             # MCP 网关：契约、事务、快照、回滚
scripts/
  freeze_dataset.py    # 数据集 v1 sha256 清单
  bench.py             # 一键重算论文数字 → reports/bench.{json,md}
  figures.py           # 6 张主图 → reports/figures/
  demo.py              # 单件端到端演示
  m1_demo.py … m4_run.py  # 各里程碑验收
reports/               # 全部数字产物（json）+ figures/
data/zenodo_rptu/      # 外部锚点集（CC-BY 4.0）+ 本文面级标注
docs/                  # 计划、ADR、里程碑报告、论文初稿、数据卡、复现指南
tests/                 # 177 条用例（状态机/黑板/有界修复/记忆/仿真/契约/第三方交叉验证）
```

## 文档地图

| 想知道什么 | 看哪里 |
|---|---|
| 论文写了什么 | `docs/PAPER-DRAFT.md` |
| 怎么复现 | `docs/REPRODUCE.md` |
| 数据从哪来、能用来干什么 | `docs/DATA-CARD.md` |
| 各里程碑结论 | `docs/M1-REPORT.md` … `docs/M4-FINAL.md` |
| 验收口径为什么这么定 | `docs/M3-ACCEPTANCE-CRITERION.md`、`docs/decisions/ADR-001-*.md` |
| 还没做什么 | `docs/NEXT-ACTIONS.md` |
| 计划 vs 实际差在哪 | `docs/PLAN-VS-ACTUAL.md` |
| LLM 怎么接的、跑出什么 | `docs/M5-LLM.md` |
| agent 这个说法还站不站得住 | `docs/AGENT-CASE.md` |
| 各家开源模型谁更强（本地量化） | `docs/M5-MODELS.md` |
| 云端大模型对照（10 个，账号积分） | `docs/M5-CLOUD.md` |
| 第二套难度集（20 条，顶天花板） | `docs/M5-HARD2.md` |
| 模型会不会"追问"（歧义检测） | `docs/M5-AMBIGUITY.md` |
| 非轴向特征（斜底槽）与可扩展性 | `docs/M5-RAMP.md` |
| 外部验证集（130 件）与第三方交叉验证 | `docs/M6-EXTERNAL.md` |
| **不用 Fusion 360 的免费非 GUI 第三方 CAM 对照** | `docs/M6-OPENCAMLIB.md` |
| 第三方判据扩到 130 件、两类过切与半格余量取舍 | `docs/M6-OCL-EXTERNAL.md` |

## 数据来源与许可

`data/zenodo_rptu/` 来自 Schmitz, Mertes, Schillinger, Wagner,
*Benchmark Dataset of 10 Multi-Feature Models for Single-Setup 3-Axis Milling*,
Zenodo 17035762，**CC-BY 4.0**。引用时请保留原始署名。

第三方工具（只作为运行时依赖，不随仓库分发）：FreeCAD 1.1.4（LGPL-2.0+）、
CAMotics 1.2.0（GPL）、**OpenCAMLib 2023.1.11（LGPL-2.1，`pip install opencamlib`）**。
外部验证集 MFCAD 为 MIT、Durham 为 CC-BY 4.0；详见 `docs/DATA-CARD.md`。
三个第三方 CAM/仿真工具全部**不需要 GUI、不需要商业授权**。
`reports/m6_step_stl/*.stl` 是从上述 Zenodo STEP 派生的二进制 STL，
沿用 CC-BY 4.0 与原始署名要求，仅用于独立几何链路复核（见 `docs/DATA-CARD.md` §6）。

## 已知坑

- **MFCAD++ 没有官方预训练权重**，只有数据集（59,665 STEP / CC BY）。第三方权重绑定
  pythonocc-core 7.5.1 + DGL，装错版本整条链就崩。本项目自训，0.35M 参数 147 秒。
- MFCAD++ 的标签是**语义分割**（每面一个类别号），**没有实例标签**，所以实例级评测做不了。
- STEP 的面顺序与 FreeCAD 读入顺序**逐位一致**（实测 40/40），可以按索引对齐标签；
  但这个结论依赖 OCC 的读取顺序，换内核需重测。
- `ADVANCED_FACE('24',...)` 的第一个参数是特征类别号（0–24），不是实例 ID。
- 真实零件的 BSpline 面在 MFCAD++ 中不存在，跨域时必然误判。

- **FreeCADCmd 执行脚本时 `__name__` 等于文件名词干，不是 `__main__`**，
  且会吞掉异常并返回 exit code 0（**1.1.4 实测行为相同**）。因此 worker 用
  「非包导入则执行」判定，并把 traceback 写进输出 JSON，由宿主侧抛出。
- 1.1.x 起可执行文件更名为小写 `freecadcmd.exe`（Windows 不区分大小写，但脚本与
  文档需按新名书写）；便携版不写注册表，故适配器改为目录探测。
- **OpenCAMLib 的 `dropCutter` 是从下往上顶**：`CLPoint` 初始 z 必须低于所有表面，
  给 `z=200` 会一律 `hit=False` / `calls=0`。二进制 STL 每条记录是 **50 字节**
  （12 个 float + 2 字节属性），按 48 字节 unpack 会报错；`STLSurf` 也没有 `build()`。
  三个坑各浪费了一轮探针，见 `docs/M6-OPENCAMLIB.md` §2。
- `Part.Vector` 不存在（会 AttributeError），向量运算用 `FreeCAD.Vector` 或纯元组。
- FreeCADCmd 不接受脚本路径之后的 CLI 参数，参数改用环境变量传递。
- 几何缓存键包含内核版本，避免不同内核的结果互相污染。
- `Part.export([shape], path)` 只接受**文档对象**，对裸 `Shape` 会写出没有几何的空壳 STEP
  （仅 1.6KB、读回报 `shape is invalid`）。裸 Shape 用 `Shape.exportStep(path)`。
- 整 360° 圆柱面的形心落在轴线上，不能用形心做径向判据；改用面的边中点作探针。

## 测试

```bash
PYTHONPATH=src python -m pytest tests -q
```
