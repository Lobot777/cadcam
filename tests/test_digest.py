"""B-Rep 摘要的行为契约：L0 必须稳定、L2 必须可分页、L3 必须可定位。"""

from cadcam.modalities.brep import digest


def make_geom():
    faces = [
        {"id": "aaa", "index": 1, "surface": "Plane", "area_mm2": 6400.0,
         "centroid": [0, 0, 52], "normal": [0, 0, 1], "edge_count": 4,
         "params": {"family": "Plane"}},
        {"id": "bbb", "index": 2, "surface": "Cylinder", "area_mm2": 314.0,
         "centroid": [10, 10, 45], "normal": None, "edge_count": 2,
         "params": {"family": "Cylinder", "radius": 5.0}},
    ]
    edges = [
        {"id": "e1", "curve": "Line", "length_mm": 10.0, "midpoint": [0, 0, 52],
         "faces": [1, 2], "convexity": "concave"},
        {"id": "e2", "curve": "Circle", "length_mm": 31.4, "midpoint": [10, 10, 45],
         "faces": [2], "convexity": None},
    ]
    return {
        "source": "synthetic.step",
        "bbox": {"min": [0, 0, 2], "max": [80, 80, 52]},
        "volume_mm3": 300000.0,
        "counts": {"faces": 2, "edges": 2, "solids": 1},
        "faces": faces,
        "edges": edges,
    }


def test_summary_level0():
    summary = digest.summarize(make_geom())
    assert summary["level"] == "L0"
    assert summary["size_mm"] == [80.0, 80.0, 50.0]
    assert summary["surface_histogram"] == {"Plane": 1, "Cylinder": 1}
    assert summary["convexity_histogram"] == {"concave": 1}


def test_render_l0_is_compact():
    text = digest.render_l0(digest.summarize(make_geom()))
    assert "80.0×80.0×50.0" in text
    assert len(text) < 600


def test_face_page_paginates():
    geom = make_geom()
    geom["faces"] = geom["faces"] * 15
    page = digest.face_page(geom, page=2, size=10)
    assert page["total_faces"] == 30
    assert page["total_pages"] == 3
    assert len(page["rows"]) == 10


def test_face_detail_by_index_and_id():
    geom = make_geom()
    assert digest.face_detail(geom, "aaa")["face"]["id"] == "aaa"
    assert digest.face_detail(geom, "2")["face"]["id"] == "bbb"
    assert digest.face_detail(geom, "missing")["error"] == "FACE_NOT_FOUND"
