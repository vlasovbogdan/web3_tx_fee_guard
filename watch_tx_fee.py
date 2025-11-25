import argparse
import sys
import time
from typing import Optional

from web3 import Web3
from web3.exceptions import TransactionNotFound


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Watch a tx until mined and check its fee."
    )
    p.add_argument(
        "--rpc",
        required=True,
        help="Ethereum-compatible RPC URL.",
    )
    p.add_argument(
        "--tx",
        required=True,
        help="Transaction hash (0x...).",
    )
    p.add_argument(
        "--warn-fee-eth",
        type=float,
        default=0.01,
        help="Warn if fee exceeds this threshold in ETH (default: 0.01).",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds (default: 5).",
    )
    p.add_argument(
        "--max-attempts",
        type=int,
        default=60,
        help="Maximum polling attempts before giving up (default: 60).",
    )
    return p.parse_args()


def get_gas_price_wei(tx, receipt) -> int:
    """
    Best-effort gas price (wei):

    - prefer receipt.effectiveGasPrice / ['effectiveGasPrice'] if present (EIP-1559)
    - fall back to tx.gasPrice / ['gasPrice']
    """
    # Try receipt effectiveGasPrice
    for key in ("effectiveGasPrice", "effective_gas_price"):
        if hasattr(receipt, key):
            val = getattr(receipt, key)
            if val is not None:
                return int(val)
        if isinstance(receipt, dict) and key in receipt and receipt[key] is not None:
            return int(receipt[key])

    # Fallback to tx.gasPrice
    for key in ("gasPrice", "gas_price"):
        if hasattr(tx, key):
            val = getattr(tx, key)
            if val is not None:
                return int(val)
        if isinstance(tx, dict) and key in tx and tx[key] is not None:
            return int(tx[key])

    return 0


def main() -> None:
    args = parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        print(f"ERROR: failed to connect to RPC {args.rpc}", file=sys.stderr)
        sys.exit(1)

      tx_hash = args.tx
    if not (tx_hash.startswith("0x") and len(tx_hash) == 66):
        print("ERROR: invalid tx hash (expected 0x + 64 hex chars).", file=sys.stderr)
        sys.exit(1)

