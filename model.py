#!/usr/bin/env python3
"""
MLB run-expectancy model.

Data source: MLB StatsAPI (statsapi.mlb.com) - free, public, no API key.
No LLM calls. No paid services. Runs entirely on stdlib Python.
"""
import json
import math
import random
import sys
import urllib.request
import urllib.error
import datetime as dt
import os

API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb-daily-model/1.0"}
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "cache")

# ---------------------------------------------------------------- http

def get(url, cache_key=None, max_age=0):
    """GET JSON with optional on-disk cache (max_age seconds)."""
    path = os.path.join(CACHE, cache_key + ".json") if cache_key else None
    if path and max_age and os.path.exists(path):
        if (dt.datetime.now().timestamp() - os.path.getmtime(path)) < max_age:
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            if path:
                os.makedirs(CACHE, exist_ok=True)
                with open(path, "w") as f:
                    json.dump(data, f)
            return data
        except Exception as e:  # noqa
            last = e
    # fall back to stale cache rather than failing the whole run
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    raise last

# ---------------------------------------------------------------- helpers

def ip_to_float(ip):
    """'126.2' -> 126.667 (baseball innings notation)."""
    try:
        s = str(ip)
        if "." not in s:
            return float(s)
        whole, outs = s.split(".")
        return float(whole) + float(outs) / 3.0
    except Exception:
        return 0.0


def f(v, default=0.0):
    """StatsAPI uses 'INF' and '-.--' sentinels; float('INF') would poison the sim."""
    try:
        out = float(v)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))

# ---------------------------------------------------------------- park factors
# 3-year run park factors, 100 = neutral. Keyed by venue-name substring so a
# team moving stadiums doesn't silently use the wrong park.
PARKS = [
    ("Coors", 112), ("Fenway", 107), ("Great American", 105),
    ("Sutter Health", 106), ("Chase Field", 103), ("Citizens Bank", 102),
    ("Globe Life", 101), ("Truist", 101), ("Rogers Centre", 101),
    ("Nationals Park", 101), ("Kauffman", 101), ("Wrigley", 100),
    ("American Family", 100), ("Angel Stadium", 100), ("Rate Field", 100),
    ("Guaranteed Rate", 100), ("Yankee Stadium", 99), ("Daikin", 99),
    ("Minute Maid", 99), ("Target Field", 99), ("Busch", 98),
    ("PNC Park", 98), ("Dodger Stadium", 98), ("Comerica", 97),
    ("Progressive", 97), ("Camden", 97), ("Citi Field", 97),
    ("loanDepot", 96), ("Petco", 96), ("Tropicana", 96),
    ("George M. Steinbrenner", 104), ("Oracle Park", 94), ("T-Mobile", 94),
    # neutral-site regular-season games
    ("Field of Dreams", 103), ("Journey Bank", 103), ("Harp Helu", 130),
    ("Rickwood", 100), ("London Stadium", 112), ("Tokyo Dome", 100),
]


UNKNOWN_PARKS = set()


def park_factor(venue_name):
    """Longest key first so 'Rate Field' cannot shadow 'Guaranteed Rate Field'."""
    name = (venue_name or "").lower()
    for key, val in sorted(PARKS, key=lambda kv: -len(kv[0])):
        if key.lower() in name:
            return val / 100.0
    if venue_name and venue_name not in UNKNOWN_PARKS:
        UNKNOWN_PARKS.add(venue_name)
        sys.stderr.write("park_factor: no entry for %r, using neutral 1.00\n" % venue_name)
    return 1.00

# ---------------------------------------------------------------- fetch

def fetch_slate(date_str):
    url = (API + "/schedule?sportId=1&date=" + date_str + "&gameType=R" +
           "&hydrate=probablePitcher,team,linescore,venue,decisions")
    return get(url)


def fetch_team_stats(season):
    hit = get(API + "/teams/stats?season=%d&sportIds=1&group=hitting&stats=season" % season,
              cache_key="team_hitting_%d" % season, max_age=3600 * 6)
    pit = get(API + "/teams/stats?season=%d&sportIds=1&group=pitching&stats=season" % season,
              cache_key="team_pitching_%d" % season, max_age=3600 * 6)
    out = {}
    for split in hit["stats"][0]["splits"]:
        out.setdefault(split["team"]["id"], {})["hit"] = split["stat"]
    for split in pit["stats"][0]["splits"]:
        out.setdefault(split["team"]["id"], {})["pit"] = split["stat"]
    return out


def fetch_bullpen(team_id, season):
    url = (API + "/teams/%d/stats?season=%d&stats=statSplits&group=pitching"
           "&sitCodes=rp&gameType=R" % (team_id, season))
    try:
        d = get(url, cache_key="pen_%d_%d" % (team_id, season), max_age=3600 * 6)
        return d["stats"][0]["splits"][0]["stat"]
    except Exception:
        return None


def fetch_pitcher(pid, season):
    url = (API + "/people/%d?hydrate=stats(group=[pitching],type=[season],"
           "season=%d,gameType=R)" % (pid, season))
    try:
        d = get(url, cache_key="p_%d_%d" % (pid, season), max_age=3600 * 6)
        person = d["people"][0]
        stats = person.get("stats", [])
        if not stats or not stats[0].get("splits"):
            return None
        return stats[0]["splits"][0]["stat"]
    except Exception:
        return None


def fetch_recent_form(date_str, days=21):
    """Per-team runs scored/allowed over recent completed games."""
    end = dt.datetime.strptime(date_str, "%Y-%m-%d").date() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days)
    url = (API + "/schedule?sportId=1&startDate=%s&endDate=%s&hydrate=linescore"
           % (start.isoformat(), end.isoformat()))
    d = get(url, cache_key="recent_%s" % date_str, max_age=3600 * 6)
    form = {}
    for date in d.get("dates", []):
        for g in date.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            try:
                a = g["teams"]["away"]; h = g["teams"]["home"]
                ar, hr = a.get("score"), h.get("score")
                if ar is None or hr is None:
                    continue
                for tid, rs, ra in ((a["team"]["id"], ar, hr), (h["team"]["id"], hr, ar)):
                    e = form.setdefault(tid, {"g": 0, "rs": 0, "ra": 0, "w": 0})
                    e["g"] += 1; e["rs"] += rs; e["ra"] += ra
                    if rs > ra:
                        e["w"] += 1
            except Exception:
                continue
    return form


def fetch_lineup(game_pk):
    """Confirmed batting orders, once posted."""
    try:
        d = get(API + "/game/%d/boxscore" % game_pk)
        out = {}
        for side in ("away", "home"):
            t = d["teams"][side]
            order = t.get("battingOrder") or []
            names = []
            for pid in order[:9]:
                p = t["players"].get("ID%s" % pid, {})
                names.append(p.get("person", {}).get("fullName", "?"))
            out[side] = names
        return out
    except Exception:
        return {"away": [], "home": []}

# ---------------------------------------------------------------- league constants

def league_constants(team_stats):
    tot = {"r": 0.0, "g": 0.0, "hr": 0.0, "bb": 0.0, "hbp": 0.0, "k": 0.0,
           "ip": 0.0, "er": 0.0, "ra": 0.0}
    for tid, s in team_stats.items():
        h = s.get("hit", {}); p = s.get("pit", {})
        tot["r"] += f(h.get("runs")); tot["g"] += f(h.get("gamesPlayed"))
        tot["hr"] += f(p.get("homeRuns")); tot["bb"] += f(p.get("baseOnBalls"))
        tot["hbp"] += f(p.get("hitByPitch")); tot["k"] += f(p.get("strikeOuts"))
        tot["ip"] += ip_to_float(p.get("inningsPitched")); tot["er"] += f(p.get("earnedRuns"))
        tot["ra"] += f(p.get("runs"))
    lg_rpg = tot["r"] / max(tot["g"], 1)
    lg_era = 9.0 * tot["er"] / max(tot["ip"], 1)
    fip_raw = (13 * tot["hr"] + 3 * (tot["bb"] + tot["hbp"]) - 2 * tot["k"]) / max(tot["ip"], 1)
    c_fip = lg_era - fip_raw
    lg_ra9 = 9.0 * tot["ra"] / max(tot["ip"], 1)
    return {"rpg": lg_rpg, "era": lg_era, "cfip": c_fip, "ra9": lg_ra9,
            "r_per_er": (tot["ra"] / tot["er"]) if tot["er"] else 1.08}

# ---------------------------------------------------------------- pitcher model

SP_PRIOR_BF = 250.0     # league-average prior, applied AFTER multi-season blending
PEN_PRIOR_BF = 400.0


PRIOR_SEASON_W = {1: 0.55, 2: 0.28}   # seasons back -> weight relative to current


def fetch_league_season(season):
    """League ERA + FIP constant for a past season (for era-adjusting priors)."""
    try:
        d = get(API + "/teams/stats?season=%d&sportIds=1&group=pitching&stats=season" % season,
                cache_key="lgpit_%d" % season, max_age=86400 * 7)
        tot = {"hr": 0.0, "bb": 0.0, "hbp": 0.0, "k": 0.0, "ip": 0.0, "er": 0.0}
        for sp in d["stats"][0]["splits"]:
            st = sp["stat"]
            tot["hr"] += f(st.get("homeRuns")); tot["bb"] += f(st.get("baseOnBalls"))
            tot["hbp"] += f(st.get("hitByPitch")); tot["k"] += f(st.get("strikeOuts"))
            tot["ip"] += ip_to_float(st.get("inningsPitched")); tot["er"] += f(st.get("earnedRuns"))
        era = 9.0 * tot["er"] / max(tot["ip"], 1)
        cfip = era - (13 * tot["hr"] + 3 * (tot["bb"] + tot["hbp"]) - 2 * tot["k"]) / max(tot["ip"], 1)
        return {"era": era, "cfip": cfip}
    except Exception:
        return None


def fetch_pitcher_history(pid):
    """Season-by-season pitching lines, aggregated per season (handles mid-year trades)."""
    try:
        d = get(API + "/people/%d?hydrate=stats(group=[pitching],type=[yearByYear],gameType=R)" % pid,
                cache_key="ph_%d" % pid, max_age=86400 * 3)
        out = {}
        for sp in d["people"][0]["stats"][0]["splits"]:
            if not sp.get("team", {}).get("id"):
                continue    # skip the combined row; we sum the team rows ourselves
            yr = int(sp["season"])
            st = sp["stat"]
            e = out.setdefault(yr, {"ip": 0.0, "bf": 0.0, "hr": 0.0, "bb": 0.0,
                                    "hbp": 0.0, "k": 0.0, "er": 0.0})
            e["ip"] += ip_to_float(st.get("inningsPitched")); e["bf"] += f(st.get("battersFaced"))
            e["hr"] += f(st.get("homeRuns")); e["bb"] += f(st.get("baseOnBalls"))
            e["hbp"] += f(st.get("hitByPitch")); e["k"] += f(st.get("strikeOuts"))
            e["er"] += f(st.get("earnedRuns"))
        return out
    except Exception:
        return {}


def fetch_start_workload(pid, season):
    """Average innings in games this pitcher actually STARTED.

    Season IP/GS is wrong for swingmen and openers: it divides total innings
    (relief included) by starts only, which projected an opener to 6.8 innings.
    """
    try:
        d = get(API + "/people/%d/stats?stats=gameLog&group=pitching&season=%d&gameType=R"
                % (pid, season), cache_key="gl_%d_%d" % (pid, season), max_age=3600 * 12)
        sp = d["stats"][0]["splits"]
    except Exception:
        return None
    starts = [x for x in sp if f(x["stat"].get("gamesStarted")) > 0]
    if not starts:
        return None
    starts.sort(key=lambda x: x.get("date", ""))
    recent = starts[-5:]
    all_ip = [ip_to_float(x["stat"].get("inningsPitched")) for x in starts]
    rec_ip = [ip_to_float(x["stat"].get("inningsPitched")) for x in recent]
    avg_all = sum(all_ip) / len(all_ip)
    avg_rec = sum(rec_ip) / len(rec_ip)
    # weight recent starts, but do not overreact to one short outing
    est = 0.6 * avg_rec + 0.4 * avg_all
    return {
        "exp_ip": clamp(est, 1.0, 7.0),
        "n_starts": len(starts),
        "avg_all": round(avg_all, 2),
        "avg_recent": round(avg_rec, 2),
        "opener": est < 3.0,
    }


def _fip_from(ip, hr, bb, hbp, k, cfip):
    if ip < 1:
        return None
    return (13 * hr + 3 * (bb + hbp) - 2 * k) / ip + cfip


def pitcher_prior(history, season, lg, lg_by_season):
    """Weighted, run-environment-adjusted FIP prior from previous seasons.
    Returns (prior_fip_on_current_scale, effective_bf)."""
    num = den = 0.0
    for back, w in PRIOR_SEASON_W.items():
        yr = season - back
        h = history.get(yr)
        if not h or h["ip"] < 20:
            continue
        lgy = lg_by_season.get(yr) or lg
        fip = _fip_from(h["ip"], h["hr"], h["bb"], h["hbp"], h["k"], lgy["cfip"])
        if fip is None:
            continue
        # re-center that season's run environment onto this season's
        fip_adj = fip - lgy["era"] + lg["era"]
        num += w * h["bf"] * fip_adj
        den += w * h["bf"]
    if den < 1:
        return None, 0.0
    return num / den, den


def pitcher_true_talent(stat, lg, history=None, lg_by_season=None, season=2026):
    """Regressed RA/9 estimate for a starter. Returns (ra9, detail dict)."""
    if not stat:
        # No season data (debut / callup): replacement-ish, on the RA/9 scale.
        return (lg["era"] + 0.35) * lg.get("r_per_er", 1.08), {"unknown": True}
    ip = ip_to_float(stat.get("inningsPitched"))
    bf = f(stat.get("battersFaced"))
    if ip < 1 or bf < 1:
        return (lg["era"] + 0.35) * lg.get("r_per_er", 1.08), {"unknown": True}
    hr = f(stat.get("homeRuns")); bb = f(stat.get("baseOnBalls"))
    hbp = f(stat.get("hitByPitch")); k = f(stat.get("strikeOuts"))
    era = f(stat.get("era"), lg["era"])
    fip = (13 * hr + 3 * (bb + hbp) - 2 * k) / ip + lg["cfip"]

    # blend this season with prior seasons BEFORE regressing to league average,
    # so an established ace is not treated as an unknown quantity
    prior_fip, prior_bf = (None, 0.0)
    if history:
        prior_fip, prior_bf = pitcher_prior(history, season, lg, lg_by_season or {})
    if prior_fip is not None and prior_bf > 0:
        comb_fip = (bf * fip + prior_bf * prior_fip) / (bf + prior_bf)
        eff_bf = bf + prior_bf
    else:
        comb_fip, eff_bf = fip, bf

    w = eff_bf / (eff_bf + SP_PRIOR_BF)
    fip_r = w * comb_fip + (1 - w) * lg["era"]
    w_era = bf / (bf + SP_PRIOR_BF)
    era_r = w_era * era + (1 - w_era) * lg["era"]
    true = 0.82 * fip_r + 0.18 * era_r
    ra9 = true * lg.get("r_per_er", 1.08)
    gs = max(f(stat.get("gamesStarted")), 1)
    ip_per = ip / gs
    exp_ip = clamp(0.65 * ip_per + 0.35 * 5.2, 3.2, 6.8) if gs >= 3 else 4.6
    return ra9, {
        "ip": round(ip, 1), "era": stat.get("era"), "fip": round(fip, 2),
        "k9": stat.get("strikeoutsPer9Inn"), "bb9": stat.get("walksPer9Inn"),
        "hr9": stat.get("homeRunsPer9"), "whip": stat.get("whip"),
        "avg": stat.get("avg"), "gs": int(gs), "exp_ip": round(exp_ip, 1),
        "ra9": round(ra9, 2), "unknown": False,
        "prior_fip": round(prior_fip, 2) if prior_fip is not None else None,
        "prior_bf": int(prior_bf), "blend_w": round(w, 3),
    }


def bullpen_ra9(pen, lg):
    if not pen:
        return lg["era"] * lg.get("r_per_er", 1.08), {"fallback": True}
    ip = ip_to_float(pen.get("inningsPitched"))
    bf = f(pen.get("battersFaced")) or (ip * 4.3)
    if ip < 1:
        return lg["era"] * lg.get("r_per_er", 1.08), {"fallback": True}
    hr = f(pen.get("homeRuns")); bb = f(pen.get("baseOnBalls"))
    hbp = f(pen.get("hitByPitch")); k = f(pen.get("strikeOuts"))
    era = f(pen.get("era"), lg["era"])
    fip = (13 * hr + 3 * (bb + hbp) - 2 * k) / ip + lg["cfip"]
    w = bf / (bf + PEN_PRIOR_BF)
    fip_r = w * fip + (1 - w) * lg["era"]
    era_r = w * era + (1 - w) * lg["era"]
    true = (0.65 * fip_r + 0.35 * era_r) * lg.get("r_per_er", 1.08)
    return true, {"era": pen.get("era"), "fip": round(fip, 2), "ip": round(ip, 1)}

# ---------------------------------------------------------------- offense

HFA = 1.065   # measured: 2026 home win rate .5257; this reproduces .5263 at r=3.34


def team_offense(team_id, team_stats, form, lg, home_park):
    s = team_stats.get(team_id, {}).get("hit", {})
    g = max(f(s.get("gamesPlayed")), 1)
    rs_pg = f(s.get("runs")) / g
    # neutralize the team's own park (half their games are at home)
    own_adj = (1.0 + home_park) / 2.0
    neutral = rs_pg / max(own_adj, 0.6)
    fm = form.get(team_id)
    form_mult = 1.0
    if fm and fm["g"] >= 8:
        recent_pg = fm["rs"] / fm["g"]
        form_mult = clamp(1 + 0.22 * (recent_pg / max(rs_pg, 0.5) - 1), 0.90, 1.10)
    return neutral, {
        "rs_pg": round(rs_pg, 2), "ops": s.get("ops"), "hr": int(f(s.get("homeRuns"))),
        "avg": s.get("avg"), "obp": s.get("obp"), "so": int(f(s.get("strikeOuts"))),
        "form_mult": round(form_mult, 3),
        "recent_rs": round(fm["rs"] / fm["g"], 2) if fm and fm["g"] else None,
        "recent_ra": round(fm["ra"] / fm["g"], 2) if fm and fm["g"] else None,
        "recent_rec": ("%d-%d" % (fm["w"], fm["g"] - fm["w"])) if fm else None,
    }, form_mult

# ---------------------------------------------------------------- simulation

DISPERSION = 3.34  # measured: 2100 completed 2026 games, per-team runs mean 4.484,
                   # var 10.501 -> r = mean^2/(var-mean) = 3.34.
                   # Do NOT calibrate this against Pythagenpat: Pythagenpat constrains the
                   # win-probability mapping only and is satisfied by almost any r, which is
                   # how an earlier fit landed on 8.0 and made every game look too certain.


def _poisson(lam, rnd):
    if lam <= 0:
        return 0
    if lam > 30:
        # normal approximation for speed/stability
        return max(0, int(round(rnd.gauss(lam, math.sqrt(lam)))))
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        p *= rnd.random()
        if p <= L:
            return k
        k += 1


def _nb_sample(mean, rnd):
    """Negative binomial via Gamma-Poisson mixture."""
    if mean <= 0.01:
        return 0
    scale = mean / DISPERSION
    lam = rnd.gammavariate(DISPERSION, scale)
    return _poisson(lam, rnd)


def simulate(home_exp, away_exp, n=30000, seed=None):
    rnd = random.Random(seed if seed is not None else 12345)
    hw = 0
    h_cover = 0     # home -1.5
    a_cover = 0     # away -1.5
    totals = []
    h_runs = 0
    a_runs = 0
    for _ in range(n):
        h = _nb_sample(home_exp, rnd)
        a = _nb_sample(away_exp, rnd)
        # Resolve extra innings for the WINNER only. Adding a run here would double-count:
        # the run expectations already contain extra-inning scoring.
        if h == a:
            if rnd.random() < 0.52:
                hw += 1
        elif h > a:
            hw += 1
        if h - a >= 2:
            h_cover += 1
        if a - h >= 2:
            a_cover += 1
        totals.append(h + a)
        h_runs += h
        a_runs += a
    totals.sort()
    def _gt(line):
        lo, hi = 0, len(totals)
        while lo < hi:
            mid = (lo + hi) // 2
            if totals[mid] <= line:
                lo = mid + 1
            else:
                hi = mid
        return len(totals) - lo

    def p_over(line):
        """No-push over price. Integer lines can push; counting a push as an under
        would misprice four of the nine lines this emits."""
        over = _gt(line) / float(n)
        push = (totals.count(int(line)) / float(n)) if float(line).is_integer() else 0.0
        under = 1.0 - over - push
        denom = over + under
        return (over / denom) if denom > 0 else 0.5
    return {
        "p_home": hw / float(n),
        "p_away": 1 - hw / float(n),
        "p_home_rl": h_cover / float(n),
        "p_away_rl": a_cover / float(n),
        "mean_total": sum(totals) / float(n),
        "median_total": totals[n // 2],
        "p_over": {str(x): p_over(x) for x in
                   (6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5)},
        "mean_home": h_runs / float(n),
        "mean_away": a_runs / float(n),
    }

# ---------------------------------------------------------------- odds

def prob_to_american(p):
    if p <= 0 or p >= 1:
        return None
    if p >= 0.5:
        return int(round(-100.0 * p / (1 - p)))
    return int(round(100.0 * (1 - p) / p))


def fmt_odds(o):
    if o is None:
        return "-"
    return ("+%d" % o) if o > 0 else str(o)


def fetch_team_parks(season):
    """team_id -> that team's own home park factor (for neutralizing season rates)."""
    d = get(API + "/teams?sportId=1&season=%d&hydrate=venue" % season,
            cache_key="teams_%d" % season, max_age=86400)
    out = {}
    for t in d.get("teams", []):
        out[t["id"]] = park_factor(t.get("venue", {}).get("name", ""))
    return out


lg_cache = {"rpg": 4.5, "era": 4.2, "cfip": 3.1}


# ---------------------------------------------------------------- home runs
# HR park factors are NOT run park factors: Kauffman suppresses HR but plays
# neutral for runs; Fenway inflates runs but not homers.
HR_PARKS = [
    ("Great American", 120), ("Rate Field", 112), ("Guaranteed Rate", 112),
    ("Yankee Stadium", 114), ("Coors", 110), ("Citizens Bank", 110),
    ("American Family", 108), ("Dodger Stadium", 108),
    ("George M. Steinbrenner", 108), ("Sutter Health", 105),
    ("Truist", 104), ("Globe Life", 103), ("Chase Field", 103),
    ("Rogers Centre", 103), ("Angel Stadium", 103), ("Wrigley", 102),
    ("Nationals Park", 102), ("Daikin", 101), ("Minute Maid", 101),
    ("Target Field", 99), ("Progressive", 98), ("Camden", 96),
    ("Fenway", 96), ("Tropicana", 96), ("T-Mobile", 95), ("Petco", 95),
    ("Comerica", 94), ("Busch", 92), ("PNC Park", 92), ("loanDepot", 92),
    ("Kauffman", 88), ("Oracle Park", 84),
    ("Field of Dreams", 105), ("Journey Bank", 105), ("Harp Helu", 130),
]

HR_REG_PA = 170.0     # HR/PA stabilization point
HR_REG_BF = 300.0


def hr_park_factor(venue_name):
    name = (venue_name or "").lower()
    for key, val in sorted(HR_PARKS, key=lambda kv: -len(kv[0])):
        if key.lower() in name:
            return val / 100.0
    return 1.00


def fetch_roster(team_id, season):
    """Active roster with season hitting stats and handedness, one request."""
    u = (API + "/teams/%d/roster?rosterType=active"
         "&hydrate=person(stats(type=season,group=hitting,season=%d))" % (team_id, season))
    try:
        d = get(u, cache_key="rost_%d_%d" % (team_id, season), max_age=3600 * 4)
    except Exception:
        return []
    out = []
    for r in d.get("roster", []):
        pos = r.get("position", {}).get("abbreviation", "")
        if pos == "P":
            continue
        p = r.get("person", {})
        st = None
        for s in p.get("stats", []):
            if s.get("group", {}).get("displayName") == "hitting" and s.get("splits"):
                st = s["splits"][0]["stat"]
                break
        if not st:
            continue
        out.append({
            "id": p.get("id"), "name": p.get("fullName"), "pos": pos,
            "bats": (p.get("batSide") or {}).get("code", "R"),
            "pa": f(st.get("plateAppearances")), "hr": f(st.get("homeRuns")),
            "g": f(st.get("gamesPlayed")), "ops": st.get("ops"),
            "slg": st.get("slg"), "avg": st.get("avg"),
        })
    return out


def fetch_pitch_hand(pid):
    try:
        d = get(API + "/people/%d" % pid, cache_key="hand_%d" % pid, max_age=86400 * 7)
        return (d["people"][0].get("pitchHand") or {}).get("code", "R")
    except Exception:
        return "R"


def league_hr_rate(team_stats):
    hr = pa = 0.0
    for tid, s in team_stats.items():
        h = s.get("hit", {})
        hr += f(h.get("homeRuns"))
        pa += f(h.get("plateAppearances"))
    return (hr / pa) if pa else 0.031


def staff_hr_per_bf(sp_stat, pen_stat, exp_ip, lg_rate):
    """Blended HR-allowed rate per batter faced for tonight's staff."""
    def rate(st, prior):
        if not st:
            return lg_rate
        bf = f(st.get("battersFaced"))
        if bf < 1:
            ip = ip_to_float(st.get("inningsPitched"))
            bf = ip * 4.3
        if bf < 1:
            return lg_rate
        r = f(st.get("homeRuns")) / bf
        w = bf / (bf + prior)
        return w * r + (1 - w) * lg_rate
    sp = rate(sp_stat, HR_REG_BF)
    pen = rate(pen_stat, HR_REG_BF * 1.5)
    share = clamp(exp_ip / 9.0, 0.0, 1.0)
    return share * sp + (1 - share) * pen


def batter_hr_prob(bat, staff_rate, lg_rate, park, pitch_hand, team_games):
    """P(at least one HR) for one hitter in one game."""
    if bat["pa"] < 40 or bat["g"] < 15:
        return None
    # regressed HR per PA
    raw = bat["hr"] / bat["pa"]
    w = bat["pa"] / (bat["pa"] + HR_REG_PA)
    rate = w * raw + (1 - w) * lg_rate
    # opposing staff, park
    rate *= (staff_rate / lg_rate) if lg_rate else 1.0
    rate *= park
    # platoon
    b = bat["bats"]
    if b == "S" or (b == "L" and pitch_hand == "R") or (b == "R" and pitch_hand == "L"):
        rate *= 1.08
        plat = "platoon edge"
    else:
        rate *= 0.93
        plat = "same-handed"
    # expected plate appearances, and how regularly this player actually starts
    pa_g = clamp(bat["pa"] / max(bat["g"], 1), 2.0, 5.2)
    play = clamp(bat["g"] / max(team_games, 1), 0.0, 1.0)
    if play < 0.55:
        return None      # bench bat: too likely to sit to publish
    p = 1.0 - (1.0 - clamp(rate, 0.0, 0.4)) ** pa_g
    return {"p": p, "rate": rate, "pa_g": round(pa_g, 2), "platoon": plat,
            "play": round(play, 2), "raw": raw}
