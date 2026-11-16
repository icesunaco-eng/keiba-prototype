# 字形対応根拠 2026-11-16

対象原本SHA256 b7dc84de4cc904070d2247214f0754a69f0e09ccd37e40479fb57de12caa54b3。新規外部資料取得なし。

## Japan1
既存環境にある Adobe-Japan1-UCS2 v10.002（Adobe 1990–2023）、SHA256 6a9693361647a37996312cc57071bb79f8c06411207be7c730a83fda1254cd82 の明示対応 <1f19> <82a6> と <204b> <8fbb> を照合。CID7961=芦、8267=辻。UniJIS-UTF32-Hの逆引きでは欠落する異体字CIDが原因。japan1_supplement.install(module)は既存Japan1専用fallbackにのみ追加し、既存値が食い違えば例外。原PDFの18箇所が0に減少。

## Type1
原PDF内のCFFをfontTools RecordingPenで展開、同一輪郭をグループ化。特殊字形73群の図版を直接確認。明瞭な丸数字1〜18（書体違い含む）、1/2・1/4・3/4、メートル・キログラム、括弧付き株・有だけ対応。登録は埋込CFF bytesのSHA256と元字形名の組合せに限定。ページ内フォント名やc033という名前だけから他フォントへ適用しない。図形、括弧片、分類表示、不確かな漢字は未登録。Type1未解決fragment数808→121。字形本体はリポジトリに複製していない。

## 実行と品質
既存抽出器へJapan1、Type1補正をinstallした後、existing_fragment_adapterで全7ページを再抽出。原本SHA不変。18自己試験成功（13構造化、3Japan1、2Type1）。独立視覚/コード検証待ち。全レース完全正規化の保証はまだなく、未知121片を保留、model_ready=falseを維持。馬ID・発表時刻・走破成績・配当を確定したとは扱わない。
