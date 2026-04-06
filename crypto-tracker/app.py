import io
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import seaborn as sns
from boto3.dynamodb.conditions import Key

matplotlib.use("Agg")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Configuration
# CoinGecko API IDs mapped to display symbols
COINS = {
    "bitcoin":   "BTC",
    "ethereum":  "ETH",
    "ripple":    "XRP",
    "solana":    "SOL",
    "bittensor": "TAO",
    "blockdag":  "BDAG",
}

# Brand colors per coin for consistent chart styling
COLORS = {
    "bitcoin":   "#F7931A",
    "ethereum":  "#627EEA",
    "ripple":    "#00AAE4",
    "solana":    "#9945FF",
    "bittensor": "#00BFA5",
    "blockdag":  "#FF6B35",
}


COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
TABLE_NAME    = os.environ["DYNAMODB_TABLE"]
S3_BUCKET     = os.environ["S3_BUCKET"]
AWS_REGION    = os.environ.get("AWS_REGION", "us-east-1")


# Step 1 — Fetch current prices from CoinGecko (no API key required)


def fetch_prices() -> dict:
    """Return raw CoinGecko price data for all tracked coins."""
    params = {
        "ids":                ",".join(COINS.keys()),
        "vs_currencies":      "usd",
        "include_market_cap": "true",
        "include_24hr_vol":   "true",
        "include_24hr_change": "true",
    }
    resp = requests.get(COINGECKO_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


# Step 2 — Build DynamoDB items from API response
def build_items(data: dict, timestamp: str) -> list[dict]:
    """Convert CoinGecko response into DynamoDB-ready records."""
    items = []
    for coin_id, symbol in COINS.items():
        if coin_id not in data:
            log.warning("Coin %s not found in API response — skipping", coin_id)
            continue
        d = data[coin_id]
        items.append({
            "coin_id":        coin_id,
            "timestamp":      timestamp,
            "symbol":         symbol,
            "price_usd":      Decimal(str(round(d.get("usd", 0), 8))),
            "market_cap_usd": Decimal(str(int(d.get("usd_market_cap", 0)))),
            "volume_24h_usd": Decimal(str(int(d.get("usd_24h_vol", 0)))),
            "change_24h_pct": Decimal(str(round(d.get("usd_24h_change", 0), 4))),
        })
    return items


# Step 3 — Write records to DynamoDB

def write_items(table, items: list[dict]) -> None:
    """Batch-write all coin records to DynamoDB."""
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
            log.info(
                "%s | price=$%,.4f | 24hr=%+.2f%% | mcap=$%,.0f",
                item["symbol"], item["price_usd"],
                item["change_24h_pct"], item["market_cap_usd"],
            )



# Step 4 — Fetch full history from DynamoDB for all coins


def fetch_history(table) -> pd.DataFrame:
    """Return all stored records as a DataFrame, handling DynamoDB pagination."""
    all_items = []
    for coin_id in COINS.keys():
        kwargs = dict(
            KeyConditionExpression=Key("coin_id").eq(coin_id),
            ScanIndexForward=True,
        )
        while True:
            resp = table.query(**kwargs)
            all_items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    if not all_items:
        return pd.DataFrame()

    df = pd.DataFrame(all_items)
    df["timestamp"]      = pd.to_datetime(df["timestamp"])
    df["price_usd"]      = df["price_usd"].astype(float)
    df["change_24h_pct"] = df["change_24h_pct"].astype(float)
    df["market_cap_usd"] = df["market_cap_usd"].astype(float)
    df["volume_24h_usd"] = df["volume_24h_usd"].astype(float)
    return df.sort_values(["coin_id", "timestamp"]).reset_index(drop=True)


# Step 5 — Compute risk metrics per coin


def compute_risk_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute per-coin risk metrics from price history:

    - total_return_pct : % gain/loss from first recorded price to latest
    - volatility       : std of log returns (measures price instability)
    - sharpe_ratio     : mean_return / volatility (simplified Sharpe —
                         higher = better risk-adjusted return)

    In real quant finance, Sharpe also subtracts the risk-free rate and
    annualizes both terms. Here we use the raw ratio as a relative
    comparison across coins since they share the same time window.
    """
    records = []
    for coin_id, group in df.groupby("coin_id"):
        group = group.sort_values("timestamp").reset_index(drop=True)
        if len(group) < 2:
            continue

        # Log returns: ln(P_t / P_{t-1}) — standard in quantitative finance
        group["log_return"] = np.log(
            group["price_usd"] / group["price_usd"].shift(1)
        )

        first_price  = group["price_usd"].iloc[0]
        last_price   = group["price_usd"].iloc[-1]
        total_return = ((last_price - first_price) / first_price) * 100

        returns    = group["log_return"].dropna()
        volatility = returns.std() * 100      # expressed as percentage
        mean_ret   = returns.mean() * 100
        sharpe     = mean_ret / volatility if volatility > 0 else 0.0

        records.append({
            "coin_id":          coin_id,
            "symbol":           COINS[coin_id],
            "total_return_pct": round(total_return, 4),
            "volatility":       round(volatility, 6),
            "sharpe_ratio":     round(sharpe, 4),
            "current_price":    last_price,
            "data_points":      len(group),
        })

    return pd.DataFrame(records).sort_values("total_return_pct", ascending=False).reset_index(drop=True)


# Step 6 — Generate 4-panel dashboard plot
def generate_plot(df: pd.DataFrame, metrics: pd.DataFrame) -> io.BytesIO | None:
    """
    4-panel dashboard:
      Panel 1 — Normalized price return since tracking started (line chart)
      Panel 2 — Fastest growing coin total return (bar chart)
      Panel 3 — Rolling volatility over time (line chart)
      Panel 4 — Simplified Sharpe ratio / risk-reward ranking (bar chart)
    """
    if df.empty or len(df) < 6:
        log.info("Not enough history to plot yet (%d rows)", len(df))
        return None

    sns.set_theme(style="darkgrid", context="talk", font_scale=0.85)
    fig = plt.figure(figsize=(18, 14))
    fig.patch.set_facecolor("#1a1a2e")

    fig.suptitle(
        f"Crypto Tracker Dashboard  —  {len(metrics)} coins tracked\n"
        f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        fontsize=15, fontweight="bold", color="white", y=0.98,
    )

    gs   = fig.add_gridspec(2, 2, hspace=0.45, wspace=0.35)
    ax1  = fig.add_subplot(gs[0, 0])
    ax2  = fig.add_subplot(gs[0, 1])
    ax3  = fig.add_subplot(gs[1, 0])
    ax4  = fig.add_subplot(gs[1, 1])

    panel_bg = "#16213e"
    for ax in [ax1, ax2, ax3, ax4]:
        ax.set_facecolor(panel_bg)
        ax.tick_params(colors="white")
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        ax.title.set_color("white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#444466")

    #  Panel 1: Normalized Price Return Over Time 
    for coin_id, group in df.groupby("coin_id"):
        group      = group.sort_values("timestamp")
        first      = group["price_usd"].iloc[0]
        normalized = ((group["price_usd"] - first) / first) * 100
        color      = COLORS.get(coin_id, "#aaaaaa")
        ax1.plot(group["timestamp"], normalized,
                 label=COINS[coin_id], color=color, linewidth=2.2)

    ax1.axhline(0, color="white", linestyle="--", alpha=0.25, linewidth=1)
    ax1.set_title("Return Since Tracking Started (%)")
    ax1.set_xlabel("Time (UTC)")
    ax1.set_ylabel("Return (%)")
    ax1.legend(loc="upper left", fontsize=8, framealpha=0.4)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:+.1f}%"))
    ax1.tick_params(axis="x", rotation=25)

    # --- Panel 2: Fastest Growing Bar Chart ---
    if not metrics.empty:
        bar_colors = [COLORS.get(r["coin_id"], "#aaaaaa") for _, r in metrics.iterrows()]
        bars = ax2.bar(metrics["symbol"], metrics["total_return_pct"],
                       color=bar_colors, edgecolor="#ffffff22", linewidth=0.5)
        ax2.axhline(0, color="white", linestyle="--", alpha=0.25, linewidth=1)
        ax2.set_title("Fastest Growing (Total Return %)")
        ax2.set_xlabel("Coin")
        ax2.set_ylabel("Total Return (%)")
        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:+.1f}%"))
        for bar, val in zip(bars, metrics["total_return_pct"]):
            offset = 0.05 if val >= 0 else -0.15
            ax2.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + offset,
                f"{val:+.2f}%",
                ha="center", va="bottom", fontsize=8,
                fontweight="bold", color="white",
            )

    # --- Panel 3: Rolling Volatility Over Time ---
    for coin_id, group in df.groupby("coin_id"):
        group = group.sort_values("timestamp").reset_index(drop=True)
        if len(group) < 4:
            continue
        group["log_return"]  = np.log(group["price_usd"] / group["price_usd"].shift(1))
        group["rolling_vol"] = group["log_return"].rolling(window=6).std() * 100
        color = COLORS.get(coin_id, "#aaaaaa")
        ax3.plot(group["timestamp"], group["rolling_vol"],
                 label=COINS[coin_id], color=color, linewidth=2.2)

    ax3.set_title("Rolling Volatility (6-period window)")
    ax3.set_xlabel("Time (UTC)")
    ax3.set_ylabel("Volatility (%)")
    ax3.legend(loc="upper left", fontsize=8, framealpha=0.4)
    ax3.tick_params(axis="x", rotation=25)

    # --- Panel 4: Simplified Sharpe Ratio ---
    if not metrics.empty:
        ms     = metrics.sort_values("sharpe_ratio", ascending=False)
        colors_s = [COLORS.get(r["coin_id"], "#aaaaaa") for _, r in ms.iterrows()]
        bars   = ax4.bar(ms["symbol"], ms["sharpe_ratio"],
                         color=colors_s, edgecolor="#ffffff22", linewidth=0.5)
        ax4.axhline(0, color="white", linestyle="--", alpha=0.25, linewidth=1)
        ax4.set_title("Risk/Reward — Simplified Sharpe Ratio")
        ax4.set_xlabel("Coin")
        ax4.set_ylabel("Sharpe Ratio")
        for bar, val in zip(bars, ms["sharpe_ratio"]):
            offset = 0.001 if val >= 0 else -0.005
            ax4.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + offset,
                f"{val:.3f}",
                ha="center", va="bottom", fontsize=8,
                fontweight="bold", color="white",
            )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    log.info("Dashboard plot generated (%d bytes, %d coins)", len(buf.getvalue()), len(metrics))
    return buf


# Step 7 — Push files to S3
def push_to_s3(data: bytes, key: str, content_type: str) -> None:
    s3 = boto3.client("s3", region_name=AWS_REGION)
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType=content_type)
    log.info("Uploaded %s to s3://%s", key, S3_BUCKET)



# Entry point

def main():
    dynamodb  = boto3.resource("dynamodb", region_name=AWS_REGION)
    table     = dynamodb.Table(TABLE_NAME)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Fetch and persist current prices
    raw_data = fetch_prices()
    items    = build_items(raw_data, timestamp)
    write_items(table, items)

    # Load full history and compute metrics
    history = fetch_history(table)
    if history.empty:
        log.info("No history yet — skipping plot generation")
        return

    metrics = compute_risk_metrics(history)

    if not metrics.empty:
        top = metrics.iloc[0]
        log.info(
            "Top performer: %s | return=%+.2f%% | sharpe=%.3f | volatility=%.4f%%",
            top["symbol"], top["total_return_pct"],
            top["sharpe_ratio"], top["volatility"],
        )

    # Generate and upload dashboard plot
    plot_buf = generate_plot(history, metrics)
    if plot_buf:
        push_to_s3(plot_buf.getvalue(), "plot.png", "image/png")

    # Export and upload full history as CSV
    csv_buf = io.BytesIO()
    history.to_csv(csv_buf, index=False)
    push_to_s3(csv_buf.getvalue(), "data.csv", "text/csv")


if __name__ == "__main__":
    main()
