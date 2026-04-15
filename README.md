# Crypto Data Pipeline — Kubernetes on AWS

A production-style containerized data pipeline that tracks 6 live cryptocurrency prices every 30 minutes, persists time series data to DynamoDB, computes quantitative finance metrics, and publishes an auto updating 4 panel analytics dashboard to a public S3 website all orchestrated by Kubernetes running on EC2.

---

## Final Dashboard

![Final Dashboard](screenshots/DataProjectPNGS/dashboard_final.png)

> 5 day tracking window (Apr 6–11, 2026) · 1,513 data points across 6 coins · 30-minute polling interval

---

## Architecture

```
CoinGecko API ──► Python App (Docker) ──► DynamoDB (crypto-tracking)
                        │                        │
                   K3S CronJob              fetch full history
                  (every 30 min)                 │
                   EC2 t3.large            compute metrics
                   (us-east-1)                   │
                        │                 generate dashboard
                   IAM Role ──────────────► S3 Static Website
                                               plot.png
                                               data.csv
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Orchestration | Kubernetes (K3S) on AWS EC2 t3.large |
| Containerization | Docker, GHCR (GitHub Container Registry) |
| Scheduling | Kubernetes CronJob (`*/30 * * * *`) |
| Data Source | CoinGecko API (no key required) |
| Persistence | AWS DynamoDB (partition: `coin_id`, sort: `timestamp`) |
| Computation | Python, pandas, numpy (log returns, Sharpe ratio, rolling volatility) |
| Visualization | matplotlib, seaborn (4-panel dark-themed dashboard) |
| Storage / Hosting | AWS S3 Static Website |
| Auth | AWS IAM Role attached to EC2, zero hardcoded credentials |

---

## How It Works

Each pod run executes `app.py` in sequence:

1. **Fetch** — call CoinGecko `/simple/price` for 6 coins
2. **Persist** — batch write price, market cap, volume, and 24h change to DynamoDB
3. **Query** — paginate full price history back from DynamoDB
4. **Compute** — calculate per-coin: log returns, rolling volatility (6-period), and simplified Sharpe ratio
5. **Visualize** — generate 4-panel matplotlib dashboard
6. **Publish** — upload `plot.png` and `data.csv` to S3 for public access

The EC2 instance's IAM role grants S3 and DynamoDB access no credentials appear anywhere in code or YAML.

---

## Dashboard Panels

| Panel | Chart Type | Metric |
|-------|-----------|--------|
| Return Since Tracking Started | Line | Normalized % return from first data point |
| Fastest Growing | Bar (ranked) | Total return % per coin |
| Rolling Volatility | Line | 6-period rolling std of log returns |
| Simplified Sharpe Ratio | Bar (ranked) | `mean(log returns) / std(log returns)` |

---

## Dashboard Evolution

The dashboard regenerates on every pod run. These snapshots show how the picture changed over the 5-day tracking window.

| Day 1 (Early) | Day 2 (Morning) | Final (Day 5) |
|:---:|:---:|:---:|
| ![Day 1 Early](screenshots/DataProjectPNGS/dashboard_day1_early.png) | ![Day 2 Morning](screenshots/DataProjectPNGS/dashboard_day2_morning.png) | ![Final](screenshots/DataProjectPNGS/dashboard_final.png) |

**Key observation:** BlockDAG (BDAG) dropped ~70% over the tracking window, completely dominating the volatility and Sharpe ratio panels. The five remaining coins (BTC, ETH, XRP, SOL, TAO) appeared nearly stable by comparison, which made them look stable just because BDAG was so extreme. That contrast actually ended up being one of the more interesting things to look at in the dashboard. It's a textbook illustration of how one high variance outlier skews risk metrics.

---

## Sample Data

`final_data.csv` — 1,513 rows · 9 columns · 6 coins · ~30-minute intervals · Apr 6–11, 2026

**Opening snapshot (Apr 6, 2026 — 14:36 UTC):**

| Coin | Symbol | Price (USD) | Market Cap | 24h Volume | 24h Change |
|------|--------|------------|------------|------------|------------|
| Bitcoin | BTC | $69,618 | $1.39T | $40.0B | +4.05% |
| Ethereum | ETH | $2,156 | $260B | $16.5B | +5.64% |
| XRP | XRP | $1.35 | $82.9B | $2.06B | +4.81% |
| Solana | SOL | $82.43 | $47.2B | $2.94B | +3.93% |
| Bittensor | TAO | $324.74 | $3.12B | $284M | +9.31% |
| BlockDAG | BDAG | $0.00123 | — | $990K | -8.75% |

**Raw CSV schema:**

```
market_cap_usd, price_usd, volume_24h_usd, symbol, coin_id, change_24h_pct, timestamp
1392630333519.0, 69618.0, 39972482605.0, BTC, bitcoin, 4.0458, 2026-04-06 14:36:03+00:00
1388728686297.0, 69394.0, 41229427172.0, BTC, bitcoin, 3.659,  2026-04-06 14:40:48+00:00
...
```

The full CSV is available at [`final_data.csv`](final_data.csv).

---

## Key Concepts Demonstrated

**IAM Role-based Auth** — The EC2 instance has an IAM Role attached. When a pod runs, `boto3` discovers credentials automatically from the instance metadata service (IMDS). No `AWS_ACCESS_KEY_ID` or secret appears anywhere in code or YAML.

**Kubernetes Secrets vs Plain Env Vars** — Non sensitive config like `S3_BUCKET` is passed as a plain env var in the CronJob YAML. Sensitive values (e.g. API keys) would be stored as Kubernetes Secrets and injected at runtime they never touch the file system or source control.

**DynamoDB Partition Design** — `coin_id` as partition key spreads writes across 6 partitions, avoiding the hot-partition problem. `timestamp` as sort key enables efficient time-range queries and ordered results. Pagination via `ExclusiveStartKey` handles arbitrarily large result sets.

**Log Returns** — `ln(P_t / P_{t-1})` — standard in quantitative finance because they are time-additive and symmetrical, unlike simple percentage returns.

**Sharpe Ratio** — `mean(log_returns) / std(log_returns)` — a basic risk-adjusted return metric. Higher = better return per unit of volatility.

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

## Repository Structure

```
├── crypto-tracker/          # Main deliverable — data pipeline
│   ├── app.py               # ETL: fetch → store → compute → visualize → publish
│   ├── Dockerfile           # Python 3.12-slim container
│   └── requirements.txt
│
├── iss-reboost/             # Reference pipeline (professor's ISS tracker)
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
│
├── k8s/                     # Kubernetes manifests
│   ├── crypto-job.yaml      # CronJob — crypto tracker (every 30 min)
│   ├── iss-job.yaml         # CronJob — ISS tracker (every 15 min)
│   └── simple-job.yaml      # Test CronJob (busybox hello world)
│
├── screenshots/
│   └── DataProjectPNGS/     # Dashboard snapshots over the tracking window
│
├── final_data.csv           # Exported time-series data (1,513 rows)
├── docs/
│   └── project-instructions.md
└── README.md
```

---

## Setup

Full instructions: [`docs/project-instructions.md`](docs/project-instructions.md)

```bash
# 1. Launch EC2 t3.large (Ubuntu 24.04) with IAM Role for S3 + DynamoDB
# 2. Install K3S
curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644

# 3. Create DynamoDB table
aws dynamodb create-table --table-name crypto-tracking \
  --attribute-definitions AttributeName=coin_id,AttributeType=S AttributeName=timestamp,AttributeType=S \
  --key-schema AttributeName=coin_id,KeyType=HASH AttributeName=timestamp,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST

# 4. Build and push Docker image to GHCR
docker build -t ghcr.io/<user>/crypto-tracker:latest ./crypto-tracker
docker push ghcr.io/<user>/crypto-tracker:latest

# 5. Deploy
kubectl apply -f k8s/crypto-job.yaml
```

---

<details>
<summary>Canvas Quiz — Reflection &amp; Graduate Questions</summary>

### Reflection Questions

**Which data source did you choose and why?**

I chose CoinGecko as my data source because I'm genuinely interested in the crypto space and where it's headed. I've always wanted to find a way to combine data science and quantitative methods with financial markets, and this project felt like a natural first step toward that. Being able to pull live price data, store it, and start building analytics around it using cloud infrastructure on top of that is exactly the kind of thing I want to keep building on after this class.

**What did you observe in the data — any patterns, spikes, or surprises over the tracking window?**

The biggest thing I noticed was what happened with BlockDAG (BDAG). It had a brief run of small gains early on, then experienced a pretty dramatic crash roughly 70% down over the tracking window. You can see it clearly in the dashboard: it completely dominates the volatility chart and tanks the Sharpe ratio. The other five coins (BTC, ETH, XRP, SOL, TAO) stayed relatively flat or slightly negative in comparison, which almost made them look stable just because BDAG was so extreme. That contrast actually ended up being one of the more interesting things to look at in the dashboard.

**How do Kubernetes Secrets differ from plain environment variables, and why does that distinction matter?**

Plain environment variables are values you write directly into the manifest YAML like I did with `S3_BUCKET` and `AWS_REGION` in my CronJob. That's fine for non-sensitive config because it shows up in the file and gets committed to the repo. Kubernetes Secrets are different — they're stored separately inside the cluster, base64-encoded, and injected into pods at runtime so the actual value never has to appear in any file on disk or in source control. The distinction matters because if you stored something like an API key or a database password as a plain env var, it would end up in your YAML, your repo history, and anywhere else that file lives. With a Secret, the sensitive value stays inside the cluster and you just reference it by name in the manifest.

**How do your CronJob pods get permission to read/write to AWS services without credentials appearing anywhere?**

The EC2 instance running the cluster has an IAM Role attached to it. That role has a policy that grants specific permissions to S3 and DynamoDB. When a pod runs on that instance, `boto3` automatically reaches out to the instance metadata service (IMDS) to retrieve temporary credentials scoped to that role. There's no `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` anywhere in the code or YAML, the permissions just flow through the role.

**One thing you would do differently if building this for a real production system.**

A couple things honestly. First, I'd make sure each pipeline has its own dedicated S3 bucket. In this project I ended up using the same bucket for both the ISS tracker and the crypto tracker, which worked but isn't clean. For a real system you'd want clear separation. Second, I'd move away from a static PNG dashboard toward something more interactive something stakeholders could actually filter and explore rather than just a snapshot image. That'd probably cost more to host but it'd be a lot more useful in practice.

---

### Graduate Questions

**1. If the ISS application were running at much higher frequency (hundreds of writes per minute), what changes would you make to the persistence strategy?**

At that kind of frequency, writing directly to DynamoDB on every pod run would start to create problems you'd either hit throughput limits or rack up a lot of cost fast. I'd probably introduce a buffer layer, something like SQS or Kinesis, so writes get queued and processed in batches rather than one at a time. On the DynamoDB side I'd switch to on demand capacity mode or set up provisioned capacity with auto scaling so it can handle sudden spikes without throttling. I'd also want some kind of alerting CloudWatch alarms or similar so that if write failures or unusual patterns start showing up, the right people get notified before it becomes a bigger problem.

**2. Describe at least one way the orbital burn detection logic could produce a false positive, and how you would make it more robust.**

The current logic flags a burn whenever altitude increases by 1 km or more in a single 15 minute interval. One clear way that could fire incorrectly is sensor noise or a bad reading from the API. For example, if the `wheretheiss.at` service returns an outlier value, the delta could easily cross the threshold even if nothing actually happened. Another scenario is atmospheric variation causing a brief, non-burn altitude fluctuation that just happens to hit the threshold. To make it more robust I'd require the altitude gain to be sustained across multiple consecutive readings rather than just one, and ideally cross-reference with velocity data so a real reboost should show a correlated increase in orbital velocity, not just altitude. Adding a rolling average and flagging only deviations above a certain number of standard deviations from recent history would help filter out noise as well.

**3. How does each CronJob pod get AWS permissions without credentials being passed into the container?**

The EC2 node running the cluster has an IAM Role attached at the instance level. When a pod starts, `boto3` automatically queries the AWS Instance Metadata Service (IMDS) at `169.254.169.254` to retrieve short-lived, rotating credentials scoped to that role. The pod never needs to know any access keys, it inherits permissions through the role assigned to the host node. This is the standard pattern for giving EC2-hosted workloads AWS access without hardcoding credentials anywhere.

**4. What are the partition key and sort key of the `iss-tracking` DynamoDB table, and why do they work here but might not elsewhere?**

The partition key is `satellite_id` and the sort key is `timestamp`. This works well for the ISS use case because there's only one satellite being tracked, so all records share the same partition key and the sort key lets you query by time range and get results back in order. The problem is that having a single partition key means all reads and writes go to one partition, what DynamoDB calls a "hot partition." For a single satellite running every 15 minutes that's totally fine, but if you scaled this to tracking hundreds of satellites with high frequency writes, you'd hammer one partition and hit throughput limits. A better key design for a larger system might include something like a date prefix in the partition key to spread the load, or use a different primary key structure entirely depending on the access patterns.

</details>

---

## Author

Mauricio Torres — DS5220 Advanced Cloud Computing
