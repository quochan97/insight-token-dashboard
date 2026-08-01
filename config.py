from dotenv import load_dotenv
import os

load_dotenv()

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")
RPC_URL = os.getenv("RPC_URL", "https://ethereum-rpc.publicnode.com")

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
