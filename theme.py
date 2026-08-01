# theme.py — Hệ thống thiết kế "Terminal Readout" (bản nền trắng, phù hợp học thuật)
#
# Ý tưởng: dashboard này về bản chất là một MÁY ĐO TÍN HIỆU on-chain (funding
# rate, RSI, OI, Long/Short... đều là các dao động), kết thúc bằng 1 gauge
# Mua/Bán. Toàn bộ hệ thống thiết kế xoay quanh ẩn dụ đó — như một tờ báo cáo
# equity research: nền trắng sạch, số liệu dùng font monospace như trên máy đo,
# 1 dải "Signal Strip" chạy xuyên suốt trang phản ánh đúng điểm số hiện tại.
#
# Dùng: from theme import inject_theme, render_signal_strip, render_section_eyebrow, PLOTLY_TEMPLATE
# Gọi inject_theme() 1 lần ngay sau st.set_page_config().

import streamlit as st
import plotly.io as pio
import plotly.graph_objects as go

# ═══════════════════════════════════════════════════════════════
# DESIGN TOKENS
# ═══════════════════════════════════════════════════════════════

COLORS = {
    "bg": "#FFFFFF",
    "surface": "#FAFAFA",
    "surface_alt": "#F2F2F5",
    "border": "#E1E2E6",
    "border_strong": "#C9CBD3",
    "text": "#14151A",
    "text_muted": "#6B6D76",
    "accent": "#4338CA",       # indigo — accent chính, dùng cho Signal Strip / link / highlight
    "accent_soft": "#EEF0FF",  # nền nhạt cho badge/pill dùng accent
    "buy": "#1F9D6C",
    "buy_soft": "#E7F6EF",
    "sell": "#D0353F",
    "sell_soft": "#FCEAEB",
    "premium": "#B45309",      # hổ phách trầm — riêng cho badge "Premium", KHÔNG lẫn với accent chính
    "premium_soft": "#FCF1E4",
}

FONT_DISPLAY = "'Space Grotesk', 'Inter', sans-serif"
FONT_MONO = "'IBM Plex Mono', 'SFMono-Regular', Consolas, monospace"
FONT_BODY = "'Inter', -apple-system, sans-serif"

PLOTLY_TEMPLATE = "insight_light"


def _register_plotly_template():
    tmpl = go.layout.Template()
    tmpl.layout = go.Layout(
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["bg"],
        font=dict(family=FONT_MONO, color=COLORS["text"], size=12),
        title=dict(font=dict(family=FONT_DISPLAY, size=15, color=COLORS["text"])),
        colorway=[COLORS["accent"], COLORS["premium"], COLORS["buy"], COLORS["sell"],
                  "#7C8CF8", "#94969E"],
        xaxis=dict(gridcolor=COLORS["border"], zerolinecolor=COLORS["border_strong"],
                   linecolor=COLORS["border_strong"], tickfont=dict(color=COLORS["text_muted"])),
        yaxis=dict(gridcolor=COLORS["border"], zerolinecolor=COLORS["border_strong"],
                   linecolor=COLORS["border_strong"], tickfont=dict(color=COLORS["text_muted"])),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=COLORS["text_muted"], size=11)),
        margin=dict(t=36, l=8, r=8, b=8),
        hoverlabel=dict(bgcolor=COLORS["text"], font=dict(family=FONT_MONO, color="#FFFFFF")),
    )
    pio.templates["insight_light"] = tmpl


_register_plotly_template()


# ═══════════════════════════════════════════════════════════════
# CSS
# ═══════════════════════════════════════════════════════════════

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"] {{
    font-family: {FONT_BODY};
}}

.stApp {{ background-color: {COLORS["bg"]}; }}

/* ── Tiêu đề: font display có cá tính, không phải sans mặc định ── */
h1, h2, h3 {{
    font-family: {FONT_DISPLAY} !important;
    color: {COLORS["text"]} !important;
    letter-spacing: -0.01em;
}}
h1 {{ font-weight: 700 !important; }}
h2, h3 {{ font-weight: 600 !important; }}

/* ── Divider mảnh, không phải <hr> mặc định thô ── */
hr {{
    border: none !important;
    border-top: 1px solid {COLORS["border"]} !important;
    margin: 1.75rem 0 !important;
}}

/* ── Caption / text phụ ── */
[data-testid="stCaptionContainer"], .stCaption {{
    color: {COLORS["text_muted"]} !important;
    font-family: {FONT_BODY} !important;
}}

/* ── Metric: biến st.metric thành "readout card" — số liệu dùng mono ── */
[data-testid="stMetric"] {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 10px;
    padding: 14px 16px 10px 16px;
}}
[data-testid="stMetricLabel"] {{
    font-family: {FONT_BODY} !important;
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: {COLORS["text_muted"]} !important;
}}
[data-testid="stMetricValue"] {{
    font-family: {FONT_MONO} !important;
    font-weight: 600 !important;
    color: {COLORS["text"]} !important;
}}
[data-testid="stMetricDelta"] {{
    font-family: {FONT_MONO} !important;
}}

/* ── Sidebar: card ví trắng, viền mảnh, tách khỏi nền chính ── */
[data-testid="stSidebar"] {{
    background: {COLORS["surface"]};
    border-right: 1px solid {COLORS["border"]};
}}
[data-testid="stSidebar"] [data-testid="stMetric"] {{
    background: {COLORS["bg"]};
}}

/* ── Alert boxes (st.info / st.warning) — viền trái làm điểm nhấn thay vì khối màu đầy ── */
[data-testid="stAlert"] {{
    background: {COLORS["surface"]} !important;
    border: 1px solid {COLORS["border"]} !important;
    border-left: 3px solid {COLORS["accent"]} !important;
    border-radius: 6px !important;
    color: {COLORS["text"]} !important;
    font-family: {FONT_BODY} !important;
}}

/* ── Button ── */
.stButton > button {{
    font-family: {FONT_BODY} !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
    border: 1px solid {COLORS["border_strong"]} !important;
}}
.stButton > button:hover {{
    border-color: {COLORS["accent"]} !important;
    color: {COLORS["accent"]} !important;
}}

/* ── Dataframe: header rõ ràng, số liệu mono ── */
[data-testid="stDataFrame"] {{
    font-family: {FONT_MONO} !important;
    border: 1px solid {COLORS["border"]} !important;
    border-radius: 8px;
}}

/* ── Radio / select_slider: label rõ, đồng bộ font ── */
[data-testid="stRadio"] label, [data-testid="stWidgetLabel"] {{
    font-family: {FONT_BODY} !important;
    color: {COLORS["text"]} !important;
}}

/* ═══ Custom components (render qua st.markdown unsafe_allow_html) ═══ */

.it-eyebrow {{
    display: flex; align-items: center; gap: 8px;
    font-family: {FONT_BODY}; font-size: 0.75rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em;
    color: {COLORS["text_muted"]};
    margin: 0.2rem 0 0.4rem 0;
}}
.it-eyebrow .dot {{
    width: 7px; height: 7px; border-radius: 50%; display: inline-block;
}}
.it-eyebrow .dot.free {{ background: {COLORS["accent"]}; }}
.it-eyebrow .dot.premium {{ background: {COLORS["premium"]}; }}
.it-eyebrow .badge {{
    font-size: 0.65rem; padding: 1px 7px; border-radius: 4px; font-weight: 700;
}}
.it-eyebrow .badge.free {{ background: {COLORS["accent_soft"]}; color: {COLORS["accent"]}; }}
.it-eyebrow .badge.premium {{ background: {COLORS["premium_soft"]}; color: {COLORS["premium"]}; }}

/* Signal Strip — chữ ký thị giác của toàn bộ dashboard */
.it-signal-strip {{ margin: 0.6rem 0 1.6rem 0; }}
.it-signal-track {{
    position: relative; height: 8px; border-radius: 4px;
    background: linear-gradient(90deg, {COLORS["sell"]} 0%, {COLORS["border_strong"]} 50%, {COLORS["buy"]} 100%);
}}
.it-signal-marker {{
    position: absolute; top: -5px; width: 3px; height: 18px;
    background: {COLORS["text"]}; border-radius: 2px;
    transform: translateX(-50%);
}}
.it-signal-caption {{
    display: flex; justify-content: space-between; align-items: baseline;
    margin-top: 6px; font-family: {FONT_MONO}; font-size: 0.72rem;
    color: {COLORS["text_muted"]}; text-transform: uppercase; letter-spacing: 0.04em;
}}
.it-signal-caption .current {{
    font-family: {FONT_DISPLAY}; font-size: 0.85rem; font-weight: 700;
    text-transform: none; letter-spacing: 0; color: {COLORS["text"]};
}}

/* Wallet card (sidebar) */
.it-wallet-card {{
    border: 1px solid {COLORS["border"]}; border-radius: 10px;
    padding: 14px; background: {COLORS["bg"]}; margin-bottom: 12px;
}}
.it-wallet-tier {{
    display: inline-block; font-family: {FONT_BODY}; font-size: 0.68rem; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.06em; padding: 2px 8px; border-radius: 4px;
    margin-bottom: 8px;
}}
.it-wallet-tier.free {{ background: {COLORS["accent_soft"]}; color: {COLORS["accent"]}; }}
.it-wallet-tier.premium {{ background: {COLORS["premium_soft"]}; color: {COLORS["premium"]}; }}
.it-wallet-balance {{
    font-family: {FONT_MONO}; font-size: 1.5rem; font-weight: 600; color: {COLORS["text"]};
}}
.it-wallet-address {{
    font-family: {FONT_MONO}; font-size: 0.78rem; color: {COLORS["text_muted"]}; margin-top: 2px;
}}
/* Badge Tích cực/Tiêu cực/Trung tính — đặt ở hàng tiêu đề mỗi chỉ báo */
.it-signal-badge {{
    display: inline-block; font-family: {FONT_BODY}; font-size: 0.72rem; font-weight: 700;
    padding: 3px 10px; border-radius: 999px; white-space: nowrap;
}}
.it-signal-badge.positive {{ background: {COLORS["buy_soft"]}; color: {COLORS["buy"]}; }}
.it-signal-badge.negative {{ background: {COLORS["sell_soft"]}; color: {COLORS["sell"]}; }}
.it-signal-badge.neutral {{ background: {COLORS["surface_alt"]}; color: {COLORS["text_muted"]}; }}

/* Box kết luận nhóm (cuối mỗi section) */
.it-group-verdict {{
    border: 1px solid {COLORS["border"]}; border-radius: 10px; padding: 14px 16px;
    background: {COLORS["surface"]}; margin: 0.8rem 0;
}}
.it-group-verdict .verdict-title {{
    font-family: {FONT_DISPLAY}; font-weight: 700; font-size: 1rem; margin-bottom: 4px;
}}
.it-group-verdict .verdict-detail {{
    font-family: {FONT_MONO}; font-size: 0.8rem; color: {COLORS["text_muted"]};
}}
</style>
"""


def inject_theme():
    st.markdown(_CSS, unsafe_allow_html=True)


def render_signal_strip(score, label, min_score=-2, max_score=2):
    """
    Dải tín hiệu ngang — chữ ký thị giác của dashboard. score nằm trong
    [min_score, max_score] (mặc định -2..+2, khớp thang generate_final_signal).
    """
    pct = (score - min_score) / (max_score - min_score) * 100
    pct = max(2, min(98, pct))  # tránh marker tràn ra ngoài track
    html = f"""
    <div class="it-signal-strip">
      <div class="it-signal-track">
        <div class="it-signal-marker" style="left:{pct:.1f}%;"></div>
      </div>
      <div class="it-signal-caption">
        <span>Bán mạnh</span>
        <span class="current">{label} · {score:+.2f}</span>
        <span>Mua mạnh</span>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_section_eyebrow(text, tier="free"):
    """
    Nhãn nhỏ phía trên mỗi section, mã hoá đúng 1 thông tin thật: section này
    thuộc Free hay Premium Tier (không phải số thứ tự trang trí).
    """
    badge_text = "Premium" if tier == "premium" else "Free"
    html = f"""
    <div class="it-eyebrow">
      <span class="dot {tier}"></span>
      <span>{text}</span>
      <span class="badge {tier}">{badge_text}</span>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_wallet_card(tier, balance, wallet_address):
    display_wallet = f"{wallet_address[:6]}...{wallet_address[-4:]}" if len(wallet_address) > 10 else wallet_address
    tier_label = "Premium" if tier == "premium" else "Free"
    html = f"""
    <div class="it-wallet-card">
      <span class="it-wallet-tier {tier}">{tier_label} Tier</span>
      <div class="it-wallet-balance">{balance:.2f} <span style="font-size:0.9rem; color:{COLORS['text_muted']};">IST</span></div>
      <div class="it-wallet-address">{display_wallet}</div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


_BADGE_LABEL = {"positive": "🟢 TÍCH CỰC", "negative": "🔴 TIÊU CỰC", "neutral": "⚪ TRUNG TÍNH"}


def render_metric_header(title, direction, badge_help=None):
    """
    Tiêu đề 1 chỉ báo kèm badge Tích cực/Tiêu cực/Trung tính SÁT BÊN PHẢI
    cùng hàng. direction: 'positive' | 'negative' | 'neutral'.
    """
    direction = direction if direction in _BADGE_LABEL else "neutral"
    col_title, col_badge = st.columns([5, 1.4])
    with col_title:
        st.markdown(f"**{title}**")
    with col_badge:
        help_attr = f' title="{badge_help}"' if badge_help else ""
        st.markdown(
            f'<div style="text-align:right; padding-top:2px;">'
            f'<span class="it-signal-badge {direction}"{help_attr}>{_BADGE_LABEL[direction]}</span></div>',
            unsafe_allow_html=True,
        )


def render_group_verdict(group_name, verdict_label, pos, neg, neu, rule_text):
    """
    Box kết luận cuối mỗi nhóm chỉ báo — hiển thị verdict (MUA MẠNH/MUA/
    TRUNG LẬP/BÁN/BÁN MẠNH) kèm số liệu tích cực/tiêu cực/trung tính và
    quy tắc phân loại đã dùng (để người đọc tự kiểm chứng, không phải hộp đen).
    """
    verdict_color = {
        "MUA MẠNH": COLORS["buy"], "MUA": COLORS["buy"],
        "TRUNG LẬP": COLORS["text_muted"],
        "BÁN": COLORS["sell"], "BÁN MẠNH": COLORS["sell"],
    }.get(verdict_label, COLORS["text_muted"])
    html = f"""
    <div class="it-group-verdict">
      <div class="verdict-title">📊 Kết luận nhóm {group_name}: <span style="color:{verdict_color};">{verdict_label}</span></div>
      <div class="verdict-detail">{pos} tích cực · {neg} tiêu cực · {neu} trung tính — {rule_text}</div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
