import argparse
import os
import sys
from typing import Optional

from web3 import Web3
from web3.exceptions import TransactionNotFound

DEFAULT_RPC = os.getenv("RPC_URL", "https://mainnet.infura.io/v3/your_api_key")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="quick_tx_fee",
        description="Print fee details for a single transaction.",
    )
    p.add_argument("--rpc", default=DEFAULT_RPC, help="RPC URL (default from RPC_URL env).")
    p.add_argument("--tx", required=True, help="Transaction hash (0x...).")
    p.add_argument(
        "--max-fee-eth",
        type=float,
        help="If set, exit non-zero if total fee exceeds this ETH value.",
    )
        p.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP RPC timeout in seconds (default: 15).",
    )
    return p.parse_args()


def normalize_hash(tx_hash: str) -> str:
    tx_hash = tx_hash.strip()
    if not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    if len(tx_hash) != 66:
        raise ValueError("tx hash must be 0x + 64 hex chars")
    int(tx_hash[2:], 16)  # validate hex
    return tx_hash.lower()


def main() -> int:
    args = parse_args()

    try:
        tx_hash = normalize_hash(args.tx)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

      w3 = Web3(Web3.HTTPProvider(args.rpc, request_kwargs={"timeout": args.timeout}))
    if not w3.is_connected():
        print(f"ERROR: could not connect to RPC {args.rpc}", file=sys.stderr)
        return 1

    try:
        tx = w3.eth.get_transaction(tx_hash)
    except TransactionNotFound:
        print(f"ERROR: transaction not found: {tx_hash}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: failed to fetch transaction: {exc}", file=sys.stderr)
        return 1

    try:
        rcpt = w3.eth.get_transaction_receipt(tx_hash)
    except TransactionNotFound:
        print(f"INFO: transaction is still pending: {tx_hash}")
        return 0
    except Exception as exc:
        print(f"ERROR: failed to fetch receipt: {exc}", file=sys.stderr)
        return 1

    gas_used: Optional[int] = rcpt.gasUsed
    gas_price_wei: Optional[int] = getattr(rcpt, "effectiveGasPrice", None) or tx.get("gasPrice")

    if gas_used is None or gas_price_wei is None:
        print("ERROR: missing gasUsed or gasPrice, cannot compute fee.", file=sys.stderr)
        return 1

    total_fee_wei = gas_used * gas_price_wei
    total_fee_eth = float(Web3.from_wei(total_fee_wei, "ether"))
    gas_price_gwei = float(Web3.from_wei(gas_price_wei, "gwei"))

    print(f"tx         : {tx_hash}")
    print(f"block      : {rcpt.blockNumber}")
    print(f"status     : {'success' if rcpt.status == 1 else 'failed'}")
    print(f"gasUsed    : {gas_used:,}")
    print(f"gasPrice   : {gas_price_gwei:.2f} gwei")
    print(f"total fee  : {total_fee_eth:.6f} ETH")

    if args.max_fee_eth is not None and total_fee_eth > args.max_fee_eth:
        print(
            f"FEE GUARD: total fee {total_fee_eth:.6f} ETH exceeds max "
            f"{args.max_fee_eth:.6f} ETH.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
