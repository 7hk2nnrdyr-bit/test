"""保存先の切り替え。通常はローカルフォルダ（GitHub ではリポジトリの data/）に書く。"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path


def to_csv_bytes(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")  # Excel でも文字化けしないように BOM 付き


class LocalStore:
    def __init__(self, root: str = "out"):
        self.root = Path(root)

    def put(self, path: str, data: bytes) -> str:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)


class BlobStore:
    def __init__(self, conn_str: str, container: str):
        from azure.storage.blob import BlobServiceClient

        svc = BlobServiceClient.from_connection_string(conn_str)
        self.container = svc.get_container_client(container)
        if not self.container.exists():
            self.container.create_container()

    def put(self, path: str, data: bytes) -> str:
        self.container.upload_blob(path, data, overwrite=True)
        return f"{self.container.container_name}/{path}"


def get_store():
    conn = os.getenv("MONEYFLOW_STORAGE") or os.getenv("AzureWebJobsStorage")
    if conn and conn != "UseDevelopmentStorage=true":
        return BlobStore(conn, os.getenv("MONEYFLOW_CONTAINER", "moneyflow"))
    return LocalStore(os.getenv("MONEYFLOW_LOCAL_DIR", "out"))


def json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
