# web3_tx_fee_guard

This repository contains a minimal CLI tool called web3_tx_fee_guard.  
It inspects a single Web3 transaction through an RPC endpoint and classifies its fee as within or above a configurable threshold, while also showing basic soundness signals such as status, block, confirmations, and network.


## Repository layout

Core tool:

  * `app.py` – main `web3_tx_fee_guard` CLI.

Companion scripts:

  * `batch_fee_guard.py` – batch fee guard for multiple tx hashes.
  * `quick_tx_fee.py` – very small helper to print fee info for one tx using an RPC from `RPC_URL`.
  * `watch_tx_fee.py` – watch a pending tx until mined and check its fee.

Docs:

  * `README.md` – this documentation file.



## Concept and relation to Aztec, Zama, and soundness

The tool is inspired by several directions in modern Web3 work.

- Aztec style rollups focus on privacy and on chain cost of zero knowledge proofs.
- Zama style stacks explore fully homomorphic encryption and its impact on performance.
- Soundness oriented labs emphasize correctness and verification of protocol behavior.

web3_tx_fee_guard does not implement zk proofs or FHE itself. Instead it sits at the infrastructure edge, giving you a quick sanity check on how expensive a given transaction actually was and whether it looks structurally sound on chain, which can be combined with more advanced pipelines in Aztec, Zama, or soundness first systems.



## What the script does

Given a transaction hash and an RPC URL, web3_tx_fee_guard

- Connects to the given Ethereum compatible RPC endpoint.
- Validates and normalizes the transaction hash.
- Fetches the transaction and its receipt.
- Detects whether the transaction is
  - missing
  - pending
  - included with success or failure
- Fetches the block and computes
  - network label and chain ID
  - timestamp in UTC
  - latest block number and confirmations
- Computes fee metrics
  - gas used
  - gas price in gwei
  - total fee in ETH
  - comparison of total fee against a threshold you choose

It then prints either a human readable report or a JSON document suitable for logging or pipelines.



## Installation

### Requirements:

- Python 3.10 or newer
- web3 Python package

You can install the required dependency with a single command such as

pip install web3

Place app.py and this README.md file into the root of your GitHub repository.



## Usage

Run the script from the project root.

Basic usage with a mainnet RPC

python app.py 0x... --rpc https://mainnet.infura.io/v3/YOUR_KEY

Set a custom fee warning threshold

python app.py 0x... --rpc https://mainnet.infura.io/v3/YOUR_KEY --warn-fee-eth 0.01

Use a different EVM chain

python app.py 0x... --rpc https://polygon-rpc.com --warn-fee-eth 0.002



## JSON mode

To integrate the tool with monitoring systems or other scripts, request JSON output

python app.py 0x... --rpc https://mainnet.infura.io/v3/YOUR_KEY --json

### The JSON document includes fields such as

- tx_hash
- chain_id
- network_label
- from_addr
- to_addr
- status
- block_number
- timestamp_utc
- confirmations
- gas_used
- gas_price_wei
- total_fee_wei
- total_fee_eth
- fee_threshold_eth
- high_fee
- pending
- error
- elapsedSeconds



## Exit codes

web3_tx_fee_guard uses simple exit codes so you can plug it into CI or automation

- 0 means ok, transaction found and fee is within threshold
- 1 means invalid input or connection error
- 2 means transaction not found on the given endpoint
- 3 means the transaction fee exceeded the configured threshold



## Notes and limitations

- The script never sends or signs transactions, it is fully read only.
- It relies on the accuracy of the backing RPC endpoint.
- Computed fees and confirmations are a point in time snapshot.
- Thresholds are up to you and should reflect your own cost and soundness assumptions.

You can extend this repository by adding more network labels, integrating with rollup explorers, or feeding the JSON report into larger soundness and privacy analysis tools built around Aztec, Zama, or formally verified Web3 systems.
