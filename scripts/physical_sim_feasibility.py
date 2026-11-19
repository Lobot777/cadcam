#!/usr/bin/env python
"""Decide whether a candidate platform can replace our verification backend.

This is a documentation-grounded capability audit, not a physics experiment.
It records which capabilities the material-removal verification chain requires
and which of them each candidate platform actually provides.

Nothing is installed and no model is called.  Running this script only reads
the declarations below and writes a deterministic JSON report, so the negative
result can be reproduced on a machine without CUDA, Rust or a CAD licence.

Usage:
    PYTHONPATH=src python scripts/physical_sim_feasibility.py
    PYTHONPATH=src python scripts/physical_sim_feasibility.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "physical_sim_feasibility.json"

# The verification chain in this paper cannot be substituted by a generic
# simulator unless ALL of these are present.  Each one is a necessary
# condition; together they are the contract a backend must satisfy.
REQUIRED_CAPABILITIES = {
    "b_rep_ingest": "Import STEP or equivalent boundary-representation solids",
    "cutter_sweep": "Execute a defined cutter geometry along a prescribed path",
    "removal_volume": "Compute removed or remaining material volume from the sweep",
    "gouge_judgement": "Judge excess removal against a target region and tolerance",
    "operation_attribution": "Attribute a violation to an individual operation id",
    "deterministic_replay": "Replay the same program with a reproducible outcome",
}

# Status values: supported | partial | absent
# Evidence type is explicit: "documentation" means the claim comes from the
# project's own published documentation, not from a locally executed test.
CANDIDATES = [
    {
        "id": "genesis_world",
        "name": "Genesis World",
        "role_claimed_by_cfp": "multimodal agents / embodied intelligence / tool use",
        "evidence_type": "documentation",
        "source": "https://github.com/Genesis-Embodied-AI/genesis-world",
        "capabilities": {
            "b_rep_ingest": (
                "absent",
                "Asset parsers listed: URDF, MJCF, OBJ, GLB, USD; no STEP or B-rep parser.",
            ),
            "cutter_sweep": (
                "absent",
                "Physics covers rigid, FEM, MPM, particle, uipc and SAP; no CNC cutter-sweep primitive.",
            ),
            "removal_volume": (
                "absent",
                "State is positions/deformations/concentrations, not a subtractive solid; "
                "the MPM 'cut dragon' example cuts deformable material, not stock removal.",
            ),
            "gouge_judgement": (
                "absent",
                "No target-region or overcut predicate exists, so no reward or label for "
                "'material that should have survived' can be defined.",
            ),
            "operation_attribution": (
                "absent",
                "No operation-graph concept in the simulation interface.",
            ),
            "deterministic_replay": (
                "partial",
                "GPU simulation is seedable in principle, but no machining-program replay "
                "interface is published.",
            ),
        },
        "training_readiness": (
            "Not trainable for this task: reinforcement learning requires a scalar reward "
            "for removed volume or gouge depth, and neither quantity is represented in the "
            "simulator state. Building them would be a new research project, not an "
            "integration step."
        ),
    },
    {
        "id": "solidcraft",
        "name": "SolidCraft (SolidCore)",
        "role_claimed_by_cfp": "MCP tool use / agentic CAD modelling",
        "evidence_type": "documentation",
        "source": "https://solidcraft.io/",
        "capabilities": {
            "b_rep_ingest": (
                "supported",
                "Imports/exports STEP and mesh; SolidCore is a Rust port of NVIDIA SMLib.",
            ),
            "cutter_sweep": (
                "absent",
                "Feature timeline is sketch/extrude/revolve/fillet; no milling operation or cutter motion.",
            ),
            "removal_volume": (
                "absent",
                "Oracle checks volume, area, centre of mass and bounding box of modelled parts, "
                "not a subtractive machining result.",
            ),
            "gouge_judgement": (
                "absent",
                "No masked region, tolerance band or overcut predicate.",
            ),
            "operation_attribution": (
                "partial",
                "Editable feature history exists, but for modelling features, not machining operations.",
            ),
            "deterministic_replay": (
                "partial",
                "Parametric rebuild is deterministic; no NC-program replay is defined.",
            ),
        },
        "training_readiness": (
            "Not trainable for this task: it is a modeller, not a machining environment; "
            "it offers no state transition driven by a toolpath."
        ),
    },
    {
        "id": "dexel_ocl_camotics",
        "name": "Z-dexel + OpenCAMLib + CAMotics (current chain)",
        "role_claimed_by_cfp": "evaluation protocol / trustworthy manufacturing grounding",
        "evidence_type": "local_report",
        "source": "reports/m6_ocl_external.json; reports/m3_camsim_xcheck.json; reports/m6_external_stats.json",
        "capabilities": {
            "b_rep_ingest": (
                "supported",
                "STEP read via FreeCAD and triangulation exported to STL for finite-cutter oracle.",
            ),
            "cutter_sweep": (
                "supported",
                "OpenCAMLib CylCutter dropCutter over sampled tool-centre positions.",
            ),
            "removal_volume": (
                "supported",
                "Z-dexel removal simulation plus CAMotics remaining-volume cross-check (1.15% mean relative difference).",
            ),
            "gouge_judgement": (
                "supported",
                "Dual-line acceptance and finite-cutter overcut depth against a 0.5 mm line; "
                "verifier blind spot quantified over 130 external parts.",
            ),
            "operation_attribution": (
                "supported",
                "Structured E_GOUGE errors carry operation ids back to the planner.",
            ),
            "deterministic_replay": (
                "supported",
                "Grid-based replay from stored G-code; figures and reports regenerated by scripts/reproduce.sh.",
            ),
        },
        "training_readiness": (
            "Not a learning environment either. It is a verifier: the contribution is an "
            "auditable acceptance protocol, not a trained policy."
        ),
    },
]


def evaluate() -> dict:
    rows = []
    for candidate in CANDIDATES:
        missing = [
            key
            for key in REQUIRED_CAPABILITIES
            if candidate["capabilities"][key][0] != "supported"
        ]
        rows.append(
            {
                "id": candidate["id"],
                "name": candidate["name"],
                "eligible_as_verification_backend": not missing,
                "missing_capabilities": missing,
                "capabilities": {
                    key: {"status": value[0], "evidence": value[1]}
                    for key, value in candidate["capabilities"].items()
                },
                "training_readiness": candidate["training_readiness"],
                "evidence_type": candidate["evidence_type"],
                "source": candidate["source"],
            }
        )
    return {
        "required_capabilities": REQUIRED_CAPABILITIES,
        "decision_rule": "A backend is eligible only if all required capabilities are supported.",
        "candidates": rows,
        "conclusion": (
            "General-purpose robotics simulation and parametric CAD do not satisfy the "
            "material-removal verification contract. This is a scope boundary, not a "
            "claim that either platform is defective at its intended task."
        ),
    }


def render(report: dict) -> str:
    lines = ["candidate                         eligible  missing"]
    for row in report["candidates"]:
        lines.append(
            "%-33s  %-8s  %s"
            % (
                row["id"],
                "yes" if row["eligible_as_verification_backend"] else "no",
                ", ".join(row["missing_capabilities"]) or "-",
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if any eligibility decision changes (for tests and CI)",
    )
    args = parser.parse_args()

    report = evaluate()
    print(render(report))
    print()
    print(report["conclusion"])

    if args.check:
        expected = {
            "genesis_world": False,
            "solidcraft": False,
            "dexel_ocl_camotics": True,
        }
        actual = {
            row["id"]: row["eligible_as_verification_backend"]
            for row in report["candidates"]
        }
        if actual != expected:
            print("CHECK FAILED: eligibility changed: %r" % actual, file=sys.stderr)
            return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\nwrote %s" % OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
