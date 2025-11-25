#!/usr/bin/env python3
import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from web3 import Web3
from web3.exceptions import TransactionNotFound
# Exit codes
EXIT_OK = 0
EXIT_INVALID_TX = 1
EXIT_TX_NOT_FOUND = 2
EXIT_HIGH_FEE = 3


@dataclass
class TxRiskReport:
    tx_hash: str
    chain_id: Optional[int]
    network_label: str
    from_addr: Optional[str]
    to_addr: Optional[str]
    status: Optional[int]
    block_number: Optional[int]
    timestamp_utc: Optional[str]
    confirmations: Optional[int]
    gas_used: Optional[int]
    gas_price_wei: Optional[int]
    total_fee_wei: Optional[int]
    total_fee_eth: Optional[float]
    fee_threshold_eth: float
    high_fee: bool
    pending: bool
    error: Optional[str]


NETWORKS: Dict[int, str] = {
    1: "Ethereum Mainnet",
    11155111: "Ethereum Sepolia",
    10: "Optimism",
    137: "Polygon",
    42161: "Arbitrum One",
    8453: "Base",
}


def network_name(chain_id: Optional[int]) -> str:
    if chain_id is None:
        return "Unknown network"
    return NETWORKS.get(chain_id, f"Chain {chain_id}")


def fmt_utc(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")


def is_tx_hash(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if not value.startswith("0x") or len(value) != 66:
        return False
    try:
        int(value[2:], 16)
    except ValueError:
        return False
    return True


def connect(rpc_url: str, timeout: int) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))
    if not w3.is_connected():
        print(f"❌ Failed to connect to RPC endpoint: {rpc_url}", file=sys.stderr)
        sys.exit(1)
    return w3


def build_report(
    w3: Web3,
    tx_hash: str,
    fee_threshold_eth: float,
) -> TxRiskReport:
    chain_id: Optional[int]
    try:
        chain_id = w3.eth.chain_id
    except Exception:
        chain_id = None

    try:
        tx = w3.eth.get_transaction(tx_hash)
    except TransactionNotFound:
        return TxRiskReport(
            tx_hash=tx_hash,
            chain_id=chain_id,
            network_label=network_name(chain_id),
            from_addr=None,
            to_addr=None,
            status=None,
            block_number=None,
            timestamp_utc=None,
            confirmations=None,
            gas_used=None,
            gas_price_wei=None,
            total_fee_wei=None,
            total_fee_eth=None,
            fee_threshold_eth=fee_threshold_eth,
            high_fee=False,
            pending=False,
            error="transaction not found",
        )

    if tx.blockNumber is None:
        return TxRiskReport(
            tx_hash=tx_hash,
            chain_id=chain_id,
            network_label=network_name(chain_id),
            from_addr=tx.get("from"),
            to_addr=tx.get("to"),
            status=None,
            block_number=None,
            timestamp_utc=None,
            confirmations=None,
            gas_used=None,
            gas_price_wei=None,
            total_fee_wei=None,
            total_fee_eth=None,
            fee_threshold_eth=fee_threshold_eth,
            high_fee=False,
            pending=True,
            error=None,
        )

    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except TransactionNotFound:
        # Very rare race: included but receipt unavailable
        return TxRiskReport(
            tx_hash=tx_hash,
            chain_id=chain_id,
            network_label=network_name(chain_id),
            from_addr=tx.get("from"),
            to_addr=tx.get("to"),
            status=None,
            block_number=tx.blockNumber,
            timestamp_utc=None,
            confirmations=None,
            gas_used=None,
            gas_price_wei=None,
            total_fee_wei=None,
            total_fee_eth=None,
            fee_threshold_eth=fee_threshold_eth,
            high_fee=False,
            pending=True,
            error="receipt not yet available",
        )

    block = w3.eth.get_block(receipt.blockNumber)
    latest_block = w3.eth.block_number

    gas_used: Optional[int] = receipt.gasUsed
    gas_price_wei: Optional[int] = getattr(receipt, "effectiveGasPrice", None)
    if gas_price_wei is None:
        gas_price_wei = getattr(receipt, "gasPrice", None)

    total_fee_wei: Optional[int] = None
    total_fee_eth: Optional[float] = None
    high_fee = False

    if gas_used is not None and gas_price_wei is not None:
        total_fee_wei = gas_used * gas_price_wei
        total_fee_eth = float(Web3.from_wei(total_fee_wei, "ether"))
        high_fee = total_fee_eth > fee_threshold_eth

    confirmations = max(0, int(latest_block) - int(receipt.blockNumber)) if latest_block is not None else None

    return TxRiskReport(
        tx_hash=tx_hash,
        chain_id=chain_id,
        network_label=network_name(chain_id),
        from_addr=tx.get("from"),
        to_addr=tx.get("to"),
        status=getattr(receipt, "status", None),
        block_number=receipt.blockNumber,
        timestamp_utc=fmt_utc(block.timestamp if block is not None else None),
        confirmations=confirmations,
        gas_used=gas_used,
        gas_price_wei=gas_price_wei,
        total_fee_wei=total_fee_wei,
        total_fee_eth=total_fee_eth,
        fee_threshold_eth=fee_threshold_eth,
        high_fee=high_fee,
        pending=False,
        error=None,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="web3_tx_fee_guard",
        description=(
            "Inspect a Web3 transaction fee and basic soundness signals. "
            "Conceptually inspired by privacy rollups (Aztec), FHE stacks (Zama), "
            "and soundness-focused protocol design."
        ),
    )
    parser.add_argument("tx_hash", help="Transaction hash (0x + 64 hex chars).")
    parser.add_argument(
        "--rpc",
        required=True,
        help="Ethereum-compatible HTTP RPC endpoint.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="RPC timeout in seconds (default: 15).",
    )
    parser.add_argument(
        "--warn-fee-eth",
        type=float,
        default=0.05,
        help="Warn if fee exceeds this value in ETH (default: 0.05).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of human-readable report.",
    )
    return parser.parse_args()


def print_human(report: TxRiskReport, elapsed: float) -> None:
    if report.error == "transaction not found":
        print(f"❌ Transaction not found on {report.network_label}: {report.tx_hash}")
        return

    if report.pending:
        print(f"⏳ Transaction is pending on {report.network_label}: {report.tx_hash}")
        if report.from_addr:
            print(f"From: {report.from_addr}")
        if report.to_addr:
            print(f"To:   {report.to_addr}")
        print(f"Elapsed: {elapsed:.2f}s")
        return

    print("🔍 web3_tx_fee_guard")
    print(f"Network      : {report.network_label}")
    print(f"Chain ID     : {report.chain_id}")
    print(f"Tx Hash      : {report.tx_hash}")
    print(f"From         : {report.from_addr}")
    print(f"To           : {report.to_addr or '(contract creation)'}")

    status_str = "success" if report.status == 1 else "failed"
    print(f"Status       : {status_str}")
    print(f"Block        : {report.block_number}")
    print(f"Timestamp    : {report.timestamp_utc}")
    print(f"Confirmations: {report.confirmations}")

    print("")
    print("Gas / Fee")
    print(f"  Gas used        : {report.gas_used}")
    if report.gas_price_wei is not None:
        print(f"  Gas price (gwei): {Web3.from_wei(report.gas_price_wei, 'gwei'):.2f}")
    else:
        print("  Gas price (gwei): unknown")

    if report.total_fee_eth is not None:
        print(f"  Total fee (ETH) : {report.total_fee_eth:.6f}")
        print(f"  Threshold (ETH) : {report.fee_threshold_eth:.6f}")
        if report.high_fee:
            print("  Fee risk        : ⚠️ high (exceeds threshold)")
        else:
            print("  Fee risk        : ✅ within threshold")
    else:
        print("  Total fee       : unknown")

    print("")
    print(f"Elapsed      : {elapsed:.2f}s")
    print("")
    print("Note: This tool does not modify the chain or send transactions. "
          "It is intended as a lightweight fee and soundness sanity check on top of Web3 clients, "
          "and can be combined with zk / FHE / soundness-focused workflows in projects like "
          "Aztec, Zama, and formal verification labs.")


def main() -> int:
    args = parse_args()

    tx_hash = args.tx_hash.strip()
    if not is_tx_hash(tx_hash):
        print("❌ Invalid transaction hash format. Expected 0x + 64 hex characters.", file=sys.stderr)
        return 1

    start = time.time()
    w3 = connect(args.rpc, args.timeout)
    report = build_report(w3, tx_hash, args.warn_fee_eth)
    elapsed = time.time() - start

    if args.json:
        payload: Dict[str, Any] = asdict(report)
        payload["elapsedSeconds"] = round(elapsed, 3)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_human(report, elapsed)

    if report.error == "transaction not found":
        return 2
    if report.high_fee:
        return 3
    return 0


    if report.error == "transaction not found":
        return EXIT_TX_NOT_FOUND
    if report.high_fee:
        return EXIT_HIGH_FEE
    return EXIT_OK

