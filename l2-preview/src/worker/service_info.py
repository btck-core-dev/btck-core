"""Public service terms. Deliberately contains no verification internals."""

BILLING_TEXT = '''计费说明 / Billing

当前版本使用 development SatK 开发积分，不接受主网充值，不支持提现。

按实际输入、输出 token 计量；每轮调用分别计费。
加权单位 = 输入 token + ceil(输出 token × 1.5)。
Task KCU = 加权单位 × 所选模型 KCU/千单位费率 ÷ 1000。
KCU 是服务计价单位；1 KCU 默认参考服务价值为 0.00010 美元。

报价把 Task KCU 按 BTC-K 参考价格换算为 mSatK，再向上取整为整数 SatK。
1 BTC-K = 100,000,000 SatK；1 SatK = 1,000 mSatK。
bootstrap-fixed-v1 使用 0.05 美元/BTC-K 作为启动参考，不是市场现价。
不同模型费率不同，以连接网关发布的模型目录和签名账单为准。
模型推荐和量化选项本身不改变网关价格，不按配置的最大上下文预收费。
缓存复用不代表输入 token 自动免费，当前没有独立的缓存折扣规则。

客户设置单次最高预算；结算后释放未用预算。
返回丢失时用原请求 ID 查询/恢复，不要新建请求重复执行。
执行结果不确定时预算保留冻结，需核查；输出不合格或截断也可能产生推理费用。

当前五方分配：第1年 服务矿工60% / 待命池10% / AI Combo15% / 销毁5% / 生态10%。
第2年：50/10/25/10/5%；第3年：40/10/30/15/5%；第4年及以后：35/5/40/20/0%。
分配采用整数配额，以账单为准；池份额不等于单矿工必得收益。
开发账本只记录积分分配，不执行链上支付或销毁。
规范中的10,000 SatK上链门槛属于正式结算设计，当前版本不提供该操作。

来源：tokenomics_v2_canonical.md §8.1、§8.5；pricing_engine.py；inference_pilot.py。
旧显存矩阵中的固定 SatK 公式与当前 KCU 规范不同，不作为现行报价。
'''


def verification_label(value):
    return {'verified': '验证通过', 'pending': '验证中', 'unavailable': '暂不可接单',
            'not_configured': '尚未完成服务验证'}.get(value, '暂不可接单')
