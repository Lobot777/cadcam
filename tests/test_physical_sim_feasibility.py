import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "physical_sim_feasibility",
    ROOT / "scripts" / "physical_sim_feasibility.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_required_capabilities_are_necessary_for_verification():
    required = set(mod.REQUIRED_CAPABILITIES)
    assert required == {
        "b_rep_ingest",
        "cutter_sweep",
        "removal_volume",
        "gouge_judgement",
        "operation_attribution",
        "deterministic_replay",
    }


def test_every_candidate_has_evidence_for_every_capability():
    for candidate in mod.CANDIDATES:
        assert set(candidate["capabilities"]) == set(mod.REQUIRED_CAPABILITIES)
        for key, (status, evidence) in candidate["capabilities"].items():
            assert status in {"supported", "partial", "absent"}, (candidate["id"], key)
            assert evidence.strip(), (candidate["id"], key)


def test_robotics_simulator_and_cad_are_not_verification_backends():
    report = mod.evaluate()
    by_id = {row["id"]: row for row in report["candidates"]}
    assert by_id["genesis_world"]["eligible_as_verification_backend"] is False
    assert by_id["solidcraft"]["eligible_as_verification_backend"] is False
    assert by_id["dexel_ocl_camotics"]["eligible_as_verification_backend"] is True


def test_missing_capabilities_are_explained_not_fudged():
    report = mod.evaluate()
    gouge = report["required_capabilities"]["gouge_judgement"]
    for row in report["candidates"]:
        if row["id"] in {"genesis_world", "solidcraft"}:
            assert "gouge_judgement" in row["missing_capabilities"]
            assert row["capabilities"]["gouge_judgement"]["status"] == "absent"
    assert "target region" in gouge


def test_report_is_deterministic():
    assert mod.evaluate() == mod.evaluate()


def test_written_report_matches_evaluator():
    out = ROOT / "reports" / "physical_sim_feasibility.json"
    assert out.exists(), "run scripts/physical_sim_feasibility.py first"
    assert json.loads(out.read_text(encoding="utf-8")) == mod.evaluate()
