"""The source catalog (ragfile_list.csv) and the S3 object layout derived from it.

The catalog is the team's inventory of approved public documents. Every row
becomes one object under raw/<source_id>/<file_name> plus a sidecar
<file_name>.metadata.json in the format Bedrock Knowledge Bases reads for
metadata filtering.
"""

from __future__ import annotations

import csv
import hashlib
import json
import mimetypes
from dataclasses import dataclass, fields
from pathlib import Path

from careonex_data.config import RAW_PREFIX

JURISDICTION_BY_SOURCE = {"nj_dmahs": "NJ", "nj_doas": "NJ", "nj_dds": "NJ", "careonex_curated": "NJ", "medicare_cms": "US", "va": "US"}


@dataclass(frozen=True)
class SourceRow:
    source_id: str
    publisher: str
    program: str
    source_url: str
    final_url: str
    file_name: str
    kind: str
    title: str
    effective_date: str
    fetch_date: str
    http_last_modified: str
    bytes: str
    sha256: str
    pages: str
    chars: str
    status: str
    note: str

    @property
    def year(self) -> int | None:
        for d in (self.effective_date, self.http_last_modified, self.fetch_date):
            if d and len(d) >= 4 and d[:4].isdigit():
                return int(d[:4])
        return None

    @property
    def effective_date_estimated(self) -> bool:
        # The crawler fell back to the fetch date, or a human set it by hand.
        return (not self.effective_date) or self.effective_date == self.fetch_date or "set by hand" in self.note

    @property
    def jurisdiction(self) -> str:
        return JURISDICTION_BY_SOURCE.get(self.source_id, "US")

    @property
    def object_key(self) -> str:
        return f"{RAW_PREFIX}/{self.source_id}/{self.file_name}"

    @property
    def sidecar_key(self) -> str:
        return self.object_key + ".metadata.json"

    @property
    def content_type(self) -> str:
        guessed, _ = mimetypes.guess_type(self.file_name)
        return guessed or {"pdf": "application/pdf", "html": "text/html", "md": "text/markdown; charset=utf-8"}.get(self.kind, "application/octet-stream")


def load_catalog(path: str | Path) -> list[SourceRow]:
    names = {f.name for f in fields(SourceRow)}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for raw in reader:
            clean = {k.strip(): (v or "").strip() for k, v in raw.items() if k and k.strip() in names}
            missing = names - clean.keys()
            for m in missing:
                clean[m] = ""
            rows.append(SourceRow(**clean))
    return rows


def catalog_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metadata_attributes(row: SourceRow) -> dict:
    """Sidecar content in the Bedrock Knowledge Bases format."""
    attrs = {
        "source_id": row.source_id,
        "publisher": row.publisher,
        "program": row.program,
        "title": row.title,
        "kind": row.kind,
        "source_url": row.source_url,
        "final_url": row.final_url or row.source_url,
        "effective_date": row.effective_date,
        "effective_date_estimated": row.effective_date_estimated,
        "fetch_date": row.fetch_date,
        "sha256": row.sha256,
        "jurisdiction": row.jurisdiction,
        "county": "all",
        "audience": "consumer",
    }
    if row.year is not None:
        attrs["year"] = row.year
    if row.http_last_modified:
        attrs["http_last_modified"] = row.http_last_modified
    if row.note:
        attrs["note"] = row.note
    return {"metadataAttributes": attrs}


def sidecar_bytes(row: SourceRow) -> bytes:
    return json.dumps(metadata_attributes(row), indent=2, ensure_ascii=False).encode("utf-8")
