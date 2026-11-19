"""データの置き場所。コードに /Users/... を書かず、~/.heroines/paths.json（または KEIBA_PATHS）から読む。

paths.json の "keiba" の下に置くキー（すべて任意。~ は展開する）：
  integrated_dir   馬ID候補の統合版フォルダ（combined/runner_candidates.jsonl を含む）
  qa_zip           着順・時計・展開の統合ZIP（data/runner_results.jsonl, data/race_flow.jsonl）
  myu_zips         netkeiba結果表を含むZIPのglob（複数可）
  heroines_dirs    HEROINES収集フォルダのglob（各レースに raw/result.html）
  noa_v3_dir       のあ v3（races.jsonl）
  jra_pdf_dirs     JRA成績PDFを探すフォルダ（複数可）
  collect_root     収集スクリプトの作業フォルダ（tools/collect 用）
  data_dir         このパッケージの出力先（既定：リポジトリ直下の data/）
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS_FILE = Path("~/.heroines/paths.json").expanduser()


def _expand(value: str) -> Path:
    return Path(os.path.expanduser(value))


def _globs(values) -> list[Path]:
    if isinstance(values, str):
        values = [values]
    out: list[Path] = []
    for v in values or []:
        out.extend(sorted(Path(p) for p in glob.glob(os.path.expanduser(v))))
    return out


@dataclass
class Paths:
    integrated_dir: Path | None = None
    qa_zip: Path | None = None
    myu_zips: list[Path] = field(default_factory=list)
    heroines_dirs: list[Path] = field(default_factory=list)
    noa_v3_dir: Path | None = None
    jra_pdf_dirs: list[Path] = field(default_factory=list)
    collect_root: Path | None = None
    data_dir: Path = REPO_ROOT / "data"

    @property
    def tables_dir(self) -> Path:
        return self.data_dir / "tables"

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / "ledger.sqlite"

    @property
    def report_dir(self) -> Path:
        return self.data_dir / "report"


def load_paths(path: str | os.PathLike | None = None) -> Paths:
    file = Path(path or os.environ.get("KEIBA_PATHS") or DEFAULT_PATHS_FILE)
    raw: dict = {}
    if file.exists():
        raw = json.loads(file.read_text(encoding="utf-8")).get("keiba", {})
    p = Paths()
    for key in ("integrated_dir", "qa_zip", "noa_v3_dir", "collect_root"):
        if raw.get(key):
            setattr(p, key, _expand(raw[key]))
    p.myu_zips = _globs(raw.get("myu_zips"))
    p.heroines_dirs = _globs(raw.get("heroines_dirs"))
    p.jra_pdf_dirs = _globs(raw.get("jra_pdf_dirs"))
    if raw.get("data_dir"):
        p.data_dir = _expand(raw["data_dir"])
    return p
