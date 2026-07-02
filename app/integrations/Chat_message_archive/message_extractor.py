"""
Chat Message Archive — local message store + LLM summarization.

Previously this module fetched chat messages and conversation variables from the
Dify API. With the migration to the in-process langchain conversation workflow,
chat messages now live in the local ``messages`` table (see
``app.models.orm.conversation``). This module reads from that table and
summarizes user query patterns with the local vLLM (langchain ChatOpenAI) —
no Dify HTTP calls remain.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from sqlalchemy import select

from app.core.async_bridge import run_async
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.time_utils import utcnow
from app.models.orm.conversation import Message

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


class UserProfileDB:
    """Manage user chat profile database operations (user_chat_profile table)."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.connection_params = {
            "host": host or settings.POSTGRES_SERVER,
            "port": port or settings.POSTGRES_PORT,
            "database": database or settings.POSTGRES_DB,
            "user": user or settings.POSTGRES_USER,
            "password": password or settings.POSTGRES_PASSWORD,
        }

    def get_connection(self):
        try:
            return psycopg2.connect(**self.connection_params)
        except psycopg2.Error as e:
            logger.error(f"Failed to connect to database: {str(e)}")
            raise

    def ensure_profile_table(self, conn) -> None:
        """确保 user_chat_profile 表存在，避免开发环境首次启动时报错。"""
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_chat_profile (
                    user_id VARCHAR(128) PRIMARY KEY,
                    latest_summary TEXT,
                    update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        conn.commit()

    def get_latest_summary(self, user_id: str) -> Optional[str]:
        conn = None
        try:
            conn = self.get_connection()
            self.ensure_profile_table(conn)
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT latest_summary FROM user_chat_profile WHERE user_id = %s",
                    (user_id,),
                )
                result = cursor.fetchone()
                if result:
                    logger.info(f"Found existing summary for user {user_id}")
                    return result["latest_summary"]
                logger.info(f"No existing summary for user {user_id}")
                return None
        except psycopg2.Error as e:
            logger.error(f"Failed to get latest summary for user {user_id}: {str(e)}")
            return None
        finally:
            if conn:
                conn.close()

    def upsert_latest_summary(self, user_id: str, latest_summary: str) -> bool:
        conn = None
        try:
            conn = self.get_connection()
            self.ensure_profile_table(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO user_chat_profile (user_id, latest_summary, update_time)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        latest_summary = EXCLUDED.latest_summary,
                        update_time = EXCLUDED.update_time
                    """,
                    (user_id, latest_summary, utcnow()),
                )
                conn.commit()
                logger.info(f"Successfully updated summary for user {user_id}")
                return True
        except psycopg2.Error as e:
            logger.error(f"Failed to upsert summary for user {user_id}: {str(e)}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                conn.close()


# ---------------------------------------------------------------------------
# Local message store (replaces Dify /v1/messages)
# ---------------------------------------------------------------------------


async def fetch_user_queries(conversation_id: str, limit: int = 20) -> List[str]:
    """Read the user's recent queries from the local ``messages`` table.

    Returns queries oldest-first (chronological order) for summarization.
    """
    if not conversation_id:
        return []
    limit = max(1, min(limit, 200))
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Message.content)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "user",
            )
            .order_by(Message.seq.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())
    # Reverse to chronological (oldest first).
    rows.reverse()
    return [r for r in rows if r]


# ---------------------------------------------------------------------------
# LLM summarization (langchain -> local vLLM, unchanged behavior)
# ---------------------------------------------------------------------------


def _build_summary_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.SECONDARY_LLM_API_URL,
        # Local vLLM leaves the key empty → "not-needed"; external
        # providers set SECONDARY_LLM_API_KEY.
        api_key=settings.SECONDARY_LLM_API_KEY or "not-needed",
        model=settings.SECONDARY_LLM_MODEL,
        temperature=0.7,
        max_tokens=1024,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


async def summarize_queries_with_llm(
    queries: List[str],
    previous_summary: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> str:
    """Summarize user query patterns, optionally building on a previous summary."""
    if not queries:
        logger.warning("No queries to summarize")
        return "No queries available for analysis."

    try:
        llm = _build_summary_llm()
        queries_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(queries))

        if previous_summary:
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        """/nothink 你是一位专业的数据分析师。你的任务是基于用户之前的聊天习惯总结，结合新的提问记录，更新用户的提问习惯和需求总结。

                                请从以下几个方面进行分析：
                                1. 用户主要关注的话题和领域（对比之前是否有变化）
                                2. 提问的风格和特点（例如：简短直接、详细描述、偏好某种类型的问题等）
                                3. 可能的业务需求或使用场景
                                4. 新的模式或趋势

                                请用简洁的中文总结，控制在150字以内。注意要整合之前的总结和新的提问记录。""",
                    ),
                    (
                        "human",
                        """用户之前的聊天习惯总结：
{previous_summary}

以下是用户最新的{count}条提问记录：
{queries}

请基于之前的总结和新的提问记录，生成更新后的用户聊天习惯总结。""",
                    ),
                ]
            )
            logger.info(f"Updating summary with {len(queries)} new queries and previous summary")
            summary = await (prompt | llm | StrOutputParser()).ainvoke(
                {
                    "previous_summary": previous_summary,
                    "count": len(queries),
                    "queries": queries_text,
                }
            )
        else:
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        """/nothink 你是一位专业的数据分析师。你的任务是分析用户的提问记录，总结他们的提问习惯和需求。

                                请从以下几个方面进行分析：
                                1. 用户主要关注的话题和领域
                                2. 提问的风格和特点（例如：简短直接、详细描述、偏好某种类型的问题等）
                                3. 可能的业务需求或使用场景
                                4. 其他值得注意的模式

                                请用简洁的中文总结，控制在150字以内。""",
                    ),
                    (
                        "human",
                        """以下是用户的{count}条提问记录：

{queries}

请分析并总结用户的提问习惯和需求。""",
                    ),
                ]
            )
            logger.info(f"Creating new summary from {len(queries)} queries")
            summary = await (prompt | llm | StrOutputParser()).ainvoke(
                {"count": len(queries), "queries": queries_text}
            )

        logger.info("Successfully generated summary")
        return summary
    except Exception as e:
        logger.error(f"Failed to summarize queries with LLM: {str(e)}")
        return f"Error generating summary: {str(e)}"


async def update_user_profile_with_new_queries(
    user_id: str,
    conversation_id: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """Complete workflow: previous summary → fetch local queries → LLM summary → upsert.

    Reads messages from the local ``messages`` table (no Dify).
    """
    db = UserProfileDB()
    result: Dict[str, Any] = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "queries": [],
        "query_count": 0,
        "previous_summary": None,
        "new_summary": None,
        "db_updated": False,
        "is_first_time": False,
    }

    logger.info(f"Step 1: Getting previous summary for user {user_id}")
    previous_summary = db.get_latest_summary(user_id)
    result["previous_summary"] = previous_summary
    if previous_summary is None:
        result["is_first_time"] = True
        logger.info(f"First time user: {user_id}")

    logger.info(f"Step 2: Extracting queries from local messages for conversation {conversation_id}")
    queries = await fetch_user_queries(conversation_id, limit)
    result["queries"] = queries
    result["query_count"] = len(queries)

    if not queries:
        logger.warning("No queries extracted, skipping summary generation")
        return result

    logger.info(f"Step 3: Generating new summary (with previous: {previous_summary is not None})")
    new_summary = await summarize_queries_with_llm(
        queries=queries, previous_summary=previous_summary
    )
    result["new_summary"] = new_summary

    logger.info(f"Step 4: Updating database for user {user_id}")
    db_updated = db.upsert_latest_summary(user_id, new_summary)
    result["db_updated"] = db_updated
    if db_updated:
        logger.info(f"Successfully updated user profile for {user_id}")
    else:
        logger.error(f"Failed to update database for user {user_id}")
    return result


def summarize_user_queries(
    user_id: str,
    conversation_id: str,
    limit: int = 20,
) -> Dict[str, Any]:
    """Sync wrapper for thread contexts."""
    return run_async(update_user_profile_with_new_queries(user_id, conversation_id, limit))
