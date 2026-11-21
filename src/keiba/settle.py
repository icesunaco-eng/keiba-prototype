"""精算。三連複の買い目（組 → 円）と、レースの結果・払戻から戻り額を出す。

  - 的中：払戻（100円あたり）× 賭け金/100。同着で的中組が複数あれば、どれに当たっても払う
  - 返還：買い目に出走取消・競走除外の馬が入っていれば、その組の賭け金を返す
  - 不成立：完走が3頭未満、またはレースが行われなかったら全額返す
  - 払戻が分からないレースは「未精算」（回収率の計算から外す）
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


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


def winning_combos(finished: list) -> set[str]:
    """着順から、的中になる三連複の組（同着なら複数）。finished：（馬番, 着順）。"""
    top = [(int(n), int(pos)) for n, pos in finished if pos and pos <= 3]
    if len(top) < 3:
        return set()
    out = set()
    for c in combinations(top, 3):
        inside = max(pos for _, pos in c)
        outside = [pos for n, pos in top if (n, pos) not in c]
        if all(pos >= inside for pos in outside):
            out.add("-".join(map(str, sorted(n for n, _ in c))))
    return out


def missing_payouts(stakes: dict, finished: list, scratched, payouts) -> set[str]:
    """買った組のうち、着順では的中なのに払戻が読めていない組（同着の払戻の一部欠けなど）。取消の返還になる組は除く。

    払戻そのものが無いとき（payouts が空）は settle_trio が「未精算」にするので、ここでは扱わない。
    """
    if not payouts:
        return set()
    scr = {int(n) for n in scratched or ()}
    live = {c for c in stakes if not ({int(x) for x in c.split("-")} & scr)}
    return (winning_combos(finished) - set(payouts)) & live


def valid_size(n) -> bool:
    """予定の頭数として使える値か（真偽値を除く正の整数）。"""
    return isinstance(n, int) and not isinstance(n, bool) and n > 0


def result_problem(finished: list, finishers: int, scratched, payouts, rows: int | None = None, field_size=None,
                   numbers: list | None = None) -> str | None:
    """結果を精算に使えない理由（一部しか読めていない等）。使えれば None。

    finished：（馬番, 着順）の完走馬。rows：読めた結果の行数（取消・除外も含む）。field_size：予定の頭数。
    numbers：結果の全行の馬番（取消・除外も含む。重複を見る）。
    一部しか読めていない結果を「完走3頭未満＝不成立」と取り違えないために、精算の前に確かめる。
    """
    if payouts and any(not isinstance(v, int) or isinstance(v, bool) or v < 100 for v in payouts.values()):
        # 三連複の払戻は100円が最低（元返し）。それ未満は数字が途中で切れた読取など：確定させずに待つ
        return "三連複の払戻の金額がおかしい（100円未満。結果がそろうまで待つ）"
    if any(pos is None for _, pos in finished):
        # 完走なのに着順が空欄の馬がいる（途中まで読めたページなど）：同着の的中組が決まらないので待つ
        return "着順が空欄の完走馬がいる（結果がそろうまで待つ）"
    nums = [int(n) for n, _ in finished]
    if numbers is not None:
        alln = [int(n) for n in numbers]
        if len(set(alln)) != len(alln):
            return "結果の馬番が重複している（着順を照合できない）"
    placed = [n for n, pos in finished if pos and pos <= 3]
    if not valid_size(field_size):
        # 予定の頭数が分からない・おかしいと、行の欠けを見分けられない（読めた行の数から推測しない）
        return "予定の頭数が分からないので、結果がそろっているか確かめられない"
    size = field_size
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
