"""Idempotent provisioning: vector bucket -> vector index -> knowledge base -> data source -> ingestion.

Every step is "get or create" so the container can run on every pipeline invocation."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass

from botocore.exceptions import ClientError

from careonex_kb import config

log = logging.getLogger(__name__)


class MissingServiceRole(RuntimeError):
    pass


@dataclass
class KBState:
    bucket: str
    vector_bucket: str
    vector_bucket_arn: str
    index_name: str
    index_arn: str
    knowledge_base_id: str | None = None
    knowledge_base_arn: str | None = None
    data_source_id: str | None = None
    ingestion_job_id: str | None = None
    ingestion_status: str | None = None
    ingestion_stats: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


# --- S3 Vectors -------------------------------------------------------------------------------

def ensure_vector_bucket(s3v, name: str) -> str:
    try:
        return s3v.get_vector_bucket(vectorBucketName=name)["vectorBucket"]["vectorBucketArn"]
    except ClientError as exc:
        if _code(exc) not in ("NotFoundException", "ResourceNotFoundException", "404"):
            raise
    resp = s3v.create_vector_bucket(vectorBucketName=name, encryptionConfiguration={"sseType": "AES256"})
    log.info("created vector bucket %s", name)
    return resp["vectorBucketArn"]


def ensure_index(s3v, vector_bucket: str, index_name: str, dimension: int) -> str:
    try:
        idx = s3v.get_index(vectorBucketName=vector_bucket, indexName=index_name)["index"]
        if idx["dimension"] != dimension:
            raise RuntimeError(f"index {index_name} has dimension {idx['dimension']}, expected {dimension}; delete it to rebuild")
        return idx["indexArn"]
    except ClientError as exc:
        if _code(exc) not in ("NotFoundException", "ResourceNotFoundException", "404"):
            raise
    resp = s3v.create_index(
        vectorBucketName=vector_bucket,
        indexName=index_name,
        dataType="float32",
        dimension=dimension,
        distanceMetric="cosine",
        # Bedrock stores the chunk text under this key; keeping it non-filterable leaves the
        # per-key metadata budget for the attributes we actually filter on (program, year, ...).
        metadataConfiguration={"nonFilterableMetadataKeys": ["AMAZON_BEDROCK_TEXT"]},
    )
    log.info("created vector index %s/%s (dim %d, cosine)", vector_bucket, index_name, dimension)
    return resp["indexArn"]


# --- Bedrock Knowledge Bases ------------------------------------------------------------------

def find_knowledge_base(agent, name: str) -> dict | None:
    token = None
    while True:
        kwargs = {"maxResults": 100, **({"nextToken": token} if token else {})}
        resp = agent.list_knowledge_bases(**kwargs)
        for kb in resp.get("knowledgeBaseSummaries", []):
            if kb["name"] == name:
                return agent.get_knowledge_base(knowledgeBaseId=kb["knowledgeBaseId"])["knowledgeBase"]
        token = resp.get("nextToken")
        if not token:
            return None


def ensure_knowledge_base(agent, name: str, role_arn: str, index_arn: str, dimension: int) -> dict:
    kb = find_knowledge_base(agent, name)
    if kb:
        return kb
    try:
        resp = agent.create_knowledge_base(
            name=name,
            description="CareOneX: NJ home-care program documents (public, no PII). Chunks under chunks/ in the ac215 knowledge bucket.",
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {
                    "embeddingModelArn": config.embedding_model_arn(),
                    "embeddingModelConfiguration": {"bedrockEmbeddingModelConfiguration": {"dimensions": dimension, "embeddingDataType": "FLOAT32"}},
                },
            },
            storageConfiguration={"type": "S3_VECTORS", "s3VectorsConfiguration": {"indexArn": index_arn}},
            tags={"project": "careonex-agents", "component": "kb-sync"},
        )
    except ClientError as exc:
        msg = str(exc)
        if _code(exc) in ("ValidationException", "AccessDeniedException") and ("role" in msg.lower() or "PassRole" in msg or "assume" in msg.lower()):
            raise MissingServiceRole(
                f"Bedrock could not use the service role {role_arn}. Create it as described in TEAM_SETUP.md "
                f"(trust bedrock.amazonaws.com; read the knowledge bucket, write the vector index, invoke the embedding model). Original error: {msg}"
            ) from exc
        raise
    kb = resp["knowledgeBase"]
    log.info("created knowledge base %s (%s)", name, kb["knowledgeBaseId"])
    _wait(lambda: agent.get_knowledge_base(knowledgeBaseId=kb["knowledgeBaseId"])["knowledgeBase"]["status"], {"ACTIVE"}, {"FAILED"}, "knowledge base")
    return agent.get_knowledge_base(knowledgeBaseId=kb["knowledgeBaseId"])["knowledgeBase"]


def ensure_data_source(agent, kb_id: str, name: str, bucket_arn: str, prefix: str) -> str:
    for ds in agent.list_data_sources(knowledgeBaseId=kb_id, maxResults=100).get("dataSourceSummaries", []):
        if ds["name"] == name:
            return ds["dataSourceId"]
    resp = agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=name,
        description="Pre-chunked Markdown, one object per chunk, sidecar metadata.",
        dataSourceConfiguration={"type": "S3", "s3Configuration": {"bucketArn": bucket_arn, "inclusionPrefixes": [f"{prefix}/"]}},
        vectorIngestionConfiguration={"chunkingConfiguration": {"chunkingStrategy": "NONE"}},
        dataDeletionPolicy="DELETE",
    )
    ds_id = resp["dataSource"]["dataSourceId"]
    log.info("created data source %s (%s)", name, ds_id)
    return ds_id


def run_ingestion(agent, kb_id: str, ds_id: str, wait: bool = True, timeout_s: int = 1800) -> tuple[str, str, dict]:
    job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id, description="careonex kb-sync")["ingestionJob"]
    job_id = job["ingestionJobId"]
    log.info("started ingestion job %s", job_id)
    status, stats = job["status"], job.get("statistics", {})
    if wait:
        deadline = time.time() + timeout_s
        while status in ("STARTING", "IN_PROGRESS") and time.time() < deadline:
            time.sleep(10)
            job = agent.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id)["ingestionJob"]
            status, stats = job["status"], job.get("statistics", {})
            log.info("ingestion %s: %s", job_id, status)
        if status != "COMPLETE":
            for reason in job.get("failureReasons", []):
                log.error("ingestion failure: %s", reason)
    return job_id, status, stats


def _wait(get_status, ok: set[str], bad: set[str], what: str, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = get_status()
        if st in ok:
            return
        if st in bad:
            raise RuntimeError(f"{what} entered status {st}")
        time.sleep(5)
    raise TimeoutError(f"{what} did not become ready in {timeout_s}s")


# --- Orchestration ---------------------------------------------------------------------------

def sync(sess, wait: bool = True, skip_ingest: bool = False) -> KBState:
    if config.CHUNKS_PREFIX != "chunks":
        if (config.CONFIG_KEY == "config/knowledge-base.json"
                or config.KB_NAME == "ac215-program-kb"
                or config.INDEX_NAME == "program-kb"
                or not config.CHUNKS_PREFIX.startswith("experiments/chunking/")):
            raise ValueError("staging KB must use distinct KB name, vector index, config key and experimental chunks prefix")
    s3 = sess.client("s3")
    s3v = sess.client("s3vectors")
    agent = sess.client("bedrock-agent")

    bucket = config.bucket_name()
    vb = config.vector_bucket_name()
    vb_arn = ensure_vector_bucket(s3v, vb)
    index_arn = ensure_index(s3v, vb, config.INDEX_NAME, config.EMBEDDING_DIMENSIONS)
    state = KBState(bucket=bucket, vector_bucket=vb, vector_bucket_arn=vb_arn, index_name=config.INDEX_NAME, index_arn=index_arn)

    kb = ensure_knowledge_base(agent, config.KB_NAME, config.kb_role_arn(), index_arn, config.EMBEDDING_DIMENSIONS)
    state.knowledge_base_id, state.knowledge_base_arn = kb["knowledgeBaseId"], kb["knowledgeBaseArn"]
    state.data_source_id = ensure_data_source(agent, kb["knowledgeBaseId"], config.DATA_SOURCE_NAME, f"arn:aws:s3:::{bucket}", config.CHUNKS_PREFIX)

    # Publish the ids where the retrieve service (and humans) can find them.
    s3.put_object(Bucket=bucket, Key=config.CONFIG_KEY, Body=json.dumps(state.as_dict(), indent=2).encode(), ContentType="application/json")

    if not skip_ingest:
        state.ingestion_job_id, state.ingestion_status, state.ingestion_stats = run_ingestion(agent, kb["knowledgeBaseId"], state.data_source_id, wait=wait)
        s3.put_object(Bucket=bucket, Key=config.CONFIG_KEY, Body=json.dumps(state.as_dict(), indent=2).encode(), ContentType="application/json")
    return state
