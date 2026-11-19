# 复现指南

> 目标：**第三个人拿到仓库，2 小时内跑通全部可复现结果**（不含 MFCAD++ 重新训练）。
> 全程不需要 GPU，不需要商业 CAD。只有 M1 网关验收需要 FreeCAD 二进制。

---

## 0 三条命令先跑通

```bash
make demo        # 单件端到端：几何 → 计划 → G-code → 仿真 → 编排判定（约 20 秒）
make test        # 全量测试（177 条，约 25 秒）
make bench       # 重算论文全部数字，出 reports/bench.md（约 2 分钟）
```

> **Windows 的 Git Bash 默认没有 `make`**（实测 `make: command not found`）。
> 两种替代：① 装 make（`choco install make`）；② 直接用脚本：
>
> ```bash
> PY=/d/Anaconda3/python bash scripts/reproduce.sh   # 等价于 make reproduce
> PY=/d/Anaconda3/python scripts/bench.py            # 等价于 make bench
> PY=/d/Anaconda3/python scripts/demo.py             # 等价于 make demo
> ```

`make bench` 结束时会打印「关键数字」表，与 `docs/PAPER-DRAFT.md` §5 的表逐项对应。
**如果数字对不上，先看 `reports/bench.md` §1 里哪一步 `FAIL`。**

---

## 1 环境

| 依赖 | 版本 | 必需性 |
|---|---|---|
| Python | ≥3.10（实测 3.13.12 / Anaconda 3.13） | 必需 |
| numpy | ≥1.24 | 必需（几何栅格化与仿真） |
| pytest | ≥8 | 必需（`make test`） |
| matplotlib | ≥3.7 | 生成主图需要 |
| Pillow | ≥10 | `scripts/m3_human_baseline.py` 需要 |
| FreeCAD | 1.1.x（`freecadcmd`） | M1 网关验收、A 路刀路复算，以及 `make step-stl` 导出二进制 STL |
| opencamlib | 2023.1.11（LGPL-2.1） | `scripts/m6_opencamlib.py` 第三方 CAM 对照 + 两个补充脚本；**无 GUI** |
| CAMotics | 1.2.0（`camsim.exe`） | 仅 `scripts/m3_camsim_xcheck.py` 交叉验证需要 |

```bash
pip install numpy pytest matplotlib pillow
pip install opencamlib==2023.1.11   # 免费、非 GUI 的第三方 CAM 内核，替代 Fusion 360
# FreeCAD 便携版解压后，让适配器能找到：
export FREECAD_CMD="E:/FreeCAD_1.1.4-Windows-x86_64-py311/bin/freecadcmd.exe"
make doctor     # 打印当前生效的内核路径与版本
```

> **坑（实测）**：Windows 上 PATH 里的第一个 `python` 可能没装 pytest。
> `make test` 报 `No module named pytest` 时，用 `make test PY=/d/Anaconda3/python`。

---

## 2 分阶段复现

| 命令 | 复现什么 | 依赖 | 耗时 |
|---|---|---|---|
| `make freeze` | 数据集 v1 的 sha256 清单 | 无 | <1 s |
| `make demo` | 单件全链路（含编排修复） | numpy/matplotlib | ~20 s |
| `make m3` | 工艺库钳制、工序 DAG、G-code、注入缺陷检出 | numpy | ~10 s |
| `make m4` | 三组对照（20 个任务） | numpy | ~47 s |
| `make bench` | 上述 + A 路复算 + 汇总台账 | 上述全部 | ~2 min |
| `make figures` | 6 张主图（png + pdf） | matplotlib | ~10 s |
| `make external-stats` | 外部集逐件统计（Wilson 区间、McNemar、阈值敏感性） | numpy | <1 s |
| `make m1` | MCP 网关：建块→挖腔→快照→回滚 | **FreeCAD** | ~30 s |
| `make m2` | 标签对齐、合成集识别、真实件零样本 | **FreeCAD** | ~1 min |
| `make train-mfr` | MFCAD++ 重新训练 | **torch + GPU** | 数十分钟起 |
| `make eval-mfr` | 在测试集上评测 | torch | ~2 min |
| `python scripts/m6_opencamlib.py` | 第三方 CAM 内核对照 + 分辨率收敛（**替代 Fusion 360，无 GUI**） | `opencamlib` | ~1 min |
| `make step-stl` | STEP→二进制 STL→OCL 独立几何链路（10 件） | `freecadcmd` + `opencamlib` | ~10 s |
| `make ocl-external` | 第三方判据扩到 130 件外部集 + 半格余量对照 | `opencamlib` | ~18 min |
| `make ocl-point-cutter` | 点刀半径敏感性（10 + 130 件 × 3 档），解释 71 mm 探针伪影 | `opencamlib` | ~30 s |
| `make autonomy-probe` | B7 补充：模型直写 G-code 的验证器投影乐观上界，**零 LLM 调用**（已进 `make bench` 重算） | 无额外依赖 | ~5 s |
| `python scripts/m3_camsim_xcheck.py` | CAMotics 独立仿真，体积差 1.15% | `camsim.exe` | ~1 min |

> `make ocl-external` 耗时长，**不进 `make reproduce` / `reproduce.sh`**；
> 报告已存档在 `reports/m6_ocl_external.json`，`make bench` 只做汇总。
> 同理 `make step-stl` 需要 FreeCAD 二进制，但产物 `reports/m6_step_stl/` 已随仓库提供。

### 可选：LLM 实验（需要本地模型）

```bash
# 一次性准备（Windows 原生；WSL 的 wsl.exe 在本机安全策略里被拉黑）
#   便携包 1.5GB → 解压 → 起服务 → 拉 Qwen3-8B（5.2GB）
OLLAMA_MODELS=E:/tools/ollama/models OLLAMA_KEEP_ALIVE=30m /e/tools/ollama/ollama.exe serve
/e/tools/ollama/ollama.exe pull qwen3:8b

PYTHONPATH=src python scripts/m5_llm_eval.py      # 六组对照，约 2–3 分钟
PYTHONPATH=src python scripts/m5_context_eval.py  # C2 token 实测，约 1 分钟
```

不开后端也能验证接入是否正确：`pytest tests/test_agents.py`（10 条，全用假客户端）。
LLM 实验**不纳入 `make bench`**——它依赖外部服务且不保证逐位可复现（`temperature=0` 仍受批处理影响）。

### 不重跑的部分（如实标注）

`make bench` 把指标分成两类：

- **重算**（`steps`）：M3 三路刀路、M4 三组对照——每次跑都重新仿真。
- **存档**（`archived`）：M2 识别指标、M1 网关验收、M3.1 自检——bench 只检查文件在不在，
  并给出各自的复现命令。M2 重新训练需要 GPU 小时，不能在 bench 里假装跑过。

---

## 3 复现清单（对论文逐表）

| 论文位置 | 数字 | 来源文件 | 重算命令 |
|---|---|---|---|
| §5.2 表 | 0.9660 / 0.9042 | `reports/mfr_test_v2.json` | `make train-mfr && make eval-mfr` |
| §5.3 表 | 196 726.7 → 0 mm³；0.206 mm | `reports/m3_heightmap_eval.json` | `make bench` |
| §5.3 表 | A 路 215 126.7 mm³、2/10 | `reports/m3_cam_eval.json` | `make bench` |
| §5.4 图 | 栅格 0.5/1.0 的 6/8/10 → 0/0/0 | `reports/m3_criterion_analysis.json` | `python scripts/m3_criterion_analysis.py` |
| §5.5 表 | 0.100 / 1.000 / 0.90 / 0.15 | `reports/m4_run.json` | `make m4` |
| §5.6 表 | 65 工序 / 2 271 行 / 检出 10-10-10 | `reports/m3_acceptance.json` | `make m3` |
| §5.7 | 1.000 vs 平凡基线 1.0000 | `reports/m3_human/ceiling_baseline.json` | `python scripts/m3_human_baseline.py` |
| §5.3b | 130/130；体积差 1.15%；B3 0/10 | `reports/m3_external_eval.json`、`m3_camsim_xcheck.json`、`m3_b3_baseline.json` | `docs/M6-EXTERNAL.md` §8 |
| §5.3b | OCL 一致 ≥99.828%；B2/B3 0.76%/10.59%；收敛 1.332→0.432→0.143% | `reports/m6_ocl.json` | `python scripts/m6_opencamlib.py` |
| §5.3b | STEP→STL→OCL：两曲面一致 ≥99.984%、B2/B3 差 <0.15% | `reports/m6_step_stl_xcheck.json` | `make step-stl` |
| §5.3b | 130 件外部集 OCL 过切分类与半格余量取舍（图 6） | `reports/m6_ocl_external.json` | `make ocl-external` |
| §5.3b | 点刀缩径后超差柱 199 → 2 → 1（71 mm 是探针伪影） | `reports/m6_ocl_point_cutter.json` | `make ocl-point-cutter` |
| §0.2 / §5.6 | 自主性层级 L0–L4；投影上界双线 0/10 → 0/10、平均残留 25 503 → 25 623 mm³（覆盖规划缺失，非下刀深度） | `reports/m5_autonomy_probe.json` | `make autonomy-probe` |
| §5.4 | Wilson 95% CI：10/10 → [0.7225, 1.0000]、130/130 → [0.9713, 1.0000] | `reports/bench.json::wilson_95ci` | `make bench` |

**确定性**：全流程无随机采样。M4 的黑板指纹（`blackboard_digest`）在同输入下逐位一致，
测试 `test_run_is_deterministic` 锁住这条。唯一的种子是人工题目包的题序
（`order_seed = 20261107`），与本文指标无关。

---

## 4 常见失败与处置

| 现象 | 原因 | 处置 |
|---|---|---|
| `No module named pytest` | 用错解释器 | `make test PY=/d/Anaconda3/python` |
| `No module named cadcam` | 忘了 `PYTHONPATH` | Makefile 已导出；手工跑脚本时写 `PYTHONPATH=src python ...` |
| `make m1` 报 `freecadcmd not found` | 未装 FreeCAD / 路径未设 | `export FREECAD_CMD=...`，或跳过（M1 不影响 M3/M4 数字） |
| 出图中文变方块 | 缺中文字体 | 需要 `Microsoft YaHei` 或 `SimHei`；Linux 装 Noto Sans CJK 后改 `scripts/figures.py` 的字体列表 |
| `bench` 的 M2 行显示 `MISSING` | 训练产物未随仓库提供 | 按 §2 跑 `train-mfr`，或忽略（论文数字已在 `mfr_test_v2.json` 存档） |
| FreeCAD 脚本静默失败但 exit 0 | `freecadcmd` 会吞异常 | 适配器已把 traceback 写进输出 JSON；查 `build/*/worker_out.json` |

---

## 5 目录速查

```
src/cadcam/
  modalities/brep/     B-Rep 摘要（L0/L1/L2）
  modalities/mfr/      面级特征识别（GNN + 规则 + 实例分组）
  modalities/cam/      工艺库、工序 DAG、G-code 生成、区域掩码刀路、G-code 解析
  verify/              Z-dexel 材料去除、DFM、结构化错误
  orchestrator/        状态机、黑板、六角色、有界修复、情节记忆
  gateway/             MCP 网关（契约、事务、快照、回滚）
scripts/               freeze / bench / figures / demo / 各里程碑验收
reports/               全部数字产物（json）+ 主图（figures/）
docs/                  计划、决策（ADR）、报告、论文初稿、数据卡
```
