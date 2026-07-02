"""从环境变量加载的全局 Settings（单例）。"""
import os
import secrets
from pathlib import Path
from typing import List, Optional, Union, Any
import threading

from pydantic import Field, validator, field_validator, model_validator, ValidationInfo
from pydantic_settings import BaseSettings
from pydantic._internal._model_construction import ModelMetaclass

from dotenv import load_dotenv
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))))
sys.path.insert(0, project_root)

load_dotenv()

def _split_comma_separated(value: Union[str, List[str]]) -> List[str]:
    """将逗号分隔配置转换成列表。"""
    if isinstance(value, str):
        if not value or value == "*":
            return ["*"]
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _is_insecure_value(value: Any) -> bool:
    """判断敏感配置是否仍为占位符或弱默认值。"""
    if not isinstance(value, str):
        return False

    normalized = value.strip().lower()
    if not normalized:
        return True

    insecure_keywords = (
        "change-me",
        "changeme",
        "your_",
        "your-",
        "placeholder",
        "example",
        "demo",
        "default",
        "minioadmin",
        "change_me_super_pass",
    )

    return any(keyword in normalized for keyword in insecure_keywords)


def _is_production_env(value: Any) -> bool:
    return str(value or "").strip().lower() in {"production", "prod"}


def _contains_wildcard(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip() == "*"
    if isinstance(value, list):
        return "*" in [str(item).strip() for item in value]
    return False


class SingletonModelMeta(ModelMetaclass):
    """Settings 用：双检锁单例，兼容 pydantic BaseSettings。"""
    _instances = {}
    _lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    instance = super().__call__(*args, **kwargs)
                    cls._instances[cls] = instance
        return cls._instances[cls]


class Settings(BaseSettings, metaclass=SingletonModelMeta):
    """单例配置：字段对应环境变量 / .env。"""

    PROJECT_NAME: str = Field("Project Yamato Shanghai", env="PROJECT_NAME")
    VERSION: str = Field("1.0.1", env="VERSION")
    DESCRIPTION: str = Field("Project Yamato Shanghai API", env="DESCRIPTION")
    ENVIRONMENT: str = Field("development", env="ENVIRONMENT")
    DEBUG: bool = Field(True, env="DEBUG")
    @validator("DEBUG", pre=True)
    def parse_debug_flag(cls, v: Any):
        """兼容 true/false、dev/prod 等字符串。"""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            normalized = v.strip().lower()
            if normalized in {"1", "true", "yes", "on", "debug", "dev", "development"}:
                return True
            if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
                return False
        return v

    API_V1_STR: str = Field("/api/v1", env="API_V1_STR")

    HOST: str = Field("0.0.0.0", env="HOST")
    PORT: int = Field(8000, env="PORT")
    ALLOWED_HOSTS: List[str] = Field(default_factory=lambda: ["*"], env="ALLOWED_HOSTS")
    TRUST_PROXY_HEADERS: bool = Field(False, env="TRUST_PROXY_HEADERS")
    TRUSTED_PROXIES: List[str] = Field(default_factory=list, env="TRUSTED_PROXIES")

    BACKEND_CORS_ORIGINS: List[str] = Field(default_factory=lambda: ["*"], env="BACKEND_CORS_ORIGINS")

    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            return _split_comma_separated(v)
        if isinstance(v, (list, str)):
            return v
        raise ValueError(v)

    @validator("ALLOWED_HOSTS", pre=True)
    def split_allowed_hosts(cls, v: Union[str, List[str]]) -> List[str]:
        return _split_comma_separated(v)

    @validator("TRUSTED_PROXIES", pre=True)
    def split_trusted_proxies(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @validator("ALLOWED_HOSTS")
    def validate_allowed_hosts_for_production(cls, v: List[str], values):
        if _is_production_env(values.get("ENVIRONMENT")) and _contains_wildcard(v):
            raise ValueError("生产环境禁止使用 ALLOWED_HOSTS=[\"*\"]，请配置明确域名")
        return v

    @validator("BACKEND_CORS_ORIGINS")
    def validate_cors_origins_for_production(cls, v: List[str], values):
        if _is_production_env(values.get("ENVIRONMENT")) and _contains_wildcard(v):
            raise ValueError("生产环境禁止使用 BACKEND_CORS_ORIGINS=[\"*\"]，请配置明确来源")
        return v

    @validator("RETRIEVER_ALLOWED_COLLECTIONS", pre=True)
    def split_retriever_allowed_collections(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    POSTGRES_SERVER: str = Field("127.0.0.1", env="POSTGRES_SERVER")
    POSTGRES_USER: str = Field("pguser", env="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field("change_me_postgres_password", env="POSTGRES_PASSWORD")
    POSTGRES_DB: str = Field("pgdb", env="POSTGRES_DB")
    POSTGRES_PORT: int = Field(5432, env="POSTGRES_PORT")

    # Connection pool tuning (consumed by app/core/database.py via getattr)
    DB_POOL_SIZE: int = Field(10, ge=1, le=100, env="DB_POOL_SIZE")
    DB_MAX_OVERFLOW: int = Field(20, ge=0, le=200, env="DB_MAX_OVERFLOW")
    DB_POOL_TIMEOUT: int = Field(30, ge=1, le=300, env="DB_POOL_TIMEOUT")
    DB_POOL_RECYCLE: int = Field(3600, ge=60, le=86400, env="DB_POOL_RECYCLE")

    # SQL Server（U8 / PDM）
    U8_SQLSERVER_HOST: str = Field("127.0.0.1", env="U8_SQLSERVER_HOST")
    U8_SQLSERVER_PORT: int = Field(1433, env="U8_SQLSERVER_PORT")
    U8_SQLSERVER_DATABASE: str = Field("change_me_u8_database", env="U8_SQLSERVER_DATABASE")
    U8_SQLSERVER_USER: str = Field("sa", env="U8_SQLSERVER_USER")
    U8_SQLSERVER_PASSWORD: str = Field("change_me_u8_sqlserver_password", env="U8_SQLSERVER_PASSWORD")
    U8_SQLSERVER_ENCRYPT: bool = Field(False, env="U8_SQLSERVER_ENCRYPT")

    PDM_SQLSERVER_HOST: str = Field("127.0.0.1", env="PDM_SQLSERVER_HOST")
    PDM_SQLSERVER_PORT: int = Field(1433, env="PDM_SQLSERVER_PORT")
    PDM_SQLSERVER_DATABASE: str = Field("change_me_pdm_database", env="PDM_SQLSERVER_DATABASE")
    PDM_SQLSERVER_USER: str = Field("sa", env="PDM_SQLSERVER_USER")
    PDM_SQLSERVER_PASSWORD: str = Field("change_me_pdm_sqlserver_password", env="PDM_SQLSERVER_PASSWORD")
    PDM_SQLSERVER_ENCRYPT: bool = Field(False, env="PDM_SQLSERVER_ENCRYPT")
    # pymssql: query timeout (seconds) and connection/login timeout
    SQLSERVER_QUERY_TIMEOUT_SEC: int = Field(120, ge=1, le=3600, env="SQLSERVER_QUERY_TIMEOUT_SEC")
    SQLSERVER_LOGIN_TIMEOUT_SEC: int = Field(30, ge=1, le=300, env="SQLSERVER_LOGIN_TIMEOUT_SEC")
    # PDM matcher 四路召回的"查询内"并行度（与跨请求并发无关）。不用于同步查询 API
    # 执行器（那个用 EXECUTOR_MAX_WORKERS）。
    SQLSERVER_QUERY_MAX_WORKERS: int = Field(2, ge=1, le=8, env="SQLSERVER_QUERY_MAX_WORKERS")
    # Circuit breaker for U8/PDM SQLServer (failure isolation, 20003 timeout protection)
    SQLSERVER_CB_FAIL_THRESHOLD: int = Field(5, ge=1, le=100, env="SQLSERVER_CB_FAIL_THRESHOLD")
    SQLSERVER_CB_OPEN_SEC: int = Field(60, ge=5, le=3600, env="SQLSERVER_CB_OPEN_SEC")

    # U8 BOM 树展开并行度：单个 BOM 任务内嵌 ThreadPoolExecutor 的 worker 数
    # （每个根编码子树一个 worker）。注意：采用全局共享连接池后，本值不再决定
    # 到 ERP 的连接数（由 U8_BOM_MAX_TOTAL_CONNECTIONS 决定），仅决定单任务的
    # 线程数/并行度。设为 1 即退回串行。调大会增加线程数（任务数 × 本值）。
    U8_BOM_PARALLEL_WORKERS: int = Field(
        16, ge=1, le=128, env="U8_BOM_PARALLEL_WORKERS"
    )
    # 允许同时运行的 BOM 查询任务数（运行时全局信号量约束）。
    # 采用共享连接池后，本值不再保护 ERP 连接（连接池上限负责），而是限制并发
    # BOM 任务数 / 嵌套线程池总数。需 ≤ EXECUTOR_MAX_WORKERS，否则任务会卡在
    # 执行器队列里等待（“看戏”）。
    U8_BOM_MAX_CONCURRENT_TASKS: int = Field(
        30, ge=1, le=64, env="U8_BOM_MAX_CONCURRENT_TASKS"
    )
    # 单个用户同时可运行的 BOM 查询任务数上限（每用户独立信号量，互不共享）。
    # 防止单人刷爆全局并发额度，保证 30 人团队公平性。
    U8_BOM_MAX_CONCURRENT_TASKS_PER_USER: int = Field(
        2, ge=1, le=64, env="U8_BOM_MAX_CONCURRENT_TASKS_PER_USER"
    )
    # 后台任务线程池大小，同时也是同步查询 API 执行器大小（两条路径共用此旋钮）。
    # 控制 OCR/导入/报价等后台任务 + 同步 /u8/bom-inventory 查询的并发。
    # 需 ≥ U8_BOM_MAX_CONCURRENT_TASKS，否则 BOM 任务（无论同步还是后台）会在
    # 执行器队列里排队"看戏"，根本到不了 per-user / 全局 BOM 信号量。
    EXECUTOR_MAX_WORKERS: int = Field(
        30, ge=1, le=512, env="EXECUTOR_MAX_WORKERS"
    )
    # 全局共享 U8 连接池大小 = 单实例同时打开的 U8 SQL Server 连接总数硬上限。
    # 所有 BOM 任务共享此池，按需 acquire/release，ERP 永远只看到这么多连接。
    # 这是保护生产 U8 ERP 数据库的唯一连接闸门（取代旧的“任务数×并行度”乘积）。
    U8_BOM_MAX_TOTAL_CONNECTIONS: int = Field(
        64, ge=1, le=2048, env="U8_BOM_MAX_TOTAL_CONNECTIONS"
    )
    # 从共享连接池获取一条连接的最长等待秒数（带 1s 轮询 + 取消检查）。
    # 超时抛 PoolTimeout（通常意味着 ERP 连接被长时间占满）。
    U8_BOM_POOL_ACQUIRE_TIMEOUT_SEC: int = Field(
        60, ge=1, le=600, env="U8_BOM_POOL_ACQUIRE_TIMEOUT_SEC"
    )

    # 文档处理重模型有界池上限。PaddleOCR / TagGenerator 各自一个全局池，
    # checkout 互斥（一实例一线程）既绕开 PaddleOCR 线程安全问题，又把 GPU
    # 显存占用从“随任务数线性增长”封顶为常数上限（5×0.8 + 5×1.9 ≈ 13.5GB）。
    # PaddleOCR 池实例数上限；0 = 禁用 OCR（PDF 仅走 pdfplumber 文本提取）。
    PADDLEOCR_POOL_MAX_SIZE: int = Field(
        5, ge=0, le=32, env="PADDLEOCR_POOL_MAX_SIZE"
    )
    # 从 PaddleOCR 池借一个实例的最长等待秒数；超时该页跳过 OCR（降级路径，不致命）。
    PADDLEOCR_ACQUIRE_TIMEOUT_SEC: int = Field(
        30, ge=1, le=300, env="PADDLEOCR_ACQUIRE_TIMEOUT_SEC"
    )
    # TagGenerator（SentenceTransformer + keyphrase pipeline）池实例数上限。
    TAGGENERATOR_POOL_MAX_SIZE: int = Field(
        5, ge=1, le=32, env="TAGGENERATOR_POOL_MAX_SIZE"
    )
    # 从 TagGenerator 池借一个实例的最长等待秒数；超时退化为 CPU 简单标签。
    TAGGENERATOR_ACQUIRE_TIMEOUT_SEC: int = Field(
        30, ge=1, le=300, env="TAGGENERATOR_ACQUIRE_TIMEOUT_SEC"
    )

    @model_validator(mode="after")
    def _validate_u8_bom_concurrency(self):
        """共享连接池模型下的并发一致性校验。

        - EXECUTOR_MAX_WORKERS ≥ U8_BOM_MAX_CONCURRENT_TASKS：EXECUTOR 同时是后台
          任务池和同步查询 API 执行器，二者共用此旋钮。若小于任务并发上限，BOM 任务
          （无论同步 /u8/bom-inventory 还是后台报价）会在执行器队列里排队“看戏”，
          根本到不了 per-user / 全局 BOM 信号量。
        - U8_BOM_MAX_TOTAL_CONNECTIONS 是 ERP 连接硬上限（共享池大小），与任务数/
          并行度解耦——任务数 × 并行度 可远大于连接数，多出的 worker 线程会在池上
          阻塞等待连接（合理的背压，非错误）。
        """
        if self.U8_BOM_MAX_CONCURRENT_TASKS > self.EXECUTOR_MAX_WORKERS:
            raise ValueError(
                f"U8_BOM_MAX_CONCURRENT_TASKS({self.U8_BOM_MAX_CONCURRENT_TASKS}) > "
                f"EXECUTOR_MAX_WORKERS({self.EXECUTOR_MAX_WORKERS})：BOM 任务会卡在"
                f"执行器队列，请调大 EXECUTOR_MAX_WORKERS 或调小任务并发数"
            )
        return self

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        """根据单项设置拼接数据库连接串。"""
        return (
            f"postgresql://{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_SERVER}:"
            f"{self.POSTGRES_PORT}/"
            f"{self.POSTGRES_DB}"
        )

    @property
    def ASYNC_SQLALCHEMY_DATABASE_URI(self) -> str:
        """asyncpg 异步数据库连接串。"""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_SERVER}:"
            f"{self.POSTGRES_PORT}/"
            f"{self.POSTGRES_DB}"
        )

    REDIS_HOST: str = Field("127.0.0.1", env="REDIS_HOST")
    REDIS_PORT: int = Field(6379, env="REDIS_PORT")
    REDIS_DB: int = Field(0, env="REDIS_DB")
    REDIS_PASSWORD: Optional[str] = Field(default=None, env="REDIS_PASSWORD")
    REDIS_MAX_CONNECTIONS: int = Field(10, env="REDIS_MAX_CONNECTIONS")

    @property
    def REDIS_URL(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # This process connects to MinIO for put_object / bucket (use loopback or published port when API is on the host).
    MINIO_APP_ENDPOINT: str = Field("127.0.0.1:9000", env="MINIO_APP_ENDPOINT")
    # Legacy: presign / anonymous URL fallback when MINIO_PUBLIC_ENDPOINT is unset. Not used for application upload.
    MINIO_ENDPOINT: str = Field("127.0.0.1:9000", env="MINIO_ENDPOINT")
    # Host:port (or full http URL) that other containers (OCR) use to GET objects — presign is signed for this host only.
    MINIO_PUBLIC_ENDPOINT: Optional[str] = Field(default=None, env="MINIO_PUBLIC_ENDPOINT")
    MINIO_ACCESS_KEY: str = Field("change_me_minio_access_key", env="MINIO_ACCESS_KEY")
    MINIO_SECRET_KEY: str = Field("change_me_minio_secret_key", env="MINIO_SECRET_KEY")
    MINIO_SECURE: bool = Field(False, env="MINIO_SECURE")
    # Set so minio-py does not call GetBucketLocation on presign; MinIO S3 default is us-east-1
    MINIO_REGION: str = Field("us-east-1", env="MINIO_REGION")
    MINIO_BUCKET_NAME: str = Field("yamatodev", env="MINIO_BUCKET_NAME")
    CLOSING_FORM_IMAGE_PREFIX: str = Field("form_pic", env="CLOSING_FORM_IMAGE_PREFIX")
    # Presigned GetObject for OCR / temp files (replaces bucket-wide anonymous read by default)
    MINIO_PRESIGN_EXPIRES_HOURS: int = Field(12, ge=1, le=168, env="MINIO_PRESIGN_EXPIRES_HOURS")
    # Override TLS for presign only (e.g. internal MINIO_ON https but public http). None = infer from URL or MINIO_SECURE
    MINIO_PRESIGN_SECURE: Optional[bool] = Field(default=None, env="MINIO_PRESIGN_SECURE")
    # Optional: upload temp OCR images to a dedicated bucket (still uses presign, no public policy)
    MINIO_OCR_TEMP_BUCKET: Optional[str] = Field(default=None, env="MINIO_OCR_TEMP_BUCKET")
    # Opt-in only: if True, set anonymous s3:GetObject on MINIO_OCR_ANONYMOUS_BUCKET (never on default bucket)
    MINIO_OCR_ENABLE_ANONYMOUS_BUCKET: bool = Field(False, env="MINIO_OCR_ENABLE_ANONYMOUS_BUCKET")
    MINIO_OCR_ANONYMOUS_BUCKET: Optional[str] = Field(default=None, env="MINIO_OCR_ANONYMOUS_BUCKET")
    # MinIO download socket timeout (protects worker threads from MinIO stalls)
    MINIO_DOWNLOAD_TIMEOUT_SEC: float = Field(60.0, ge=5.0, le=600.0, env="MINIO_DOWNLOAD_TIMEOUT_SEC")
    # MinIO orphan reconciliation (scan bucket vs DB, delete unreferenced objects)
    MINIO_RECONCILE_INTERVAL_SEC: int = Field(259200, ge=300, le=604800, env="MINIO_RECONCILE_INTERVAL_SEC")
    MINIO_RECONCILE_GRACE_SEC: int = Field(259200, ge=60, le=604800, env="MINIO_RECONCILE_GRACE_SEC")

    OCR_HTTP_CONNECT_TIMEOUT: float = Field(10.0, ge=1.0, le=300.0, env="OCR_HTTP_CONNECT_TIMEOUT")
    OCR_HTTP_READ_TIMEOUT: float = Field(300.0, ge=5.0, le=3600.0, env="OCR_HTTP_READ_TIMEOUT")

    OCR_PDFTEXT_ENABLED: bool = Field(True, env="OCR_PDFTEXT_ENABLED")
    OCR_PDFTEXT_TIMEOUT: int = Field(30, ge=5, le=120, env="OCR_PDFTEXT_TIMEOUT")

    HTTP_CLIENT_TIMEOUT: float = Field(30.0, env="HTTP_CLIENT_TIMEOUT")
    HTTP_CLIENT_MAX_CONNECTIONS: int = Field(100, env="HTTP_CLIENT_MAX_CONNECTIONS")
    HTTP_CLIENT_MAX_KEEPALIVE: int = Field(20, env="HTTP_CLIENT_MAX_KEEPALIVE")

    SECRET_KEY: str = Field(default_factory=lambda: secrets.token_urlsafe(32), env="SECRET_KEY")
    ALGORITHM: str = Field("HS256", env="ALGORITHM")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(60, env="ACCESS_TOKEN_EXPIRE_MINUTES")
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(7, env="REFRESH_TOKEN_EXPIRE_DAYS")
    INTERNAL_API_KEY: str = Field("change_me_internal_api_key", env="INTERNAL_API_KEY")
    RETRIEVER_ALLOWED_COLLECTIONS: List[str] = Field(default_factory=list, env="RETRIEVER_ALLOWED_COLLECTIONS")

    BOOTSTRAP_SUPERUSER_USERNAME: Optional[str] = Field(default=None, env="BOOTSTRAP_SUPERUSER_USERNAME")
    BOOTSTRAP_SUPERUSER_EMAIL: Optional[str] = Field(default=None, env="BOOTSTRAP_SUPERUSER_EMAIL")
    BOOTSTRAP_SUPERUSER_PASSWORD: Optional[str] = Field(default=None, env="BOOTSTRAP_SUPERUSER_PASSWORD")

    RATE_LIMIT_REQUESTS_PER_MINUTE: int = Field(300, env="RATE_LIMIT_REQUESTS_PER_MINUTE")
    RATE_LIMIT_REQUESTS_PER_HOUR: int = Field(3000, env="RATE_LIMIT_REQUESTS_PER_HOUR")
    RATE_LIMIT_AUTH: int = Field(300, env="RATE_LIMIT_AUTH")
    RATE_LIMIT_ANON: int = Field(60, env="RATE_LIMIT_ANON")
    RATE_LIMIT_EXPENSIVE_AUTH: int = Field(60, env="RATE_LIMIT_EXPENSIVE_AUTH")
    RATE_LIMIT_EXPENSIVE_ANON: int = Field(15, env="RATE_LIMIT_EXPENSIVE_ANON")
    # admin/superuser 分档（4× 普通预算）：admin 查看大量任务列表/详情时避免误触限流
    RATE_LIMIT_AUTH_ADMIN: int = Field(1200, env="RATE_LIMIT_AUTH_ADMIN")
    RATE_LIMIT_EXPENSIVE_AUTH_ADMIN: int = Field(240, env="RATE_LIMIT_EXPENSIVE_AUTH_ADMIN")
    RATE_LIMIT_WINDOW: int = Field(60, env="RATE_LIMIT_WINDOW")
    RATE_LIMIT_FAIL_OPEN: bool = Field(True, env="RATE_LIMIT_FAIL_OPEN")
    RATE_LIMIT_REDIS_ERROR_STATUS: int = Field(503, env="RATE_LIMIT_REDIS_ERROR_STATUS")
    WS_MAX_CONNECTIONS_PER_IP: int = Field(20, env="WS_MAX_CONNECTIONS_PER_IP")
    WS_MAX_MESSAGES_PER_MINUTE: int = Field(120, env="WS_MAX_MESSAGES_PER_MINUTE")
    # 按用户（user_id）的 WS 连接上限——NAT 安全（不同于按 IP，办公室多人共享 IP 不会互相挤占）。
    # admin/superuser 需要同时盯多个任务，给更高分档。
    WS_MAX_CONNECTIONS_PER_USER: int = Field(16, env="WS_MAX_CONNECTIONS_PER_USER")
    WS_MAX_CONNECTIONS_PER_USER_ADMIN: int = Field(64, env="WS_MAX_CONNECTIONS_PER_USER_ADMIN")
    # Cap in-memory per-IP message counter entries to avoid unbounded growth
    WS_MAX_TRACKED_IPS_FOR_COUNTERS: int = Field(2000, ge=100, le=100000, env="WS_MAX_TRACKED_IPS_FOR_COUNTERS")

    # Quotation queue runtime concurrency limits
    QUOTATION_MAX_RUNNING_PER_OWNER: int = Field(2, ge=1, le=20, env="QUOTATION_MAX_RUNNING_PER_OWNER")
    QUOTATION_MAX_RUNNING_PER_IP: int = Field(2, ge=1, le=20, env="QUOTATION_MAX_RUNNING_PER_IP")

    QUOTATION_RETENTION_MAX_TOTAL: int = Field(100, ge=10, le=10000, env="QUOTATION_RETENTION_MAX_TOTAL")
    QUOTATION_RETENTION_TARGET: int = Field(50, ge=1, le=5000, env="QUOTATION_RETENTION_TARGET")
    QUOTATION_RETENTION_INTERVAL_SEC: int = Field(300, ge=60, le=86400, env="QUOTATION_RETENTION_INTERVAL_SEC")
    QUOTATION_AWAITING_APPROVAL_TTL_HOURS: int = Field(24, ge=1, le=168, env="QUOTATION_AWAITING_APPROVAL_TTL_HOURS")
    # Reclaim quotation tasks stuck in running longer than this (worker hang protection)
    QUOTATION_RUNNING_TIMEOUT_SEC: int = Field(1800, ge=60, le=86400, env="QUOTATION_RUNNING_TIMEOUT_SEC")

    # task_owner_registry in-memory cache bounds (prevents unbounded growth)
    TASK_OWNER_CACHE_TTL_SEC: int = Field(86400, ge=60, le=604800, env="TASK_OWNER_CACHE_TTL_SEC")
    TASK_OWNER_CACHE_MAX: int = Field(5000, ge=100, le=100000, env="TASK_OWNER_CACHE_MAX")

    # Log rotation
    LOG_MAX_BYTES: int = Field(10 * 1024 * 1024, ge=1024 * 1024, env="LOG_MAX_BYTES")
    LOG_BACKUP_COUNT: int = Field(5, ge=1, le=50, env="LOG_BACKUP_COUNT")

    # Headless document conversion; abort hung soffice processes
    LIBREOFFICE_SUBPROCESS_TIMEOUT_SEC: int = Field(300, ge=30, le=3600, env="LIBREOFFICE_SUBPROCESS_TIMEOUT_SEC")

    ENABLE_RATE_LIMIT: bool = Field(False, env="ENABLE_RATE_LIMIT")
    ENABLE_REQUEST_SIZE_LIMIT: bool = Field(True, env="ENABLE_REQUEST_SIZE_LIMIT")
    ENABLE_CACHE: bool = Field(False, env="ENABLE_CACHE")

    MAX_JSON_SIZE: int = Field(5 * 1024 * 1024, env="MAX_JSON_SIZE")
    MAX_FILE_SIZE: int = Field(50 * 1024 * 1024, env="MAX_FILE_SIZE")
    CACHE_DEFAULT_TTL: int = Field(300, env="CACHE_DEFAULT_TTL")
    CACHE_ENABLED_METHODS: List[str] = Field(default_factory=lambda: ["GET"], env="CACHE_ENABLED_METHODS")
    CACHE_TTL: int = Field(3600, env="CACHE_TTL")
    CACHE_API_RESPONSE_TTL: int = Field(300, env="CACHE_API_RESPONSE_TTL")
    CACHE_JOB_STATUS_TTL: int = Field(86400, env="CACHE_JOB_STATUS_TTL")

    @validator("CACHE_ENABLED_METHODS", pre=True)
    def split_cache_methods(cls, v: Union[str, List[str]]) -> List[str]:
        return _split_comma_separated(v)

    MAX_FILE_SIZE_MB: int = Field(50, env="MAX_FILE_SIZE_MB")
    ALLOWED_FILE_EXTENSIONS: List[str] = Field(
        default_factory=lambda: [".pdf", ".doc", ".docx", ".txt", ".csv", ".xlsx", ".xls", ".json", ".xml"],
        env="ALLOWED_FILE_EXTENSIONS",
    )

    @validator("ALLOWED_FILE_EXTENSIONS", pre=True)
    def split_allowed_extensions(cls, v: Union[str, List[str]]) -> List[str]:
        return _split_comma_separated(v)

    BGE_M3_API_URL: str = Field("http://localhost:8002/v1/embeddings", env="BGE_M3_API_URL")
    BGE_M3_MODEL_NAME: str = Field("BAAI/bge-m3", env="BGE_M3_MODEL_NAME")
    BGE_M3_TOKENIZER_NAME: str = Field("BAAI/bge-m3", env="BGE_M3_TOKENIZER_NAME")
    
    RERANKER_API_URL: str = Field("http://localhost:8003/v1/rerank", env="RERANKER_API_URL")

    LOCAL_MODEL_GPU_DEVICE: int = Field(3, env="LOCAL_MODEL_GPU_DEVICE")

    # ── LLM / OCR models (all OpenAI-compatible chat-completions) ──────────
    # Each group carries API_URL (base), MODEL (served id) and API_KEY
    # (provider auth). Local vLLM leaves API_KEY empty — resolved to
    # "not-needed" at the ChatOpenAI call site; external providers
    # (DeepSeek, Moonshot, Together, OpenAI, …) set API_KEY and point
    # API_URL at the provider's …/v1 root.

    # Primary LLM — the larger model (was Qwen3.6-35B). Powers remark
    # interpretation and context compression.
    PRIMARY_LLM_API_URL: str = Field(
        "http://localhost:80/llm/qwen36b/v1",
        env="PRIMARY_LLM_API_URL",
        description="OpenAI-compatible API root (…/v1) for the primary LLM. Proxy must target vLLM, not Dify/Next static routes.",
    )
    PRIMARY_LLM_MODEL: str = Field(
        "/models/Qwen3.6-35B-A3B",
        env="PRIMARY_LLM_MODEL",
        description="Served model id (GET /v1/models on the same base as PRIMARY_LLM_API_URL). vLLM often uses path-style ids, not Hub names.",
    )
    PRIMARY_LLM_API_KEY: Optional[str] = Field(
        default=None, env="PRIMARY_LLM_API_KEY",
        description="API key for the primary LLM. Leave empty for local unauthenticated vLLM; required for external providers.",
    )

    # Web search backend for the conversation workflow (replaces the Dify SearXNG plugin).
    WEB_SEARCH_PROVIDER: str = Field("tavily", env="WEB_SEARCH_PROVIDER")
    TAVILY_API_KEY: Optional[str] = Field(default=None, env="TAVILY_API_KEY")

    # Conversation workflow concurrency caps (shared singletons, see
    # app/integrations/conversation/runtime.py). These bound in-flight LLM
    # calls per model so a burst of chat users cannot exhaust vLLM or memory.
    CONVERSATION_8B_MAX_CONCURRENT: int = Field(
        20, ge=1, env="CONVERSATION_8B_MAX_CONCURRENT",
        description="Max in-flight Qwen3-8B calls (keyword extraction + intent).",
    )
    CONVERSATION_35B_MAX_CONCURRENT: int = Field(
        10, ge=1, env="CONVERSATION_35B_MAX_CONCURRENT",
        description="Max in-flight Qwen3.6-35B streaming answers (each holds a vLLM slot for the whole stream).",
    )
    CONVERSATION_RETRIEVAL_MAX_WORKERS: int = Field(
        8, ge=1, env="CONVERSATION_RETRIEVAL_MAX_WORKERS",
        description="Dedicated thread pool size for blocking RAG retrieval in the conversation workflow.",
    )

    # Secondary LLM — the smaller model (was Qwen3-8B). Powers chat-summary.
    SECONDARY_LLM_API_URL: str = Field(
        "http://localhost:80/llm/qwen8b/v1",
        env="SECONDARY_LLM_API_URL",
        description="OpenAI-compatible API root (…/v1) for the secondary LLM.",
    )
    SECONDARY_LLM_MODEL: str = Field(
        "Qwen/Qwen3-8B-FP8",
        env="SECONDARY_LLM_MODEL",
        description="Served model id for the secondary LLM (GET /v1/models).",
    )
    SECONDARY_LLM_API_KEY: Optional[str] = Field(
        default=None, env="SECONDARY_LLM_API_KEY",
        description="API key for the secondary LLM. Leave empty for local vLLM; required for external providers.",
    )

    # OCR model — vision chat-completions endpoint (was DotsOCR).
    OCR_MODEL_API_URL: str = Field(
        "http://localhost:80/ocr/dotsocr/v1/chat/completions",
        env="OCR_MODEL_API_URL",
        description="OpenAI-compatible chat/completions URL for the OCR vision model.",
    )
    OCR_MODEL_NAME: str = Field(
        "rednote-hilab/dots.ocr",
        env="OCR_MODEL_NAME",
        description="Model id sent in the OCR chat-completions payload.",
    )
    OCR_MODEL_API_KEY: Optional[str] = Field(
        default=None, env="OCR_MODEL_API_KEY",
        description="API key for the OCR model. Leave empty for local unauthenticated endpoint; sent as Bearer token when set.",
    )
    OCR_MODEL_MAX_TOKENS: int = Field(4096, ge=1024, le=16384, env="OCR_MODEL_MAX_TOKENS")

    # ── Remark LLM interpreter (spec sheet field adjustment) ───────────────
    # When enabled, a remark found in the parsed spec content is sent to the
    # primary LLM to produce {canonical_field: adjusted_value} overrides.
    # Any failure (no model service, timeout, bad output) degrades gracefully
    # to the original pipeline behavior — this flag only gates the attempt.
    REMARK_LLM_INTERPRETER_ENABLED: bool = Field(
        True,
        env="REMARK_LLM_INTERPRETER_ENABLED",
        description="Enable remark → field-adjustment in spec parsing (uses the primary LLM). Failures degrade gracefully.",
    )
    REMARK_LLM_REQUEST_TIMEOUT: float = Field(
        30.0, env="REMARK_LLM_REQUEST_TIMEOUT",
        description="Per-request timeout (seconds) for the remark LLM call.",
    )
    REMARK_LLM_MAX_TOKENS: int = Field(
        512, env="REMARK_LLM_MAX_TOKENS",
        description="Max output tokens for the remark LLM call.",
    )

    N8N_BASE_URL: str = Field("http://localhost:5678", env="N8N_BASE_URL")
    N8N_API_KEY: Optional[str] = Field(default=None, env="N8N_API_KEY")

    RAGFLOW_BASE_URL: str = Field("http://localhost:9380", env="RAGFLOW_BASE_URL")
    RAGFLOW_API_KEY: Optional[str] = Field(default=None, env="RAGFLOW_API_KEY")
    RAGFLOW_DATASET_ID: Optional[str] = Field(default=None, env="RAGFLOW_DATASET_ID")
    
    SUPERSET_BASE_URL: str = Field("http://localhost:8088", env="SUPERSET_BASE_URL")
    SUPERSET_USERNAME: str = Field("admin", env="SUPERSET_USERNAME")
    SUPERSET_PASSWORD: str = Field("admin", env="SUPERSET_PASSWORD")
    SUPERSET_DATABASE_ID: int = Field(1, env="SUPERSET_DATABASE_ID")
    SUPERSET_GUEST_TOKEN_SECRET: str = Field("your-superset-guest-token-secret", env="SUPERSET_GUEST_TOKEN_SECRET")
    SUPERSET_CSRF_TOKEN_TIMEOUT: int = Field(3600, env="SUPERSET_CSRF_TOKEN_TIMEOUT")
    SUPERSET_CACHE_DEFAULT_TIMEOUT: int = Field(300, env="SUPERSET_CACHE_DEFAULT_TIMEOUT")
    
    DATAHUB_GMS_URL: str = Field("http://localhost:8080", env="DATAHUB_GMS_URL")
    DATAHUB_GMS_TOKEN: Optional[str] = Field(default=None, env="DATAHUB_GMS_TOKEN")
    DATAHUB_KAFKA_BOOTSTRAP_SERVERS: str = Field("localhost:9092", env="DATAHUB_KAFKA_BOOTSTRAP_SERVERS")
    DATAHUB_SCHEMA_REGISTRY_URL: str = Field("http://localhost:8081", env="DATAHUB_SCHEMA_REGISTRY_URL")

    LOG_LEVEL: str = Field("INFO", env="LOG_LEVEL")
    LOG_FORMAT: str = Field("%(asctime)s - %(name)s - %(levelname)s - %(message)s", env="LOG_FORMAT")
    LOG_FILE: Optional[str] = Field(default=None, env="LOG_FILE")
    ENABLE_METRICS: bool = Field(True, env="ENABLE_METRICS")
    METRICS_PORT: int = Field(9090, env="METRICS_PORT")
    ENABLE_MONITORING: bool = Field(True, env="ENABLE_MONITORING")
    MONITORING_INTERVAL: int = Field(60, env="MONITORING_INTERVAL")
    ENABLE_PROMETHEUS: bool = Field(True, env="ENABLE_PROMETHEUS")
    ENABLE_HEALTH_CHECK: bool = Field(True, env="ENABLE_HEALTH_CHECK")
    METRICS_REQUIRE_API_KEY: bool = Field(True, env="METRICS_REQUIRE_API_KEY")
    ENABLE_SECURITY_HEADERS: bool = Field(True, env="ENABLE_SECURITY_HEADERS")
    ENABLE_HSTS: bool = Field(True, env="ENABLE_HSTS")
    HSTS_MAX_AGE: int = Field(31536000, env="HSTS_MAX_AGE")

    @field_validator(
        "SECRET_KEY",
        "INTERNAL_API_KEY",
        "POSTGRES_PASSWORD",
        "U8_SQLSERVER_PASSWORD",
        "PDM_SQLSERVER_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        mode="after",
    )
    def validate_sensitive_values(cls, v: str, info: ValidationInfo):
        """生产环境下禁止使用占位符或弱默认敏感配置。"""
        env = str((info.data or {}).get("ENVIRONMENT", "development")).lower()
        field_name = getattr(info, "field_name", "敏感配置")

        if not v or not str(v).strip():
            raise ValueError(f"{field_name} 不能为空")

        if env in {"production", "prod"} and _is_insecure_value(v):
            raise ValueError(f"{field_name} 使用了不安全默认值，请通过环境变量覆盖")

        return v

    @validator("BOOTSTRAP_SUPERUSER_PASSWORD")
    def validate_bootstrap_superuser_password(cls, v: Optional[str], values):
        """生产环境禁用弱引导密码。"""
        if v is None:
            return v

        env = str(values.get("ENVIRONMENT", "development")).lower()
        if env in {"production", "prod"} and _is_insecure_value(v):
            raise ValueError("BOOTSTRAP_SUPERUSER_PASSWORD 使用了不安全默认值")
        return v

    @validator("ENABLE_RATE_LIMIT")
    def validate_rate_limit_required_in_production(cls, v: bool, values):
        if _is_production_env(values.get("ENVIRONMENT")) and not v:
            raise ValueError("生产环境必须启用 ENABLE_RATE_LIMIT")
        return v

    @validator("RATE_LIMIT_REDIS_ERROR_STATUS")
    def validate_rate_limit_redis_error_status(cls, v: int):
        if v not in {429, 503}:
            raise ValueError("RATE_LIMIT_REDIS_ERROR_STATUS 仅支持 429 或 503")
        return v

    RELOAD: bool = Field(False, env="RELOAD")

    @validator("RELOAD", pre=True)
    def set_reload_for_dev(cls, v, values):
        if values.get("ENVIRONMENT") == "development":
            return True
        return v

    class Config:
        env_file = str(Path(__file__).resolve().parents[2] / ".env")
        case_sensitive = True
        extra = "ignore"


settings = Settings()
