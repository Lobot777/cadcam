"""G 代码解析器的行为契约。

这些用例刻意不依赖外部数据文件：解析器是后续所有制造侧指标的地基，
一旦回归，论文里的空切比、工序数、Z 深度全部失真。
"""

from cadcam.modalities.cam import ngc

SIMPLE = """%
(BLANK)
N10 G90 G94 G17 G91.1
N15 G21
N20 T1 M6
N25 S8000 M3
(FACE)
N30 G54
N35 G0 X0 Y0 Z10
N40 G1 Z-2 F300
N45 X50 F800
N50 G2 X60 Y10 I10 J0
N55 G0 Z10
(MILL)
N60 G0 X0 Y0
N65 G83 Z-8 R2 Q2 F120
N70 M5
N75 M30
%
"""


def test_absolute_simple():
    result = ngc.parse(SIMPLE)
    assert result["blocks"] > 10
    assert result["tools"] == [1]
    stats = result["stats"]
    assert stats["z_min"] == -8.0
    assert stats["cut_mm"] > 0
    assert stats["air_cut_ratio"] is not None


def test_operations_split_by_comment():
    result = ngc.parse(SIMPLE)
    labels = [op["label"] for op in result["operations"]]
    assert "FACE" in labels
    assert "MILL" in labels


def test_incremental_mode():
    text = """%
G90 G21
T1 M6
S1000 M3
G0 X0 Y0 Z0
G91
G1 X10 F100
G1 X10
G90
%
"""
    result = ngc.parse(text)
    last = [s for s in result["segments"] if s["kind"] == "linear"][-1]
    assert last["to"][0] == 20.0


def test_arc_length_quarter_circle():
    # 起点 (0,0)，圆心 (10,0)，终点 (10,10)：顺时针 = 90°（弧长 15.7），逆时针 = 270°（弧长 47.1）
    text = """%
G90 G21 G17
T1 M6
S1000 M3
G0 X0 Y0 Z0
G1 Z-2 F100
G2 X10 Y10 I10 J0 F500
%
"""
    result = ngc.parse(text)
    arcs = [s for s in result["segments"] if s["kind"] == "arc_cw"]
    assert len(arcs) == 1
    assert 14.0 < arcs[0]["length"] < 17.0


def test_arc_direction_changes_sweep():
    text = """%
G90 G21 G17
T1 M6
S1000 M3
G0 X0 Y0 Z0
G1 Z-2 F100
G3 X10 Y10 I10 J0 F500
%
"""
    result = ngc.parse(text)
    arcs = [s for s in result["segments"] if s["kind"] == "arc_ccw"]
    assert len(arcs) == 1
    assert 45.0 < arcs[0]["length"] < 49.0


def test_unknown_code_recorded():
    text = """%
G90 G21
T1 M6
S1000 M3
G0 X0 Y0
G123 X10
M99
%
"""
    result = ngc.parse(text)
    assert "G123" in result["unknown_codes"]
    assert "M99" in result["unknown_codes"]


def test_plane_tracking():
    text = """%
G90 G21
T1 M6
S1000 M3
G18 G3 X0 Z-5 I2 K0 F200
%
"""
    result = ngc.parse(text)
    assert "G18" in result["stats"]["planes"]
