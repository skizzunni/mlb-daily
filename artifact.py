#!/usr/bin/env python3
"""Render the shareable board from the data build.py produces.

  python3 artifact.py              -> Artifact fragment (no doctype/html/head/body)
  python3 artifact.py --standalone -> full document for GitHub Pages / any web host
"""
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
    hrb = data.get("hr_board", [])
    lghr = data.get("league_hr_rate", 0.0302)
    games = data["games"]

    rows = []
    for g in games:
        p = g["pick"]
        ln = lines.get(str(g["pk"]), {})
        pa, ph = B.novig(ln.get("ml_away"), ln.get("ml_home"))
        mkt = (ph if p["side"] == "home" else pa) if pa is not None else None
        edge = (p["p"] - mkt) if mkt is not None else None
        rows.append((g, p, ln, mkt, edge))

    # Ranked by edge on the book, because model confidence has a -0.101
    # correlation with winning across the graded picks and cannot rank them.
    rows.sort(key=lambda r: -(r[4] if r[4] is not None else -99))
    plays = [r for r in rows if r[4] is not None and r[4] >= 0.02]

    live_now = [g for g in games if g.get("state") == "Live"]
    done = [g for g in games if g.get("state") == "Final"]
    strip = ('<section class="strip" id="strip" hidden><h2 id="striph">Scoreboard</h2>'
             '<div class="scg" id="scg"></div></section>')
    if live_now or done:
        cells = ""
        for g in live_now + done:
            lv = g.get("live") or {}
            if g.get("state") == "Live":
                sub = ("%s %s" % (lv.get("half", ""), lv.get("ord", ""))).strip() or "live"
                cls = "sc islive"
            else:
                sub, cls = "final", "sc"
                if g.get("result"):
                    cls += " won" if g["result"] == "W" else " lost"
            cells += ('<div class="%s"><b>%s %s</b><b>%s %s</b><span>%s</span></div>'
                      % (cls, g["away"]["abbr"], g["away"]["score"],
                         g["home"]["abbr"], g["home"]["score"], sub))
        strip = ('<section class="strip" id="strip"><h2 id="striph">Scoreboard &middot; %d live '
                 '&middot; %d final</h2><div class="scg" id="scg">%s</div></section>'
                 % (len(live_now), len(done), cells))

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
        if g.get("state") == "Live":
            lv = g.get("live") or {}
            half = lv.get("half") or ""
            when = '<span class="lv">&#9679; %s %s</span> %s %s&ndash;%s %s' % (
                half, lv.get("ord", ""), g["away"]["abbr"], g["away"]["score"],
                g["home"]["abbr"], g["home"]["score"])
        elif g.get("state") == "Final":
            res = g.get("result")
            mark = ('<span class="won">PICK WON</span>' if res == "W" else
                    '<span class="lost">PICK LOST</span>' if res == "L" else "")
            when = 'FINAL %s %s&ndash;%s %s %s' % (
                g["away"]["abbr"], g["away"]["score"],
                g["home"]["abbr"], g["home"]["score"], mark)
        if edge is None:
            ecls, etxt = "flat", "no line"
        elif edge >= 0.03:
            ecls, etxt = "pos", "+%.1f vs market" % (100 * edge)
        elif edge <= -0.03:
            ecls, etxt = "neg", "%.1f vs market" % (100 * edge)
        else:
            ecls, etxt = "flat", "level with market"
        # "A"/"B" are legacy values still carried by picks that locked before the
        # tiers were collapsed; they are not written any more.
        tier = {"A": "PLAY", "B": "LEAN", "PLAY": "PLAY", "PASS": "PASS"}.get(
            p["tier"], p["tier"])

        why = "".join("<li>%s</li>" % esc(r) for r in p["reasons"][:3])
        nt = notes.get(str(g["pk"]), {})
        ctx = ""
        if nt.get("verified"):
            ctx += ('<div class="ctx"><span class="ctxh">Sourced</span><ul>%s</ul></div>'
                    % "".join("<li>%s</li>" % esc(x) for x in nt["verified"]))
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
<article class="g %s" data-pk="%s" data-side="%s" data-away="%s" data-home="%s">
  <header class="gh">
    <div class="teams"><b>%s</b><i>%s</i> <em>at</em> <b>%s</b><i>%s</i></div>
    <div class="when" data-when>%s</div>
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
            {"A": "a", "B": "b", "PLAY": "a", "PASS": "pass"}.get(p["tier"], "pass"),
            g["pk"], p["side"],
            esc(g["away"]["abbr"]), esc(g["home"]["abbr"]),
            esc(g["away"]["abbr"]), esc(g["away"]["rec"]),
            esc(g["home"]["abbr"]), esc(g["home"]["rec"]), when,
            esc(g["away"]["sp"]), esc(g["home"]["sp"]),
            p["tier"].lower(), tier, esc(p["team_name"]),
            "%.1f%%" % (100 * p["p"]), p["fair"], ecls, etxt,
            bar(p["p"], mkt), book,
            g["away"]["abbr"], g["away"]["exp_runs"],
            g["home"]["abbr"], g["home"]["exp_runs"], p["mean_total"], why, ctx))

    hr_html = ""
    if hrb:
        rows_hr = ""
        for i, h in enumerate(hrb, 1):
            why = []
            if h.get("hr_per"):
                why.append("%d HR in %d PA (one per %.1f)" % (h["hr"], h["pa"], h["hr_per"]))
            if h.get("sp_hr9"):
                why.append("%s allows %s HR/9" % (h["opp_sp"], h["sp_hr9"]))
            if h["hr_park"] >= 1.05:
                why.append("%s inflates homers (%d HR factor)"
                           % (h["venue"], round(h["hr_park"] * 100)))
            elif h["hr_park"] <= 0.95:
                why.append("%s suppresses homers (%d) &mdash; counted against him"
                           % (h["venue"], round(h["hr_park"] * 100)))
            why.append("%s-handed bat, %s" % (h["bats"], h["platoon"]))
            rows_hr += (
                '<div class="hrow"><div class="hrk">%d</div>'
                '<div class="hmain"><div class="hname">%s <span class="htm">%s %s</span></div>'
                '<div class="hwhy">%s</div></div>'
                '<div class="hnum"><b>%.1f%%</b><span>fair %s</span></div></div>'
                % (i, esc(h["name"]), esc(h["team"]), esc(h["pos"]),
                   " &middot; ".join(why), 100 * h["p"], h["fair"]))
        hr_html = (
            '<section class="hr"><h2>Most likely to homer today</h2>%s'
            '<p class="fine">Not an HR leaderboard. Each hitter\'s regressed HR-per-plate-appearance '
            'is scaled by the opposing staff\'s HR-allowed rate, the park\'s <i>home run</i> factor '
            '(a different number from its run factor &mdash; Kauffman is neutral for runs but 0.88 '
            'for homers) and the platoon matchup, then converted to P(at least one) over his '
            'expected plate appearances. League average is one homer per %.1f PA. Bench bats who '
            'start under 55%% of games are excluded. Prices are model fair odds, not a book\'s.</p>'
            '</section>' % (rows_hr, 1 / lghr if lghr else 33.1))

    if plays:
        pl = ""
        anyfav = False
        for g, p, ln, mkt, ed in plays:
            price = ln.get("ml_home" if p["side"] == "home" else "ml_away", "")
            dog = str(price).startswith("+")
            anyfav = anyfav or not dog
            pl += ('<div class="prow"><b>%s %s</b><span>model %.1f%% &middot; market %.1f%%%s'
                   '</span><b class="pos">+%.1f</b></div>'
                   % (esc(p["team_name"]), price, 100 * p["p"], 100 * mkt,
                      ' &middot; <i class="dog">plus-money dog</i>' if dog else "", 100 * ed))
        readnote = ("The only spots where the model prices above the de-vigged market."
                    + ("" if anyfav else " All of them are plus-money underdogs, which the "
                       "no-plus-money rule excludes &mdash; by that rule today is a pass."))
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

    standalone = "--standalone" in sys.argv
    out = PAGE
    for k, v in (("@DATE@", data["date"]), ("@GEN@", data["generated"]),
                 ("@N@", str(len(games))), ("@PLAYS@", pl), ("@READNOTE@", readnote),
                 ("@BT@", btb), ("@HR@", hr_html), ("@STRIP@", strip), ("@CARDS@", "".join(cards))):
        out = out.replace(k, v)
    if standalone:
        marker = '\n<div class="wrap">'
        head, body = out.split(marker, 1)
        out = (
            '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<meta http-equiv="refresh" content="900">\n'
            '<meta name="theme-color" content="#0c1216">\n'
            '<meta name="color-scheme" content="dark">\n'
            '<meta name="apple-mobile-web-app-capable" content="yes">\n'
            '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">\n'
            '<meta name="description" content="MLB model win probabilities against real '
            'de-vigged market prices, with live scores.">\n'
            + head +
            '\n</head>\n<body>\n<div class="wrap">' + body
            + LIVE_JS.replace("__BUILT__", data["generated"].replace("'", ""))
            + '\n</body>\n</html>')
    print(out)


LIVE_JS = r"""
<script>
/* Live scores, fetched straight from MLB's public API by the browser.
   GitHub throttles scheduled Actions hard, so the static rebuild cannot be
   relied on for score freshness -- this keeps scores current regardless. */
(function () {
  var API = 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType=R' +
            '&hydrate=linescore,team&date=';
  var BUILT = '__BUILT__';
  function etDate() {
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York',
      year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
  }
  function esc(s) {
    return String(s).replace(/[&<>]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; });
  }
  function stamp(t) {
    var el = document.getElementById('livestamp'); if (el) el.textContent = t;
  }
  function cell(cls, a, b, sub) {
    return '<div class="' + cls + '"><b>' + esc(a) + '</b><b>' + esc(b) +
           '</b><span>' + esc(sub) + '</span></div>';
  }
  async function tick() {
    var data;
    try {
      var r = await fetch(API + etDate(), { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      data = await r.json();
    } catch (e) { stamp('scores unavailable'); return; }

    var games = [];
    (data.dates || []).forEach(function (d) {
      (d.games || []).forEach(function (g) { games.push(g); }); });

    var live = 0, final = 0, cells = [];
    games.forEach(function (g) {
      var art = document.querySelector('article[data-pk="' + g.gamePk + '"]');
      var st = (g.status || {}).abstractGameState;
      var a = g.teams.away.score, h = g.teams.home.score;
      if (a == null || h == null) return;
      var aw = art ? art.dataset.away : (g.teams.away.team.abbreviation || 'AWY');
      var hm = art ? art.dataset.home : (g.teams.home.team.abbreviation || 'HOM');
      var ls = g.linescore || {};
      var w = art ? art.querySelector('[data-when]') : null;
      if (st === 'Live') {
        live++;
        var half = (ls.inningState || '').trim(), ord = ls.currentInningOrdinal || '';
        var sub = (half + ' ' + ord).trim() || 'live';
        if (ls.outs != null && (half === 'Top' || half === 'Bottom')) sub += ', ' + ls.outs + ' out';
        if (w) w.innerHTML = '<span class="lv">&#9679; ' + esc(sub) + '</span> ' +
          esc(aw) + ' ' + a + '&ndash;' + esc(hm) + ' ' + h;
        cells.push(cell('sc islive', aw + ' ' + a, hm + ' ' + h, sub));
      } else if (st === 'Final') {
        final++;
        var cls = 'sc', tail = '';
        if (art) {
          var won = art.dataset.side === 'home' ? h > a : a > h;
          cls += won ? ' won' : ' lost';
          tail = ' <span class="' + (won ? 'won' : 'lost') + '">PICK ' +
                 (won ? 'WON' : 'LOST') + '</span>';
        }
        if (w) w.innerHTML = 'FINAL ' + esc(aw) + ' ' + a + '&ndash;' + esc(hm) + ' ' + h + tail;
        cells.push(cell(cls, aw + ' ' + a, hm + ' ' + h, 'final'));
      }
    });

    var strip = document.getElementById('strip');
    var scg = document.getElementById('scg');
    if (cells.length && strip && scg) {
      scg.innerHTML = cells.join('');
      document.getElementById('striph').innerHTML =
        'Scoreboard &middot; ' + live + ' live &middot; ' + final + ' final';
      strip.hidden = false;
    } else if (strip) { strip.hidden = true; }

    stamp('scores ' + new Date().toLocaleTimeString('en-US',
      { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }));

    /* reload only when a genuinely newer build exists */
    try {
      var j = await (await fetch('data.json', { cache: 'no-store' })).json();
      if (BUILT && j.generated && j.generated !== BUILT) location.reload();
    } catch (e) {}
  }
  tick();
  setInterval(tick, 45000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) tick(); });
})();
</script>
"""

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
html{background:var(--ground)}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--body);
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:26px 18px 60px}
h1{font-family:var(--display);font-size:38px;font-weight:700;letter-spacing:.01em;
  margin:0;line-height:1;text-transform:uppercase}
h1 span{color:var(--jade)}
.meta{color:var(--dim);font-size:13px;margin-top:7px;font-family:var(--mono);
  display:flex;gap:14px;flex-wrap:wrap}
.lstamp{color:var(--jade)}
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
.strip .scg{display:flex;gap:7px;flex-wrap:wrap}
.sc{background:var(--panel);border:1px solid var(--rule);border-radius:3px;padding:7px 10px;
  min-width:82px;display:flex;flex-direction:column}
.sc b{font-family:var(--mono);font-size:13px;font-weight:500;font-variant-numeric:tabular-nums}
.sc span{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--faint);
  margin-top:3px}
.sc.islive{border-color:var(--jade)}
.sc.won{border-left:2px solid var(--jade)} .sc.lost{border-left:2px solid var(--rust)}
.lv{color:var(--jade);font-weight:600}
.won{color:var(--jade);font-weight:600} .lost{color:var(--rust);font-weight:600}
.dog{color:var(--amber);font-style:normal}
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
.hrow{display:flex;align-items:flex-start;gap:12px;padding:11px 0;
  border-bottom:1px solid var(--rule)}
.hrow:last-of-type{border-bottom:0}
.hrk{font-family:var(--display);font-size:20px;font-weight:700;color:var(--faint);
  min-width:20px;line-height:1.2}
.hmain{flex:1;min-width:0}
.hname{font-family:var(--display);font-size:20px;font-weight:600;letter-spacing:.01em}
.htm{font-family:var(--mono);font-size:11px;color:var(--faint);letter-spacing:0;
  margin-left:5px}
.hwhy{font-size:12.5px;color:#a9bcc7;margin-top:2px;line-height:1.45}
.hnum{text-align:right;white-space:nowrap}
.hnum b{display:block;font-family:var(--mono);font-size:18px;font-weight:500;
  font-variant-numeric:tabular-nums;color:var(--jade)}
.hnum span{font-family:var(--mono);font-size:11px;color:var(--faint)}
@media(max-width:430px){.hname{font-size:18px}.hwhy{font-size:12px}}
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
<div class="meta"><span>@DATE@</span><span>@N@ games</span><span>built @GEN@</span>
<span id="livestamp" class="lstamp"></span></div>

@STRIP@
<section class="read">
  <h2>Today's read</h2>
  @PLAYS@
  <p class="readnote">@READNOTE@</p>
</section>

@HR@
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
