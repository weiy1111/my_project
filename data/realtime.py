from __future__ import annotations
"""实时行情（多源）

优先级：
  1. 新浪财经 API（快，不限速）
  2. 腾讯实时行情 API（批量、稳定）
  3. 东方财富 API（备选）
  4. AKShare 日K线
  5. baostock 日K线（兜底）
"""
import requests
import re
import pandas as pd
import time
from datetime import datetime, timedelta

from discovery.data_quality import attach_source

# 行情缓存
_quotes_cache: dict[str, dict] = {}
_quotes_cache_time: float = 0
_QUOTES_CACHE_TTL = 15

# K线缓存
_klines_cache: dict[tuple, pd.DataFrame] = {}
_klines_cache_time: dict[tuple, float] = {}
_KLINES_CACHE_TTL = 60


def _code_to_sina(code: str) -> str:
    """股票代码转新浪格式：sh/sz + 代码"""
    if code.startswith(('5', '6')):
        return f'sh{code}'
    return f'sz{code}'


def _code_to_tencent(code: str) -> str:
    """股票代码转腾讯格式：sh/sz + 代码"""
    if code.startswith(('5', '6')):
        return f'sh{code}'
    return f'sz{code}'


def _fetch_sina_realtime(codes: list[str]) -> dict[str, dict]:
    """新浪财经实时行情 API（批量，不限速）"""
    if not codes:
        return {}
    sina_codes = ','.join(_code_to_sina(c) for c in codes)
    url = f'https://hq.sinajs.cn/list={sina_codes}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn/',
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        resp.encoding = 'gbk'
    except Exception:
        return {}

    result = {}
    # 解析: var hq_str_sz002050="三花智控,51.740,51.690,51.450,..."
    pattern = re.compile(r'var hq_str_(s[hz]\d+)="(.+?)"')
    for match in pattern.finditer(resp.text):
        sina_code = match.group(1)
        code = sina_code[2:]  # 去掉 sh/sz 前缀
        fields = match.group(2).split(',')
        if len(fields) < 10 or not fields[3]:
            continue
        try:
            price = float(fields[3])
            if price <= 0:
                continue
            pre_close = float(fields[2])
            pct = ((price - pre_close) / pre_close * 100) if pre_close > 0 else 0
            result[code] = {
                "name": fields[0],
                "price": price,
                "open": float(fields[1]),
                "high": float(fields[4]),
                "low": float(fields[5]),
                "pre_close": pre_close,
                "volume": int(float(fields[8])),
                "amount": float(fields[9]),
                "pct_change": round(pct, 2),
                "timestamp": datetime.now(),
            }
            result[code] = attach_source(result[code], "sina", "realtime")
        except (ValueError, IndexError):
            continue
    return result


def _fetch_tencent_realtime(codes: list[str]) -> dict[str, dict]:
    """腾讯实时行情 API（批量）。

    qt.gtimg.cn 返回字段用 ~ 分隔，常用字段：
    1 名称、3 最新价、4 昨收、5 今开、30 时间、32 涨跌幅、
    33 最高、34 最低、36 成交量、37 成交额(万元)。
    """
    if not codes:
        return {}
    result: dict[str, dict] = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Referer': 'https://gu.qq.com/',
    }
    for start in range(0, len(codes), 80):
        batch = codes[start:start + 80]
        symbols = ','.join(_code_to_tencent(c) for c in batch)
        url = f'https://qt.gtimg.cn/q={symbols}'
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            resp.raise_for_status()
            resp.encoding = 'gbk'
        except Exception:
            continue

        pattern = re.compile(r'v_(s[hz]\d+)="([^"]*)"')
        for match in pattern.finditer(resp.text):
            symbol = match.group(1)
            code = symbol[2:]
            parts = match.group(2).split('~')
            if len(parts) < 38:
                continue
            try:
                price = float(parts[3] or 0)
                if price <= 0:
                    continue
                timestamp = datetime.now()
                if len(parts[30]) >= 14:
                    timestamp = datetime.strptime(parts[30][:14], '%Y%m%d%H%M%S')
                result[code] = {
                    "name": parts[1],
                    "price": price,
                    "open": float(parts[5] or 0),
                    "high": float(parts[33] or 0),
                    "low": float(parts[34] or 0),
                    "pre_close": float(parts[4] or 0),
                    "volume": int(float(parts[36] or 0)),
                    "amount": float(parts[37] or 0) * 10000,
                    "pct_change": round(float(parts[32] or 0), 2),
                    "timestamp": timestamp,
                }
                result[code] = attach_source(result[code], "tencent", "realtime")
            except (ValueError, IndexError):
                continue
    return result


def _code_to_secid(code: str) -> str:
    """股票代码转东方财富 secid：0=深圳 1=上海 2=北交所"""
    if code.startswith(('5', '6')):
        return f'1.{code}'
    elif code.startswith(('0', '3')):
        return f'0.{code}'
    elif code.startswith(('4', '8')):
        return f'0.{code}'
    return f'0.{code}'


def _fetch_eastmoney_realtime(codes: list[str]) -> dict[str, dict]:
    """东方财富实时行情 API（批量，不限速）

    返回值单位：价格字段(f2/f15/f16/f17/f18)单位为分，需÷100；
    涨跌幅(f3)单位为百分比×100，需÷100。
    """
    if not codes:
        return {}
    secids = ','.join(_code_to_secid(c) for c in codes)
    url = 'https://push2.eastmoney.com/api/qt/ulist.np/get'
    params = {
        'secids': secids,
        'fields': 'f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18',
        'ut': 'fa5fd1943c7b386f172d6893dbbd4594',
        '_': int(time.time() * 1000),
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Referer': 'https://quote.eastmoney.com/',
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        diff = data.get('data', {}).get('diff', [])
        if not diff:
            return {}
        result = {}
        for item in diff:
            code = item.get('f12', '')
            price_raw = item.get('f2', 0)
            if isinstance(price_raw, str) and price_raw == '-':
                continue
            price = float(price_raw or 0) / 100
            if price <= 0:
                continue
            result[code] = {
                "name": str(item.get('f14', '')),
                "price": price,
                "open": float(item.get('f17', 0) or 0) / 100,
                "high": float(item.get('f15', 0) or 0) / 100,
                "low": float(item.get('f16', 0) or 0) / 100,
                "pre_close": float(item.get('f18', 0) or 0) / 100,
                "volume": int(item.get('f5', 0) or 0),
                "amount": float(item.get('f6', 0) or 0),
                "pct_change": float(item.get('f3', 0) or 0) / 100,
                "timestamp": datetime.now(),
            }
            result[code] = attach_source(result[code], "eastmoney", "realtime")
        return result
    except Exception:
        return {}


def _fetch_eastmoney_single(code: str) -> dict | None:
    """东方财富单只股票实时行情"""
    secid = _code_to_secid(code)
    url = 'https://push2.eastmoney.com/api/qt/stock/get'
    params = {
        'secid': secid,
        'fields': 'f43,f44,f45,f46,f47,f48,f57,f58,f60,f170',
        'ut': 'fa5fd1943c7b386f172d6893dbbd4594',
        '_': int(time.time() * 1000),
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Referer': 'https://quote.eastmoney.com/',
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json().get('data', {})
        if not data:
            return None
        price_raw = data.get('f43', 0)
        if not price_raw or str(price_raw) == '-':
            return None
        price = float(price_raw) / 100
        if price <= 0:
            return None
        return attach_source({
            "name": str(data.get('f58', '')),
            "price": price,
            "open": float(data.get('f46', 0) or 0) / 100,
            "high": float(data.get('f44', 0) or 0) / 100,
            "low": float(data.get('f45', 0) or 0) / 100,
            "pre_close": float(data.get('f60', 0) or 0) / 100,
            "volume": int(data.get('f47', 0) or 0),
            "amount": float(data.get('f48', 0) or 0),
            "pct_change": float(data.get('f170', 0) or 0) / 100,
            "timestamp": datetime.now(),
        }, "eastmoney", "realtime")
    except Exception:
        return None


def _fetch_akshare_kline(code: str) -> dict | None:
    """用 AKShare 日K线接口获取最新数据（收盘价，非实时）"""
    try:
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start, end_date=end, adjust="qfq"
        )
        if df is None or df.empty:
            return None
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        price = float(latest["收盘"])
        pre_close = float(prev["收盘"])
        pct = ((price - pre_close) / pre_close * 100) if pre_close > 0 else 0
        return attach_source({
            "name": "", "price": price,
            "open": float(latest["开盘"]), "high": float(latest["最高"]),
            "low": float(latest["最低"]), "pre_close": pre_close,
            "volume": int(latest["成交量"]), "amount": float(latest["成交额"]),
            "pct_change": pct, "timestamp": datetime.now(),
        }, "akshare", "historical")
    except Exception:
        return None


def _fetch_baostock_kline(code: str) -> dict | None:
    """用 baostock 获取最新数据（兜底，仅历史收盘价）"""
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            return None

        prefix = 'sh' if code.startswith('6') else 'sz'
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')

        rs = bs.query_history_k_data_plus(
            f'{prefix}.{code}',
            'date,open,high,low,close,volume,amount',
            start_date=start, end_date=end,
            frequency='d', adjustflag='2'
        )

        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
        bs.logout()

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=rs.fields)
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['close'])
        if df.empty:
            return None

        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        price = float(latest['close'])
        pre_close = float(prev['close'])
        pct = ((price - pre_close) / pre_close * 100) if pre_close > 0 else 0

        return attach_source({
            "name": "", "price": price,
            "open": float(latest['open']), "high": float(latest['high']),
            "low": float(latest['low']), "pre_close": pre_close,
            "volume": int(latest['volume']), "amount": float(latest['amount']),
            "pct_change": pct, "timestamp": datetime.now(),
        }, "baostock", "historical")
    except Exception:
        return None


def get_realtime_quotes(codes: list[str]) -> dict[str, dict]:
    """获取多只股票实时快照

    优先级：腾讯实时API → 新浪实时API → 东方财富实时API → AKShare日K → baostock日K
    """
    global _quotes_cache, _quotes_cache_time
    now = time.time()

    # 命中缓存直接返回
    if _quotes_cache and now - _quotes_cache_time < _QUOTES_CACHE_TTL:
        hit = {c: _quotes_cache[c] for c in codes if c in _quotes_cache}
        if len(hit) == len(codes):
            return hit

    # 只取未缓存的
    missing = [c for c in codes if c not in _quotes_cache or now - _quotes_cache_time >= _QUOTES_CACHE_TTL]

    if missing:
        # 方式1：腾讯批量API（优先，稳定）
        tencent_result = _fetch_tencent_realtime(missing)
        if tencent_result:
            _quotes_cache.update(tencent_result)
            missing = [c for c in missing if c not in tencent_result]

        # 方式2：新浪财经批量API
        if missing:
            sina_result = _fetch_sina_realtime(missing)
            if sina_result:
                _quotes_cache.update(sina_result)
                missing = [c for c in missing if c not in sina_result]

        # 方式3：东方财富批量API
        if missing:
            em_result = _fetch_eastmoney_realtime(missing)
            if em_result:
                _quotes_cache.update(em_result)
                missing = [c for c in missing if c not in em_result]

        # 方式4：逐个获取（AKShare → baostock）
        if missing:
            print(f"  [行情] {len(missing)} 只备用源获取...")
            for code in missing:
                quote = _fetch_akshare_kline(code)
                if quote is None:
                    quote = _fetch_baostock_kline(code)
                if quote:
                    _quotes_cache[code] = quote
                time.sleep(0.3)

        _quotes_cache_time = now

    return {c: _quotes_cache[c] for c in codes if c in _quotes_cache}


def get_realtime_price(code: str) -> float | None:
    """获取单只股票最新价格"""
    quotes = get_realtime_quotes([code])
    q = quotes.get(code)
    return q["price"] if q else None


def get_recent_klines(code: str, period: str = "daily", count: int = 60) -> pd.DataFrame | None:
    """获取近N条K线（AKShare 优先，baostock 兜底）"""
    cache_key = (code, period, count)
    now = time.time()

    if cache_key in _klines_cache and now - _klines_cache_time.get(cache_key, 0) < _KLINES_CACHE_TTL:
        return _klines_cache[cache_key]

    # 方式1：AKShare
    for attempt in range(2):
        try:
            if period == "daily":
                start = (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d")
                end = datetime.now().strftime("%Y%m%d")
                df = ak.stock_zh_a_hist(
                    symbol=code, period="daily",
                    start_date=start, end_date=end, adjust="qfq"
                )
                df = df.rename(columns={
                    "日期": "date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close", "成交量": "volume",
                    "成交额": "amount",
                })
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                result = df.tail(count)
            else:
                df = ak.stock_zh_a_hist_min_em(
                    symbol=code, period=period, adjust="qfq"
                )
                if df is None or df.empty:
                    return None
                df = df.rename(columns={
                    "时间": "date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close", "成交量": "volume",
                    "成交额": "amount",
                })
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                result = df.tail(count)

            _klines_cache[cache_key] = result
            _klines_cache_time[cache_key] = now
            return result
        except Exception:
            if attempt < 1:
                time.sleep(1)

    # 方式2：baostock 兜底
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            return None

        prefix = 'sh' if code.startswith('6') else 'sz'
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=count * 2)).strftime('%Y-%m-%d')

        rs = bs.query_history_k_data_plus(
            f'{prefix}.{code}',
            'date,open,high,low,close,volume,amount',
            start_date=start, end_date=end,
            frequency='d', adjustflag='2'
        )

        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
        bs.logout()

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=rs.fields)
        df['date'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['close'])
        df = df.set_index('date').sort_index()
        result = df.tail(count)

        _klines_cache[cache_key] = result
        _klines_cache_time[cache_key] = now
        return result
    except Exception as e:
        print(f"获取 {code} K线失败: {e}")
        return None
