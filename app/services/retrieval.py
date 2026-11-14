"""Milvus 混合检索：稠密向量 + 稀疏向量召回，再用交叉编码器重排。"""

import json
import logging
from time import perf_counter
from functools import lru_cache
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openai import OpenAI
from pymilvus import AnnSearchRequest, Function, FunctionType, MilvusClient

from app.config import get_settings

logger = logging.getLogger(__name__)


def find_common_with_size(A, B,size:int):
    """Return chunks appearing in every query group of both searches."""
    # MilvusClient.search returns list[list[hit]], one inner list per query.
    # A hit is commonly {"id": ..., "entity": {...}}, but some versions return
    # the entity fields directly, so support both forms.
    groups = []
    for left, right in zip(A or [], B or []):
        records = []
        for hit in (*left, *right):
            if not isinstance(hit, dict):
                continue
            entity = hit.get("entity", hit)
            if isinstance(entity, dict) and entity.get("chunk_id") is not None:
                records.append(entity)
        if records:
            groups.append(records)

    if not groups:
        return []

    common_ids = set.intersection(*(
        {item["chunk_id"] for item in records} for records in groups
    ))
    result = {}
    num=0
    should_break = False
    for records in groups:
        if should_break: break
        for item in records:
            if item["chunk_id"] in common_ids:
                result.setdefault(item["chunk_id"], item)
                num+=1
                if num >= size:
                    should_break=True
                    break
    return list(result.values())


@lru_cache(maxsize=1)
def _dense_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(api_key=settings.sf_api_key, base_url=settings.sf_base_url)


@lru_cache(maxsize=1)
def _milvus_client() -> MilvusClient:
    return MilvusClient(
        uri=get_settings().milvus_uri,
        user=get_settings().milvus_user,
        password=get_settings().milvus_password,
        db_name=get_settings().milvus_database,
    )


def _embed(texts: list[str]) -> list[list[float]]:
    started = perf_counter()
    settings = get_settings()
    response = _dense_client().embeddings.create(input=texts, model=settings.embedding_model)
    embeddings = [item.embedding for item in response.data]
    logger.debug(
        "向量化完成：输入数量=%d，输出数量=%d，耗时=%.3fs",
        len(texts),
        len(embeddings),
        perf_counter() - started,
    )
    return embeddings


def _rerank_function(queries: list[str]) -> Function:
    settings = get_settings()
    return Function(
        name="siliconflow_semantic_ranker",
        input_field_names=["text"],
        function_type=FunctionType.RERANK,
        params={
            "reranker": "model",
            "provider": "siliconflow",
            "model_name": settings.rerank_model,
            "queries": queries,
            "max_client_batch_size": 128,
            "max_chunks_per_doc": 5,
            "overlap_tokens": 50,
            "credential": settings.milvus_rerank_credential,
        },
    )


def _rerank_results(
        common: list[dict[str, Any]], queries: list[str], limit: int
) -> list[dict[str, Any]]:
    """Use SiliconFlow's rerank API to order already-retrieved chunks."""
    if not common:
        logger.info("重排跳过：没有共同召回片段")
        return []

    settings = get_settings()
    started = perf_counter()
    logger.debug(
        "重排请求开始：查询数量=%d，文档数量=%d，返回数量=%d，模型=%s",
        len(queries),
        len(common),
        min(limit, len(common)),
        settings.rerank_model,
    )
    request = Request(
        url=f"{settings.sf_base_url.rstrip('/')}/rerank",
        data=json.dumps({
            "model": settings.rerank_model,
            "query": " ".join(queries),
            "documents": [item["text"] for item in common],
            "top_n": min(limit, len(common)),
            "return_documents": False,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.sf_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as exc:
        logger.error(
            "SiliconFlow 重排失败：HTTP 状态码=%s，耗时=%.3fs",
            exc.code,
            perf_counter() - started,
        )
        raise RuntimeError(
            f"SiliconFlow rerank 请求失败，HTTP 状态码：{exc.code}"
        ) from exc
    except URLError as exc:
        logger.error(
            "SiliconFlow 重排失败：网络错误=%s，耗时=%.3fs",
            exc.reason,
            perf_counter() - started,
        )
        raise RuntimeError("SiliconFlow rerank 网络请求失败") from exc

    ranked = []
    for result in payload.get("results", []):
        index = result.get("index")
        if isinstance(index, int) and 0 <= index < len(common):
            item = dict(common[index])
            item["rerank_score"] = result.get("relevance_score")
            ranked.append(item)
    logger.info(
        "重排完成：输入=%d，输出=%d，耗时=%.3fs",
        len(common),
        len(ranked),
        perf_counter() - started,
    )
    return ranked


def hybrid_search(
        queries: list[str], mode: Literal["or", "and"], start_ts: int | None = None, end_ts: int | None = None,
        limit: int | None = None
) -> list[dict[str, Any]]:
    """返回按相关度排序的聊天片段"""
    started = perf_counter()
    settings = get_settings()
    limit = limit or settings.search_limit
    conditions = []
    if start_ts is not None:
        conditions.append(f"start_ts >= {start_ts}")
    if end_ts is not None:
        conditions.append(f"end_ts <= {end_ts}")
    expr = " && ".join(conditions) if conditions else ""
    logger.info(
        "检索开始：模式=%s，查询数量=%d，limit=%d，时间范围=[%s, %s]",
        mode, len(queries), limit, start_ts, end_ts,
    )
    if mode == "and":
        and_limit = len(queries) * limit
        dense_result = _milvus_client().search(
            collection_name=settings.milvus_collection,
            data=_embed(queries),
            anns_field=settings.milvus_dense_field,
            limit=and_limit,
            filter=expr,
            output_fields=["text", "start_ts", "end_ts", "chunk_id", "session_id", "session_chunk_count"]
        )
        sparse_result = _milvus_client().search(
            collection_name=settings.milvus_collection,
            data=queries,
            anns_field=settings.milvus_sparse_field,
            search_params={
                "metric_type": "BM25"
            },
            limit=and_limit,
            filter=expr,
            output_fields=["text", "start_ts", "end_ts", "chunk_id", "session_id", "session_chunk_count"]
        )
        common = find_common_with_size(dense_result, sparse_result,limit)
        fragments = _rerank_results(common, queries, limit)
        logger.info(
            "检索完成：模式=and，稠密召回=%d，稀疏召回=%d，共同片段=%d，"
            "返回=%d，耗时=%.3fs",
            sum(len(group) for group in dense_result),
            sum(len(group) for group in sparse_result),
            len(common),
            len(fragments),
            perf_counter() - started,
        )
        return fragments


    requests = [
        AnnSearchRequest(
            data=_embed(queries),
            anns_field=settings.milvus_dense_field,
            param={},
            limit=limit,
            filter=expr,
        ),
        AnnSearchRequest(
            data=queries,
            anns_field=settings.milvus_sparse_field,
            param={},
            limit=limit,
            filter=expr,
        )
    ]

    results = _milvus_client().hybrid_search(
        collection_name=settings.milvus_collection,
        reqs=requests,
        ranker=_rerank_function(queries),
        limit=limit,
        output_fields=["text", "start_ts", "end_ts"],
    )

    # hybrid_search 按查询分组返回；本项目每次只查一条组合查询
    fragments = [hit["entity"] for hit in results[0]] if results else []
    logger.info(
        "检索完成：模式=or，返回=%d，耗时=%.3fs",
        len(fragments),
        perf_counter() - started,
    )

    return fragments
