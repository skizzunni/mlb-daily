#!/usr/bin/env python3
"""Emit an Artifact-ready page (no doctype/html/head/body wrapper) from the
same data build.py produces. Usage: python3 artifact.py > site/board.html"""
import json, os, sys, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build as B

D = lambda n, d: B.load_json(os.path.join(HERE, "data", n), d)


def esc(x):
    return (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def main():
    data = json.load(open(os.path.join(HERE, "site", "data.json")))
    lines, notes = D("lines.json", {}), D("notes.json", {})
    bt = B.backtest_summary()
    games = data["games"]

    rows = []
    for g in games:
        p = g["pick"]
        ln = lines.get(str(g["pk"]), {})
        pa, ph = B.novig(ln.get("ml_away"), ln.get("ml_home"))
        mkt = (ph if p["side"] == "home" else pa) if pa is not None else None
        edge = (p["p"] - mkt) if mkt is not None else None
        rows.append((g, p, ln, mkt, edge))

    plays = [r for r in rows if r[4] is not None and r[4] >= 0.03 and r[1]["tier"] in ("A", "B")]

    def bar(model, market):
        """Two stacked measures on one 0-100 scale."""
        m2 = "" if market is None else (
            '<div class="tick mk" style="left:%.1f%%"></div>' % (market * 100))
        return ('<div class="scale"><div class="fill" style="width:%.1f%%"></div>%s'
                '<div class="mid"></div></div>' % (model * 100, m2))

    cards = []
    for g, p, ln, mkt, edge in rows:
        t = dt.datetime.strptime(g["start_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc)
        t = t.astimezone(B.TZ) if B.TZ else t
        when = t.strftime("%-I:%M %p")
        if edge is None:
            ecls, etxt = "flat", "no line"
        elif edge >= 0.03:
            ecls, etxt = "pos", "+%.1f vs market" % (100 * edge)
        elif edge <= -0.03:
            ecls, etxt = "neg", "%.1f vs market" % (100 * edge)
        else:
            ecls, etxt = "flat", "level with market"
        tier = {"A": "PLAY", "B": "LEAN", "PASS": "PASS"}[p["tier"]]

        why = "".join("<li>%s</li>" % esc(r) for r in p["reasons"][:3])
        nt = notes.get(str(g["pk"]), {})
        ctx = ""
        if nt.get("verified"):
            ctx += ('<div class="ctx"><span class="ctxh">Sourced</span><ul>%s</ul></div>'
                    % "".join("<li>%s</li>" % esc(x) for x in nt["verified"][:3]))
        if nt.get("caution"):
            ctx += ('<div class="ctx warn"><span class="ctxh">Fact-check flag</span><ul>%s</ul></div>'
                    % "".join("<li>%s</li>" % esc(x) for x in nt["caution"]))

        book = ""
        if ln.get("ml_away"):
            book = ('<div class="book"><span>%s %s</span><span>%s %s</span>'
                    '<span>O/U %s</span></div>'
                    % (g["away"]["abbr"], ln["ml_away"], g["home"]["abbr"],
                       ln.get("ml_home", ""), ln.get("total", "-")))

        cards.append("""
<article class="g %s">
  <header class="gh">
    <div class="teams"><b>%s</b><i>%s</i> <em>at</em> <b>%s</b><i>%s</i></div>
    <div class="when">%s</div>
  </header>
  <div class="arms">%s <span class="v">vs</span> %s</div>
  <div class="line">
    <span class="tag %s">%s</span>
    <span class="sel">%s</span>
    <span class="num">%s</span>
    <span class="fair">fair %s</span>
    <span class="edge %s">%s</span>
  </div>
  %s
  %s
  <div class="proj">proj %s %s &ndash; %s %s &middot; total %s</div>
  <ul class="why">%s</ul>
  %s
</article>""" % (
            p["tier"].lower(),
            esc(g["away"]["abbr"]), esc(g["away"]["rec"]),
            esc(g["home"]["abbr"]), esc(g["home"]["rec"]), when,
            esc(g["away"]["sp"]), esc(g["home"]["sp"]),
            p["tier"].lower(), tier, esc(p["team_name"]),
            "%.1f%%" % (100 * p["p"]), p["fair"], ecls, etxt,
            bar(p["p"], mkt), book,
            g["away"]["abbr"], g["away"]["exp_runs"],
            g["home"]["abbr"], g["home"]["exp_runs"], p["mean_total"], why, ctx))

    if plays:
        pl = "".join(
            '<div class="prow"><b>%s %s</b><span>model %.1f%% &middot; market %.1f%%</span>'
            '<b class="pos">+%.1f</b></div>'
            % (esc(p["team_name"]),
               ln.get("ml_home" if p["side"] == "home" else "ml_away", ""),
               100 * p["p"], 100 * mkt, 100 * ed)
            for g, p, ln, mkt, ed in plays)
        readnote = ("The only spots where the model prices above the de-vigged market. "
                    "Everywhere else the market is level or ahead &mdash; those are not bets, "
                    "at any leg count.")
    else:
        pl = '<div class="prow none">Nothing prices above the market today.</div>'
        readnote = "The honest answer is no play &mdash; not a smaller play."

    btb = ""
    if bt:
        ab_n = bt["AB"][0] + bt["AB"][1]
        r = bt["AB"][0] / ab_n if ab_n else 0
        pn = bt["PASS"][0] + bt["PASS"][1]
        btb = """
<section class="bt">
  <h2>Backtest &middot; %d games &middot; %s to %s</h2>
  <div class="btg">
    <div class="btc"><span>Play + Lean</span><b>%d&ndash;%d</b><i>%.1f%%</i></div>
    <div class="btc bad"><span>Pass tier</span><b>%d&ndash;%d</b><i>%.1f%%</i></div>
  </div>
  <div class="parlay">
    <span>At %.1f%% a leg:</span>
    <div class="pgrid">%s</div>
  </div>
  <p class="fine">Uses current-season stats on past games, so it flatters the model. A calibration
  check, not a profit claim &mdash; no closing prices were captured.</p>
</section>""" % (bt["n"], bt["from"], bt["to"], bt["AB"][0], bt["AB"][1], 100 * r,
                 bt["PASS"][0], bt["PASS"][1], 100.0 * bt["PASS"][0] / pn if pn else 0,
                 100 * r,
                 "".join('<div class="pl%s"><b>%d</b><span>%s</span></div>'
                         % (" danger" if n >= 5 else "", n,
                            ("%.1f%%" % (100 * r ** n)) if r ** n < 0.1 else
                            ("%.0f%%" % (100 * r ** n)))
                         for n in (1, 2, 3, 5, 9)))

    out = PAGE
    for k, v in (("@DATE@", data["date"]), ("@GEN@", data["generated"]),
                 ("@N@", str(len(games))), ("@PLAYS@", pl), ("@READNOTE@", readnote),
                 ("@BT@", btb), ("@CARDS@", "".join(cards))):
        out = out.replace(k, v)
    print(out)


PAGE = """<title>MLB Model Board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root{
  --ground:#0c1216; --panel:#121b21; --panel2:#0f171c; --rule:#1e2c35;
  --ink:#e9f0f4; --dim:#8ba1ae; --faint:#5d7280;
  --jade:#46b47f; --jade-dim:#1b3a2c; --amber:#d99a2b; --rust:#d95f5f;
  --display:'Barlow Condensed','Haettenschweiler','Arial Narrow',sans-serif;
  --body:'Barlow','Helvetica Neue',Arial,sans-serif;
  --mono:'IBM Plex Mono','SF Mono',Menlo,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--body);
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:26px 18px 60px}
h1{font-family:var(--display);font-size:38px;font-weight:700;letter-spacing:.01em;
  margin:0;line-height:1;text-transform:uppercase}
h1 span{color:var(--jade)}
.meta{color:var(--dim);font-size:13px;margin-top:7px;font-family:var(--mono);
  display:flex;gap:14px;flex-wrap:wrap}
h2{font-family:var(--display);font-size:15px;font-weight:600;text-transform:uppercase;
  letter-spacing:.1em;color:var(--dim);margin:0 0 12px}
section{margin-top:30px}
.read{border:1px solid var(--jade-dim);background:linear-gradient(180deg,#10201a,#0e1a16);
  border-radius:4px;padding:16px 18px;margin-top:22px}
.read h2{color:var(--jade)}
.prow{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
  padding:7px 0;border-bottom:1px solid var(--jade-dim)}
.prow:last-of-type{border-bottom:0}
.prow b{font-family:var(--display);font-size:21px;font-weight:600;letter-spacing:.01em}
.prow span{color:var(--dim);font-size:13px;font-family:var(--mono);margin-right:auto}
.prow .pos{color:var(--jade);font-family:var(--mono);font-size:17px}
.prow.none{color:var(--dim)}
.readnote{font-size:13px;color:#b6c8d2;margin-top:11px;padding-top:11px;
  border-top:1px solid var(--jade-dim)}
.btg{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.btc{background:var(--panel);border:1px solid var(--rule);border-radius:4px;padding:12px 14px;
  display:flex;flex-direction:column;gap:1px}
.btc span{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--faint)}
.btc b{font-family:var(--mono);font-size:23px;font-weight:500}
.btc i{font-style:normal;font-family:var(--mono);font-size:13px;color:var(--jade)}
.btc.bad i{color:var(--rust)}
.parlay{margin-top:14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.parlay>span{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.07em}
.pgrid{display:flex;gap:7px;flex-wrap:wrap}
.pl{background:var(--panel2);border:1px solid var(--rule);border-radius:3px;
  padding:5px 10px;text-align:center;min-width:56px}
.pl b{display:block;font-family:var(--display);font-size:15px;color:var(--dim);font-weight:600}
.pl span{font-family:var(--mono);font-size:13px}
.pl.danger span{color:var(--rust)}
.fine{font-size:12px;color:var(--faint);margin:13px 0 0;line-height:1.5}
.g{background:var(--panel);border:1px solid var(--rule);border-left:2px solid var(--faint);
  border-radius:3px;padding:14px 16px;margin-bottom:10px}
.g.a{border-left-color:var(--jade)} .g.b{border-left-color:var(--amber)}
.gh{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.teams{font-family:var(--display);font-size:23px;font-weight:600;letter-spacing:.01em}
.teams i{font-style:normal;font-family:var(--mono);font-size:11px;color:var(--faint);
  margin-left:4px;letter-spacing:0}
.teams em{font-style:normal;color:var(--faint);font-size:15px;margin:0 3px}
.when{font-family:var(--mono);font-size:12px;color:var(--dim);white-space:nowrap}
.arms{color:var(--dim);font-size:13px;margin:3px 0 11px}
.arms .v{color:var(--faint);margin:0 5px}
.line{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:9px}
.tag{font-family:var(--display);font-size:12px;font-weight:700;letter-spacing:.11em;
  padding:2px 7px;border-radius:2px;background:var(--faint);color:var(--ground)}
.tag.a{background:var(--jade)} .tag.b{background:var(--amber)}
.sel{font-weight:600}
.num{font-family:var(--mono);font-size:17px;font-variant-numeric:tabular-nums}
.fair,.edge{font-family:var(--mono);font-size:12px;color:var(--dim)}
.edge{margin-left:auto}
.edge.pos{color:var(--jade)} .edge.neg{color:var(--amber)}
.scale{position:relative;height:5px;background:var(--panel2);border:1px solid var(--rule);
  border-radius:2px;overflow:hidden;margin-bottom:9px}
.fill{position:absolute;inset:0 auto 0 0;background:var(--jade);opacity:.5}
.mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--faint)}
.tick{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink);border-radius:1px}
.book{display:flex;gap:14px;font-family:var(--mono);font-size:12px;color:var(--dim);
  padding-bottom:9px;flex-wrap:wrap}
.proj{font-family:var(--mono);font-size:12px;color:var(--faint);
  border-top:1px solid var(--rule);padding-top:8px}
.why{margin:8px 0 0;padding-left:16px;font-size:13.5px;color:#bccbd4}
.why li{margin-bottom:3px}
.ctx{margin-top:10px;border-top:1px solid var(--rule);padding-top:9px}
.ctxh{font-family:var(--display);font-size:11px;text-transform:uppercase;letter-spacing:.1em;
  color:var(--faint)}
.ctx ul{margin:4px 0 0;padding-left:16px;font-size:13px;color:#a9bcc7}
.ctx li{margin-bottom:3px}
.ctx.warn .ctxh{color:var(--amber)} .ctx.warn ul{color:#d3b177}
footer{margin-top:34px;border-top:1px solid var(--rule);padding-top:14px;
  color:var(--faint);font-size:12.5px;line-height:1.6}
footer b{color:var(--dim);font-weight:600}
@media(max-width:520px){h1{font-size:31px}.btg{grid-template-columns:1fr}.edge{margin-left:0}}
</style>

<div class="wrap">
<h1>MLB Model <span>Board</span></h1>
<div class="meta"><span>@DATE@</span><span>@N@ games</span><span>built @GEN@</span></div>

<section class="read">
  <h2>Today's read</h2>
  @PLAYS@
  <p class="readnote">@READNOTE@</p>
</section>

@BT@

<section>
  <h2>Every game, ranked by model confidence</h2>
  @CARDS@
</section>

<footer>
<p><b>How it works.</b> Each starter becomes a regressed RA/9 from FIP components blended with two
prior seasons, then combined with that team's bullpen over the innings the starter is not projected
to reach. Offenses are park-neutralised season run rates nudged by three-week form. Both run
expectations go through 30,000 negative-binomial simulations.</p>
<p><b>The bar</b> under each pick is the model's win probability; the white tick is the de-vigged
market price. Tick to the right of the fill means the market likes it more than the model does.</p>
<p><b>Fair odds are model prices, never a book's.</b> Where a real line appears it was read from a
named source. Lines are never estimated &mdash; a blank means none was found.</p>
</footer>
</div>"""

if __name__ == "__main__":
    main()
