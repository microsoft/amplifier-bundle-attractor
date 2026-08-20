"""Deterministic, self-contained HTML artifact. NO LLM anywhere in this module.

Determinism is the point: the same ranked JSON always produces byte-identical
HTML, so the artifact is reproducible, diffable, and free. Everything is
inlined — no network fetch, no CDN, no font call — because an onboarding
report about your own private session data must not phone anywhere.

Layout:
* a **sampled simple -> complex range** across the grain axis at the top
  (breadth, deliberately NOT just the top-k by score),
* **in-page modal deep-dives** into the full discovered list,
* **honest-NOs rendered with verdict + failed sub-test + remediation** —
  first-class, never a footnote,
* **waste findings** in their own channel.

Renderer honesty invariant (machine-checked): a 4c-UNKNOWN unit is NEVER
rendered as FAIL. `unproven` is displayed as a caveat on an opportunity.

Its sibling, added with the demonstration layer: an UNVERIFIED demo is NEVER
rendered as verified. The verification level is printed as the exact label the
ladder resolved, and "the linter did not run here" is stated in those words
rather than left as a silence a reader would read as a pass.

**Additivity is a hard guarantee.** With `demos=None` this module emits the
byte-identical artifact it emitted before the demonstration layer existed:
every new byte — CSS included — is inside a conditional. A test pins it.

**The explainer is LINKED, never inlined, and that does not breach the
self-contained rule.** Self-contained forbids *fetched resources* — CSS, JS,
fonts, data the document loads in order to display itself. A hyperlink loads
nothing; it is a reference a reader may choose to follow, and the artifact
renders completely with the network unplugged.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from . import demo_templates as T
from .naming import ARTIFACT_STEM, SKILL_TITLE

_VERDICT_CLASS = {
    "OPPORTUNITY": "verdict-opp",
    "OPPORTUNITY(unproven)": "verdict-unproven",
    "HONEST-NO": "verdict-no",
}

_TRAJECTORY_LABEL = {
    "escalating": "escalating",
    "chronic-stable": "chronic",
    "fading": "fading",
    "structural": "structural",
}

_CSS = """
:root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--fg:#e6e9ef;--dim:#98a1b3;
--opp:#4ade80;--unproven:#fbbf24;--no:#f87171;--waste:#818cf8;--acc:#60a5fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 96px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 10px}
.sub{color:var(--dim);font-size:13px;margin:0 0 26px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;cursor:pointer}
.card:hover{border-color:var(--acc)}
.card h3{margin:0 0 6px;font-size:15px;line-height:1.35}
.meta{color:var(--dim);font-size:12px;display:flex;flex-wrap:wrap;gap:10px;margin-top:8px}
.badge{display:inline-block;font-size:11px;padding:2px 7px;border-radius:999px;
border:1px solid var(--line)}
.verdict-opp{color:var(--opp);border-color:var(--opp)}
.verdict-unproven{color:var(--unproven);border-color:var(--unproven)}
.verdict-no{color:var(--no);border-color:var(--no)}
.badge-waste{color:var(--waste);border-color:var(--waste)}
.badge-esc{color:var(--unproven);border-color:var(--unproven)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:600}
tr.row{cursor:pointer}tr.row:hover td{background:#1d222c}
.rem{margin-top:8px;font-size:13px;color:var(--fg);background:#1c2130;
border-left:3px solid var(--acc);padding:8px 10px;border-radius:0 6px 6px 0}
.modal{position:fixed;inset:0;background:rgba(4,6,10,.72);display:none;
align-items:center;justify-content:center;padding:24px;z-index:50}
.modal.open{display:flex}
.modal-inner{background:var(--panel);border:1px solid var(--line);border-radius:12px;
max-width:760px;width:100%;max-height:82vh;overflow:auto;padding:22px 24px}
.kv{display:grid;grid-template-columns:180px 1fr;gap:4px 14px;font-size:13px;margin-top:12px}
.kv div:nth-child(odd){color:var(--dim)}
.close{float:right;color:var(--dim);cursor:pointer;font-size:20px;line-height:1}
.note{color:var(--dim);font-size:12px;margin-top:6px}
code{background:#1c2130;padding:1px 5px;border-radius:4px;font-size:12px}
"""

_JS = """
var DATA = __DATA__;
function openUnit(id){
  var u = DATA.units[id]; if(!u){return;}
  var rows = '';
  function kv(k,v){ if(v===null||v===undefined||v==='')return;
    rows += '<div>'+k+'</div><div>'+v+'</div>'; }
  kv('verdict', u.verdict);
  kv('failed sub-test', u.failed_subtest);
  kv('distinct sessions', u.n_sessions);
  kv('leverage', u.leverage);
  kv('score', u.score);
  kv('author', u.author);
  kv('4a cycle', u.cycle);
  kv('4b gate', u.gate);
  kv('4c recovery', u.recovery + ' (confidence: ' + u.confidence + ')');
  kv('trajectory', u.trajectory);
  kv('rung', u.rung);
  kv('frequency', u.provisional ? 'provisional (n in {2,3})' : 'confirmed');
  var rem = u.remediation ? '<div class="rem">'+u.remediation+'</div>' : '';
  var gist = u.gist ? '<p class="note">'+u.gist+'</p>' : '';
  document.getElementById('modal-body').innerHTML =
    '<span class="close" onclick="closeModal()">&times;</span>'
    + '<h3>'+u.name+'</h3>' + gist
    + '<span class="badge '+u.vclass+'">'+u.verdict+'</span>'
    + '<div class="kv">'+rows+'</div>' + rem
    + '<p class="note">'+u.n_members+' member session(s), keyed on full session ids.</p>';
  document.getElementById('modal').classList.add('open');
}
function closeModal(){ document.getElementById('modal').classList.remove('open'); }
document.addEventListener('keydown', function(e){ if(e.key==='Escape'){closeModal();} });
"""


#: Appended to _CSS **only** when demos are supplied, so that the no-demos
#: artifact stays byte-identical to the pre-demonstration-layer output.
_DEMO_CSS = """
.demo{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:18px 20px;margin:14px 0}
.demo h3{margin:22px 0 6px;font-size:15px}
.demo h3:first-of-type{margin-top:10px}
.primer{background:#141824;border:1px solid var(--line);border-radius:10px;padding:18px 20px}
.primer dt{font-weight:600;margin-top:12px}
.primer dd{margin:2px 0 0;color:var(--dim)}
.statline{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--dim);margin:8px 0 0}
.statline b{color:var(--fg);font-weight:600}
pre.dotsrc{background:#0b0d12;border:1px solid var(--line);border-radius:8px;padding:12px 14px;
overflow:auto;font-size:12px;line-height:1.45;max-height:520px;white-space:pre}
pre.cmd{background:#0b0d12;border:1px solid var(--line);border-radius:8px;padding:10px 12px;
overflow:auto;font-size:12px;white-space:pre-wrap}
.panel{border-left:3px solid var(--acc);background:#1c2130;padding:10px 12px;
border-radius:0 6px 6px 0;margin-top:8px}
.panel h4{margin:0 0 4px;font-size:13px}
.panel + .panel{margin-top:10px}
.unverified{border-left-color:var(--no);background:#2a1a1d}
.lvl{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.walk{margin:6px 0 0;padding-left:18px;font-size:13px}
.walk li{margin-bottom:4px}
.math{font-size:13px}
"""


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _vclass(verdict: str) -> str:
    return _VERDICT_CLASS.get(verdict, "verdict-no")


def _card(unit: dict) -> str:
    verdict = str(unit.get("verdict", ""))
    traj = str(unit.get("trajectory", "structural"))
    badges = [f'<span class="badge {_vclass(verdict)}">{_esc(verdict)}</span>']
    if traj == "escalating":
        badges.append('<span class="badge badge-esc">escalating</span>')
    if unit.get("provisional"):
        badges.append('<span class="badge">provisional n</span>')
    return (
        f'<div class="card" onclick="openUnit({json.dumps(str(unit.get("unit_id")))})">'
        f"<h3>{_esc(unit.get('name'))}</h3>"
        f"{''.join(badges)}"
        f'<div class="meta"><span>{_esc(unit.get("n_sessions"))} sessions</span>'
        f"<span>leverage {_esc(unit.get('leverage'))}</span>"
        f"<span>score {_esc(unit.get('score'))}</span>"
        f"<span>rung {_esc(unit.get('rung'))}</span></div></div>"
    )


def _rows(units: list[dict], *, show_remediation: bool) -> str:
    out: list[str] = []
    for unit in units:
        verdict = str(unit.get("verdict", ""))
        cells = [
            f"<td>{_esc(unit.get('name'))}</td>",
            f"<td>{_esc(unit.get('n_sessions'))}</td>",
            f"<td>{_esc(unit.get('leverage'))}</td>",
            f"<td>{_esc(unit.get('score'))}</td>",
            f'<td><span class="badge {_vclass(verdict)}">{_esc(verdict)}</span></td>',
        ]
        if show_remediation:
            cells.append(f"<td>{_esc(unit.get('failed_subtest') or '-')}</td>")
            cells.append(f"<td>{_esc(unit.get('remediation') or '')}</td>")
        uid = json.dumps(str(unit.get("unit_id")))
        out.append(f'<tr class="row" onclick="openUnit({uid})">{"".join(cells)}</tr>')
    return "\n".join(out)


def _units_index(*groups: list[dict]) -> dict:
    index: dict[str, dict] = {}
    for group in groups:
        for unit in group:
            fit_detail = unit.get("fit_detail") or {}
            index[str(unit.get("unit_id"))] = {
                "name": unit.get("name"),
                "gist": unit.get("gist"),
                "verdict": unit.get("verdict"),
                "vclass": _vclass(str(unit.get("verdict", ""))),
                "failed_subtest": unit.get("failed_subtest"),
                "remediation": unit.get("remediation"),
                "n_sessions": unit.get("n_sessions"),
                "n_members": unit.get("n_members", len(unit.get("members") or [])),
                "leverage": unit.get("leverage"),
                "score": unit.get("score"),
                "author": unit.get("author"),
                "recovery": unit.get("recovery"),
                "confidence": unit.get("confidence"),
                "trajectory": unit.get("trajectory"),
                "rung": unit.get("rung"),
                "provisional": unit.get("provisional"),
                "cycle": fit_detail.get("cycle"),
                "gate": fit_detail.get("gate"),
            }
    return index


def _primer_section() -> str:
    """The learn-about primer — fixed template, rendered at most ONCE."""
    parts = "".join(f"<dt>{_esc(head)}</dt><dd>{_esc(body)}</dd>" for head, body in T.PRIMER_PARTS)
    return (
        f'<h2 id="primer-section">{_esc(T.PRIMER_TITLE)}</h2>'
        f'<div class="primer"><p class="note">{_esc(T.PRIMER_LEAD)}</p>'
        f"<dl>{parts}</dl>"
        f'<p class="note">{_esc(T.PRIMER_LINK_LEAD)} '
        f'<a href="{_esc(T.EXPLAINER_URL)}">{_esc(T.EXPLAINER_URL)}</a></p></div>'
    )


def _demo_stats(demo: dict) -> str:
    stats = demo.get("stats") or {}
    cells = [
        ("distinct sessions", stats.get("n_sessions")),
        ("median tool calls", stats.get("med_tool_calls")),
        ("median LLM cycles", stats.get("med_llm_cycles")),
        ("median span (s)", stats.get("med_span_s")),
        ("errors per session", stats.get("err_rate")),
    ]
    body = "".join(f"<span>{_esc(label)} <b>{_esc(value)}</b></span>" for label, value in cells)
    if stats.get("provisional"):
        body += "<span><b>provisional</b> (n in {2,3})</span>"
    return f'<div class="statline">{body}</div>'


def _demo_fit(demo: dict) -> str:
    fit = demo.get("fit") or {}
    narrative = demo.get("narrative") or {}
    rows = [
        (T.FIT_Q1, "yes" if fit.get("cycle") else "no", narrative.get("q1_cycle_note")),
        (T.FIT_Q2, "yes" if fit.get("gate") else "no", narrative.get("q2_gate_note")),
        (
            T.FIT_Q3,
            T.RECOVERY_RENDER.get(str(fit.get("recovery")), str(fit.get("recovery"))),
            narrative.get("q3_recovery_note"),
        ),
    ]
    return "".join(
        f"<p><b>{_esc(question)}</b> {_esc(verdict)}<br><span class='note'>{_esc(note)}</span></p>"
        for question, verdict, note in rows
    )


def _demo_math(demo: dict) -> str:
    math = demo.get("convergence_math") or {}
    chain = math.get("chain_len")
    once = math.get("once_through")
    gated = math.get("gated_loop")
    budget = math.get("budget")
    p_step = math.get("p_step")
    return (
        f'<p class="note">{_esc(T.MATH_LEAD)}</p>'
        f'<ul class="math">'
        f"<li>chain length taken from your verified median LLM cycles: <b>{_esc(chain)}</b></li>"
        f"<li>illustrative per-step success rate: <b>{_esc(p_step)}</b></li>"
        f"<li>once-through, everything right the first time: <b>{_esc(once)}</b></li>"
        f"<li>gated loop, within its budget of <b>{_esc(budget)}</b> attempt(s): <b>{_esc(gated)}</b></li>"
        f"</ul>"
        f'<p class="note">{_esc(T.MATH_LABEL)}</p>'
    )


def _demo_panel(demo: dict) -> str:
    """Section 10 — the three-part self-certification answer, every time."""
    ver = demo.get("verification") or {}
    level = str(ver.get("level", T.LEVEL_NONE))
    lint_verdict = ver.get("lint_verdict")
    not_run = ver.get("lint_not_run_reason")

    machine: list[str] = [f'<p class="lvl">verification level: <b>{_esc(level)}</b></p>']
    if level == T.LEVEL_NONE:
        machine.append(f"<p><b>{_esc(T.LABEL_UNVERIFIED)}</b></p>")
        if not_run:
            machine.append(f'<p class="note">{_esc(not_run)}</p>')
        machine.append(
            f"<p class='note'>Run both yourself: <code>attractor lint {_esc(demo.get('dot_relpath'))}</code>"
            f" and the bundled authoring-contract checker.</p>"
        )
    else:
        if lint_verdict:
            machine.append(f"<p><b>attractor lint</b>, verbatim:</p><pre class='cmd'>{_esc(lint_verdict)}</pre>")
        elif not_run:
            machine.append(f"<pre class='cmd'>{_esc(not_run)}</pre>")
        if ver.get("doctrine_report"):
            machine.append(
                f"<p><b>authoring contract</b> ({_esc(ver.get('doctrine_verdict'))}), verbatim:</p>"
                f"<pre class='cmd'>{_esc(ver.get('doctrine_report'))}</pre>"
            )

    invocation = demo.get("invocation") or {}
    unverified_class = " unverified" if level == T.LEVEL_NONE else ""
    return (
        f"<h3>{_esc(T.SEC_PANEL)}</h3>"
        f'<div class="panel{unverified_class}"><h4>1. {_esc(T.PANEL_PART1_TITLE)}</h4>{"".join(machine)}</div>'
        f'<div class="panel"><h4>2. {_esc(T.PANEL_PART2_TITLE)}</h4>'
        f'<p class="note">{_esc(T.PANEL_PART2_BODY)}</p></div>'
        f'<div class="panel"><h4>3. {_esc(T.PANEL_PART3_TITLE)}</h4>'
        f'<p class="note">{_esc(T.PANEL_PART3_BODY)}</p>'
        f"<pre class='cmd'>{_esc(invocation.get('author_cmd'))}</pre>"
        f"<p class='note'>The CLI itself, if you do not have it:</p>"
        f"<pre class='cmd'>{_esc(invocation.get('install_cmd'))}</pre></div>"
    )


def _demo_section(demo: dict) -> str:
    narrative = demo.get("narrative") or {}
    fit = demo.get("fit") or {}
    verdict = str(fit.get("verdict", ""))
    walk = "".join(
        f"<li><code>{_esc(step.get('node'))}</code> — {_esc(step.get('note'))}</li>"
        for step in (narrative.get("pipeline_walk") or [])
    )
    entries = "".join(
        f"<li><b>{_esc(label)}</b> — <code>{_esc(target)}</code>: {_esc(why)}</li>"
        for label, target, why in T.ENTRY_POINTS
    )
    invocation = demo.get("invocation") or {}
    stats = demo.get("stats") or {}
    return (
        f'<h2 class="demo-heading">Demonstration — {_esc(demo.get("name"))}</h2>'
        f'<div class="demo">'
        f'<span class="badge {_vclass(verdict)}">{_esc(verdict)}</span>'
        f"{'<span class="badge">provisional n</span>' if stats.get('provisional') else ''}"
        f'<p class="note">{_esc(T.DEMO_LEAD)}</p>'
        f"{_demo_stats(demo)}"
        f"<h3>{_esc(T.SEC_COST)}</h3>"
        f"<p class='note'>Across {_esc(stats.get('n_sessions'))} distinct sessions this unit ran a median of "
        f"{_esc(stats.get('med_tool_calls'))} tool calls and {_esc(stats.get('med_llm_cycles'))} LLM cycles, "
        f"taking a median of {_esc(stats.get('med_span_s'))} seconds of wall time, with "
        f"{_esc(stats.get('err_rate'))} tool errors per session — every one of those numbers re-verified "
        f"against your own records before it was printed here.</p>"
        f"<h3>{_esc(T.SEC_GIST)}</h3><p>{_esc(narrative.get('scenario_gist'))}</p>"
        f"<h3>{_esc(T.SEC_FIT)}</h3>{_demo_fit(demo)}"
        f"<h3>{_esc(T.SEC_WALK)}</h3><ul class='walk'>{walk}</ul>"
        f"<pre class='dotsrc'>{_esc(demo.get('dot_text'))}</pre>"
        f"<p class='note'>Written to <a href=\"{_esc(demo.get('dot_relpath'))}\">"
        f"<code>{_esc(demo.get('dot_relpath'))}</code></a>, with its companion at "
        f'<a href="{_esc(demo.get("companion_relpath"))}"><code>{_esc(demo.get("companion_relpath"))}</code></a>, '
        f"beside this file.</p>"
        f"<h3>{_esc(T.SEC_MATH)}</h3>{_demo_math(demo)}"
        f"<h3>{_esc(T.SEC_PAYOFF)}</h3><p>{_esc(narrative.get('payoff_note'))}</p>"
        f"<h3>{_esc(T.SEC_ENTRY)}</h3><ul class='walk'>{entries}</ul>"
        f"<h3>{_esc(T.SEC_RUN)}</h3>"
        f"<pre class='cmd'>{_esc(invocation.get('run_cmd'))}</pre>"
        f"{_demo_panel(demo)}"
        f"</div>"
    )


def _demos_block(demos: dict | None) -> str:
    """Primer + one section per demo. Empty string when nothing was supplied."""
    if not demos:
        return ""
    body = _primer_section() if demos.get("primer", True) else ""
    for demo in demos.get("demos") or []:
        body += _demo_section(demo)
    return f"\n{body}\n" if body else ""


def render_html(
    result: dict,
    *,
    tier_note: str = "",
    generated_at: str | None = None,
    demos: dict | None = None,
) -> str:
    """Render the ranked result to a single self-contained HTML string.

    `demos=None` reproduces the pre-demonstration-layer artifact byte for
    byte: every demonstration byte, CSS included, is inside a conditional.
    """
    from .ranking import sample_simple_to_complex

    opportunities = result.get("opportunities", [])
    honest_nos = result.get("honest_no", [])
    waste = result.get("waste_findings", [])
    summary = result.get("summary", {})
    sample = sample_simple_to_complex(opportunities)

    sample_cards = "".join(_card(u) for u in sample) or "<p class='note'>Nothing cleared the frequency floor.</p>"
    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Every demonstration byte is conditional, CSS included: with demos=None
    # the artifact below is byte-identical to the pre-demo-layer output.
    demo_block = _demos_block(demos)
    demo_css = _DEMO_CSS if demo_block else ""
    n_demonstrated = len((demos or {}).get("demos") or [])
    demo_count_note = f" &middot; {_esc(n_demonstrated)} demonstrated" if n_demonstrated else ""
    data_json = json.dumps({"units": _units_index(opportunities, honest_nos)}, sort_keys=True)
    script = _JS.replace("__DATA__", data_json)

    waste_rows = "\n".join(
        f"<tr><td>{_esc(w.get('name') or w.get('unit_id'))}</td>"
        f"<td>{_esc(w.get('n_sessions'))}</td>"
        f"<td>{_esc(w.get('reclaimable_hours'))} h</td>"
        f'<td><span class="badge badge-waste">{_esc(w.get("author"))}</span></td></tr>'
        for w in waste
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(SKILL_TITLE)} report</title>
<style>{_CSS}{demo_css}</style></head><body><div class="wrap">
<h1>{_esc(SKILL_TITLE)}</h1>
<p class="sub">Generated {_esc(stamp)} &middot; own data only, computed locally &middot;
{_esc(summary.get("n_opportunities", 0))} opportunities &middot;
{_esc(summary.get("n_honest_no", 0))} honest-NOs &middot;
{_esc(summary.get("n_waste", 0))} waste findings{demo_count_note}{(" &middot; " + _esc(tier_note)) if tier_note else ""}</p>

<h2>A range of what you already do (simple &rarr; complex)</h2>
<p class="note">Sampled across the grain axis, not the top of the ranking &mdash; this shows
breadth. The ranked list below orders everything.</p>
<div class="grid">{sample_cards}</div>
{demo_block}
<h2>Ranked opportunities</h2>
<table><thead><tr><th>Unit</th><th>Sessions</th><th>Leverage</th><th>Score</th><th>Verdict</th></tr></thead>
<tbody>{_rows(opportunities, show_remediation=False) or '<tr><td colspan="5">none</td></tr>'}</tbody></table>

<h2>Honest NOs &mdash; recurring, costly, and still not an attractor</h2>
<p class="note">These are first-class findings. Each names which sub-test it failed and what
would change the answer. <code>unproven</code> means resilience was never observed &mdash;
that is a caveat on an opportunity, never a failure.</p>
<table><thead><tr><th>Unit</th><th>Sessions</th><th>Leverage</th><th>Score</th><th>Verdict</th>
<th>Failed</th><th>What would change it</th></tr></thead>
<tbody>{_rows(honest_nos, show_remediation=True) or '<tr><td colspan="7">none</td></tr>'}</tbody></table>

<h2>Waste findings &mdash; the machine talking to itself</h2>
<p class="note">Recurring harness ceremony. Not opportunities to act on; time to reclaim.</p>
<table><thead><tr><th>Unit</th><th>Sessions</th><th>Wall time</th><th>Author</th></tr></thead>
<tbody>{waste_rows or '<tr><td colspan="4">none</td></tr>'}</tbody></table>

</div>
<div class="modal" id="modal" onclick="if(event.target===this)closeModal()">
<div class="modal-inner" id="modal-body"></div></div>
<script>{script}</script></body></html>
"""


def write_report(
    result: dict,
    out_path: str | Path | None = None,
    *,
    tier_note: str = "",
    generated_at: str | None = None,
    demos: dict | None = None,
) -> Path:
    """Write the artifact. Default target is the CURRENT WORKING DIRECTORY."""
    path = Path(out_path) if out_path else Path.cwd() / f"{ARTIFACT_STEM}-report.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_html(result, tier_note=tier_note, generated_at=generated_at, demos=demos),
        encoding="utf-8",
    )
    return path
