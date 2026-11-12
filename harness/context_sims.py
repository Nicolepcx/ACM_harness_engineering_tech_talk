"""Two context-growth illustrations. No model is called in either.

1. skill_growth(): rules pile up inside one SKILL.md (chapter 3). Compares
   loading the whole file with the surface the harness compiles for one ticket.
2. simulate_state(): one long skill run. Compares three ways of carrying the
   run's state: append-only transcript, compaction to a token budget, and an
   explicit state the harness owns (the SKILL.state design, arXiv:2608.26263).

Everything here is mechanical: the prompts are really built as strings and
measured. Token counts are characters / 4. Whether a model would answer
correctly is not simulated; the scenes only report what is in the prompt.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Any

CHARS_PER_TOKEN = 4


def tokens(text_or_chars: str | int) -> int:
    n = text_or_chars if isinstance(text_or_chars, int) else len(text_or_chars)
    return round(n / CHARS_PER_TOKEN)


# ============================================================== 1. rules pile up
# The ticket every run in this scene is about (Dana's refund ticket).
TICKET_AXES = {"region": "US", "segment": "retail", "tier": "standard",
               "reason": "return", "season": "regular", "payment": "card", "valid": "current"}

TOPICS = {
    "when": "When may the refund be paid?",
    "limit": "Who has to approve, above which amount?",
    "window": "How long is the return window?",
    "where": "Where does the money go?",
}


@dataclass(frozen=True)
class Rule:
    added: str                       # month the rule was added to SKILL.md
    scope: dict[str, str]            # {} = shared; otherwise the task axes it is written for
    text: str
    topic: str | None = None         # which question of TOPICS it answers
    answer: str = ""

    @property
    def applies(self) -> bool:
        return all(TICKET_AXES.get(k) == v for k, v in self.scope.items())

    @property
    def section(self) -> str:
        return "shared" if not self.scope else ", ".join(f"{k}={v}" for k, v in self.scope.items())


RULES = [
    # The skill as first written: one procedure, four rules.
    Rule("2025-01", {}, "Pay a refund only after the return shipment is recorded as received at the "
         "warehouse. A customer's statement that the parcel was sent is not sufficient.", "when", "after the warehouse scan"),
    Rule("2025-01", {}, "Refunds above $200 need approval from a support lead before they are paid. "
         "Record the approver with the refund.", "limit", "support lead above $200"),
    Rule("2025-01", {}, "The return window is 30 days from delivery. Outside the window, decline and "
         "explain the policy.", "window", "30 days"),
    Rule("2025-01", {}, "Refund to the original payment method. Never to a different card or account.",
         "where", "original payment method"),
    # Every incident, market and campaign since then added a rule to the same file.
    Rule("2025-02", {"region": "EU"}, "EU customers: the refund is due within 14 days of the withdrawal "
         "notice. If the customer provides proof of postage, do not wait for the warehouse scan.",
         "when", "on proof of postage (EU)"),
    Rule("2025-02", {"region": "EU"}, "EU customers: the withdrawal period is 14 days from delivery and "
         "starts when the last item of the order arrives.", "window", "14 days (EU)"),
    Rule("2025-03", {"region": "UK"}, "UK customers: amounts are in GBP. A support lead approves refunds "
         "above £150.", "limit", "support lead above £150 (UK)"),
    Rule("2025-03", {"segment": "business"}, "Business accounts: refunds are issued as a credit note on "
         "the next invoice, never to a card. The account manager approves above $5,000.",
         "limit", "account manager above $5,000 (business)"),
    Rule("2025-03", {"segment": "business"}, "Business accounts: credit note on the next invoice.",
         "where", "credit note (business)"),
    Rule("2025-04", {"payment": "gift_card"}, "Orders paid with a gift card: refund to the gift card "
         "only, even if the customer asks for a card refund.", "where", "gift card balance"),
    Rule("2025-04", {"reason": "damaged"}, "Damaged on arrival: no return shipment is needed. Refund "
         "after the customer uploads photo evidence of the damage.", "when", "after photo evidence (damaged)"),
    Rule("2025-05", {"tier": "gold"}, "Gold members: refund on request, before the item arrives, for "
         "orders up to $500. Above $500 the standard procedure applies.", "when", "on request up to $500 (Gold)"),
    Rule("2025-06", {"valid": "expired"}, "Update June: because of the peak backlog, support leads only "
         "need to approve refunds above $300.", "limit", "support lead above $300 (June update)"),
    Rule("2025-06", {"segment": "marketplace"}, "Marketplace orders: the seller decides on the refund. "
         "Forward the ticket to the seller desk and do not pay from the company account."),
    Rule("2025-07", {}, "If the customer mentions a chargeback or their bank, add the disputes team to "
         "the ticket. Do not change the refund decision because of it."),
    Rule("2025-07", {"region": "CA"}, "Canadian customers: amounts are in CAD and provincial sales tax "
         "is refunded as a separate line."),
    Rule("2025-08", {"reason": "digital"}, "Digital goods and licences: no refund once the download "
         "link was opened, except where local law requires it."),
    Rule("2025-09", {"valid": "expired"}, "Back-to-school campaign: offer 110% store credit first and "
         "pay a cash refund only if the customer declines.", "where", "110% store credit first (campaign)"),
    Rule("2025-09", {}, "For items above $1,000 the warehouse records the serial number on receipt. "
         "A refund needs the matching serial number."),
    Rule("2025-10", {"region": "DE"}, "German customers: reply in German, formal address. Cite the "
         "Widerrufsrecht when confirming a withdrawal."),
    Rule("2025-11", {"season": "holiday"}, "Holiday season (15 Nov to 15 Jan): the return window is "
         "60 days instead of 30.", "window", "60 days (holiday season)"),
    Rule("2025-12", {"reason": "oversized"}, "Oversized items shipped by freight: the carrier's pickup "
         "confirmation counts as receipt of the return.", "when", "on carrier pickup (freight)"),
    Rule("2026-01", {"region": "AU"}, "Australian customers: a refund for a major failure is the "
         "customer's choice and cannot be replaced by store credit.", "where", "customer's choice (AU)"),
    Rule("2026-02", {"tier": "flagged"}, "Accounts flagged by the fraud team: no refund without a "
         "fraud analyst's approval, whatever the amount.", "limit", "fraud analyst, any amount (flagged)"),
    Rule("2026-03", {"segment": "subscription"}, "Subscriptions: refund the unused part of the current "
         "period, calculated per day."),
    Rule("2026-04", {"region": "JP"}, "Japanese customers: returns go through the convenience store "
         "drop-off partner; the partner's receipt counts as received.", "when", "on partner receipt (JP)"),
    Rule("2026-05", {"tier": "employee"}, "Employee purchases: refunds are handled by HR payroll, not "
         "by support.", "where", "payroll (employees)"),
    Rule("2026-06", {"reason": "late"}, "Delivery more than 10 days late: the customer may cancel and "
         "is refunded before the parcel is returned.", "when", "before return (late delivery)"),
    Rule("2026-08", {"segment": "outlet"}, "Outlet items: final sale, no refund, exchange only."),
    Rule("2026-09", {"region": "UK"}, "UK customers: the return window is 28 days for sale items.",
         "window", "28 days (UK sale)"),
    Rule("2026-10", {"valid": "expired"}, "Temporary: while the warehouse scanner at dock 3 is down, "
         "accept the carrier tracking status 'delivered' as receipt.", "when", "on carrier status (scanner outage)"),
]
BASE_RULES = 4
IDENTITY = ("You are the operations agent for an online shop. You resolve one support ticket per run. "
            "Follow the refund procedure below exactly.")


def _surface(rules: list[Rule]) -> str:
    return IDENTITY + "\n\n# Refund procedure\n" + "\n".join(
        f"\n## [{r.section}] added {r.added}\n{r.text}" for r in rules)


def skill_growth() -> list[dict[str, Any]]:
    """One entry per rule added after the original four."""
    applicable_topics = {r.topic for r in RULES if r.applies and r.topic}
    steps = []
    for k in range(BASE_RULES, len(RULES) + 1):
        rules = RULES[:k]
        used = [r for r in rules if r.applies]
        competing = [r for r in rules if not r.applies and r.topic in applicable_topics]
        chars = {"applies": sum(len(r.text) for r in used),
                 "competes": sum(len(r.text) for r in competing)}
        chars["other"] = sum(len(r.text) for r in rules) - chars["applies"] - chars["competes"]
        whole, compiled = _surface(rules), _surface(used)
        answers = {t: {"whole": [r.answer for r in rules if r.topic == t],
                       "compiled": [r.answer for r in used if r.topic == t]} for t in TOPICS}
        steps.append({
            "added": rules[-1], "rules": len(rules), "applies": len(used), "competes": len(competing),
            "chars": chars, "whole_tokens": tokens(whole), "compiled_tokens": tokens(compiled),
            "digest": hashlib.sha256(compiled.encode()).hexdigest()[:12],
            "sections": sorted({r.section for r in used}), "answers": answers,
            "recent": rules[-6:],
        })
    return steps


# ======================================================= 2. state of a long skill run
SKILL_P = """# Skill: returns-intake (v1.4.0)
You run the returns desk of the shop's warehouse for one shift.
- "Return received": put the parcel on a free shelf. Action: Store <order> <shelf>
- "Shelf needed": move the parcel to the shelf named. Action: Move <order> <old> <new>
- "Refund check": confirm the return is in the warehouse and name its current shelf.
  Action: Confirm <order> <shelf>. If the return is not in the warehouse: Action: Deny <order>
- "Restocked": the parcel left the returns area. Action: Release <order> <shelf>
Never confirm a return you cannot locate. Never name a shelf the parcel has left."""

ITEMS = ["Trail backpack 45L", "Wireless earbuds", "Espresso machine", "Running shoes", "4K monitor 32in",
         "Standing desk", "Mechanical keyboard", "Down jacket", "Smart watch", "Robot vacuum"]
NOISE = ["[Scanner] dock {a} battery {b}%, signal {c}%", "[Conveyor] belt {a} speed 1.{b} m/s, load {c}%",
         "[Camera] forklift parked in zone {a}", "[HVAC] returns hall 1{a}.5 C, humidity {c}%",
         "[Printer] label roll {a} at {c}%", "[Door] dock {a} opened, badge {b}{c}"]
MAX_OPEN = 14


def make_events(steps: int, seed: int = 11) -> list[dict[str, Any]]:
    """A seeded shift at the returns desk. Each event carries the text the
    agent would see and the reply it would give."""
    rng = random.Random(seed)
    open_: dict[str, str] = {}
    free = [f"S-{i:02d}" for i in range(1, 41)]
    order_no, out = 60000, []
    for _ in range(steps):
        kinds = ["receive"] * 3 if len(open_) < MAX_OPEN else []
        if open_:
            kinds += ["move", "check", "check", "check", "restock"]
        kind = rng.choice(kinds)
        noise = "\n".join(rng.choice(NOISE).format(a=rng.randint(1, 9), b=rng.randint(10, 99), c=rng.randint(10, 99))
                          for _ in range(3))
        if kind == "receive":
            order_no += rng.randint(1, 9)
            order, shelf = f"O-{order_no}", free.pop(rng.randrange(len(free)))
            open_[order] = shelf
            obs = f"Return received: order {order} ({rng.choice(ITEMS)})."
            fact = f"{order} is on shelf {shelf}."
            reply = (f"Reasoning: a new return arrived for {order}. I need a free shelf; {shelf} is free. "
                     f"I store the parcel there and remember the location.\nAction: Store {order} {shelf}")
        elif kind == "move":
            order = rng.choice(list(open_))
            old, new = open_[order], free.pop(rng.randrange(len(free)))
            free.append(old)
            open_[order] = new
            obs = f"Shelf needed: {old} is required for maintenance. Move its parcel to {new}."
            fact = f"{order} moved from shelf {old} to shelf {new}."
            reply = (f"Reasoning: {old} holds the return for {order}. It has to move to {new}; the old "
                     f"location is no longer valid.\nAction: Move {order} {old} {new}")
        elif kind == "restock":
            order = rng.choice(list(open_))
            shelf = open_.pop(order)
            free.append(shelf)
            obs = f"Restocked: the return for {order} passed inspection and left the returns area."
            fact = f"{order} left the returns area; shelf {shelf} is free."
            reply = (f"Reasoning: {order} is done. Its shelf {shelf} is free again and must not be "
                     f"confirmed for refund checks any more.\nAction: Release {order} {shelf}")
        else:
            order = rng.choice(list(open_))
            obs = f"Refund check: the refund desk asks whether the return for {order} is in the warehouse, and where."
            fact = None
            reply = (f"Reasoning: I look up where the return for {order} currently is. It is on shelf "
                     f"{open_[order]}.\nAction: Confirm {order} {open_[order]}")
        out.append({"kind": kind, "order": order, "obs": obs + "\n--- background telemetry ---\n" + noise,
                    "headline": obs, "fact": fact, "reply": reply, "state": dict(open_)})
    return out


def simulate_state(steps: int = 100, budget_tokens: int = 800, seed: int = 11) -> dict[str, Any]:
    """Build the prompt of every step under three state-keeping designs."""
    events = make_events(steps, seed)
    budget = budget_tokens * CHARS_PER_TOKEN
    lanes = {k: [] for k in ("append", "compact", "state")}
    cum = dict.fromkeys(lanes, 0)
    checks = {k: [] for k in lanes}
    latest: dict[str, int] = {}           # order -> index of the event holding its current location
    statements = 0                         # location statements made so far
    digest = hashlib.sha256(SKILL_P.encode()).hexdigest()[:12]
    turns: list[str] = []
    last_state_prompt = ""

    for t, e in enumerate(events):
        prior = events[t - 1]["state"] if t else {}
        tail = f"\n\nLatest Observation: {e['obs']}"

        # 1. append-only transcript: everything ever seen and said
        p_append = SKILL_P + "\n\nHistory:\n" + "\n".join(turns) + tail
        stale_append = statements - len(prior)

        # 2. compaction: the last 3 turns verbatim, plus as many of the newest
        #    summary lines as still fit the budget
        recent = turns[-3:]
        room = budget - len(SKILL_P) - sum(map(len, recent)) - len(tail) - 60
        kept: list[int] = []
        for i in range(t - 4, -1, -1):
            if events[i]["fact"] is None:
                continue
            room -= len(events[i]["fact"]) + 1
            if room < 0:
                break
            kept.append(i)
        p_compact = (SKILL_P + "\n\nSummarized History:\n" + "\n".join(events[i]["fact"] for i in reversed(kept))
                     + "\n\nRecent History:\n" + "\n".join(recent) + tail)
        visible = set(kept) | set(range(max(t - 3, 0), t))
        stale_compact = sum(1 for i in kept if latest.get(events[i]["order"]) != i)

        # 3. explicit state owned by the harness: procedure + state + latest observation
        sigma = {"skill": {"id": "returns-intake", "version": "1.4.0", "digest": digest},
                 "step": t + 1, "open_returns": prior}
        p_state = (SKILL_P + "\n\nSkill Execution State:\n" + json.dumps(sigma, separators=(",", ":")) + tail)
        last_state_prompt = p_state

        if e["kind"] == "check":
            checks["append"].append(True)
            checks["compact"].append(latest[e["order"]] in visible)
            checks["state"].append(True)
        for name, prompt, stale in (("append", p_append, stale_append), ("compact", p_compact, stale_compact),
                                    ("state", p_state, 0)):
            cum[name] += tokens(prompt) + tokens(e["reply"])
            lanes[name].append({"prompt_tokens": tokens(prompt), "cum_tokens": cum[name], "stale": stale,
                                "checks": list(checks[name])})

        turns.append(f"Observation: {e['obs']}\n{e['reply']}")
        if e["fact"]:
            if e["kind"] == "restock":
                latest.pop(e["order"], None)
            else:
                latest[e["order"]] = t
                statements += 1
    return {"events": events, "lanes": lanes, "state_prompt": last_state_prompt,
            "peak": max(s["prompt_tokens"] for s in lanes["append"])}


# Measured results quoted from the paper (Badhe, Tiwari, Chung: "SKILL.state: Scalable
# Long-Horizon Agent Skills", arXiv:2608.26263v3, Tables 1 and 5; warehouse task,
# 100 steps, Gemini-3-Flash, mean of 5 seeds).
PAPER_T100 = [
    {"Runtime": "Full transcript (ReAct)", "Score": "0.84", "Avg prompt (chars)": "36,362", "Total tokens": "1,245,413"},
    {"Runtime": "Sliding window, capped at the same budget", "Score": "0.18", "Avg prompt (chars)": "1,800", "Total tokens": "62,100"},
    {"Runtime": "Summary, capped at the same budget", "Score": "0.52", "Avg prompt (chars)": "1,840", "Total tokens": "63,400"},
    {"Runtime": "Prompt compression (LLMLingua)", "Score": "0.22", "Avg prompt (chars)": "1,810", "Total tokens": "62,350"},
    {"Runtime": "Explicit execution state (SKILL.state)", "Score": "0.94", "Avg prompt (chars)": "1,905", "Total tokens": "65,408"},
]
