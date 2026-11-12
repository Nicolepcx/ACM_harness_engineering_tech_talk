"""Harness Engineering: what goes wrong when a component is missing.

    .venv/bin/streamlit run app.py

Every scene sends model turns through the real harness code in harness/core.py
against a real SQLite shop database. Scenes say whether the model turns are a
recorded live run or scripted for illustration.
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from harness.core import SCENARIOS, SIDE_EFFECTING, SKILL_PATH, MODEL, Flags
from harness import context_sims as cs
from harness.scenes import Side, build_scenes, damage, run_side, tokens_per_attempt, _paid_turns

ROOT = Path(__file__).resolve().parent
RED, GREEN, INK, MUTED = "#E3173A", "#16794A", "#2B2B2B", "#6B6B6B"

st.set_page_config(page_title="Harness Engineering", layout="wide", initial_sidebar_state="expanded")
st.markdown(f"""
<style>
html, body, [class*="st-"] {{ font-size: 21px; }}
.block-container {{ padding-top: 4rem; max-width: 1500px; }}
h1.headline {{ font-size: 46px; line-height: 1.15; font-weight: 800; color: {INK}; margin: 0.2rem 0 0.8rem 0; }}
.kicker {{ font-size: 20px; letter-spacing: .08em; text-transform: uppercase; color: {RED}; font-weight: 700; }}
.chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0.4rem 0 1rem 0; }}
.chip {{ border: 2px solid #C9C9C9; border-radius: 8px; padding: 4px 12px; font-size: 18px; color: {INK}; background: #F6F6F6; }}
.chip.off {{ border-color: {RED}; color: {RED}; background: #FDECEF; text-decoration: line-through; font-weight: 700; }}
.ticket {{ border-left: 6px solid #C9C9C9; background: #F6F6F6; padding: 12px 16px; font-size: 19px; white-space: pre-wrap; color: {INK}; border-radius: 4px; }}
.panel-title {{ font-size: 28px; font-weight: 800; margin-bottom: .3rem; }}
.badge {{ display: inline-block; font-size: 15px; font-weight: 700; padding: 2px 10px; border-radius: 999px; border: 1.5px solid {MUTED}; color: {MUTED}; margin-bottom: .5rem; }}
.big {{ font-size: 54px; font-weight: 800; line-height: 1.05; }}
.biglabel {{ font-size: 18px; color: {MUTED}; margin-bottom: .6rem; }}
.trace {{ font-family: ui-monospace, Menlo, monospace; font-size: 16.5px; line-height: 1.55; background: #FBFBFB; border: 1px solid #E3E3E3; border-radius: 6px; padding: 10px 14px; }}
.trace .muted {{ color: #9A9A9A; }}
.trace .stop {{ color: {GREEN}; font-weight: 700; }}
.trace .effect {{ color: {RED}; font-weight: 700; }}
.trace .warn {{ color: #B25E00; font-weight: 700; }}
.said {{ font-size: 19px; font-style: italic; color: {INK}; border-left: 4px solid #C9C9C9; padding-left: 12px; }}
.fact {{ font-size: 20px; color: {MUTED}; margin: .15rem 0; }}
.totals {{ display: flex; gap: 28px; font-size: 19px; color: {MUTED}; border-bottom: 1px solid #E3E3E3; padding-bottom: 8px; margin-bottom: 10px; }}
.totals b {{ color: {RED}; font-size: 24px; }}
table.big-table {{ width: 100%; border-collapse: collapse; font-size: 20px; margin: .4rem 0 1rem 0; }}
table.big-table th {{ text-align: left; color: {MUTED}; font-weight: 600; border-bottom: 2px solid #C9C9C9; padding: 8px 10px; }}
table.big-table td {{ border-bottom: 1px solid #E3E3E3; padding: 8px 10px; color: {INK}; }}
div.stButton > button {{ font-size: 20px; padding: .5rem 1.1rem; }}
</style>""", unsafe_allow_html=True)

COMPONENTS = ["Identity & behavior", "Context & memory", "Orchestration", "Tools & execution",
              "Observability", "Verification", "Adaptation", "Governance & security"]
SCENES = build_scenes()
PAGES = ["The harness", "Intact run"] + [s.nav for s in SCENES] + ["Skill rules", "Skill state", "Retries", "What it cost"]
state = st.session_state
state.setdefault("page", 0)
state.setdefault("results", {})


# ------------------------------------------------------------------------ helpers
def esc(x) -> str:
    return html.escape(str(x))


def money(x: float) -> str:
    return f"${x:,.0f}"


def chips(off: str | None = None) -> None:
    st.markdown('<div class="chips">' + "".join(
        f'<span class="chip{" off" if c == off else ""}">{esc(c)}</span>' for c in COMPONENTS)
        + "</div>", unsafe_allow_html=True)


def short(args: dict, n: int = 96) -> str:
    text = ", ".join(f"{k}={json.dumps(v)}" for k, v in args.items())
    return esc(text if len(text) <= n else text[:n] + " …")


def trace_html(events: list[dict]) -> str:
    if not events:
        return '<div class="trace"><span class="muted">(nothing recorded)</span></div>'
    lines = []
    for e in events:
        k, p = e["kind"], e["payload"]
        tool, args = p.get("tool", ""), p.get("arguments", {})
        if k in ("model_call_started", "tool_started"):
            continue
        if k == "model_call_completed":
            lines.append(f'<span class="muted">model call {p["step"]}</span>')
        elif k == "tool_completed":
            cls = "effect" if tool in SIDE_EFFECTING else ""
            lines.append(f'<span class="{cls}">ran  {esc(tool)}({short(args)})</span>')
        elif k == "tool_blocked":
            lines.append(f'<span class="stop">BLOCKED  {esc(tool)}({short(args, 60)})</span>'
                         f'<br><span class="muted">&nbsp;&nbsp;{esc(p.get("reason", ""))}</span>')
        elif k == "tool_unavailable":
            lines.append(f'<span class="stop">NO EXECUTOR  {esc(tool)}({short(args, 60)})</span>')
        elif k == "tool_failed":
            lines.append(f'<span class="warn">failed  {esc(tool)}: {esc(p.get("error", ""))[:90]}</span>')
        elif k == "effect_replayed":
            lines.append(f'<span class="stop">REPLAYED recorded effect of {esc(tool)}; not executed again</span>')
        elif k == "process_crashed":
            lines.append('<span class="warn">PROCESS CRASHED right after the payout committed</span>')
        elif k == "run_restarted":
            lines.append('<span class="warn">run restarted from the beginning</span>')
        elif k == "verification_passed":
            lines.append('<span>verification passed</span>')
        elif k == "verification_failed":
            lines.append(f'<span class="stop">VERIFICATION FAILED</span><br><span class="muted">&nbsp;&nbsp;'
                         f'{esc(str(p.get("error", ""))[:150])} …</span>')
        elif k == "skill_refused":
            lines.append(f'<span class="stop">SKILL REFUSED at admission: not in registry, '
                         f'{p["findings"]} scanner findings</span>')
        elif k == "skill_loaded":
            lines.append(f'<span>skill loaded into context (digest {esc(p["digest"])})</span>')
        elif k == "replay_ended":
            lines.append('<span class="muted">recording ends here: the harness diverged from it</span>')
        elif k == "escalated":
            lines.append('<span class="warn">escalated: step budget exhausted</span>')
    return '<div class="trace">' + "<br>".join(lines) + "</div>"


def table(rows: list[dict]) -> None:
    head = "".join(f"<th>{esc(k)}</th>" for k in rows[0])
    body = "".join("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in r.values()) + "</tr>" for r in rows)
    st.markdown(f'<table class="big-table"><tr>{head}</tr>{body}</table>', unsafe_allow_html=True)


def big(value: str, label: str, color: str = INK) -> None:
    st.markdown(f'<div class="big" style="color:{color}">{esc(value)}</div>'
                f'<div class="biglabel">{esc(label)}</div>', unsafe_allow_html=True)


def source_badge(side: Side) -> None:
    text = (f"RECORDED run of {MODEL}, replayed" if side.source == "recorded"
            else "SCRIPTED model turns (illustration)")
    st.markdown(f'<span class="badge">{esc(text)}</span>', unsafe_allow_html=True)


def outcome(scene_key: str, result, bad: bool) -> None:
    d, color = damage(result), (RED if bad else GREEN)
    if scene_key == "scoping":
        big(f"{d['rows_deleted']:,} rows", "deleted from the shop database", color if d["rows_deleted"] else GREEN)
        if d["tables_dropped"]:
            st.markdown(f"Tables that no longer exist: **{', '.join(d['tables_dropped'])}**")
    elif scene_key == "skill":
        c1, c2 = st.columns(2)
        with c1:
            big(f"{d['card_records']:,}", "customer card records sent out", RED if d["card_records"] else GREEN)
        with c2:
            big("sent" if d["gcp_key"] else "safe", "GCP service-account key", RED if d["gcp_key"] else GREEN)
        st.markdown(f"Product post published: **{'yes' if result.posts else 'no'}**")
    else:
        paid = d["paid_usd"]
        expected = 1249.0 if scene_key in ("idempotency", "observability", "baseline") else 0.0
        big(money(paid), "left the company account",
            INK if scene_key == "observability" else GREEN if paid == expected or not bad else RED)
        rows = result.after["refund_rows"]
        if rows:
            st.markdown("Refund ledger: " + "; ".join(
                f"**{o}** {money(a)} (approved by: {esc(by)})" for o, a, by in rows))
    if scene_key == "observability":
        big(str(len(result.events)), "events in the audit trail", RED if not result.events else GREEN)
    if result.resolution:
        st.markdown(f'<div class="said">Reply sent: “{esc(result.resolution["customer_reply"])}”</div>',
                    unsafe_allow_html=True)
    elif result.refused:
        st.markdown('<div class="said">The skill never entered the model\'s context. Nothing ran.</div>',
                    unsafe_allow_html=True)
    elif result.replay_ended:
        st.markdown('<div class="said">No reply sent: the harness rejected the answer.</div>',
                    unsafe_allow_html=True)


def totals_bar() -> None:
    paid = rows = cards = 0
    key = False
    for (scene_key, which), res in state.results.items():
        if which != "removed":
            continue
        d = damage(res)
        legit = 1249.0 if scene_key in ("idempotency", "observability") else 0.0
        paid += max(d["paid_usd"] - legit, 0)
        rows += d["rows_deleted"]
        cards += d["card_records"]
        key = key or d["gcp_key"]
    st.markdown(f'<div class="totals"><span>Damage so far</span><span><b>{money(paid)}</b> paid out wrongly</span>'
                f'<span><b>{rows:,}</b> rows deleted</span><span><b>{cards:,}</b> card records leaked</span>'
                f'<span><b>{"1" if key else "0"}</b> cloud keys leaked</span></div>', unsafe_allow_html=True)


def nav_buttons() -> None:
    st.write("")
    left, _, right = st.columns([1, 6, 1])
    if state.page > 0 and left.button("← Back", key="back"):
        state.page -= 1
        st.rerun()
    if state.page < len(PAGES) - 1 and right.button("Next →", key="next", type="primary"):
        state.page += 1
        st.rerun()


def panel(scene_key: str, which: str, side: Side, bad: bool) -> None:
    color = RED if bad else GREEN
    st.markdown(f'<div class="panel-title" style="color:{color}">{esc(side.label)}</div>', unsafe_allow_html=True)
    source_badge(side)
    slot = (scene_key, which)
    if st.button("▶ Run", key=f"run-{scene_key}-{which}"):
        state.results[slot] = run_side(side)
        st.rerun()
    if slot in state.results:
        res = state.results[slot]
        outcome(scene_key, res, bad)
        st.markdown(trace_html(res.events), unsafe_allow_html=True)
        if scene_key == "skill" and res.outbound:
            st.markdown("**Attacker's inbox** (a list on this laptop):")
            for o in res.outbound:
                st.code(f"POST {o['url']}\n{o['body'][:330]} …", language=None, wrap_lines=True)
        if scene_key == "skill" and res.refused:
            table(res.scan["findings"])


# -------------------------------------------------------------------------- pages
with st.sidebar:
    st.markdown("### Scenes")
    choice = st.radio("Scenes", range(len(PAGES)), index=state.page,
                      format_func=lambda i: f"{i}  {PAGES[i]}", label_visibility="collapsed")
    if choice != state.page:
        state.page = choice
        st.rerun()
    if st.button("Reset all runs"):
        state.results = {}
        st.rerun()

page = PAGES[state.page]
totals_bar()

if page == "The harness":
    st.markdown('<div class="kicker">Harness engineering</div>'
                '<h1 class="headline">The model proposes. The harness decides.</h1>', unsafe_allow_html=True)
    a, b = st.columns([3, 2])
    a.image(str(ROOT / "assets/ch02_component_flow_map.png"), width='stretch')
    with b:
        st.markdown("""
**The system:** an operations agent for an online shop.

- handles support tickets: refunds, account deletion
- writes to the shop database: 400 customers, 1,920 orders, cards on file
- uses a social media skill to announce products

**Each scene removes one component** from the harness and runs the same model
behaviour through it. Then the same turns go through the intact harness.

Watch the damage counter at the top.
""")

elif page == "Intact run":
    import time
    from harness import walkthrough as wt
    st.markdown(f"""<style>
.cmap {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }}
.cbox {{ border: 2px solid #D5D5D5; border-radius: 10px; padding: 10px 14px; background: #FAFAFA; min-height: 118px; }}
.cbox .cn {{ font-size: 21px; font-weight: 800; color: #9A9A9A; }}
.cbox .cr {{ font-size: 16px; color: #9A9A9A; line-height: 1.3; }}
.cbox .cd {{ font-size: 17px; color: {GREEN}; font-weight: 700; margin-top: 6px; line-height: 1.3; }}
.cbox.done {{ border-color: {GREEN}; background: #F3FAF6; }}
.cbox.done .cn {{ color: {INK}; }}
.cbox.done .cr {{ color: {MUTED}; }}
.cbox.active {{ border: 4px solid {GREEN}; background: #DDF3E7; box-shadow: 0 0 0 5px #BFE8D2; }}
.cbox.active .cn {{ color: {GREEN}; }}
.cbox.active .cr {{ color: {INK}; }}
.cbox.model {{ border-style: dashed; }}
.cbox.absent {{ border-style: dashed; }}
.cbox.absent .cd {{ color: {MUTED}; font-weight: 600; }}
.stepcard {{ border-left: 8px solid {GREEN}; background: #F6F6F6; border-radius: 6px; padding: 14px 20px; min-height: 420px; }}
.stepcard .sn {{ font-size: 17px; letter-spacing: .08em; text-transform: uppercase; color: {GREEN}; font-weight: 700; }}
.stepcard .st {{ font-size: 30px; font-weight: 800; line-height: 1.2; color: {INK}; margin: 2px 0 10px 0; }}
.stepcard .sl {{ font-size: 21px; line-height: 1.45; color: {INK}; margin: 4px 0; }}
.stepcard .sl.say {{ font-family: ui-monospace, Menlo, monospace; font-size: 19px; }}
.stepcard .sl.note {{ color: {MUTED}; }}
.stepcard .mk {{ color: {GREEN}; font-weight: 800; }}
.stepcard .sh {{ font-size: 21px; font-weight: 700; color: {INK}; border-top: 1px solid #D5D5D5; margin-top: 12px; padding-top: 10px; }}
.stepcard .sr {{ font-size: 18px; color: {RED}; font-weight: 700; margin-top: 8px; }}
.dots span {{ display: inline-block; width: 26px; height: 10px; border-radius: 5px; background: #D5D5D5; margin-right: 6px; }}
.dots span.on {{ background: {GREEN}; }}
</style>""", unsafe_allow_html=True)
    st.markdown('<div class="kicker">All components in place</div>'
                '<h1 class="headline">A legitimate refund: checked, approved, paid once, on the record.</h1>',
                unsafe_allow_html=True)
    st.markdown(f'<div class="ticket">{esc(SCENARIOS["approved_refund"].ticket)}</div>', unsafe_allow_html=True)
    st.write("")
    intact = Side("Intact harness", "approved_refund", Flags(), _paid_turns, "recorded")
    slot = ("baseline", "intact")
    st.markdown(f'<span class="badge">RECORDED run of {esc(MODEL)}, replayed through the real harness. '
                f'Each step below is read from its trace.</span>', unsafe_allow_html=True)

    MARK = {"ok": "✓", "in": "＋", "say": "›", "note": "·"}

    def draw(steps: list[dict], i: int) -> None:
        s, done = steps[i], wt.contributions(steps, i)
        boxes = []
        for name in wt.ORDER:
            cls = "active" if name in s["active"] else "done" if name in done else ""
            if name == "Model":
                cls += " model"
            if name == "Adaptation":
                cls = "absent"
            did = f'<div class="cd">{esc(done[name])}</div>' if name in done else ""
            boxes.append(f'<div class="cbox {cls}"><div class="cn">{esc(name)}</div>'
                         f'<div class="cr">{esc(wt.ROLES[name])}</div>{did}</div>')
        lines = "".join(f'<div class="sl {k}"><span class="mk">{MARK[k]}</span> {esc(t)}</div>' for k, t in s["lines"])
        removed = ""
        if s["removed"]:
            refs = " and ".join(f"scene {PAGES.index(n)} ({n})" for n in s["removed"])
            removed = f'<div class="sr">Removed later in {esc(refs)}.</div>'
        dots = "".join(f'<span class="{"on" if j <= i else ""}"></span>' for j in range(len(steps)))
        left, right = st.columns([11, 9])
        left.markdown('<div class="cmap">' + "".join(boxes) + "</div>", unsafe_allow_html=True)
        right.markdown(f'<div class="stepcard"><div class="sn">Step {i + 1} of {len(steps)}</div>'
                       f'<div class="dots">{dots}</div>'
                       f'<div class="st">{esc(s["title"])}</div>{lines}'
                       f'<div class="sh">→ {esc(s["hands"])}</div>{removed}</div>', unsafe_allow_html=True)

    b1, b2, b3, _ = st.columns([2.2, 1.5, 1.5, 6])
    play = b1.button("▶ Run the ticket", key="run-baseline-intact", type="primary")
    if play:
        state.results[slot] = run_side(intact)
        state.intact_step = 0
    have = slot in state.results
    steps = wt.build_steps(state.results[slot]) if have else []
    last = len(steps) - 1

    def move(delta: int) -> None:
        state.intact_step = min(max(state.get("intact_step", last) + delta, 0), last)

    if have and not play:
        at = state.get("intact_step", last)
        b2.button("← Step", key="intact-prev", disabled=at <= 0, on_click=move, args=(-1,))
        b3.button("Step →", key="intact-next", disabled=at >= last, on_click=move, args=(1,))

    stage = st.empty()
    if not have:
        with stage.container():
            left, right = st.columns([11, 9])
            left.markdown('<div class="cmap">' + "".join(
                f'<div class="cbox{" model" if n == "Model" else " absent" if n == "Adaptation" else ""}"><div class="cn">{esc(n)}</div>'
                f'<div class="cr">{esc(wt.ROLES[n])}</div></div>' for n in wt.ORDER) + "</div>",
                unsafe_allow_html=True)
            right.markdown('<div class="stepcard"><div class="sn">Not started</div>'
                           '<div class="st">Eight components and a model.</div>'
                           '<div class="sl">Run the ticket and watch which component acts, what it checks '
                           'and what it hands on.</div></div>', unsafe_allow_html=True)
    else:
        if play:
            for i in range(len(steps)):
                with stage.container():
                    draw(steps, i)
                time.sleep(1.6)
            state.intact_step = last
            st.rerun()
        with stage.container():
            draw(steps, min(state.get("intact_step", len(steps) - 1), len(steps) - 1))
        res = state.results[slot]
        st.write("")
        o1, o2 = st.columns([2, 3])
        with o1:
            outcome("baseline", res, bad=False)
        with o2:
            st.markdown("**Why this run is right, and where each reason comes from**")
            table([
                {"What held": "The right policy was read", "Component": "Context & memory", "Removed in": f"scene {PAGES.index('Context')}"},
                {"What held": "Return and approval checked as facts", "Component": "Governance & security", "Removed in": f"scene {PAGES.index('Governance')}"},
                {"What held": "Paid once, through a scoped tool", "Component": "Tools & execution", "Removed in": f"scenes {PAGES.index('Execution')} and {PAGES.index('Tool scope')}"},
                {"What held": "Every step is on the record", "Component": "Observability", "Removed in": f"scene {PAGES.index('Observability')}"},
                {"What held": "The answer claims only what ran", "Component": "Verification", "Removed in": f"reads the trace, see scene {PAGES.index('Observability')}"},
            ])
        st.markdown('<div class="fact">The model made three proposals. Every one of them passed through a component '
                    'that could have said no.</div>', unsafe_allow_html=True)
        st.markdown('<div class="fact">Limit of this demo harness: no adaptation and no memory component. '
                    'In one of the three recorded runs the model handed this refund to a human instead of paying it.</div>',
                    unsafe_allow_html=True)
        with st.expander("Raw trace of this run"):
            st.markdown(trace_html(res.events), unsafe_allow_html=True)

elif page in ("Skill rules", "Skill state"):
    import time
    st.markdown(f"""<style>
.clock {{ font-size: 40px; font-weight: 800; font-variant-numeric: tabular-nums; line-height: 1.1; }}
.stats {{ font-size: 19px; color: {MUTED}; margin: .2rem 0 .5rem 0; }} .stats b {{ color: {INK}; }}
.meter {{ display: flex; height: 26px; background: #EFEFEF; border-radius: 5px; overflow: hidden; margin: .3rem 0; }}
.meter span {{ height: 100%; }}
.rule {{ font-size: 16.5px; line-height: 1.45; padding: 2px 0 2px 10px; border-left: 5px solid #C9C9C9; margin: 4px 0; color: {INK}; }}
.rule small {{ color: {MUTED}; }}
.sqs {{ display: flex; flex-wrap: wrap; gap: 4px; margin: .4rem 0; min-height: 22px; }}
.sqs span {{ width: 18px; height: 18px; border-radius: 4px; }}
.now {{ font-family: ui-monospace, Menlo, monospace; font-size: 17px; background: #FBFBFB; border: 1px solid #E3E3E3;
        border-radius: 6px; padding: 8px 12px; margin: .4rem 0 .8rem 0; min-height: 2.6em; }}
</style>""", unsafe_allow_html=True)

    def meter(parts: list[tuple[float, str]], scale: float) -> str:
        return '<div class="meter">' + "".join(
            f'<span style="width:{100 * v / scale:.2f}%;background:{c}"></span>' for v, c in parts) + "</div>"

    if page == "Skill rules":
        st.markdown('<div class="kicker">Context & memory</div>'
                    '<h1 class="headline">Every incident adds a rule to the skill. Every run pays for all of them.</h1>',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="ticket">{esc(SCENARIOS["refund"].ticket)}</div>', unsafe_allow_html=True)
        steps = cs.skill_growth()
        last = steps[-1]
        b1, _ = st.columns([2, 5])
        go = b1.button("▶ Let 21 months pass", key="rules-go", type="primary")
        st.markdown('<span class="badge">ILLUSTRATION, no model call: the rule texts are written for this demo; '
                    'sizes are measured from the text that would enter the context (characters ÷ 4)</span>',
                    unsafe_allow_html=True)
        left, right = st.columns(2)
        slots = (left.empty(), right.empty())

        def draw(i: int) -> None:
            s = steps[i]
            c = s["chars"]
            total = sum(last["chars"].values())
            rules = "".join(
                f'<div class="rule" style="border-color:'
                f'{GREEN if r.applies else RED if r.topic and r.topic in cs.TOPICS else "#C9C9C9"}">'
                f'<small>{esc(r.added)} · {esc(r.section)}</small><br>{esc(r.text[:118])}{"…" if len(r.text) > 118 else ""}</div>'
                for r in reversed(s["recent"]))
            slots[0].markdown(
                f'<div class="panel-title" style="color:{RED}">Whole SKILL.md loaded</div>'
                f'<div class="clock">≈ {s["whole_tokens"]:,} tokens</div>'
                f'<div class="stats">in every run · <b>{s["rules"]}</b> rules · <b>{s["applies"]}</b> apply to this ticket · '
                f'<b style="color:{RED}">{s["competes"]}</b> give a different answer to a question this ticket raises</div>'
                + meter([(c["applies"], GREEN), (c["competes"], RED), (c["other"], "#BDBDBD")], total)
                + f'<div class="stats">Last added to the file:</div>{rules}', unsafe_allow_html=True)
            slots[1].markdown(
                f'<div class="panel-title" style="color:{GREEN}">Harness compiles the sections for this ticket</div>'
                f'<div class="clock">≈ {s["compiled_tokens"]:,} tokens</div>'
                f'<div class="stats">in this run · <b>{s["applies"]}</b> rules, all apply · sections: '
                f'<b>{esc(", ".join(s["sections"]))}</b> · digest <b>{s["digest"]}</b></div>'
                + meter([(c["applies"], GREEN)], total)
                + '<div class="stats">The file is the same on both sides. The team maintains one procedure. '
                  'The harness selects by the ticket\'s facts (region, customer type, reason, date) and records '
                  'which sections and which version the model received.</div>', unsafe_allow_html=True)

        if go:
            for i in range(len(steps)):
                draw(i)
                time.sleep(0.45)
            state.rules_done = True
        done = state.get("rules_done", False)
        draw(len(steps) - 1 if done else 0)
        if done:
            st.markdown('<div class="panel-title">What the model has to sort out for this one ticket</div>',
                        unsafe_allow_html=True)
            table([{"Question": q,
                    "Answers in the whole skill": f'{len(last["answers"][t]["whole"])}: ' + " · ".join(last["answers"][t]["whole"]),
                    "Answers in the compiled surface": " · ".join(last["answers"][t]["compiled"])}
                   for t, q in cs.TOPICS.items()])
            c1, _ = st.columns([1, 2])
            per_day = c1.select_slider("Tickets per day", [1_000, 10_000, 100_000], 10_000)
            extra = (last["whole_tokens"] - last["compiled_tokens"]) * per_day
            st.markdown(
                f'<div class="said" style="font-style:normal;font-size:24px">The skill grew from '
                f'{steps[0]["whole_tokens"]:,} to <b>{last["whole_tokens"]:,} tokens</b>. '
                f'<b>{last["rules"] - last["applies"]} of {last["rules"]} rules</b> do not apply to this ticket, and '
                f'{last["competes"]} of them answer its questions differently. At {per_day:,} tickets a day that is '
                f'<b>{extra:,} tokens a day</b> of instructions for cases the agent is not handling.</div>',
                unsafe_allow_html=True)
        for fact in ("A bigger context window makes this fit. It does not remove the competing answers.",
                     "Summarising the skill is not a fix: the thresholds and exceptions are the content.",
                     "Not simulated: whether the model picks the wrong rule. The scene shows what it is asked to sort through."):
            st.markdown(f'<div class="fact">• {esc(fact)}</div>', unsafe_allow_html=True)

    else:
        st.markdown('<div class="kicker">Orchestration & lifecycle · Context & memory</div>'
                    '<h1 class="headline">A long-running skill has state. If the harness does not hold it, the transcript does.</h1>',
                    unsafe_allow_html=True)
        c1, c2, _ = st.columns([1, 1, 2])
        T = c1.select_slider("Steps in the shift", [50, 100, 200], 200)
        budget = c2.slider("Compaction budget (tokens per prompt)", 600, 1200, 700, 100)
        sim = cs.simulate_state(T, budget)
        L, ev = sim["lanes"], sim["events"]
        b1, _ = st.columns([2, 5])
        go = b1.button("▶ Run the shift", key="state-go", type="primary")
        st.markdown('<span class="badge">ILLUSTRATION, no model call: a scripted shift at the returns desk; every prompt '
                    'is really assembled and measured (characters ÷ 4)</span>', unsafe_allow_html=True)
        now = st.empty()
        cols = st.columns(3)
        slots = [c.empty() for c in cols]
        names = [("append", "Append everything", RED, "the full transcript of the shift"),
                 ("compact", f"Compact to {budget:,} tokens", RED, "last 3 turns + the newest summary lines that fit"),
                 ("state", "Harness-managed skill state", GREEN, "procedure + current state + latest observation")]

        def draw(t: int) -> None:
            now.markdown(f'<div class="now">step {t + 1} of {T}: {esc(ev[t]["headline"])}</div>', unsafe_allow_html=True)
            for slot, (key, title, color, what) in zip(slots, names):
                s = L[key][t]
                missing = len(s["checks"]) - sum(s["checks"])
                sq = "".join(f'<span style="background:{GREEN if ok else RED}"></span>' for ok in s["checks"])
                if key == "append":
                    note = (f'<b style="color:{RED}">{s["stale"]}</b> obsolete shelf locations still in the prompt')
                elif key == "compact":
                    note = (f'<b style="color:{RED}">{missing}</b> of {len(s["checks"])} refund checks: '
                            f'the return\'s location was no longer in the prompt')
                else:
                    note = f'<b>0</b> obsolete locations · <b>0</b> missing'
                slot.markdown(
                    f'<div class="panel-title" style="color:{color}">{esc(title)}</div>'
                    f'<div class="stats">{esc(what)}</div>'
                    f'<div class="clock">≈ {s["prompt_tokens"]:,}</div><div class="stats">tokens in this prompt</div>'
                    + meter([(s["prompt_tokens"], color)], sim["peak"])
                    + f'<div class="stats"><b>{s["cum_tokens"]:,}</b> tokens used so far</div>'
                    f'<div class="stats">Refund checks (green: the return\'s current shelf is in the prompt)</div>'
                    f'<div class="sqs">{sq}</div><div class="stats">{note}</div>', unsafe_allow_html=True)

        params = (T, budget)
        if go:
            stride = max(T // 100, 1)
            for t in range(0, T, stride):
                draw(t)
                time.sleep(0.12)
            state.state_done = params
        done = state.get("state_done") == params
        draw(T - 1 if done else 0)
        if done:
            a, c, s_ = (L[k][-1] for k in ("append", "compact", "state"))
            miss = len(c["checks"]) - sum(c["checks"])
            st.markdown(
                f'<div class="said" style="font-style:normal;font-size:24px">After {T} steps the transcript cost '
                f'<b>{a["cum_tokens"]:,} tokens</b>, {a["cum_tokens"] / s_["cum_tokens"]:.0f}× the managed state, and its '
                f'last prompt carries {a["stale"]} shelf locations that are no longer true. Compaction kept the prompt '
                f'small and answered <b>{miss} of {len(c["checks"])} refund checks without the return\'s location</b>. '
                f'The managed state used {s_["cum_tokens"]:,} tokens and never lost one.</div>', unsafe_allow_html=True)
            with st.expander("The last prompt of the managed-state run: procedure, state, latest observation"):
                st.code(sim["state_prompt"], language=None, wrap_lines=True)
            st.markdown('<div class="panel-title">Measured with a real model</div>'
                        '<div class="stats">Badhe, Tiwari, Chung: “SKILL.state: Scalable Long-Horizon Agent Skills”, '
                        'arXiv:2608.26263. Warehouse task, 100 steps, Gemini-3-Flash, mean of 5 seeds (Tables 1 and 5).</div>',
                        unsafe_allow_html=True)
            table(cs.PAPER_T100)
        for fact in ("Raise the compaction budget and its misses disappear here, while the prompt grows. A compaction that "
                     "never drops a needed fact is a state schema. Make it explicit and let the harness own it.",
                     "The state names the skill, its version and digest, the step, and the open returns. The model proposes "
                     "a change; the harness validates it and applies it.",
                     "The history is not thrown away. It goes to the trace, where audits read it, not into every prompt.",
                     "Limits the paper states: the state needs a schema known in advance, and on small open-weight models "
                     "it saved tokens but did not improve accuracy at 100 steps (0.42 vs 0.42).",
                     "Not simulated here: model accuracy. The squares show whether the needed fact was in the prompt."):
            st.markdown(f'<div class="fact">• {esc(fact)}</div>', unsafe_allow_html=True)

elif page == "Retries":
    st.markdown('<div class="kicker">Orchestration & observability</div>'
                '<h1 class="headline">More retries buy waiting and cost. They do not fix the cause.</h1>',
                unsafe_allow_html=True)
    st.markdown(f"""<style>
.lane {{ display: flex; align-items: center; gap: 5px; height: 30px; font-size: 17px; }}
.lane .who {{ width: 74px; color: {MUTED}; flex: none; }}
.lane .sq {{ width: 20px; height: 20px; border-radius: 4px; flex: none; }}
.lane .bad {{ background: {RED}; }} .lane .ok {{ background: {GREEN}; }}
.lane .run {{ background: #BDBDBD; animation: pulse .9s infinite; }}
.lane .st {{ margin-left: 8px; white-space: nowrap; }}
@keyframes pulse {{ 0% {{ opacity: .25; }} 50% {{ opacity: 1; }} 100% {{ opacity: .25; }} }}
.clock {{ font-size: 40px; font-weight: 800; font-variant-numeric: tabular-nums; }}
.stats {{ font-size: 19px; color: {MUTED}; margin-bottom: .5rem; }} .stats b {{ color: {INK}; }}
</style>""", unsafe_allow_html=True)

    USERS = 16
    c1, c2, c3, c4 = st.columns(4)
    p = c1.slider("Success probability per attempt, p", 0.30, 0.95, 0.60, 0.05)
    shared = c2.slider("Failures with a shared cause (%)", 0, 100, 50, 10,
                       help="Share of first-attempt failures caused by something a retry cannot fix, "
                            "for example a defective instruction.") / 100
    budget_a = c3.slider("Attempts allowed, left", 1, 6, 3)
    budget_b = c4.slider("Attempts allowed, right", 4, 20, 10)

    d = shared * (1 - p)                     # requests that fail on every attempt
    p_retry = p / (1 - d)                    # per-attempt success for the rest
    tok = tokens_per_attempt()
    state.setdefault("retry_seed", 7)
    rng = np.random.default_rng(state.retry_seed)
    success_at = rng.geometric(p_retry, USERS).astype(float)
    success_at[rng.choice(USERS, round(d * USERS), replace=False)] = math.inf

    def tally(budget: int, t: int) -> dict:
        ends = np.minimum(success_at, budget)
        done = ends <= t
        return {"answered": int((done & (success_at <= budget)).sum()),
                "gave_up": int((done & (success_at > budget)).sum()), "waiting": int((~done).sum()),
                "attempts": int(np.minimum(ends, t).sum())}

    def lanes(budget: int, t: int) -> str:
        m = tally(budget, t)
        out = [f'<div class="panel-title">Up to {budget} attempts</div>'
               f'<div class="clock">{min(t, budget)} s</div>'
               f'<div class="stats"><b>{m["answered"]}</b> answered · <b>{m["gave_up"]}</b> gave up · '
               f'<b>{m["waiting"]}</b> waiting · <b>{m["attempts"]}</b> attempts · '
               f'<b>{m["attempts"] * tok:,.0f}</b> tokens</div>']
        for u, s_at in enumerate(success_at):
            end = int(min(s_at, budget))
            sq = "".join(f'<span class="sq {"ok" if k == s_at else "bad"}"></span>'
                         for k in range(1, min(t, end) + 1))
            if t < end:
                sq += '<span class="sq run"></span>'
                status = '<span class="st" style="color:#9A9A9A">waiting …</span>'
            elif s_at <= budget:
                status = f'<span class="st" style="color:{GREEN}">answered after {end} s</span>'
            else:
                status = f'<span class="st" style="color:{RED};font-weight:700">no answer after {end} s</span>'
            out.append(f'<div class="lane"><span class="who">user {u + 1}</span>{sq}{status}</div>')
        return "".join(out)

    b1, b2, _ = st.columns([2, 1, 4])
    go = b1.button("▶ Send 16 requests", key="retry-go", type="primary")
    if b2.button("New users", key="retry-new"):
        state.retry_seed += 1
        state.pop("retry_done", None)
        st.rerun()
    st.markdown(f'<span class="badge">SIMULATED requests (illustration): one attempt takes 1 s, '
                f'{round(d * USERS)} of {USERS} requests hit a cause that a retry cannot fix</span>',
                unsafe_allow_html=True)
    left, right = st.columns(2)
    slots = [(left.empty(), budget_a), (right.empty(), budget_b)]
    params = (p, shared, budget_a, budget_b, state.retry_seed)

    def draw(t: int) -> None:
        for slot, budget in slots:
            slot.markdown(lanes(budget, t), unsafe_allow_html=True)

    if go:
        import time
        for t in range(budget_b + 1):
            draw(t)
            if t < budget_b:
                time.sleep(1.0)
        state.retry_done = params
    finished = state.get("retry_done") == params
    draw(budget_b if finished else 0)
    if finished:
        a, b = tally(budget_a, budget_b), tally(budget_b, budget_b)
        extra = b["answered"] - a["answered"]
        st.markdown(
            f'<div class="said" style="font-style:normal;font-size:24px">Allowing {budget_b} attempts instead of '
            f'{budget_a} answered <b>{extra} more</b> of {USERS} users. It used <b>{b["attempts"] - a["attempts"]} '
            f'more attempts</b> ({(b["attempts"] - a["attempts"]) * tok:,.0f} tokens), and the '
            f'{b["gave_up"]} users without an answer waited <b>{budget_b} s instead of {budget_a} s</b> '
            f'to find that out.</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown('<div class="panel-title">The same thing at scale</div>', unsafe_allow_html=True)
    c1, c2, _ = st.columns([1, 1, 2])
    n = c1.slider("Maximum attempts, n", 1, 20, 5)
    users = c2.select_slider("Users", [100, 1_000, 10_000, 100_000], 1_000)
    fail_after = lambda k: d + (1 - d) * (1 - p_retry) ** k
    p_star = 1 - fail_after(n)
    expected_attempts = sum(fail_after(k) for k in range(n))
    n_min = math.ceil(math.log(0.02) / math.log(1 - p))
    finish = [fail_after(k - 1) - fail_after(k) for k in range(1, n)] + [fail_after(n - 1)]
    cum = np.cumsum(finish)
    p95, p99 = int(np.searchsorted(cum, 0.95) + 1), int(np.searchsorted(cum, 0.99) + 1)

    a, b, c, e = st.columns(4)
    with a:
        big(f"{p_star:.2%}", f"succeed within {n} attempts", GREEN if p_star >= 0.98 else RED)
    with b:
        big(f"{expected_attempts:.2f}", "attempts per request, on average")
    with c:
        big(f"{users * fail_after(n):,.1f}", f"of {users:,} users exhaust the retries", RED)
    with e:
        big(f"{p95} s / {p99} s", "p95 / p99 latency at 1 s per attempt")

    left, right = st.columns([3, 2])
    with left:
        ks = list(range(1, 21))
        st.line_chart(pd.DataFrame({
            "independent failures": [1 - (1 - p) ** k for k in ks],
            "with shared cause": [1 - fail_after(k) for k in ks]}, index=pd.Index(ks, name="attempts allowed")),
            height=330, color=["#9A9A9A", RED])
    with right:
        st.latex(r"p^*=1-(1-p)^n \qquad n_{\min}=\left\lceil\frac{\ln(1-p^*)}{\ln(1-p)}\right\rceil")
        st.markdown(f"For 98% success at p = {p:.2f} with independent failures: **{n_min} attempts**.")
        if d:
            st.markdown(f"With the shared cause, success can never pass **{1 - d:.0%}**, "
                        f"however many attempts are allowed.")
        st.markdown(f"Token cost: **{expected_attempts:.2f} ×** a clean run. One attempt of the refund "
                    f"ticket measured **{tok:,.0f} tokens** in the recorded runs, so "
                    f"{users:,} requests burn about **{users * tok * (expected_attempts - 1):,.0f} "
                    f"extra tokens** on retries.")
    table([{"Users": f"{u:,}", f"Expected to reach attempt {n}": f"{u * fail_after(n - 1):,.2f}",
            "Expected to exhaust the retries": f"{u * fail_after(n):,.2f}"} for u in (100, 1_000, 10_000)])
    st.markdown('<div class="fact">A probability model with the assumptions shown, not measured agent data. '
                'Only the token figure per attempt is measured. Set the shared cause to 0 to see the textbook '
                'case where retries do help.</div>', unsafe_allow_html=True)

elif page == "What it cost":
    st.markdown('<div class="kicker">Same model in every scene</div>'
                '<h1 class="headline">Six components removed. One raised an alarm.</h1>', unsafe_allow_html=True)
    alarm = {"governance": "No. The run reported success.", "context": "No. The run reported success.",
             "idempotency": "No. The run reported success.", "scoping": "No. The run reported success.",
             "skill": "No. Verification passed.", "observability": "Yes, but nobody can say why."}
    rows = []
    for s in SCENES:
        d = damage(state.results.get((s.key, "removed")) or run_side(s.removed))
        legit = 1249.0 if s.key in ("idempotency", "observability") else 0.0
        parts = []
        if d["paid_usd"] - legit > 0:
            parts.append(f"{money(d['paid_usd'] - legit)} paid out wrongly")
        if d["rows_deleted"]:
            parts.append(f"{d['rows_deleted']:,} rows deleted")
        if d["card_records"]:
            parts.append(f"{d['card_records']} card records and a cloud key sent out")
        if s.key == "observability":
            parts.append("no audit trail; a correct answer was rejected")
        rows.append({"Removed": s.removed.label, "Component": s.component,
                     "What happened": "; ".join(parts), "Did anything raise an alarm?": alarm[s.key],
                     "Model turns": s.removed.source})
    table(rows)
    a, b = st.columns(2)
    a.markdown("#### Belongs in instructions\n- identity, tone, audience\n- procedure: how to do the task well\n"
               "- what good output looks like")
    b.markdown("#### Needs enforcement in the harness\n- who may approve, and what runs without approval\n"
               "- which tools exist and what they can touch\n- which skills are admitted, and their grant\n"
               "- that effects happen once\n- that answers are checked against what actually ran\n"
               "- which part of a skill enters the context, and the state of a long run")

else:
    scene = SCENES[state.page - 2]
    st.markdown(f'<div class="kicker">Removed: {esc(scene.component)}</div>'
                f'<h1 class="headline">{esc(scene.headline)}</h1>', unsafe_allow_html=True)
    chips(scene.component)
    st.markdown(f'<div class="ticket">{esc(SCENARIOS[scene.ticket].ticket)}</div>', unsafe_allow_html=True)
    if scene.key == "skill":
        with st.expander("The skill file the team installed (SKILL.md)"):
            st.code(SKILL_PATH.read_text(), language="markdown")
    st.write("")
    cols = st.columns(3 if scene.extra else 2)
    with cols[0]:
        panel(scene.key, "removed", scene.removed, bad=True)
    with cols[1]:
        panel(scene.key, "intact", scene.intact, bad=False)
    if scene.extra:
        with cols[2]:
            panel(scene.key, "extra", scene.extra, bad=False)
    st.write("")
    for fact in scene.facts:
        st.markdown(f'<div class="fact">• {esc(fact)}</div>', unsafe_allow_html=True)

nav_buttons()
