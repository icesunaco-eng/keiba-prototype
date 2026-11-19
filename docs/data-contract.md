# データの置き方と列

## 置き場所（`~/.heroines/paths.json` の `"keiba"`）

| キー | 中身 | 今の置き場所の例 |
|---|---|---|
| `integrated_dir` | 馬ID候補の統合版（`combined/runner_candidates.jsonl`・`combined/races.jsonl`・`jra-baseline/expected_races.jsonl`） | `~/競馬/馬ID候補統合_6893_完了_20261119` |
| `qa_zip` | 着順・時計・展開の統合ZIP（`data/runner_results.jsonl`・`data/race_flow.jsonl`） | `~/競馬/照合台帳受領/horse-two-year-flow-results-qa-checked.zip` |
| `myu_zips` | netkeiba 結果表を含むZIP（`raw/netkeiba/**/result_table.dom.html` と `capture_metadata.json`） | `~/競馬/みゅう引継ぎ受領/mini-*.zip` |
| `heroines_dirs` | HEROINES 収集フォルダ（`日付/場/NR/raw/result.html`） | `~/競馬/HEROINES並行取得*` |
| `noa_v3_dir` | のあ v3（`races.jsonl`） | `~/Documents/Codex/…/keiba-glyph-20261117-4/v3` |
| `jra_pdf_dirs` | JRA成績PDF（日・場別）を探すフォルダ（払戻の読取） | 複数可 |
| `pdf_table_sources` | JRA成績PDFを表にしたもの（`tools/jra_pdf/convert.py` の出力）と、使うレースの一覧 | `[{"races_dir": "~/keiba-prototype/data/work/aisu_pdf/races", "manifest_csv": "~/競馬/あいすの引継ぎ受領/aisu-jra-seiseki-1876-20261119/manifest.csv"}]` |
| `aj1_cmap` | PDF読取に使う Adobe-Japan1 の CMap（UniJIS-UTF32-H） | のあの作業フォルダの `ref/` |
| `data_dir` | 出力先（省略時はリポジトリの `data/`） | |

## 正規の表（`data/tables/`）

- **races.parquet**：race_key（`日付|場|R`）、日付、場、R、netkeiba の race_id、担当（noa/mini/aisu）、芝ダ、距離、回り、馬場、天候、クラス、頭数、発走時刻、レースの上り3F/4F、出走表の項目の出どころ
- **runners.parquet**：runner_key（`race_key|馬番`）、馬ID（netkeiba、全行あり）、馬名、状態（finished/did_not_finish/withdrawn/excluded）、着順、同着、走破時計、枠、性、齢、斤量、騎手キー・調教師キー（`nk:<netkeibaのID>`）、馬体重・増減、確定単勝オッズ・人気、上り3F、通過順、着差
- **payouts.parquet**：三連複の払戻（組・円・出どころ・照合結果）
- **conflicts.csv**：同じ値が複数の資料で違ったもの
- **build_report.json**：件数・欠け・照合の結果

## 資料の優先と照合

- 着順・時計・状態は統合版（QA済み）を正とする
- 出走表の項目は netkeiba 結果ページ → netkeiba 結果表 → のあ v3 → JRA成績PDFの表（あいす分）の順に使い、違いは conflicts.csv に残す（計画では「PDF優先」としていたが、どれも同じ成績表の転記で、netkeiba には騎手・調教師のIDがあるため、IDで揃えられる netkeiba を先にした）
- 騎手・調教師は netkeiba のIDで揃える。のあ v3 だけのレースは、同じ馬の行が両方にある期間から「名前→ID」の対応を作って当てる
- 三連複の払戻は netkeiba 結果ページを優先し、無いレースは JRA成績PDF から読む。PDFの額は、同じ段の「3連複票数 計X 的中Y」から計算した額（X×0.75÷Y×100、10円未満切捨）と ±10円で一致したものだけ使う

## 使える時点（`src/keiba/schema.py`）

| 列 | 前日（24h） | 1時間前 | 15分前 |
|---|---|---|---|
| 過去走から作る特徴、枠・馬番・斤量・騎手・調教師・距離・芝ダ・クラス・頭数 | ○ | ○ | ○ |
| 馬場・天候・馬体重・増減 | × | ○ | ○ |
| 確定オッズ・確定人気・そのレースの着順/時計/上り | × | × | × |

確定オッズは発走後の値なので特徴に入れない。比較用の「人気順」モデルだけが使い、レポートで「比較専用」と明記する。

## JRA成績PDFを表にする

```
python3 tools/jra_pdf/convert.py <PDFのフォルダ> <出力フォルダ> [pdf_manifest.csv]
```

中身は のあ の `pdf_text2.py`・`parse_report2.py`（通信しない）。外字の騎手名（「鮫」など）は推測で埋めず、名前のまま残す。
あいす分 1,876R は 2026-11-19 に受領（`~/競馬/あいすの引継ぎ受領/`、PDF 157本・SHA256SUMS 全一致・欠け0）。変換結果は依頼の一覧と1,876R・25,234行で一致。

## 統合版に足してほしいもの

1. **三連複の払戻**：みに分（netkeiba 結果表だけのレース）約1,600Rは払戻が無い。結果ページかPDFがあれば精算できる
3. **発走前のオッズ**（任意）：検証B（発走前オッズでの回収率）と配分に必要。時刻つきで、発走前に取得したものだけ

## これからのレース（`data/live/`、`keiba collect tick`）

- `snapshots/<日付>/<場>/<R>/<UTC時刻>-<時点>-<種類>.html`：取ったページそのまま（種類：card＝出馬表、odds_first／odds_win／odds_trio＝オッズ、result＝結果）
- `snapshots.jsonl`：1行1ページ。race_key・種類・時点（24h/1h/15m/result）・取得時刻（JST）・ファイル・SHA-256・出典URL・cname。オッズ未発売のときは `odds_unavailable` を記録
- `state.json`：知っているレース（発走時刻・リンク）と、取り終えた時点
- 時点ごとの締切：24h＝発走24時間前、1h＝発走60分前、15m＝発走15分前。締切の15分前から締切までに取る。その時点の予想・合格判定には、締切までに取った出馬表・取消・オッズだけを使う（締切後の取得は不合格）
- 取り方：リクエスト間は2秒以上、1回の実行で300ページまで。JRAの「ご利用に際して」と robots.txt は 2026-11-19 に確認（自動取得の禁止なし。著作物は私的使用の範囲）
