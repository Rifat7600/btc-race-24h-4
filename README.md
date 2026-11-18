# EMA-Trend BTC Bot

[![make.fun](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/Rifat7600/btc-race-24h-4/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/Rifat7600/btc-race-24h-4/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)

An entry for the make.fun bounty
[Race a BTC trading bot live for 24 hours](https://make.fun/bounty/btc-race-24h-20261118-1444)
(`btc-race-24h-20261118-1444`, harness `bars-replay/v1`).

## Strategy

EMA trend with a deep-discount reversal filter and a profit target:

- Track a fast EMA (30 bars) and a slow EMA (120 bars) of 1-minute closes,
  plus a 14-bar average true range.
- Go long (100% of equity, long only, no leverage) when the fast EMA is
  above the slow EMA, the close is above the fast EMA and not extended more
  than 60 bps above it, and the price is at least 120 bps below its
  480-bar high — i.e. only buy a confirmed uptrend while it is still
  deeply discounted from recent highs, never chase a top.
- Exit on a +80 bps take-profit, on the fast EMA crossing back below the
  slow EMA (trend broken), or on an 8x ATR stop below entry.
- Fully deterministic: every decision follows from the bars seen so far and
  the hello seed. No clock, no randomness, no network, no files read at
  runtime. Standard library only.

## Dev-window result

On the 1,440-bar dev window: **+12.62 USDC net PnL, 2 round trips
(4 orders), 0.3% max drawdown**, action log byte-identical across two runs.

## Run it

```sh
python3 main.py
```

The bot speaks `agent-fun/bars-replay/v1` on stdin/stdout: it reads one
`hello` message, then one `batch` per minute, and answers each batch with
exactly one `reply` echoing its `seq`.
