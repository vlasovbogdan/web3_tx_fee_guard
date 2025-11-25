#!/usr/bin/env python3
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
  start = time.time()
    tx_hash = args.tx
    if not (tx_hash.startswith("0x") and len(tx_hash) == 66):
        print("ERROR: invalid tx hash format.", file=sys.stderr)
        sys.exit(1)

    print(f"Watching tx: {tx_hash}")
    print(f"RPC: {args.rpc}")
    print(f"Warn threshold: {args.warn_fee_eth} ETH")
    print()

    attempts = 0
    receipt = None

    while attempts < args.max_attempts:
        attempts += 1
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            # If no exception: tx is mined / at least has a receipt
            break
        except TransactionNotFound:
            print(f"[{attempts}/{args.max_attempts}] Pending...")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR while fetching receipt: {e}", file=sys.stderr)
            sys.exit(1)

        time.sleep(args.interval)

    if receipt is None:
        print("Gave up waiting for transaction to be mined.", file=sys.stderr)
        sys.exit(2)

    # Fetch the tx for gasPrice fallback
    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR while fetching transaction: {e}", file=sys.stderr)
        sys.exit(1)

    gas_used: Optional[int] = receipt.get("gasUsed") if isinstance(receipt, dict) else getattr(receipt, "gasUsed", None)
    gas_price_wei = get_gas_price_wei(tx, receipt)

    if gas_used is None:
        print("ERROR: receipt has no gasUsed field.", file=sys.stderr)
        sys.exit(1)

    total_fee_wei = gas_used * gas_price_wei
    total_fee_eth = float(w3.from_wei(total_fee_wei, "ether"))

    status = receipt.get("status") if isinstance(receipt, dict) else getattr(receipt, "status", None)
    status_str = "success" if status == 1 else "failure" if status == 0 else "unknown"

    print("=== TX MINED ===")
    print(f"Status        : {status_str}")
    print(f"Block         : {receipt.get('blockNumber') if isinstance(receipt, dict) else getattr(receipt, 'blockNumber', None)}")
    print(f"Gas used      : {gas_used}")
    print(f"Gas price     : {w3.from_wei(gas_price_wei, 'gwei')} gwei")
    print(f"Total fee     : {total_fee_eth:.8f} ETH")
    print(f"Warn threshold: {args.warn_fee_eth:.8f} ETH")

    if total_fee_eth > args.warn_fee_eth:
        print("\n⚠️  Fee exceeded threshold!")
        sys.exit(3)
    elapsed = time.time() - start
    print(f"\nElapsed time watching tx: {elapsed:.2f}s")

    print("\n✅ Fee within threshold.")
    sys.exit(0)


if __name__ == "__main__":
    main()
