from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import spearmanr


ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = ROOT / "data" / "raw" / "factor_return_panel.csv"
PROCESSED_DIR = ROOT / "data" / "processed"
FIGURE_DIR = ROOT / "reports" / "figures"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
FIGURE_DIR.mkdir(parents=True, exist_ok=True)


FACTOR_COL = "momentum_60_5"
RET_COL = "next_ret"
BENCH_COL = "benchmark_ret"
DATE_COL = "signal_date"
N_GROUPS = 5


def load_panel() -> pd.DataFrame:
    if not RAW_PATH.exists():
        raise FileNotFoundError(f"Cannot find file: {RAW_PATH}")

    df = pd.read_csv(RAW_PATH, encoding="utf-8-sig")

    required_cols = {
        "signal_date",
        "entry_date",
        "exit_date",
        "code",
        FACTOR_COL,
        RET_COL,
        BENCH_COL,
    }

    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(f"Missing columns: {missing_cols}")

    df[DATE_COL] = pd.to_datetime(df[DATE_COL])
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["exit_date"] = pd.to_datetime(df["exit_date"])

    df = df.dropna(subset=[FACTOR_COL, RET_COL])
    df = df.sort_values([DATE_COL, "code"]).reset_index(drop=True)

    return df


def assign_groups_one_month(group: pd.DataFrame) -> pd.DataFrame:
    """
    对每个月的股票按因子值分成5组：
    G1 = 动量最低组
    G5 = 动量最高组
    """
    group = group.copy()

    if len(group) < N_GROUPS:
        group["factor_group"] = np.nan
        return group

    # 用 rank(method='first') 避免 qcut 因重复值过多而报错
    rank_value = group[FACTOR_COL].rank(method="first", ascending=True)

    labels = [f"G{i}" for i in range(1, N_GROUPS + 1)]
    group["factor_group"] = pd.qcut(rank_value, q=N_GROUPS, labels=labels)

    return group


def calculate_rank_ic(group: pd.DataFrame) -> float:
    """
    RankIC = 当期因子值 与 下一期收益率 的 Spearman 秩相关。
    """
    x = group[FACTOR_COL]
    y = group[RET_COL]

    if len(group) < 20:
        return np.nan

    if x.nunique() <= 1 or y.nunique() <= 1:
        return np.nan

    return spearmanr(x, y).correlation


def max_drawdown(net_value: pd.Series) -> float:
    running_max = net_value.cummax()
    drawdown = net_value / running_max - 1
    return drawdown.min()


def performance_summary(returns: pd.Series, name: str) -> dict:
    """
    月度收益序列绩效指标。
    """
    returns = returns.dropna()

    if len(returns) == 0:
        return {
            "name": name,
            "months": 0,
            "total_return": np.nan,
            "annual_return": np.nan,
            "annual_volatility": np.nan,
            "sharpe": np.nan,
            "max_drawdown": np.nan,
            "win_rate": np.nan,
        }

    net_value = (1 + returns).cumprod()

    total_return = net_value.iloc[-1] - 1
    annual_return = net_value.iloc[-1] ** (12 / len(returns)) - 1
    annual_volatility = returns.std(ddof=1) * np.sqrt(12)

    if annual_volatility == 0 or np.isnan(annual_volatility):
        sharpe = np.nan
    else:
        sharpe = annual_return / annual_volatility

    return {
        "name": name,
        "months": len(returns),
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown(net_value),
        "win_rate": (returns > 0).mean(),
    }


def main():
    panel = load_panel()

    print("=" * 80)
    print("Basic information")
    print("=" * 80)
    print(panel.head())
    print()
    print("Shape:", panel.shape)
    print("Date range:", panel[DATE_COL].min().date(), "to", panel[DATE_COL].max().date())
    print("Number of stocks:", panel["code"].nunique())
    print("Average stocks per month:", panel.groupby(DATE_COL)["code"].nunique().mean())
    print()

    print("=" * 80)
    print("Missing values")
    print("=" * 80)
    print(panel.isna().sum())
    print()

    # 1. 分组
    panel_grouped = (
        panel.groupby(DATE_COL, group_keys=False)
        .apply(assign_groups_one_month)
        .dropna(subset=["factor_group"])
    )

    panel_grouped.to_csv(
        PROCESSED_DIR / "panel_with_factor_group.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # 2. 分组收益
    group_return = (
        panel_grouped
        .groupby([DATE_COL, "factor_group"], observed=False)[RET_COL]
        .mean()
        .unstack()
        .sort_index()
    )

    group_return.to_csv(
        PROCESSED_DIR / "monthly_group_return.csv",
        encoding="utf-8-sig",
    )

    average_group_return = group_return.mean().to_frame("average_monthly_return")
    average_group_return["annualized_simple"] = average_group_return["average_monthly_return"] * 12

    print("=" * 80)
    print("Average group return")
    print("=" * 80)
    print(average_group_return)
    print()

    # 3. RankIC
    rank_ic = panel.groupby(DATE_COL).apply(calculate_rank_ic)
    rank_ic.name = "rank_ic"
    rank_ic.to_csv(
        PROCESSED_DIR / "monthly_rank_ic.csv",
        encoding="utf-8-sig",
    )

    print("=" * 80)
    print("RankIC summary")
    print("=" * 80)
    print(rank_ic.describe())
    print("RankIC mean:", rank_ic.mean())
    print("RankIC positive ratio:", (rank_ic > 0).mean())
    print()

    # 4. 第一版策略：买入 G5，也就是动量最高20%
    strategy_ret = group_return["G5"].dropna()
    low_momentum_ret = group_return["G1"].dropna()
    long_short_ret = (group_return["G5"] - group_return["G1"]).dropna()

    benchmark_ret = (
        panel.groupby(DATE_COL)[BENCH_COL]
        .first()
        .reindex(strategy_ret.index)
        .dropna()
    )

    # 对齐
    common_index = strategy_ret.index.intersection(benchmark_ret.index)
    strategy_ret = strategy_ret.loc[common_index]
    benchmark_ret = benchmark_ret.loc[common_index]
    low_momentum_ret = low_momentum_ret.reindex(common_index)
    long_short_ret = long_short_ret.reindex(common_index)

    perf = pd.DataFrame([
        performance_summary(strategy_ret, "G5_top_momentum"),
        performance_summary(low_momentum_ret, "G1_low_momentum"),
        performance_summary(long_short_ret, "G5_minus_G1"),
        performance_summary(benchmark_ret, "benchmark_CSI500"),
    ])

    perf.to_csv(
        PROCESSED_DIR / "performance_summary_no_cost.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("=" * 80)
    print("Performance summary, no transaction cost")
    print("=" * 80)
    print(perf)
    print()

    # 5. 画图：分组净值
    group_net_value = (1 + group_return).cumprod()

    plt.figure(figsize=(10, 6))
    for col in group_net_value.columns:
        plt.plot(group_net_value.index, group_net_value[col], label=col)

    plt.title("Cumulative Net Value by Momentum Groups")
    plt.xlabel("Date")
    plt.ylabel("Net Value")
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIGURE_DIR / "group_net_value.png", dpi=300)
    plt.close()

    # 6. 画图：策略 vs 基准
    strategy_net_value = (1 + strategy_ret).cumprod()
    benchmark_net_value = (1 + benchmark_ret).cumprod()

    plt.figure(figsize=(10, 6))
    plt.plot(strategy_net_value.index, strategy_net_value, label="Top Momentum 20%")
    plt.plot(benchmark_net_value.index, benchmark_net_value, label="CSI500 Benchmark")
    plt.title("Top Momentum Portfolio vs CSI500 Benchmark")
    plt.xlabel("Date")
    plt.ylabel("Net Value")
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIGURE_DIR / "strategy_vs_benchmark_no_cost.png", dpi=300)
    plt.close()

    # 7. 画图：RankIC
    plt.figure(figsize=(10, 6))
    plt.plot(rank_ic.index, rank_ic.values, label="Monthly RankIC")
    plt.axhline(rank_ic.mean(), linestyle="--", label="Mean RankIC")
    plt.title("Monthly RankIC of Momentum Factor")
    plt.xlabel("Date")
    plt.ylabel("RankIC")
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIGURE_DIR / "monthly_rank_ic.png", dpi=300)
    plt.close()

    print("=" * 80)
    print("Files saved")
    print("=" * 80)
    print(PROCESSED_DIR / "panel_with_factor_group.csv")
    print(PROCESSED_DIR / "monthly_group_return.csv")
    print(PROCESSED_DIR / "monthly_rank_ic.csv")
    print(PROCESSED_DIR / "performance_summary_no_cost.csv")
    print(FIGURE_DIR / "group_net_value.png")
    print(FIGURE_DIR / "strategy_vs_benchmark_no_cost.png")
    print(FIGURE_DIR / "monthly_rank_ic.png")


if __name__ == "__main__":
    main()