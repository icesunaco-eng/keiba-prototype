"""JRA成績PDF（日・場別）を、のあ v3 形式のレース表（PDF1本＝races/<名前>.json）にする。

中身は のあ の pdf_text2.py（PDF→文字片）と parse_report2.py（文字片→レース表）。通信しない。
使い方: python3 tools/jra_pdf/convert.py <PDFのフォルダ> <出力フォルダ> [pdf_manifest.csv]
CMap（UniJIS-UTF32-H）の場所は ~/.heroines/paths.json の keiba.aj1_cmap から読む。
"""
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(pdf_dir, out_dir, manifest=None):
    cfg = json.loads(Path(os.environ.get("KEIBA_PATHS") or Path("~/.heroines/paths.json").expanduser()).read_text()).get("keiba", {})
    env = {**os.environ, "AJ1_CMAP": os.path.expanduser(cfg["aj1_cmap"])}
    out = Path(out_dir)
    for d in ("pieces", "races", "meta"):
        (out / d).mkdir(parents=True, exist_ok=True)
    info = {}
    if manifest:
        for r in csv.DictReader(open(manifest, encoding="utf-8-sig")):
            info[Path(r["pdf_path"]).name] = {"url": r["source_url"], "sha256": r["sha256"], "acquired_at": None,
                                              "bytes": int(r["bytes"]), "published_at": None}
    ok = fail = 0
    for f in sorted(Path(pdf_dir).glob("*.pdf")):
        meta = out / "meta" / f"{f.name}.meta.json"
        if not meta.exists():
            m = info.get(f.name) or (json.loads(Path(str(f) + ".meta.json").read_text()) if Path(str(f) + ".meta.json").exists() else None)
            if m is None:
                print(f"meta が無いので飛ばす: {f.name}")
                continue
            meta.write_text(json.dumps(m, ensure_ascii=False))
        pieces, races = out / "pieces" / f"{f.stem}.json", out / "races" / f"{f.stem}.json"
        if races.exists():
            continue
        try:
            subprocess.run([sys.executable, "-I", str(HERE / "pdf_text2.py"), str(f), str(pieces)], check=True, capture_output=True, env=env)
            subprocess.run([sys.executable, "-I", str(HERE / "parse_report2.py"), str(pieces), str(meta), str(races)], check=True, capture_output=True, env=env)
            ok += 1
        except subprocess.CalledProcessError as exc:
            fail += 1
            print(f"失敗: {f.name}: {exc.stderr.decode()[-300:]}")
    print(f"変換 {ok} 本・失敗 {fail} 本 → {out / 'races'}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
