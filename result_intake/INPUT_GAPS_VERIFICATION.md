# 入力欠落監査の独立再確認

対象15ディレクトリ、PDF42個は全て同一hash b7dc84de4cc904070d2247214f0754a69f0e09ccd37e40479fb57de12caa54b3。normalized-record 35個・490行の列はname,odds,order_by_row,time,umaban,waku。読取エラー0。announced_atは全35個null。metadata6個は同一取得時刻・PDF hashで発表時刻字段なし。extract.json1個、登録/血統/発表の語は該当0、前走は増減と同位置の見出し。PDF生バイトの/URI検出0。

結論は『今回検査した資料・字段では安定馬ID、過去走履歴、発表時刻を確認できなかった』に限定する。全Macでの不存在やPDF内の全情報不存在を断定しない。原資料変更・新規取得なし。実資料本文・一覧はGitHubに保存していない。
