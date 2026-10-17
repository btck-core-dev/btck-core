"""Deterministic integer reward allocation for a frozen PPLNS share window."""
from decimal import Decimal, ROUND_DOWN


def allocate_reward(reward_satk, weights, finder_wallet, fee_rate, bonus_rate):
    if type(reward_satk) is not int or reward_satk < 0:
        raise ValueError('Reward must be nonnegative integer SatK')
    fee_rate, bonus_rate = Decimal(str(fee_rate)), Decimal(str(bonus_rate))
    if not fee_rate.is_finite() or not bonus_rate.is_finite():
        raise ValueError('Reward rates must be finite')
    fee_rate = max(Decimal(0), min(Decimal(1), fee_rate))
    bonus_rate = max(Decimal(0), min(Decimal(1), bonus_rate))
    weights = {wallet: Decimal(str(value)) for wallet, value in weights.items()}
    if any(not value.is_finite() or value <= 0 for value in weights.values()):
        raise ValueError('Window weights must be finite and positive')
    fee = int((Decimal(reward_satk) * fee_rate).to_integral_value(rounding=ROUND_DOWN))
    bonus = min(int((Decimal(reward_satk) * bonus_rate).to_integral_value(rounding=ROUND_DOWN)), reward_satk-fee) if finder_wallet else 0
    available = reward_satk - fee - bonus
    total = sum(weights.values(), Decimal(0))
    credits = {}
    if total > 0 and available > 0:
        fractions = []
        for wallet in sorted(weights):
            exact = Decimal(available) * weights[wallet] / total
            credits[wallet] = int(exact.to_integral_value(rounding=ROUND_DOWN))
            fractions.append((exact-credits[wallet], wallet))
        for _, wallet in sorted(fractions, key=lambda item: (-item[0], item[1]))[:available-sum(credits.values())]:
            credits[wallet] += 1
        if bonus:
            credits[finder_wallet] = credits.get(finder_wallet, 0) + bonus
    elif finder_wallet and reward_satk > fee:
        credits[finder_wallet] = reward_satk-fee
    else:
        fee = reward_satk
    credits = {wallet: value for wallet, value in credits.items() if value > 0}
    if fee + sum(credits.values()) != reward_satk:
        raise RuntimeError('Reward allocation does not conserve SatK')
    return fee, bonus, credits
