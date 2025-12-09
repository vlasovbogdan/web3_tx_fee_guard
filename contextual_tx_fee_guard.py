#!/usr/bin/env python3
"""
contextual_tx_fee_guard.py

Contextual fee guard for a single transaction.

Instead of only checking a fixed absolute threshold, this script:
  - fetches the transaction + receipt
  - profiles recent gas prices over a configurable block window
  - compares the tx's gas price against recent median / p95
  - classifies the tx as: ok / high_vs_median / high_vs_p95

It is meant to complement app.py and batch_fee_guard.py by giving
you a "how bad was this fee compared to recent network conditions?"
signal.

Example (human-readable):

    python contextual_tx_fee_guard.py 0x... \
        --rpc https://mainnet.infura.io/v3/YOUR_KEY

Example (JSON):

    python contextual_tx_fee_guard.py 0x... \
        --rpc https://mainnet.infura.io/v3/YOUR_KEY \
        --json

Exit codes:
    0  - ok (fee within configured multipliers)
    1  - invalid input or connection error
    2  - transaction not found
    3  - fee considered high vs recent median / p95
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from web3 import Web3
from web3.types import TxData, TxReceipt


DEFAULT_RPC = os.getenv("RPC_URL", "https://mainnet.infura.io/v3/YOUR_API_KEY")
DEFAULT_TIMEOUT = int(os.getenv("CTX_GUARD_TIMEOUT", "30"))

DEFAULT_BLOCKS = int(os.getenv("CTX_GUARD_BLOCKS", "300"))
DEFAULT_STEP = int(os.getenv("CTX_GUARD_STEP", "3"))

# If tx gas price is above (median * MULT), we mark it as high vs median
DEFAULT_WARN_MULT_MEDIAN = float(os.getenv("CTX_GUARD_WARN_MULT_MEDIAN", "2.0"))
DEFAULT_WARN_MULT_P95 = float(os.getenv("CTX_GUARD_WARN_MULT_P95", "1.2"))


NETWORK_LABELS: Dict[int, str] = {
    1: "Ethereum Mainnet",
    5: "Goerli Testnet",
    10: "Optimism",
    137: "Polygon",
    42161: "Arbitrum One",
    8453: "Base",
    11155111: "Sepolia Testnet",
}


def network_name(cid: Optional[int]) -> str:
    if cid is None:
        return "Unknown"
    return NETWORK_LABELS.get(cid, f"Unknown (chainId {cid})")


def pct(values: List[float], q: float) -> float:
    """Return the q-th percentile (0..1) of a list of floats."""
    if not values:
        return 0.0
    q = max(0.0, min(1.0, q))
    sorted_vals = sorted(values)
    idx = int(round(q * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def connect(rpc: str, timeout: int) -> Web3:
    """Connect to RPC and print a short banner."""
    start = time.time()
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": timeout}))

    if not w3.is_connected():
        print(f"❌ Failed to connect to RPC endpoint: {rpc}", file=sys.stderr)
        sys.exit(1)

    # Some testnets / L2s use PoA
    try:
        from web3.middleware import geth_poa_middleware

        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    except Exception:
        pass

    try:
        cid = int(w3.eth.chain_id)
    except Exception:
        cid = None

    tip = w3.eth.block_number
    elapsed = time.time() - start
    print(
        f"🌐 Connected: chainId={cid} ({network_name(cid)}), tip={tip}",
        file=sys.stderr,
    )
    print(f"⚡ RPC connected in {elapsed:.2f}s", file=sys.stderr)
    return w3


def normalize_tx_hash(tx_hash: str) -> str:
    if not isinstance(tx_hash, str):
        raise ValueError("tx hash must be a string")
    tx_hash = tx_hash.strip()
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        raise ValueError(f"invalid transaction hash: {tx_hash!r}")
    return tx_hash.lower()


def sample_gas_prices(
    w3: Web3,
    blocks: int,
    step: int,
    head_override: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Scan recent blocks and collect gasPrice samples (in Gwei).

    Returns:
      {
        "head": int,
        "sampledBlocks": int,
        "gasPriceGwei": {
            "p50": ...,
            "p95": ...,
            "min": ...,
            "max": ...,
            "count": ...,
        }
      }
    """
    head = int(head_override) if head_override is not None else int(w3.eth.block_number)
    start = max(0, head - blocks + 1)

    gas_prices: List[float] = []

    print(
        f"🔍 Sampling gas prices over last {blocks} blocks (step={step})...",
        file=sys.stderr,
    )

    for n in range(head, start - 1, -step):
        blk = w3.eth.get_block(n, full_transactions=True)
        for tx in blk.transactions:
            if isinstance(tx, dict):
                gp_wei = int(tx.get("gasPrice", 0))
            else:
                gp_wei = int(getattr(tx, "gasPrice", 0))
            gas_prices.append(float(Web3.from_wei(gp_wei, "gwei")))

    if not gas_prices:
        return {
            "head": head,
            "sampledBlocks": 0,
            "gasPriceGwei": {
                "p50": 0.0,
                "p95": 0.0,
                "min": 0.0,
                "max": 0.0,
                "count": 0,
            },
        }

    return {
        "head": head,
        "sampledBlocks": len(range(head, start - 1, -step)),
        "gasPriceGwei": {
            "p50": round(median(gas_prices), 3),
            "p95": round(pct(gas_prices, 0.95), 3),
            "min": round(min(gas_prices), 3),
            "max": round(max(gas_prices), 3),
            "count": len(gas_prices),
        },
    }


def fetch_tx_and_receipt(
    w3: Web3, tx_hash: str
) -> Tuple[Optional[TxData], Optional[TxReceipt]]:
    tx = w3.eth.get_transaction(tx_hash) if w3.eth.get_transaction else None
    if tx is None:
        return None, None
    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        receipt = None
    return tx, receipt


def compute_tx_gas_price_gwei(tx: TxData) -> float:
    gp_wei = int(tx.get("gasPrice", 0)) if isinstance(tx, dict) else int(
        getattr(tx, "gasPrice", 0)
    )
    return float(Web3.from_wei(gp_wei, "gwei"))


def classify_tx_fee(
    tx_gas_price_gwei: float,
    median_gwei: float,
    p95_gwei: float,
    mult_median: float,
    mult_p95: float,
) -> str:
    """
    Return one of:
      - "ok"
      - "high_vs_median"
      - "high_vs_p95"
    """
    if median_gwei <= 0 or p95_gwei <= 0:
        # degenerate / no data case
        return "ok"

    if tx_gas_price_gwei > median_gwei * mult_median:
        return "high_vs_median"
    if tx_gas_price_gwei > p95_gwei * mult_p95:
        return "high_vs_p95"
    return "ok"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Contextual fee guard for a single transaction: compares its gasPrice "
            "against recent network gas prices (median / p95)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "tx_hash",
        help="Transaction hash (0x...) to inspect.",
    )
    p.add_argument(
        "--rpc",
        default=DEFAULT_RPC,
        help="RPC URL (default from RPC_URL env).",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="HTTP RPC timeout in seconds.",
    )
    p.add_argument(
        "--blocks",
        type=int,
        default=DEFAULT_BLOCKS,
        help="How many recent blocks to sample for gasPrice context.",
    )
    p.add_argument(
        "--step",
        type=int,
        default=DEFAULT_STEP,
        help="Sample every Nth block for speed.",
    )
    p.add_argument(
        "--warn-mult-median",
        type=float,
        default=DEFAULT_WARN_MULT_MEDIAN,
        help=(
            "Warn if tx.gasPrice > median_gas_price * this multiplier "
            "(default: 2.0)."
        ),
    )
    p.add_argument(
        "--warn-mult-p95",
        type=float,
        default=DEFAULT_WARN_MULT_P95,
        help=(
            "Warn if tx.gasPrice > p95_gas_price * this multiplier "
            "(default: 1.2)."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a human-readable summary.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    try:
        tx_hash = normalize_tx_hash(args.tx_hash)
    except Exception as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    if args.blocks <= 0 or args.step <= 0:
        print("❌ --blocks and --step must be > 0", file=sys.stderr)
        return 1

    # Soft guardrail
    if args.blocks > 10_000:
        print(
            "⚠️  Limiting --blocks to 10000 to avoid excessive RPC load.",
            file=sys.stderr,
        )
        args.blocks = 10_000

    print(
        f"📅 Contextual fee guard run at UTC: "
        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}",
        file=sys.stderr,
    )
    print(f"⚙️ Using RPC endpoint: {args.rpc}", file=sys.stderr)

    w3 = connect(args.rpc, timeout=args.timeout)

    # Fetch tx + receipt
    start_tx = time.time()
    try:
        tx = w3.eth.get_transaction(tx_hash)
    except Exception:
        tx = None
    if tx is None:
        print(f"❌ Transaction not found: {tx_hash}", file=sys.stderr)
        if args.json:
            payload = {
                "txHash": tx_hash,
                "error": "not_found",
                "network": None,
                "chainId": None,
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 2

    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        receipt = None

    elapsed_tx = time.time() - start_tx

    tx_block = int(tx.get("blockNumber", 0) if isinstance(tx, dict) else getattr(tx, "blockNumber", 0))
    tx_gas_price_gwei = compute_tx_gas_price_gwei(tx)

    # Sample context
    ctx = sample_gas_prices(w3, args.blocks, args.step, head_override=None)
    gas_ctx = ctx["gasPriceGwei"]
    median_gwei = gas_ctx["p50"]
    p95_gwei = gas_ctx["p95"]

    try:
        chain_id = int(w3.eth.chain_id)
    except Exception:
        chain_id = None

    classification = classify_tx_fee(
        tx_gas_price_gwei,
        median_gwei,
        p95_gwei,
        args.warn_mult_median,
        args.warn_mult_p95,
    )

    # Build result payload
    result: Dict[str, Any] = {
        "txHash": tx_hash,
        "chainId": chain_id,
        "network": network_name(chain_id),
        "txBlockNumber": tx_block,
        "txGasPriceGwei": round(tx_gas_price_gwei, 3),
        "contextHead": ctx["head"],
        "contextSampledBlocks": ctx["sampledBlocks"],
        "contextGasPriceGwei": gas_ctx,
        "warnMultMedian": args.warn_mult_median,
        "warnMultP95": args.warn_mult_p95,
        "classification": classification,
        "elapsedSeconds": round(elapsed_tx, 3),
    }

    # JSON mode
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if classification == "ok" else 3

    # Human-readable mode
    print(
        f"🌐 {result['network']} (chainId {result['chainId']})  "
        f"tx={tx_hash}  block={result['txBlockNumber']}"
    )
    print(
        f"⛽ Tx gasPrice: {result['txGasPriceGwei']} gwei "
        f"(ctx median={median_gwei} gwei, p95={p95_gwei} gwei)"
    )
    print(
        f"📦 Context window: {result['contextSampledBlocks']} sampled blocks "
        f"ending at head={result['contextHead']} "
        f"(blocks span ~{args.blocks}, step={args.step})"
    )
    print(
        f"🎚️  Multipliers: median×{args.warn_mult_median}, p95×{args.warn_mult_p95}"
    )

    if classification == "ok":
        print("✅ Fee classification: ok (within contextual bounds)")
        exit_code = 0
    elif classification == "high_vs_median":
        print("⚠️ Fee classification: high_vs_median")
        print(
            "   → Tx gasPrice is noticeably higher than recent median gas price. "
            "This may indicate overpayment vs recent conditions."
        )
        exit_code = 3
    elif classification == "high_vs_p95":
        print("⚠️ Fee classification: high_vs_p95")
        print(
            "   → Tx gasPrice exceeds the high-percentile (p95) range. "
            "This is likely an outlier in recent conditions."
        )
        exit_code = 3
    else:
        print(f"⚠️ Fee classification: {classification} (unexpected)")
        exit_code = 3

    print(f"⏱️  Tx fetch time: {result['elapsedSeconds']}s")
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(1)
