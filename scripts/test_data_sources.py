#!/usr/bin/env python3
from __future__ import annotations

"""测试exe文件数据源获取"""

import sys
import requests
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_sina_api():
    """测试新浪财经API"""
    print("测试新浪财经API...")
    try:
        url = "https://hq.sinajs.cn/list=sh000001"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.sina.com.cn/',
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'gbk'
        
        if 'var hq_str_' in resp.text:
            print("  [PASS] 新浪财经API正常")
            return True
        else:
            print("  [FAIL] 新浪财经API返回异常")
            return False
    except Exception as e:
        print(f"  [FAIL] 新浪财经API失败: {e}")
        return False


def test_tencent_api():
    """测试腾讯财经API"""
    print("测试腾讯财经API...")
    try:
        url = "https://qt.gtimg.cn/q=sh000001"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://gu.qq.com/',
        }
        resp = requests.get(url, headers=headers, timeout=8)
        resp.encoding = 'gbk'
        
        if 'v_sh000001' in resp.text:
            print("  [PASS] 腾讯财经API正常")
            return True
        else:
            print("  [FAIL] 腾讯财经API返回异常")
            return False
    except Exception as e:
        print(f"  [FAIL] 腾讯财经API失败: {e}")
        return False


def test_akshare():
    """测试AKShare"""
    print("测试AKShare...")
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20260101", end_date="20260131", adjust="qfq")
        if df is not None and not df.empty:
            print(f"  [PASS] AKShare正常，获取 {len(df)} 条数据")
            return True
        else:
            print("  [FAIL] AKShare返回空数据")
            return False
    except ImportError:
        print("  [FAIL] AKShare未安装")
        return False
    except Exception as e:
        print(f"  [FAIL] AKShare失败: {e}")
        return False


def main():
    """运行测试"""
    print("=" * 60)
    print("数据源测试")
    print("=" * 60)
    
    results = []
    results.append(test_sina_api())
    results.append(test_tencent_api())
    results.append(test_akshare())
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"通过: {passed}/{total}")
    
    if passed == total:
        print("\n[PASS] 所有测试通过！")
    else:
        print(f"\n[FAIL] 有 {total - passed} 个测试失败")
    
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
