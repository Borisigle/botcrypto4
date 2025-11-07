# VWAP & POC Methodology

This document summarizes how the backend service computes VWAP (Volume-Weighted Average Price) and POC (Point of Control) for the BTCUSDT perpetual futures feed, and how these values are aligned with TradingView configuration.

## Data contracts & exchange information

On service startup the backend queries `fapi/v1/exchangeInfo` from the Binance Futures REST API for the configured symbol (default `BTCUSDT`). We persist the relevant filter values:

- **tickSize** – price precision used for binning the volume profile
- **stepSize** – quantity precision for orders
- **minQty** – minimum order size
- **minNotional** – minimum notional value when available

The parsed payload is logged once on startup and exposed through `GET /debug/exchangeinfo` for verification. When the fetch fails the service continues running using a configurable fallback tick size.

### Price binning utility

The service now uses a shared price binning utility (`backend/app/context/price_bins.py`) that provides:

- **Precise Decimal math**: Eliminates floating-point drift in price quantization
- **Tick-size validation**: Ensures tick sizes are reasonable for crypto markets
- **Configurable fallback**: Uses `PROFILE_TICK_SIZE` environment variable when exchange info is unavailable
- **Consistent binning**: Same logic applied to live ingestion and backfill modes

The `quantize_price_to_tick()` function implements floor rounding to match TradingView's volume profile behavior.

### Environment variables

- `PROFILE_TICK_SIZE` – Fallback tick size when exchange info fails (default: 0.1)
- `SYMBOL` – Trading symbol (default: BTCUSDT)

## Session boundaries

Trades are aggregated on a rolling UTC session. Each trading day starts at `00:00:00Z` and ends at the next midnight. The opening range (OR) spans from `08:00:00Z` to `08:10:00Z`. Trades outside the active session window are ignored. The first five trades executed between `00:00` and `00:05` are logged to confirm that the accumulator anchors correctly at midnight.

## VWAP calculation

The primary VWAP computation is anchored to midnight and uses **base volume** (BTC). For every accepted trade we accumulate:

- `sum_price_qty = Σ(price × qty_base)`
- `sum_qty = Σ(qty_base)`
- `trade_count = number of session trades`

VWAP (base mode) is `sum_price_qty / sum_qty`.

For diagnostics we also maintain an alternate **quote** mode where `volume = price × qty_base` (USDT notional). In that mode the numerator is `Σ(price × volume_quote) = Σ(price² × qty_base)` and the denominator is `Σ(volume_quote)`. The default API responses continue to report the base-volume VWAP, but `/context` accepts an optional `vwap_mode=quote` query parameter so the alternate calculation can be inspected without restarting the service.

The in-memory checkpoint (`GET /debug/vwap`) exposes `sum_price_qty`, `sum_qty`, `vwap`, `trade_count`, plus the first and most recent trade snapshots for manual reconciliation.

## POC calculation

POC is derived from a volume profile built with the exchange `tickSize` using the shared price binning utility.

1. For each trade we snap the execution price down to the nearest tick-size bin using `quantize_price_to_tick()`
2. We accumulate base volume (BTC) per bin using precise Decimal arithmetic
3. The **POC** is the bin with the largest total volume. Ties are broken by selecting the lower price.
4. The top ten bins are kept in memory and exposed via `GET /debug/poc` (sorted by descending volume, then ascending price).
5. Value Area metrics reuse the binned profile and select prices until 70 % of the total volume is covered.

When historical data is bootstrapped the same binning logic is applied, ensuring the previous-day levels line up with live calculations.

### Fallback behavior

If exchange info fetching fails or returns invalid tick sizes:

1. The service logs a warning and falls back to `PROFILE_TICK_SIZE` (default: 0.1)
2. All price binning continues using the fallback tick size
3. The service remains operational and provides consistent results
4. Debug endpoints show the effective tick size being used

## API surface

| Endpoint | Purpose |
| --- | --- |
| `GET /context?vwap_mode=base|quote` | Full context payload with selectable VWAP mode (defaults to base) |
| `GET /levels?vwap_mode=…` | Levels subset mirroring `context` |
| `GET /debug/vwap` | Raw VWAP accumulators and trade checkpoints |
| `GET /debug/poc` | Tick-size, top bins, and current POC |
| `GET /debug/exchangeinfo` | Cached Binance exchangeInfo fields |

Periodic logs (every 10 minutes during the active session) emit: anchor date, VWAP, POC, `sum_price_qty`, `sum_qty`, and `trade_count` so long-running sessions can be audited without hitting the API.

## TradingView alignment

To reproduce TradingView values use the following chart configuration:

- Symbol: `BINANCE:BTCUSDT.P`
- VWAP: anchored daily at `00:00 UTC`, volume source set to **Base/Quantity**
- Volume Profile: Session mode, Volume (not TPO), row size = tick size (0.1 at the time of writing)

### Reconciliation checklist

When comparing backend POC values with TradingView:

1. **Verify tick size alignment**: Ensure both systems use the same tick size
   - Check `GET /debug/exchangeinfo` for the backend tick size
   - Verify TradingView row size matches the exchange tickSize

2. **Check fallback usage**: If exchange info failed, ensure `PROFILE_TICK_SIZE` matches TradingView row size

3. **Validate price binning**: Test with synthetic data to confirm binning behavior:
   ```python
   # Example: 101.505 should bin to 101.5 with tickSize=0.1
   from app.context.price_bins import quantize_price_to_tick
   quantize_price_to_tick(101.505, 0.1)  # Should return 101.5
   ```

4. **Tie-breaking verification**: Ensure ties are resolved by selecting the lower price

5. **Volume source confirmation**: Both systems should use base volume (BTC) for POC calculation

### Expected precision

Comparing the TradingView overlay with `/debug/vwap` and `/debug/poc` at the same timestamp should result in discrepancies smaller than ±0.05 % when using the same anchor and inputs. Remaining deviations should be logged alongside the collected debug payload when investigated.

If future audits identify persistent differences linked to volume source selection, switch `/context?vwap_mode=quote` to validate the quote-based calculation. Any systematic gap that remains after matching tick size, anchor, and volume definition should be documented with the observed root cause and mitigation plan.

### Troubleshooting common discrepancies

| Symptom | Likely cause | Fix |
|---|---|---|
| POC differs by exactly one tick size | Different rounding modes or tie-breaking | Verify floor rounding is used in both systems |
| POC differs by multiple tick sizes | Different tick size configuration | Check exchange info vs TradingView row size |
| Inconsistent results across restarts | Floating-point drift | Ensure Decimal math is used (shared utility) |
| Large discrepancies | Different volume source or time window | Verify both use base volume and same session boundaries |
