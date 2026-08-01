# data/binance_derivatives.py
# Dữ liệu Phái Sinh & Tâm Lý Thị Trường từ Binance (Futures + Spot).
#
# QUAN TRỌNG VỀ API KEY: TẤT CẢ các hàm trong file này gọi endpoint
# MARKET DATA CÔNG KHAI của Binance (không phải endpoint tài khoản/giao dịch),
# nên KHÔNG cần API key. Binance chỉ yêu cầu API key (header X-MBX-APIKEY)
# cho các endpoint riêng tư (đọc số dư tài khoản, đặt lệnh...). Nếu bạn có sẵn
# API key và muốn dùng để tăng weight limit, hàm _headers() bên dưới sẽ tự
# thêm header nếu biến BINANCE_API_KEY tồn tại trong .env — nhưng không bắt
# buộc, dashboard vẫn chạy bình thường nếu để trống.

import time
import json
import requests
import pandas as pd
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import BINANCE_API_KEY

FAPI_BASE = "https://fapi.binance.com"   # USDS-M Futures (funding, OI, long/short ratio)
SPOT_BASE = "https://api.binance.com"    # Spot (klines, order book)


def _headers():
    if BINANCE_API_KEY:
        return {"X-MBX-APIKEY": BINANCE_API_KEY}
    return {}


# ═══════════════════════════════════════════════════════════════
# 1. FUNDING RATE — giá trị HIỆN TẠI (khác với fetch_funding_rate_history
#    trong data/fetcher.py vốn lấy LỊCH SỬ funding mỗi 8 giờ)
# ═══════════════════════════════════════════════════════════════

def fetch_funding_rate_current(symbol="ETHUSDT"):
    """
    premiumIndex trả về funding rate SẮP ĐƯỢC ÁP DỤNG (chưa chốt), kèm
    markPrice/indexPrice — dùng để tính chênh lệch Futures vs Spot (basis).
    """
    url = f"{FAPI_BASE}/fapi/v1/premiumIndex"
    resp = requests.get(url, params={"symbol": symbol}, headers=_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return {
        "symbol": data["symbol"],
        "mark_price": float(data["markPrice"]),
        "index_price": float(data["indexPrice"]),
        "last_funding_rate_pct": float(data["lastFundingRate"]) * 100,
        "next_funding_time": pd.to_datetime(int(data["nextFundingTime"]), unit="ms"),
    }


# ═══════════════════════════════════════════════════════════════
# 2. OPEN INTEREST
# ═══════════════════════════════════════════════════════════════

def fetch_open_interest_current(symbol="ETHUSDT"):
    url = f"{FAPI_BASE}/fapi/v1/openInterest"
    resp = requests.get(url, params={"symbol": symbol}, headers=_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return float(data["openInterest"])


def fetch_open_interest_hist(symbol="ETHUSDT", period="5m", limit=200):
    """
    Lịch sử Open Interest (chỉ lưu tối đa 30 ngày gần nhất theo giới hạn Binance).
    period: '5m','15m','30m','1h','2h','4h','6h','12h','1d'
    """
    url = f"{FAPI_BASE}/futures/data/openInterestHist"
    params = {"symbol": symbol, "period": period, "limit": min(limit, 500)}
    resp = requests.get(url, params=params, headers=_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()

    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df["sumOpenInterest"] = df["sumOpenInterest"].astype(float)
    df["sumOpenInterestValue"] = df["sumOpenInterestValue"].astype(float)
    df.set_index("timestamp", inplace=True)
    return df[["sumOpenInterest", "sumOpenInterestValue"]]


# ═══════════════════════════════════════════════════════════════
# 3. LONG/SHORT RATIO — 3 góc nhìn khác nhau Binance cung cấp:
#    - "global": tỷ lệ TẤT CẢ trader (retail + tổ chức)
#    - "top_account": tỷ lệ SỐ TÀI KHOẢN của Top Trader (theo số dư ký quỹ)
#    - "top_position": tỷ lệ KHỐI LƯỢNG VỊ THẾ của Top Trader (phản ánh
#      đúng "cá voi" nặng ký hơn vì tính theo volume, không phải đầu người)
# ═══════════════════════════════════════════════════════════════

_LS_ENDPOINT = {
    "global": "/futures/data/globalLongShortAccountRatio",
    "top_account": "/futures/data/topLongShortAccountRatio",
    "top_position": "/futures/data/topLongShortPositionRatio",
}


def fetch_long_short_ratio(symbol="ETHUSDT", period="5m", limit=200, kind="top_position"):
    """
    kind: 'global' | 'top_account' | 'top_position'
    longShortRatio > 1: nghiêng Long nhiều hơn. < 1: nghiêng Short nhiều hơn.
    """
    endpoint = _LS_ENDPOINT.get(kind, _LS_ENDPOINT["top_position"])
    url = f"{FAPI_BASE}{endpoint}"
    params = {"symbol": symbol, "period": period, "limit": min(limit, 500)}
    resp = requests.get(url, params=params, headers=_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()

    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    for col in ["longShortRatio", "longAccount", "shortAccount", "longPosition", "shortPosition"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    df.set_index("timestamp", inplace=True)
    return df


def fetch_taker_buy_sell_ratio(symbol="ETHUSDT", period="5m", limit=200):
    """
    Taker Buy/Sell Volume Ratio: so với Long/Short Ratio (vị thế đang MỞ),
    chỉ báo này đo lực mua/bán CHỦ ĐỘNG (market order) trong từng khung
    thời gian — phản ánh động lượng ngắn hạn tốt hơn.
    """
    url = f"{FAPI_BASE}/futures/data/takerlongshortRatio"
    params = {"symbol": symbol, "period": period, "limit": min(limit, 500)}
    resp = requests.get(url, params=params, headers=_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()

    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df["buySellRatio"] = df["buySellRatio"].astype(float)
    df["buyVol"] = df["buyVol"].astype(float)
    df["sellVol"] = df["sellVol"].astype(float)
    df.set_index("timestamp", inplace=True)
    return df[["buySellRatio", "buyVol", "sellVol"]]


# ═══════════════════════════════════════════════════════════════
# 4. KLINES (nến giá mịn) — thay thế CoinGecko (chỉ có daily) khi cần
#    khung thời gian ngắn: 1m/5m/15m/1h/4h...
# ═══════════════════════════════════════════════════════════════

def fetch_futures_klines(symbol="ETHUSDT", interval="5m", limit=500):
    url = f"{FAPI_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
    resp = requests.get(url, params=params, headers=_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return _klines_to_df(data)


def fetch_spot_klines(symbol="ETHUSDT", interval="5m", limit=500):
    url = f"{SPOT_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}
    resp = requests.get(url, params=params, headers=_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return _klines_to_df(data)


def _klines_to_df(raw):
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "num_trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(raw, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df.set_index("open_time", inplace=True)
    return df[["open", "high", "low", "close", "volume", "quote_volume"]]


# ═══════════════════════════════════════════════════════════════
# 5. ORDER BOOK DEPTH — tìm vùng hỗ trợ/kháng cự từ các "bức tường" lệnh
# ═══════════════════════════════════════════════════════════════

def fetch_order_book_depth(symbol="ETHUSDT", limit=100):
    """
    limit hợp lệ: 5,10,20,50,100,500,1000,5000 (Binance Spot).
    Trả về 2 DataFrame (bids, asks) đã sort theo giá, kèm cột volume
    lũy kế (cumulative) để dễ vẽ biểu đồ "tường lệnh".
    """
    url = f"{SPOT_BASE}/api/v3/depth"
    resp = requests.get(url, params={"symbol": symbol, "limit": limit}, headers=_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()

    bids = pd.DataFrame(data["bids"], columns=["price", "qty"]).astype(float)
    asks = pd.DataFrame(data["asks"], columns=["price", "qty"]).astype(float)
    bids = bids.sort_values("price", ascending=False).reset_index(drop=True)
    asks = asks.sort_values("price", ascending=True).reset_index(drop=True)
    bids["cum_qty"] = bids["qty"].cumsum()
    asks["cum_qty"] = asks["qty"].cumsum()
    return bids, asks


def find_order_book_walls(bids, asks, top_n=3):
    """
    "Tường lệnh" = các mức giá có khối lượng đơn lẻ (qty) lớn bất thường
    so với phần còn lại của sổ lệnh — đơn giản lấy top_n theo qty giảm dần.
    """
    top_bid_walls = bids.nlargest(top_n, "qty")[["price", "qty"]]
    top_ask_walls = asks.nlargest(top_n, "qty")[["price", "qty"]]
    return top_bid_walls, top_ask_walls


