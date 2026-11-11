# Weekly digest: 2026-06-24 to 2026-06-30

Compared with 2026-06-17 to 2026-06-23. Dataset: `fixture` (SYNTHETIC fixture). Written by `zap digest` at 2026-11-11T10:27:13+00:00.

## Findings

- Orders fell 2.0% to 633 against the previous 7 days, which is within normal variation.
- Delivered GMV fell 6.8% to ₹0.04 Cr.
- The cancel rate was 6.3%, from 6.3%, and 17.9% of deliveries were late, from 17.0%.
- None of the 7 largest cities moved beyond noise.

## Brief

None. A brief is written only with `LLM_MODE=live` (a paid call) or from a saved answer, and it is dropped if it quotes a number the findings don't contain.

## The 7 largest cities, tested

| City | Orders this week | Orders last week | Change | z | q |
|---|---:|---:|---:|---:|---:|
| Mumbai | 192 | 211 | -9.0% | -0.95 | 0.782 |
| Bangalore | 161 | 176 | -8.5% | -0.82 | 0.782 |
| Bikaner | 115 | 116 | -0.9% | -0.07 | 0.948 |
| Hyderabad | 62 | 53 | +17.0% | +0.84 | 0.782 |
| Pune | 43 | 37 | +16.2% | +0.67 | 0.782 |
| Kanpur | 39 | 34 | +14.7% | +0.59 | 0.782 |
| Delhi | 21 | 19 | +10.5% | +0.32 | 0.877 |

## How to read it

Two weeks of orders always differ. `z` is the gap divided by the noise expected if order counts are random (Poisson): beyond ±3 is unusual. `q` is the chance-adjusted p-value after testing every city together (Benjamini-Hochberg); a city counts as moved only below 0.05. A week where nothing moved is a normal week.
