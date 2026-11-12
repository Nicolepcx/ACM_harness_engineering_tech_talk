"""Turn the trace of the intact run into a step-by-step account of which
component acted, what it checked and what it handed on.

Nothing here is invented: tool names, arguments, results and the final answer
come from the run's events, the governor's facts are read from the shop
database and the scenario, and the effect key is computed the way the gateway
computes it. Permitted governance decisions are not trace events in this
harness, so those lines restate the rule in ApprovalGovernor.decide.
"""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from . import shop
from .core import (REFUND_APPROVAL_LIMIT, REFUND_POLICY, SCENARIOS, SIDE_EFFECTING, SUPPORT_SYSTEM,
                   RunResult)

ROLES = {
    "Identity & behavior": "who the agent is and the shape of its answer",
    "Context & memory": "what the model gets to read",
    "Orchestration": "runs the loop, routes every proposal",
    "Model": "proposes the next step",
    "Governance & security": "decides whether a proposal may become an action",
    "Tools & execution": "the only path to an effect",
    "Observability": "writes down what happened",
    "Verification": "checks the answer against the record",
    "Adaptation": "improves the harness from traces, between runs",
}
ORDER = ["Identity & behavior", "Context & memory", "Orchestration", "Model", "Governance & security",
         "Tools & execution", "Observability", "Verification", "Adaptation"]


def _tok(text: str) -> int:
    return max(1, round(len(text) / 4))


def _usd(x: float) -> str:
    return f"${x:,.0f}"


def build_steps(result: RunResult, scenario_key: str = "approved_refund") -> list[dict[str, Any]]:
    sc = SCENARIOS[scenario_key]
    system = SUPPORT_SYSTEM.replace("{policy}", REFUND_POLICY)
    schemas = json.dumps([shop.SCHEMAS[n] for n in sc.tools])
    db = shop.build_shop()

    steps: list[dict[str, Any]] = [{
        "active": ["Identity & behavior", "Context & memory"],
        "title": "The harness assembles what the model will read",
        "lines": [
            ("in", f"Role and answer contract: {_tok(system) - _tok(REFUND_POLICY)} tokens"),
            ("in", f"Refund policy: {_tok(REFUND_POLICY)} tokens"),
            ("in", f"Ticket, with the customer the harness authenticated ({sc.principal}): {_tok(sc.ticket)} tokens"),
            ("in", f"{len(sc.tools)} tool schemas: {', '.join(sc.tools)}"),
        ],
        "hands": "A prompt the model can act on. No raw SQL tool is on the list.",
        "contrib": {"Identity & behavior": "role + JSON answer contract",
                    "Context & memory": "policy and ticket in the prompt"},
        "removed": ["Context", "Tool scope"],
    }]

    # Group the trace into model calls and the tool events that follow each one.
    calls: list[dict[str, Any]] = []
    for e in result.events:
        if e["kind"] == "model_call_completed":
            calls.append({"step": e["payload"]["step"], "tools": [], "seen": 0})
        elif e["kind"] == "tool_completed" and calls:
            calls[-1]["tools"].append(e["payload"])
    seen = 0
    for call in calls:
        tools = call["tools"]
        if not tools:                                   # the final answer
            res = result.resolution or {}
            steps.append({
                "active": ["Orchestration", "Model"],
                "title": f"Model call {call['step']}: the model proposes its answer",
                "lines": [("say", f"status: {res.get('status', '?')}"),
                          ("say", "actions_taken: " + ", ".join(res.get("actions_taken", [])))],
                "hands": "Still a proposal. Nothing is sent to the customer yet.",
                "contrib": {"Orchestration": f"{call['step']} model calls routed", "Model": "proposed the answer"},
                "removed": [],
            })
            observed = sorted({e["payload"]["tool"] for e in result.events if e["kind"] == "tool_completed"})
            steps.append({
                "active": ["Verification", "Observability"],
                "title": "Verification checks the answer against the record",
                "lines": [("ok", "Answer matches the contract: status, customer_reply, actions_taken"),
                          *[("ok", f"“{a}” claimed, and the trace shows it completed")
                            for a in res.get("actions_taken", []) if any(t in a for t in observed)],
                          ("ok", f"The run closes with {len(result.events)} events on the record")],
                "hands": "Only now is the reply sent to the customer.",
                "contrib": {"Verification": f"{len(res.get('actions_taken', []))} claimed actions grounded in the trace",
                            "Observability": f"{len(result.events)} events recorded"},
                "removed": ["Observability"],
            })
            continue

        names = [t["tool"] for t in tools]
        effect = [t for t in tools if t["tool"] in SIDE_EFFECTING]
        steps.append({
            "active": ["Orchestration", "Model"],
            "title": f"Model call {call['step']}: the model proposes",
            "lines": [("say", f"{t['tool']}({', '.join(f'{k}={v}' for k, v in t['arguments'].items())})")
                      for t in tools],
            "hands": "The orchestrator does not run these. It hands each one to the tool gateway.",
            "contrib": {"Orchestration": f"{call['step']} model calls routed", "Model": "proposed " + ", ".join(names)},
            "removed": [],
        })
        seen += len(tools)
        if not effect:
            steps.append({
                "active": ["Governance & security", "Tools & execution", "Observability"],
                "title": "The gateway runs two read-only lookups",
                "lines": [("ok", "Governance: neither call moves money or deletes data, permitted"),
                          *[("ok", f"{t['tool']} ran: " + _summary(t)) for t in tools],
                          ("ok", f"Observability: {seen} tool results recorded")],
                "hands": "The results go back to the model as observations.",
                "contrib": {"Tools & execution": f"{seen} scoped tool calls", "Observability": f"{seen} tool results recorded"},
                "removed": [],
            })
            continue

        t = effect[0]
        order, amount = str(t["arguments"]["order_id"]), float(t["arguments"]["amount_usd"])
        ret = db.execute("SELECT status, received_at FROM returns WHERE order_id=?", (order,)).fetchone()
        approver = sc.approvals.get(order, "")
        steps.append({
            "active": ["Governance & security"],
            "title": f"Governance decides on the {_usd(amount)} refund",
            "lines": [("ok", f"Return for {order}: {ret[0]} at the warehouse on {ret[1]} (returns table)"),
                      ("ok", f"{_usd(amount)} is above the {_usd(REFUND_APPROVAL_LIMIT)} limit, so approval is required"),
                      ("ok", f"Approval held by this run: {approver}")],
            "hands": "Permitted. The approver's name travels with the call. The ticket text was not consulted.",
            "contrib": {"Governance & security": f"3 checks on {order}, permitted"},
            "removed": ["Governance"],
        })
        key = sha256(f"run-{scenario_key}|{t['tool']}|{json.dumps(t['arguments'], sort_keys=True)}".encode()).hexdigest()[:16]
        ledger = result.after["refund_rows"]
        steps.append({
            "active": ["Tools & execution", "Observability"],
            "title": "Execution pays out once and writes it down",
            "lines": [("ok", f"Effect key {key}: no earlier payout under this key"),
                      ("ok", f"issue_refund ran: {t['result']['status']}, {_usd(t['result']['refunded_usd'])}"),
                      ("ok", "Ledger row: " + "; ".join(f"{o} {_usd(a)}, approved by {by}" for o, a, by in ledger)),
                      ("ok", f"Observability: {seen} tool results recorded, with arguments and results")],
            "hands": "If the process dies now, a restart finds the key and replays the result instead of paying again.",
            "contrib": {"Tools & execution": f"{_usd(amount)} paid once, effect key stored",
                        "Observability": f"{seen} tool results recorded"},
            "removed": ["Execution"],
        })

    return steps


def _summary(t: dict[str, Any]) -> str:
    r = t["result"]
    if "orders" in r:
        return "; ".join(f"{o['order_id']} {o['item']} {_usd(o['amount_usd'])} ({o['status']})" for o in r["orders"])
    return ", ".join(str(v) for k, v in r.items() if k in ("id", "name"))


def contributions(steps: list[dict[str, Any]], upto: int) -> dict[str, str]:
    """What each component has done in the run up to and including step `upto`."""
    out: dict[str, str] = {"Adaptation": "not in this demo harness"}
    for s in steps[:upto + 1]:
        out.update(s["contrib"])
    return out
