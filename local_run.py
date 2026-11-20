"""python local_run.py → ./out（または MONEYFLOW_LOCAL_DIR）に CSV と要約を書き出す。"""
import json
import logging
import sys

from collectors.pipeline import run_weekly

logging.basicConfig(level=logging.INFO)
summary = run_weekly()
print(json.dumps(summary, ensure_ascii=False, indent=2))

# 両方とも失敗したら異常終了にして、GitHub Actions 上で赤く表示させる
if "mof_error" in summary and "cftc_error" in summary:
    sys.exit(1)
