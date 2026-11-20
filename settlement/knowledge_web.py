"""Analyst search and source views for the policy library."""

from urllib.parse import urlencode

from settlement.knowledge import Scope
from settlement.web import form, html, money


EXAMPLES = (
    ("Combined dispute fees", "What is the combined fee to open a card dispute and prepare a representment packet?"),
    ("Payout cutoff", "What's the last UTC submission time for an automatic payout to go out on the same day?"),
    ("Ambiguous bank credits", "The statement shows two indistinguishable deposits on the same day. What do we ask the processor to provide before matching?"),
    ("Missing policy", "What is the combined price of opening a card dispute and shipping a paper dispute packet internationally?"),
)


def scope_from(fields, library):
    workspace, market = fields["scope"].split("/")
    scope = Scope(workspace, market, fields["effective_on"], fields["known_on"])
    if (workspace, market) not in library.corpus.scopes:
        raise ValueError("Choose a workspace and market in the library")
    return scope


def source_card(hit, scope):
    workspace, market, _, edition = hit.id.split("#")[0].split("/")
    link = urlencode({"passage": hit.id, "scope": f"{workspace}/{market}",
                      "effective_on": scope.effective_on, "known_on": scope.known_on})
    amount = f'<strong class="policy-amount">{money(hit.cents)}</strong>' if hit.cents is not None else ""
    return (f'<article class="policy-citation"><small>{html(hit.title)} · {html(hit.section)}</small>'
            f'<a href="/knowledge/source?{html(link)}">{html(hit.label)}</a>{amount}'
            f'<blockquote>{html(hit.text)}</blockquote><small>{html(edition.upper())} · '
            f'Effective {html(hit.effective)} · Published {html(hit.published)}</small></article>')


def view(library, csrf, scope=None, question="", result=None, error=""):
    if scope is None:
        workspace, market = library.corpus.scopes[0]
        latest = max((dates for family, versions in library.corpus.editions.items()
                      if family[:2] == (workspace, market) for dates in versions))
        scope = Scope(workspace, market, latest[0], max(latest))
        if (workspace, market) == ("atlas-pay", "us"):
            scope = Scope(workspace, market, "2025-04-12", "2025-05-03")
    active = (scope.workspace, scope.market)
    options = []
    for workspace, market in library.corpus.scopes:
        label = f"{workspace.replace('-', ' ').title()} · {market.upper()}"
        selected = " selected" if (workspace, market) == active else ""
        options.append(f'<option value="{html(workspace)}/{html(market)}"{selected}>{html(label)}</option>')
    live = library.models is not None
    examples = "".join(f'<a class="source" href="/knowledge?example={i}">{html(label)}</a>'
                       for i, (label, _) in enumerate(EXAMPLES))
    controls = f"""<label class="policy-question">Question<textarea name="question" rows="2" maxlength="500"
required placeholder="Ask about a payment policy or a fee…">{html(question)}</textarea></label>
<div class="policy-filters"><label>Workspace and market<select name="scope">{''.join(options)}</select></label>
<label>Policy effective on<input type="date" name="effective_on" value="{html(scope.effective_on)}" required></label>
<label>Known on<input type="date" name="known_on" value="{html(scope.known_on)}" required></label>
<button>{'Ask policy library' if live else 'Search policies'}</button></div>"""
    search_form = form("knowledge", csrf, {}, controls)
    status = "Hybrid search · Local Qwen3" if live else "Keyword search"
    output = f'<p role="alert">{html(error)}</p>' if error else ""
    if result and result["status"] == "answered":
        total = result["total_cents"]
        calculation = ""
        if total is not None:
            inputs = " + ".join(money(hit.cents) for hit in result["citations"])
            calculation = (f'<div class="policy-total"><span>Combined fee</span><strong>{money(total)}</strong>'
                           f'<small>{inputs} · USD</small></div>')
        references = "".join(source_card(hit, scope) for hit in result["citations"])
        output = (f'<section class="panel body-panel policy-output"><div class="policy-answer-head">'
                  f'<h2>Answer from policy</h2><span class="badge reconciled">Cited</span></div>{calculation}{references}</section>')
    elif result and result["status"] == "abstained":
        output = ('<section class="panel body-panel policy-output"><h2>Insufficient evidence</h2>'
                  '<p>The available policies did not support a complete answer for this question and scope.</p></section>')
    if result:
        references = "".join(source_card(hit, scope) for hit in result["hits"])
        found = references or '<p>No passages matched this question and scope.</p>'
        if result["status"] == "search":
            output += f'<section class="panel body-panel policy-output"><h2>Search results</h2>{found}</section>'
        else:
            count = len(result["hits"])
            output += (f'<section class="panel body-panel policy-output"><details><summary>Retrieved passages ({count})</summary>'
                       f'{found}</details><small>{result["elapsed_ms"] / 1000:.2f} s · {html(scope.effective_on)} effective · '
                       f'{html(scope.known_on)} known</small></section>')
    return f"""<div class="heading"><div><h1>Payments knowledge</h1>
<p>Find settlement procedures and fees in the policy version available on a specified date.</p></div></div>
<div class="policy-layout"><div>
<section class="panel body-panel policy-form"><div class="policy-answer-head"><h2>Search the policy library</h2><small>{status}</small></div>
{search_form}<div class="source-links">{examples}</div></section>{output}
</div><aside class="panel body-panel policy-about"><h2>Policy dates</h2><dl><dt>Policy effective on</dt>
<dd>The date the procedure or fee applies to.</dd><dt>Known on</dt>
<dd>The last date a policy could have been published and still be considered.</dd></dl>
<small>{len(library.corpus.passages):,} passages · {len(library.corpus.scopes)} workspace and market combinations</small></aside></div>"""


def source_view(library, scope, passage_id):
    hit = next((hit for hit in library.corpus.visible(scope) if hit.id == passage_id), None)
    if hit is None:
        raise ValueError("Policy passage unavailable in this scope")
    return (f'<div class="crumb"><a href="/knowledge">Payments knowledge</a> / Source passage</div>'
            f'<div class="heading"><h1>{html(hit.title)}</h1></div><section class="panel body-panel">'
            f'{source_card(hit, scope)}<p>Passage ID <code>{html(hit.id)}</code></p>'
            f'<p>Source SHA-256 <code>{html(hit.source_sha256)}</code></p></section>')
