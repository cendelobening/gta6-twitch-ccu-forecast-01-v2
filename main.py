#!/usr/bin/env python3
"""Forecast GTA VI's launch-day Twitch viewers, hour by hour (v2).

Reads one case file (schema makefun/twitch-ccu-case/v1) from
$MAKEFUN_INPUT_DIR (default /input) and writes, for each case, a forecast
of hours 2..24 as five quantiles each to $MAKEFUN_OUTPUT_DIR
(default /output).

Method (per hour h = 2..24, log scale, over past big launches shipped
with the case as comparables):

  log viewers(h) = a_h + b_h * log viewers(1)
                   + c_h * slope24 + d_h * diu_cos48

- slope24 is the slope of log viewers against time over the case's own
  last 24 pre-release hours (its own hype momentum into launch).
- diu_cos48 is the cosine of the first-harmonic phase of the case's own
  pre-release diurnal cycle over the last 48 hours (which timezone mix
  the audience sits in); it shifts the daytime wave.
- The slope is shrunk 60% toward 1 and the two extra coefficients 50%
  toward 0, then the intercept is re-centred on the weighted means.
  Weights: tier comparable 1.0, early-access-contaminated 0.3, weak 0.3;
  console-only launches count double. When the case itself is
  console-only, console launches count three times more again, because
  they are the relevant population for a console-only launch.
- Quantiles are the fitted median plus the empirical quantiles of the
  fit residuals. When the case is console-only, the residuals come from
  the console-only launches alone: their late-day curves sit
  systematically below the all-launch fit, so the pooled spread is both
  too wide and centred too high for them. Offsets are capped at 1.5 log
  units from the median. The median curve is smoothed over neighbouring
  hours (+/-1).
- Fallback: with fewer than 5 usable comparables, the house persistence
  spread (every hour at the hour-1 print, widening over the day).

Pure Python standard library, no network, deterministic.
"""

import json
import math
import os
import re
import sys
from datetime import datetime, timezone

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
HOURS = range(2, 25)
SLOPE_SHRINK = 0.6      # fraction the log-viewers(1) slope moves toward 1
EXTRA_SHRINK = 0.5      # fraction extra coefficients move toward 0
SPREAD_SCALE = 1.1     # residual quantile offsets are widened 10%: with
                   # the console-only residual pool (few launches) the
                   # raw empirical quantiles run a little narrow
SPREAD_CAP = 1.5        # log units from the median
CONSOLE_BOOST = 3.0     # extra weight on console comps for a console case
TIER_WEIGHT = {
    "comparable": 1.0,
    "early-access-contaminated": 0.3,
    "weak": 0.3,
}


def parse_ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def safe_log(value):
    return math.log(max(float(value), 1.0))


def quantile_sorted(sorted_xs, tau):
    if not sorted_xs:
        return 0.0
    n = len(sorted_xs)
    pos = tau * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_xs[lo]
    return sorted_xs[lo] * (hi - pos) + sorted_xs[hi] * (pos - lo)


def solve_linear(matrix, rhs):
    """Gaussian elimination with partial pivoting; None if singular."""
    n = len(rhs)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            return None
        aug[col], aug[piv] = aug[piv], aug[col]
        pv = aug[col][col]
        for r in range(n):
            if r != col and aug[r][col] != 0.0:
                factor = aug[r][col] / pv
                for k in range(col, n + 1):
                    aug[r][k] -= factor * aug[col][k]
    return [aug[i][n] / aug[i][i] for i in range(n)]


def weighted_least_squares(rows, ys, ws):
    """rows: feature vectors (first entry 1.0); returns coefficient list."""
    k = len(rows[0])
    normal = [[0.0] * k for _ in range(k)]
    target = [0.0] * k
    for row, y, w in zip(rows, ys, ws):
        for i in range(k):
            target[i] += w * row[i] * y
            for j in range(k):
                normal[i][j] += w * row[i] * row[j]
    for i in range(k):
        normal[i][i] += 1e-9
    return solve_linear(normal, target)


def tier_of(comp):
    match = re.search(r"Tier:\s*([a-z\-]+)", comp.get("unlock", ""))
    return match.group(1) if match else "unknown"


def console_only(platforms):
    text = (platforms or "").lower()
    return ("pc" not in text) and ("switch" not in text)


def comp_weight(comp, case_is_console):
    w = TIER_WEIGHT.get(comp["tier"], 0.0)
    if comp["console"]:
        w *= 2.0
        if case_is_console:
            w *= CONSOLE_BOOST
    return w


def release_curve(comp):
    """Hour -> viewers for the comparable's release day (hours 1..24)."""
    day_start = parse_ts(comp["day_start"])
    curve = {}
    for row in comp.get("hourly", []):
        try:
            hour = int((parse_ts(row["from"]) - day_start).total_seconds() // 3600) + 1
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= hour <= 24:
            viewers = row.get("viewers")
            if viewers is not None and viewers > 0:
                curve[hour] = viewers
    return curve


def _features_from_points(points):
    """slope24 and diu_cos48 from (delta_hours, log viewers, utc hour).

    points must already be restricted to before day_start. Both features
    are 0.0 when the history is too sparse to estimate them.
    """
    slope24 = 0.0
    last_day = [(d, v) for d, v, _ in points if d <= 24.0]
    if len(last_day) >= 6:
        xs = [-d for d, _ in last_day]
        ys = [v for _, v in last_day]
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if den > 0:
            slope24 = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    diu_cos48 = 0.0
    last_two_days = [(h, v) for d, v, h in points if d <= 48.0]
    if len(last_two_days) >= 12:
        by_hour = {}
        for hour, value in last_two_days:
            by_hour.setdefault(hour, []).append(value)
        profile = {h: sum(vs) / len(vs) for h, vs in by_hour.items()}
        s = sum(math.sin(2 * math.pi * h / 24.0) * m for h, m in profile.items())
        c = sum(math.cos(2 * math.pi * h / 24.0) * m for h, m in profile.items())
        diu_cos48 = math.cos(math.atan2(s, c))
    return slope24, diu_cos48


def case_features(history_hourly, day_start):
    """slope24 and diu_cos48 from the case's own pre-release hours.

    history_hourly entries: {"from", "viewers", ...}. Only points
    strictly before day_start count.
    """
    points = []
    for row in history_hourly or []:
        try:
            when = parse_ts(row["from"])
            viewers = row.get("viewers")
        except (KeyError, TypeError, ValueError):
            continue
        if viewers is None:
            continue
        delta_hours = (day_start - when).total_seconds() / 3600.0
        if delta_hours > 0:
            points.append((delta_hours, safe_log(viewers), when.hour))
    return _features_from_points(points)


def comp_features(comp):
    """Same two features for a comparable, from its pre-release hours."""
    pre = []
    day_start = parse_ts(comp["day_start"])
    for row in comp.get("hourly", []):
        try:
            when = parse_ts(row["from"])
            viewers = row.get("viewers")
        except (KeyError, TypeError, ValueError):
            continue
        if viewers is None:
            continue
        delta_hours = (day_start - when).total_seconds() / 3600.0
        if delta_hours > 0:
            pre.append((delta_hours, safe_log(viewers), when.hour))
    return _features_from_points(pre)


def house_forecast(print1):
    """Persistence baseline: every hour at the hour-1 print."""
    z = {0.05: -1.6448536269514722, 0.25: -0.6744897501960817, 0.5: 0.0,
         0.75: 0.6744897501960817, 0.95: 1.6448536269514722}
    out = {}
    for h in HOURS:
        qs = [round(print1 * math.exp(z[t] * 0.15 * math.sqrt(h - 1)), 2)
              for t in LEVELS]
        out[h] = qs
    return out


def forecast(case):
    known = (case.get("known") or [{}])[0]
    print1 = known.get("viewers") or 0
    print1 = max(float(print1), 1.0)
    log_print1 = math.log(print1)
    day_start = parse_ts(case["day_start"])

    history_hourly = case.get("history_hourly") or []
    if not history_hourly and case.get("history"):
        # Fallback: aggregate minute reads into hourly means.
        buckets = {}
        for row in case["history"]:
            try:
                when = parse_ts(row["at"])
                viewers = row.get("viewers")
            except (KeyError, TypeError, ValueError):
                continue
            if viewers is None:
                continue
            key = when.replace(minute=0, second=0, microsecond=0)
            buckets.setdefault(key, []).append(float(viewers))
        history_hourly = [
            {"from": key.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "viewers": sum(vs) / len(vs)}
            for key, vs in sorted(buckets.items())
        ]
    slope24, diu_cos48 = case_features(history_hourly, day_start)

    # The target-history comparable describes this same game's category;
    # its platforms tell us whether the case is a console-only launch.
    case_is_console = False
    references = []
    for comp in case.get("comparables", []):
        tier = tier_of(comp)
        if tier == "target-history":
            case_is_console = console_only(comp.get("platforms", ""))
            continue
        curve = release_curve(comp)
        if len(curve) < 24 or curve.get(1, 0) <= 0:
            continue
        references.append({
            "tier": tier,
            "console": console_only(comp.get("platforms", "")),
            "curve": curve,
            "features": comp_features(comp),
            "weight": 0.0,  # filled below, once the case flag is known
        })
    for ref in references:
        ref["weight"] = comp_weight(ref, case_is_console)

    if len(references) < 5:
        return house_forecast(print1)

    medians = {}
    spreads = {}
    for h in HOURS:
        rows, ys, ws = [], [], []
        for ref in references:
            if ref["weight"] <= 0:
                continue
            s24, dc48 = ref["features"]
            rows.append([1.0, safe_log(ref["curve"][1]), s24, dc48])
            ys.append(safe_log(ref["curve"][h]))
            ws.append(ref["weight"])
        beta = weighted_least_squares(rows, ys, ws)
        if beta is None:
            beta = [ys[0] if ys else 0.0, 1.0, 0.0, 0.0]
        slope = 1.0 + (beta[1] - 1.0) * (1.0 - SLOPE_SHRINK)
        shrunk = [beta[0], slope,
                  beta[2] * (1.0 - EXTRA_SHRINK),
                  beta[3] * (1.0 - EXTRA_SHRINK)]
        total_w = sum(ws)
        means = [sum(w * row[k] for row, w in zip(rows, ws)) / total_w
                 for k in range(4)]
        mean_y = sum(w * y for y, w in zip(ys, ws)) / total_w
        shrunk[0] += mean_y - sum(b * m for b, m in zip(shrunk, means))
        medians[h] = (shrunk[0] + shrunk[1] * log_print1
                      + shrunk[2] * slope24 + shrunk[3] * diu_cos48)
        # Residual quantiles: console-only launches alone when the case
        # is console-only (their spread is tighter and centred lower);
        # otherwise every reference launch.
        pool = references
        if case_is_console:
            console_refs = [r for r in references if r["console"]]
            if len(console_refs) >= 5:
                pool = console_refs
        residuals = []
        for ref in pool:
            s24, dc48 = ref["features"]
            fitted = (shrunk[0] + shrunk[1] * safe_log(ref["curve"][1])
                      + shrunk[2] * s24 + shrunk[3] * dc48)
            residuals.append(safe_log(ref["curve"][h]) - fitted)
        spreads[h] = sorted(residuals)

    smoothed = {}
    for h in HOURS:
        near = [medians[hh] for hh in range(max(2, h - 1), min(24, h + 1) + 1)]
        smoothed[h] = sum(near) / len(near)

    out = {}
    for h in HOURS:
        qs = []
        for tau in LEVELS:
            offset = quantile_sorted(spreads[h], tau) * SPREAD_SCALE
            offset = max(-SPREAD_CAP, min(SPREAD_CAP, offset))
            qs.append(math.exp(smoothed[h] + offset))
        qs = [min(max(round(q, 2), 0.01), 1e9) for q in qs]
        for i in range(1, 5):
            qs[i] = max(qs[i], qs[i - 1])
        out[h] = qs
    return out


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(output_dir, exist_ok=True)
    names = sorted(n for n in os.listdir(input_dir) if n.endswith(".in"))
    for name in names:
        case_id = name[:-3]
        with open(os.path.join(input_dir, name), "r", encoding="utf-8") as fh:
            case = json.load(fh)
        result = forecast(case)
        payload = {"hours": [{"hour": h, "q": result[h]} for h in HOURS]}
        out_path = os.path.join(output_dir, case_id + ".out")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
            fh.write("\n")


if __name__ == "__main__":
    main()
