"""CFTC 建玉明細（Traders in Financial Futures, 先物のみ）の取得と整形。

レバレッジ勢（ヘッジファンド等）と機関投資家（年金・運用会社等）の
ネットポジション（ロング − ショート、枚数）を市場ごとに週次で取る。
"""
from __future__ import annotations

import logging
import time

import requests

TFF_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"

# 市場名の部分一致パターン（SoQL の LIKE）。0件ならログに出るので名前を調整する。
MARKETS = {
    "JPY": "JAPANESE YEN -%",
    "EUR": "EURO FX -%",
    "SP500": "E-MINI S&P 500 -%",
    "UST10Y": "UST 10Y NOTE -%",
    "NIKKEI_YEN": "NIKKEI STOCK AVERAGE YEN%",
}

# データセットの列名が版によって違っても拾えるよう候補を並べる
FIELDS = {
    "lev_long": ["lev_money_positions_long", "lev_money_positions_long_all"],
    "lev_short": ["lev_money_positions_short", "lev_money_positions_short_all"],
    "am_long": ["asset_mgr_positions_long", "asset_mgr_positions_long_all"],
    "am_short": ["asset_mgr_positions_short", "asset_mgr_positions_short_all"],
    "open_interest": ["open_interest_all", "open_interest"],
}

log = logging.getLogger(__name__)


def _pick(rec: dict, candidates: list[str]) -> float | None:
    for key in candidates:
        if key in rec and rec[key] not in (None, ""):
            try:
                return float(rec[key])
            except ValueError:
                return None
    return None


def fetch_market(code: str, pattern: str, since: str, timeout: int = 40) -> list[dict]:
    params = {
        "$where": (
            f"report_date_as_yyyy_mm_dd >= '{since}' "
            f"AND upper(market_and_exchange_names) like '{pattern}'"
        ),
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": 5000,
    }
    last_err: Exception | None = None
    for attempt in range(2):  # CFTC のサーバーは時々応答が遅いので、1回だけ待って再試行
        try:
            resp = requests.get(TFF_URL, params=params, timeout=timeout)
            resp.raise_for_status()
            records = resp.json()
            break
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last_err = e
            log.warning("CFTC %s 取得失敗（%d回目）: %s", code, attempt + 1, e)
            if attempt == 0:
                time.sleep(10)
    else:
        raise last_err
    if not records:
        log.warning("CFTC: %s に一致する市場がありません（%s）", code, pattern)

    rows: list[dict] = []
    prev: dict | None = None
    for rec in records:
        lev_l, lev_s = _pick(rec, FIELDS["lev_long"]), _pick(rec, FIELDS["lev_short"])
        am_l, am_s = _pick(rec, FIELDS["am_long"]), _pick(rec, FIELDS["am_short"])
        row = {
            "market": code,
            "market_name": rec.get("market_and_exchange_names"),
            "report_date": (rec.get("report_date_as_yyyy_mm_dd") or "")[:10],
            "open_interest": _pick(rec, FIELDS["open_interest"]),
            "lev_net": None if lev_l is None or lev_s is None else lev_l - lev_s,
            "asset_mgr_net": None if am_l is None or am_s is None else am_l - am_s,
        }
        for k in ("lev_net", "asset_mgr_net"):
            row[f"{k}_chg"] = (
                row[k] - prev[k]
                if prev and row[k] is not None and prev[k] is not None
                else None
            )
        rows.append(row)
        prev = row
    return rows


def collect(since: str = "2020-01-01", budget_sec: int = 240) -> list[dict]:
    """全市場を取得する。サーバーが応答しない日は早めに打ち切り、例外を投げる。

    打ち切った場合、呼び出し側（pipeline）は前回保存したデータで続行する。
    1市場でも取得に失敗したら、残りも同じ状態とみなして試さない。
    """
    start = time.monotonic()
    rows: list[dict] = []
    for code, pattern in MARKETS.items():
        if time.monotonic() - start > budget_sec:
            raise TimeoutError(f"CFTC の応答が遅いため {budget_sec} 秒で打ち切り")
        rows.extend(fetch_market(code, pattern, since))
    return rows


def headline(rows: list[dict]) -> dict:
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["market"]] = r
    return {
        code: {
            "date": r["report_date"],
            "レバレッジ勢_ネット": r["lev_net"],
            "前週差": r["lev_net_chg"],
        }
        for code, r in latest.items()
    }
