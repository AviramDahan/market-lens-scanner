# Capital Management Upgrade - 2026-09-01

## Purpose

The entry strategy, Smart Universe, setup detection, confirmation, R/R,
earnings, cooldown, correlation, and position monitor remain unchanged. This
upgrade changes only how approved paper-trading candidates compete for capital
and how capital constraints are explained.

## Active Model

- Starting paper capital: `$100,000`.
- Base trade risk: `0.50%` (`$500`).
- High-quality confirmed trade risk: up to `0.75%` (`$750`).
- Pilot/low-tier risk: `0.25%` (`$250`).
- Maximum open portfolio heat: `2.50%` (`$2,500`).
- Maximum position allocation remains `$10,000`.
- Maximum dynamic exposure is `60%`.
- Minimum cash floor is `40%`.
- `BEAR` still blocks all new buys.

Portfolio heat is the sum of the active stop risk of all open positions. A new
position is reduced to the number of shares that fit the remaining heat. If not
even one share fits, it is not opened.

## Dynamic Exposure Curve

The existing market-regime indicators still produce risk points. Exposure is
now interpolated continuously:

| Risk points | Maximum exposure |
| ---: | ---: |
| `<= -2` | `0%` |
| `0` | `20%` |
| `2` | `30%` |
| `4` | `45%` |
| `>= 6` | `60%` |

Intermediate values are linear. For example, risk points `3.75` allow
`43.125%`, or `$43,125` on a `$100,000` portfolio. This removes the old cliff
where a small regime-score change could cut exposure from `$40,000` to
`$20,000`.

## Candidate Allocation

1. Existing open positions are processed first.
2. Active setups are processed before `No Trade` results.
3. Candidates are ordered by setup score, then R/R.
4. Each candidate must still pass every active entry gate.
5. Position size is reduced by the tightest remaining limit: cash, maximum
   position, dynamic market exposure, sector exposure, factor exposure,
   per-trade risk, or portfolio heat.

## Decision Transparency

Decision JSON now records:

- `market_regime_risk_points`
- `dynamic_exposure_limit_pct` and `dynamic_exposure_limit`
- `capital_quality_tier` and `trade_risk_budget`
- `portfolio_heat_before`, `portfolio_heat_after`, and `portfolio_heat_cap`
- `entry_eligibility_status`
- `entry_gate_blockers`
- `capital_blockers`

The statuses distinguish:

- `TECHNICAL_CANDIDATE`: the base scanner did not request a new buy.
- `ENTRY_GATES_BLOCKED`: one or more quality/risk gates failed.
- `QUALIFIED_CAPITAL_BLOCKED`: every entry gate passed, but no safe capital
  capacity remained.
- `BUY_SIMULATED`: all gates and capital constraints passed.

## Configuration

```dotenv
MARKET_LENS_DYNAMIC_EXPOSURE_ENABLED=true
MARKET_LENS_DYNAMIC_EXPOSURE_MAX_PCT=0.60
MARKET_LENS_CASH_FLOOR_PCT=0.40
MARKET_LENS_BASE_TRADE_RISK_PCT=0.005
MARKET_LENS_HIGH_QUALITY_TRADE_RISK_PCT=0.0075
MARKET_LENS_PORTFOLIO_HEAT_LIMIT_PCT=0.025
```

## Historical Evidence and Limits

The existing capital replay showed that a balanced dynamic scenario improved
the resized historical sample from `$996.51` to `$1,428.89`. That comparison is
directional, not a predictive backtest: only 28 closed trades were available,
and the replay cannot reconstruct every candidate that was never entered.

The archive also showed that technical quality gates, especially weighted R/R
and setup score, blocked far more candidates than capital alone. This upgrade
therefore removes the exposure cliff without weakening any entry gate.

## Verification

Run:

```powershell
python -m pytest -q
python agent/capital_replay.py --skip-candidate-analysis
```

The system remains paper trading only. It does not connect to a broker and does
not place real orders.
