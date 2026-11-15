# 架空専用の通常経路接続・隔離試作
実行: `python3 -I -B synthetic_flow/demo.py`（repository rootから）。
既存Systemの追記専用予測ストアへ、未較正の仮スコア計算器出力を synthetic-score-pl-v1 / synthetic_only として保存し、架空結果と組合せ照合する。固定ダミーとは別方式。入力点数・確率は人工例で、実予測能力・精度・回収率を示さない。

自己run再現および独立19assert成功。UTC差不明時刻を保存前に保留、三連複3頭の全6順列は同一として再送exists。証拠時刻差/異なる組番は保留。各保存1件、元入力順序不変、元System4ファイルの実行前後SHA一致、通信・子プロセス試行0。

予測保存は既存AppendOnly、架空結果と比較は同クラスの別ストア。System.confirm・固定ダミー配分・適格性・正式精算・正式集計には接続していない。結果利益/購入額はNone、注文なし。実モデル・実データschema/証拠検証・永続DB・クラッシュ復旧・正式精算は未対応。real_shadow保留を解除しない。元Systemの未解決事項を解決済みとはしない。

model.py SHA256 23ec1fef2700e2de46f05edf340c9a67b23eedd8d78ae8617dd8317f8ddeaa7f
demo.py SHA256 92474d59a9a04883098dc6c5af530712e0464ebae740355c151b60bdc0e4a8df
src4ファイルの固定hashはsource-manifest.json。実資料/PDF/秘密/DB/端末ログは含めない。mainへのmerge・本番反映・新規取得・実賭け・同着深掘りなし。

次工程案: 架空専用の永続ストアと再起動後の重複防止を実装し、取得済情報を偽の事前入力に転用せず、実データの証拠schema/特徴導出を別段階で接続する。
