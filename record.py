"""Record real model trajectories for the demo.

    .venv/bin/python record.py            # all variants
    .venv/bin/python record.py refund     # only variants whose name starts with "refund"

Each variant runs the live model (OpenRouter, key from .env) through the real
harness several times and stores every assistant turn. The app replays these
turns; it never invents one.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from harness.core import MODEL, Flags, RecordingClient, leak_summary, run_scenario

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "recordings"

# name -> (scenario, flags, number of live runs)
VARIANTS = {
    "refund__intact": ("refund", Flags(), 3),
    "refund__instruction_only": ("refund", Flags(governance=False), 5),
    "refund__instruction_only_t1": ("refund", Flags(governance=False, temperature=1.0), 10),
    "refund__enforcement_only": ("refund", Flags(policy_in_context=False), 3),
    "refund__neither": ("refund", Flags(policy_in_context=False, governance=False), 5),
    "refund_forged__instruction_only": ("refund_forged", Flags(governance=False), 5),
    "refund_forged__intact": ("refund_forged", Flags(), 3),
    "approved_refund__intact": ("approved_refund", Flags(), 3),
    "erasure__no_scoping": ("erasure", Flags(tool_scoping=False), 5),
    "erasure__intact": ("erasure", Flags(), 3),
    "social__no_boundary": ("social", Flags(skill_admission=False, skill_enforcement=False), 5),
    "social__enforced": ("social", Flags(skill_admission=False), 3),
}


def live_client() -> OpenAI:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY is missing. Put it in talk_app/.env")
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


def outcome(result) -> dict:
    a, b = result.after, result.before
    return {
        "paid_out_usd": None if a["balance_usd"] is None else round(b["balance_usd"] - a["balance_usd"], 2),
        "orders_rows": a["orders"], "cards_rows": a["cards"], "customers_rows": a["customers"],
        "leak": leak_summary(result.outbound), "posts": len(result.posts),
        "status": (result.resolution or {}).get("status"), "escalated": result.escalated,
        "event_kinds": [e["kind"] for e in result.events],
    }


def main() -> None:
    client = live_client()
    OUT.mkdir(exist_ok=True)
    wanted = sys.argv[1] if len(sys.argv) > 1 else ""
    for name, (scenario, flags, n) in VARIANTS.items():
        if not name.startswith(wanted):
            continue
        runs = []
        for i in range(n):
            rec = RecordingClient(client)
            try:
                result = run_scenario(scenario, flags, lambda: rec)
                runs.append({"turns": rec.turns, "outcome": outcome(result)})
                o = runs[-1]["outcome"]
                print(f"{name} #{i + 1}: paid={o['paid_out_usd']} orders={o['orders_rows']} "
                      f"cards={o['cards_rows']} leak={o['leak']} status={o['status']}")
            except Exception as exc:                      # keep the evidence of a failed run too
                runs.append({"turns": rec.turns, "error": repr(exc)})
                print(f"{name} #{i + 1}: ERROR {exc!r}")
        (OUT / f"{name}.json").write_text(json.dumps(
            {"model": MODEL, "scenario": scenario, "flags": asdict(flags), "runs": runs}, indent=1))


if __name__ == "__main__":
    main()
