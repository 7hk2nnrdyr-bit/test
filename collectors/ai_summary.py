"""GitHub Models で週次の資金フローを初心者向けに要約する。

GitHub Actions 上では、ワークフローに `permissions: models: read` を書き、
GITHUB_TOKEN を環境変数で渡すだけで使える（APIキーの登録は不要）。
トークンが無い・AIが失敗した場合は None を返し、データ取得自体は止めない。
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

import requests

ENDPOINT = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODELS = ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"]

MARKET_LABELS = {
    "JPY": "日本円", "EUR": "ユーロ", "SP500": "米国株S&P500",
    "UST10Y": "米10年国債", "NIKKEI_YEN": "日経平均",
}

SYSTEM_PROMPT = """あなたは個人投資家向けに「お金の流れ」を解説するアナリストです。
読み手は投資の初心者で、「買い越し＝買った額の方が多い＝お金が入ってきた」「売り越し＝お金が出ていった」程度の知識です。

渡されるデータ:
- mof: 財務省の週次統計（単位は億円、＋は買い越し、−は売り越し）
  - foreign_jp_equity: 海外勢による日本株
  - foreign_jp_bonds: 海外勢による日本の債券
  - domestic_foreign_equity: 国内勢による外国株
  - domestic_foreign_bonds: 国内勢による外国の債券
- cftc: 先物市場の持ち高（単位は枚）
  - lev_net: ヘッジファンド等の短期筋（＋は買い持ち、−は売り持ち。日本円なら＋は円高に賭けている）
  - lev_pct_3y: 直近3年の中で今の水準が何%の位置か（0に近いほど売りに偏り、100に近いほど買いに偏り）

ルール:
- データにある数字だけを使う。数字は億円・兆円で読みやすく書く
- 1週間だけの動きと、4週・13週の流れを区別する
- 原因の推測は「〜の可能性があります」と書く。断定しない
- 「買うべき」「売るべき」などの売買の推奨はしない
- 専門用語を使うときは短く言い換える
- 次のJSONだけを出力する（前後に文章や```を付けない）

{"headline": "今週の一番大事な動きを一文で（45字以内）",
 "points": ["読み解きのポイントを3〜4個（各90字以内）"],
 "watch": "来週以降に注目するとよい点（90字以内）"}"""

log = logging.getLogger(__name__)


def _round(v):
    return None if v is None else round(v)


def _sum(rows, key):
    return round(sum((r.get(key) or 0) for r in rows))


def build_context(mof_rows: list[dict], cftc_rows: list[dict]) -> dict:
    keys = {
        "foreign_jp_equity": "in_equity_net",
        "foreign_jp_bonds": "in_ltdebt_net",
        "domestic_foreign_equity": "out_equity_net",
        "domestic_foreign_bonds": "out_ltdebt_net",
    }
    recent = mof_rows[-8:]
    mof = {
        "unit": "億円",
        "weeks": [
            {"week": f'{r["week_start"]}〜{r["week_end"]}', **{k: _round(r.get(c)) for k, c in keys.items()}}
            for r in recent
        ],
        "sum_4w": {k: _sum(mof_rows[-4:], c) for k, c in keys.items()},
        "sum_13w": {k: _sum(mof_rows[-13:], c) for k, c in keys.items()},
    }

    cftc = {}
    by_market: dict[str, list[dict]] = {}
    for r in cftc_rows:
        by_market.setdefault(r["market"], []).append(r)
    for code, rows in by_market.items():
        rows = sorted((r for r in rows if r.get("lev_net") is not None), key=lambda r: r["report_date"])
        if not rows:
            continue
        last = rows[-1]
        hist = [r["lev_net"] for r in rows[-156:]]
        pct = round(100 * sum(1 for v in hist if v <= last["lev_net"]) / len(hist))
        four_ago = rows[-5]["lev_net"] if len(rows) >= 5 else None
        cftc[MARKET_LABELS.get(code, code)] = {
            "date": last["report_date"],
            "lev_net": _round(last["lev_net"]),
            "lev_change_4w": None if four_ago is None else _round(last["lev_net"] - four_ago),
            "lev_pct_3y": pct,
            "asset_mgr_net": _round(last.get("asset_mgr_net")),
        }
    return {"mof": mof, "cftc": cftc}


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def _valid(d: dict) -> bool:
    return (
        isinstance(d.get("headline"), str) and d["headline"].strip()
        and isinstance(d.get("points"), list) and all(isinstance(p, str) for p in d["points"])
        and isinstance(d.get("watch", ""), str)
    )


def summarize(mof_rows: list[dict], cftc_rows: list[dict]) -> dict | None:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        log.info("GITHUB_TOKEN が無いので AI 要約はスキップ")
        return None
    if not mof_rows:
        return None

    context = build_context(mof_rows, cftc_rows)
    models = [m for m in [os.getenv("MONEYFLOW_AI_MODEL")] + DEFAULT_MODELS if m]
    seen = set()
    for model in [m for m in models if not (m in seen or seen.add(m))]:
        try:
            resp = requests.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                    ],
                },
                timeout=90,
            )
            if resp.status_code >= 400:
                log.warning("AI(%s) HTTP %s: %s", model, resp.status_code, resp.text[:300])
                continue
            data = _parse_json(resp.json()["choices"][0]["message"]["content"])
            if not _valid(data):
                log.warning("AI(%s) の出力形式が想定外: %s", model, str(data)[:300])
                continue
            return {
                "headline": data["headline"].strip(),
                "points": [p.strip() for p in data["points"] if p.strip()][:5],
                "watch": (data.get("watch") or "").strip(),
                "based_on_week": mof_rows[-1]["week_end"],
                "model": model,
                "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            }
        except Exception:
            log.exception("AI(%s) の呼び出しに失敗", model)
    return None
