#!/usr/bin/env python3
"""Grade the model against completed games. Usage: backtest.py START END"""
import sys, os, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M
import build as B

SEASON = 2026


def main():
    start = dt.date.fromisoformat(sys.argv[1]); end = dt.date.fromisoformat(sys.argv[2])
    ts = M.fetch_team_stats(SEASON); lg = M.league_constants(ts); M.lg_cache = lg
    parks = M.fetch_team_parks(SEASON)
    lgby = {}
    for y in (SEASON - 1, SEASON - 2):
        d = M.fetch_league_season(y)
        if d: lgby[y] = d

    buckets = {}
    rows = []
    d = start
    while d <= end:
        ds = d.isoformat()
        try:
            sl = M.get(M.API + "/schedule?sportId=1&date=" + ds +
                       "&hydrate=probablePitcher,team,linescore,venue",
                       cache_key="bt_%s" % ds, max_age=86400 * 30)
        except Exception as e:
            sys.stderr.write("%s: %s\n" % (ds, e)); d += dt.timedelta(days=1); continue
        form = M.fetch_recent_form(ds)
        pen_cache = {}
        for date in sl.get("dates", []):
            for g in date.get("games", []):
                if g.get("status", {}).get("abstractGameState") != "Final":
                    continue
                hs = g["teams"]["home"].get("score"); as_ = g["teams"]["away"].get("score")
                if hs is None or as_ is None or hs == as_:
                    continue
                if not (g["teams"]["home"].get("probablePitcher") and
                        g["teams"]["away"].get("probablePitcher")):
                    continue
                try:
                    a = B.analyze_game(g, ts, form, lg, pen_cache, parks, lgby)
                except Exception:
                    continue
                a["sim"] = M.simulate(a["home"]["exp_runs"], a["away"]["exp_runs"],
                                      n=8000, seed=g["gamePk"])
                p = B.build_pick(a)
                actual = "home" if hs > as_ else "away"
                win = 1 if p["side"] == actual else 0
                b = int(p["p"] * 100 // 2.5) * 2.5
                e = buckets.setdefault(b, [0, 0, 0.0])
                e[0] += win; e[1] += 1; e[2] += p["p"]
                rows.append((ds, a["away"]["abbr"], a["home"]["abbr"], p["team"],
                             p["p"], p["tier"], win))
        d += dt.timedelta(days=1)

    print("\n=== CALIBRATION (model prob vs actual win rate) ===")
    print("%-12s %6s %8s %9s %8s" % ("bucket", "n", "predict", "actual", "diff"))
    tot_n = tot_w = 0; tot_p = 0.0
    for b in sorted(buckets):
        w, n, ps = buckets[b]
        if n < 5: continue
        print("%-12s %6d %7.1f%% %8.1f%% %+7.1f%%" %
              ("%.1f-%.1f%%" % (b, b + 2.5), n, 100 * ps / n, 100.0 * w / n,
               100.0 * w / n - 100 * ps / n))
        tot_n += n; tot_w += w; tot_p += ps
    if tot_n:
        print("-" * 48)
        print("%-12s %6d %7.1f%% %8.1f%% %+7.1f%%" %
              ("ALL", tot_n, 100 * tot_p / tot_n, 100.0 * tot_w / tot_n,
               100.0 * tot_w / tot_n - 100 * tot_p / tot_n))

    print("\n=== BY TIER ===")
    for tier in ("A", "B", "PASS"):
        sel = [r for r in rows if r[5] == tier]
        if not sel: continue
        w = sum(r[6] for r in sel)
        print("Tier %-4s %4d picks  %3d-%-3d  %.1f%%  (model expected %.1f%%)" %
              (tier, len(sel), w, len(sel) - w, 100.0 * w / len(sel),
               100.0 * sum(r[4] for r in sel) / len(sel)))
    fav = [r for r in rows if r[4] >= 0.5]
    if fav:
        print("\nAll games, always taking the model favorite: %d-%d (%.1f%%)" %
              (sum(r[6] for r in fav), len(fav) - sum(r[6] for r in fav),
               100.0 * sum(r[6] for r in fav) / len(fav)))
    json.dump([{"date": r[0], "away": r[1], "home": r[2], "pick": r[3],
                "p": r[4], "tier": r[5], "win": r[6]} for r in rows],
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", "backtest.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
