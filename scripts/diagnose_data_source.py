#!/usr/bin/env python3
from __future__ import annotations

"""数据源诊断脚本

帮助诊断为什么exe文件无法获取网上数据源
"""

import sys
import requests
import ssl
import socket
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_ssl_certificate():
    """测试SSL证书"""
    print("1. 测试SSL证书...")
    try:
        ssl.create_default_context()
        print("   [PASS] SSL证书正常")
        return True
    except Exception as e:
        print(f"   [FAIL] SSL证书问题: {e}")
        return False


def test_network_connection():
    """测试网络连接"""
    print("\n2. 测试网络连接...")
    test_urls = [
        ("百度", "https://www.baidu.com"),
        ("新浪财经", "https://hq.sinajs.cn/list=sh000001"),
        ("腾讯财经", "https://qt.gtimg.cn/q=sh000001"),
        ("东方财富", "https://push2his.eastmoney.com/api/qt/stock/kline/get"),
    ]
    
    results = []
    for name, url in test_urls:
        try:
            resp = requests.get(url, timeout=5, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            if resp.status_code == 200:
                print(f"   [PASS] {name}: 连接成功")
                results.append(True)
            else:
                print(f"   [WARN] {name}: 状态码 {resp.status_code}")
                results.append(False)
        except requests.exceptions.SSLError as e:
            print(f"   [FAIL] {name}: SSL错误 - {e}")
            results.append(False)
        except requests.exceptions.ConnectionError as e:
            print(f"   [FAIL] {name}: 连接错误 - {e}")
            results.append(False)
        except requests.exceptions.Timeout:
            print(f"   [FAIL] {name}: 超时")
            results.append(False)
        except Exception as e:
            print(f"   [FAIL] {name}: 其他错误 - {e}")
            results.append(False)
    
    return any(results)


def test_sina_api():
    """测试新浪财经API"""
    print("\n3. 测试新浪财经API...")
    try:
        url = "https://hq.sinajs.cn/list=sh000001"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.sina.com.cn/',
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'gbk'
        
        if 'var hq_str_' in resp.text:
            print("   [PASS] 新浪财经API正常")
            return True
        else:
            print("   [FAIL] 新浪财经API返回异常")
            return False
    except Exception as e:
        print(f"   [FAIL] 新浪财经API失败: {e}")
        return False


def test_tencent_api():
    """测试腾讯财经API"""
    print("\n4. 测试腾讯财经API...")
    try:
        url = "https://qt.gtimg.cn/q=sh000001"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://gu.qq.com/',
        }
        resp = requests.get(url, headers=headers, timeout=8)
        resp.encoding = 'gbk'
        
        if 'v_sh000001' in resp.text:
            print("   [PASS] 腾讯财经API正常")
            return True
        else:
            print("   [FAIL] 腾讯财经API返回异常")
            return False
    except Exception as e:
        print(f"   [FAIL] 腾讯财经API失败: {e}")
        return False


def test_eastmoney_api():
    """测试东方财富API"""
    print("\n5. 测试东方财富API...")
    try:
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": "1.000001",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": "20260101",
            "end": "20260131",
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com/',
        }
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            print("   [PASS] 东方财富API正常")
            return True
        else:
            print(f"   [FAIL] 东方财富API状态码: {resp.status_code}")
            return False
    except Exception as e:
        print(f"   [FAIL] 东方财富API失败: {e}")
        return False


def test_akshare():
    """测试AKShare"""
    print("\n6. 测试AKShare...")
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20260101", end_date="20260131", adjust="qfq")
        if df is not None and not df.empty:
            print(f"   [PASS] AKShare正常，获取 {len(df)} 条数据")
            return True
        else:
            print("   [FAIL] AKShare返回空数据")
            return False
    except ImportError:
        print("   [FAIL] AKShare未安装")
        return False
    except Exception as e:
        print(f"   [FAIL] AKShare失败: {e}")
        return False


def test_baostock():
    """测试baostock"""
    print("\n7. 测试baostock...")
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code == '0':
            print("   [PASS] baostock登录成功")
            bs.logout()
            return True
        else:
            print(f"   [FAIL] baostock登录失败: {lg.error_msg}")
            return False
    except ImportError:
        print("   [FAIL] baostock未安装")
        return False
    except Exception as e:
        print(f"   [FAIL] baostock失败: {e}")
        return False


def test_pyinstaller_environment():
    """测试PyInstaller环境"""
    print("\n8. 测试PyInstaller环境...")
    try:
        # 检查是否在PyInstaller打包环境中
        if getattr(sys, 'frozen', False):
            print("   [INFO] 运行在PyInstaller打包环境中")
            print(f"   [INFO] 可执行文件路径: {sys.executable}")
            
            # 检查SSL证书路径
            import certifi
            print(f"   [INFO] SSL证书路径: {certifi.where()}")
        else:
            print("   [INFO] 运行在Python环境中")
        
        return True
    except Exception as e:
        print(f"   [FAIL] 环境检测失败: {e}")
        return False


def main():
    """运行所有诊断"""
    print("=" * 60)
    print("数据源诊断工具")
    print("=" * 60)
    
    results = []
    results.append(test_ssl_certificate())
    results.append(test_network_connection())
    results.append(test_sina_api())
    results.append(test_tencent_api())
    results.append(test_eastmoney_api())
    results.append(test_akshare())
    results.append(test_baostock())
    results.append(test_pyinstaller_environment())
    
    print("\n" + "=" * 60)
    print("诊断结果汇总")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"通过: {passed}/{total}")
    
    if passed == total:
        print("\n[PASS] 所有测试通过！")
        print("如果exe文件仍然无法获取数据，请检查：")
        print("1. 防火墙设置")
        print("2. 安全软件拦截")
        print("3. 网络代理设置")
    else:
        print(f"\n[FAIL] 有 {total - passed} 个测试失败")
        print("\n常见解决方案：")
        print("1. SSL证书问题：安装certifi包 (pip install certifi)")
        print("2. 网络连接问题：检查网络设置或使用代理")
        print("3. API被拦截：更换User-Agent或使用代理")
        print("4. 依赖缺失：重新打包，确保包含所有依赖")
    
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
