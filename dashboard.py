# dashboard.py — v2: single-page, MetaMask nhúng trực tiếp, on-chain nâng cao

import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from data.fetcher import (
    fetch_eth_price_history, fetch_gas_history, fetch_gas_history_hourly,
    fetch_daily_netflow_history, scan_recent_onchain_activity,
    fetch_eth_supply_stats, fetch_defillama_tvl, fetch_github_dev_activity,
    build_issuance_history, fetch_funding_rate_history,
    fetch_eth_staking_ratio_dune, fetch_eth_staking_flows_history,
)
from analytics.indicators import (
    calculate_ema, calculate_rsi, forecast_gas_arima, generate_insights,
    resample_by_timeframe, generate_final_signal, generate_derivatives_insights,
    classify_rsi, classify_netflow, classify_funding_rate, classify_tvl_change,
    classify_net_issuance, classify_oi_price_squeeze, classify_long_short_ratio,
    classify_taker_ratio, classify_price_momentum, classify_dev_activity, summarize_group, GROUP_VERDICT_RULE_TEXT,
)
from data.binance_derivatives import (
    fetch_funding_rate_current, fetch_open_interest_current, fetch_open_interest_hist,
    fetch_long_short_ratio, fetch_taker_buy_sell_ratio, fetch_futures_klines,
    fetch_order_book_depth, find_order_book_walls,
)
from data.dex_price import fetch_dex_eth_price, compute_cex_dex_arbitrage, compute_fee_slippage_comparison
from config import CONTRACT_ADDRESS, ETHERSCAN_API_KEY
from theme import inject_theme, render_signal_strip, render_section_eyebrow, render_wallet_card, PLOTLY_TEMPLATE, COLORS, render_metric_header, render_group_verdict

DERIV_SYMBOL = "ETHUSDT"  # symbol mặc định cho toàn bộ chỉ báo phái sinh Binance

st.set_page_config(page_title="InsightToken Dashboard", page_icon="📊", layout="wide")
inject_theme()

# ═══════════════════════════════════════════════════════
# NHÚNG METAMASK NGAY TRÊN CÙNG TRANG (thay cho wallet_gate.html tách riêng)
# Cơ chế: JS chạy trong iframe của components.html, sau khi lấy được balance,
# tự cập nhật URL của trang cha (window.parent) kèm query params
# → Streamlit tự động rerun với dữ liệu ví mới, không cần rời trang.
# ═══════════════════════════════════════════════════════
params = st.query_params
wallet = params.get("wallet", None)
tier = params.get("tier", "free")
balance = float(params.get("balance", 0))

if not wallet:
    st.title("InsightToken Dashboard")
    st.caption("Kết nối ví MetaMask để bắt đầu — mọi thao tác diễn ra ngay trên trang này")

    components.html(f"""
    <div style="text-align:center; padding: 40px; font-family: 'Inter', Arial, sans-serif;">
      <button id="connectBtn" style="background:{COLORS['accent']}; color:white; border:none;
        padding:14px 32px; border-radius:8px; font-size:1rem; font-weight:600; cursor:pointer;">
        Kết nối MetaMask
      </button>
      <p id="status" style="color:{COLORS['text_muted']}; margin-top:16px; font-family:'IBM Plex Mono',monospace; font-size:0.85rem;">Chưa kết nối</p>
    </div>
    <script>
      const CONTRACT_ADDRESS = '{CONTRACT_ADDRESS}';
      const SEPOLIA_RPC = 'https://ethereum-sepolia-rpc.publicnode.com';

      // Đổi chuỗi hex (wei) sang số token dạng thập phân (18 decimals),
      // dùng BigInt để tránh mất độ chính xác với số quá lớn (float JS
      // chỉ an toàn tới 2^53, trong khi balance*10^18 vượt xa mức đó).
      function weiHexToToken(hexStr, decimals = 18) {{
        const weiStr = BigInt(hexStr).toString();
        const padded = weiStr.padStart(decimals + 1, '0');
        const intPart = padded.slice(0, -decimals) || '0';
        const fracPart = padded.slice(-decimals).slice(0, 4);
        return parseFloat(intPart + '.' + fracPart);
      }}

      document.getElementById('connectBtn').onclick = async function() {{
        const statusEl = document.getElementById('status');

        // XỬ LÝ LỖI IFRAME: Tìm MetaMask ở trang gốc (window.parent)
        let ethProvider = window.ethereum;
        if (!ethProvider) {{
            try {{ ethProvider = window.parent.ethereum; }} catch(e) {{}}
        }}

        if (!ethProvider) {{
          statusEl.innerText = 'Vui lòng cài đặt MetaMask! (Hoặc nhấn F5 tải lại trang)';
          return;
        }}
        try {{
          const accounts = await ethProvider.request({{ method: 'eth_requestAccounts' }});
          const walletAddress = accounts[0];
          statusEl.innerText = 'Đang kiểm tra số dư token qua RPC Sepolia...';

          // Gọi thẳng hàm balanceOf(address) của contract qua eth_call —
          // KHÔNG qua Etherscan nữa (tránh rate-limit / lỗi API key khiến
          // balance trả về NaN như trước). eth_call là chuẩn JSON-RPC,
          // miễn phí, không cần API key.
          const selector = '0x70a08231'; // keccak256("balanceOf(address)")[:4]
          const paddedAddress = walletAddress.slice(2).padStart(64, '0');
          const callData = selector + paddedAddress;

          const rpcResp = await fetch(SEPOLIA_RPC, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              jsonrpc: '2.0', id: 1, method: 'eth_call',
              params: [{{ to: CONTRACT_ADDRESS, data: callData }}, 'latest'],
            }}),
          }});
          const rpcData = await rpcResp.json();

          if (rpcData.error || !rpcData.result || rpcData.result === '0x') {{
            statusEl.innerText = 'Lỗi khi đọc balance từ contract: ' +
              (rpcData.error ? rpcData.error.message : 'không có phản hồi hợp lệ từ RPC');
            return;
          }}

          const balanceIST = weiHexToToken(rpcData.result);
          const tier = balanceIST >= 10 ? 'premium' : 'free';

          const newUrl = window.parent.location.pathname +
            `?wallet=${{walletAddress}}&tier=${{tier}}&balance=${{balanceIST}}`;

          // GHI CHÚ QUAN TRỌNG: Streamlit render components.html() trong 1 iframe
          // có sandbox KHÔNG có quyền "allow-top-navigation". Mọi cách điều hướng
          // frame cha (window.parent.location=..., window.top.location=..., thẻ <a
          // target="_top">) đều bị trình duyệt ÂM THẦM chặn — KHÔNG throw exception,
          // nên try/catch không phát hiện được (đây là lý do bản trước bị kẹt vô hình).
          // "allow-popups" thì luôn được Streamlit cấp sẵn -> mở TAB MỚI bằng
          // window.open() là cách duy nhất chắc chắn hoạt động từ trong iframe này.
          const opened = window.open(newUrl, '_blank');

          if (opened) {{
            statusEl.innerHTML = `Kết nối thành công! Balance: ${{balanceIST}} IST — ` +
              `đã mở dashboard ở tab mới. ` +
              `<a href="${{newUrl}}" target="_blank" style="color:{COLORS['accent']};">Bấm vào đây nếu tab không tự mở</a>`;
          }} else {{
            statusEl.innerHTML = `Kết nối thành công! Balance: ${{balanceIST}} IST — ` +
              `<a href="${{newUrl}}" target="_blank" style="color:{COLORS['accent']};">Bấm vào đây để vào dashboard</a> ` +
              `(trình duyệt đang chặn popup, hãy cho phép popup cho localhost:8501)`;
          }}

        }} catch (err) {{
          statusEl.innerText = 'Lỗi: ' + err.message;
        }}
      }};
    </script>
    """, height=220)

    st.info("Chưa có ví? Bạn vẫn có thể xem bản Demo (Free Tier) bên dưới.")
    if st.button("Xem Demo (không cần kết nối ví)"):
        st.query_params["wallet"] = "Demo Mode"
        st.query_params["tier"] = "free"
        st.query_params["balance"] = "0"
        st.rerun()
    st.stop()

# ── Sidebar ─────────────────────────────────────────────
with st.sidebar:
    st.title("InsightToken")
    st.markdown("---")
    render_wallet_card(tier=tier, balance=balance, wallet_address=wallet)
    if st.button("Ngắt kết nối"):
        st.query_params.clear()
        st.rerun()

st.title("InsightToken On-Chain Analytics Dashboard")

# ── Load dữ liệu ────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_price_data():
    df = fetch_eth_price_history(days=365)
    for w in [34, 89, 200, 610]:
        df = calculate_ema(df, window=w)
    df = calculate_rsi(df, window=14)
    return df

@st.cache_data(ttl=600)
def load_gas_data():
    return fetch_gas_history(num_blocks=200)

@st.cache_data(ttl=1800)
def load_supply_stats():
    return fetch_eth_supply_stats()

@st.cache_data(ttl=3600 * 6)
def load_staking_ratio_dune():
    """
    % ETH stake lấy từ Dune (query 1933048) — thay cho số của Etherscan
    ethsupply2 vốn không chính xác. TTL dài (6 giờ) vì Dune query này tự
    làm mới theo lịch riêng của tác giả, gọi dồn dập không giúp có số mới hơn.
    """
    return fetch_eth_staking_ratio_dune()

@st.cache_data(ttl=3600)
def load_tvl_data():
    return fetch_defillama_tvl()

@st.cache_data(ttl=3600 * 6)
def load_dev_activity():
    return fetch_github_dev_activity()

@st.cache_data(ttl=1800)
def load_funding_rate():
    return fetch_funding_rate_history(symbol="ETHUSDT", limit=200)

@st.cache_data(ttl=300)
def load_onchain_scan(num_blocks, whale_threshold_eth):
    return scan_recent_onchain_activity(num_blocks=num_blocks, whale_threshold_eth=whale_threshold_eth)

@st.cache_data(ttl=3600 * 6)
def load_netflow_history(days):
    return fetch_daily_netflow_history(days=days)

@st.cache_data(ttl=3600 * 6)
def load_issuance_history(days):
    return build_issuance_history(days=days)

@st.cache_data(ttl=3600 * 6)
def load_staking_flows(days):
    return fetch_eth_staking_flows_history(days=days)

@st.cache_data(ttl=3600 * 6)
def load_gas_hourly_history(hours):
    return fetch_gas_history_hourly(hours=hours)

# ── Loaders cho nhóm Phái Sinh & Tâm Lý Thị Trường (Binance) ────────────
@st.cache_data(ttl=300)
def load_funding_current(symbol):
    return fetch_funding_rate_current(symbol=symbol)

@st.cache_data(ttl=600)
def load_oi_hist(symbol, period="5m", limit=200):
    return fetch_open_interest_hist(symbol=symbol, period=period, limit=limit)

@st.cache_data(ttl=600)
def load_long_short_ratio(symbol, period="5m", limit=200, kind="top_position"):
    return fetch_long_short_ratio(symbol=symbol, period=period, limit=limit, kind=kind)

@st.cache_data(ttl=300)
def load_taker_ratio(symbol, period="5m", limit=200):
    return fetch_taker_buy_sell_ratio(symbol=symbol, period=period, limit=limit)

@st.cache_data(ttl=180)
def load_futures_klines(symbol, interval, limit=300):
    return fetch_futures_klines(symbol=symbol, interval=interval, limit=limit)

@st.cache_data(ttl=120)
def load_order_book(symbol, limit=100):
    return fetch_order_book_depth(symbol=symbol, limit=limit)

@st.cache_data(ttl=300)
def load_dex_price():
    return fetch_dex_eth_price()

# ── Bộ chọn khung thời gian (áp dụng cho các biểu đồ có trục ngày thật) ──
st.markdown("---")
def parallel_run(tasks: dict):
    """
    Chạy nhiều hàm KHÔNG THAM SỐ, ĐỘC LẬP với nhau (gọi API/RPC — I/O-bound)
    SONG SONG bằng ThreadPoolExecutor thay vì tuần tự từng hàm một. Đây là
    tối ưu hiệu năng quan trọng nhất của dashboard: trước đây mỗi lần tải
    trang hoặc đổi khung thời gian phải chờ TỔNG thời gian của tất cả các
    lệnh gọi cộng lại (vì Streamlit chạy lại toàn bộ script mỗi khi có
    tương tác); giờ chỉ cần chờ lệnh gọi CHẬM NHẤT trong nhóm.

    tasks: dict {tên: callable không tham số}. Trả về (results, errors) —
    results[tên] = kết quả hoặc None nếu lỗi; errors[tên] = thông báo lỗi
    (chỉ có mặt nếu tên đó thất bại).
    """
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as executor:
        futures = {name: executor.submit(fn) for name, fn in tasks.items()}
        for name, fut in futures.items():
            try:
                results[name] = fut.result()
            except Exception as e:
                results[name] = None
                errors[name] = str(e)
    return results, errors


timeframe = st.radio(
    "Khung thời gian cho biểu đồ giá / kỹ thuật / TVL / Dev Activity / Funding Rate / Netflow / Net Issuance:",
    ["Ngày", "Tuần", "Tháng"], horizontal=True,
    help="Không áp dụng được cho Gas Fee và Whale Monitor (vì dữ liệu theo BLOCK, không phải theo ngày "
         "nếu muốn xem theo ngày phải quét ~7,200 block/ngày, tốn request RPC miễn phí). Riêng khung "
         "'Tuần'/'Tháng' cho Netflow và Net Issuance có thể chỉ hiển thị vài cột do backfill 30 ngày."
)

with st.spinner("Đang tải dữ liệu on-chain (song song)..."):
    _r, _err = parallel_run({
        "price_df": load_price_data,
        "gas_df": load_gas_data,
        "supply_stats": load_supply_stats,
        "staking_ratio_dune": load_staking_ratio_dune,
        "tvl_df": load_tvl_data,
        "dev_df": load_dev_activity,
        "funding_df": load_funding_rate,
    })
    price_df, gas_df = _r["price_df"], _r["gas_df"]
    supply_stats, tvl_df, dev_df, funding_df = _r["supply_stats"], _r["tvl_df"], _r["dev_df"], _r["funding_df"]
    staking_ratio_dune = _r["staking_ratio_dune"]

if price_df is None or gas_df is None:
    st.error(
        f"Không tải được dữ liệu cốt lõi (giá ETH / gas fee) — dashboard không thể hiển thị. "
        f"Chi tiết: {_err.get('price_df') or _err.get('gas_df')}. Thử tải lại trang."
    )
    st.stop()

# ═══════════════════════════════════════════════════════
# FREE TIER: On-chain Cơ Bản (giá, gas, staking, burned supply, TVL, dev activity)
# (Biểu đồ giá kèm EMA Ribbon + RSI nằm ở Premium — xem caption cuối section)
# ═══════════════════════════════════════════════════════
latest_price = price_df["price"].iloc[-1]
prev_price = price_df["price"].iloc[-2]
change_pct = (latest_price - prev_price) / prev_price * 100

# --- CÁC HÀM CLASSIFY MỚI CHO GAS VÀ STAKING ---
def classify_gas_fee_simple(gwei):
    if gwei is None: return "neutral"
    if gwei > 15: return "positive"
    if gwei < 3: return "negative"
    return "neutral"

def classify_staking_ratio_simple(ratio):
    if ratio is None: return "neutral"
    if ratio >= 28: return "positive"
    if ratio < 20: return "negative"
    return "neutral"

# --- TÍNH TOÁN CÁC BIẾN & NHÃN ---
gas_val = gas_df['baseFee_gwei'].iloc[-1] if gas_df is not None and not gas_df.empty else None
staking_val = staking_ratio_dune if staking_ratio_dune is not None else (supply_stats['staking_ratio_pct'] if supply_stats else None)

_dir_price = classify_price_momentum(change_pct)
_dir_gas = classify_gas_fee_simple(gas_val)
_dir_staking = classify_staking_ratio_simple(staking_val)
_badge_label_map = {"positive": "🟢 TÍCH CỰC", "negative": "🔴 TIÊU CỰC", "neutral": "⚪ TRUNG TÍNH"}

# --- VẼ GIAO DIỆN 3 CỘT ĐỒNG BỘ ---
col1, col2, col3 = st.columns(3)

with col1:
    with st.container(border=True):
        st.markdown(
            f'<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">'
            f'<span style="font-size:0.875rem; color:#6B6D76; font-weight:600; text-transform:uppercase;">Giá ETH Hiện Tại</span>'
            f'<span class="it-signal-badge {_dir_price}">{_badge_label_map[_dir_price]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        is_negative = change_pct < 0
        delta_color = "#D93025" if is_negative else "#188038"
        delta_bg = "#FCE8E6" if is_negative else "#E6F4EA"
        arrow = "↓" if is_negative else "↑"
        delta_html = f'<span style="background-color: {delta_bg}; color: {delta_color}; font-size: 0.85rem; font-weight: 600; padding: 2px 8px; border-radius: 4px; white-space: nowrap;">{arrow} {abs(change_pct):.2f}%</span>'
        
        st.markdown(
            f'<div style="display: flex; align-items: baseline; gap: 8px;">'
            f'<span style="font-size: 2.25rem; font-weight: 700; color: #111827; line-height: 1.2;">${latest_price:,.2f}</span>'
            f'{delta_html}'
            f'</div>',
            unsafe_allow_html=True
        )

with col2:
    with st.container(border=True):
        st.markdown(
            f'<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">'
            f'<span style="font-size:0.875rem; color:#6B6D76; font-weight:600; text-transform:uppercase;">Gas Fee (Gwei) <span title=">15 Gwei: Sôi động/Đốt nhiều ETH. <3 Gwei: Ảm đạm." style="cursor:help;">❔</span></span>'
            f'<span class="it-signal-badge {_dir_gas}">{_badge_label_map[_dir_gas]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        val_str = f"{gas_val:.4f}" if gas_val is not None else "N/A"
        st.markdown(f'<div style="font-size: 2.25rem; font-weight: 700; color: #111827; line-height: 1.2;">{val_str}</div>', unsafe_allow_html=True)

with col3:
    with st.container(border=True):
        st.markdown(
            f'<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">'
            f'<span style="font-size:0.875rem; color:#6B6D76; font-weight:600; text-transform:uppercase;">Staking Ratio <span title=">28%: Khóa cung mạnh mẽ. <20%: Dòng tiền rời staking." style="cursor:help;">❔</span></span>'
            f'<span class="it-signal-badge {_dir_staking}">{_badge_label_map[_dir_staking]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        val_str = f"{staking_val:.2f}%" if staking_val is not None else "N/A"
        st.markdown(f'<div style="font-size: 2.25rem; font-weight: 700; color: #111827; line-height: 1.2;">{val_str}</div>', unsafe_allow_html=True)

st.caption(f"IST Token Balance của bạn: {balance:.2f} IST")
st.markdown("---")

# TVL + Dev Activity — cơ bản, miễn phí, xem được ở Free Tier
colf1, colf2 = st.columns(2)
with colf1:
    render_section_eyebrow("Tổng quan Hệ sinh thái", tier="free")
    if tvl_df is not None:
        latest_tvl = tvl_df["tvl_usd"].iloc[-1]
        tvl_30d_ago = tvl_df["tvl_usd"].iloc[-30] if len(tvl_df) > 30 else tvl_df["tvl_usd"].iloc[0]
        tvl_change_pct = (latest_tvl - tvl_30d_ago) / tvl_30d_ago * 100
        render_metric_header("TVL — Hệ sinh thái DeFi trên Ethereum", classify_tvl_change(tvl_change_pct),
                              badge_help="TVL tăng >2%/30 ngày = tích cực; giảm <-2% = tiêu cực")
        st.metric("TVL hiện tại", f"${latest_tvl/1e9:,.2f}B", f"{tvl_change_pct:+.2f}% (30 ngày)")
        tvl_view = resample_by_timeframe(tvl_df[["tvl_usd"]].iloc[-540:], timeframe)
        fig_tvl = go.Figure()
        fig_tvl.add_trace(go.Scatter(x=tvl_view.index, y=tvl_view["tvl_usd"] / 1e9,
                                      fill="tozeroy", line=dict(color=COLORS["accent"])))
        fig_tvl.update_layout(template=PLOTLY_TEMPLATE, height=250, yaxis_title="TVL (tỷ USD)")
        st.plotly_chart(fig_tvl, use_container_width=True)
    else:
        render_metric_header("TVL — Hệ sinh thái DeFi trên Ethereum", "neutral")
        st.caption("Không tải được dữ liệu TVL từ DefiLlama.")
        tvl_change_pct = None

with colf2:
    # 1. Tính toán giá trị trước
    current_commits_val = None
    avg_commits_val = None
    if dev_df is not None and not dev_df.empty:
        avg_commits_val = dev_df['commits'].mean()
        current_commits_val = dev_df['commits'].iloc[-1]

    # 2. Hiển thị Tiêu đề kèm Badge (Thay cho st.subheader cũ)
    render_metric_header(
        "Developer Activity (go-ethereum)", 
        classify_dev_activity(current_commits_val, avg_commits_val),
        badge_help="Tích cực: > +20% trung bình năm | Tiêu cực: < -20% trung bình năm"
    )
    st.caption("Dữ liệu GitHub sẽ hiển thị theo TUẦN, không hiển thị theo NGÀY hay THÁNG")
    
    # 3. Vẽ biểu đồ và hiển thị số liệu
    if dev_df is not None and not dev_df.empty:
        st.metric("Commit trung bình/tuần (52 tuần)", f"{avg_commits_val:.1f}")
        dev_view = dev_df.set_index("week")
        if timeframe == "Tháng":
            dev_view = resample_by_timeframe(dev_view, "Tháng", agg="mean")
        fig_dev = go.Figure()
        fig_dev.add_trace(go.Bar(x=dev_view.index, y=dev_view["commits"], marker_color=COLORS["premium"]))
        fig_dev.update_layout(template=PLOTLY_TEMPLATE, height=250, yaxis_title="Số commit/tuần")
        st.plotly_chart(fig_dev, use_container_width=True)
    else:
        st.caption("GitHub API đang tính toán thống kê (cache miss) — thử tải lại trang sau vài giây.")

st.caption(
    "💡 Xem thêm biểu đồ giá ETH kèm chỉ báo Fibonacci EMA Ribbon (34/89/200/610) kết hợp RSI "
    "ở phần Premium bên dưới để có insight tốt hơn"
)

# --- TÍNH ĐIỂM VÀ ĐƯA RA KẾT LUẬN FREE TIER ---
_free_directions = [
    _dir_price, 
    _dir_gas, 
    _dir_staking, 
    classify_tvl_change(tvl_change_pct), 
    classify_dev_activity(current_commits_val, avg_commits_val)
]

_free_verdict = summarize_group(_free_directions)

# Tùy chỉnh màu sắc dựa trên nhãn kết luận
label_text = _free_verdict["label"].upper()
if "MUA" in label_text:
    label_color = "#188038" # Xanh lá
elif "BÁN" in label_text:
    label_color = "#D93025" # Đỏ
else:
    label_color = "#6B6D76" # Xám

# Vẽ giao diện Box Kết luận tùy chỉnh (Thay thế hoàn toàn hàm render_group_verdict mặc định)
with st.container(border=True):
    # Tiêu đề kết luận
    st.markdown(
        f"📊 <span style='font-size: 1.05rem; font-weight: 600;'>Kết luận nhóm Free Tier:</span> <span style='font-size: 1.05rem; font-weight: 700; color: {label_color};'>{label_text}</span>", 
        unsafe_allow_html=True
    )
    
    # Nội dung chi tiết được ngắt dòng và canh lề chuẩn xác
    st.markdown(
        f"""
        <div style="font-size: 0.9rem; color: #4B5563; line-height: 1.7; margin-top: 10px;">
            <b>{_free_verdict['pos']}</b> tích cực &nbsp;·&nbsp; <b>{_free_verdict['neg']}</b> tiêu cực &nbsp;·&nbsp; <b>{_free_verdict['neu']}</b> trung tính
            <br><br>
            <b>Quy tắc đưa ra kết luận:</b> (tích cực − tiêu cực) / tổng chỉ báo<br>
            &nbsp;&nbsp;• ≥ 0.6 → Mua mạnh<br>
            &nbsp;&nbsp;• ≥ 0.2 → Mua<br>
            &nbsp;&nbsp;• Trong khoảng (-0.2, 0.2) → Trung lập<br>
            &nbsp;&nbsp;• ≤ -0.2 → Bán<br>
            &nbsp;&nbsp;• ≤ -0.6 → Bán mạnh
            <br><br>
            <i style="color: #6B7280;">Điểm số được tính toán từ Giá ETH (24h), Gas Fee, Staking Ratio, TVL (30 ngày) và Developer Activity.</i>
        </div>
        """, 
        unsafe_allow_html=True
    )
st.info("🔒 **Để xem Kết luận toàn diện, phân tích kỹ thuật chuyên sâu và dự báo ARIMA, hãy đăng ký tính năng Premium.**")


# ═══════════════════════════════════════════════════════
# PREMIUM TIER: On-chain chuyên sâu + Kỹ thuật + Insight — LUÔN HIỂN THỊ CHUNG
# ═══════════════════════════════════════════════════════
if tier == "premium":
    st.markdown("---")
    render_section_eyebrow("Premium Tier — mở khoá bằng token IST", tier="premium")
    st.header("Premium: On-Chain Chuyên Sâu & Phân Tích Kỹ Thuật")

    NUM_BLOCKS_SCAN = 100
    WHALE_THRESHOLD_ETH = 1000  # ≥1000 ETH mới tính là "cá voi" — 100 ETH (~vài trăm nghìn USD) quá phổ biến để có ý nghĩa phân loại
    NETFLOW_BACKFILL_DAYS = 14    # tăng từ 30 lên 60 — mỗi ngày cần quét txlist 4 ví (Etherscan), tăng thêm sẽ chậm hơn tuyến tính
    ISSUANCE_BACKFILL_DAYS = 30   # tăng từ 30 lên 90 — burn dùng 1 batch RPC duy nhất nên gần như không tốn thêm thời gian dù tăng nhiều
    GAS_HOURLY_BACKFILL_HOURS = 720  # 90 ngày — đủ để khung Tuần/Tháng có ý nghĩa; vẫn rẻ vì chỉ 1 batch RPC dù tăng số giờ

    with st.spinner(f"Đang tải On-Chain Chuyên Sâu (song song: quét {NUM_BLOCKS_SCAN} block, dựng lịch sử "
                     f"Netflow/Net Issuance/Gas Fee theo giờ)... lần đầu có thể mất khoảng "
                     "10-20 giây, các lần sau sẽ được cache."):
        _r2, _err2 = parallel_run({
            "onchain": lambda: load_onchain_scan(num_blocks=NUM_BLOCKS_SCAN, whale_threshold_eth=WHALE_THRESHOLD_ETH),
            "netflow_hist": lambda: [time.sleep(2), load_netflow_history(NETFLOW_BACKFILL_DAYS)][1],
            "issuance_hist": lambda: [time.sleep(5), load_issuance_history(ISSUANCE_BACKFILL_DAYS)][1],
            "staking_flows": lambda: load_staking_flows(NETFLOW_BACKFILL_DAYS),
            "gas_hourly": lambda: load_gas_hourly_history(GAS_HOURLY_BACKFILL_HOURS),
        })

    onchain = _r2["onchain"]
    if onchain is not None:
        whale_df = onchain["whale_df"]
        active_addr_count = onchain["active_addresses"]
        tx_volume_eth = onchain["tx_volume_eth"]
    else:
        whale_df, active_addr_count, tx_volume_eth = None, 0, 0
        st.warning(f"Không quét được onchain activity: {_err2.get('onchain')}")

    netflow_hist = _r2["netflow_hist"]
    if netflow_hist is not None:
        netflow_latest = netflow_hist["netflow_eth"].iloc[-1] if len(netflow_hist) >= 1 else None
    else:
        netflow_latest = None
        st.warning(f"Không lấy được exchange netflow: {_err2.get('netflow_hist')}")

    issuance_hist = _r2["issuance_hist"]
    if issuance_hist is not None:
        latest_issuance_row = issuance_hist.iloc[-1] if len(issuance_hist) >= 1 else None
    else:
        latest_issuance_row = None
        st.warning(f"Không tính được Net Issuance: {_err2.get('issuance_hist')}")

    staking_flows_df = _r2["staking_flows"]
    if staking_flows_df is None:
        st.info(
            f"Chưa hiển thị được Staking Flows (nguồn Dune, query 2371805): "
            f"{_err2.get('staking_flows', 'chưa rõ lỗi')}. Kiểm tra DUNE_API_KEY trong .env."
        )

    colp1, colp2, colp3 = st.columns(3)
    colp1.metric(f"Active Addresses ({NUM_BLOCKS_SCAN} block gần nhất)", active_addr_count)
    colp2.metric(f"Tx Volume on-chain ({NUM_BLOCKS_SCAN} block gần nhất)", f"{tx_volume_eth:,.1f} ETH")
    if netflow_latest is not None:
        colp3.metric("Exchange Netflow (hôm qua)", f"{netflow_latest:+,.0f} ETH")
    else:
        colp3.metric("Exchange Netflow (hôm qua)", "Đang tải...")

    # ═══════════════════════════════════════════════
    # A. ON-CHAIN CHUYÊN SÂU
    # ═══════════════════════════════════════════════
    render_section_eyebrow("Whale · Netflow · Issuance", tier="premium")
    st.subheader("🔗 On-Chain Chuyên Sâu")

# ── PHÂN TÍCH GAS FEE (MACRO & MICRO) ──────
    st.markdown("### ⛽ Phân tích Gas Fee Mạng lưới")
    
    tab_macro, tab_micro = st.tabs([
        "📊 Xu hướng Dài hạn (Lịch sử 90 ngày)", 
        "🔮 Dự báo Ngắn hạn (Mô hình ARIMA)"
    ])
    
    with tab_macro:
        gas_hourly_df = _r2["gas_hourly"]
        if gas_hourly_df is None:
            st.warning(f"Không dựng được lịch sử Gas Fee theo giờ: {_err2.get('gas_hourly')}")

        st.markdown(f"**Gas Fee lịch sử theo giờ ({GAS_HOURLY_BACKFILL_HOURS//24} ngày gần nhất)**")
        st.caption(
            "Khác với biểu đồ dự báo giá gas bằng mô hình ARIMA (vài trăm block gần nhất, vài giờ), biểu đồ này backfill thật  theo giờ "
            "bằng cách ngoại suy block number từ block mới nhất (block time Ethereum sau The Merge gần như cố "
            "định 12 giây/block) rồi lấy baseFeePerGas qua 1 batch RPC duy nhất, không tốn thêm request nào "
            "đáng kể dù kéo dài số ngày. Cho góc nhìn xu hướng gas fee dài hạn hơn nhiều so với chart gas còn lại."
        )
        if gas_hourly_df is not None and not gas_hourly_df.empty:
            gas_hourly_view = resample_by_timeframe(gas_hourly_df.set_index("hour"), timeframe, agg="mean")
            fig_gas_hourly = go.Figure()
            fig_gas_hourly.add_trace(go.Scatter(
                x=gas_hourly_view.index, y=gas_hourly_view["baseFee_gwei"],
                line=dict(color=COLORS["premium"], width=1.5), name="Base Fee (gwei, trung bình theo kỳ)",
            ))
            y_label = "Base Fee (gwei, trung bình/giờ)" if timeframe == "Ngày" else f"Base Fee (gwei, trung bình/{timeframe.lower()})"
            fig_gas_hourly.update_layout(template=PLOTLY_TEMPLATE, height=280, yaxis_title=y_label)
            st.plotly_chart(fig_gas_hourly, use_container_width=True)
            if timeframe != "Ngày" and len(gas_hourly_view) < 3:
                st.caption(
                    f"ℹ️ Chỉ có {len(gas_hourly_view)} cột ở khung '{timeframe}' vì mới backfill "
                    f"{GAS_HOURLY_BACKFILL_HOURS//24} ngày dữ liệu — không phải lỗi, chỉ là chưa đủ lịch sử để "
                    "gộp lên khung dài."
                )
        else:
            st.caption("Đang dựng lịch sử lần đầu — vui lòng đợi hoặc tải lại trang sau ít phút.")

    with tab_micro:
        render_metric_header("Gas Fee — Thực tế & Dự báo (ARIMA) + Khoảng tin cậy 95%", "neutral",
                              badge_help="Không tính vào điểm số hướng mua/bán — chỉ mang tính thông tin")
        st.caption(
            "Gas fee là phí giao dịch on-chain dùng để trả cho validator để xử lý giao dịch" 
            "Dữ liệu được lấy theo Block (12 giây/block), và không áp dụng được bộ chọn Ngày/Tuần/Tháng ở trên."
        )
        forecast_df, gas_trend, gas_change_pct = forecast_gas_arima(gas_df, periods_ahead=20)
        st.caption(f"Xu hướng dự báo: **{gas_trend}** ({gas_change_pct:+.2f}%)")

        fig_gas = go.Figure()
        fig_gas.add_trace(go.Scatter(x=list(gas_df.index), y=gas_df["baseFee_gwei"], name="Thực tế", line=dict(color=COLORS["accent"])))
        future_x = [gas_df.index[-1] + s for s in range(1, len(forecast_df) + 1)]
        fig_gas.add_trace(go.Scatter(x=future_x, y=forecast_df["baseFee_gwei_forecast"], name="Dự báo (ARIMA)", line=dict(color="orange", dash="dot")))
        fig_gas.add_trace(go.Scatter(x=future_x, y=forecast_df["upper_95"], line=dict(width=0), showlegend=False))
        fig_gas.add_trace(go.Scatter(x=future_x, y=forecast_df["lower_95"], fill="tonexty",
                                      fillcolor="rgba(255,165,0,0.15)", line=dict(width=0), name="Khoảng tin cậy 95%"))
        fig_gas.update_layout(template=PLOTLY_TEMPLATE, height=350)
        st.plotly_chart(fig_gas, use_container_width=True)

    render_metric_header(f"Whale Transfer Monitor (≥{WHALE_THRESHOLD_ETH:,} ETH, {NUM_BLOCKS_SCAN} block gần nhất)", "neutral",
                          badge_help="Không tự thân xác định hướng — cần kết hợp Exchange Netflow để suy ra ý định mua/bán")
    st.caption(
        f"Ngưỡng {WHALE_THRESHOLD_ETH:,} ETH để quan sát các giao dịch thực sự "
        "lớn, các con số nhỏ nhơ không có ý nghĩa phân loại 'cá voi'."
    )
    if whale_df is not None and not whale_df.empty:
        whale_display = whale_df[["block", "value_eth", "from", "to"]].rename(columns={
            "block": "Block", "value_eth": "Giá trị (ETH)", "from": "Từ (ví gửi)", "to": "Đến (ví nhận)",
        })
        st.dataframe(whale_display, use_container_width=True, hide_index=True)

        whale_sorted = whale_df.sort_values(by="value_eth", ascending=False).reset_index(drop=True)
        rank_labels = [f"#{i+1}" for i in range(len(whale_sorted))]
        
        fig_whale = go.Figure()
        
        # 1. Vẽ các đường thẳng đứng (cành kẹo)
        for i in range(len(whale_sorted)):
            fig_whale.add_shape(
                type="line",
                x0=rank_labels[i], y0=0,
                x1=rank_labels[i], y1=whale_sorted["value_eth"].iloc[i],
                line=dict(color=COLORS["accent"], width=3, dash="dot")
            )
            
        # 2. Vẽ các điểm tròn (viên kẹo) đè lên trên chóp
        fig_whale.add_trace(go.Scatter(
            x=rank_labels, 
            y=whale_sorted["value_eth"],
            mode="markers+text",
            marker=dict(
                color=COLORS["accent"], 
                size=22, 
                line=dict(color="white", width=2.5)
            ),
            text=[f"{v:,.0f} ETH" for v in whale_sorted["value_eth"]],
            textposition="top center",
            textfont=dict(size=13, color="#111827", family="Inter"),
            hovertext=(
                "Block: " + whale_sorted["block"].astype(str)
                + "<br>Từ: " + whale_sorted["from"].astype(str)
                + "<br>Đến: " + whale_sorted["to"].astype(str)
                + "<br>Tx hash: " + whale_sorted["hash"].astype(str)
            ),
            hoverinfo="text",
        ))
        
        # 3. Cấu hình Layout cho thanh thoát và ÉP CHIỀU NGANG
        fig_whale.update_layout(
            template=PLOTLY_TEMPLATE, 
            height=360,
            xaxis_title=f"Giao dịch cá voi (Top {len(whale_sorted)} trong {NUM_BLOCKS_SCAN} block gần nhất)",
            yaxis_title="Giá trị (ETH)",
            xaxis=dict(
                type="category",        # Ép Plotly hiểu trục X là chữ, KHÔNG được sinh ra số thập phân 1.5, 2.5
                showgrid=False,
                range=[-0.5, max(4.5, len(whale_sorted) - 0.5)] # Bí quyết: Ép luôn có không gian chứa ít nhất 5 cột
            ),
            showlegend=False,
            margin=dict(t=40) 
        )
        
        # Nới rộng trục Y thêm 15% để các con số text trên cùng không bị cắt lẹm vào viền
        max_val = whale_sorted["value_eth"].max()
        fig_whale.update_yaxes(range=[0, max_val * 1.15])
        
        st.plotly_chart(fig_whale, use_container_width=True)
    else:
        st.caption(f"Không có giao dịch ≥{WHALE_THRESHOLD_ETH:,} ETH trong {NUM_BLOCKS_SCAN} block gần nhất — điều này bình thường, whale transfer không xảy ra liên tục. (Dữ liệu theo BLOCK, không áp dụng được bộ chọn Ngày/Tuần/Tháng ở trên.)")

    render_metric_header(
        f"Exchange Netflow — dựa trên lịch sử giao dịch thật ({NETFLOW_BACKFILL_DAYS} ngày gần nhất) · khung: {timeframe}",
        classify_netflow(netflow_latest),
        badge_help="Dương (tiền vào sàn) = tiêu cực; âm (rút khỏi sàn) = tích cực",
    )
    st.caption(
        """
        * 🟩 **DƯƠNG** = Tiền chảy VÀO ví sàn đã biết (áp lực bán tiềm năng).
        * 🟥 **ÂM** = Tiền RÚT RA (áp lực mua tiềm năng).
        
        💡 *Dữ liệu được tính toán từ lịch sử giao dịch (txlist) của 4 ví sàn lớn (Binance, Coinbase, Kraken) đã được Etherscan gắn nhãn công khai, tuy nhiên vẫn là PROXY (chưa bao gồm mọi ví của mọi sàn), nên phản ánh đúng XU HƯỚNG chứ không phải netflow toàn thị trường.*
        """
    )
    if netflow_hist is not None and not netflow_hist.empty:
        netflow_view = resample_by_timeframe(netflow_hist.set_index("date"), timeframe, agg="mean")
        if not netflow_view.empty:
            colors = [COLORS["buy"] if v > 0 else COLORS["sell"] for v in netflow_view["netflow_eth"]]
            y_label = "Netflow (ETH) — Xanh = tiền vào sàn · Đỏ = tiền rút khỏi sàn" if timeframe == "Ngày" \
                else "Netflow (ETH/ngày, trung bình trong kỳ)"            
            fig_flow = go.Figure()
            fig_flow.add_trace(go.Bar(
                x=netflow_view.index, y=netflow_view["netflow_eth"],
                marker_color=colors,
                text=[f"{v:+,.0f}" for v in netflow_view["netflow_eth"]],
                textposition="outside",
            ))
            fig_flow.add_hline(y=0, line_color="gray")
            fig_flow.update_layout(template=PLOTLY_TEMPLATE, height=320, xaxis_title="Ngày", yaxis_title=y_label)
            st.plotly_chart(fig_flow, use_container_width=True)

            if timeframe != "Ngày" and len(netflow_view) < 3:
                st.caption(
                    f"ℹ️ Chỉ có {len(netflow_view)} cột ở khung '{timeframe}' vì mới backfill "
                    f"{NETFLOW_BACKFILL_DAYS} ngày dữ liệu thật — không phải lỗi, chỉ là chưa đủ lịch sử để "
                    "gộp lên khung dài. Tăng NETFLOW_BACKFILL_DAYS trong dashboard.py nếu muốn xem xa hơn "
                    "(đổi lại: lần backfill đầu sẽ chậm hơn)."
                )

            n_partial = int(netflow_hist["is_partial"].sum()) if "is_partial" in netflow_hist else 0
            if n_partial > 0:
                st.caption(
                    f"⚠️ {n_partial}/{len(netflow_hist)} ngày có thể CHƯA ĐẦY ĐỦ 100% do (các) ví sàn phát "
                    "sinh nhiều giao dịch hơn giới hạn trả về của API miễn phí trong ngày đó — vẫn phản ánh "
                    "đúng xu hướng tăng/giảm, chỉ có thể lệch phần biên độ tuyệt đối."
                )
        else:
            st.caption("Chưa đủ dữ liệu để gộp theo khung thời gian này.")
    else:
        st.caption("Đang dựng lịch sử netflow lần đầu — vui lòng đợi hoặc tải lại trang sau khoảng 1-2 phút.")

    # ── Staking Flows (nguồn Dune, query 2371805) ──────────────────────
    # Xác định hướng của Staking Flows
    staking_netflow_latest = staking_flows_df["net_flow_eth"].iloc[-1] if (staking_flows_df is not None and not staking_flows_df.empty) else None
    
    if staking_netflow_latest is None:
        dir_staking = "neutral"
    elif staking_netflow_latest > 0:
        dir_staking = "positive" # Nạp nhiều hơn rút -> Thắt chặt nguồn cung -> Tích cực
    else:
        dir_staking = "negative" # Rút nhiều hơn nạp -> Tăng nguồn cung -> Tiêu cực

    render_metric_header(
        f"Staking Flows — ETH nạp/rút khỏi hàng đợi staking ({NETFLOW_BACKFILL_DAYS} ngày gần nhất, nguồn: Dune) · khung: {timeframe}",
        dir_staking,
        badge_help="Net Flow Dương (Nạp > Rút) = Tích cực; Âm (Rút > Nạp) = Tiêu cực",
    )
    st.caption(
        """
        * 🟩 **Cột xanh:** ETH được gửi vào staking (Deposit).
        * 🟥 **Cột đỏ:** ETH rút khỏi staking (Withdrawal, vẽ âm để dễ so sánh).
        * ⬜ **Đường trắng:** Net Flow (= Deposit − Withdrawal).
        
        **Net Flow dương kéo dài:** Thắt chặt nguồn cung (ETH bị khóa lại nhiều hơn rút ra).
        **Net Flow âm:** Tăng nguồn cung lưu thông (Rút ròng).
        
        *Dữ liệu lấy từ Dune Analytics (dashboard cộng đồng @hildobby, query 2371805).*
        """
    )
    if staking_flows_df is not None and not staking_flows_df.empty:
        staking_view = resample_by_timeframe(staking_flows_df.set_index("date"), timeframe, agg="mean")
        if not staking_view.empty:
            y_suffix = "ETH" if timeframe == "Ngày" else "ETH/ngày, trung bình trong kỳ"
            fig_staking = go.Figure()
            has_split = "deposits_eth" in staking_view and staking_view["deposits_eth"].notna().any()
            if has_split:
                fig_staking.add_trace(go.Bar(
                    x=staking_view.index, y=staking_view["deposits_eth"],
                    name="Deposit (ETH vào staking)", marker_color=COLORS["buy"],
                ))
                fig_staking.add_trace(go.Bar(
                    x=staking_view.index, y=staking_view["withdrawals_eth"],
                    name="Withdrawal (ETH rút khỏi staking)", marker_color=COLORS["sell"],
                ))
            fig_staking.add_trace(go.Scatter(
                x=staking_view.index, y=staking_view["net_flow_eth"],
                name="Net Flow", line=dict(color="white", width=2),
            ))
            fig_staking.add_hline(y=0, line_color="gray")
            fig_staking.update_layout(
                template=PLOTLY_TEMPLATE, height=340, barmode="relative",
                xaxis_title="Ngày", yaxis_title=y_suffix,
            )
            st.plotly_chart(fig_staking, use_container_width=True)
            if not has_split:
                st.caption("ℹ️ Query Dune chỉ trả về sẵn cột Net Flow (không tách deposit/withdrawal riêng), nên biểu đồ chỉ vẽ được đường Net Flow.")
        else:
            st.caption("Chưa đủ dữ liệu để gộp theo khung thời gian này.")
    else:
        st.caption("Chưa tải được Staking Flows (xem thông báo phía trên nếu có lỗi).")

    st.markdown("---")

    _net_issuance_for_badge = latest_issuance_row["net_issuance_daily_eth"] if latest_issuance_row is not None else None
    _pct_burned_for_badge = latest_issuance_row["pct_minted_supply_burned"] if latest_issuance_row is not None else None
    render_metric_header(
        f"Net Issuance & % Minted Supply Burned (ước lượng) — {ISSUANCE_BACKFILL_DAYS} ngày gần nhất · khung: {timeframe}",
        classify_net_issuance(_pct_burned_for_badge),
        badge_help="% Burn/Issuance ≥100% (thực sự giảm phát) = tích cực; <50% (lạm phát rõ) = tiêu cực",
    )
    st.caption(
        """
        **Lưu ý về phương pháp tính toán dữ liệu:**
        
        * Burn (Lượng đốt): Tính từ dữ liệu block **thật** (`baseFee × gasUsed`), lấy mẫu theo từng ngày lịch sử chứ không chỉ ngoại suy từ gas hiện tại.
        * Issuance (Lượng phát hành): Là **ước lượng** theo công thức giao thức chính thức (`940.8659 × √N ETH/năm`).
        * Trọng số Validator (N): Tạm dùng số lượng validator **hiện tại** áp dụng cho mọi ngày trong quá khứ (do giới hạn của API).
        
        💡 *Sai số ở phần ước lượng này là rất nhỏ vì lượng phát hành ETH biến động cực kỳ chậm theo từng ngày.*
        """
    )
    if latest_issuance_row is not None:
        colq1, colq2, colq3 = st.columns(3)
        colq1.metric("Issuance ước lượng/ngày", f"{latest_issuance_row['estimated_daily_issuance_eth']:,.0f} ETH")
        colq2.metric("Burn thật/ngày", f"{latest_issuance_row['estimated_daily_burn_eth']:,.0f} ETH")
        colq3.metric("Net Issuance/ngày", f"{latest_issuance_row['net_issuance_daily_eth']:+,.0f} ETH")

        issuance_view = resample_by_timeframe(issuance_hist.set_index("date"), timeframe, agg="mean")
        if not issuance_view.empty:
            fig_issuance = go.Figure()
            fig_issuance.add_trace(go.Bar(
    		x=issuance_view.index, y=issuance_view["net_issuance_daily_eth"],
    		name="Net Issuance (ETH/ngày)",
    		marker_color=[COLORS["buy"] if v > 0 else COLORS["sell"] for v in issuance_view["net_issuance_daily_eth"]],
	    ))
            fig_issuance.add_hline(y=0, line_dash="dash", line_color="gray")
            fig_issuance.update_layout(
    		template=PLOTLY_TEMPLATE, height=300,
    		xaxis_title="Ngày", yaxis_title="Net Issuance (ETH/ngày) — Xanh = lạm phát (>0) · Đỏ = giảm phát (<0)",
	    )
            st.plotly_chart(fig_issuance, use_container_width=True)
            if timeframe != "Ngày" and len(issuance_view) < 3:
                st.caption(
                    f"ℹ️ Chỉ có {len(issuance_view)} cột ở khung '{timeframe}' vì mới backfill "
                    f"{ISSUANCE_BACKFILL_DAYS} ngày dữ liệu — không phải lỗi, chỉ là chưa đủ lịch sử để gộp "
                    "lên khung dài."
                )
        else:
            st.caption("Chưa đủ dữ liệu để gộp theo khung thời gian này.")
    else:
        st.caption("Đang dựng lịch sử Net Issuance lần đầu — vui lòng đợi hoặc tải lại trang sau khoảng 1-2 phút.")

    # Đưa Staking Flows vào hệ thống tính điểm
    _onchain_directions = [
        classify_netflow(netflow_latest), 
        classify_net_issuance(_pct_burned_for_badge),
        dir_staking 
    ]
    _onchain_verdict = summarize_group(_onchain_directions)

    label_text_onchain = _onchain_verdict["label"].upper()
    if "MUA" in label_text_onchain:
        label_color_onchain = "#188038" 
    elif "BÁN" in label_text_onchain:
        label_color_onchain = "#D93025" 
    else:
        label_color_onchain = "#6B6D76" 

    with st.container(border=True):
        st.markdown(
            f"📊 <span style='font-size: 1.05rem; font-weight: 600;'>Kết luận nhóm On-Chain Chuyên Sâu (Premium):</span> <span style='font-size: 1.05rem; font-weight: 700; color: {label_color_onchain};'>{label_text_onchain}</span>", 
            unsafe_allow_html=True
        )
        
        st.markdown(
            f"""
            <div style="font-size: 0.9rem; color: #4B5563; line-height: 1.7; margin-top: 10px;">
                <b>{_onchain_verdict['pos']}</b> tích cực &nbsp;·&nbsp; <b>{_onchain_verdict['neg']}</b> tiêu cực &nbsp;·&nbsp; <b>{_onchain_verdict['neu']}</b> trung tính
                <br><br>
                <b>Quy tắc đưa ra kết luận:</b> (tích cực − tiêu cực) / tổng chỉ báo<br>
                &nbsp;&nbsp;• ≥ 0.6 → Mua mạnh<br>
                &nbsp;&nbsp;• ≥ 0.2 → Mua<br>
                &nbsp;&nbsp;• Trong khoảng (-0.2, 0.2) → Trung lập<br>
                &nbsp;&nbsp;• ≤ -0.2 → Bán<br>
                &nbsp;&nbsp;• ≤ -0.6 → Bán mạnh
                <br><br>
                <i style="color: #6B7280;">Lưu ý: Tính toán dựa trên 3 chỉ báo có hướng tài chính rõ ràng (Exchange Netflow, Net Issuance và Staking Flows). Nhóm thông số mô tả (Gas Fee và Whale Monitor) mặc định là Trung tính nên không đưa vào tỷ lệ này.</i>
            </div>
            """, 
            unsafe_allow_html=True
        )

    # ═══════════════════════════════════════════════
    # B. PHÂN TÍCH KỸ THUẬT (bao gồm Funding Rate)
    # ═══════════════════════════════════════════════
    st.markdown("---")
    render_section_eyebrow("Technical Analysis & Market Data", tier="premium")
    st.subheader("📈 Phân Tích Kỹ Thuật & Phái Sinh (Binance Futures)")

    funding_latest = funding_df["funding_rate_pct"].iloc[-1] if funding_df is not None and not funding_df.empty else None
    render_metric_header(f"Funding Rate ETH/USDT Perpetual (Binance Futures) · khung: {timeframe}",
                          classify_funding_rate(funding_latest),
                          badge_help="Funding >0.05% (Long quá tải) = tiêu cực; <-0.02% (Short quá tải) = tích cực")
    st.caption(
        "Funding Rate DƯƠNG = phe Long đang trả phí cho phe Short (thị trường nghiêng Long/tham lam). "
        "Funding Rate ÂM = phe Short trả phí cho Long (thị trường nghiêng Short/sợ hãi). "
        "Funding thu thập mỗi 8 giờ nên có thể resample theo Ngày/Tuần/Tháng bình thường."
    )
    if funding_df is not None and not funding_df.empty:
        st.metric("Funding Rate gần nhất", f"{funding_latest:+.4f}%")
        funding_view = resample_by_timeframe(funding_df, timeframe, agg="mean")
        fig_funding = go.Figure()
        fig_funding.add_trace(go.Bar(
            x=funding_view.index, y=funding_view["funding_rate_pct"],
            marker_color=[COLORS["buy"] if v > 0 else COLORS["sell"] for v in funding_view["funding_rate_pct"]],
        ))
        fig_funding.add_hline(y=0, line_color="gray")
        fig_funding.update_layout(template=PLOTLY_TEMPLATE, height=280, yaxis_title="Funding Rate (%)")
        st.plotly_chart(fig_funding, use_container_width=True)
    else:
        st.caption("Không tải được Funding Rate từ Binance (có thể do mạng chặn hoặc API tạm thời lỗi).")


    render_section_eyebrow("Binance Futures · Market Data", tier="premium")
    st.subheader(f"🔮 Phái Sinh & Tâm Lý Thị Trường — {DERIV_SYMBOL} Futures (Binance)")
    st.caption(
        "Toàn bộ dữ liệu bên dưới là market data CÔNG KHAI của Binance (không cần API key). "
        "Đây là các chỉ báo THEO PHÚT/GIỜ — độc lập với bộ chọn khung Ngày/Tuần/Tháng ở trên."
    )

    # Giá trị mặc định phòng khi 1 nguồn bị lỗi (để không làm sập phần Insight/Kết luận bên dưới)
    funding_current_val = None
    oi_change_pct_val = None
    price_change_pct_recent_val = None
    ls_ratio_val = None
    taker_ratio_val = None
    arbitrage_val = None

    with st.spinner("Đang tải dữ liệu phái sinh Binance (song song)..."):
        _r3, _err3 = parallel_run({
            "funding_info": lambda: load_funding_current(DERIV_SYMBOL),
            "oi_hist_df": lambda: load_oi_hist(DERIV_SYMBOL, period="1h", limit=240),
            "klines_df": lambda: load_futures_klines(DERIV_SYMBOL, interval="1h", limit=240),
            "ls_df": lambda: load_long_short_ratio(DERIV_SYMBOL, period="5m", limit=200, kind="top_position"),
            "taker_df": lambda: load_taker_ratio(DERIV_SYMBOL, period="5m", limit=200),
        })

    funding_info = _r3["funding_info"]
    if funding_info is not None:
        funding_current_val = funding_info["last_funding_rate_pct"]
    else:
        st.warning(f"Không lấy được Funding Rate hiện tại: {_err3.get('funding_info')}")

    oi_hist_df = _r3["oi_hist_df"]
    if oi_hist_df is not None:
        oi_current_val = oi_hist_df["sumOpenInterest"].iloc[-1]
        oi_prev_val = oi_hist_df["sumOpenInterest"].iloc[0]
        oi_change_pct_val = (oi_current_val - oi_prev_val) / oi_prev_val * 100
    else:
        st.warning(f"Không lấy được Open Interest: {_err3.get('oi_hist_df')}")

    klines_df = _r3["klines_df"]
    if klines_df is not None:
        price_change_pct_recent_val = (
            (klines_df["close"].iloc[-1] - klines_df["close"].iloc[0]) / klines_df["close"].iloc[0] * 100
        )
    else:
        st.warning(f"Không lấy được Klines: {_err3.get('klines_df')}")

    ls_df = _r3["ls_df"]
    if ls_df is not None:
        ls_ratio_val = ls_df["longShortRatio"].iloc[-1]
    else:
        st.warning(f"Không lấy được Long/Short Ratio: {_err3.get('ls_df')}")

    taker_df = _r3["taker_df"]
    if taker_df is not None:
        taker_ratio_val = taker_df["buySellRatio"].iloc[-1]
    else:
        st.warning(f"Không lấy được Taker Ratio: {_err3.get('taker_df')}")

# 1. Tính toán label (hướng) TRƯỚC khi vẽ cột
    _dir_funding_cur = classify_funding_rate(funding_current_val)
    _dir_oi = classify_oi_price_squeeze(oi_change_pct_val, price_change_pct_recent_val)
    _dir_ls = classify_long_short_ratio(ls_ratio_val)
    _dir_taker = classify_taker_ratio(taker_ratio_val)
    _badge_label_map = {"positive": "🟢 TÍCH CỰC", "negative": "🔴 TIÊU CỰC", "neutral": "⚪ TRUNG TÍNH"}

    # 2. Khởi tạo 4 cột
    colb1, colb2, colb3, colb4 = st.columns(4)

    with colb1:
        # Bọc toàn bộ nội dung trong 1 khung viền duy nhất
        with st.container(border=True):
            # Phần Tiêu đề + Badge
            st.markdown(
                f'<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">'
                f'<span style="font-size:0.875rem; color:#6B6D76; font-weight:600; text-transform:uppercase;">Funding Rate <span title="Dương cao (>0.05%) = Long quá tải. Âm (<-0.02%) = Short quá tải." style="cursor:help;">❔</span></span>'
                f'<span class="it-signal-badge {_dir_funding_cur}">{_badge_label_map[_dir_funding_cur]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # Phần Con số (dùng HTML thay vì st.metric để giữ form đồng nhất)
            val = f"{funding_current_val:+.4f}%" if funding_current_val is not None else "N/A"
            st.markdown(f'<div style="font-size: 2.25rem; font-weight: 700; color: #111827; line-height: 1.2;">{val}</div>', unsafe_allow_html=True)

    with colb2:
        with st.container(border=True):
            st.markdown(
                f'<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">'
                f'<span style="font-size:0.875rem; color:#6B6D76; font-weight:600; text-transform:uppercase;">Open Interest <span title="% thay đổi so với 10 ngày trước (không phải trong ngày)." style="cursor:help;">❔</span></span>'
                f'<span class="it-signal-badge {_dir_oi}">{_badge_label_map[_dir_oi]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            val = f"{oi_hist_df['sumOpenInterest'].iloc[-1]:,.0f}" if oi_hist_df is not None and not oi_hist_df.empty else "N/A"
            
            # Xử lý mũi tên và màu nền cho phần Delta (giống hệt logic Giá ETH)
            if oi_change_pct_val is not None:
                is_negative = oi_change_pct_val < 0
                delta_color = "#D93025" if is_negative else "#188038"
                delta_bg = "#FCE8E6" if is_negative else "#E6F4EA"
                arrow = "↓" if is_negative else "↑"
                # Dùng abs() để tránh hiện 2 dấu trừ (vd: ↓ -1.95% sẽ thành ↓ 1.95%)
                delta_html = f'<span style="background-color: {delta_bg}; color: {delta_color}; font-size: 0.85rem; font-weight: 600; padding: 2px 8px; border-radius: 4px; white-space: nowrap;">{arrow} {abs(oi_change_pct_val):.2f}%</span>'
                st.markdown(
                    f'<div style="display: flex; align-items: baseline; gap: 8px;">'
                    f'<span style="font-size: 2.25rem; font-weight: 700; color: #111827; line-height: 1.2;">{val}</span>'
                    f'{delta_html}'
                    f'</div>',
                    unsafe_allow_html=True
                )
            else:
                st.markdown(f'<div style="font-size: 2.25rem; font-weight: 700; color: #111827; line-height: 1.2;">{val}</div>', unsafe_allow_html=True)

    with colb3:
        with st.container(border=True):
            st.markdown(
                f'<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">'
                f'<span style="font-size:0.875rem; color:#6B6D76; font-weight:600; text-transform:uppercase;">Long/Short Ratio <span title="Tỷ lệ Top Trader. >1.5: cá voi nghiêng Long. <0.7: cá voi nghiêng Short." style="cursor:help;">❔</span></span>'
                f'<span class="it-signal-badge {_dir_ls}">{_badge_label_map[_dir_ls]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            val = f"{ls_ratio_val:.2f}" if ls_ratio_val is not None else "N/A"
            st.markdown(f'<div style="font-size: 2.25rem; font-weight: 700; color: #111827; line-height: 1.2;">{val}</div>', unsafe_allow_html=True)

    with colb4:
        with st.container(border=True):
            st.markdown(
                f'<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:4px;">'
                f'<span style="font-size:0.875rem; color:#6B6D76; font-weight:600; text-transform:uppercase;">Taker Buy/Sell <span title="Lực mua/bán CHỦ ĐỘNG gần nhất — nhạy hơn Long/Short Ratio." style="cursor:help;">❔</span></span>'
                f'<span class="it-signal-badge {_dir_taker}">{_badge_label_map[_dir_taker]}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            val = f"{taker_ratio_val:.2f}" if taker_ratio_val is not None else "N/A"
            st.markdown(f'<div style="font-size: 2.25rem; font-weight: 700; color: #111827; line-height: 1.2;">{val}</div>', unsafe_allow_html=True)

    # ── Insight riêng cho nhóm Phái Sinh ─────────────────────────────────
    # ── 1. ĐƯA CÁC BIỂU ĐỒ PHÁI SINH LÊN NGAY DƯỚI 4 Ô CHỈ SỐ ──
    st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
    
    # ── Open Interest theo thời gian ────────────────────────────────────
    if oi_hist_df is not None and not oi_hist_df.empty:
        oi_series = oi_hist_df["sumOpenInterestValue"]
        oi_change_10d_pct = (oi_series.iloc[-1] - oi_series.iloc[0]) / oi_series.iloc[0] * 100
        st.markdown(
            f"**Open Interest theo thời gian (10 ngày gần nhất, mẫu mỗi giờ)** — thay đổi: "
            f"**{oi_change_10d_pct:+.2f}%** (đối chiếu với biến động giá ở trên để phát hiện squeeze)"
        )
        y_min, y_max = oi_series.min(), oi_series.max()
        y_pad = (y_max - y_min) * 0.1 or y_max * 0.01  # tránh pad=0 khi OI gần như không đổi
        fig_oi = go.Figure()
        fig_oi.add_trace(go.Scatter(x=oi_hist_df.index, y=oi_series,
                                     line=dict(color=COLORS["accent"], width=1.8), name="OI (USD)"))
        fig_oi.update_layout(template=PLOTLY_TEMPLATE, height=260, yaxis_title="Open Interest (USD)")
        fig_oi.update_yaxes(range=[y_min - y_pad, y_max + y_pad])  # KHÔNG ép về 0 — mới thấy được biến động thật
        st.plotly_chart(fig_oi, use_container_width=True)

    # ── Long/Short Ratio + Taker Ratio ──────────────────────────────────
    colc1, colc2 = st.columns(2)
    with colc1:
        if ls_df is not None and not ls_df.empty:
            st.markdown("**Long/Short Ratio (Top Trader — theo vị thế)**")
            fig_ls = go.Figure()
            fig_ls.add_trace(go.Scatter(x=ls_df.index, y=ls_df["longShortRatio"], line=dict(color=COLORS["accent"])))
            fig_ls.add_hline(y=1, line_dash="dash", line_color="gray")
            fig_ls.update_layout(template=PLOTLY_TEMPLATE, height=260, yaxis_title="Long/Short Ratio")
            st.plotly_chart(fig_ls, use_container_width=True)
    with colc2:
        if taker_df is not None and not taker_df.empty:
            st.markdown("**Taker Buy/Sell Volume Ratio**")
            fig_taker = go.Figure()
            fig_taker.add_trace(go.Scatter(x=taker_df.index, y=taker_df["buySellRatio"], line=dict(color=COLORS["accent"])))
            fig_taker.add_hline(y=1, line_dash="dash", line_color="gray")
            fig_taker.update_layout(template=PLOTLY_TEMPLATE, height=260, yaxis_title="Buy/Sell Ratio")
            st.plotly_chart(fig_taker, use_container_width=True)


    # ── 2. CÁC KHUNG XANH INSIGHT NẰM DƯỚI CÙNG NHÓM PHÁI SINH ──
    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True) 
    
    derivatives_insights = generate_derivatives_insights(
        funding_rate_current=funding_current_val,
        oi_change_pct=oi_change_pct_val,
        price_change_pct_recent=price_change_pct_recent_val,
        long_short_ratio=ls_ratio_val,
        taker_buy_sell_ratio=taker_ratio_val,
        arbitrage=arbitrage_val,
    )
    for ins in derivatives_insights:
        st.info(ins)
    
    st.markdown("<div style='margin-bottom: 25px;'></div>", unsafe_allow_html=True)


    # ── Nến giá thời gian thực TÍCH HỢP CHỈ BÁO (EMA + RSI) ──────
    
    # 1. Đưa thanh trượt lên trước để lấy tham số khung thời gian
    kline_interval = st.select_slider(
        "Khung nến (Tín hiệu kỹ thuật sẽ tự động cập nhật theo khung thời gian này)", 
        options=["1m", "5m", "15m", "1h", "4h", "1d", "1w", "1M"], value="1h",
        help="Lưu ý: Để hiển thị được đường EMA-610, hệ thống cần tải tối thiểu 610 nến lịch sử."
    )
    
    _rsi_badge_val = 50 # Giá trị mặc định phòng khi có lỗi
    dir_trend_dynamic = "neutral"
    
    try:
        # 2. Gọi dữ liệu và tính toán chỉ báo theo khung thời gian tùy chỉnh
        kdf = load_futures_klines(DERIV_SYMBOL, interval=kline_interval, limit=1000)
        kdf["price"] = kdf["close"]
        
        for w in [34, 89, 200, 610]:
            kdf = calculate_ema(kdf, window=w)
        kdf = calculate_rsi(kdf, window=14)
        
        # 3. Đánh giá tín hiệu ĐỘNG theo đúng logic bạn yêu cầu
        latest_kline = kdf.iloc[-1]
        p_now = latest_kline["close"]
        e34, e89, e200, e610 = latest_kline["EMA_34"], latest_kline["EMA_89"], latest_kline["EMA_200"], latest_kline["EMA_610"]
        
        # Bắt lỗi nếu RSI trả về NaN ở những nến đầu tiên
        import pandas as pd
        _rsi_badge_val = latest_kline["RSI_14"] if not pd.isna(latest_kline["RSI_14"]) else 50
        
        # Logic: Giá > 4 EMA + RSI > 50 -> Tích cực | Giá < 4 EMA + RSI < 50 -> Tiêu cực
        if (p_now > e34 and p_now > e89 and p_now > e200 and p_now > e610) and (_rsi_badge_val > 50):
            dir_trend_dynamic = "positive"
        elif (p_now < e34 and p_now < e89 and p_now < e200 and p_now < e610) and (_rsi_badge_val < 50):
            dir_trend_dynamic = "negative"
        else:
            dir_trend_dynamic = "neutral"
            
    except Exception as e:
        kdf = None
        st.caption(f"Không tải được nến giá và chỉ báo: {e}")

    # 4. Hiển thị Tiêu đề Biểu đồ kèm Nhãn (Badge) Động
    st.markdown(
        f'<div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; margin-top:16px;">'
        f'<span style="font-size:1.05rem; font-weight:700;">Biểu đồ Nến {DERIV_SYMBOL} Futures — EMA Ribbon + RSI(14) <span title="Tín hiệu động: Giá > 4 EMA + RSI > 50 = Tích cực. Giá < 4 EMA + RSI < 50 = Tiêu cực." style="cursor:help; font-size:0.9rem;">❔</span></span>'
        f'<span class="it-signal-badge {dir_trend_dynamic}">{_badge_label_map.get(dir_trend_dynamic, "⚪ TRUNG TÍNH")}</span>'
        f'</div>',
        unsafe_allow_html=True
    )

    # 5. Khởi tạo và vẽ Subplots (2 dòng: Nến + EMA ở trên, RSI ở dưới)
    if kdf is not None and not kdf.empty:
        fig_combo = make_subplots(
            rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
            row_heights=[0.75, 0.25],
        )
        
        # Dòng 1: Vẽ Nến
        fig_combo.add_trace(go.Candlestick(
            x=kdf.index, open=kdf["open"], high=kdf["high"], low=kdf["low"], close=kdf["close"],
            name="Nến giá", increasing_line_color=COLORS["buy"], decreasing_line_color=COLORS["sell"]
        ), row=1, col=1)
        
        # Dòng 1: Vẽ EMA Ribbon
        ema_colors = {34: "#FFD700", 89: "#FFA500", 200: "#FF4500", 610: "#8B0000"}
        for w, c in ema_colors.items():
            ema_series = kdf[f"EMA_{w}"].dropna()
            if not ema_series.empty:
                fig_combo.add_trace(go.Scatter(
                    x=ema_series.index, y=ema_series, name=f"EMA-{w}",
                    line=dict(color=c, width=1.3, dash="dot")
                ), row=1, col=1)
                
        # Dòng 2: Vẽ RSI
        if "RSI_14" in kdf.columns:
            rsi_series = kdf["RSI_14"].dropna()
            fig_combo.add_trace(go.Scatter(
                x=rsi_series.index, y=rsi_series, name="RSI-14",
                line=dict(color="#BB86FC")
            ), row=2, col=1)
            fig_combo.add_hline(y=70, line_dash="dash", line_color="red", row=2, col=1)
            fig_combo.add_hline(y=50, line_dash="dot", line_color="gray", row=2, col=1) # Thêm mốc 50
            fig_combo.add_hline(y=30, line_dash="dash", line_color="green", row=2, col=1)
            fig_combo.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)

        # Cấu hình Layout tổng thể
        fig_combo.update_yaxes(title_text="Giá (USDT)", row=1, col=1)
        fig_combo.update_layout(
            template=PLOTLY_TEMPLATE, height=650, 
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", y=-0.1)
        )
        
        st.plotly_chart(fig_combo, use_container_width=True)
        st.caption(f"💡 Chỉ số RSI(14) tại cây nến hiện tại đang là: **{_rsi_badge_val:.2f}**")


    # ── Order Book Depth (tường lệnh) ────────────────────────────────────
    st.markdown(f"**Sổ Lệnh (Order Book Depth) — {DERIV_SYMBOL} Spot**")
    st.caption("Xác định vùng hỗ trợ/kháng cự từ các mức giá có khối lượng đặt lệnh lớn (\"tường lệnh\").")
    try:
        bids, asks = load_order_book(DERIV_SYMBOL, limit=100)
        fig_depth = go.Figure()
        fig_depth.add_trace(go.Bar(x=bids["price"], y=bids["cum_qty"], name="Bids (Mua)", marker_color=COLORS["buy"]))
        fig_depth.add_trace(go.Bar(x=asks["price"], y=asks["cum_qty"], name="Asks (Bán)", marker_color=COLORS["sell"]))
        fig_depth.update_layout(template=PLOTLY_TEMPLATE, height=300, barmode="overlay",
                                 xaxis_title="Giá (USDT)", yaxis_title="Khối lượng lũy kế")
        st.plotly_chart(fig_depth, use_container_width=True)

        top_bid_walls, top_ask_walls = find_order_book_walls(bids, asks, top_n=3)
        colw1, colw2 = st.columns(2)
        with colw1:
            st.caption("Tường Mua (hỗ trợ) lớn nhất:")
            st.dataframe(top_bid_walls, use_container_width=True, hide_index=True)
        with colw2:
            st.caption("Tường Bán (kháng cự) lớn nhất:")
            st.dataframe(top_ask_walls, use_container_width=True, hide_index=True)
    except Exception as e:
        bids, asks = None, None
        st.caption(f"Không tải được Order Book: {e}")


    # ── TÍNH ĐIỂM VÀ ĐƯA RA KẾT LUẬN CHUNG (KỸ THUẬT + PHÁI SINH) ──
    # 1. Đánh giá Xu hướng dựa trên EMA-89 (được lấy từ kdf của biểu đồ nến phía trên)
    if 'kdf' in locals() and kdf is not None and not kdf.empty and "EMA_89" in kdf.columns and not kdf["EMA_89"].isna().all():
        price_latest = kdf["close"].iloc[-1]
        ema89_latest = kdf["EMA_89"].iloc[-1]
        dir_ema = "positive" if price_latest > ema89_latest else "negative"
    else:
        dir_ema = "neutral"

    # 2. Gộp TẤT CẢ 6 chỉ báo vào chung 1 hệ thống tính điểm
    # ── TÍNH ĐIỂM VÀ ĐƯA RA KẾT LUẬN CHUNG (KỸ THUẬT + PHÁI SINH) ──
    # Gộp 5 chỉ báo cốt lõi vào chung 1 hệ thống tính điểm
    _combined_directions = [
        dir_trend_dynamic,            # Đã đổi thành Xu hướng kỹ thuật ĐỘNG theo khung nến đang chọn
        _dir_funding_cur,             # Funding Rate
        _dir_oi,                      # Open Interest Squeeze
        _dir_ls,                      # Long/Short Ratio
        _dir_taker                    # Taker Buy/Sell Volume
    ]
    _combined_verdict = summarize_group(_combined_directions)

    # 3. Tùy chỉnh màu sắc Box Kết Luận
    label_text_combo = _combined_verdict["label"].upper()
    if "MUA" in label_text_combo:
        label_color_combo = "#188038" 
    elif "BÁN" in label_text_combo:
        label_color_combo = "#D93025" 
    else:
        label_color_combo = "#6B6D76" 

    # 4. Vẽ giao diện Box
    with st.container(border=True):
        st.markdown(
            f"📊 <span style='font-size: 1.05rem; font-weight: 600;'>Kết luận nhóm Kỹ Thuật & Phái Sinh (Premium):</span> <span style='font-size: 1.05rem; font-weight: 700; color: {label_color_combo};'>{label_text_combo}</span>", 
            unsafe_allow_html=True
        )
        
        st.markdown(
            f"""
            <div style="font-size: 0.9rem; color: #4B5563; line-height: 1.7; margin-top: 10px;">
                <b>{_combined_verdict['pos']}</b> tích cực &nbsp;·&nbsp; <b>{_combined_verdict['neg']}</b> tiêu cực &nbsp;·&nbsp; <b>{_combined_verdict['neu']}</b> trung tính
                <br><br>
                <b>Quy tắc đưa ra kết luận:</b> (tích cực − tiêu cực) / tổng chỉ báo<br>
                &nbsp;&nbsp;• ≥ 0.6 → Mua mạnh<br>
                &nbsp;&nbsp;• ≥ 0.2 → Mua<br>
                &nbsp;&nbsp;• Trong khoảng (-0.2, 0.2) → Trung lập<br>
                &nbsp;&nbsp;• ≤ -0.2 → Bán<br>
                &nbsp;&nbsp;• ≤ -0.6 → Bán mạnh
                <br><br>
                <i style="color: #6B7280;">Điểm số được tính toán kết hợp từ 6 chỉ báo: RSI(14), Vị thế Giá/EMA-89, Funding Rate, OI Squeeze, Long/Short Ratio và Taker Buy/Sell. Sự đồng thuận giữa Kỹ thuật và Phái sinh mang lại độ tin cậy cao hơn cho quyết định giao dịch.</i>
            </div>
            """, 
            unsafe_allow_html=True
        )

    # ═══════════════════════════════════════════════
    # C. INSIGHT TỔNG HỢP
    # ═══════════════════════════════════════════════
    st.markdown("---")
    render_section_eyebrow("Tổng hợp tự động theo rule-based engine", tier="premium")
    st.subheader("📌 Insight Phân Tích Tổng Hợp")
    
    rsi_latest_val = _rsi_badge_val
    net_issuance_val = latest_issuance_row["net_issuance_daily_eth"] if latest_issuance_row is not None else None
    pct_burned_val = latest_issuance_row["pct_minted_supply_burned"] if latest_issuance_row is not None else None
    whale_count_val = len(whale_df) if whale_df is not None and not whale_df.empty else 0
    staking_netflow_val = staking_flows_df["net_flow_eth"].iloc[-1] if (staking_flows_df is not None and not staking_flows_df.empty) else None
    dev_current_val = dev_df['commits'].iloc[-1] if dev_df is not None and not dev_df.empty else None
    dev_avg_val = dev_df['commits'].mean() if dev_df is not None and not dev_df.empty else None

    insights = generate_insights(
        gas_trend=gas_trend, gas_change_pct=gas_change_pct,
        netflow_latest=netflow_latest,
        whale_count=whale_count_val,
        rsi_latest=rsi_latest_val,
        net_issuance_daily=net_issuance_val,
        pct_minted_burned=pct_burned_val,
        tvl_change_pct=tvl_change_pct,
        whale_threshold_eth=WHALE_THRESHOLD_ETH,
        staking_netflow_latest=staking_netflow_val, 
        dev_current=dev_current_val, 
        dev_avg=dev_avg_val
    )
    
    # ── LOGIC TỰ ĐỘNG PHÂN LOẠI VÀ CHIA 3 CỘT INSIGHT ──
    
    # 1. Khởi tạo 3 mảng chứa insight tương ứng
    positive_insights = []
    neutral_insights = []
    negative_insights = []

    # 2. Phân loại tự động dựa trên Icon nhận diện
    # Đã sửa 'all_insights' thành 'insights' để quét đúng dữ liệu từ hàm trả về
    for ins in insights:
        if "🟢" in ins:
            positive_insights.append(ins)
        elif "🔴" in ins:
            negative_insights.append(ins)
        else:
            # Các icon còn lại (⚪, 🟡, ⚖️, ⛽) sẽ được gom vào nhóm Trung lập / Cảnh báo nhẹ
            neutral_insights.append(ins)

    # 3. Dựng giao diện 3 cột cân xứng
    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
    col_pos, col_neu, col_neg = st.columns(3)

    # Cột 1: TÍCH CỰC
    with col_pos:
        st.markdown("<h4 style='color: #188038;'>🟢 Tích Cực</h4>", unsafe_allow_html=True)
        if positive_insights:
            for ins in positive_insights:
                st.success(ins) # Hiển thị hộp nền xanh lá
        else:
            st.caption("Chưa có tín hiệu nổi bật.")

    # Cột 2: TRUNG LẬP
    with col_neu:
        st.markdown("<h4 style='color: #6B6D76;'>⚪ Trung Lập</h4>", unsafe_allow_html=True)
        if neutral_insights:
            for ins in neutral_insights:
                st.info(ins) # Hiển thị hộp nền xanh nhạt/xám
        else:
            st.caption("Chưa có tín hiệu nổi bật.")

    # Cột 3: TIÊU CỰC
    with col_neg:
        st.markdown("<h4 style='color: #D93025;'>🔴 Tiêu Cực</h4>", unsafe_allow_html=True)
        if negative_insights:
            for ins in negative_insights:
                st.error(ins) # Hiển thị hộp nền đỏ
        else:
            st.caption("Chưa có tín hiệu nổi bật.")
            
    st.markdown("<div style='margin-bottom: 25px;'></div>", unsafe_allow_html=True)


    # ═══════════════════════════════════════════════
    # D. KẾT LUẬN CUỐI CÙNG — MUA / BÁN (gradient bar)
    # ═══════════════════════════════════════════════
    st.markdown("---")
    render_section_eyebrow("Composite Signal", tier="premium")
    st.subheader("🎯 Kết Luận Tổng Hợp")
    st.caption(
        "Điểm số dưới đây tổng hợp tất cả chỉ báo on-chain và kỹ thuật ở trên mang tính tham khảo học thuật "
        "(rule-based, không phải AI/ML), không phải khuyến nghị đầu tư."
    )

    signal_score, signal_label, signal_reasons = generate_final_signal(
        rsi_latest=rsi_latest_val, netflow_latest=netflow_latest, whale_count=whale_count_val,
        net_issuance_daily=net_issuance_val, tvl_change_pct=tvl_change_pct,
        gas_trend=gas_trend, funding_rate_latest=funding_latest,
        oi_change_pct=oi_change_pct_val, price_change_pct_recent=price_change_pct_recent_val,
        long_short_ratio=ls_ratio_val,
    )

    render_signal_strip(signal_score, signal_label)
    st.caption(
        """
        **📐 Phương pháp chuẩn hóa điểm số (Composite Score):**
        
        Hệ thống tính toán dựa trên tổng hợp có trọng số của toàn bộ các chỉ báo phía trên *(RSI, Netflow, Net Issuance, TVL, Funding, OI Squeeze, Long/Short Ratio...)*, sau đó quy đổi về thang điểm từ **-2.0 đến +2.0**.
        
        **Phân bổ tín hiệu giao dịch:**
        * 🟢 **Mua mạnh:** Điểm ≥ +1.2
        * 🟢 **Mua:** Từ +0.4 đến < +1.2
        * ⚪ **Trung lập:** Trong khoảng (-0.4, +0.4)
        * 🔴 **Bán:** Từ > -1.2 đến ≤ -0.4
        * 🔴 **Bán mạnh:** Điểm ≤ -1.2
        """
    )

    with st.expander("Xem chi tiết cách tính điểm"):
        for r in signal_reasons:
            st.markdown(f"- {r}")

else:
    st.markdown("---")
    st.warning("Tính năng Premium (RSI, EMA phân tích chuyên sâu, Whale Monitor, Exchange Netflow, dự báo ARIMA, Insight tổng hợp) yêu cầu tối thiểu 10 IST.")
    if st.button("Mua IST Token ngay"):
        st.code(f"Contract: {CONTRACT_ADDRESS}\nGửi tối thiểu 0.001 Sepolia ETH để nhận IST (tỷ lệ theo TOKEN_PRICE)")


# ════════════════════════════════════════════════════════════
# 🛠️ TỐI ƯU HÓA THỰC THI LỆNH (TRADE EXECUTION)
# ════════════════════════════════════════════════════════════
st.markdown("<br><br>", unsafe_allow_html=True)
st.markdown("---")

st.markdown(
    f'<div style="display:flex; align-items:center; margin-bottom:15px;">'
    f'<h3 style="margin:0; padding:0; color:#111827;">🛠️ Tối ưu hóa thực thi Lệnh (Trade Execution)</h3>'
    f'</div>',
    unsafe_allow_html=True
)
st.caption("Công cụ so sánh phí và trượt giá giữa sàn tập trung (CEX) và phi tập trung (DEX) dựa trên quy mô lệnh.")

fee_comparison_val = None
try:
    dex_info = load_dex_price()
    
    # Bắt lỗi an toàn: Ưu tiên lấy giá từ kdf (biểu đồ nến), nếu không có thì lấy klines_df, cuối cùng là price_df
    if 'kdf' in locals() and kdf is not None and not kdf.empty:
        cex_price_now = kdf["close"].iloc[-1]
    elif 'klines_df' in locals() and klines_df is not None and not klines_df.empty:
        cex_price_now = klines_df["close"].iloc[-1]
    else:
        cex_price_now = price_df["price"].iloc[-1]
        
    arbitrage_val = compute_cex_dex_arbitrage(cex_price_now, dex_info["price_usd"])

    colD1, colD2, colD3 = st.columns(3)
    colD1.metric("Giá Binance (CEX)", f"${arbitrage_val['cex_price']:,.2f}")
    colD2.metric(f"Giá {dex_info['dex_id']} (DEX)", f"${arbitrage_val['dex_price']:,.2f}")
    colD3.metric("Chênh lệch", f"{arbitrage_val['spread_pct']:+.3f}%")
    st.caption(
            f"⚖️ **Phân tích Arbitrage:** {arbitrage_val['direction']} (Chênh lệch giá quá hẹp để giao dịch) · 🔗 [Kiểm tra Pool thanh khoản]({dex_info['pair_url']})"
        )

    st.markdown("**So sánh Phí + Trượt giá theo quy mô lệnh**")
    order_size = st.select_slider("Quy mô lệnh (USD)", options=[100, 500, 1000, 5000, 20000], value=1000)
    
    # Bắt lỗi an toàn cho gas_df
    if 'gas_df' in locals() and gas_df is not None and not gas_df.empty:
        gas_now_gwei = gas_df["baseFee_gwei"].iloc[-1]
    else:
        gas_now_gwei = 15 # Giả định 15 Gwei nếu lỗi API lấy Gas
        
    fee_comparison_val = compute_fee_slippage_comparison(
        order_size_usd=order_size, gas_gwei=gas_now_gwei, eth_price=cex_price_now,
    )
    colF1, colF2 = st.columns(2)
    with colF1:
        st.metric("Phí Binance (taker 0.075%)", f"${fee_comparison_val['binance_fee_usd']:,.2f}")
    with colF2:
        st.metric(
            "Phí Uniswap (0.30% pool + gas)",
            f"${fee_comparison_val['dex_fee_usd']:,.2f}",
            f"gas: ${fee_comparison_val['dex_gas_cost_usd']:,.2f}",
        )
    st.caption(
        f"""
        💡 **Khuyến nghị:** Với quy mô lệnh **${order_size:,}**, giao dịch trên **{fee_comparison_val['cheaper_venue']}** đang tối ưu chi phí hơn.
        
        **📌 Lưu ý quan trọng khi đi lệnh:**
        * **Đặc thù phí Gas (DEX):** Phí mạng lưới là cố định. Do đó, DEX thường chỉ có lợi thế cạnh tranh về phí so với CEX khi quy mô lệnh đủ lớn.
        * **Trượt giá (Slippage):** Chi phí trên chưa bao gồm thiệt hại do trượt giá thực tế. Hãy đối chiếu với **Sổ lệnh (Order Book)** ở phần trên để ước lượng điểm vào lệnh an toàn.
        """
    )
except Exception as e:
    # Đổi thành st.error để nếu có lỗi, nó sẽ báo đỏ rõ ràng trên màn hình cho bạn dễ debug
    st.error(f"Không lấy được dữ liệu so sánh CEX vs DEX. Chi tiết lỗi: {e}")
