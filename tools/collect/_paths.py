"""収集スクリプト用の場所。~/.heroines/paths.json の keiba.collect_root などから読む（/tmp や /Users を直接書かない）。"""
import json
import os
from pathlib import Path


def _cfg() -> dict:
    f = Path(os.environ.get("KEIBA_PATHS") or Path("~/.heroines/paths.json").expanduser())
    return json.loads(f.read_text(encoding="utf-8")).get("keiba", {}) if f.exists() else {}


def _get(key: str, default: str | None = None) -> Path:
    value = _cfg().get(key, default)
    if not value:
        raise SystemExit(f"~/.heroines/paths.json の keiba.{key} を設定してください")
    return Path(os.path.expanduser(value))


def collect_root() -> Path:
    return _get("collect_root", "~/競馬")


def heroines_2025_artifacts() -> Path:
    return _get("heroines_2025_artifacts")


def jra_pdf_root() -> Path:
    return _get("jra_pdf_root")


def prior_jra_archive() -> Path:
    return _get("prior_jra_archive")
