"""精算。三連複の買い目（組 → 円）と、レースの結果・払戻から戻り額を出す。

  - 的中：払戻（100円あたり）× 賭け金/100。同着で的中組が複数あれば、どれに当たっても払う
  - 返還：買い目に出走取消・競走除外の馬が入っていれば、その組の賭け金を返す
  - 不成立：完走が3頭未満、またはレースが行われなかったら全額返す
  - 払戻が分からないレースは「未精算」（回収率の計算から外す）
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Settlement:
    status: str          # settled / refunded_void / unsettled_no_payout
    stake: int
    returned: int
    hits: list
    refunds: list

    @property
    def profit(self) -> int:
        return self.returned - self.stake


def combo_key(nums) -> str:
    return "-".join(str(n) for n in sorted(int(x) for x in nums))


def settle_trio(stakes: dict, finishers: int, scratched: set, payouts: dict | None) -> Settlement:
    """stakes: 組 → 円 / finishers: 完走頭数 / scratched: 取消・除外の馬番 / payouts: 組 → 100円あたり払戻。"""
    total = sum(stakes.values())
    if finishers < 3:
        return Settlement("refunded_void", total, total, [], list(stakes))
    returned, hits, refunds = 0, [], []
    live = {}
    for combo, amount in stakes.items():
        if {int(x) for x in combo.split("-")} & scratched:
            returned += amount
            refunds.append(combo)
        else:
            live[combo] = amount
    if live and payouts is None:
        return Settlement("unsettled_no_payout", total, returned, [], refunds)
    for combo, amount in live.items():
        pay = (payouts or {}).get(combo)
        if pay:
            returned += pay * amount // 100
            hits.append(combo)
    return Settlement("settled", total, returned, hits, refunds)
