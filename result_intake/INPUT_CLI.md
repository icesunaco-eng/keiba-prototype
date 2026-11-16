# 既存JSONの直接読取CLI

```
cd result_intake
python load_input.py /path/to/normalized-record.json /path/to/source-meta.json
python -m unittest -v
```

元ファイルをread_bytesで読み、UTF-8 JSONをPythonで直接解釈する。中継でJSON.parse/stringify等を挟まず7.0の型を維持し、raw bytes hashとcanonical JSON hashを別名で記録する。重複キー・非有限数・非object JSONは拒否する。出力は件数・状態・hash・不足項目だけ。馬名一覧・出典URL・金額・本文は出さない。stagingのままでモデルへ昇格しない。

以前の98d266…は中継で7.0→7となったJSONのcanonical値であり採用しない。保存正本のcanonicalはd8c05902001d33b7053ce7d8aeada2caf7d931773ac581084ac925a710a9ad79、rawはf4e2ca9c0be1666ee730bc1a2d62dc9541737b7f4a7a84333c77afe0e4644e5d。両者は異なる対象のhashであり互いに一致する必要はない。hash差は解決済みで、残る未解決事項は馬IDや時点など入力内容の根拠である。

新規取得・原資料変更・実賭け・本番変更なし。既存元ファイルへ書込む処理はない。任意規模ファイルの資源上限や同時書込み中の入力は保証対象外、固定済みファイルを対象とする。

独立確認: 作者7件再現＋追加CLI境界4件成功。入れ子重複キー・±Infinity・不正UTF-8拒否、終了2、標準出力なし、元ファイル不変、欠損ファイル新規作成なし。Mac保存正本の直接読込で上記raw/canonicalの双方を再現し、hash不一致解消を独立確認。14entries、history0、model/prediction/order false。normalized/metadataとも元バイト不変。新たな必須修正なし。実データ予測への昇格・識別子/時点根拠の確定はしていない。
