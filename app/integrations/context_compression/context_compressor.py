"""LangChain + OpenAI-compatible local LLM context compression.

Previously fetched ``long_memory`` / ``recent_dialogs`` from the Dify
conversation-variables API. With Dify removed, those variables live on the local
``conversations`` row and are read through ``ConversationRepoPort``.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from app.core.config import settings
from app.ports.domains.conversation import ConversationRepoPort

logger = logging.getLogger(__name__)

_MAX_N_RECENT = 100
_MAX_TOTAL_DIALOGUE_CHARS = 400_000
_MAX_SINGLE_DIALOGUE_ITEM_CHARS = 80_000
_TRUNCATE_NOTE = "\n[truncated]"


def _clamp_n_recent(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, _MAX_N_RECENT))


def _truncate_string(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    keep = max(0, max_len - len(_TRUNCATE_NOTE))
    return s[:keep] + _TRUNCATE_NOTE


def _truncate_dialogue_lists(
    recent: List[str], older: List[str]
) -> tuple[List[str], List[str]]:
    r = [_truncate_string(str(x), _MAX_SINGLE_DIALOGUE_ITEM_CHARS) for x in recent]
    o = [_truncate_string(str(x), _MAX_SINGLE_DIALOGUE_ITEM_CHARS) for x in older]

    def char_total() -> int:
        return sum(len(s) for s in r) + sum(len(s) for s in o)

    if char_total() <= _MAX_TOTAL_DIALOGUE_CHARS:
        return r, o
    logger.warning(
        "Dialogue text exceeded soft cap (total_chars=%s, max=%s); dropping from end",
        char_total(),
        _MAX_TOTAL_DIALOGUE_CHARS,
    )
    while r and char_total() > _MAX_TOTAL_DIALOGUE_CHARS:
        r.pop()
    while o and char_total() > _MAX_TOTAL_DIALOGUE_CHARS:
        o.pop()
    if r and char_total() > _MAX_TOTAL_DIALOGUE_CHARS:
        r[-1] = _truncate_string(
            r[-1], _MAX_TOTAL_DIALOGUE_CHARS - (char_total() - len(r[-1]))
        )
    if o and char_total() > _MAX_TOTAL_DIALOGUE_CHARS:
        o[-1] = _truncate_string(
            o[-1], _MAX_TOTAL_DIALOGUE_CHARS - (char_total() - len(o[-1]))
        )
    return r, o


def _is_upstream_html_response(exc: BaseException) -> bool:
    """True if the error body looks like an HTML page (e.g. 404 from a UI, not vLLM)."""
    text = str(exc).lower()
    if "<!doctype html" in text or "<!doctype" in text:
        return True
    if "text/html" in text and "application/json" not in text:
        return True
    return False


class LlmEndpointMisconfiguredError(RuntimeError):
    """Configured OpenAI-compatible base_url returned an HTML error page, not a chat completion."""


class ContextCompressor:
    """Load memory from the local conversation repo, then compress via LLM."""

    def __init__(
        self,
        conversation_repo: ConversationRepoPort,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ):
        """base_url defaults to PRIMARY_LLM; model defaults to PRIMARY_LLM_MODEL."""
        self.conversation_repo = conversation_repo
        self.base_url = base_url or settings.PRIMARY_LLM_API_URL
        self.model_name = model_name if model_name is not None else settings.PRIMARY_LLM_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens

        self.llm = ChatOpenAI(
            base_url=self.base_url,
            # Local vLLM leaves the key empty → "not-needed"; external
            # providers set PRIMARY_LLM_API_KEY.
            api_key=settings.PRIMARY_LLM_API_KEY or "not-needed",
            model=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

    async def _fetch_and_split_dialogues(
        self, conversation_id: str, _n_recent: int = 5
    ) -> Dict[str, List[str]]:
        """Load long_memory + recent_dialogs from the local conversation row."""
        try:
            snapshot = await self.conversation_repo.load_snapshot(conversation_id)
        except Exception as e:
            logger.error("Failed to load conversation snapshot for %s: %s", conversation_id, e)
            return {"recent": [], "older": []}
        if snapshot is None:
            return {"recent": [], "older": []}

        older = [str(x) for x in (snapshot.long_memory or [])]
        recent = [str(x) for x in (snapshot.recent_dialogs or [])]
        recent, older = _truncate_dialogue_lists(recent, older)
        return {"recent": recent, "older": older}

    async def compress(self, context_data: Dict[str, Any]) -> str:
        """Compress dialogues into a dense summary; strips ``<think>`` tags."""
        conversation_id = context_data.get("conversation_id")
        n_recent = _clamp_n_recent(context_data.get("n_recent", 5))

        if conversation_id:
            dialogues = await self._fetch_and_split_dialogues(str(conversation_id), n_recent)
            recent_dialogues = dialogues["recent"][-n_recent:]
            older_dialogues = dialogues["older"]
        else:
            r = context_data.get("recent_dialogues", [])
            o = context_data.get("older_dialogues", [])
            r_list = [str(x) for x in r] if isinstance(r, list) else []
            o_list = [str(x) for x in o] if isinstance(o, list) else []
            r2, o2 = _truncate_dialogue_lists(r_list, o_list)
            recent_dialogues = r2[-n_recent:]
            older_dialogues = o2

        system_template = """你是一个专业的上下文压缩助手。你的任务是将冗长的对话和背景信息压缩成极其精简且高密度的格式。

**必须严格遵守以下规则：**
1. **必须**严格按照以下三部分进行输出，且每部分必须包含标题（使用 ** 加粗）：
   - **工作上下文（working context）**：当前的系统提示关键点、当前任务、以及最近5轮对话核心。
   - **会话摘要（session summary）**：把较早的对话压缩成“到目前为止发生了什么”。
   - **长期记忆（durable memory）**：提炼用户偏好、长期约束、术语表、项目背景、已确认决策。
   - **记录最近一轮的待执行决策**
2. **总字数控制在300字左右。** 优先保证信息完整性，绝对不能在话说到一半时截断。
3. 使用极其简练的短句，保留关键实体和动作。
4. 确保最终输出包含完整的三个部分，并在完成所有总结后结束。
5. 不要加入任何多余的客套话或解释性文本。"""

        human_template = """请按照要求压缩以下信息：

【当前系统提示】：{system_prompt}
【当前任务】：{current_task}
【最近对话】：
{recent_dialogues}

【较早对话】：
{older_dialogues}

【用户偏好与长期约束】：{user_preferences}
【项目背景与术语】：{project_background}
【已确认的决策】：{confirmed_decisions}"""

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_template),
            ("human", human_template),
        ])

        chain = prompt | self.llm | StrOutputParser()

        def join_dialogues(dialogues: Any) -> str:
            if not dialogues:
                return "无"
            if isinstance(dialogues, list):
                return "\n".join(f"- {d}" for d in dialogues)
            return str(dialogues)

        payload = {
            "system_prompt": context_data.get("system_prompt", "无"),
            "current_task": context_data.get("current_task", "无"),
            "recent_dialogues": join_dialogues(recent_dialogues),
            "older_dialogues": join_dialogues(older_dialogues),
            "user_preferences": context_data.get("user_preferences", "无"),
            "project_background": context_data.get("project_background", "无"),
            "confirmed_decisions": context_data.get("confirmed_decisions", "无"),
        }

        try:
            logger.info("Starting context compression for conversation %s", conversation_id)
            raw_result = await chain.ainvoke(payload)
        except Exception as e:
            if _is_upstream_html_response(e):
                logger.error(
                    "Context compression: upstream LLM URL returned HTML (not JSON). "
                    "Check PRIMARY_LLM_API_URL and reverse proxy: /llm/... must route to the "
                    "inference service (e.g. vLLM), not a web UI."
                )
                raise LlmEndpointMisconfiguredError(
                    "LLM 接口返回了网页而非 API 结果。请检查 PRIMARY_LLM_API_URL 与 Nginx/网关："
                    "路径需指向 OpenAI 兼容的推理服务（如 vLLM）。"
                ) from e

            error_text = str(e)
            match = re.search(r"\((\d+)\s*>\s*(\d+)\s*-\s*(\d+)\)", error_text)
            if not match:
                logger.error("Error during context compression: %s", error_text[:2000])
                raise e

            requested_max = int(match.group(1))
            max_context = int(match.group(2))
            input_tokens = int(match.group(3))
            available = max_context - input_tokens - 64
            retry_max_tokens = min(requested_max, self.max_tokens, max(64, available))

            if retry_max_tokens <= 0:
                logger.error("Error during context compression: %s", error_text[:2000])
                raise e

            logger.warning(
                "Context close to model limit, retrying with reduced max_tokens: "
                f"requested={requested_max}, available={max_context - input_tokens}, retry={retry_max_tokens}"
            )

            retry_llm = ChatOpenAI(
                base_url=self.base_url,
                api_key=settings.PRIMARY_LLM_API_KEY or "not-needed",
                model=self.model_name,
                temperature=self.temperature,
                max_tokens=retry_max_tokens,
            )
            retry_chain = prompt | retry_llm | StrOutputParser()
            raw_result = await retry_chain.ainvoke(payload)

        processed_result = re.sub(r"<think>.*?</think>", "", raw_result, flags=re.DOTALL | re.IGNORECASE).strip()
        if "<think>" in processed_result.lower():
            processed_result = re.split(r"<think>", processed_result, flags=re.IGNORECASE)[0].strip()

        logger.info("[Debug] Raw Compression Result length: %s", len(raw_result))
        logger.info("[Debug] Processed Compression Result length: %s", len(processed_result))

        logger.info("Context compression completed successfully.")
        return processed_result


async def compress_context(
    context_data: Dict[str, Any], conversation_repo: ConversationRepoPort
) -> str:
    """Convenience wrapper: ``ContextCompressor(conversation_repo).compress``."""
    return await ContextCompressor(conversation_repo=conversation_repo).compress(context_data)
