#!/usr/bin/env python3
"""Leave-one-out validation for main.py (v2).

Each of the 37 usable comparables in the kit's p00 case takes a turn as
a pseudo-case: hour 1 known, hours 2..24 scored with the contest's
log-scale pinball loss. The reference set is the other comparables.
The pseudo-case is fed to main.forecast() as a synthetic case dict, so
this validates the shipped entrypoint code, not a copy of it. The
synthetic case's target-history entry carries the pseudo-case game's
own platforms, so the console-only flag main.py derives from it is the
pseudo-case's own.

Subsets: all 37 pseudo-cases; hour-1 >= 10k (the GTA-like ones);
console-only (7, most GTA-like); hour-1 >= 100k (mega launches).
Also prints per-hour losses and 90% coverage per subset.

Usage: python3 validate.py [path/to/p00.in]
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main

TAUS = (0.05, 0.25, 0.5, 0.75, 0.95)
KIT = os.path.expanduser(
    "~/workspace/makefun/kit_gta6/gta6-twitch-ccu-forecast-01-kit/input/p00.in")


def pinball(y, q, tau):
    d = math.log(max(y, 1)) - math.log(q)
    return 2 * max(tau * d, (tau - 1) * d)


def main_loo():
    path = sys.argv[1] if len(sys.argv) > 1 else KIT
    with open(path, "r", encoding="utf-8") as fh:
        case = json.load(fh)
    comps = []
    for comp in case["comparables"]:
        tier = main.tier_of(comp)
        if tier == "target-history":
            continue
        curve = main.release_curve(comp)
        if len(curve) < 24 or curve.get(1, 0) <= 0:
            continue
        comps.append(comp)
    print("usable comparables: %d" % len(comps))

    totals = {"all": [], "h1ge10k": [], "console": [], "mega": []}
    cover = {"all": [0, 0], "h1ge10k": [0, 0], "console": [0, 0],
             "mega": [0, 0]}
    per_hour = {h: [] for h in range(2, 25)}
    per_hour_h1 = {h: [] for h in range(2, 25)}
    per_hour_con = {h: [] for h in range(2, 25)}
    for i, comp in enumerate(comps):
        others = comps[:i] + comps[i + 1:]
        curve = main.release_curve(comp)
        # Pre-release hourly points of the pseudo-case, as history.
        day_start = main.parse_ts(comp["day_start"])
        history_hourly = [
            {"from": row["from"], "viewers": row["viewers"]}
            for row in comp.get("hourly", [])
            if main.parse_ts(row["from"]) < day_start
        ]
        fake_target = {
            "name": comp["name"] + " category before release",
            "platforms": comp.get("platforms", ""),
            "unlock": "Tier: target-history",
            "day_start": comp["day_start"],
            "hourly": [],
        }
        synth = {
            "known": [{"viewers": curve[1]}],
            "day_start": comp["day_start"],
            "history_hourly": history_hourly,
            "comparables": others + [fake_target],
        }
        result = main.forecast(synth)
        for h in range(2, 25):
            y = curve[h]
            loss = sum(pinball(y, q, t)
                       for q, t in zip(result[h], TAUS)) / 5
            totals["all"].append(loss)
            per_hour[h].append(loss)
            covered = result[h][0] <= y <= result[h][4]
            cover["all"][0] += covered
            cover["all"][1] += 1
            if curve[1] >= 10000:
                totals["h1ge10k"].append(loss)
                per_hour_h1[h].append(loss)
                cover["h1ge10k"][0] += covered
                cover["h1ge10k"][1] += 1
            if main.console_only(comp.get("platforms", "")):
                totals["console"].append(loss)
                per_hour_con[h].append(loss)
                cover["console"][0] += covered
                cover["console"][1] += 1
            if curve[1] >= 100000:
                totals["mega"].append(loss)
                cover["mega"][0] += covered
                cover["mega"][1] += 1

    def mean(xs):
        return sum(xs) / len(xs) if xs else float("nan")

    print("LOO pinball loss (log scale, lower is better)")
    for name in ("all", "h1ge10k", "console", "mega"):
        c = cover[name]
        print("  %-8s %.4f   coverage %.3f  (n=%d)"
              % (name, mean(totals[name]), c[0] / c[1], len(totals[name]) // 23))
    print("per-hour LOO loss, hour-1 >= 10k subset:")
    print("  " + " ".join("h%d:%.2f" % (h, mean(per_hour_h1[h]))
                          for h in range(2, 25)))
    print("per-hour LOO loss, console-only subset:")
    print("  " + " ".join("h%d:%.2f" % (h, mean(per_hour_con[h]))
                          for h in range(2, 25)))
    print("per-hour LOO loss, all:")
    print("  " + " ".join("h%d:%.2f" % (h, mean(per_hour[h]))
                          for h in range(2, 25)))


if __name__ == "__main__":
    main_loo()
