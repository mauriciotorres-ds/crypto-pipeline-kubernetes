# Crypto Data Pipeline — Kubernetes on AWS

A production-style containerized data pipeline that tracks live cryptocurrency prices for 6 assets every 30 minutes, persists data to AWS DynamoDB, computes risk metrics, and publishes an evolving 4-panel analytics dashboard to a public S3 website — all orchestrated by Kubernetes running on EC2.

**Live Dashboard:** `http://iss-tracking-data.s3-website-us-east-1.amazonaws.com/plot.png`

---

## Architecture

```
CoinGecko API ──► Python App (Docker) ──► DynamoDB (crypto-tracking)
                        │                        │
                   K3S CronJob              fetch history
                  (every 30 min)                 │
                        │                  compute metrics
                   EC2 t3.large                  │
                  (us-east-1)             generate dashboard
                        │                        │
                   IAM Role ──────────────► S3 Static Website
                                               plot.png
                                               data.csv
```

---

## Tracked Assets

| Coin | Symbol | Category |
|------|--------|----------|
| Bitcoin | BTC | Blue chip |
| Ethereum | ETH | Blue chip |
| XRP | XRP | Blue chip |
| Solana | SOL | Blue chip |
| Bittensor | TAO | AI / emerging |
| BlockDAG | BDAG | Emerging |

---

## Dashboard Panels

The live dashboard updates every 30 minutes with 4 panels:

- **Return Since Tracking Started** — normalized % return per coin from first data point
- **Fastest Growing** — total return bar chart ranked best to worst
- **Rolling Volatility** — 6-period rolling standard deviation of log returns
- **Simplified Sharpe Ratio** — mean return / volatility, a basic risk/reward ranking

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Orchestration | Kubernetes (K3S) on AWS EC2 t3.large |
| Containerization | Docker, GHCR (GitHub Container Registry) |
| Scheduling | Kubernetes CronJob (*/30 * * * *) |
| Data Source | CoinGecko API (no key required) |
| Persistence | AWS DynamoDB (partition: coin_id, sort: timestamp) |
| Visualization | Python — matplotlib, seaborn, pandas, numpy |
| Storage / Hosting | AWS S3 Static Website |
| Auth | AWS IAM Role (no hardcoded credentials) |

---

## Repository Structure

```
├── crypto-tracker/          # Your data pipeline (main deliverable)
│   ├── app.py               # Pipeline script — fetch, store, plot, publish
│   ├── Dockerfile           # Container definition
│   └── requirements.txt     # Python dependencies
│
├── iss-reboost/             # Professor's sample pipeline (ISS tracker)
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── k8s/                     # Kubernetes manifests
│   ├── crypto-job.yaml      # CronJob for crypto tracker (every 30 min)
│   ├── iss-job.yaml         # CronJob for ISS tracker (every 15 min)
│   └── simple-job.yaml      # Test CronJob used during setup
│
├── screenshots/             # Dashboard snapshots over the 5-day run
│
├── docs/
│   └── project-instructions.md   # Original assignment README
│
└── README.md                # This file
```

---

## How It Works

Each pod run executes `app.py` which does the following in sequence:

1. Calls the CoinGecko `/simple/price` endpoint for all 6 coins
2. Writes price, market cap, 24hr volume, and 24hr change to DynamoDB
3. Queries the full price history from DynamoDB
4. Computes log returns, rolling volatility, and a simplified Sharpe ratio per coin
5. Generates a 4-panel matplotlib dashboard and uploads it to S3 as `plot.png`
6. Exports the full history as `data.csv` and uploads it to S3

The EC2 instance's IAM role grants S3 and DynamoDB access — no credentials appear anywhere in the code or YAML files.

---

## Key Concepts Demonstrated

**Kubernetes Secrets vs Plain Env Vars** — The S3 bucket name is passed as a plain environment variable in the CronJob YAML since it is not sensitive. API keys (if required) would be stored as Kubernetes Secrets and injected as env vars so the value never appears in any file on disk.

**How Pods Get AWS Permissions** — The EC2 instance has an IAM Role attached. When a pod runs, boto3 automatically discovers credentials from the instance metadata service (IMDS). No `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` is needed anywhere.

**Sharpe Ratio** — A standard risk-adjusted return metric from quantitative finance. Here computed as `mean(log_returns) / std(log_returns)` per coin over the tracking window. Higher = better return per unit of risk.

**Log Returns** — `ln(P_t / P_{t-1})` — the standard way to measure price changes in finance because they are time-additive and symmetrical, unlike simple percentage returns.

---

## Setup (Reproducibility)

Full setup instructions are in [`docs/project-instructions.md`](docs/project-instructions.md). At a high level:

1. Create an S3 bucket with static website hosting enabled
2. Launch an EC2 t3.large (Ubuntu 24.04) with an IAM Role for S3 + DynamoDB
3. Install K3S: `curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644`
4. Create the DynamoDB table: `aws dynamodb create-table --table-name crypto-tracking ...`
5. Build and push the Docker image to GHCR
6. Apply the CronJob: `kubectl apply -f k8s/crypto-job.yaml`

---

## Author

Mauricio Torres — DS5220 Cloud Computing, Spring 2026
