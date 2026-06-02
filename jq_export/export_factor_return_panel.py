from jqdata import *
import pandas as pd
import numpy as np
from datetime import datetime


INDEX_CODE = '000905.XSHG'   # 中证500
START_DATE = '2016-01-01'
END_DATE = '2025-12-31'

LOOKBACK = 60
SKIP_RECENT = 5
MIN_LISTING_DAYS = 180


def get_month_end_trade_days(start_date, end_date):
    """
    获取每个月最后一个交易日，作为信号日。
    """
    trade_days = get_trade_days(start_date=start_date, end_date=end_date)
    df = pd.DataFrame({'date': pd.to_datetime(trade_days)})
    month_end_days = df.groupby(df['date'].dt.to_period('M'))['date'].max()
    return [d.date() for d in month_end_days]


def get_next_trade_day(current_date, end_date):
    """
    获取 current_date 之后的下一个交易日。
    """
    trade_days = get_trade_days(start_date=current_date, end_date=end_date)
    trade_days = list(trade_days)

    if len(trade_days) < 2:
        return None

    return trade_days[1]


def is_old_enough(code, signal_date, min_days=180):
    """
    过滤上市时间太短的新股。
    """
    info = get_security_info(code)
    if info is None:
        return False

    listing_date = info.start_date
    return (signal_date - listing_date).days >= min_days


def calc_momentum_and_next_return(signal_date, next_signal_date):
    """
    在某个信号日计算：
    1. 中证500成分股的 momentum_60_5
    2. 下一持有期收益率
    3. 同期基准收益率
    """
    universe = get_index_stocks(INDEX_CODE, date=signal_date)

    # 过滤上市不足180天的新股
    universe = [
        code for code in universe
        if is_old_enough(code, signal_date, MIN_LISTING_DAYS)
    ]

    if len(universe) == 0:
        return pd.DataFrame()

    # 取过去至少 60 天价格，用于计算动量
    price = get_price(
        universe,
        count=LOOKBACK + 1,
        end_date=signal_date,
        frequency='daily',
        fields=['close', 'paused'],
        fq='pre',
        panel=False
    )

    if price is None or len(price) == 0:
        return pd.DataFrame()

    close = price.pivot(index='time', columns='code', values='close').sort_index()
    paused = price.pivot(index='time', columns='code', values='paused').sort_index()

    # 数据长度不足则跳过
    if close.shape[0] < LOOKBACK + 1:
        return pd.DataFrame()

    # momentum_60_5 = close[t-5] / close[t-60] - 1
    # 注意：这里剔除最近5个交易日，避免短期反转干扰
    momentum = close.iloc[-SKIP_RECENT] / close.iloc[-LOOKBACK] - 1

    # 信号日停牌过滤
    paused_today = paused.iloc[-1]
    tradable_codes = paused_today[paused_today == 0].index.tolist()

    # ST 过滤
    st_df = get_extras(
        'is_st',
        tradable_codes,
        start_date=signal_date,
        end_date=signal_date,
        df=True
    )

    if st_df is None or st_df.empty:
        return pd.DataFrame()

    is_st_today = st_df.iloc[0]
    tradable_codes = [
        code for code in tradable_codes
        if code in is_st_today.index and not bool(is_st_today.loc[code])
    ]

    if len(tradable_codes) == 0:
        return pd.DataFrame()

    entry_date = get_next_trade_day(signal_date, END_DATE)
    exit_date = get_next_trade_day(next_signal_date, END_DATE)

    if entry_date is None or exit_date is None:
        return pd.DataFrame()

    # 计算下一期个股收益：从 entry_date close 到 exit_date close
    future_price = get_price(
        tradable_codes,
        start_date=entry_date,
        end_date=exit_date,
        frequency='daily',
        fields=['close'],
        fq='pre',
        panel=False
    )

    if future_price is None or len(future_price) == 0:
        return pd.DataFrame()

    future_close = future_price.pivot(
        index='time',
        columns='code',
        values='close'
    ).sort_index()

    if future_close.shape[0] < 2:
        return pd.DataFrame()

    next_ret = future_close.iloc[-1] / future_close.iloc[0] - 1

    # 计算同期基准收益
    bench_price = get_price(
        INDEX_CODE,
        start_date=entry_date,
        end_date=exit_date,
        frequency='daily',
        fields=['close'],
        fq='pre',
        panel=False
    )

    if bench_price is None or len(bench_price) < 2:
        benchmark_ret = np.nan
    else:
        benchmark_ret = bench_price['close'].iloc[-1] / bench_price['close'].iloc[0] - 1

    result = pd.DataFrame({
        'signal_date': signal_date,
        'entry_date': entry_date,
        'exit_date': exit_date,
        'code': tradable_codes,
        'momentum_60_5': momentum.reindex(tradable_codes).values,
        'next_ret': next_ret.reindex(tradable_codes).values,
        'benchmark_ret': benchmark_ret
    })

    result = result.dropna(subset=['momentum_60_5', 'next_ret'])

    return result


def main():
    signal_dates = get_month_end_trade_days(START_DATE, END_DATE)

    all_results = []

    # 最后一个信号日没有下一期收益，所以跳过
    for i in range(len(signal_dates) - 1):
        signal_date = signal_dates[i]
        next_signal_date = signal_dates[i + 1]

        print(f'Processing signal date: {signal_date}')

        try:
            df = calc_momentum_and_next_return(signal_date, next_signal_date)
            if df is not None and len(df) > 0:
                all_results.append(df)
        except Exception as e:
            print(f'Error on {signal_date}: {e}')

    panel = pd.concat(all_results, ignore_index=True)

    print(panel.head())
    print(panel.tail())
    print(panel.shape)

    # 在聚宽研究环境中导出 CSV
    panel.to_csv('factor_return_panel.csv', index=False, encoding='utf-8-sig')

    print('Saved to factor_return_panel.csv')


main()