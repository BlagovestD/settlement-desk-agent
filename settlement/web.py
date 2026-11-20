"""Loopback analyst workbench and HTML form endpoints."""

import hmac
import json
import secrets
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from settlement.analysis import REQUESTS
from settlement.service import case_detail, cases, queue, review, run_one
from settlement.storage import database


def html(value):
    return escape(str(value), quote=True)


def money(cents, sign=False):
    if cents is None:
        return "Unknown"
    prefix = "−" if cents < 0 else "+" if cents and sign else ""
    whole, remainder = divmod(abs(cents), 100)
    return f"{prefix}${whole:,}.{remainder:02d}"


def page(title, content, mode, refresh=False):
    refresh_tag = '<meta http-equiv="refresh" content="2">' if refresh else ""
    mode_label = {"live": "Live local model", "replay": "Recorded model replay", "baseline": "Rules-only mode"}[mode]
    knowledge_page = title in {"Payments knowledge", "Policy source"}
    queue_current = '' if knowledge_page else ' aria-current="page"'
    knowledge_current = ' aria-current="page"' if knowledge_page else ''
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{refresh_tag}
<title>{html(title)} · Settlement Desk</title><link rel="stylesheet" href="/style.css"></head>
<body><header class="top"><a href="/" class="brand"><span class="logo">≡</span> Settlement Desk</a>
<nav aria-label="Workspace"><a href="/"{queue_current}>Close queue</a><a href="/knowledge"{knowledge_current}>Knowledge</a></nav><span class="mode">{mode_label}</span></header>
<main>{content}</main><footer>Synthetic workspace <span>USD · Automatic payouts</span></footer></body></html>"""


def form(action, csrf, fields, controls):
    values = {"token": csrf, **fields}
    hidden = "".join(f'<input type="hidden" name="{html(k)}" value="{html(v)}">' for k, v in values.items())
    return f'<form action="/{action}" method="post">{hidden}{controls}</form>'


def badge(state):
    return f'<span class="badge {state.replace(" ", "-")}">{html(state.capitalize())}</span>'


def dashboard(items, query):
    counts = {key: sum(case["state"] == key for case in items)
              for key in ("open", "explained", "evidence requested", "reconciled")}
    selected = query.get("state", [""])[0]
    floor = query.get("minimum", [""])[0]
    from_day = query.get("from", [""])[0]
    filtered = []
    for row in items:
        delta = row["summary"]["differences"]
        if selected and selected != row["state"]:
            continue
        if floor.isdecimal() and max(abs(d or 0) for d in delta.values()) < int(floor) * 100:
            continue
        if from_day and row["summary"]["cutoff"][:10] < from_day:
            continue
        filtered.append(row)
    cards = "".join(f'<div class="metric"><span>{html(label.capitalize())}</span><strong>{number:02d}</strong></div>'
                    for label, number in counts.items())
    options = '<option value="">All states</option>' + "".join(
        f'<option {"selected" if key == selected else ""}>{html(key)}</option>' for key in counts)
    lines = "".join(f"""<tr><td><a class="case-link" href="/cases/{row['snapshot']}">{html(row['case_id'])}</a>
<small>{html(row['account'])} · v{row['version']}</small></td>
<td>{html(row['summary']['cutoff'][:10])}</td><td class="money">{money(row['summary']['payout'])}</td>
<td class="money">{money(row['summary']['differences']['merchant'], True)}</td>
<td class="money">{money(row['summary']['differences']['bank'], True)}</td><td>{badge(row['state'])}</td></tr>"""
                    for row in filtered)
    empty = '<tr><td colspan="6">No payouts match these filters.</td></tr>'
    return f"""<div class="heading"><div><h1>Daily close</h1><p>Track settlement differences and review the evidence behind each payout.</p></div></div>
<div class="metric-grid">{cards}</div><section class="panel">
<div class="panel-header"><h2>Payout queue</h2><span>{len(filtered)} of {len(items)} payouts</span></div>
<form method="get" action="/" class="filters"><label>State<select name="state">{options}</select></label>
<label>Minimum variance (USD)<input type="number" min="0" max="1000000000" name="minimum" value="{html(floor)}"></label>
<label>Close date from<input type="date" name="from" value="{html(from_day)}"></label>
<button class="secondary">Apply filters</button></form>
<div class="scroll"><table><thead><tr><th>Case</th><th>Close date</th><th>Payout</th><th>Ledger variance</th><th>Bank variance</th><th>Status</th></tr></thead>
<tbody>{lines or empty}</tbody></table></div></section>"""


def detail(path, snapshot, csrf, mode):
    with database(path) as db:
        view = case_detail(db, snapshot)
        source_rows = db.execute("SELECT source,line,row_id FROM source_rows WHERE snapshot=? ORDER BY source,line",
                                 (snapshot,)).fetchall()
        versions = db.execute("SELECT id,version FROM snapshots WHERE account=? AND case_id=? ORDER BY version",
                              (view["account"], view["case_id"])).fetchall()
        history = db.execute("""SELECT r.action,r.actor,r.created_at,j.snapshot,f.instruction,f.completed_snapshot
            FROM reviews r JOIN jobs j ON j.id=r.job JOIN snapshots s ON s.id=j.snapshot
            LEFT JOIN followups f ON f.review=r.id WHERE s.account=? AND s.case_id=? ORDER BY r.id""",
                             (view["account"], view["case_id"])).fetchall()
        job = view["job"]
        steps = db.execute("SELECT detail FROM steps WHERE job=? ORDER BY rowid", (job["id"],)).fetchall() if job else []
    s = view["summary"]
    diffs = s["differences"]
    amounts = [("Merchant expectation", s["merchant_net"]), ("Processor components", s["component_net"]),
               ("Payout header", s["payout"]), ("Posted bank credit", s["bank_credit"])]
    tiles = "".join(f'<div class="metric"><span>{label}</span><strong>{money(value)}</strong></div>' for label, value in amounts)
    comparison = "".join(f'<div class="kv"><span>{label}</span><strong>{money(diffs[k], True)}</strong></div>'
                         for k, label in [("merchant", "Components minus merchant"),
                                          ("processor", "Header minus components"), ("bank", "Bank minus header")])
    source_links = [(r["row_id"], f'<a class="source" href="/evidence/{snapshot}/{r["source"]}/{r["line"]}">'
                                  f'{html(r["source"])} · {html(r["row_id"])}</a>') for r in source_rows]
    version_links = " ".join(f'<a class="source" href="/cases/{v["id"]}">v{v["version"]}</a>' for v in versions)
    refresh = bool(job and job["status"] in {"queued", "running"})
    audit = "".join(f'<li><strong>{html(r["action"].capitalize())}</strong> · {html(r["actor"])}'
                    f'<small>{html(r["created_at"])} UTC · <a href="/cases/{r["snapshot"]}">Reviewed snapshot</a></small>'
                    f'<p>{html(r["instruction"] or "")} {("<strong>Completed</strong>" if r["completed_snapshot"] else "")}</p></li>' for r in history)
    timeline = []
    for row in steps:
        entry = json.loads(row["detail"])
        label = entry.get("tool", "Model error").replace("_", " ").capitalize()
        timeline.append(f'<details><summary>{html(label)}</summary><pre>{html(json.dumps(entry, indent=2))}</pre></details>')
    if job and job["result"]:
        result = json.loads(job["result"])
        f = result["finding"]
        links = "".join(link for key, link in source_links if key in f["evidence_ids"])
        proposal = f.get("proposal")
        supporting = ""
        if proposal:
            quotes = "".join(f'<blockquote>{html(c["quote"])}</blockquote>' for c in proposal["citations"])
            supporting = f'<p>Suggested bank trace <strong>{html(proposal["trace"])}</strong></p>{quotes}'
        actions = ""
        if not job["action"] and view["state"] != "reconciled" and snapshot == versions[-1]["id"]:
            accept = ('<button name="action" value="accept">Accept explanation</button>'
                      if f["reason"] not in REQUESTS and result["outcome"] in {"verified", "rules"} else "")
            controls = accept + ('<button class="secondary" name="action" value="request">Request evidence</button>'
                                 '<button class="secondary" name="action" value="reject">Reject</button>')
            actions = form("review", csrf, {"job": job["id"], "request_key": secrets.token_hex(16)}, controls)
        result_html = (f'<h3>{html(f["reason"].replace("_", " ").capitalize())}</h3>'
                       f'<p>{html(f["next_action"])}</p>{supporting}<div class="kv"><span>Residual</span><strong>{money(f["residual_cents"], True)}</strong></div>'
                       f'<div class="source-links">{links}</div>{actions}')
    elif refresh:
        result_html = '<p role="status">Investigation in progress. This page refreshes automatically.</p>'
    elif view["state"] == "reconciled":
        result_html = '<p>All three comparisons balance and the bank credit has a unique reference.</p>'
    elif snapshot != versions[-1]["id"]:
        result_html = '<p>Historical snapshot.</p>'
    else:
        result_html = form("investigate", csrf, {"snapshot": snapshot}, "<button>Run investigation</button>")
    return f"""<div class="crumb"><a href="/">Close queue</a> / Payout</div>
<div class="heading"><div><h1>{html(view['case_id'])}</h1><p>{html(view['account'])} · close {html(s['cutoff'])}</p></div>{badge(view['state'])}</div>
<div class="metric-grid">{tiles}</div><div class="detail-grid">
<section class="panel body-panel"><h2>Investigation</h2>{result_html}
<h2>Tool activity</h2>{''.join(timeline) or '<p>No tool activity for this snapshot.</p>'}</section>
<aside class="panel body-panel"><h2>Reconciliation</h2>{comparison}
<h2>Snapshots</h2><div class="source-links">{version_links}</div>
<h2>Review history</h2><ul class="audit">{audit or '<li>No reviewer decision yet.</li>'}</ul></aside></div>
<section class="panel body-panel"><h2>Source records</h2><div class="source-links">{''.join(link for _,link in source_links)}</div></section>""", refresh


def application(path, port=8765, mode="baseline", chat=None, model="", context=4096, actor="local analyst", replay=None,
                knowledge=None):
    from settlement.knowledge_web import EXAMPLES, scope_from, source_view, view

    token = secrets.token_urlsafe(24)
    event = threading.Event()
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            event.wait(1)
            event.clear()
            while not stop.is_set() and run_one(path, chat, model, context, replay=replay) is not None:
                pass

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, body, content_type="text/html; charset=utf-8", status=200, location=None):
            body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlsplit(self.path)
            parts = route.path.strip("/").split("/")
            try:
                if route.path == "/style.css":
                    self.respond(Path(__file__).with_name("style.css").read_text(encoding="utf-8"), "text/css; charset=utf-8")
                elif route.path == "/health":
                    self.respond("ready", "text/plain")
                elif route.path == "/":
                    self.respond(page("Daily close", dashboard(cases(path), parse_qs(route.query)), mode))
                elif route.path == "/knowledge" and knowledge is not None:
                    params = parse_qs(route.query)
                    example = params.get("example", [""])[0]
                    question = EXAMPLES[int(example)][1] if example in {str(i) for i in range(len(EXAMPLES))} else ""
                    self.respond(page("Payments knowledge", view(knowledge, token, question=question), mode))
                elif route.path == "/knowledge/source" and knowledge is not None:
                    fields = {key: value[0] for key, value in parse_qs(route.query).items()}
                    body = source_view(knowledge, scope_from(fields, knowledge), fields["passage"])
                    self.respond(page("Policy source", body, mode))
                elif parts[0] == "cases" and len(parts) == 2:
                    body, refresh = detail(path, int(parts[1]), token, mode)
                    self.respond(page("Payout review", body, mode, refresh))
                elif parts[0] == "evidence" and len(parts) == 4:
                    snapshot, source, line = int(parts[1]), parts[2], int(parts[3])
                    with database(path) as db:
                        row = db.execute("SELECT * FROM source_rows WHERE snapshot=? AND source=? AND line=?",
                                         (snapshot, source, line)).fetchone()
                    if not row:
                        raise ValueError("Source record unavailable")
                    raw = html(json.dumps(json.loads(row["raw"]), indent=2))
                    self.respond(page("Source evidence", f'<a href="/cases/{snapshot}">Back to payout</a><h1>Source evidence</h1>'
                                      f'<section class="panel body-panel"><h2>{html(source)} · Line {line}</h2>'
                                      f'<p>SHA-256 <code>{html(row["file_digest"])}</code></p><pre>{raw}</pre></section>', mode))
                else:
                    self.respond(page("Page unavailable", "<h1>Page unavailable</h1>", mode), status=404)
            except (ValueError, KeyError) as exc:
                self.respond(page("Record unavailable", f"<h1>{html(exc)}</h1>", mode), status=404)

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("Invalid form size")
                fields = {k: v[0] for k, v in parse_qs(self.rfile.read(size).decode()).items()}
                if not hmac.compare_digest(token, fields.get("token", "")):
                    self.respond("Invalid form token", "text/plain", status=403)
                    return
                if self.path == "/investigate":
                    snapshot = int(fields["snapshot"])
                    queue(path, snapshot, mode)
                    event.set()
                elif self.path == "/review":
                    job = int(fields["job"])
                    review(path, job, fields["action"], fields["request_key"], actor)
                    with database(path) as db:
                        snapshot = db.execute("SELECT snapshot FROM jobs WHERE id=?", (job,)).fetchone()[0]
                elif self.path == "/knowledge" and knowledge is not None:
                    scope, question = None, fields.get("question", "")[:500]
                    try:
                        scope = scope_from(fields, knowledge)
                        body = view(knowledge, token, scope, question, knowledge.answer(scope, fields.get("question", "")))
                        self.respond(page("Payments knowledge", body, mode))
                    except (KeyError, ValueError) as exc:
                        body = view(knowledge, token, scope, question, error=str(exc))
                        self.respond(page("Payments knowledge", body, mode), status=400)
                    except (OSError, TimeoutError):
                        body = view(knowledge, token, scope, question, error="The local model is unavailable. Try again shortly.")
                        self.respond(page("Payments knowledge", body, mode), status=503)
                    return
                else:
                    raise ValueError("Unknown action")
                self.respond("", status=303, location=f"/cases/{snapshot}")
            except (KeyError, ValueError) as exc:
                self.respond(page("Review unavailable", f"<h1>Review unavailable</h1><p>{html(exc)}</p><a href='/'>Close queue</a>", mode),
                             status=409)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    background = threading.Thread(target=worker, daemon=True)
    background.start()
    return server, stop, event
