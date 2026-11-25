"""keiba コマンド。

  keiba ingest     正規の表（races/runners/payouts）を作る
  keiba features   時点つき特徴の表を作る
  keiba evaluate   時間順の検証＋封印期間の評価。予想を台帳に保存し、検証Aの参考買い目で精算する
  keiba report     iPhone幅のHTMLレポートを作る
  keiba verify     台帳の中身がハッシュと一致するか確かめる
  keiba collect tick      これからのレースの出馬表・オッズ・結果をJRA公式から時刻つきで集める（5分ごとに呼ぶ）
  keiba collect install   上を5分ごとに動かす launchd の設定ファイルを書く（読み込みは表示するコマンドで）
  keiba live run          集めたページで予想・配分・精算（tick が新しいページを取ったときにも自動で動く）
  keiba live check        週末の合格判定：三連複オッズを正しく読めたか（時点ごと）
  keiba live wallets      仮想の財布（方式×配分）の成績
  keiba review [--month YYYY-MM]  月次の振り返り（data/review/<月>.html・.json）
  keiba mam export        まむ（文章AI方式）への入力の束を data/mam/outbox/ に書く
  keiba mam import <file> まむの予想を検証して台帳に入れる（締切後は受け付けない）
  keiba app [--port 8765] [--open] [--notify] [--lan]  確認アプリ（予想を見る・購入を記録する。ふだんはこのMacの中だけ）
  keiba all        上を順に全部
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from .config import load_paths


def _tables(paths):
    t = paths.tables_dir
    return (pd.read_parquet(t / "races.parquet"), pd.read_parquet(t / "runners.parquet"),
            pd.read_parquet(t / "payouts.parquet"))


def cmd_ingest(paths, args):
    from .ingest.build import build
    report = build(paths, pdf_workers=args.workers)
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))


def cmd_features(paths, args):
    from .features.asof import build_features
    races, runners, _ = _tables(paths)
    from .models.rule_v1 import build_utilities
    feat = build_features(races, runners)
    feat = feat.merge(build_utilities(races, runners), on=["runner_key", "race_key"], how="left")
    from .models.rule_v1 import COMPARISON_VARIANTS
    for v in COMPARISON_VARIANTS:
        feat = feat.merge(build_utilities(races, runners, v).drop(columns=["is_flat"]), on=["runner_key", "race_key"], how="left")
    feat.to_parquet(paths.tables_dir / "features.parquet", index=False)
    print(f"特徴 {feat.shape[0]:,}行 × {feat.shape[1]}列 → {paths.tables_dir / 'features.parquet'}")


def cmd_evaluate(paths, args):
    from .evaluate import EvalConfig, run_evaluation
    from .ledger import Ledger
    races, runners, payouts = _tables(paths)
    feat = pd.read_parquet(paths.tables_dir / "features.parquet")
    cfg = EvalConfig(first_month=args.first_month, holdout_start=args.holdout_start)
    led = Ledger(paths.ledger_path)
    result, rows = run_evaluation(feat, races, runners, payouts, led, cfg)
    led.close()
    out = paths.data_dir / "eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    rows.drop(columns=["horse_p_top3"]).to_csv(out / "race_scores.csv", index=False)
    for s in result["summary"]:
        print(f"{s['period']:12s} {s['time_point']:3s} {s['method']:10s} races={s['races']:5d} "
              f"logloss={s['trio_logloss']} hit@10={s['hit@10']} ref10_roi={s['ref_top10_equal']['roi']}")
    print(f"版 {result['version']} → {out / 'metrics.json'}")


def cmd_report(paths, args):
    from .report.html import build_report
    index = build_report(paths.ledger_path, paths.report_dir, paths.tables_dir / "build_report.json", args.version)
    print(f"レポート → {index}")


def cmd_verify(paths, args):
    from .ledger import Ledger
    led = Ledger(paths.ledger_path, read_only=True)
    print(json.dumps(led.verify(), ensure_ascii=False))
    led.close()


def _notify(title: str, body: str):
    """Mac の通知センターに出す（手元だけ。外部には送らない）。"""
    import subprocess
    t = title.replace('"', "'")
    b = body.replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{b}" with title "keiba 収集" subtitle "{t}"'], check=False)


def cmd_live(paths, args):
    from .live.wallet import odds_check, run_live, wallet_summary
    if args.action == "run":
        print(json.dumps(run_live(paths), ensure_ascii=False))
    elif args.action == "check":
        df = odds_check(paths)
        out = paths.data_dir / "live" / "odds_check.csv"
        df.to_csv(out, index=False)
        print(df.to_string(index=False) if len(df) else "まだ判定するページがない")
        if len(df):
            print(f"合格 {int(df['pass'].sum())} / {len(df)}（1h・15m）→ {out}")
    else:
        w = wallet_summary(paths)
        print(w.to_string(index=False))
        print(w.attrs.get("note", ""))


def cmd_mam(paths, args):
    from .live.mam import export_packets, import_predictions
    if args.action == "export":
        files = export_packets(paths)
        print(f"まむへの入力 {len(files)} 件 → {paths.data_dir / 'mam' / 'outbox'}")
    else:
        print(json.dumps(import_predictions(paths, args.file), ensure_ascii=False, indent=1))


def cmd_review(paths, args):
    from datetime import date
    from .review import write_review
    if args.start or args.end:
        print(f"期間の振り返り（8週の点検など） → {write_review(paths, None, args.start, args.end)}")
        return
    month = args.month or date.today().strftime("%Y-%m")
    print(f"月次の振り返り → {write_review(paths, month)}")


def cmd_collect(paths, args):
    from .live.collector import Collector
    root = paths.data_dir / "live"
    if args.action == "tick":
        import fcntl
        root.mkdir(parents=True, exist_ok=True)
        # 見回りは1つずつ（重なると状態の書込みで落ち、取得ずみの記録も消える）。鍵はプロセスが終われば外れる
        with open(root / "tick.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({"skipped": "前の見回りがまだ動いている"}, ensure_ascii=False))
                return
            col = Collector(root)
            out = col.tick(notify=_notify)
            if col.live_pending():   # 新しいページ・取りやめ・前の回の失敗のどれかがある
                from .live.wallet import run_live
                interrupted = col.live_started()   # 前の回が最後まで終わっていない（途中で止まった・失敗した）
                try:
                    out["live"] = run_live(paths)
                    from .live.mam import export_packets
                    out["mam_packets"] = len(export_packets(paths))
                    if out["live"].get("settled") or interrupted:
                        # 前の回が精算を書いた後に止まっていると、この回の精算は0件でも月次は古いまま
                        from datetime import date, timedelta
                        from .review import write_review
                        days = [date.today(), *([date.today() - timedelta(days=1)] if interrupted else [])]
                        # 精算を書いたレースの月も（長く止まった後に予定表から外れたレースなど、前の月のことがある）
                        for month in sorted({x.strftime("%Y-%m") for x in days} | set(out["live"].get("months") or [])):
                            write_review(paths, month)
                    failed = out["live"].get("failed")   # 一部のレース・方式だけの失敗：ほかは書けている。次の回にやり直す
                    new = col.live_finished(failed or [], out["live"].get("retry_now", True))
                    if new:
                        _notify("予想・精算の一部に失敗", "; ".join(new)[:120])
                except Exception as exc:  # 予想の失敗で収集を止めない（次の回にやり直す）
                    out["live_error"] = repr(exc)
                    if col.live_finished([repr(exc)[:200]]):
                        _notify("予想・精算に失敗", repr(exc)[:120])
            print(json.dumps(out, ensure_ascii=False))
    elif args.action == "install":
        import os
        from pathlib import Path
        from .config import REPO_ROOT
        label = "com.heroines.keiba-collect"
        plist = Path("~/Library/LaunchAgents").expanduser() / f"{label}.plist"
        root.mkdir(parents=True, exist_ok=True)
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>{REPO_ROOT / 'bin' / 'keiba'}</string><string>collect</string><string>tick</string></array>
<key>StartInterval</key><integer>300</integer>
<key>StandardOutPath</key><string>{root / 'collect.log'}</string>
<key>StandardErrorPath</key><string>{root / 'collect.log'}</string>
</dict></plist>
""", encoding="utf-8")
        print(f"書いた：{plist}")
        print(f"動かす：launchctl bootstrap gui/{os.getuid()} {plist}")
        print(f"止める：launchctl bootout gui/{os.getuid()}/{label}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="keiba", description="三連複予想の検証（実購入はしない）")
    ap.add_argument("--paths", help="keiba.json の場所（既定 ~/.heroines/keiba.json）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest")
    p.add_argument("--workers", type=int, default=6)
    sub.add_parser("features")
    for name in ("evaluate", "all"):
        p = sub.add_parser(name)
        p.add_argument("--first-month", default="2025-05-01")
        p.add_argument("--holdout-start", default="2026-08-16")
        if name == "all":
            p.add_argument("--workers", type=int, default=6)
            p.add_argument("--version", default=None)
    p = sub.add_parser("report")
    p.add_argument("--version", default=None)
    sub.add_parser("verify")
    p = sub.add_parser("collect")
    p.add_argument("action", choices=["tick", "install"])
    p = sub.add_parser("mam")
    p.add_argument("action", choices=["export", "import"])
    p.add_argument("file", nargs="?")
    p = sub.add_parser("review")
    p.add_argument("--month", default=None)
    p.add_argument("--from", dest="start", default=None, help="期間の始め YYYY-MM-DD（8週の点検など）")
    p.add_argument("--to", dest="end", default=None, help="期間の終わり YYYY-MM-DD")
    p = sub.add_parser("live")
    p.add_argument("action", choices=["run", "check", "wallets"])
    p = sub.add_parser("app")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="起動したらブラウザで開く")
    p.add_argument("--store", default=None, help="購入記録の保存先（試すとき用。既定 data/app/purchases.sqlite）")
    p.add_argument("--mode", choices=["live", "check"], default="live", help="最初に見るデータ（check＝検証データ。画面の確認用）")
    p.add_argument("--notify", action="store_true", help="自信度Aと、買い目に取消馬が残るレースを発走20分前からMacに通知")
    p.add_argument("--lan", action="store_true", help="同じWi-FiのiPhoneから開けるようにする（.env の KEIBA_APP_PASSPHRASE が要る）")
    args = ap.parse_args(argv)
    paths = load_paths(args.paths)
    if args.cmd == "app":
        from .app.server import serve
        serve(paths, args.port, args.open, args.store, args.mode, args.lan, args.notify)
        return 0
    if args.cmd == "all":
        for f in (cmd_ingest, cmd_features, cmd_evaluate, cmd_report, cmd_verify):
            f(paths, args)
    else:
        {"ingest": cmd_ingest, "features": cmd_features, "evaluate": cmd_evaluate,
         "report": cmd_report, "verify": cmd_verify, "collect": cmd_collect, "live": cmd_live, "review": cmd_review, "mam": cmd_mam}[args.cmd](paths, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
