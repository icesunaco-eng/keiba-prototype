# 架空snapshot→計算器接続（race_key照合版）
実行: python3 -I -B snapshot-interface/connect_demo.py
snapshot受理後、別明示の架空特徴入力とrace_key・予測/発走時刻・馬ID集合を照合し、計算器へ渡す。3列snapshotから8群点数を捏造せず、特徴無しは保留。race_key欠落/不一致はモデル呼出し前に保留する。別レースsnapshot自体は保管されても予測されない。

修正版は自己run成功、独立11項目成功。前版独立20項目と修正版を混同しない。依存snapshot/modelは既存独立確認版。合成試験でのみ接続し、実予測・出典真正性・実データ利用権利は未検証。永続DB・実特徴導出・資金配分・本番接続は未実装。未発売/欠損/未実装は全て配分不可。正式成績不可、注文なし。

connect_demo.py SHA256 928ded6840bc1d714b7deebd0899a74a34b3cf639ed502c542e797c6eab12d2f
snapshot.py SHA256 e32bda572e114ecaee1a7a06296faf89a6166da1c2fba6de715ab967f9998920
model.py SHA256 23ec1fef2700e2de46f05edf340c9a67b23eedd8d78ae8617dd8317f8ddeaa7f

秘密・実個人情報・実データ・端末ログは含めない。private別branch保存のみ、main・本番変更・新規取得・実賭けなし。
