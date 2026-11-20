"""週次の取得をまとめて実行する。GitHub Actions からも手元からも呼ぶ。"""
from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

from . import ai_summary, cftc_tff, mof_weekly
from .storage import get_store, json_bytes, to_csv_bytes

log = logging.getLogger(__name__)


def _load_previous_cftc() -> list[dict]:
    """CFTC の取得に失敗した週は、前回保存した CSV を AI 要約の材料に使う。"""
    path = Path(os.getenv("MONEYFLOW_LOCAL_DIR", "out")) / "curated" / "cftc_tff.csv"
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            for k in ("open_interest", "lev_net", "asset_mgr_net", "lev_net_chg", "asset_mgr_net_chg"):
                r[k] = float(r[k]) if r.get(k) not in (None, "") else None
            rows.append(r)
    return rows


def run_weekly() -> dict:
    store = get_store()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    summary: dict = {"collected_at_utc": datetime.now(timezone.utc).isoformat()}
    # GitHub ではコミット履歴が版管理を兼ねるので、日付付きの生データは既定で残さない
    keep_raw = os.getenv("MONEYFLOW_KEEP_RAW", "1") == "1"
    mof_rows: list[dict] = []
    cftc_rows: list[dict] = []

    # 1) 財務省 週次の対内・対外証券投資
    try:
        raw, mof_rows = mof_weekly.collect()
        if keep_raw:
            store.put(f"raw/mof_weekly/{stamp}.csv", raw)
        store.put("curated/mof_weekly.csv", to_csv_bytes(mof_rows))
        summary["mof"] = mof_weekly.headline(mof_rows) | {"rows": len(mof_rows)}
    except Exception as e:  # 片方が落ちてももう片方は続ける
        log.exception("MOF の取得に失敗")
        summary["mof_error"] = str(e)

    # 2) CFTC 建玉明細（TFF）
    try:
        cftc_rows = cftc_tff.collect()
        if keep_raw:
            store.put(f"raw/cftc_tff/{stamp}.json", json_bytes(cftc_rows))
        store.put("curated/cftc_tff.csv", to_csv_bytes(cftc_rows))
        summary["cftc"] = cftc_tff.headline(cftc_rows)
    except Exception as e:
        log.exception("CFTC の取得に失敗")
        summary["cftc_error"] = str(e)
        cftc_rows = _load_previous_cftc()  # 前回分のデータは残っているので、それを使う

    # 3) AI による要約（失敗しても前回の要約を残し、取得処理は成功扱い）
    try:
        ai = ai_summary.summarize(mof_rows, cftc_rows)
        if ai:
            store.put("curated/ai_summary.json", json_bytes(ai))
            summary["ai"] = ai["model"]
        else:
            summary["ai"] = "skipped"
            summary["ai_reason"] = ai_summary.LAST_ERROR
    except Exception as e:
        log.exception("AI 要約に失敗")
        summary["ai_error"] = str(e)

    store.put("curated/latest_summary.json", json_bytes(summary))
    return summary
