# data/fetcher.py
# 3 hàm thu thập dữ liệu: giá ETH, phí gas, số dư token IST

import requests
import pandas as pd
import sys
import os
import time
import threading

# Cho phép import config.py từ thư mục cha (insight_dashboard/)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ETHERSCAN_API_KEY, DUNE_API_KEY, COINGECKO_API_KEY


# ═══════════════════════════════════════════════════════════════
# RATE LIMITER TOÀN CỤC CHO ETHERSCAN
# Etherscan free tier giới hạn THẬT là 3 request/giây (đã xác nhận qua lỗi
# "Max calls per sec rate limit reached (3/sec)"). Nhiều hàm trong file
# này gọi Etherscan từ BÊN TRONG ThreadPoolExecutor riêng của từng hàm
# (netflow, issuance, whale...) — khi các hàm này chạy SONG SONG với nhau
# (qua parallel_run() ở dashboard.py), tổng số luồng cộng dồn lại dễ dàng
# vượt xa 3/giây dù mỗi hàm tự giới hạn max_workers riêng của nó — kết quả
# là 1 số ngày bị lỗi rate-limit ngẫu nhiên mỗi lần chạy (khác ngày mỗi
# lần), đúng triệu chứng "reload vài lần thì hiện thêm nhưng không hết".
#
# Giải pháp: 1 rate limiter DÙNG CHUNG cho TOÀN BỘ file — mọi lệnh gọi
# Etherscan, bất kể đang chạy trong luồng/hàm nào, đều phải xếp hàng qua
# đây trước khi được gửi đi. Đặt ngưỡng 2/giây (dưới mức thật 3/giây một
# chút) để có margin an toàn.
# ═══════════════════════════════════════════════════════════════
_ETHERSCAN_RATE_LIMIT = 2.0  # request/giây
_etherscan_lock = threading.Lock()
_etherscan_next_slot = [0.0]


def _etherscan_get(params, timeout=15):
    """
    Gọi Etherscan API (module=..., action=...) kèm rate limit TOÀN CỤC.
    Dùng hàm này thay vì requests.get() trực tiếp ở MỌI nơi gọi Etherscan
    trong file — nếu thêm hàm mới cần gọi Etherscan, hãy dùng lại hàm này.
    """
    url = "https://api.etherscan.io/v2/api"
    full_params = {**params, "apikey": ETHERSCAN_API_KEY}

    with _etherscan_lock:
        now = time.monotonic()
        wait = _etherscan_next_slot[0] - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _etherscan_next_slot[0] = max(now, _etherscan_next_slot[0]) + (1.0 / _ETHERSCAN_RATE_LIMIT)

    response = requests.get(url, params=full_params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_eth_price_history(days=365):
    url = "https://api.coingecko.com/api/v3/coins/ethereum/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}

    last_error = None
    for attempt, wait_s in enumerate([0, 1, 2, 4]):
        if wait_s:
            time.sleep(wait_s)
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 429:
                last_error = f"429 Too Many Requests (lần thử {attempt + 1}/4)"
                continue
            response.raise_for_status()
            data = response.json()
            df = pd.DataFrame(data["prices"], columns=["timestamp", "price"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df["volume"] = [v[1] for v in data["total_volumes"]]
            df.set_index("timestamp", inplace=True)
            return df
        except requests.exceptions.RequestException as e:
            last_error = str(e)

    raise ValueError(f"Không lấy được giá ETH từ CoinGecko sau 4 lần thử: {last_error}")


def fetch_gas_history(num_blocks=200):
    """
    Lấy lịch sử phí gas THẬT bằng eth_feeHistory qua RPC công khai.
    Miễn phí, không cần API key, không phụ thuộc Etherscan
    (Etherscan đã chuyển dailyavggasprice sang gói Pro trả phí).
    """
    rpc_url = "https://ethereum-rpc.publicnode.com"
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_feeHistory",
        "params": [hex(num_blocks), "latest", []],
        "id": 1
    }

    response = requests.post(rpc_url, json=payload, timeout=15)
    response.raise_for_status()
    data = response.json()

    if "error" in data:
        raise ValueError(f"RPC trả lỗi: {data['error']}")

    base_fees = data["result"]["baseFeePerGas"]
    oldest_block = int(data["result"]["oldestBlock"], 16)

    base_fees_gwei = [int(fee, 16) / 1e9 for fee in base_fees]
    block_numbers = list(range(oldest_block, oldest_block + len(base_fees_gwei)))

    df = pd.DataFrame({"block": block_numbers, "baseFee_gwei": base_fees_gwei})
    df.set_index("block", inplace=True)
    return df


def fetch_token_balance(wallet_address, contract_address):
    """
    Kiểm tra số dư IST token của một ví — dùng cho token-gating.
    wallet_address: địa chỉ ví cần kiểm tra (ví dụ '0xAbC123...')
    contract_address: địa chỉ smart contract InsightToken đã deploy
    """
    params = {
        "chainid": 11155111,  # Sepolia testnet (mainnet là 1)
        "module": "account",
        "action": "tokenbalance",
        "contractaddress": contract_address,
        "address": wallet_address,
        "tag": "latest",
    }

    data = _etherscan_get(params, timeout=10)

    if data.get("status") == "0" and data.get("message") != "OK":
        raise ValueError(f"Etherscan trả lỗi: {data.get('message')} - {data.get('result')}")

    balance_ist = int(data["result"]) / 1e18  # đổi từ wei sang IST
    return balance_ist


# ═══════════════════════════════════════════════════════════════
# NHÓM 1: DỮ LIỆU THẬT 100% — MIỄN PHÍ HOÀN TOÀN
# ═══════════════════════════════════════════════════════════════

def fetch_defillama_tvl(chain="Ethereum"):
    """
    TVL (Total Value Locked) toàn hệ sinh thái DeFi trên Ethereum.
    DefiLlama API hoàn toàn miễn phí, KHÔNG cần API key, không giới hạn
    gắt gao — đây là nguồn dữ liệu chuẩn công nghiệp được hầu hết
    dashboard on-chain khác cũng dùng (kể cả các bên trả phí).
    """
    url = f"https://api.llama.fi/v2/historicalChainTvl/{chain}"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    data = response.json()

    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"], unit="s")
    df.rename(columns={"tvl": "tvl_usd"}, inplace=True)
    df.set_index("date", inplace=True)
    return df


def fetch_github_dev_activity(repo="ethereum/go-ethereum"):
    """
    Developer Activity — tần suất commit hàng tuần của repo core Ethereum.
    GitHub API miễn phí, không cần token cho tần suất gọi thấp
    (giới hạn 60 request/giờ nếu không đăng nhập — đủ dùng cho đồ án).
    Trả về 52 tuần gần nhất.
    """
    url = f"https://api.github.com/repos/{repo}/stats/commit_activity"
    response = requests.get(url, timeout=15, headers={"Accept": "application/vnd.github+json"})
    response.raise_for_status()
    data = response.json()

    # GitHub đôi khi trả về [] ở lần gọi đầu tiên vì đang tính toán thống kê
    # (cache miss) — cần thử lại sau vài giây nếu gặp trường hợp này.
    if not data:
        return pd.DataFrame(columns=["week", "commits"])

    df = pd.DataFrame(data)
    df["week"] = pd.to_datetime(df["week"], unit="s")
    df.rename(columns={"total": "commits"}, inplace=True)
    return df[["week", "commits"]]


def calculate_estimated_annual_issuance(eth2_staking):
    """
    Ước lượng lượng ETH phát hành mới hàng năm (issuance) cho validator,
    dựa trên công thức CHÍNH THỨC của Ethereum consensus layer:

        Max issuance/year = 940.8659 × sqrt(N)   (đơn vị: ETH)
        N = số validator active ≈ eth2_staking / 32

    Nguồn: ethereum.org (base_reward_factor=64, base_rewards_per_epoch=4)
    và eth2book.info (Ben Edgington) — đây là công thức GIAO THỨC THẬT,
    không phải số liệu đo lường, nên đây là "issuance TỐI ĐA lý thuyết"
    (giả định 100% validator hoạt động hoàn hảo, không bị phạt/slashing).
    Thực tế issuance thấp hơn một chút do một phần validator offline/bị phạt.
    """
    n_validators = eth2_staking / 32
    annual_issuance_eth = 940.8659 * (n_validators ** 0.5)
    daily_issuance_eth = annual_issuance_eth / 365
    return annual_issuance_eth, daily_issuance_eth


def scan_recent_onchain_activity(num_blocks=20, whale_threshold_eth=1000):
    """
    Quét N block gần nhất trên MAINNET qua RPC công khai để lấy ĐỒNG THỜI:
    1. Whale Transfer Monitor (giao dịch >= whale_threshold_eth)
    2. Active Addresses ước lượng (địa chỉ duy nhất xuất hiện)
    3. Transaction Volume on-chain (tổng ETH thực sự di chuyển, KHÁC với
       volume giao dịch trên sàn của CoinGecko — đây là volume THỰC TẾ
       trên blockchain, không phải volume order-book)
    4. Burned ETH (tính CHÍNH XÁC bằng baseFeePerGas × gasUsed mỗi block —
       đây là công thức gốc EIP-1559, không cần qua bên thứ ba)

    TỐI ƯU HIỆU NĂNG: bản trước gọi RPC TUẦN TỰ từng block một (100 request
    nối tiếp có thể mất 20-60 giây). Bản này gửi TẤT CẢ request trong 1 lần
    JSON-RPC BATCH (1 POST duy nhất chứa mảng N request — đúng chuẩn JSON-RPC
    2.0, hầu hết node public đều hỗ trợ). Nếu node không trả về đúng định
    dạng batch (một số node công khai giới hạn/tắt batching), tự động chuyển
    sang phương án dự phòng: gọi song song (ThreadPoolExecutor) thay vì tuần
    tự — vẫn nhanh hơn nhiều so với cách cũ dù không tối ưu bằng batch.
    """
    rpc_url = "https://ethereum-rpc.publicnode.com"

    latest_payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
    resp = requests.post(rpc_url, json=latest_payload, timeout=15)
    latest_block = int(resp.json()["result"], 16)

    block_numbers = [latest_block - i for i in range(num_blocks)]
    blocks_data = _fetch_blocks_batched_or_parallel(rpc_url, block_numbers)

    whale_txs = []
    unique_addresses = set()
    total_volume_eth = 0.0
    total_burned_eth = 0.0
    blocks_ok = 0

    for block_num, block_data in zip(block_numbers, blocks_data):
        if block_data is None:
            continue
        blocks_ok += 1

        base_fee_wei = int(block_data.get("baseFeePerGas", "0x0"), 16)
        gas_used = int(block_data.get("gasUsed", "0x0"), 16)
        total_burned_eth += (base_fee_wei * gas_used) / 1e18

        for tx in block_data.get("transactions", []):
            value_eth = int(tx["value"], 16) / 1e18
            total_volume_eth += value_eth
            if tx.get("from"):
                unique_addresses.add(tx["from"])
            if tx.get("to"):
                unique_addresses.add(tx["to"])
            if value_eth >= whale_threshold_eth:
                whale_txs.append({
                    "block": block_num, "hash": tx["hash"], "from": tx["from"],
                    "to": tx.get("to", "Contract Creation"), "value_eth": value_eth,
                })

    whale_df = pd.DataFrame(whale_txs)
    if not whale_df.empty:
        whale_df = whale_df.sort_values("value_eth", ascending=False)

    # Ngoại suy sang mức "hàng ngày" (7,200 block/ngày ở mainnet) để so sánh
    # tương đối với issuance ước lượng — CHỈ LÀ NGOẠI SUY, không phải đo
    # thật cả ngày (biến động rất lớn theo giờ, chỉ mang tính tham khảo).
    daily_burn_estimate = (total_burned_eth / max(blocks_ok, 1)) * 7200

    return {
        "whale_df": whale_df,
        "active_addresses": len(unique_addresses),
        "tx_volume_eth": total_volume_eth,
        "burned_eth_recent": total_burned_eth,
        "daily_burn_estimate_eth": daily_burn_estimate,
        "blocks_scanned": blocks_ok,
    }


def _fetch_blocks_batched_or_parallel(rpc_url, block_numbers, max_workers=20, full_tx=True):
    """
    Thử lấy nhiều block cùng lúc bằng 1 JSON-RPC BATCH request (1 POST chứa
    mảng N request — public node hầu hết đều hỗ trợ). Nếu thất bại hoặc
    trả về sai định dạng, tự động chuyển sang gọi SONG SONG qua thread pool
    (vẫn nhanh hơn nhiều lần so với gọi tuần tự từng block một).

    full_tx=False: chỉ lấy block header (baseFeePerGas, gasUsed...), KHÔNG
    kèm danh sách giao dịch — nhẹ và nhanh hơn nhiều khi chỉ cần tính burn.
    """
    batch_payload = [
        {"jsonrpc": "2.0", "method": "eth_getBlockByNumber", "params": [hex(b), full_tx], "id": idx}
        for idx, b in enumerate(block_numbers)
    ]
    try:
        resp = requests.post(rpc_url, json=batch_payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and len(data) == len(block_numbers):
            by_id = {item.get("id"): item.get("result") for item in data}
            return [by_id.get(idx) for idx in range(len(block_numbers))]
    except Exception:
        pass  # node không hỗ trợ batch (hoặc lỗi tạm thời) → rơi xuống phương án song song

    from concurrent.futures import ThreadPoolExecutor

    def _fetch_one(block_num):
        try:
            payload = {"jsonrpc": "2.0", "method": "eth_getBlockByNumber",
                       "params": [hex(block_num), full_tx], "id": 1}
            r = requests.post(rpc_url, json=payload, timeout=15)
            return r.json().get("result")
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(_fetch_one, block_numbers))


def get_block_number_by_timestamp(dt, closest="before"):
    """
    Chuyển 1 mốc thời gian (Timestamp) sang block number gần nhất — endpoint
    MIỄN PHÍ (không phải Pro) của Etherscan. Dùng làm điểm neo để quét dữ
    liệu lịch sử THẬT theo ngày (block header + tx list đều là dữ liệu công
    khai vĩnh viễn, KHÔNG cần node archive — khác với số dư tài khoản lịch
    sử vốn là tính năng Etherscan Pro trả phí).
    """
    params = {
        "chainid": 1, "module": "block", "action": "getblocknobytime",
        "timestamp": int(pd.Timestamp(dt).timestamp()), "closest": closest,
    }
    data = _etherscan_get(params, timeout=15)
    if data.get("status") == "0":
        raise ValueError(f"Etherscan getblocknobytime lỗi: {data.get('result')}")
    return int(data["result"])


def fetch_eth_supply_stats():
    """
    Lấy tổng cung ETH, số ETH đã stake, số ETH đã burn (từ EIP-1559).
    Endpoint ethsupply2 miễn phí trên mainnet.
    """
    params = {"chainid": 1, "module": "stats", "action": "ethsupply2"}
    data = _etherscan_get(params, timeout=15)

    if data.get("status") == "0" and data.get("message") != "OK":
        raise ValueError(f"Etherscan trả lỗi: {data.get('message')} - {data.get('result')}")

    result = data["result"]
    eth_supply = int(result["EthSupply"]) / 1e18
    eth2_staking = int(result["Eth2Staking"]) / 1e18
    burnt_fees = int(result["BurntFees"]) / 1e18

    return {
        "eth_supply": eth_supply,
        "eth2_staking": eth2_staking,
        "burnt_fees": burnt_fees,
        "staking_ratio_pct": (eth2_staking / eth_supply) * 100,
    }


def fetch_daily_burn_history(days=30, samples_per_day=6):
    """
    Backfill lịch sử BURN THẬT (không phải ước lượng ngoại suy từ vài block
    gần nhất) cho `days` ngày gần nhất. Cách làm:

    1. Với mỗi ngày, tìm block đầu/cuối ngày qua get_block_number_by_timestamp
       (endpoint MIỄN PHÍ của Etherscan).
    2. Lấy mẫu `samples_per_day` block rải đều trong ngày đó, gọi
       eth_getBlockByNumber qua RPC công khai để lấy baseFeePerGas + gasUsed.
       QUAN TRỌNG: đây là dữ liệu BLOCK HEADER (lịch sử vĩnh viễn, mọi full
       node đều có), KHÁC với số dư tài khoản lịch sử (cần archive node/API
       Pro trả phí) — nên cách này khả thi hoàn toàn miễn phí cho BẤT KỲ
       ngày nào trong quá khứ, không giới hạn ở vài giờ gần nhất.
    3. Burn/block trung bình × ~7,200 block/ngày = ước lượng burn CẢ NGÀY đó
       — vẫn là ngoại suy từ mẫu (không quét hết ~7,200 block/ngày để tiết
       kiệm request), nhưng CHÍNH XÁC HƠN NHIỀU so với chỉ dùng điều kiện
       gas HIỆN TẠI cho mọi ngày trong quá khứ (cách làm cũ).

    Kết quả cache vào CSV, mỗi lần gọi sau chỉ backfill thêm ngày còn thiếu.
    """
    csv_path = os.path.join(os.path.dirname(__file__), "daily_burn_history.csv")
    if os.path.exists(csv_path):
        existing = pd.read_csv(csv_path, parse_dates=["date"])
    else:
        existing = pd.DataFrame(columns=["date", "daily_burn_estimate_eth"])

    today = pd.Timestamp.now().normalize()
    target_dates = [today - pd.Timedelta(days=d) for d in range(1, days + 1)]  # bỏ hôm nay (chưa hết ngày)
    have = set(existing["date"].dt.normalize()) if not existing.empty else set()
    missing_dates = sorted(d for d in target_dates if d not in have)

    rpc_url = "https://ethereum-rpc.publicnode.com"

    # Bước 1: xác định block đầu/cuối của từng ngày còn thiếu (Etherscan free).
    # QUAN TRỌNG: đây từng là điểm nghẽn thật — mỗi ngày cần 2 lệnh gọi
    # Etherscan (đầu ngày + cuối ngày), chạy TUẦN TỰ thì với days=90 sẽ ra
    # tới 180 lệnh nối tiếp (~60-90s+ chỉ riêng bước này). Batch RPC ở Bước 2
    # không giúp được gì cho bước này vì Etherscan REST không hỗ trợ batch —
    # nên chạy SONG SONG bằng thread pool thay vì tuần tự (max_workers vừa
    # phải để không vượt rate limit Etherscan free tier ~5 request/giây).
    from concurrent.futures import ThreadPoolExecutor

    def _lookup_day_range(d):
        try:
            start_block = get_block_number_by_timestamp(d, "after")
            end_block = get_block_number_by_timestamp(d + pd.Timedelta(hours=23, minutes=59, seconds=59), "before")
            if end_block > start_block:
                return d, (start_block, end_block)
        except Exception:
            pass
        return d, None

    day_block_ranges = {}
    if missing_dates:
        with ThreadPoolExecutor(max_workers=5) as executor:
            for d, rng in executor.map(_lookup_day_range, missing_dates):
                if rng is not None:
                    day_block_ranges[d] = rng

    # Bước 2: gộp TẤT CẢ mẫu block của MỌI ngày thành 1 danh sách phẳng, rồi
    # lấy về bằng 1 BATCH RPC duy nhất (thay vì tuần tự từng ngày/từng mẫu —
    # tối ưu quan trọng nhất, giảm hàng trăm request nối tiếp xuống còn 1).
    all_sample_blocks = []
    blocks_per_day = {}
    for d, (start_block, end_block) in day_block_ranges.items():
        sample_blocks = sorted(set(
            int(start_block + (end_block - start_block) * i / max(samples_per_day - 1, 1))
            for i in range(samples_per_day)
        ))
        blocks_per_day[d] = sample_blocks
        all_sample_blocks.extend(sample_blocks)

    burn_by_block = {}
    if all_sample_blocks:
        blocks_data = _fetch_blocks_batched_or_parallel(rpc_url, all_sample_blocks, full_tx=False)
        for b, block in zip(all_sample_blocks, blocks_data):
            if not block:
                continue
            base_fee = int(block.get("baseFeePerGas", "0x0"), 16)
            gas_used = int(block.get("gasUsed", "0x0"), 16)
            burn_by_block[b] = base_fee * gas_used / 1e18

    new_rows = []
    for d, (start_block, end_block) in day_block_ranges.items():
        burns_per_block = [burn_by_block[b] for b in blocks_per_day[d] if b in burn_by_block]
        if not burns_per_block:
            continue
        avg_burn_per_block = sum(burns_per_block) / len(burns_per_block)
        blocks_in_day = end_block - start_block
        daily_burn_estimate = avg_burn_per_block * blocks_in_day
        new_rows.append({"date": d, "daily_burn_estimate_eth": daily_burn_estimate})

    if new_rows:
        existing = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
        existing = existing.drop_duplicates(subset="date").sort_values("date")
        existing.to_csv(csv_path, index=False)

    return existing.sort_values("date").reset_index(drop=True)


def fetch_gas_history_hourly(hours=240):
    """
    Backfill lịch sử Gas Fee THẬT theo GIỜ (khác với fetch_gas_history() ở
    trên vốn chỉ lấy được ~200-1000 block gần nhất, tương đương chưa đầy
    3.5 giờ) — dùng đúng kỹ thuật batch RPC đã dùng cho burn history.

    Điểm khác biệt quan trọng so với fetch_daily_burn_history(): thay vì
    gọi Etherscan get_block_number_by_timestamp cho MỖI giờ còn thiếu (tốn
    request, bị giới hạn rate limit ~5/s), hàm này NGOẠI SUY block number
    trực tiếp từ 1 block neo (block mới nhất) — vì Ethereum sau The Merge
    có block time gần như cố định 12 giây/slot, sai số ngoại suy chỉ vài
    chục giây, chấp nhận được cho mục đích xem xu hướng gas fee theo giờ.
    Nhờ vậy, TOÀN BỘ việc dựng block number cho hàng trăm giờ không tốn
    một request Etherscan nào — chỉ cần 1 request RPC lấy block mới nhất,
    rồi 1 batch request duy nhất lấy toàn bộ block header.

    Cache vào CSV, mỗi lần gọi sau chỉ backfill thêm giờ còn thiếu.
    """
    csv_path = os.path.join(os.path.dirname(__file__), "gas_fee_hourly_history.csv")
    if os.path.exists(csv_path):
        existing = pd.read_csv(csv_path, parse_dates=["hour"])
    else:
        existing = pd.DataFrame(columns=["hour", "baseFee_gwei"])

    now = pd.Timestamp.now().floor("h")
    target_hours = [now - pd.Timedelta(hours=h) for h in range(1, hours + 1)]  # bỏ giờ hiện tại (chưa trọn giờ)
    have = set(existing["hour"]) if not existing.empty else set()
    missing_hours = sorted(h for h in target_hours if h not in have)

    if not missing_hours:
        return existing.sort_values("hour").reset_index(drop=True)

    rpc_url = "https://ethereum-rpc.publicnode.com"
    try:
        latest_num = int(requests.post(rpc_url, json={
            "jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1
        }, timeout=15).json()["result"], 16)
        latest_block = requests.post(rpc_url, json={
            "jsonrpc": "2.0", "method": "eth_getBlockByNumber",
            "params": [hex(latest_num), False], "id": 1
        }, timeout=15).json()["result"]
        latest_ts = int(latest_block["timestamp"], 16)
    except Exception:
        return existing.sort_values("hour").reset_index(drop=True)

    AVG_BLOCK_TIME_SEC = 12.0  # target chính thức của Ethereum sau The Merge
    hour_to_block = {}
    for h in missing_hours:
        seconds_ago = latest_ts - int(h.timestamp())
        est_block = latest_num - int(seconds_ago / AVG_BLOCK_TIME_SEC)
        if est_block > 0:
            hour_to_block[h] = est_block

    if not hour_to_block:
        return existing.sort_values("hour").reset_index(drop=True)

    blocks = list(hour_to_block.values())
    blocks_data = _fetch_blocks_batched_or_parallel(rpc_url, blocks, full_tx=False)
    block_to_basefee = {}
    for b, block in zip(blocks, blocks_data):
        if not block:
            continue
        base_fee_wei = int(block.get("baseFeePerGas", "0x0"), 16)
        block_to_basefee[b] = base_fee_wei / 1e9  # gwei

    new_rows = [
        {"hour": h, "baseFee_gwei": block_to_basefee[b]}
        for h, b in hour_to_block.items() if b in block_to_basefee
    ]

    if new_rows:
        existing = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
        existing = existing.drop_duplicates(subset="hour").sort_values("hour")
        existing.to_csv(csv_path, index=False)

    return existing.sort_values("hour").reset_index(drop=True)


def build_issuance_history(days=30, samples_per_day=6):
    """
    Ghép burn lịch sử THẬT (fetch_daily_burn_history) với issuance ước lượng
    theo công thức giao thức (940.8659×√N) — N (số validator) dùng giá trị
    HIỆN TẠI áp cho mọi ngày trong quá khứ, vì không có nguồn miễn phí đáng
    tin cậy cho số validator lịch sử (beaconcha.in đã ngừng free API,
    Etherscan không có endpoint free cho việc này). Đây là hạn chế đã biết:
    issuance thực tế biến động RẤT CHẬM theo ngày (validator mới gia nhập
    dần dần) nên sai số của việc "áp giá trị hiện tại cho quá khứ" là nhỏ,
    ngược lại với burn (biến động mạnh theo gas fee từng ngày) — đây là lý
    do burn được ưu tiên tính THẬT còn issuance vẫn dùng ước lượng đồng nhất.
    """
    burn_hist = fetch_daily_burn_history(days=days, samples_per_day=samples_per_day)
    supply_stats = fetch_eth_supply_stats()
    _, daily_issuance_now = calculate_estimated_annual_issuance(supply_stats["eth2_staking"])

    df = burn_hist.copy()
    df["estimated_daily_issuance_eth"] = daily_issuance_now
    df = df.rename(columns={"daily_burn_estimate_eth": "estimated_daily_burn_eth"})
    df["net_issuance_daily_eth"] = df["estimated_daily_issuance_eth"] - df["estimated_daily_burn_eth"]
    df["pct_minted_supply_burned"] = (
        df["estimated_daily_burn_eth"] / df["estimated_daily_issuance_eth"] * 100
    ).where(df["estimated_daily_issuance_eth"] > 0)
    df["burnt_fees_cumulative"] = supply_stats["burnt_fees"]
    return df



# ═══════════════════════════════════════════════════════════════
# NHÓM 2: DỮ LIỆU PROXY / ƯỚC LƯỢNG (đã ghi rõ giới hạn ở mỗi hàm)
# ═══════════════════════════════════════════════════════════════

# Địa chỉ ví sàn công khai đã được Etherscan gắn nhãn (Public Name Tags).
# Dùng làm PROXY để ước lượng dòng tiền vào/ra sàn — KHÔNG chính xác 100%
# vì các sàn còn nhiều ví khác (cold storage, ví nội bộ...) không công khai.
KNOWN_EXCHANGE_WALLETS = {
    "Binance 14": "0x28C6c06298d514Db089934071355E5743bf21d60",
    "Binance Hot 20": "0xF977814e90dA44bFA03b6295A0616a897441aceC",
    "Coinbase 1": "0x71660c4005BA85c37ccec55d0C4493E66Fe775d3",
    "Kraken 1": "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2",
}


def fetch_daily_netflow_history(days=30, max_pages_per_day=3):
    """
    Backfill lịch sử Exchange Netflow THẬT cho `days` ngày gần nhất — dựa
    trên LỊCH SỬ GIAO DỊCH thật (txlist) của các ví sàn đã biết, KHÔNG phải
    chỉ snapshot số dư hiện tại (vốn chỉ tích lũy được 1 điểm/lần chạy).

    Cách làm mỗi ngày, với mỗi ví sàn:
    1. Tìm block đầu/cuối ngày (get_block_number_by_timestamp — miễn phí).
    2. Lấy toàn bộ giao dịch ETH thường (txlist) trong khoảng block đó.
    3. netflow_ngày = tổng ETH chuyển VÀO ví sàn − tổng ETH chuyển RA.

    GIỚI HẠN CẦN HIỂU RÕ: Etherscan free tier trả tối đa 10,000 bản ghi/trang.
    Các ví hot wallet của sàn lớn (đặc biệt Binance) có thể có NHIỀU HƠN
    max_pages_per_day × 10,000 giao dịch/ngày vào những ngày cao điểm — khi
    đó số liệu ngày đó sẽ bị đánh dấu `is_partial=True` (thiếu một phần,
    không phải sai, chỉ là CHƯA ĐẦY ĐỦ 100%). Đây là giới hạn kỹ thuật thật
    của việc dùng API miễn phí để phân tích ví có khối lượng giao dịch cực
    lớn — không có cách nào khắc phục hoàn toàn nếu không trả phí cho
    node/API chuyên dụng (kiểu Nansen/Glassnode/CryptoQuant).

    Kết quả cache vào CSV, mỗi lần gọi sau chỉ backfill thêm ngày còn thiếu
    (không quét lại các ngày đã có).
    """
    csv_path = os.path.join(os.path.dirname(__file__), "exchange_netflow_daily_history.csv")
    if os.path.exists(csv_path):
        existing = pd.read_csv(csv_path, parse_dates=["date"])
    else:
        existing = pd.DataFrame(columns=["date", "netflow_eth", "is_partial"])

    today = pd.Timestamp.now().normalize()
    target_dates = [today - pd.Timedelta(days=d) for d in range(1, days + 1)]  # bỏ hôm nay (chưa hết ngày)
    have = set(existing["date"].dt.normalize()) if not existing.empty else set()
    missing_dates = sorted(d for d in target_dates if d not in have)

    def _fetch_wallet_netflow(d, addr, start_block, end_block):
        """
        Trả về (d, net_eth, is_partial, ok). ok=False nghĩa là gọi API
        thất bại (rate-limit/timeout/lỗi response) — KHÁC với "thực sự
        không có giao dịch nào" (result=[] hợp lệ, ok=True, net=0).
        Nhầm 2 trường hợp này với nhau là lỗi đã phát hiện ở bản trước:
        API lỗi bị tính thành netflow=0 và LƯU VĨNH VIỄN vào cache, khiến
        nhiều ngày hiển thị sai là "không có dòng tiền" trong khi thực
        chất là chưa quét được dữ liệu ngày đó.
        """
        total_in, total_out, is_partial = 0.0, 0.0, False
        page = 1
        while page <= max_pages_per_day:
            params = {
                "chainid": 1, "module": "account", "action": "txlist", "address": addr,
                "startblock": start_block, "endblock": end_block,
                "page": page, "offset": 10000, "sort": "asc",
            }
            resp_json = None
            for attempt in range(2):  # 1 lần thử + 1 lần retry nếu lỗi tạm thời
                try:
                    resp_json = _etherscan_get(params, timeout=20)
                    break
                except Exception:
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    return d, addr, 0.0, 0.0, False, False  # ok=False sau khi đã retry vẫn lỗi

            if resp_json is None:
                return d, addr, 0.0, 0.0, False, False

            results = resp_json.get("result")
            if isinstance(results, list) and len(results) == 0:
                break  # ok=True: Etherscan xác nhận HỢP LỆ là không có giao dịch nào
            if not isinstance(results, list):
                # result không phải list = Etherscan trả lỗi (vd "Max rate limit reached",
                # "NOTOK"...) — đây LÀ lỗi thật, không phải "không có giao dịch"
                return d, addr, 0.0, 0.0, False, False

            for tx in results:
                if tx.get("isError") == "1":
                    continue
                value_eth = int(tx["value"]) / 1e18
                to_addr = (tx.get("to") or "").lower()
                from_addr = (tx.get("from") or "").lower()
                if to_addr == addr.lower():
                    total_in += value_eth
                elif from_addr == addr.lower():
                    total_out += value_eth
            if len(results) < 10000:
                break
            is_partial = True  # còn khả năng có thêm giao dịch chưa quét tới
            page += 1
            time.sleep(0.25)  # tránh vượt rate limit Etherscan free tier khi phải phân trang
        return d, addr, total_in, total_out, is_partial, True

    # Mỗi cặp (ngày, ví) hoàn toàn độc lập → chạy SONG SONG thay vì tuần tự.
    # max_workers vừa phải để không vượt rate limit Etherscan free tier (~5 req/s).
    # QUAN TRỌNG: bước "tìm block đầu/cuối ngày" bên dưới cũng ĐƯỢC SONG SONG
    # HOÁ — bản trước chạy tuần tự (2 lệnh Etherscan/ngày), với days=60 ra tới
    # 120 lệnh nối tiếp, là điểm nghẽn chính khiến Premium tải rất chậm.
    from concurrent.futures import ThreadPoolExecutor

    def _lookup_day_range(d):
        try:
            start_block = get_block_number_by_timestamp(d, "after")
            end_block = get_block_number_by_timestamp(d + pd.Timedelta(hours=23, minutes=59, seconds=59), "before")
            if end_block > start_block:
                return d, (start_block, end_block)
        except Exception:
            pass
        return d, None

    tasks = []
    if missing_dates:
        with ThreadPoolExecutor(max_workers=5) as executor:
            for d, rng in executor.map(_lookup_day_range, missing_dates):
                if rng is None:
                    continue
                start_block, end_block = rng
                for addr in KNOWN_EXCHANGE_WALLETS.values():
                    tasks.append((d, addr, start_block, end_block))

    day_results = {}  # date -> [(net_eth, is_partial, ok), ...] gộp từ nhiều ví
    if tasks:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_fetch_wallet_netflow, *t) for t in tasks]
            for fut in futures:
                d, addr, total_in, total_out, partial, ok = fut.result()
                day_results.setdefault(d, []).append((total_in - total_out, partial, ok))

    new_rows = []
    skipped_days = []
    for d, entries in day_results.items():
        if not all(e[2] for e in entries):
            # ít nhất 1 ví lỗi thật (không phải "không có giao dịch") → KHÔNG cache
            # ngày này, để lần gọi sau tự động coi là "còn thiếu" và thử lại
            skipped_days.append(d)
            continue
        total_net = sum(e[0] for e in entries)
        is_partial = any(e[1] for e in entries)
        new_rows.append({"date": d, "netflow_eth": total_net, "is_partial": is_partial})

    if new_rows:
        existing = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
        existing = existing.drop_duplicates(subset="date").sort_values("date")
        existing.to_csv(csv_path, index=False)

    # Thử lại 1 lần cho các ngày bị lỗi API (không phải "không có giao dịch")
    # ngay trong lần gọi này — giảm số lần người dùng phải tải lại trang để
    # backfill hoàn tất, thay vì luôn phải chờ tới lần chạy kế tiếp.
    if skipped_days:
        time.sleep(1.5)
        retry_tasks = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            for d, rng in executor.map(_lookup_day_range, skipped_days):
                if rng is None:
                    continue
                start_block, end_block = rng
                for addr in KNOWN_EXCHANGE_WALLETS.values():
                    retry_tasks.append((d, addr, start_block, end_block))

        if retry_tasks:
            from concurrent.futures import ThreadPoolExecutor
            retry_day_results = {}
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(_fetch_wallet_netflow, *t) for t in retry_tasks]
                for fut in futures:
                    d, addr, total_in, total_out, partial, ok = fut.result()
                    retry_day_results.setdefault(d, []).append((total_in - total_out, partial, ok))

            retry_rows = []
            for d, entries in retry_day_results.items():
                if not all(e[2] for e in entries):
                    continue  # vẫn lỗi sau khi thử lại → để lần chạy dashboard sau tự thử tiếp
                total_net = sum(e[0] for e in entries)
                is_partial = any(e[1] for e in entries)
                retry_rows.append({"date": d, "netflow_eth": total_net, "is_partial": is_partial})

            if retry_rows:
                existing = pd.concat([existing, pd.DataFrame(retry_rows)], ignore_index=True)
                existing = existing.drop_duplicates(subset="date").sort_values("date")
                existing.to_csv(csv_path, index=False)

    return existing.sort_values("date").reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════
# NHÓM 3: FUNDING RATE (Binance Futures — public, miễn phí, không cần key)
# ═══════════════════════════════════════════════════════════════

def fetch_funding_rate_history(symbol="ETHUSDT", limit=200):
    """
    Funding Rate của hợp đồng Perpetual Futures ETH/USDT trên Binance.
    Đây là API THỊ TRƯỜNG CÔNG KHAI (market data), KHÔNG cần API key,
    KHÔNG cần đăng ký — endpoint dữ liệu chuẩn của Binance Futures.

    Ý nghĩa: Funding Rate là khoản phí định kỳ (mỗi 8 giờ) trao đổi giữa
    trader Long và Short để giữ giá Futures bám sát giá Spot.
    - Funding Rate DƯƠNG: Long trả phí cho Short → thị trường đang nghiêng
      về phe Long (tâm lý lạc quan/tham lam, đòn bẩy long chiếm ưu thế).
    - Funding Rate ÂM: Short trả phí cho Long → thị trường đang nghiêng
      về phe Short (tâm lý bi quan/sợ hãi).
    Funding Rate dương quá cao kéo dài thường cảnh báo rủi ro "long
    squeeze" (thị trường quá tải đòn bẩy mua), ngược lại với short.
    """
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    params = {"symbol": symbol, "limit": limit}

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    df = pd.DataFrame(data)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["fundingRate"] = df["fundingRate"].astype(float) * 100  # đổi sang %
    df = df.rename(columns={"fundingTime": "timestamp", "fundingRate": "funding_rate_pct"})
    df.set_index("timestamp", inplace=True)
    return df[["funding_rate_pct"]]


# ═══════════════════════════════════════════════════════════════
# NHÓM 6: DUNE ANALYTICS — Staking Ratio & Staking Flows
# (Etherscan ethsupply2 cho ra staking ratio KHÔNG chính xác — thay bằng
# nguồn Dune Analytics, dựa trên dashboard cộng đồng @hildobby chuyên
# theo dõi ETH2 staking, được xem là nguồn tham chiếu phổ biến)
# ═══════════════════════════════════════════════════════════════

def _find_col(df, keywords):
    """
    Dò tìm cột theo từ khóa (không phân biệt hoa/thường, so khớp một phần).
    CẦN THIẾT vì tên cột thực tế trả về từ mỗi Dune query có thể khác nhau
    tuỳ cách tác giả đặt tên (vd 'pct_staked' hay 'staking_ratio' hay
    '% Supply Staked') — không có cách nào biết chắc chắn tên cột chính
    xác nếu không tự chạy thử query đó (mình không có DUNE_API_KEY của
    bạn nên không gọi thử được từ phía mình). Nếu dò không ra, hàm gọi
    phía trên sẽ báo lỗi kèm danh sách CÁC CỘT THỰC TẾ để bạn đối chiếu
    và có thể chỉnh lại danh sách từ khóa cho khớp.
    """
    for kw in keywords:
        for col in df.columns:
            if kw.lower() in col.lower():
                return col
    return None


def fetch_dune_query_results(query_id, limit=1000):
    """
    Gọi hàm chung để lấy kết quả MỚI NHẤT đã được vật chất hoá (materialized)
    của 1 Dune query, qua Dune API v1 — cần DUNE_API_KEY (free tier của Dune
    đủ dùng, giới hạn theo số request/tháng chứ không chặn theo query).
    Lưu ý: đây là kết quả lần chạy GẦN NHẤT của query trên Dune (không tự
    chạy lại query mới mỗi lần gọi hàm này — Dune tự làm mới theo lịch của
    tác giả query, thường vài giờ/lần).
    """
    if not DUNE_API_KEY:
        raise ValueError("Chưa cấu hình DUNE_API_KEY trong file .env")

    url = f"https://api.dune.com/api/v1/query/{query_id}/results"
    headers = {"X-Dune-API-Key": DUNE_API_KEY}
    params = {"limit": limit}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    rows = data.get("result", {}).get("rows", [])
    if not rows:
        raise ValueError(f"Dune query {query_id} không trả về dữ liệu (rows rỗng).")
    return pd.DataFrame(rows)


def fetch_eth_staking_ratio_dune():
    """
    % ETH đang stake / tổng cung — lấy từ Dune query 1933048.

    ĐÃ XÁC NHẬN qua đối chiếu trực tiếp với dashboard @hildobby: query này
    trả về ĐÚNG 1 dòng, dạng {"total_validators": 33.156...} — tên cột bị
    đặt sai (kế thừa từ bản gốc), nhưng GIÁ TRỊ ĐÃ LÀ PHẦN TRĂM SẴN (33.156
    nghĩa là 33.156%, khớp đúng widget "Percentage of Staked ETH: 33.16%"
    trên dashboard gốc) — KHÔNG cần quy đổi qua triệu ETH hay chia cho tổng
    cung như phiên bản trước (đã sửa sai chỗ này).
    """
    df = fetch_dune_query_results(1933048)
    if df.empty:
        raise ValueError("Query 1933048 (Staking Ratio) không trả về dữ liệu.")

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if not numeric_cols:
        raise ValueError(f"Query 1933048 không có cột số nào. Cột thực tế: {list(df.columns)}")
    return float(df[numeric_cols[0]].iloc[-1])


def fetch_eth_staking_flows_history(days=90):
    """
    Dòng tiền vào/ra staking (Staking Flows) theo ngày — lấy từ Dune query
    2371805. Dữ liệu trả về dạng "dài" (long format): mỗi dòng có
    {amount, flow_type: "Deposits"/"Withdrawals", time} — KHÔNG có sẵn cột
    net_flow hay cột deposit/withdrawal riêng, cần tự PIVOT lại.

    Trả về DataFrame: date, deposits_eth, withdrawals_eth, net_flow_eth.
    """
    df = fetch_dune_query_results(2371805, limit=days * 3 + 60)  # x3 vì mỗi ngày có thể có 2+ dòng (Deposits/Withdrawals)

    date_col = _find_col(df, ["time", "day", "date", "block_date"])
    flow_type_col = _find_col(df, ["flow_type", "type"])
    amount_col = _find_col(df, ["amount"])
    if date_col is None or flow_type_col is None or amount_col is None:
        raise ValueError(
            f"Không tìm thấy đủ cột date/flow_type/amount trong query 2371805. "
            f"Cột thực tế: {list(df.columns)}"
        )

    df["_date"] = pd.to_datetime(df[date_col]).dt.normalize()
    df["_flow_type_norm"] = df[flow_type_col].astype(str).str.strip().str.lower()

    pivot = df.pivot_table(index="_date", columns="_flow_type_norm", values=amount_col, aggfunc="sum", fill_value=0.0)

    deposit_col = _find_col(pd.DataFrame(columns=pivot.columns), ["deposit"])
    withdraw_col = _find_col(pd.DataFrame(columns=pivot.columns), ["withdraw"])
    if deposit_col is None or withdraw_col is None:
        raise ValueError(
            f"Không tìm thấy nhóm 'Deposits'/'Withdrawals' trong flow_type của query 2371805. "
            f"Các giá trị flow_type thực tế: {sorted(df['_flow_type_norm'].unique().tolist())}"
        )

    result = pd.DataFrame({
        "date": pivot.index,
        "deposits_eth": pivot[deposit_col].values,
        "withdrawals_eth": pivot[withdraw_col].values,
    })
    result["net_flow_eth"] = result["deposits_eth"] - result["withdrawals_eth"]
    result = result.sort_values("date").tail(days).reset_index(drop=True)
    return result


if __name__ == "__main__":
    print("=== Test 1: Giá ETH ===")
    price_df = fetch_eth_price_history(days=30)
    print(price_df.head())
    print()

    print("=== Test 2: Phí Gas ===")
    gas_df = fetch_gas_history(num_blocks=200)
    print(gas_df.head())
    print()

    print("=== Test 3: Số dư IST token ===")
    # Thay 2 địa chỉ dưới bằng địa chỉ ví và contract THẬT của bạn
    balance = fetch_token_balance("0xĐỊA_CHỈ_VÍ_CỦA_BẠN", "0x0250b1920998963518973fD7D6a4CfE7A5da38Da")
    print(f"Balance: {balance} IST")
