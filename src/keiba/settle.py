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


def result_problem(finished: list, finishers: int, scratched, payouts, rows: int | None = None, field_size=None) -> str | None:
    """結果を精算に使えない理由（一部しか読めていない等）。使えれば None。

    finished：（馬番, 着順）の完走馬。rows：読めた結果の行数（取消・除外も含む）。field_size：予定の頭数。
    一部しか読めていない結果を「完走3頭未満＝不成立」と取り違えないために、精算の前に確かめる。
    """
    nums = [int(n) for n, _ in finished]
    placed = [n for n, pos in finished if pos and pos <= 3]
    try:
        size = int(field_size) if field_size is not None else None
    except (TypeError, ValueError):
        size = None
    if len(set(nums)) != len(nums) or set(nums) & {int(n) for n in scratched or ()}:
        return "結果の馬番が重複している（着順を照合できない）"
    if size and rows is not None and rows < size:
        return f"結果は{size}頭のうち{rows}頭分しか読めていない"
    if int(finishers) >= 3 and len(placed) < 3:
        return f"3着までの着順が{len(placed)}頭分しか読めていない"
    if int(finishers) < 3 and payouts:
        return "完走が3頭未満なのに払戻がある（結果の読取が足りない）"
    return None


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
