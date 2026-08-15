#!/usr/bin/env python3
from __future__ import annotations

"""测试所有优化模块

验证以下模块的正确性：
1. 市场环境检测
2. 评分权重调整
3. 评分置信度计算
4. 资金流分析
5. 动态止损
6. 风险分散检查
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_market_state():
    """测试市场环境检测"""
    print("1. 测试市场环境检测...")
    try:
        from discovery.market_state import detect_market_state, MarketState
        
        result = detect_market_state()
        print(f"   市场状态: {result.state.value}")
        print(f"   置信度: {result.confidence:.2f}")
        print(f"   MA20趋势: {result.ma20_trend}")
        print(f"   波动率: {result.volatility:.2f}%")
        print("   [PASS] 市场环境检测正常")
        return True
    except Exception as e:
        print(f"   [FAIL] 市场环境检测失败: {e}")
        return False


def test_score_confidence():
    """测试评分置信度计算"""
    print("\n2. 测试评分置信度计算...")
    try:
        from discovery.score_confidence import calculate_score_confidence
        
        # 模拟股票数据
        test_data = {
            "data_quality_score": 75.0,
            "history_estimated": False,
            "current_flow_source_quality": 80.0,
            "updated_at": "2026-08-15 10:30:00",
        }
        
        result = calculate_score_confidence(test_data, 0.85)
        print(f"   置信度: {result.confidence:.2f}")
        print(f"   等级: {result.level}")
        print(f"   因子数量: {len(result.factors)}")
        print("   [PASS] 评分置信度计算正常")
        return True
    except Exception as e:
        print(f"   [FAIL] 评分置信度计算失败: {e}")
        return False


def test_fund_flow_analysis():
    """测试资金流分析"""
    print("\n3. 测试资金流分析...")
    try:
        from discovery.fund_flow_analysis import analyze_fund_flow
        
        # 模拟股票数据
        test_data = {
            "main_net": 1e8,
            "main_pct": 5.0,
            "super_net": 5e7,
            "large_net": 5e7,
            "medium_net": -2e7,
            "small_net": -2e7,
            "amount": 1e9,
            "pct_change": 3.5,
            "main_net_3d": 2e8,
            "main_net_5d": 3e8,
            "main_net_10d": 5e8,
            "main_net_30d": 1e9,
        }
        
        result = analyze_fund_flow(test_data)
        print(f"   强度评分: {result.strength_score:.2f}")
        print(f"   持续性评分: {result.persistence_score:.2f}")
        print(f"   聪明资金模式: {result.smart_money_pattern}")
        print("   [PASS] 资金流分析正常")
        return True
    except Exception as e:
        print(f"   [FAIL] 资金流分析失败: {e}")
        return False


def test_dynamic_stop_loss():
    """测试动态止损"""
    print("\n4. 测试动态止损...")
    try:
        from discovery.dynamic_stop_loss import dynamic_stop_loss
        
        # 测试不同市场环境
        test_cases = [
            {"price": 50.0, "market_state": "bull", "risk_tolerance": "medium"},
            {"price": 50.0, "market_state": "bear", "risk_tolerance": "conservative"},
            {"price": 50.0, "market_state": "sideways", "risk_tolerance": "aggressive"},
        ]
        
        for case in test_cases:
            result = dynamic_stop_loss(
                current_price=case["price"],
                market_state=case["market_state"],
                risk_tolerance=case["risk_tolerance"],
            )
            print(f"   {case['market_state']}/{case['risk_tolerance']}: "
                  f"止损价={result.stop_loss_price:.2f}, "
                  f"止损幅度={result.stop_loss_pct:.2f}%")
        
        print("   [PASS] 动态止损正常")
        return True
    except Exception as e:
        print(f"   [FAIL] 动态止损失败: {e}")
        return False


def test_risk_diversification():
    """测试风险分散检查"""
    print("\n5. 测试风险分散检查...")
    try:
        from discovery.risk_diversification import check_risk_diversification
        
        # 模拟投资组合
        test_portfolio = [
            {"code": "002156", "sectors": ["半导体", "芯片"], "score": 75, "risk_score": 35},
            {"code": "000988", "sectors": ["光通信", "光模块"], "score": 70, "risk_score": 40},
            {"code": "603986", "sectors": ["半导体", "存储"], "score": 65, "risk_score": 30},
        ]
        
        result = check_risk_diversification(test_portfolio)
        print(f"   分散度评分: {result.diversification_score:.2f}")
        print(f"   行业集中度风险: {result.concentration_risk}")
        print(f"   相关性风险: {result.correlation_risk}")
        print(f"   建议数量: {len(result.suggestions)}")
        print("   [PASS] 风险分散检查正常")
        return True
    except Exception as e:
        print(f"   [FAIL] 风险分散检查失败: {e}")
        return False


def test_scorer_integration():
    """测试评分器集成"""
    print("\n6. 测试评分器集成...")
    try:
        # 这里只测试导入，实际评分需要完整数据
        from discovery.scorer import _get_cached_market_state, _get_market_state_confidence
        
        market_state = _get_cached_market_state()
        confidence = _get_market_state_confidence()
        
        print(f"   市场状态: {market_state.value}")
        print(f"   市场状态置信度: {confidence:.2f}")
        print("   [PASS] 评分器集成正常")
        return True
    except Exception as e:
        print(f"   [FAIL] 评分器集成失败: {e}")
        return False


def main():
    """运行所有测试"""
    print("=" * 60)
    print("量化交易系统优化模块测试")
    print("=" * 60)
    
    tests = [
        test_market_state,
        test_score_confidence,
        test_fund_flow_analysis,
        test_dynamic_stop_loss,
        test_risk_diversification,
        test_scorer_integration,
    ]
    
    results = []
    for test in tests:
        results.append(test())
    
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"通过: {passed}/{total}")
    
    if passed == total:
        print("[PASS] 所有测试通过！")
        return 0
    else:
        print(f"[FAIL] 有 {total - passed} 个测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
