"""运行时配置：全部来自环境变量或项目根目录下的 .env。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# 缺少这些配置时服务无法工作，启动阶段直接给出明确报错
_REQUIRED_ENV = {
    "DASHSCOPE_API_KEY": "dashscope_api_key",
    "SF_API_KEY": "sf_api_key",
    "MILVUS_URI": "milvus_uri",
    "POSTGRES_URI": "postgres_uri",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 对话模型（OpenAI 兼容接口）
    dashscope_api_key: str = ""
    dashscope_base_url: str = ""
    chat_model: str = "deepseek-v4.1-flash-1017"

    # 向量化与重排（SiliconFlow）
    sf_api_key: str = ""
    sf_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # Milvus 检索
    milvus_uri: str = ""
    milvus_database: str = "rag_love"
    milvus_collection: str = ""
    milvus_user: str = "root"
    milvus_password: str = ""
    milvus_dense_field: str = "float_vector"
    milvus_sparse_field: str = "sparse_vector"
    milvus_rerank_credential: str = "siliconflow_apikey"
    search_limit: int = 10

    # LangGraph 检查点（PostgreSQL）
    postgres_uri: str = ""
    postgres_pool_max_size: int = 10

    # Web 服务
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def require_runtime_keys(self) -> None:
        missing = [env for env, field in _REQUIRED_ENV.items() if not getattr(self, field).strip()]
        if missing:
            raise RuntimeError(
                "缺少必要的环境变量：" + "、".join(missing) + "，请参考 .env.example 配置后重启。"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()