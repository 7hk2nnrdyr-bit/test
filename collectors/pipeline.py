"""週次の取得をまとめて実行する。GitHub Actions からも手元からも呼ぶ。"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from . import ai_summary, cftc_tff, mof_weekly
from .storage import get_store, json_bytes, to_csv_bytes

log = logging.getLogger(__name__)


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
