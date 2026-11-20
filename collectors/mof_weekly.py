"""財務省「対外及び対内証券売買契約等の状況（週次・指定報告機関ベース）」の取得と整形。

単位: 億円。ネットは + が取得超（買い越し）、- が処分超（売り越し）。
  out_* = 対外証券投資（日本の居住者が海外の証券を売買）
  in_*  = 対内証券投資（海外の非居住者が日本の証券を売買）
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date

import requests

MOF_WEEKLY_CSV_URL = (
    "https://www.mof.go.jp/policy/international_policy/reference/"
    "itn_transactions_in_securities/week.csv"
)

COLUMNS = [
    "out_equity_acq", "out_equity_disp", "out_equity_net",
    "out_ltdebt_acq", "out_ltdebt_disp", "out_ltdebt_net",
    "out_subtotal_net",
    "out_stdebt_acq", "out_stdebt_disp", "out_stdebt_net",
    "out_total_net",
    "in_equity_acq", "in_equity_disp", "in_equity_net",
    "in_ltdebt_acq", "in_ltdebt_disp", "in_ltdebt_net",
    "in_subtotal_net",
    "in_stdebt_acq", "in_stdebt_disp", "in_stdebt_net",
    "in_total_net",
]

# 例: "2005.1.2~1.8" / "2013.12.29~2014.1.4"（全角を正規化した後の形）
_PERIOD_RE = re.compile(
    r"^(\d{4})\.(\d{1,2})\.(\d{1,2})~(?:(\d{4})\.)?(\d{1,2})\.(\d{1,2})$"
)


def download_raw(timeout: int = 30) -> bytes:
    resp = requests.get(MOF_WEEKLY_CSV_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", errors="replace")


def _normalize(s: str) -> str:
    return (
        s.replace("．", ".").replace("～", "~").replace("〜", "~")
        .replace(" ", "").replace("　", "").strip()
    )


def parse_period(text: str) -> tuple[date, date] | None:
    m = _PERIOD_RE.match(_normalize(text))
    if not m:
        return None
    y1, m1, d1, y2, m2, d2 = m.groups()
    start = date(int(y1), int(m1), int(d1))
    end_year = int(y2) if y2 else (start.year + 1 if int(m2) < start.month else start.year)
    return start, date(end_year, int(m2), int(d2))


def _to_number(cell: str) -> float | None:
    s = cell.replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "－", "…"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse(text: str) -> list[dict]:
    rows: list[dict] = []
    for cells in csv.reader(io.StringIO(text)):
        if not cells:
            continue
        period = parse_period(cells[0])
        if period is None:
            continue
        values = [_to_number(c) for c in cells[1:1 + len(COLUMNS)]]
        if len(values) < len(COLUMNS):
            continue
        row = {"week_start": period[0].isoformat(), "week_end": period[1].isoformat()}
        row.update(dict(zip(COLUMNS, values)))
        rows.append(row)
    rows.sort(key=lambda r: r["week_start"])
    return rows


def headline(rows: list[dict]) -> dict:
    """最新週のうち、投資判断で見ることが多い項目だけを抜き出す。"""
    if not rows:
        return {}
    last = rows[-1]
    return {
        "week": f'{last["week_start"]}〜{last["week_end"]}',
        "海外勢_日本株_ネット億円": last["in_equity_net"],
        "海外勢_日本中長期債_ネット億円": last["in_ltdebt_net"],
        "国内勢_外国株_ネット億円": last["out_equity_net"],
        "国内勢_外国中長期債_ネット億円": last["out_ltdebt_net"],
    }


def collect() -> tuple[bytes, list[dict]]:
    raw = download_raw()
    return raw, parse(decode(raw))
