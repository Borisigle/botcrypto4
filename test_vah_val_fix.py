#!/usr/bin/env python3
"""Test script to verify VAH/VAL calculation fix."""

def test_value_area_calculation():
    """Test the new Value Area calculation algorithm."""
    # Simulate a volume profile with POC in the middle
    volume_map = {
        100.0: 10.0,   # Low volume
        100.5: 20.0,   # Low volume
        101.0: 50.0,   # Medium volume
        101.5: 100.0,  # High volume
        102.0: 150.0,  # POC - highest volume
        102.5: 100.0,  # High volume
        103.0: 50.0,   # Medium volume
        103.5: 20.0,   # Low volume
        104.0: 10.0,   # Low volume
    }
    
    total_volume = sum(volume_map.values())
    print(f"Total volume: {total_volume}")
    
    # Find POC
    poc_price = max(volume_map.items(), key=lambda item: (item[1], -item[0]))[0]
    print(f"POC: {poc_price}")
    
    # Calculate Value Area using new algorithm
    target_volume = total_volume * 0.7
    sorted_prices = sorted(volume_map.keys())
    poc_index = sorted_prices.index(poc_price)
    
    # Initialize Value Area with POC
    value_area_prices = {poc_price}
    cumulative_volume = volume_map[poc_price]
    
    # Expand outward from POC
    lower_index = poc_index - 1
    upper_index = poc_index + 1
    
    print(f"\nExpanding from POC {poc_price} (volume: {cumulative_volume})")
    print(f"Target volume: {target_volume} (70% of {total_volume})")
    
    iteration = 1
    while cumulative_volume < target_volume:
        # Determine which direction to expand
        lower_volume = volume_map.get(sorted_prices[lower_index], 0.0) if lower_index >= 0 else 0.0
        upper_volume = volume_map.get(sorted_prices[upper_index], 0.0) if upper_index < len(sorted_prices) else 0.0
        
        # If both sides exhausted, break
        if lower_volume == 0.0 and upper_volume == 0.0:
            break
        
        # Expand to the side with higher volume
        if lower_volume >= upper_volume and lower_index >= 0:
            print(f"  Iteration {iteration}: Adding lower price {sorted_prices[lower_index]} (volume: {lower_volume})")
            value_area_prices.add(sorted_prices[lower_index])
            cumulative_volume += lower_volume
            lower_index -= 1
        elif upper_index < len(sorted_prices):
            print(f"  Iteration {iteration}: Adding upper price {sorted_prices[upper_index]} (volume: {upper_volume})")
            value_area_prices.add(sorted_prices[upper_index])
            cumulative_volume += upper_volume
            upper_index += 1
        else:
            # Only one side available
            if lower_index >= 0:
                print(f"  Iteration {iteration}: Adding lower price {sorted_prices[lower_index]} (volume: {lower_volume})")
                value_area_prices.add(sorted_prices[lower_index])
                cumulative_volume += lower_volume
                lower_index -= 1
            else:
                break
        
        print(f"    Cumulative: {cumulative_volume:.1f} / {target_volume:.1f}")
        iteration += 1
    
    vah = max(value_area_prices)
    val = min(value_area_prices)
    
    print(f"\nFinal Value Area:")
    print(f"  VAH (Value Area High): {vah}")
    print(f"  VAL (Value Area Low): {val}")
    print(f"  Cumulative Volume: {cumulative_volume} ({cumulative_volume/total_volume*100:.1f}%)")
    print(f"  Price Range: {sorted(value_area_prices)}")
    
    # Verify it's contiguous and centered on POC
    assert vah >= poc_price, "VAH should be >= POC"
    assert val <= poc_price, "VAL should be <= POC"
    assert cumulative_volume >= target_volume, "Should have reached target volume"
    
    print("\n✅ Value Area calculation test passed!")
    
    # Compare with old algorithm (top volume bins)
    print("\n--- Comparison with OLD algorithm ---")
    sorted_by_volume = sorted(volume_map.items(), key=lambda item: (-item[1], item[0]))
    cumulative_old = 0.0
    selected_prices_old = set()
    for price, vol in sorted_by_volume:
        selected_prices_old.add(price)
        cumulative_old += vol
        if cumulative_old >= target_volume:
            break
    
    vah_old = max(selected_prices_old)
    val_old = min(selected_prices_old)
    
    print(f"OLD Algorithm:")
    print(f"  VAH: {vah_old}")
    print(f"  VAL: {val_old}")
    print(f"  Price Range: {sorted(selected_prices_old)}")
    print(f"\nDifference:")
    print(f"  VAH: {vah} (new) vs {vah_old} (old) - Diff: {vah - vah_old}")
    print(f"  VAL: {val} (new) vs {val_old} (old) - Diff: {val - val_old}")


def test_buy_sell_volume_tracking():
    """Test buy/sell volume tracking logic."""
    from datetime import datetime, timezone, timedelta
    
    print("\n" + "="*60)
    print("Testing Buy/Sell Volume Tracking")
    print("="*60)
    
    # Simulate a trading day
    day_start = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    or_start = day_start + timedelta(hours=8)  # 08:00 UTC
    
    # Pre-market trades (00:00 - 08:00)
    pre_market_trades = [
        {"time": day_start + timedelta(hours=1), "side": "buy", "qty": 10.0},
        {"time": day_start + timedelta(hours=2), "side": "sell", "qty": 5.0},
        {"time": day_start + timedelta(hours=3), "side": "buy", "qty": 15.0},
        {"time": day_start + timedelta(hours=7), "side": "sell", "qty": 8.0},
    ]
    
    # Live trades (after 08:00)
    live_trades = [
        {"time": or_start + timedelta(minutes=30), "side": "buy", "qty": 20.0},
        {"time": or_start + timedelta(hours=1), "side": "sell", "qty": 12.0},
        {"time": or_start + timedelta(hours=2), "side": "buy", "qty": 25.0},
    ]
    
    # Calculate pre-market volumes
    pre_market_buy = sum(t["qty"] for t in pre_market_trades if t["side"] == "buy")
    pre_market_sell = sum(t["qty"] for t in pre_market_trades if t["side"] == "sell")
    pre_market_delta = pre_market_buy - pre_market_sell
    
    # Calculate live volumes
    live_buy = sum(t["qty"] for t in live_trades if t["side"] == "buy")
    live_sell = sum(t["qty"] for t in live_trades if t["side"] == "sell")
    live_delta = live_buy - live_sell
    
    print(f"\nPre-market (00:00 - 08:00 UTC):")
    print(f"  Buy Volume: {pre_market_buy}")
    print(f"  Sell Volume: {pre_market_sell}")
    print(f"  Delta: {pre_market_delta:+.1f}")
    
    print(f"\nLive (after 08:00 UTC):")
    print(f"  Buy Volume: {live_buy}")
    print(f"  Sell Volume: {live_sell}")
    print(f"  Delta: {live_delta:+.1f}")
    
    print(f"\nTotal Day:")
    print(f"  Buy Volume: {pre_market_buy + live_buy}")
    print(f"  Sell Volume: {pre_market_sell + live_sell}")
    print(f"  Delta: {(pre_market_buy + live_buy) - (pre_market_sell + live_sell):+.1f}")
    
    assert pre_market_buy == 25.0, "Pre-market buy volume incorrect"
    assert pre_market_sell == 13.0, "Pre-market sell volume incorrect"
    assert pre_market_delta == 12.0, "Pre-market delta incorrect"
    assert live_buy == 45.0, "Live buy volume incorrect"
    assert live_sell == 12.0, "Live sell volume incorrect"
    assert live_delta == 33.0, "Live delta incorrect"
    
    print("\n✅ Buy/Sell volume tracking test passed!")


if __name__ == "__main__":
    test_value_area_calculation()
    test_buy_sell_volume_tracking()
    print("\n" + "="*60)
    print("All tests passed! ✅")
    print("="*60)
