PY ?= python
export PYTHONPATH := src

.PHONY: help test lint doctor summary baseline ab m1 m2 m3 m4 demo freeze bench \
        figures reproduce external-stats llm-eval llm-context train-mfr eval-mfr mcp ocl \
        ocl-external step-stl ocl-point-cutter autonomy-probe clean

help:
	@echo "make test        全量测试"
	@echo "make demo        端到端演示（单件，约 20 秒，不需要 FreeCAD）"
	@echo "make m1|m2|m3|m4 各里程碑验收脚本"
	@echo "make freeze      冻结数据集 v1（sha256 清单）"
	@echo "make bench       一键复现论文数字（约 2 分钟，出 bench.json/md）"
	@echo "make figures     生成 6 张主图（reports/figures）"
	@echo "make external-stats 外部集逐件统计（Wilson/McNemar/阈值敏感性）"
	@echo "make physical-sim-audit 物理仿真平台接入判定（负结果，零依赖）"
	@echo "make reproduce   freeze + bench + figures + test"
	@echo "make llm-eval    接本地 LLM 的四组对照（需先 ollama serve）"
	@echo "make llm-context C2 上下文 token 实测（需先 ollama serve）"
	@echo "没有 make？用 bash scripts/reproduce.sh（Windows Git Bash 默认不带 make）"
	@echo "make ocl         免费非 GUI 的第三方 CAM 对照（需 pip install opencamlib）"
	@echo "make step-stl    STEP→STL→OCL 独立几何链路（约 10 秒，需 freecadcmd）"
	@echo "make ocl-external 130 件外部集 OCL 交叉验证（约 18 分钟，需 opencamlib）"
	@echo "make ocl-point-cutter 点刀半径敏感性（约 30 秒，解释 71mm 探针伪影）"
	@echo "make autonomy-probe B7 自由生成 G-code 的验证器投影上界（约 5 秒，零 LLM 调用）"
	@echo "make doctor      打印几何内核与依赖版本"

test:
	$(PY) -m pytest tests -q

lint:
	$(PY) -m ruff check src tests

doctor:
	$(PY) -c "import sys; sys.path.insert(0,'src'); from cadcam.adapters import freecad; print('cmd    ', freecad.FREECAD_CMD); print('version', freecad.engine_version())"

summary:
	$(PY) -m cadcam.cli summary data/zenodo_rptu/1.step

baseline:
	$(PY) scripts/baseline.py

# 双内核 A/B 回归：拓扑逐位相等 + 指标偏差 < 0.1%
ab:
	$(PY) scripts/ab_kernel.py

# M1 端到端验收：建块 -> 挖腔 -> 快照 -> 回滚 + 契约拦截
m1:
	$(PY) scripts/m1_demo.py

# M2 端到端验收：标签对齐 -> 合成集识别 -> 真实件零样本 -> 网关契约
m2:
	$(PY) scripts/m2_demo.py

# M3 端到端验收：工艺库钳制 -> 工序 DAG -> G-code -> 仿真 -> 缺陷注入检出
m3:
	$(PY) scripts/m3_demo.py

# M4 三组对照：不修复 / 修复+冷记忆 / 修复+热记忆
m4:
	$(PY) scripts/m4_run.py

demo:
	$(PY) scripts/demo.py

freeze:
	$(PY) scripts/freeze_dataset.py

bench:
	$(PY) scripts/bench.py

figures:
	$(PY) scripts/figures.py

# 外部集逐件统计：Wilson 区间、配对 McNemar、阈值敏感性与覆盖率分层
external-stats:
	$(PY) scripts/m6_external_stats.py

# 物理仿真/建模平台能否替代验证后端的能力审计（文档判定，不装依赖）
physical-sim-audit:
	$(PY) scripts/physical_sim_feasibility.py --check

# 第三方 CAM 内核对照：替代 Fusion 360，纯命令行、无 GUI（可选依赖）
ocl:
	$(PY) scripts/m6_opencamlib.py

# 补充：把第三方判据扩到 130 件外部集（耗时长，不放进 reproduce）
ocl-external:
	$(PY) scripts/m6_ocl_external.py

# 补充：另一条几何链路，从 STEP 经二进制 STL 到 OCL（绕开 geom JSON 缓存）
step-stl:
	$(PY) scripts/m6_step_stl_xcheck.py

# 补充：点刀半径敏感性（10 + 130 件 × 3 档，约 30 秒）
ocl-point-cutter:
	$(PY) scripts/m6_ocl_point_cutter.py

# B7 补充：模型直写 G-code 的验证器投影乐观上界（零 LLM 调用，已进 bench 重算）
autonomy-probe:
	$(PY) scripts/m5_autonomy_probe.py

# 需要本地 LLM 后端（ollama serve）与已拉取的模型
llm-eval:
	$(PY) scripts/m5_llm_eval.py

llm-context:
	$(PY) scripts/m5_context_eval.py

reproduce: freeze bench figures test
	@echo "复现完成：reports/bench.md 汇总了所有可引用的数字"

# 训练 / 评测特征识别器（需 torch + GPU；bench 不重跑，只引用产物）
train-mfr:
	$(PY) scripts/train_mfr.py --epochs 40 --hidden 256

eval-mfr:
	$(PY) scripts/eval_mfr.py

# 以 MCP stdio 服务器启动网关
mcp:
	$(PY) -m cadcam.gateway.server

clean:
	rm -rf reports/*.json reports/figures .pytest_cache
