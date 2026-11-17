# Build notes — gta6-twitch-ccu-forecast-01-v2

## Model
Per hour h = 2..24, a weighted least-squares fit in log space over the
comparables shipped with the case:

    log viewers(h) = a_h + b_h * log viewers(1)
                     + c_h * slope24 + d_h * diu_cos48

- `slope24`: slope of log viewers against time over the case's own last
  24 pre-release hours (hype momentum into launch), from
  `history_hourly`.
- `diu_cos48`: cosine of the first-harmonic phase of the case's own
  pre-release diurnal cycle over the last 48 hours (which timezone mix
  the audience sits in); it shifts the daytime wave. The 48-hour window
  is deliberate: the final case's history only reaches back ~2 days
  (recorder started 2026-11-17), and the 48h window scored better in
  LOO than the 7-day window (0.2477 vs 0.2493 on hour-1 >= 10k).
- Slope shrunk 60% toward 1, extra coefficients shrunk 50% toward 0,
  intercept re-centred on the weighted means.
- Weights: `comparable` 1.0, `early-access-contaminated` 0.3, `weak`
  0.3; console-only launches count double. When the case itself is
  console-only, console launches count 3x more again. The case's
  console-only flag comes from the target-history comparable's
  platforms (it describes this same game's category); GTA VI is
  PS5 + Xbox Series X|S, so the flag is on for the final.
- Quantiles = fitted median + empirical quantiles of the fit residuals,
  scaled 1.1, capped at 1.5 log units from the median; median smoothed
  over neighbouring hours (+/-1). When the case is console-only the
  residuals come from the console-only launches alone.

Why the console-only treatment: in LOO the console launches' residuals
against the all-launch fit are systematically negative from mid-day on
(e.g. at h20 all six console residuals sit in [-1.12, -0.74]) — the
pooled fit over-predicts console launches' late hours, because PC /
global-unlock launches sustain higher evening viewership. The pooled
spread is therefore both too wide and centred too high for a
console-only launch. Using the console launches' own residuals fixes
the centre as well as the width.

Fallback to the house persistence spread if fewer than 5 comparables
inform an hour. Pure Python stdlib, no network, deterministic.

## Leave-one-out validation (`python3 validate.py`)
Each of the 37 usable comparables in turn is a pseudo-case (hour 1
known, hours 2–24 scored with the contest's log-scale pinball loss);
reference set = the other 36. `validate.py` feeds each pseudo-case to
`main.forecast()` as a synthetic case dict, so it tests the shipped
entrypoint, not a copy. The synthetic case's target-history entry
carries the pseudo-case game's own platforms, so the console flag is
the pseudo-case's own. Numbers:

| subset | v2 | v1 (reported) | v1 (own replication) |
|---|---|---|---|
| all 37 pseudo-cases | 0.5503 | 0.5235 | 0.5851 |
| hour-1 >= 10k (28, GTA-like) | **0.2463** | 0.2831 | 0.2929 |
| console-only (7, most GTA-like) | **0.1230** | 0.2085 | 0.2032 |
| hour-1 >= 100k (16 mega launches) | 0.2364 | — | 0.2644 |

vs v1 reported: hour-1 >= 10k -13.0%, console-only -41.0%. The gain is
broad-based, not one launch: 19/28 hour-1 >= 10k pseudo-cases and 5/7
console pseudo-cases improve (Saros, h1 = 6,400, is the one console
case that worsens). 90% coverage: all 0.766, hour-1 >= 10k 0.902,
console-only 0.770, mega 0.927 (target ~0.90; the console spread is
narrow because its residual pool is only 6 launches in LOO — 11 in
the final, which also adds the 4 practice launches).

Per-hour LOO loss, hour-1 >= 10k subset (v2 vs v1 replication):
v2:  h2 0.12, h3 0.17, h4 0.16, h5 0.18, h6 0.26, h7 0.27, h8 0.26,
     h9 0.26, h10 0.25, h11 0.24, h12 0.25, h13 0.26, h14 0.27, h15 0.27,
     h16 0.26, h17 0.26, h18 0.24, h19 0.27, h20 0.29, h21 0.29,
     h22 0.28, h23 0.27, h24 0.27
v1:  h2 0.11, h3 0.17, h4 0.16, h5 0.18, h6 0.26, h7 0.27, h8 0.27,
     h9 0.28, h10 0.29, h11 0.31, h12 0.33, h13 0.35, h14 0.37, h15 0.37,
     h16 0.36, h17 0.35, h18 0.34, h19 0.33, h20 0.34, h21 0.34,
     h22 0.32, h23 0.31, h24 0.32
The improvement concentrates in hours 10–19 (0.29–0.37 -> 0.24–0.27).

Per-hour LOO loss, console-only subset (v2):
h2 0.10, h3 0.21, h4 0.15, h5 0.12, h6 0.13, h7 0.14, h8 0.15, h9 0.13,
h10 0.13, h11 0.11, h12 0.10, h13 0.09, h14 0.09, h15 0.05, h16 0.07,
h17 0.09, h18 0.10, h19 0.12, h20 0.12, h21 0.12, h22 0.12, h23 0.18,
h24 0.22 (v1 replication: 0.07–0.34, worst at h23–24).

## Local run tests (p00–p03)
- `MAKEFUN_INPUT_DIR=<kit>/input MAKEFUN_OUTPUT_DIR=out python3 main.py`:
  all 4 outputs valid (23 rows, hours 2–24, 5 finite non-decreasing
  quantiles each, 0 < q <= 1e9, <= 65,536 bytes).
- Deterministic: two runs byte-identical (`diff -r` clean).
- Runtime: ~0.25 s for all 4 cases (~0.06 s/case; limit 8 s/case).
- Sanity: median curve dips in the small hours, rises through the day
  and peaks at hours 19–20 (US afternoon/evening UTC), e.g. p00
  (Wolverine, h1 = 23,357): h2 ≈ 14.8k → h12 ≈ 29.9k → h19 ≈ 84.2k →
  h24 ≈ 70.1k. V2's late-day medians run lower than v1's for console
  cases — that is the console-only correction above, not a bug.
- Note: the practice answers are not shipped with the kit and were not
  re-fetched (the kit's NOTICE asks not to bulk-download SullyGnome to
  extend the data), so no local practice score is claimed; LOO is the
  honest local check.

## Ideas tested and rejected (LOO)
- Unlock-wave phase shift: pre-release diurnal peak hour vs launch-day
  peak hour correlate only -0.12 — no signal; the wave idea as a phase
  shift is dead. (The diurnal *cosine* as a regression feature does
  help; the phase-shift warp does not.)
- Quadratic log-viewers(1) term: much worse (0.3550 on hour-1 >= 10k).
- Quantile regression per hour/level: gradient implementation diverged;
  dropped — the median + empirical residual quantiles are stable.
- Excluding/downweighting global-unlock or trickle-hour-1 comparables:
  no effect on the GTA-like subsets (only one trickle launch exists).
- Rolling-midnight-only median fit: console improved but coverage
  collapsed (0.58) and hour-1 >= 10k worsened.
- Console dummy inside the regression: helped console slightly but
  hurt non-console and mega launches (0.3117 / 0.2977) — rejected in
  favour of the weight boost + console residual pool, which leaves
  non-console cases untouched.
- Era/recency weighting: hurt console (0.2235 at 3-year half-life).
- diu_sin + diu_cos + diu_amp together: worse than diu_cos alone.
- Scaling the pooled spread by the console/all residual-std ratio:
  collapsed the spread (the console effect is a median shift, not a
  scale) — much worse (console 0.4635).
- Pre-release level (pre24), launch jump, channels ratio, 7-day slope:
  no improvement alone or combined with the kept features.

## Files
- `main.py` — entrypoint (written from scratch in this session, then
  revised twice: spread 1.0 -> 1.1, then the case/comparable feature
  code unified into one helper). V1's `main.py` was never read.
- `validate.py` — LOO harness (not the entrypoint)
- `LICENSE` — MIT, carries `SPDX-License-Identifier: MIT`
- `README.md` — make.fun badges + run instructions
- `.makefun/owner.json` — exactly 58 bytes, no trailing newline
