# data/dex_price.py
# So sánh giá CEX (Binance) vs DEX (Uniswap trên Ethereum mainnet), và so sánh
# phí giao dịch + trượt giá giữa 2 mô hình.
#
# Dùng DexScreener API (https://api.dexscreener.com) — free, KHÔNG cần API key,
# endpoint /token-pairs/v1/{chainId}/{tokenAddress} trả về TẤT CẢ pool đang có
# thanh khoản cho 1 token, chọn pool USD lớn nhất để có giá đáng tin cậy nhất
# (tránh pool thanh khoản mỏng bị lệch giá).

import requests

WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # WETH canonical trên Ethereum mainnet
DEXSCREENER_BASE = "https://api.dexscreener.com"

BINANCE_TAKER_FEE_PCT = 0.075   # phí taker mặc định Binance (chưa áp dụng chiết khấu BNB/VIP)
UNISWAP_V3_FEE_TIERS_PCT = {"0.01%": 0.01, "0.05%": 0.05, "0.30%": 0.30, "1.00%": 1.00}


def fetch_dex_eth_price(chain_id="ethereum"):
    """
    Lấy giá ETH (qua WETH) trên DEX — chọn pool có thanh khoản USD cao nhất
    trong số các pool trả về, vì đây thường là pool được arbitrage sát giá
    thị trường nhất (pool nhỏ dễ bị lệch giá do trượt giá/ít được trade).
    """
    url = f"{DEXSCREENER_BASE}/token-pairs/v1/{chain_id}/{WETH_ADDRESS}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    pairs = resp.json()

    if not pairs:
        raise ValueError("DexScreener không trả về pool nào cho WETH.")

    def liquidity_usd(p):
        liq = p.get("liquidity") or {}
        return liq.get("usd") or 0

    best_pair = max(pairs, key=liquidity_usd)

    return {
        "dex_id": best_pair.get("dexId"),
        "pair_url": best_pair.get("url"),
        "price_usd": float(best_pair.get("priceUsd")),
        "liquidity_usd": liquidity_usd(best_pair),
        "quote_symbol": (best_pair.get("quoteToken") or {}).get("symbol"),
        "volume_24h_usd": (best_pair.get("volume") or {}).get("h24"),
    }


def compute_cex_dex_arbitrage(cex_price, dex_price, min_spread_pct=0.15):
    """
    So sánh giá CEX (spot Binance) và giá DEX. Trả về chênh lệch % và hướng
    cơ hội arbitrage tiềm năng — CHƯA trừ phí/gas, chỉ là chênh lệch giá thô.
    min_spread_pct: ngưỡng chênh lệch tối thiểu để coi là "đáng chú ý"
    (mặc định 0.15% — vì phí round-trip CEX+DEX thường đã ~0.4-0.6% nên
    chênh lệch nhỏ hơn mức này gần như chắc chắn không có lời sau phí).
    """
    spread_pct = (dex_price - cex_price) / cex_price * 100

    if abs(spread_pct) < min_spread_pct:
        direction = "Không đáng kể"
    elif spread_pct > 0:
        direction = "DEX đang ĐẮT hơn CEX → cơ hội lý thuyết: mua trên Binance, bán trên DEX"
    else:
        direction = "DEX đang RẺ hơn CEX → cơ hội lý thuyết: mua trên DEX, bán trên Binance"

    return {
        "cex_price": cex_price,
        "dex_price": dex_price,
        "spread_pct": spread_pct,
        "direction": direction,
    }


def compute_fee_slippage_comparison(order_size_usd, gas_gwei, eth_price,
                                     estimated_gas_units=150_000,
                                     dex_fee_tier_pct=0.30,
                                     binance_taker_fee_pct=BINANCE_TAKER_FEE_PCT):
    """
    So sánh tổng chi phí thực hiện 1 lệnh khối lượng order_size_usd (USD):
    - CEX (Binance): chỉ có phí taker (%), không có "gas", trượt giá phụ
      thuộc độ sâu order book (không tính ở đây, xem thêm order book depth).
    - DEX (Uniswap V3): phí pool (%) + phí gas cố định (không phụ thuộc quy mô
      lệnh — đây là điểm mấu chốt: lệnh càng NHỎ, gas càng chiếm tỷ trọng lớn).
    estimated_gas_units: gas ước lượng cho 1 swap Uniswap V3 đơn giản (~150k gas
      là mức phổ biến cho swap 1 hop; swap qua nhiều hop hoặc phức tạp hơn sẽ cao hơn).
    """
    gas_cost_eth = (gas_gwei * estimated_gas_units) / 1e9
    gas_cost_usd = gas_cost_eth * eth_price

    binance_fee_usd = order_size_usd * (binance_taker_fee_pct / 100)
    dex_fee_usd = order_size_usd * (dex_fee_tier_pct / 100) + gas_cost_usd

    total_binance_pct = binance_taker_fee_pct
    total_dex_pct = (dex_fee_usd / order_size_usd) * 100 if order_size_usd > 0 else None

    cheaper = "Binance (CEX)" if binance_fee_usd < dex_fee_usd else "Uniswap (DEX)"

    return {
        "order_size_usd": order_size_usd,
        "binance_fee_usd": binance_fee_usd,
        "binance_fee_pct": total_binance_pct,
        "dex_fee_usd": dex_fee_usd,
        "dex_fee_pct": total_dex_pct,
        "dex_gas_cost_usd": gas_cost_usd,
        "dex_pool_fee_usd": order_size_usd * (dex_fee_tier_pct / 100),
        "cheaper_venue": cheaper,
    }
