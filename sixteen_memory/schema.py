import os
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

Identifier = Annotated[StrictStr, Field(min_length=1, max_length=512)]
Text = Annotated[StrictStr, Field(min_length=1, max_length=1_000_000)]


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: Text
    timestamp: Annotated[int, Field(strict=True, ge=0, le=253402300799000)] | None = None

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class AddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Identifier
    user_id: Identifier
    session_id: Identifier
    messages: Annotated[list[Message], Field(min_length=1, max_length=1000)]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: Annotated[StrictStr, Field(min_length=1, max_length=32000)]
    user_id: Identifier
    top_k: Annotated[int, Field(strict=True, ge=1, le=100)]
    options: Annotated[list[Annotated[StrictStr, Field(max_length=8000)]], Field(max_length=100)] | None = None

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


@dataclass(frozen=True)
class Settings:
    database: str
    api_key: str
    mode: str = "hybrid"
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    dimensions: int = 1024
    enable_facts: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    chunk_chars: int = 1200
    max_chunks_per_user: int = 10000
    max_concurrent_requests: int = 2
    retention_days: int = 30
    source_commit: str = "unreleased"

    def __post_init__(self):
        if len(self.api_key) < 32 or not self.api_key.isascii() or self.api_key.isspace():
            raise ValueError("MEMORY_API_KEY must contain at least 32 ASCII characters")
        if self.mode not in {"lexical", "hybrid"}:
            raise ValueError("MEMORY_MODE must be lexical or hybrid")
        if self.embedding_model != "text-embedding-v4" or self.llm_model != "gpt-4o-mini":
            raise ValueError("This open-source entry only supports the mandated models")
        if self.mode == "hybrid" and not self.embedding_api_key:
            raise ValueError("Hybrid mode requires EMBEDDING_API_KEY")
        if self.enable_facts and not self.llm_api_key:
            raise ValueError("ENABLE_FACTS requires LLM_API_KEY")
        if self.enable_facts and self.mode != "hybrid":
            raise ValueError("Fact extraction is only supported in hybrid mode")
        if self.dimensions not in {64, 128, 256, 512, 768, 1024, 1536, 2048}:
            raise ValueError("Unsupported text-embedding-v4 dimension")
        if not 200 <= self.chunk_chars <= 2000:
            raise ValueError("CHUNK_CHARS must be between 200 and 2000")
        if self.max_chunks_per_user < 1 or self.max_concurrent_requests < 1:
            raise ValueError("Capacity and concurrency must be positive")
        if not 1 <= self.retention_days <= 30:
            raise ValueError("RETENTION_DAYS must be between 1 and 30")
        for url in (self.embedding_base_url, self.llm_base_url):
            if not url.startswith("https://"):
                raise ValueError("Model providers require HTTPS")

    @classmethod
    def from_env(cls):
        return cls(
            database=os.getenv("DATABASE_PATH", "data/memory.sqlite3"),
            api_key=os.getenv("MEMORY_API_KEY", ""),
            mode=os.getenv("MEMORY_MODE", "hybrid"),
            embedding_base_url=os.getenv("EMBEDDING_BASE_URL", cls.embedding_base_url),
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
            embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
            dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "1024")),
            enable_facts=os.getenv("ENABLE_FACTS", "false").lower() == "true",
            llm_base_url=os.getenv("LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            chunk_chars=int(os.getenv("CHUNK_CHARS", "1200")),
            max_chunks_per_user=int(os.getenv("MAX_CHUNKS_PER_USER", "10000")),
            max_concurrent_requests=int(os.getenv("MAX_CONCURRENT_REQUESTS", "2")),
            retention_days=int(os.getenv("RETENTION_DAYS", "30")),
            source_commit=os.getenv("SOURCE_COMMIT", "unreleased"),
        )
