# 競馬プロトタイプ（三連複予想の検証）

JRAの三連複を、過去データで予想して検証するための試作です。設計は Notion の「設計Y書 v0.1」に従っています。
**実際の購入・自動投票・本番接続はしません。**

- 本人向けの現在地：[docs/STATUS.md](docs/STATUS.md)
- データの置き方と列：[docs/data-contract.md](docs/data-contract.md)
- 旧試作（17ブランチ）は消さずにタグ `archive/<ブランチ名>` で残しています。新しい main とは別物です

## 流れ

```
取込（統合版＋元資料） → 時点つき特徴 → 予想（ルール／学習モデル／比較用2つ） → 三連複の確率
 → 配分（オッズが無いので今は未配分） → 追記専用の台帳 → 精算（検証Aの参考買い目） → HTMLレポート
```

| 場所 | 中身 |
|---|---|
| `src/keiba/ingest/` | 統合版（馬ID・着順・展開）に、netkeiba 結果表・のあ v3・JRA成績PDFの払戻を race_key×馬番 で結合 |
| `src/keiba/features/asof.py` | 全出走を日付順に1回なめて、前日までの情報だけで特徴を作る |
| `src/keiba/models/` | `rule.py`（8グループ10段階）、`ranker.py`（LightGBM 順位学習＋3着内分類）、`baseline.py`、`trio.py`（Plackett–Luce＋2・3着補正） |
| `src/keiba/evaluate.py` | 月ごとのウォークフォワード、封印期間の1回だけの評価、指標 |
| `src/keiba/ledger.py` | SQLite の追記専用台帳（レース×時点×方式×版、ハッシュ照合） |
| `src/keiba/allocate.py` / `settle.py` | 配分（均等払戻・期待値・分数ケリー、100円単位・上限1万円）／精算（返還・同着・不成立） |
| `src/keiba/report/` | iPhone幅の静的HTML |
| `tools/collect/` | `~/競馬` にあった収集スクリプト（場所は keiba.json から読む） |

## 使い方（Mac）

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
```

データの場所は `~/.heroines/keiba.json` の `"keiba"` に書きます（キーは `src/keiba/config.py` の先頭に一覧）。

```bash
bin/keiba all
```

`bin/keiba` は、Homebrew の libomp が無い Mac でも LightGBM が動くよう、scikit-learn 同梱の libomp を使わせます
（`brew install libomp` 済みなら `.venv/bin/keiba all` でも同じ）。2年分で約3分。出力は `data/`（git 管理外）：
`data/tables/`（正規の表・build_report.json・conflicts.csv）、`data/ledger.sqlite`、`data/eval/metrics.json`、`data/report/index.html`。

```bash
.venv/bin/python -m pytest -q
```

確認アプリ（予想を見る・購入を記録する。このMacの中だけ）：`bin/keiba app --open`。くわしくは [docs/app.md](docs/app.md)。

## 決まりごと

[CONTRIBUTING.md](CONTRIBUTING.md) を参照。PDF・画像・実データ・DB・秘密はコミットしません。
