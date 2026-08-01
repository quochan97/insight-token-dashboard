from dotenv import load_dotenv
import os
from web3 import Web3  # Thêm thư viện Web3 để chuẩn hóa địa chỉ

load_dotenv()

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
RPC_URL = os.getenv("RPC_URL", "https://ethereum-rpc.publicnode.com")

# --- ĐOẠN CODE TỰ ĐỘNG BỌC LỖI CHO CONTRACT ADDRESS ---
raw_address = os.getenv("CONTRACT_ADDRESS")

# Kiểm tra nếu có địa chỉ nhưng bị thiếu '0x' thì tự động nối vào
if raw_address and not raw_address.startswith("0x"):
    raw_address = "0x" + raw_address

# Chuẩn hóa thành Checksum Address (bắt buộc đối với phần lớn RPC)
if raw_address:
    CONTRACT_ADDRESS = Web3.to_checksum_address(raw_address)
else:
    CONTRACT_ADDRESS = None

# Tùy chọn — KHÔNG bắt buộc: tất cả endpoint Binance dùng trong dự án này là
# market data công khai (funding rate, open interest, klines, order book...),
# không cần API key. Chỉ set biến này nếu bạn muốn gửi kèm key để tăng weight
# limit; để trống vẫn hoạt động bình thường.
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", None)

# BẮT BUỘC cho 2 tính năng mới: Staking Ratio (query Dune 1933048) và
# Staking Flows (query Dune 2371805). Đăng ký miễn phí tại dune.com,
# vào Settings → API → tạo key, dán vào .env dạng: DUNE_API_KEY=...
DUNE_API_KEY = os.getenv("DUNE_API_KEY", None)

if not DUNE_API_KEY:
    print("CANH BAO: Chua tim thay DUNE_API_KEY trong file .env — Staking Ratio se "
          "tam dung lai gia tri cu (Etherscan) va Staking Flows se khong hien thi.")

if not ETHERSCAN_API_KEY:
    print("CANH BAO: Chua tim thay ETHERSCAN_API_KEY trong file .env")
