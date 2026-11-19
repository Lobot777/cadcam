# bench 台账（自动生成，勿手改）

> 生成时间：2026-11-09 10:44:17　｜　Python：3.11.5
> 重跑：`make bench`　｜　只汇总：`PYTHONPATH=src python scripts/bench.py --reuse`

## 1 重算步骤

| 步骤 | 说明 | 状态 | 耗时 |
|---|---|---|---|
| `m3_cam` | A 路 FreeCAD CAM 参考刀路复算 | ok | 6.2s |
| `m3_heightmap` | B1 包围盒 vs B2 区域掩码复算 | ok | 10.6s |
| `m4` | M4 三组对照（20 个任务） | ok | 46.4s |
| `m5_autonomy` | B7 补充：自由生成 G-code 的验证器投影乐观上界（零 LLM 调用） | ok | 1.2s |

## 2 存档产物（未在本次重跑）

| 产物 | 说明 | 复现命令 | 状态 |
|---|---|---|---|
| `mfr_test_v2.json` | M2 GNN 面级识别（训练需 GPU，未在 bench 重跑；权重→指标已用 eval_mfr 复算，逐位一致） | `python scripts/eval_mfr.py --weights reports/mfr_train_v2.pt` | present |
| `m1_acceptance.json` | M1 MCP 网关验收（需 FreeCAD 二进制） | `python scripts/m1_demo.py` | present |
| `m3_acceptance.json` | M3.1 工艺库/注入自检 | `python scripts/m3_demo.py` | present |
| `m3_external_eval.json` | MFCAD 130 件外部验证集（双线判据） | `PYTHONPATH=src python scripts/external_eval.py` | present |
| `m3_camsim_xcheck.json` | CAMotics 独立仿真，体积交叉验证 | `PYTHONPATH=src python scripts/m3_camsim_xcheck.py` | present |
| `m3_b3_baseline.json` | B3 无膨胀基线（零过切归因） | `PYTHONPATH=src python scripts/m3_b3_baseline.py` | present |
| `m6_ocl.json` | OpenCAMLib 第三方 CAM 对照（替代 Fusion 360，无 GUI） | `PYTHONPATH=src python scripts/m6_opencamlib.py` | present |
| `m6_ocl_external.json` | OpenCAMLib × 130 件外部集交叉验证（含半格余量对照） | `PYTHONPATH=src python scripts/m6_ocl_external.py` | present |
| `m6_step_stl_xcheck.json` | STEP→FreeCAD Mesh→二进制 STL→OCL 独立几何链路 | `PYTHONPATH=src python scripts/m6_step_stl_xcheck.py` | present |
| `m6_ocl_point_cutter.json` | OCL 点刀半径敏感性（解释 71mm 探针伪影） | `PYTHONPATH=src python scripts/m6_ocl_point_cutter.py` | present |

## 3 关键数字（论文引用口径）

| 指标 | 值 |
|---|---|
| M2 GNN 面级准确率 | `0.966` |
| M2 GNN mIoU | `0.9042` |
| M2 决策树基线准确率 | `0.9075` |
| M2 决策树 mIoU | `0.7198` |
| B1 包围盒总过切 (mm³) | `196726.7` |
| B2 区域掩码总过切 (mm³) | `0.0` |
| B2 最坏平均绝对偏差 (mm) | `0.2059` |
| B2 双线通过件数（共 10） | `10` |
| B2 旧 max 口径通过件数 | `0` |
| A 路双线通过件数（共 10） | `2` |
| A 路总过切 (mm³) | `215126.7` |
| M4 不修复通过率 | `0.1` |
| M4 有界修复通过率 | `1.0` |
| M4 冷启动平均修复轮次 | `0.9` |
| M4 热启动平均修复轮次 | `0.15` |
| m3_whitelist_all_ok | `True` |
| m3_inject_detected | `[10, 10, 10]` |
| M6 MFCAD 外部 130 件双线通过数 | `130` |
| M6 CAMotics 体积相对差均值 (%) | `1.15` |
| M6 B3 无膨胀基线双线通过件数（共 10） | `0` |
| M6 OpenCAMLib 目标几何逐柱一致率下限 (%) | `99.828` |
| M6 OCL 判 B2 过切刀心点数 | `2822` |
| M6 OCL 判 B3 过切刀心点数 | `43260` |
| M6 OCL 自生成刀路双线通过件数（共 10） | `9` |
| M6 分辨率收敛过切率 (res 0.5/0.25/0.125, %) | `[1.332, 0.432, 0.143]` |
| M6 OCL×外部集评测件数 | `130` |
| M6 OCL×外部集几何逐柱一致率下限 (%) | `87.364` |
| M6 OCL 判外部集 B2 过切点数 | `316398` |
| M6 其中深过切 (>0.5mm) 点数 | `77363` |
| M6 半格余量后剩余过切点数 | `5167` |
| M6 半格余量后双线通过件数 | `107` |
| M6 STEP→STL 两条 OCL 曲面一致率下限 (%) | `99.9844` |
| M6 STL 链路判 B2 过切点数 | `2826` |
| M6 STL 链路判 B3 过切点数 | `43262` |
| M6 点刀 Ø0.02 时几何差>0.5mm 的柱 | `199` |
| M6 点刀 Ø0.002 时几何差>0.5mm 的柱 | `2` |
| M6 点刀 Ø0.0002 时几何差>0.5mm 的柱 | `1` |
| B7 模型直写 G-code 双线通过件数（共 10） | `0` |
| B7 验证器投影后双线通过件数（共 10） | `0` |
| B7 投影前后平均残留体积 (mm³) | `[25503.0, 25623.1]` |
| B7 投影前后平均最深过切 (mm) | `[10.596, 4.996]` |

## 3b 小样本通过率的 Wilson 95% 置信区间

> 10/10 不等于 100%：下界只有 0.7225。论文只写 k/n，不写百分比。

| 结论 | k/n | 点估计 | Wilson 95% CI |
|---|---|---|---|
| B2 区域掩码双线通过 | 10/10 | 1.000 | [0.7225, 1.0000] |
| A 路 FreeCAD CAM 双线通过 | 2/10 | 0.200 | [0.0567, 0.5098] |
| B3 无膨胀基线双线通过 | 0/10 | 0.000 | [0.0000, 0.2775] |
| MFCAD 外部集双线通过 | 130/130 | 1.000 | [0.9713, 1.0000] |
| OCL 自生成刀路双线通过 | 9/10 | 0.900 | [0.5958, 0.9821] |
| M4 A_no_repair | 2/20 | 0.100 | [0.0279, 0.3010] |
| M4 B_repair_cold | 20/20 | 1.000 | [0.8389, 1.0000] |

## 4 测试

```
177 passed in 15.17s
```
