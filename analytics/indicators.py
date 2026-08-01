import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression


# ═══════════════════════════════════════════════════════════════
# HỆ THỐNG PHÂN LOẠI TÍCH CỰC / TIÊU CỰC / TRUNG TÍNH
# Dùng CHUNG cho mọi chỉ báo có badge — đảm bảo ngưỡng nhất quán với phần
# insight text và generate_final_signal (không phải 2 bộ quy tắc khác nhau).
# ═══════════════════════════════════════════════════════════════

def classify_rsi(rsi):
    if rsi is None:
        return "neutral"
    if rsi >= 70:
        return "negative"  # quá mua → rủi ro điều chỉnh
    if rsi <= 30:
        return "positive"  # quá bán → cơ hội hồi phục
    return "neutral"


def classify_netflow(netflow_eth):
    if netflow_eth is None:
        return "neutral"
    if netflow_eth > 0:
        return "negative"  # tiền vào sàn → áp lực bán
    if netflow_eth < 0:
        return "positive"  # tiền rút khỏi sàn → tích lũy dài hạn
    return "neutral"


def classify_funding_rate(funding_pct):
    if funding_pct is None:
        return "neutral"
    if funding_pct > 0.05:
        return "negative"  # Long quá tải → rủi ro long squeeze
    if funding_pct < -0.02:
        return "positive"  # Short quá tải → khả năng short squeeze
    return "neutral"


def classify_tvl_change(tvl_change_pct):
    if tvl_change_pct is None:
        return "neutral"
    if tvl_change_pct > 2:
        return "positive"
    if tvl_change_pct < -2:
        return "negative"
    return "neutral"


def classify_net_issuance(pct_minted_supply_burned):
    """
    QUAN TRỌNG: bản đầu tiên dùng ngưỡng ETH TUYỆT ĐỐI (net_issuance <
    -50 / > +50 ETH/ngày) — nhưng với issuance hiện tại ~2,700 ETH/ngày
    (từ ~1 triệu+ validator) và burn thực tế quan sát được chỉ ~7-165
    ETH/ngày (do phần lớn hoạt động đã chuyển sang Layer 2 sau EIP-4844),
    net issuance LUÔN lớn hơn +50 — khiến hàm này CHỈ BAO GIỜ trả về
    "negative", không có ý nghĩa phân loại thật (đã phát hiện khi rà lại
    dữ liệu backfill thật). Sửa bằng cách chuyển sang ngưỡng theo TỶ LỆ
    % burn/issuance (pct_minted_supply_burned, đã có sẵn trong dữ liệu)
    — không phụ thuộc quy mô mạng lưới, vẫn đúng dù issuance/burn tuyệt
    đối thay đổi ra sao trong tương lai:
        >= 100%  → burn ĐÃ vượt issuance ngày đó → thực sự giảm phát → positive
        < 50%    → burn chưa bằng nổi 1 nửa issuance → lạm phát rõ rệt → negative
        50-100%  → trung tính
    """
    if pct_minted_supply_burned is None:
        return "neutral"
    if pct_minted_supply_burned >= 100:
        return "positive"
    if pct_minted_supply_burned < 50:
        return "negative"
    return "neutral"


def classify_oi_price_squeeze(oi_change_pct, price_change_pct):
    if oi_change_pct is None or price_change_pct is None:
        return "neutral"
    oi_up = oi_change_pct > 5
    price_up = price_change_pct > 3
    price_flat = abs(price_change_pct) <= 3
    if oi_up and price_up:
        return "positive"
    if not oi_up and price_up:
        return "neutral"  # tăng giá thiếu dòng tiền OI xác nhận — không hẳn tiêu cực, chỉ kém bền
    if oi_up and not price_up and not price_flat:
        return "negative"  # OI tăng nhưng giá giảm → dòng tiền Short mới
    return "neutral"  # bao gồm cả tình huống "sắp squeeze" — hướng chưa rõ


def classify_long_short_ratio(ratio):
    if ratio is None:
        return "neutral"
    if ratio >= 1.5:
        return "negative"  # đám đông Long quá tải
    if ratio <= 0.7:
        return "positive"  # đám đông Short quá tải
    return "neutral"


def classify_taker_ratio(ratio):
    if ratio is None:
        return "neutral"
    if ratio >= 1.2:
        return "positive"
    if ratio <= 0.85:
        return "negative"
    return "neutral"


def classify_price_momentum(change_pct):
    if change_pct is None:
        return "neutral"
    if change_pct > 0:
        return "positive"
    if change_pct < 0:
        return "negative"
    return "neutral"

def classify_dev_activity(current_commits, avg_commits):
    if current_commits is None or avg_commits is None or avg_commits == 0:
        return "neutral"
    # Lệch 20% so với trung bình năm
    if current_commits >= avg_commits * 1.2:
        return "positive"
    if current_commits <= avg_commits * 0.8:
        return "negative"
    return "neutral"

def summarize_group(directions):
    """
    Tổng hợp danh sách 'positive'/'negative'/'neutral' của 1 nhóm chỉ báo
    thành 1 kết luận MUA MẠNH/MUA/TRUNG LẬP/BÁN/BÁN MẠNH.

    QUY TẮC (áp dụng chung, không phụ thuộc số lượng chỉ báo trong nhóm —
    để nhóm nào cũng dùng được dù có 2 hay 10 chỉ báo):
        ratio = (số tích cực − số tiêu cực) / tổng số chỉ báo trong nhóm
        ratio ≥ +0.6            → MUA MẠNH   (vd: 5 tích cực, 0 tiêu cực / 5 chỉ báo = 1.0)
        +0.2 ≤ ratio < +0.6      → MUA
        −0.2 < ratio < +0.2      → TRUNG LẬP
        −0.6 < ratio ≤ −0.2      → BÁN
        ratio ≤ −0.6             → BÁN MẠNH
    Trung tính không cộng/trừ vào tử số nhưng vẫn tính vào mẫu số (làm
    "loãng" kết luận về TRUNG LẬP nếu phần lớn chỉ báo không rõ hướng).
    """
    n = len(directions)
    if n == 0:
        return {"label": "TRUNG LẬP", "pos": 0, "neg": 0, "neu": 0, "ratio": 0.0}
    pos = directions.count("positive")
    neg = directions.count("negative")
    neu = n - pos - neg
    ratio = (pos - neg) / n
    if ratio >= 0.6:
        label = "MUA MẠNH"
    elif ratio >= 0.2:
        label = "MUA"
    elif ratio > -0.2:
        label = "TRUNG LẬP"
    elif ratio > -0.6:
        label = "BÁN"
    else:
        label = "BÁN MẠNH"
    return {"label": label, "pos": pos, "neg": neg, "neu": neu, "ratio": ratio}


GROUP_VERDICT_RULE_TEXT = (
    "quy tắc: tỷ lệ (tích cực−tiêu cực)/tổng chỉ báo ≥0.6→Mua mạnh, ≥0.2→Mua, "
    "trong khoảng (-0.2, 0.2)→Trung lập, ≤-0.2→Bán, ≤-0.6→Bán mạnh"
)


def calculate_sma(df, price_column="price", window=7):
    """
    SMA (Simple Moving Average) = trung bình giá của N ngày gần nhất.
    Dùng để làm 'mượt' biến động giá, dễ nhìn xu hướng hơn giá thô.
    window=7 nghĩa là trung bình 7 ngày gần nhất (đổi thành 20, 50... tùy nhu cầu).
    """
    result = df.copy()
    result[f"SMA_{window}"] = result[price_column].rolling(window=window).mean()
    return result


def calculate_rsi(df, price_column="price", window=14):
    """
    RSI (Relative Strength Index) = chỉ số đo tốc độ và mức độ thay đổi giá,
    giá trị từ 0-100.
    - RSI > 70: thường được coi là 'quá mua' (overbought) — giá có thể sắp giảm
    - RSI < 30: thường được coi là 'quá bán' (oversold) — giá có thể sắp tăng
    Đây là lý do RSI chỉ mở cho tier Premium — nó cần diễn giải kỹ hơn SMA.
    """
    result = df.copy()
    delta = result[price_column].diff()  # chênh lệch giá so với ngày trước

    gain = delta.where(delta > 0, 0)   # chỉ giữ phần tăng giá
    loss = -delta.where(delta < 0, 0)  # chỉ giữ phần giảm giá (đổi thành số dương)

    avg_gain = gain.rolling(window=window).mean()
    avg_loss = loss.rolling(window=window).mean()

    rs = avg_gain / avg_loss
    result[f"RSI_{window}"] = 100 - (100 / (1 + rs))
    return result


def forecast_gas_trend(gas_df, gas_column="baseFee_gwei", periods_ahead=10):
    """
    Dùng Linear Regression (hồi quy tuyến tính) để dự báo xu hướng phí gas
    trong `periods_ahead` block tiếp theo.
    Đây KHÔNG phải dự báo chính xác tuyệt đối — chỉ là ước lượng xu hướng
    (đang tăng hay giảm) dựa trên dữ liệu gần đây, phù hợp cho mục đích minh họa
    trong đồ án, không dùng để giao dịch thật.
    """
    df = gas_df.reset_index()  # đưa 'block' từ index thành cột bình thường
    df["step"] = range(len(df))  # đánh số thứ tự 0,1,2... để làm biến X cho model

    X = df[["step"]].values
    y = df[gas_column].values

    model = LinearRegression()
    model.fit(X, y)

    future_steps = np.array(
        [[len(df) + i] for i in range(periods_ahead)]
    )
    forecast_values = model.predict(future_steps)

    forecast_df = pd.DataFrame({
        "step": future_steps.flatten(),
        f"{gas_column}_forecast": forecast_values,
    })

    slope = model.coef_[0]
    trend = "TĂNG" if slope > 0 else "GIẢM"

    return forecast_df, trend


def calculate_ema(df, price_column="price", window=200):
    """
    EMA (Exponential Moving Average) — khác SMA ở chỗ EMA đặt trọng số cao hơn
    cho giá GẦN ĐÂY, phản ứng nhanh hơn với biến động mới so với SMA (vốn coi
    mọi ngày trong window quan trọng như nhau).
    Dùng chu kỳ Fibonacci (34, 89, 200, 610) — một dải EMA phổ biến trong
    phân tích kỹ thuật crypto, gọi là "Fibonacci EMA Ribbon":
    - EMA-34/89: xu hướng ngắn-trung hạn
    - EMA-200: ngưỡng phân định thị trường "bull/bear" kinh điển
    - EMA-610: xu hướng rất dài hạn
    """
    result = df.copy()
    result[f"EMA_{window}"] = result[price_column].ewm(span=window, adjust=False).mean()
    return result


def forecast_gas_arima(gas_df, gas_column="baseFee_gwei", periods_ahead=20, order=(2, 1, 2)):
    """
    Dự báo xu hướng gas fee bằng mô hình ARIMA(p,d,q) thay cho Linear Regression.

    TẠI SAO ARIMA THAY VÌ LINEAR REGRESSION?
    - Linear Regression giả định giá trị tương lai = hàm TUYẾN TÍNH của thời gian
      (luôn tăng hoặc luôn giảm với tốc độ cố định) — không đúng với gas fee,
      vốn dao động QUANH một mức trung bình (mean-reverting), không có xu
      hướng dài hạn thật sự.
    - ARIMA xử lý đúng bản chất này qua 3 thành phần:
        AR (p=2): giá trị hiện tại phụ thuộc 2 giá trị gần nhất trước đó
        I  (d=1): lấy sai phân bậc 1 để loại bỏ xu hướng, chỉ giữ lại
                  phần dao động (giúp mô hình ổn định - stationary)
        MA (q=2): điều chỉnh theo sai số dự báo của 2 bước trước
    - Đây là mô hình chuẩn trong giáo trình time-series (Box-Jenkins),
      đủ đơn giản để giải thích trong đồ án, không cần tới Deep Learning
      (LSTM) vốn đòi hỏi lượng dữ liệu lớn hơn nhiều để tránh overfitting.

    Trả về: forecast_df (giá trị dự báo + khoảng tin cậy 95%), trend (TĂNG/GIẢM)
    """
    from statsmodels.tsa.arima.model import ARIMA

    series = gas_df[gas_column].reset_index(drop=True)

    model = ARIMA(series, order=order)
    fitted = model.fit()

    forecast_result = fitted.get_forecast(steps=periods_ahead)
    forecast_values = forecast_result.predicted_mean
    conf_int = forecast_result.conf_int(alpha=0.05)  # khoảng tin cậy 95%

    forecast_df = pd.DataFrame({
        "step": range(len(series), len(series) + periods_ahead),
        f"{gas_column}_forecast": forecast_values.values,
        "lower_95": conf_int.iloc[:, 0].values,
        "upper_95": conf_int.iloc[:, 1].values,
    })

    last_actual = series.iloc[-1]
    last_forecast = forecast_values.iloc[-1]
    change_pct = (last_forecast - last_actual) / last_actual * 100
    trend = "TĂNG" if change_pct > 1 else ("GIẢM" if change_pct < -1 else "ỔN ĐỊNH")

    return forecast_df, trend, change_pct


def generate_insights(gas_trend, gas_change_pct, netflow_latest=None, whale_count=0,
                       rsi_latest=None, net_issuance_daily=None, pct_minted_burned=None,
                       tvl_change_pct=None, whale_threshold_eth=1000, 
                       staking_netflow_latest=None, dev_current=None, dev_avg=None):
    """
    insight phân tích dạng văn bản dựa trên rule-based logic (if-else).
    Đã được gắn thêm Icon (🟢, 🔴, ⚪) ở đầu mỗi câu để hệ thống UI tự động phân loại cột.
    """
    insights = []

    # ── 1. Gas fee (dự báo ARIMA) ──────────────────────────────────
    if gas_trend == "TĂNG":
        insights.append(
            f"⚪ **Phí Gas — xu hướng TĂNG (dự báo +{abs(gas_change_pct):.1f}%).** Mô hình ARIMA đang dự báo "
            f"phí gas trung bình sẽ tăng khoảng {abs(gas_change_pct):.1f}% trong khoảng thời gian tới. "
            "Về bản chất, gas fee tăng phản ánh nhu cầu sử dụng block space đang lớn hơn nguồn cung, "
            "thường xảy ra khi có làn sóng giao dịch tăng đột biến. Với trader ngắn hạn, gas tăng thường là "
            "dấu hiệu gián tiếp cho thấy thị trường đang 'nóng' lên và đáng theo dõi."
        )
    elif gas_trend == "GIẢM":
        insights.append(
            f"⚪ **Phí Gas — xu hướng GIẢM (dự báo {gas_change_pct:.1f}%).** Mạng lưới đang có dấu hiệu bớt "
            "tắc nghẽn. Đây thường là giai đoạn thị trường tương đối trầm lắng. Đối với người dùng, đây là "
            "THỜI ĐIỂM THUẬN LỢI để thực hiện các giao dịch tốn nhiều gas vì chi phí sẽ thấp hơn đáng kể."
        )
    else:
        insights.append(
            "⚪ **Phí Gas — đang ỔN ĐỊNH.** Mô hình dự báo không cho thấy biến động đáng kể nào trong ngắn "
            "hạn, nghĩa là nhu cầu sử dụng mạng lưới hiện khá cân bằng, không có tín hiệu bất thường."
        )

    # ── 2. Exchange Netflow ─────────────────────────────────────────
    if netflow_latest is not None:
        if netflow_latest > 0:
            insights.append(
                f"🔴 **Exchange Netflow — DƯƠNG (+{netflow_latest:,.0f} ETH trong ngày gần nhất).** Tổng ETH "
                "chuyển VÀO sàn nhiều hơn lượng chuyển RA. Đây thường được xem là tín hiệu tiêu cực "
                "trong ngắn hạn: nhà đầu tư chuyển ETH vào sàn có thể để bán hoặc chốt lời, tạo áp lực bán tiềm ẩn."
            )
        elif netflow_latest < 0:
            insights.append(
                f"🟢 **Exchange Netflow — ÂM ({netflow_latest:,.0f} ETH trong ngày gần nhất).** Tổng ETH được "
                "RÚT RA khỏi sàn nhiều hơn lượng chuyển vào. Đây thường là tín hiệu tích cực dài hạn: nhà đầu tư "
                "rút tiền về ví cá nhân để nắm giữ, làm giảm nguồn cung lưu thông và hỗ trợ giá."
            )
        else:
            insights.append(
                "⚪ **Exchange Netflow — gần như không đổi.** Dòng tiền vào và ra khỏi các ví sàn "
                "đang cân bằng nhau, không có tín hiệu rõ ràng về áp lực mua hay bán."
            )

    # ── 3. Whale transfer ───────────────────────────────────────────
    if whale_count > 0:
        insights.append(
            f"⚪ **Whale Transfer Monitor — phát hiện {whale_count} giao dịch lớn (≥{whale_threshold_eth:,} ETH).** "
            "Giao dịch cá voi thường là dấu hiệu sớm của biến động lớn. Đây là tín hiệu ĐÁNG CHÚ Ý THEO DÕI THÊM, "
            "đặc biệt nếu kết hợp cùng chiều với Exchange Netflow."
        )
    else:
        insights.append(
            f"⚪ **Whale Transfer Monitor — không phát hiện giao dịch ≥{whale_threshold_eth:,} ETH nào.** "
            "Đây là điều bình thường, chỉ đơn giản là chưa có giao dịch nào đủ lớn để vượt ngưỡng theo dõi."
        )

    # ── 4. RSI (Đã đồng bộ với logic RSI > 50 / RSI < 50 của Chart) ──
    if rsi_latest is not None:
        if rsi_latest >= 70:
            insights.append(
                f"🔴 **RSI(14) — {rsi_latest:.1f}: Vùng QUÁ MUA (Overbought).** Dù động lượng đang tích cực (RSI > 50), "
                "nhưng việc vượt mốc 70 cho thấy giá ETH đã tăng quá nóng trong ngắn hạn, tiềm ẩn rủi ro điều chỉnh/chốt lời. "
                "Nên thận trọng với các vị thế mua mới."
            )
        elif rsi_latest <= 30:
            insights.append(
                f"🟢 **RSI(14) — {rsi_latest:.1f}: Vùng QUÁ BÁN (Oversold).** Dù xu hướng đang tiêu cực (RSI < 50), "
                "nhưng việc giảm dưới 30 cho thấy lực bán đã cạn kiệt, rất dễ xuất hiện nhịp hồi phục kỹ thuật."
            )
        elif rsi_latest > 50:
            insights.append(
                f"🟢 **RSI(14) — {rsi_latest:.1f}: Động lượng TÍCH CỰC.** RSI duy trì trên mốc 50 cho thấy phe Mua "
                "đang kiểm soát thị trường, đồng thuận với xu hướng tăng ngắn hạn."
            )
        else:
            insights.append(
                f"🔴 **RSI(14) — {rsi_latest:.1f}: Động lượng TIÊU CỰC.** RSI nằm dưới mốc 50 cho thấy phe Bán "
                "đang áp đảo. Động lượng giá đang yếu đi."
            )

    # ── 5. Net Issuance / Burn (Đã đồng bộ ngưỡng 100% và 50%) ───────
    if net_issuance_daily is not None:
        if net_issuance_daily > 0:
            insights.append(
                f"🔴 **Net Issuance — DƯƠNG (+{net_issuance_daily:,.0f} ETH/ngày, lạm phát nhẹ).** Lượng ETH "
                "phát hành mới hiện đang NHIỀU HƠN lượng ETH bị đốt. Việc nguồn cung lưu thông tăng lên được hệ thống "
                "đánh giá là tín hiệu nghiêng về tiêu cực (áp lực bán nhẹ)."
            )
        else:
            insights.append(
                f"🟢 **Net Issuance — ÂM ({net_issuance_daily:,.0f} ETH/ngày, giảm phát).** Lượng ETH bị đốt "
                "hiện đang NHIỀU HƠN lượng phát hành mới. Nguồn cung giảm là tín hiệu Tích cực hỗ trợ giá dài hạn."
            )

    if pct_minted_burned is not None:
        if pct_minted_burned >= 100:
            insights.append(
                f"🟢 **Tỷ lệ Burn/Issuance — {pct_minted_burned:.1f}%.** Cứ 100 ETH sinh ra thì có tới {pct_minted_burned:.1f} ETH "
                "bị đốt. Tỷ lệ > 100% xác nhận mạng lưới đang trong trạng thái giảm phát ròng mạnh mẽ."
            )
        elif pct_minted_burned < 50:
            insights.append(
                f"🔴 **Tỷ lệ Burn/Issuance — {pct_minted_burned:.1f}%.** Tỷ lệ đốt dưới 50% cho thấy mạng lưới đang "
                "thiếu vắng giao dịch trầm trọng, không đủ để tạo ra lực cầu đốt phí gas."
            )
        else:
            insights.append(
                f"⚪ **Tỷ lệ Burn/Issuance — {pct_minted_burned:.1f}%.** Mạng lưới duy trì tỷ lệ đốt ở mức trung bình, "
                "cung-cầu tương đối cân bằng."
            )

    # ── 6. TVL DeFi ──────────────────────────────────────────────────
    if tvl_change_pct is not None:
        if tvl_change_pct > 2:
            insights.append(
                f"🟢 **TVL DeFi — tăng {tvl_change_pct:+.2f}% (30 ngày).** Dòng vốn mới đang chảy vào hệ "
                "sinh thái. Đây là dấu hiệu tâm lý thị trường đang tích cực ('risk-on')."
            )
        elif tvl_change_pct < -2:
            insights.append(
                f"🔴 **TVL DeFi — giảm {tvl_change_pct:.2f}% (30 ngày).** Vốn đang có xu hướng rút "
                "ra khỏi DeFi, phản ánh tâm lý thận trọng/né rủi ro ('risk-off') của nhà đầu tư."
            )
        else:
            insights.append(
                "⚪ **TVL DeFi — tương đối ổn định** trong 30 ngày gần nhất, không có dòng vốn vào/ra bất thường."
            )
            
    # ── 7. Staking Flows ─────────────────────────────────────────────
    if staking_netflow_latest is not None:
        if staking_netflow_latest > 0:
            insights.append(
                f"🟢 **Staking Flows — NẠP RÒNG (+{staking_netflow_latest:,.0f} ETH).** Lượng ETH nạp vào mạng lưới "
                "đang nhiều hơn lượng rút ra. Việc khóa cung này cho thấy cam kết dài hạn và là tín hiệu Hỗ trợ giá mạnh mẽ."
            )
        else:
            insights.append(
                f"🔴 **Staking Flows — RÚT RÒNG ({staking_netflow_latest:,.0f} ETH).** Lượng ETH rút khỏi mạng lưới "
                "đang áp đảo. Việc giải phóng thanh khoản này có thể tạo ra áp lực bán chốt lời trong ngắn hạn."
            )

    # ── 8. Developer Activity ─────────────────────────────────────────
    if dev_current is not None and dev_avg is not None and dev_avg > 0:
        if dev_current >= dev_avg * 1.2:
            insights.append(
                f"🟢 **Developer Activity — TĂNG MẠNH ({dev_current:.0f} commits/tuần).** Hoạt động đẩy code vượt >20% "
                f"so với trung bình năm ({dev_avg:.0f}). Nền tảng công nghệ đang được phát triển rất sôi động."
            )
        elif dev_current <= dev_avg * 0.8:
            insights.append(
                f"🔴 **Developer Activity — SUY GIẢM ({dev_current:.0f} commits/tuần).** Hoạt động lập trình giảm >20% "
                f"so với trung bình năm ({dev_avg:.0f}). Cần theo dõi thêm nếu sự sụt giảm này kéo dài."
            )
        else:
            insights.append(
                f"⚪ **Developer Activity — ỔN ĐỊNH ({dev_current:.0f} commits/tuần).** Mức độ phát triển mã nguồn "
                "duy trì ở nhịp độ bình thường, không có biến động bất thường."
            )

    return insights

      


def generate_derivatives_insights(funding_rate_current=None, oi_change_pct=None,
                                   price_change_pct_recent=None, long_short_ratio=None,
                                   taker_buy_sell_ratio=None, arbitrage=None):
    """
    Insight rule-based cho nhóm chỉ báo Phái Sinh & Tâm Lý Thị Trường (Binance).
    Cùng phong cách với generate_insights() ở trên: mỗi insight giải thích Ý
    NGHĨA của chỉ số, KHÔNG chỉ liệt kê con số khô khan.
    """
    insights = []

    # ── 1. Funding Rate hiện tại ────────────────────────────────────
    if funding_rate_current is not None:
        if funding_rate_current > 0.05:
            insights.append(
                f"**Funding Rate hiện tại — {funding_rate_current:+.4f}%: phe Long đang HƯNG PHẤN quá "
                "mức.** Khi funding vượt ngưỡng 0.05%/8 giờ, phe Long đang phải trả phí đáng kể cho phe "
                "Short chỉ để giữ vị thế — dấu hiệu thị trường đang dùng đòn bẩy Long quá tải. Đây là bối "
                "cảnh kinh điển dẫn tới hiện tượng \"long squeeze\": chỉ cần một nhịp giảm giá nhỏ cũng đủ "
                "kích hoạt thanh lý dây chuyền, khiến giá giảm nhanh và mạnh hơn bình thường. Nên thận "
                "trọng khi mở thêm vị thế Long mới ở vùng này."
            )
        elif funding_rate_current < -0.02:
            insights.append(
                f"**Funding Rate hiện tại — {funding_rate_current:+.4f}%: phe Short đang chiếm ưu thế.** "
                "Funding âm nghĩa là Short đang trả phí cho Long — thị trường đang nghiêng về tâm lý bi "
                "quan/sợ hãi. Nếu diễn ra kéo dài kèm giá không giảm tương ứng, đây có thể là dấu hiệu "
                "sắp có một nhịp hồi ngắn hạn do short bị ép đóng vị thế (short squeeze)."
            )
        else:
            insights.append(
                f"**Funding Rate hiện tại — {funding_rate_current:+.4f}%: trung tính.** Không có phe nào "
                "dùng đòn bẩy quá tải rõ rệt, thị trường phái sinh đang ở trạng thái cân bằng tương đối."
            )

    # ── 2. Open Interest kết hợp với biến động giá ───────────────────
    if oi_change_pct is not None and price_change_pct_recent is not None:
        oi_up = oi_change_pct > 5  # ngưỡng cho khung 10 ngày (khác khung ngắn hạn trước đây)
        price_up = price_change_pct_recent > 3  # ngưỡng cho khung 10 ngày
        price_flat = abs(price_change_pct_recent) <= 3

        if oi_up and price_up:
            insights.append(
                f"**Open Interest tăng ({oi_change_pct:+.2f}%) CÙNG CHIỀU với giá ({price_change_pct_recent:+.2f}%) "
                "— xu hướng TĂNG có dòng tiền mới xác nhận.** Dòng vốn mới đang chảy vào thị trường phái "
                "sinh để đặt cược theo xu hướng hiện tại, không phải chỉ có vị thế cũ đẩy giá — đây là tổ "
                "hợp thường được xem là xu hướng tăng \"khỏe\" và có khả năng bền vững hơn."
            )
        elif oi_up and price_flat:
            insights.append(
                f"**Open Interest tăng mạnh ({oi_change_pct:+.2f}%) trong khi giá gần như ĐI NGANG "
                f"({price_change_pct_recent:+.2f}%) — cảnh báo khả năng SẮP CÓ BIẾN ĐỘNG BÙNG NỔ (squeeze).** "
                "Dòng vốn/đòn bẩy đang tích lũy nhanh trong khi giá chưa phản ứng tương ứng — tình trạng "
                "\"nén\" này thường giải phóng bằng một cú breakout mạnh về 1 trong 2 hướng, kèm thanh lý "
                "hàng loạt phía thua cuộc. Nên theo dõi sát Order Book và Long/Short Ratio để đoán hướng."
            )
        elif not oi_up and price_up:
            insights.append(
                f"**Giá tăng ({price_change_pct_recent:+.2f}%) nhưng Open Interest KHÔNG tăng tương ứng "
                f"({oi_change_pct:+.2f}%).** Nhịp tăng này có thể chủ yếu do các vị thế Short cũ bị ép đóng "
                "(short covering) hơn là dòng tiền Long mới thực sự — loại tăng giá này thường kém bền "
                "vững hơn so với trường hợp OI và giá cùng tăng."
            )
        else:
            insights.append(
                f"**Open Interest ({oi_change_pct:+.2f}%) và giá ({price_change_pct_recent:+.2f}%) không có "
                "tín hiệu rõ rệt** trong khung thời gian quan sát — thị trường phái sinh tương đối cân bằng."
            )

    # ── 3. Long/Short Ratio (Top Trader) ─────────────────────────────
    if long_short_ratio is not None:
        if long_short_ratio >= 1.5:
            insights.append(
                f"**Long/Short Ratio (Top Trader) — {long_short_ratio:.2f}: cá voi đang nghiêng hẳn về "
                "phía Long.** Tỷ lệ trên 1.5 nghĩa là số vị thế/tài khoản Long của nhóm trader lớn nhất "
                "sàn đông/nặng ký hơn đáng kể so với Short. Cần lưu ý: đây là tín hiệu ĐÁM ĐÔNG cùng phe, "
                "nên nếu kết hợp với Funding Rate dương cao ở trên, rủi ro long squeeze càng được củng cố."
            )
        elif long_short_ratio <= 0.7:
            insights.append(
                f"**Long/Short Ratio (Top Trader) — {long_short_ratio:.2f}: cá voi đang nghiêng về phía "
                "Short.** Nhóm trader lớn đang đặt cược giá giảm nhiều hơn tăng — kết hợp với Funding Rate "
                "âm ở trên sẽ củng cố thêm khả năng thị trường đang trong tâm lý phòng thủ/bi quan."
            )
        else:
            insights.append(
                f"**Long/Short Ratio (Top Trader) — {long_short_ratio:.2f}: tương đối cân bằng** giữa hai "
                "phe, không có tín hiệu đám đông rõ rệt từ nhóm trader lớn."
            )

    # ── 4. Taker Buy/Sell Ratio ───────────────────────────────────────
    if taker_buy_sell_ratio is not None:
        if taker_buy_sell_ratio >= 1.2:
            insights.append(
                f"**Taker Buy/Sell Ratio — {taker_buy_sell_ratio:.2f}: lực MUA chủ động đang áp đảo** "
                "trong khung thời gian gần nhất — khác với Long/Short Ratio (đo VỊ THẾ ĐANG MỞ), chỉ báo "
                "này đo hành vi giao dịch NGAY LÚC NÀY, phản ánh động lượng ngắn hạn nhạy hơn."
            )
        elif taker_buy_sell_ratio <= 0.85:
            insights.append(
                f"**Taker Buy/Sell Ratio — {taker_buy_sell_ratio:.2f}: lực BÁN chủ động đang áp đảo** "
                "trong khung thời gian gần nhất, cho thấy động lượng ngắn hạn đang nghiêng về phía bán."
            )

    # ── 5. Arbitrage CEX vs DEX ───────────────────────────────────────
    if arbitrage is not None and arbitrage.get("direction") != "Không đáng kể":
        insights.append(
            f"**Chênh lệch giá CEX–DEX — {arbitrage['spread_pct']:+.3f}%.** {arbitrage['direction']}. "
            "Lưu ý con số này CHƯA trừ phí giao dịch 2 sàn + phí gas + độ trượt giá thực tế khi khớp lệnh "
            "khối lượng lớn — xem thêm bảng so sánh phí bên dưới trước khi coi đây là cơ hội thực sự có lời."
        )

    return insights


def resample_by_timeframe(df, timeframe="Ngày", agg="last"):
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.copy()
        df.index = pd.to_datetime(df.index)
    """
    Chuyển đổi dữ liệu theo chuỗi thời gian (index kiểu datetime) sang
    khung Ngày/Tuần/Tháng bằng pandas resample.

    QUAN TRỌNG: luôn thực sự resample qua pandas (kể cả khi chọn "Ngày"),
    KHÔNG trả thẳng dữ liệu gốc — vì một số nguồn có tần suất DƯỚI 1 ngày
    (Funding Rate: mỗi 8 giờ, tức 3 điểm/ngày). Nếu chọn "Ngày" mà bỏ qua
    resample, biểu đồ vẫn hiển thị 3 cột/ngày thay vì gộp về 1 điểm/ngày
    như caption mô tả. Với dữ liệu vốn đã 1 điểm/ngày (giá ETH, TVL,
    Netflow, Net Issuance), resample("D") không đổi gì (no-op) nên an toàn.

    CHỈ áp dụng được cho dữ liệu có TRỤC THỜI GIAN THẬT với đủ lịch sử
    (giá ETH, EMA, RSI, Funding Rate, TVL, Dev Activity, Exchange Netflow,
    Net Issuance) — KHÔNG áp dụng được cho Gas Fee / Whale Monitor: trục
    là SỐ BLOCK (~12 giây/block), không phải ngày — muốn xem "theo ngày"
    phải quét ~7,200 block/ngày, quá tốn request RPC miễn phí trong thời
    gian thực.
    """
    rule_map = {"Ngày": "D", "Tuần": "W", "Tháng": "ME"}
    rule = rule_map.get(timeframe, "D")
    numeric_cols = df.select_dtypes(include="number").columns
    if agg == "last":
        return df[numeric_cols].resample(rule).last().dropna(how="all")
    return df[numeric_cols].resample(rule).mean().dropna(how="all")


def generate_final_signal(rsi_latest=None, netflow_latest=None, whale_count=0,
                           net_issuance_daily=None, tvl_change_pct=None,
                           gas_trend=None, funding_rate_latest=None,
                           oi_change_pct=None, price_change_pct_recent=None,
                           long_short_ratio=None):
    """
    Tổng hợp TẤT CẢ tín hiệu đã phân tích ở trên thành 1 điểm số duy nhất
    (rule-based scoring, KHÔNG phải AI/ML), quy đổi về thang từ -2 đến +2:
        -2: BÁN MẠNH   -1: BÁN   0: TRUNG LẬP   +1: MUA   +2: MUA MẠNH

    Mỗi chỉ báo đóng góp +1 (ủng hộ mua), -1 (ủng hộ bán), hoặc 0 (trung lập)
    vào điểm tổng, sau đó CHUẨN HÓA về khoảng [-2, 2] theo số chỉ báo thực
    sự có dữ liệu (để tránh thiên lệch khi 1 vài chỉ báo bị thiếu dữ liệu).
    Đây là cách tiếp cận phổ biến trong các bảng "Fear & Greed" / "Technical
    Summary" của các sàn/trang phân tích — đơn giản, minh bạch, dễ giải
    thích trong đồ án, KHÔNG phải khuyến nghị đầu tư thực sự.
    """
    score = 0.0
    max_score = 0.0
    reasons = []

    if rsi_latest is not None:
        max_score += 1
        if rsi_latest >= 70:
            score -= 1
            reasons.append(f"RSI quá mua ({rsi_latest:.1f}) → nghiêng BÁN")
        elif rsi_latest <= 30:
            score += 1
            reasons.append(f"RSI quá bán ({rsi_latest:.1f}) → nghiêng MUA")
        else:
            reasons.append(f"RSI trung tính ({rsi_latest:.1f})")

    if netflow_latest is not None:
        max_score += 1
        if netflow_latest > 0:
            score -= 1
            reasons.append("Netflow dương (tiền vào sàn) → nghiêng BÁN")
        elif netflow_latest < 0:
            score += 1
            reasons.append("Netflow âm (tiền rút khỏi sàn) → nghiêng MUA")

    if whale_count and whale_count >= 3:
        reasons.append(f"{whale_count} giao dịch cá voi lớn → biến động cao, cần thận trọng (không tính vào điểm số hướng)")

    if net_issuance_daily is not None:
        max_score += 1
        if net_issuance_daily < 0:
            score += 1
            reasons.append("Net Issuance âm (giảm phát) → nghiêng MUA dài hạn")
        else:
            score -= 0.5
            reasons.append("Net Issuance dương (lạm phát nhẹ) → trung lập/nghiêng BÁN nhẹ")

    if tvl_change_pct is not None:
        max_score += 1
        if tvl_change_pct > 2:
            score += 1
            reasons.append(f"TVL tăng {tvl_change_pct:+.1f}% → dòng vốn vào, nghiêng MUA")
        elif tvl_change_pct < -2:
            score -= 1
            reasons.append(f"TVL giảm {tvl_change_pct:.1f}% → dòng vốn ra, nghiêng BÁN")

    if gas_trend == "TĂNG":
        reasons.append("Gas fee tăng → mạng lưới sôi động, có thể đi kèm biến động giá (không tính vào điểm số hướng)")

    if funding_rate_latest is not None:
        max_score += 1
        if funding_rate_latest > 0.05:
            score -= 1
            reasons.append(f"Funding Rate dương cao ({funding_rate_latest:.3f}%) → thị trường quá tải Long, rủi ro điều chỉnh, nghiêng BÁN")
        elif funding_rate_latest < -0.02:
            score += 1
            reasons.append(f"Funding Rate âm ({funding_rate_latest:.3f}%) → thị trường nghiêng Short, có thể sắp hồi, nghiêng MUA")
        else:
            reasons.append(f"Funding Rate trung tính ({funding_rate_latest:.3f}%)")

    if oi_change_pct is not None and price_change_pct_recent is not None:
        max_score += 0.5
        if oi_change_pct > 5 and price_change_pct_recent > 3:
            score += 0.5
            reasons.append(f"OI tăng {oi_change_pct:+.1f}% cùng chiều giá → xu hướng có dòng tiền xác nhận, nghiêng MUA nhẹ")
        elif oi_change_pct > 5 and abs(price_change_pct_recent) <= 3:
            reasons.append(f"OI tăng {oi_change_pct:+.1f}% trong khi giá đi ngang → cảnh báo sắp biến động mạnh (squeeze), trung lập nhưng cần thận trọng")

    if long_short_ratio is not None:
        max_score += 0.5
        if long_short_ratio >= 1.5:
            score -= 0.5
            reasons.append(f"Long/Short Ratio cao ({long_short_ratio:.2f}) → đám đông quá tải Long, nghiêng BÁN nhẹ (rủi ro squeeze)")
        elif long_short_ratio <= 0.7:
            score += 0.5
            reasons.append(f"Long/Short Ratio thấp ({long_short_ratio:.2f}) → đám đông quá tải Short, nghiêng MUA nhẹ")

    # Chuẩn hóa về thang [-2, 2]
    normalized_score = (score / max_score) * 2 if max_score > 0 else 0
    normalized_score = max(-2, min(2, normalized_score))

    if normalized_score <= -1.2:
        label = "BÁN MẠNH"
    elif normalized_score <= -0.4:
        label = "BÁN"
    elif normalized_score < 0.4:
        label = "TRUNG LẬP"
    elif normalized_score < 1.2:
        label = "MUA"
    else:
        label = "MUA MẠNH"

    return normalized_score, label, reasons


# Test nhanh nếu chạy trực tiếp file này
if __name__ == "__main__":
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data.fetcher import fetch_eth_price_history, fetch_gas_history

    print("=== Test SMA + RSI trên giá ETH ===")
    price_df = fetch_eth_price_history(days=30)
    price_df = calculate_sma(price_df, window=7)
    price_df = calculate_rsi(price_df, window=14)
    print(price_df[["price", "SMA_7", "RSI_14"]].tail(10))
    print()

    print("=== Test dự báo xu hướng Gas ===")
    gas_df = fetch_gas_history(num_blocks=100)
    forecast_df, trend = forecast_gas_trend(gas_df, periods_ahead=10)
    print(f"Xu hướng dự báo: {trend}")
    print(forecast_df.head())
