"""Milvus 混合检索：稠密向量 + 稀疏向量召回，再用交叉编码器重排。"""

from functools import lru_cache
from typing import Any

from openai import OpenAI
from pymilvus import AnnSearchRequest, Function, FunctionType, MilvusClient

from app.config import get_settings


@lru_cache(maxsize=1)
def _dense_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(api_key=settings.sf_api_key, base_url=settings.sf_base_url)


@lru_cache(maxsize=1)
def _milvus_client() -> MilvusClient:
    return MilvusClient(uri=get_settings().milvus_uri, user=get_settings().milvus_user,
                        password=get_settings().milvus_password, db_name=get_settings().milvus_database)


def _embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    response = _dense_client().embeddings.create(input=texts, model=settings.embedding_model)
    return [item.embedding for item in response.data]


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


def hybrid_search(queries: list[str], start_ts: int, end_ts: int, limit: int | None = None) -> list[dict[str, Any]]:
    """返回按相关度排序的聊天片段，每个片段包含 text / start_ts / end_ts。"""
    settings = get_settings()
    limit = limit or settings.search_limit
    conditions = []
    if start_ts is not None:
        conditions.append(f"start_ts >= {start_ts}")
    if end_ts is not None:
        conditions.append(f"end_ts <= {end_ts}")
    expr = " && ".join(conditions) if conditions else ""
    requests = [
        AnnSearchRequest(
            data=_embed(queries), anns_field=settings.milvus_dense_field, param={}, limit=limit, filter=expr
        ),
        AnnSearchRequest(
            data=queries, anns_field=settings.milvus_sparse_field, param={}, limit=limit, filter=expr
        ),
    ]

    results = _milvus_client().hybrid_search(
        collection_name=settings.milvus_collection,
        reqs=requests,
        ranker=_rerank_function(queries),
        limit=limit,
        output_fields=["text", "start_ts", "end_ts"],
    )
    # hybrid_search 按查询分组返回；本项目每次只查一条组合查询
    return [hit["entity"] for hit in results[0]] if results else []
