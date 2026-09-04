#!/usr/bin/env python3
"""
Builds the MLB picks site. Run hourly by launchd/cron.
Zero API keys, zero LLM calls -- pure data + math.

  python3 build.py [YYYY-MM-DD]
"""
import json
import os
import sys
import datetime as dt

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/New_York")
except Exception:
    TZ = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
DATA = os.path.join(HERE, "data")
HISTORY = os.path.join(DATA, "history.json")
LINES = os.path.join(DATA, "lines.json")   # optional real book lines, keyed by gamePk
BACKTEST = os.path.join(DATA, "backtest.json")
NOTES = os.path.join(DATA, "notes.json")
SEASON = 2026

# thresholds
TIER_A = 0.600
TIER_B = 0.555

TEAM_ABBR = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS", "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE", "Colorado Rockies": "COL",
    "Detroit Tigers": "DET", "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Athletics": "ATH", "Oakland Athletics": "ATH",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}


def abbr(name):
    return TEAM_ABBR.get(name, name[:3].upper())


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)

# ---------------------------------------------------------------- analysis

def live_state(g):
    """Inning / half / outs for an in-progress game."""
    ls = g.get("linescore") or {}
    if not ls.get("currentInning"):
        return None
    half = (ls.get("inningState") or "").strip()
    return {
        "inning": ls.get("currentInning"),
        "ord": ls.get("currentInningOrdinal", ""),
        "half": half,
        "outs": ls.get("outs"),
        "away_h": (ls.get("teams", {}).get("away") or {}).get("hits"),
        "home_h": (ls.get("teams", {}).get("home") or {}).get("hits"),
    }


def analyze_game(g, team_stats, form, lg, pen_cache, parks, lgby):
    away_t = g["teams"]["away"]["team"]
    home_t = g["teams"]["home"]["team"]
    venue = g.get("venue", {}).get("name", "")
    pf = M.park_factor(venue)

    ap = g["teams"]["away"].get("probablePitcher") or {}
    hp = g["teams"]["home"].get("probablePitcher") or {}
    a_stat = M.fetch_pitcher(ap["id"], SEASON) if ap.get("id") else None
    h_stat = M.fetch_pitcher(hp["id"], SEASON) if hp.get("id") else None
    a_hist = M.fetch_pitcher_history(ap["id"]) if ap.get("id") else {}
    h_hist = M.fetch_pitcher_history(hp["id"]) if hp.get("id") else {}
    a_ra9, a_det = M.pitcher_true_talent(a_stat, lg, a_hist, lgby, SEASON)
    h_ra9, h_det = M.pitcher_true_talent(h_stat, lg, h_hist, lgby, SEASON)
    # correct expected innings using games actually started (catches openers)
    for pp, det in ((ap, a_det), (hp, h_det)):
        if not pp.get("id"):
            continue
        wl = M.fetch_start_workload(pp["id"], SEASON)
        if wl:
            det["exp_ip"] = round(wl["exp_ip"], 1)
            det["opener"] = wl["opener"]
            det["start_ip_recent"] = wl["avg_recent"]
            det["n_starts"] = wl["n_starts"]

    for tid in (away_t["id"], home_t["id"]):
        if tid not in pen_cache:
            pen_cache[tid] = M.bullpen_ra9(M.fetch_bullpen(tid, SEASON), lg)
    a_pen, a_pen_det = pen_cache[away_t["id"]]
    h_pen, h_pen_det = pen_cache[home_t["id"]]

    # park-neutral offense (strip each team's OWN home park from its season run rate)
    a_off, a_off_det, a_form = M.team_offense(
        away_t["id"], team_stats, form, lg, parks.get(away_t["id"], 1.0))
    h_off, h_off_det, h_form = M.team_offense(
        home_t["id"], team_stats, form, lg, parks.get(home_t["id"], 1.0))

    # tonight's staff RA/9 for each side (SP for expected innings, pen for the rest)
    def staff(ra9, det, pen):
        eip = det.get("exp_ip", 5.0)
        return (eip / 9.0) * ra9 + ((9.0 - eip) / 9.0) * pen

    away_staff = staff(a_ra9, a_det, a_pen)   # what the AWAY team's pitching allows
    home_staff = staff(h_ra9, h_det, h_pen)

    # strip each staff's OWN park, mirroring what team_offense does to the hitters.
    # Without this the venue factor below is applied on top of a park-contaminated rate.
    away_staff /= (1.0 + parks.get(away_t["id"], 1.0)) / 2.0
    home_staff /= (1.0 + parks.get(home_t["id"], 1.0)) / 2.0

    # expected runs: team offense x opposing staff / league, then park, form, HFA.
    # Denominator is league RA/9 -- the same per-9-innings scale as the numerator.
    lg_ra9 = lg.get("ra9") or lg["rpg"]
    away_exp = a_off * (home_staff / lg_ra9) * pf * a_form / M.HFA ** 0.5
    home_exp = h_off * (away_staff / lg_ra9) * pf * h_form * M.HFA ** 0.5

    sim = M.simulate(home_exp, away_exp, n=30000, seed=g["gamePk"])

    return {
        "pk": g["gamePk"],
        "state": g["status"]["abstractGameState"],
        "detail": g["status"]["detailedState"],
        "live": live_state(g),
        "start_utc": g["gameDate"],
        "venue": venue,
        "park_factor": round(pf, 3),
        "hr_park": round(M.hr_park_factor(venue), 3),
        "away_id": away_t["id"], "home_id": home_t["id"],
        "away": {
            "name": away_t["name"], "abbr": abbr(away_t["name"]),
            "rec": "%s-%s" % (g["teams"]["away"].get("leagueRecord", {}).get("wins", 0),
                              g["teams"]["away"].get("leagueRecord", {}).get("losses", 0)),
            "sp": ap.get("fullName", "TBD"), "sp_id": ap.get("id"),
            "sp_det": a_det, "sp_ra9": round(a_ra9, 2),
            "pen_ra9": round(a_pen, 2), "pen": a_pen_det,
            "off": a_off_det, "exp_runs": round(away_exp, 2),
            "score": g["teams"]["away"].get("score"),
        },
        "home": {
            "name": home_t["name"], "abbr": abbr(home_t["name"]),
            "rec": "%s-%s" % (g["teams"]["home"].get("leagueRecord", {}).get("wins", 0),
                              g["teams"]["home"].get("leagueRecord", {}).get("losses", 0)),
            "sp": hp.get("fullName", "TBD"), "sp_id": hp.get("id"),
            "sp_det": h_det, "sp_ra9": round(h_ra9, 2),
            "pen_ra9": round(h_pen, 2), "pen": h_pen_det,
            "off": h_off_det, "exp_runs": round(home_exp, 2),
            "score": g["teams"]["home"].get("score"),
        },
        "sim": sim,
    }

# ---------------------------------------------------------------- picks

def build_hr_board(games, team_stats, lg, top_n=5):
    """Most likely home run hitters on the slate.

    Not an HR leaderboard: a hitter's regressed HR-per-PA is scaled by the
    opposing staff's HR-allowed rate, the park's HR factor (which is a different
    number from its run factor) and the platoon matchup, then converted to
    P(at least one) over his expected plate appearances.
    """
    lg_rate = M.league_hr_rate(team_stats)
    cands = []
    for a in games:
        if a["state"] != "Preview":
            continue                      # only publish before first pitch
        for side, opp in (("away", "home"), ("home", "away")):
            me, other = a[side], a[opp]
            roster = M.fetch_roster(a["%s_id" % side], SEASON)
            if not roster:
                continue
            sp_stat = M.fetch_pitcher(other["sp_id"], SEASON) if other.get("sp_id") else None
            pen_stat = M.fetch_bullpen(a["%s_id" % opp], SEASON)
            hand = M.fetch_pitch_hand(other["sp_id"]) if other.get("sp_id") else "R"
            staff_rate = M.staff_hr_per_bf(
                sp_stat, pen_stat, other["sp_det"].get("exp_ip", 5.0), lg_rate)
            tg = M.f(team_stats.get(a["%s_id" % side], {}).get("hit", {}).get("gamesPlayed"), 1)
            for bat in roster:
                r = M.batter_hr_prob(bat, staff_rate, lg_rate, a["hr_park"], hand, tg)
                if not r:
                    continue
                cands.append({
                    "name": bat["name"], "team": me["abbr"], "pos": bat["pos"],
                    "bats": bat["bats"], "opp": other["abbr"], "opp_sp": other["sp"],
                    "opp_hand": hand, "venue": a["venue"], "hr_park": a["hr_park"],
                    "hr": int(bat["hr"]), "pa": int(bat["pa"]),
                    "hr_per": round(bat["pa"] / bat["hr"], 1) if bat["hr"] else None,
                    "ops": bat["ops"], "platoon": r["platoon"], "pa_g": r["pa_g"],
                    "p": round(r["p"], 4),
                    "fair": M.fmt_odds(M.prob_to_american(r["p"])),
                    "sp_hr9": other["sp_det"].get("hr9"),
                    "start_utc": a["start_utc"],
                })
    cands.sort(key=lambda x: -x["p"])
    return cands[:top_n], round(lg_rate, 4)


def build_pick(a):
    p_home = a["sim"]["p_home"]
    p_away = a["sim"]["p_away"]
    side = "home" if p_home >= p_away else "away"
    other = "away" if side == "home" else "home"
    p = max(p_home, p_away)
    if p >= TIER_A:
        tier = "A"
    elif p >= TIER_B:
        tier = "B"
    else:
        tier = "PASS"

    me, opp = a[side], a[other]
    reasons = []

    sp_gap = opp["sp_ra9"] - me["sp_ra9"]
    if abs(sp_gap) >= 0.35 and not me["sp_det"].get("unknown") and not opp["sp_det"].get("unknown"):
        reasons.append(
            "Starter edge %+.2f RA/9: %s (%s ERA, %s K/9, %s BB/9 over %s IP) vs %s (%s ERA, %s K/9, %s BB/9)"
            % (sp_gap, me["sp"], me["sp_det"].get("era"), me["sp_det"].get("k9"),
               me["sp_det"].get("bb9"), me["sp_det"].get("ip"), opp["sp"],
               opp["sp_det"].get("era"), opp["sp_det"].get("k9"), opp["sp_det"].get("bb9")))
    if opp["sp_det"].get("unknown"):
        reasons.append("%s has no meaningful 2026 track record — modelled at league average, "
                       "which is itself a risk flag" % opp["sp"])

    pen_gap = opp["pen_ra9"] - me["pen_ra9"]
    if abs(pen_gap) >= 0.30 and not me["pen"].get("fallback") and not opp["pen"].get("fallback"):
        reasons.append("Bullpen edge %+.2f RA/9 (%s pen %s ERA vs %s pen %s ERA) — matters because "
                       "the starters project to hand off around the 6th"
                       % (pen_gap, me["abbr"], me["pen"].get("era"), opp["abbr"], opp["pen"].get("era")))

    off_gap = M.f(me["off"]["rs_pg"]) - M.f(opp["off"]["rs_pg"])
    if abs(off_gap) >= 0.4:
        reasons.append("Offense gap %+.2f R/G (%s %s R/G, %s OPS vs %s %s R/G, %s OPS)"
                       % (off_gap, me["abbr"], me["off"]["rs_pg"], me["off"]["ops"],
                          opp["abbr"], opp["off"]["rs_pg"], opp["off"]["ops"]))

    if me["off"].get("recent_rec") and opp["off"].get("recent_rec"):
        reasons.append("Last 3 weeks: %s %s (%s R/G scored, %s allowed) vs %s %s (%s / %s)"
                       % (me["abbr"], me["off"]["recent_rec"], me["off"]["recent_rs"],
                          me["off"]["recent_ra"], opp["abbr"], opp["off"]["recent_rec"],
                          opp["off"]["recent_rs"], opp["off"]["recent_ra"]))

    if a["park_factor"] >= 1.03:
        reasons.append("%s plays as a hitters' park (%d run factor) — raises variance, "
                       "which cuts against the favorite" % (a["venue"], round(a["park_factor"] * 100)))
    elif a["park_factor"] <= 0.97:
        reasons.append("%s suppresses runs (%d factor), which protects a lead and helps the "
                       "better pitching staff" % (a["venue"], round(a["park_factor"] * 100)))

    # totals lean
    mt = a["sim"]["mean_total"]
    total_lean = None
    for line in (7.5, 8.5, 9.5):
        po = a["sim"]["p_over"][str(line)]
        if po >= 0.60:
            total_lean = ("OVER %.1f" % line, po)
            break
        if po <= 0.40:
            total_lean = ("UNDER %.1f" % line, 1 - po)
            break

    rl = a["sim"]["p_home_rl"] if side == "home" else a["sim"]["p_away_rl"]

    return {
        "side": side, "team": me["abbr"], "team_name": me["name"],
        "p": round(p, 4), "tier": tier,
        "fair": M.fmt_odds(M.prob_to_american(p)),
        "reasons": reasons,
        "total_lean": total_lean, "mean_total": round(mt, 2),
        "rl_p": round(rl, 4),
        "rl_fair": M.fmt_odds(M.prob_to_american(rl)),
    }

# ---------------------------------------------------------------- history / grading

def update_history(date_str, games, lines=None):
    """Freeze each pick at first pitch, and freeze the PRICE with it.

    Hit rate and profit are different things. On the other board's ledger, the
    picks whose model probability beat the de-vigged price went 41-45 (47.7%)
    for +10.4% ROI, while the picks that lost to the price went 35-19 (64.8%)
    for +3.9%. The worse record made more money because it was paid better.
    Recording only W-L cannot see that, so the price is stored at lock time and
    the record carries ROI beside the hit rate.
    """
    lines = lines or {}
    hist = load_json(HISTORY, {})
    day = hist.setdefault(date_str, {})
    for a in games:
        pk = str(a["pk"])
        rec = day.get(pk)
        started = a["state"] != "Preview"
        final = a["state"] == "Final"
        if rec is None:
            ln = lines.get(pk, {})
            price = ln.get("ml_home" if a["pick"]["side"] == "home" else "ml_away")
            pa, ph = novig(ln.get("ml_away"), ln.get("ml_home"))
            mkt = (ph if a["pick"]["side"] == "home" else pa)
            day[pk] = rec = {
                "matchup": "%s @ %s" % (a["away"]["abbr"], a["home"]["abbr"]),
                "pick": a["pick"]["team"], "side": a["pick"]["side"],
                "p": a["pick"]["p"], "tier": a["pick"]["tier"],
                "fair": a["pick"]["fair"], "locked": started, "result": None,
                "price": price, "mkt": round(mkt, 4) if mkt else None,
            }
        elif rec.get("price") is None and lines.get(pk):
            # written before the price was being frozen; fill it in once
            ln = lines[pk]
            pa, ph = novig(ln.get("ml_away"), ln.get("ml_home"))
            mkt = ph if rec.get("side") == "home" else pa
            rec["price"] = ln.get("ml_home" if rec.get("side") == "home" else "ml_away")
            rec["mkt"] = round(mkt, 4) if mkt else None
        if not rec.get("locked"):
            # refresh the pick until first pitch, then freeze it
            rec.update({"pick": a["pick"]["team"], "side": a["pick"]["side"],
                        "p": a["pick"]["p"], "tier": a["pick"]["tier"],
                        "fair": a["pick"]["fair"], "locked": started})
        if final and rec.get("result") is None:
            hs, as_ = a["home"]["score"], a["away"]["score"]
            if hs is not None and as_ is not None:
                winner = "home" if hs > as_ else "away"
                rec["result"] = "W" if winner == rec["side"] else "L"
                rec["final"] = "%s %s - %s %s" % (a["away"]["abbr"], as_, a["home"]["abbr"], hs)
        a["locked"] = rec.get("locked", False)
        a["result"] = rec.get("result")
        # The graded record is the locked one; render that, not whatever the model
        # says now, or the page would show a pick the history never took.
        if rec.get("locked") and rec.get("side") != a["pick"]["side"]:
            a["pick"] = dict(a["pick"], team=rec["pick"], side=rec["side"], p=rec["p"],
                             tier=rec["tier"], fair=rec["fair"],
                             team_name=a[rec["side"]]["name"],
                             reasons=["Locked at first pitch. The model has since moved to "
                                      "the other side; the locked pick is what gets graded."])
        elif rec.get("locked"):
            a["pick"] = dict(a["pick"], p=rec["p"], tier=rec["tier"], fair=rec["fair"])
    save_json(HISTORY, hist)
    return hist


def grade_open_days(hist, today_str):
    """A 10pm ET game ends after midnight, by which point the build has moved to the
    next slate -- without this sweep those picks would never be graded."""
    graded = 0
    for date_str in sorted(hist.keys()):
        if date_str >= today_str:
            continue
        day = hist[date_str]
        if all(r.get("result") for r in day.values()):
            continue
        try:
            sl = M.fetch_slate(date_str)
        except Exception as e:
            sys.stderr.write("grade %s: %s\n" % (date_str, e))
            continue
        finals = {}
        for d in sl.get("dates", []):
            for g in d.get("games", []):
                if g.get("status", {}).get("abstractGameState") != "Final":
                    continue
                hs = g["teams"]["home"].get("score")
                as_ = g["teams"]["away"].get("score")
                if hs is None or as_ is None:
                    continue
                finals[str(g["gamePk"])] = (as_, hs)
        for pk, r in day.items():
            if r.get("result") or pk not in finals:
                continue
            a_, h_ = finals[pk]
            if a_ == h_:
                continue
            winner = "home" if h_ > a_ else "away"
            r["result"] = "W" if winner == r.get("side") else "L"
            r["final"] = "%d-%d" % (a_, h_)
            r["locked"] = True
            graded += 1
    if graded:
        sys.stderr.write("graded %d carry-over pick(s) from previous days\n" % graded)
    return graded


def payout(american):
    """Units returned per unit staked on a winner."""
    try:
        v = int(str(american).replace("+", ""))
    except (TypeError, ValueError):
        return None
    return v / 100.0 if v > 0 else 100.0 / (-v)


def tally(hist):
    out = {"A": [0, 0], "B": [0, 0], "PASS": [0, 0], "ALL": [0, 0], "days": 0,
           "pl": 0.0, "staked": 0, "ev_pl": 0.0, "ev_staked": 0}
    for day, games in hist.items():
        graded = False
        for pk, r in games.items():
            if r.get("result") in ("W", "L"):
                graded = True
                t = r.get("tier", "PASS")
                i = 0 if r["result"] == "W" else 1
                out.setdefault(t, [0, 0])[i] += 1
                out["ALL"][i] += 1
                pay = payout(r.get("price"))
                if pay is not None:
                    unit = pay if r["result"] == "W" else -1.0
                    out["pl"] += unit
                    out["staked"] += 1
                    # picks the model priced ABOVE the market -- the ones that
                    # are supposed to carry the edge
                    if r.get("mkt") and r.get("p", 0) > r["mkt"]:
                        out["ev_pl"] += unit
                        out["ev_staked"] += 1
        if graded:
            out["days"] += 1
    return out

# ---------------------------------------------------------------- render

def pct(x):
    return "%.1f%%" % (100.0 * x)


def american_to_prob(o):
    try:
        o = int(str(o).replace("+", ""))
    except Exception:
        return None
    return 100.0 / (o + 100.0) if o > 0 else (-o) / ((-o) + 100.0)


def novig(ml_away, ml_home):
    """De-vig a two-way market -> (p_away, p_home)."""
    pa, ph = american_to_prob(ml_away), american_to_prob(ml_home)
    if pa is None or ph is None or (pa + ph) <= 0:
        return None, None
    return pa / (pa + ph), ph / (pa + ph)


def backtest_summary():
    bt = load_json(BACKTEST, [])
    if not bt:
        return None
    out = {"A": [0, 0], "B": [0, 0], "PASS": [0, 0]}
    lo = hi = None
    for r in bt:
        t = r.get("tier", "PASS")
        out.setdefault(t, [0, 0])[0 if r["win"] else 1] += 1
        d = r["date"]
        lo = d if lo is None or d < lo else lo
        hi = d if hi is None or d > hi else hi
    ab = [out["A"][0] + out["B"][0], out["A"][1] + out["B"][1]]
    out["AB"] = ab
    out["n"] = len(bt)
    out["from"], out["to"] = lo, hi
    return out


def render(date_str, games, hist, lines, notes, generated):
    rec = tally(hist)
    plays = [g for g in games if g["pick"]["tier"] == "A"]
    leans = [g for g in games if g["pick"]["tier"] == "B"]
    passes = [g for g in games if g["pick"]["tier"] == "PASS"]

    def game_card(a):
        p = a["pick"]
        tier_cls = {"A": "tier-a", "B": "tier-b", "PASS": "tier-pass"}[p["tier"]]
        tier_txt = {"A": "PLAY", "B": "LEAN", "PASS": "PASS"}[p["tier"]]
        ln = lines.get(str(a["pk"]), {})
        book = ""
        if ln.get("ml_home") or ln.get("total"):
            pa, ph = novig(ln.get("ml_away"), ln.get("ml_home"))
            edge_html = ""
            if pa is not None:
                mkt = ph if p["side"] == "home" else pa
                ed = p["p"] - mkt
                if ed >= 0.03:
                    cls, word = "edge-good", "model sees value"
                elif ed <= -0.03:
                    cls, word = "edge-bad", "market disagrees — caution"
                else:
                    cls, word = "edge-flat", "in line with market"
                edge_html = ('<div class="edge %s">Market (no-vig) %s on %s vs model %s '
                             '&nbsp;→&nbsp; <b>%+.1f pts, %s</b></div>'
                             % (cls, pct(mkt), p["team"], pct(p["p"]), 100 * ed, word))
            book = ('<div class="book">Book: %s %s / %s %s%s<div class="src">%s</div></div>%s'
                    % (a["away"]["abbr"], ln.get("ml_away", "-"), a["home"]["abbr"],
                       ln.get("ml_home", "-"),
                       (" &nbsp;·&nbsp; O/U " + str(ln["total"])) if ln.get("total") else "",
                       ln.get("source", ""), edge_html))
        status = ""
        if a["state"] == "Final":
            status = '<span class="final">FINAL %s %s - %s %s</span>' % (
                a["away"]["abbr"], a["away"]["score"], a["home"]["abbr"], a["home"]["score"])
            if a.get("result"):
                status += ' <span class="%s">%s</span>' % (
                    "win" if a["result"] == "W" else "loss",
                    "PICK WON" if a["result"] == "W" else "PICK LOST")
        elif a["state"] == "Live":
            lv = a.get("live") or {}
            half = lv.get("half") or ""
            outs = lv.get("outs")
            when_ = "%s %s" % (half, lv.get("ord", "")) if half else "In progress"
            if outs is not None and half in ("Top", "Bottom"):
                when_ += ", %d out" % outs
            status = ('<span class="live">&#9679; LIVE</span> '
                      '<span class="livescore">%s %s &ndash; %s %s</span> '
                      '<span class="inn">%s</span>'
                      % (a["away"]["abbr"], a["away"]["score"],
                         a["home"]["abbr"], a["home"]["score"], when_))
        else:
            t = dt.datetime.strptime(a["start_utc"], "%Y-%m-%dT%H:%M:%SZ")
            t = t.replace(tzinfo=dt.timezone.utc)
            t = t.astimezone(TZ) if TZ else t.astimezone()
            status = '<span class="time">%s</span>' % t.strftime("%-I:%M %p %Z")

        reasons = "".join("<li>%s</li>" % r for r in p["reasons"])
        nt = notes.get(str(a["pk"]), {})
        note_html = ""
        if nt.get("verified"):
            note_html += ('<div class="notes"><div class="nh">Sourced context '
                          '(not in the model)</div><ul>%s</ul></div>'
                          % "".join("<li>%s</li>" % x for x in nt["verified"]))
        if nt.get("caution"):
            note_html += ('<div class="notes warn"><div class="nh">Flagged by the fact-check</div>'
                          '<ul>%s</ul></div>'
                          % "".join("<li>%s</li>" % x for x in nt["caution"]))
        tot = ""
        if p["total_lean"]:
            tot = '<span class="chip">%s (%s)</span>' % (p["total_lean"][0], pct(p["total_lean"][1]))
        lock = '<span class="lock">locked</span>' if a.get("locked") else ""
        return """
<div class="card %s">
  <div class="chead">
    <div class="match">%s <span class="rec">%s</span> @ %s <span class="rec">%s</span></div>
    <div class="status">%s</div>
  </div>
  <div class="pitchers">%s (%s ERA) vs %s (%s ERA) &nbsp;·&nbsp; %s</div>
  <div class="pickrow">
    <span class="tier %s">%s</span>
    <span class="team">%s</span>
    <span class="prob">%s</span>
    <span class="fair">fair %s</span>
    %s %s
  </div>
  %s
  <div class="proj">Projected score: %s %s &ndash; %s %s &nbsp;·&nbsp; model total %s
     &nbsp;·&nbsp; %s -1.5 %s (%s)</div>
  <ul class="why">%s</ul>
  %s
</div>""" % (
            tier_cls,
            a["away"]["abbr"], a["away"]["rec"], a["home"]["abbr"], a["home"]["rec"], status,
            a["away"]["sp"], a["away"]["sp_det"].get("era", "-"),
            a["home"]["sp"], a["home"]["sp_det"].get("era", "-"), a["venue"],
            tier_cls, tier_txt, p["team_name"], pct(p["p"]), p["fair"], tot, lock,
            book,
            a["away"]["abbr"], a["away"]["exp_runs"], a["home"]["abbr"], a["home"]["exp_runs"],
            p["mean_total"], p["team"], p["rl_fair"], pct(p["rl_p"]),
            reasons, note_html)

    body_plays = "".join(game_card(a) for a in plays) or '<p class="none">No game on this slate clears the 60% threshold. That is the honest read — not every day has a play.</p>'
    body_leans = "".join(game_card(a) for a in leans)
    body_pass = "".join(game_card(a) for a in passes)

    live_now = [a for a in games if a["state"] == "Live"]
    done = [a for a in games if a["state"] == "Final"]
    strip = ""
    if live_now or done:
        cells = ""
        for a in live_now + done:
            lv = a.get("live") or {}
            if a["state"] == "Live":
                sub = "%s %s" % (lv.get("half", ""), lv.get("ord", "")) if lv.get("half") else "live"
                cls = "sc live"
            else:
                sub = "final"
                cls = "sc"
                if a.get("result"):
                    cls += " won" if a["result"] == "W" else " lost"
            cells += ('<div class="%s"><div class="scs">%s %s<br>%s %s</div>'
                      '<div class="scl">%s</div></div>'
                      % (cls, a["away"]["abbr"], a["away"]["score"],
                         a["home"]["abbr"], a["home"]["score"], sub))
        strip = ('<div class="strip"><div class="striph">Scoreboard &mdash; %d live, '
                 '%d final</div><div class="scg">%s</div></div>'
                 % (len(live_now), len(done), cells))

    bt = backtest_summary()
    a_rec = rec.get("A", [0, 0]); b_rec = rec.get("B", [0, 0]); all_rec = rec["ALL"]
    def wl(r):
        tot = r[0] + r[1]
        return "%d-%d%s" % (r[0], r[1], (" (%.0f%%)" % (100.0 * r[0] / tot)) if tot else "")

    # ---- today's read: where the model actually disagrees with the market ----
    edges = []
    for a in games:
        ln = lines.get(str(a["pk"]), {})
        pa, ph = novig(ln.get("ml_away"), ln.get("ml_home"))
        if pa is None:
            continue
        pk_ = a["pick"]
        mkt = ph if pk_["side"] == "home" else pa
        edges.append((pk_["p"] - mkt, a, mkt))
    edges.sort(key=lambda x: -x[0])
    # Edge vs the market is the criterion once real lines exist -- a 62% pick the market
    # prices at 72% is not a bet, and a 52% pick the market prices at 48% might be.
    val = [e for e in edges if e[0] >= 0.03]
    if val:
        rows = ""
        for ed, a, mkt in val:
            pk_ = a["pick"]
            price = lines.get(str(a["pk"]), {}).get(
                "ml_home" if pk_["side"] == "home" else "ml_away", "")
            dog = str(price).startswith("+")
            rows += ('<div class="vrow"><b>%s %s</b>'
                     '<span>model %s vs market %s%s</span>'
                     '<b class="pos">+%.1f pts</b></div>'
                     % (pk_["team_name"], price, pct(pk_["p"]), pct(mkt),
                        ' &middot; <i class="dogflag">plus-money dog</i>' if dog else "",
                        100 * ed))
        favs = [e for e in val if not str(lines.get(str(e[1]["pk"]), {}).get(
            "ml_home" if e[1]["pick"]["side"] == "home" else "ml_away", "")).startswith("+")]
        tail = ("" if favs else
                " Every one of them is a plus-money underdog, which your own rule excludes from "
                "a floor build &mdash; so by that rule today is a pass.")
        read = ('<div class="read"><div class="rh">Today\'s read</div>%s'
                '<div class="rnote">%d of %d games priced. These are the only spots where the '
                'model sits <i>above</i> the de-vigged market.%s</div></div>'
                % (rows, len(edges), len(games), tail))
    else:
        read = ('<div class="read"><div class="rh">Today\'s read</div>'
                '<div class="rnote">No game on this slate prices better than the market by 3+ '
                'points. The honest answer is no play &mdash; not a smaller play.</div></div>')

    bt_block = ""
    if bt:
        ab_n = bt["AB"][0] + bt["AB"][1]
        ab_pct = 100.0 * bt["AB"][0] / ab_n if ab_n else 0
        pass_n = bt["PASS"][0] + bt["PASS"][1]
        pass_pct = 100.0 * bt["PASS"][0] / pass_n if pass_n else 0
        r = ab_pct / 100.0
        # ---- today's read: where the model actually disagrees with the market ----
    edges = []
    for a in games:
        ln = lines.get(str(a["pk"]), {})
        pa, ph = novig(ln.get("ml_away"), ln.get("ml_home"))
        if pa is None:
            continue
        pk_ = a["pick"]
        mkt = ph if pk_["side"] == "home" else pa
        edges.append((pk_["p"] - mkt, a, mkt))
    edges.sort(key=lambda x: -x[0])
    # Edge vs the market is the criterion once real lines exist -- a 62% pick the market
    # prices at 72% is not a bet, and a 52% pick the market prices at 48% might be.
    val = [e for e in edges if e[0] >= 0.03]
    if val:
        rows = ""
        for ed, a, mkt in val:
            pk_ = a["pick"]
            price = lines.get(str(a["pk"]), {}).get(
                "ml_home" if pk_["side"] == "home" else "ml_away", "")
            dog = str(price).startswith("+")
            rows += ('<div class="vrow"><b>%s %s</b>'
                     '<span>model %s vs market %s%s</span>'
                     '<b class="pos">+%.1f pts</b></div>'
                     % (pk_["team_name"], price, pct(pk_["p"]), pct(mkt),
                        ' &middot; <i class="dogflag">plus-money dog</i>' if dog else "",
                        100 * ed))
        favs = [e for e in val if not str(lines.get(str(e[1]["pk"]), {}).get(
            "ml_home" if e[1]["pick"]["side"] == "home" else "ml_away", "")).startswith("+")]
        tail = ("" if favs else
                " Every one of them is a plus-money underdog, which your own rule excludes from "
                "a floor build &mdash; so by that rule today is a pass.")
        read = ('<div class="read"><div class="rh">Today\'s read</div>%s'
                '<div class="rnote">%d of %d games priced. These are the only spots where the '
                'model sits <i>above</i> the de-vigged market.%s</div></div>'
                % (rows, len(edges), len(games), tail))
    else:
        read = ('<div class="read"><div class="rh">Today\'s read</div>'
                '<div class="rnote">No game on this slate prices better than the market by 3+ '
                'points. The honest answer is no play &mdash; not a smaller play.</div></div>')

    bt_block = ""
    if bt:
        bt_block = """
<div class="bt">
  <div class="bth">Backtest &mdash; %d games, %s to %s</div>
  <div class="btrow"><span>PLAY + LEAN tiers</span><b>%d-%d &nbsp;(%.1f%%)</b></div>
  <div class="btrow"><span>PASS tier &mdash; why they are passed</span><b class="bad">%d-%d &nbsp;(%.1f%%)</b></div>
  <div class="btnote">Picks at that %.1f%% rate, parlayed:
    <b>2 legs %.0f%%</b> &middot; <b>3 legs %.0f%%</b> &middot; <b>5 legs %.0f%%</b> &middot;
    <b>9 legs %.1f%%</b>.
    A nine-leg card needs every leg; going 6-3 pays exactly the same as going 0-9.
    Singles and 2&ndash;3 leg combos are where a %.1f%% hit rate is actually worth something.</div>
  <div class="btnote dim">Backtest uses current-season stats applied to past games, so it flatters
    the model somewhat. It is a calibration check, not a profit claim &mdash; no closing prices were
    captured, and hit rate alone does not establish an edge against the vig.</div>
</div>""" % (bt["n"], bt["from"], bt["to"], bt["AB"][0], bt["AB"][1], ab_pct,
               bt["PASS"][0], bt["PASS"][1], pass_pct, ab_pct,
               100 * r ** 2, 100 * r ** 3, 100 * r ** 5, 100 * r ** 9, ab_pct)

    return """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>MLB Model — %s</title>
<style>
:root{--bg:#0b0f14;--card:#141b24;--card2:#0f1620;--fg:#e7edf3;--dim:#8fa3b8;--line:#22303f;
--a:#22c55e;--b:#eab308;--pass:#64748b;--red:#ef4444;--acc:#38bdf8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text",Segoe UI,Roboto,sans-serif;padding:16px;max-width:820px;margin:0 auto}
h1{font-size:20px;margin:0 0 2px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);margin:26px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.sub{color:var(--dim);font-size:13px;margin-bottom:16px}
.rowstats{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;flex:1;min-width:110px}
.stat .k{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.stat .v{font-size:20px;font-weight:600;margin-top:2px}
.stat .sub2{font-size:10.5px;color:var(--dim);margin-top:3px;line-height:1.35}
.card{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--pass);border-radius:10px;padding:13px 15px;margin-bottom:11px}
.card.tier-a{border-left-color:var(--a)}
.card.tier-b{border-left-color:var(--b)}
.chead{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.match{font-weight:600;font-size:16px}
.rec{color:var(--dim);font-weight:400;font-size:12px}
.status{font-size:12px;color:var(--dim);text-align:right}
.live{color:var(--acc);font-weight:700}
.livescore{font-weight:600;color:var(--fg)}
.inn{color:var(--dim);font-size:11.5px}
.strip{background:var(--card2);border:1px solid var(--line);border-radius:10px;
  padding:11px 13px;margin:14px 0 4px}
.striph{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
  margin-bottom:8px}
.scg{display:flex;gap:8px;flex-wrap:wrap}
.sc{background:var(--card);border:1px solid var(--line);border-radius:7px;padding:7px 10px;
  min-width:88px}
.sc.live{border-color:var(--acc)}
.sc.won{border-left:3px solid var(--a)}
.sc.lost{border-left:3px solid var(--red)}
.scs{font-size:13px;font-weight:600;line-height:1.35;font-variant-numeric:tabular-nums}
.scl{font-size:10.5px;color:var(--dim);margin-top:3px;text-transform:uppercase;letter-spacing:.05em}
.final{color:var(--fg)}
.win{color:var(--a);font-weight:700}
.loss{color:var(--red);font-weight:700}
.pitchers{color:var(--dim);font-size:12.5px;margin:5px 0 9px}
.pickrow{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:7px}
.tier{font-size:10.5px;font-weight:800;letter-spacing:.07em;padding:3px 7px;border-radius:5px;color:#04121a}
.tier.tier-a{background:var(--a)} .tier.tier-b{background:var(--b)} .tier.tier-pass{background:var(--pass);color:#e7edf3}
.team{font-weight:650}
.prob{font-variant-numeric:tabular-nums;font-weight:600}
.fair,.chip{color:var(--dim);font-size:12.5px}
.chip{background:var(--card2);border:1px solid var(--line);padding:2px 7px;border-radius:5px}
.lock{font-size:10px;color:var(--dim);border:1px solid var(--line);padding:2px 6px;border-radius:5px}
.book{font-size:12px;color:var(--acc);margin-bottom:6px}
.edge{font-size:12px;margin-top:4px;padding:5px 8px;border-radius:6px;border:1px solid var(--line)}
.edge-good{color:var(--a);border-color:#1c4532;background:#0e1f18}
.edge-bad{color:#fbbf24;border-color:#4a3410;background:#1f1708}
.edge-flat{color:var(--dim)}
.notes{margin-top:9px;border-top:1px solid var(--line);padding-top:8px}
.notes .nh{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin-bottom:4px}
.notes ul{margin:0;padding-left:17px;font-size:12.5px;color:#b8c6d4}
.notes li{margin-bottom:4px}
.notes.warn ul{color:#fbbf24}
.src{color:var(--dim)}
.proj{font-size:12px;color:var(--dim);border-top:1px solid var(--line);padding-top:7px;margin-top:4px}
.why{margin:8px 0 0;padding-left:17px;font-size:13px;color:#c3d0dd}
.why li{margin-bottom:4px}
.none{color:var(--dim);background:var(--card);border:1px solid var(--line);padding:14px;border-radius:10px}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);color:var(--dim);font-size:12px}
code{background:var(--card2);padding:1px 5px;border-radius:4px;font-size:12px}
.read{background:#0e1f18;border:1px solid #1c4532;border-radius:10px;padding:13px 15px;margin:4px 0 10px}
.rh{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--a);margin-bottom:8px;font-weight:700}
.vrow{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:13.5px;padding:4px 0}
.vrow span{color:var(--dim)}
.dogflag{color:#fbbf24;font-style:normal}
.pos{color:var(--a)}
.rnote{font-size:12px;color:#c3d0dd;margin-top:9px;border-top:1px solid #1c4532;padding-top:8px;line-height:1.55}
.bt{background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:13px 15px;margin:4px 0 6px}
.bth{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin-bottom:8px}
.btrow{display:flex;justify-content:space-between;font-size:13.5px;padding:3px 0}
.btrow b{font-variant-numeric:tabular-nums}
.bad{color:var(--red)}
.btnote{font-size:12px;color:#c3d0dd;margin-top:9px;border-top:1px solid var(--line);padding-top:8px;line-height:1.55}
.btnote.dim{color:var(--dim)}
</style></head><body>

<h1>MLB Model &mdash; %s</h1>
<div class="sub">Updated %s &middot; rebuilds every 5 min, scores live &middot; %d games</div>

%s
<div class="rowstats">
  <div class="stat"><div class="k">Live record</div><div class="v">%s</div></div>
  <div class="stat"><div class="k">PLAY tier</div><div class="v">%s</div></div>
  <div class="stat"><div class="k">LEAN tier</div><div class="v">%s</div></div>
  <div class="stat"><div class="k">Days graded</div><div class="v">%d</div></div>
  <div class="stat"><div class="k">ROI on priced picks</div><div class="v">%s</div>
    <div class="sub2">%s</div></div>
</div>
%s
%s

<h2>Plays &mdash; 60%%+ confidence</h2>
%s

<h2>Leans &mdash; 55.5&ndash;60%%</h2>
%s

<h2>Pass &mdash; too close to bet</h2>
%s

<footer>
<p><strong>How this works.</strong> Each starter is converted to a regressed RA/9 from FIP components
(HR, BB, K) blended with ERA and pulled toward league average by sample size, then combined with that
team's bullpen for the innings the starter is not projected to cover. Offenses are park-neutralized
season run rates nudged by 3-week form. The two run expectations are simulated 30,000 times with a
negative-binomial run distribution to produce win, run-line and total probabilities.
League baseline: %.2f R/G, %.2f ERA.</p>
<p><strong>Fair odds are model prices, not book prices.</strong> Where a real sportsbook line appears
above, it was read from a named source. Lines are never estimated — a blank means no line was found,
because a guessed line is worse than no line.</p>
<p><strong>Hit rate is not profit.</strong> On the companion board's 140 priced
graded picks, the ones whose model probability beat the de-vigged price went 41-45
(47.7%%) for +10.4%% ROI, while the ones that lost to the price went 35-19 (64.8%%) for
+3.9%%. The worse record made more money because it was paid better. So the price is
frozen here alongside each pick and the record carries ROI, not just wins.</p>
<p><strong>Picks lock at first pitch</strong> and are graded automatically against final scores, so
the record above is the model's real one, including its losses.</p>
<p>Generated %s by <code>build.py</code>. No LLM in the loop.</p>
</footer>
</body></html>""" % (
        date_str, date_str, generated, len(games), strip,
        wl(all_rec), wl(a_rec), wl(b_rec), rec["days"],
        ("%+.1f%%" % (100 * rec["pl"] / rec["staked"])) if rec["staked"] else "--",
        ("%d priced picks%s" % (
            rec["staked"],
            (" · +EV subset %+.1f%%" % (100 * rec["ev_pl"] / rec["ev_staked"]))
            if rec["ev_staked"] else "")) if rec["staked"] else "no prices recorded yet",
        read, bt_block,
        body_plays,
        body_leans or '<p class="none">None.</p>',
        body_pass or '<p class="none">None.</p>',
        M.lg_cache["rpg"], M.lg_cache["era"], generated)

# ---------------------------------------------------------------- main

def main():
    # MLB schedules by Eastern date. On a UTC runner dt.date.today() would roll the
    # slate over at 8pm ET and drop every late game.
    today = (dt.datetime.now(TZ) if TZ else dt.datetime.now()).date()
    date_str = sys.argv[1] if len(sys.argv) > 1 else today.isoformat()
    sl = M.fetch_slate(date_str)
    team_stats = M.fetch_team_stats(SEASON)
    lg = M.league_constants(team_stats)
    M.lg_cache = lg
    form = M.fetch_recent_form(date_str)
    parks = M.fetch_team_parks(SEASON)
    lgby = {}
    for y in (SEASON - 1, SEASON - 2):
        d = M.fetch_league_season(y)
        if d:
            lgby[y] = d
    lines = load_json(LINES, {})

    games = []
    pen_cache = {}
    for date in sl.get("dates", []):
        for g in date.get("games", []):
            try:
                for side in ("away", "home"):
                    tid = g["teams"][side]["team"]["id"]
                    if tid not in team_stats:
                        raise ValueError("no season stats for team id %s" % tid)
                a = analyze_game(g, team_stats, form, lg, pen_cache, parks, lgby)
                a["pick"] = build_pick(a)
                games.append(a)
            except Exception as e:
                sys.stderr.write("skip game %s: %s\n" % (g.get("gamePk"), e))

    games.sort(key=lambda x: (-x["pick"]["p"], x["start_utc"]))
    try:
        hr_board, lg_hr = build_hr_board(games, team_stats, lg)
    except Exception as e:
        sys.stderr.write("hr board: %s\n" % e)
        hr_board, lg_hr = [], 0.0
    hist = update_history(date_str, games, lines)
    if grade_open_days(hist, date_str):
        save_json(HISTORY, hist)

    now = dt.datetime.now(TZ) if TZ else dt.datetime.now()
    generated = now.strftime("%b %-d, %-I:%M %p %Z").strip()
    notes = load_json(NOTES, {})
    html = render(date_str, games, hist, lines, notes, generated)
    os.makedirs(SITE, exist_ok=True)
    with open(os.path.join(SITE, "index.html"), "w") as f:
        f.write(html)
    save_json(os.path.join(SITE, "data.json"),
              {"date": date_str, "generated": generated, "league": lg,
               "league_hr_rate": lg_hr, "hr_board": hr_board, "games": games})
    print("built %d games -> %s/index.html" % (len(games), SITE))


if __name__ == "__main__":
    main()
