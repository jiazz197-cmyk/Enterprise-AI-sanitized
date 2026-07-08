"""``<think>`` tag stripping — ported from the Dify ``fuck think`` code node.

The answering LLM (Qwen3.6-35B-A3B) runs with ``reasoning_format=separated``,
which emits ``<think>...</think>`` reasoning blocks. These must never reach the
user. For non-streaming use, :func:`strip_think` removes them. For streaming,
see :class:`ThinkStreamFilter` which buffers tokens until the closing tag.
"""

from __future__ import annotations

import ast
import json
import re


def _extract_text(x: object) -> str:
    """Extract the ``text`` field from a Dify-style LLM output (dict/JSON/str)."""
    if isinstance(x, dict):
        return str(x.get("text", "") or "")
    if x is None:
        return ""

    s = str(x).strip()

    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return str(obj.get("text", "") or "")
    except Exception:
        pass

    try:
        obj = ast.literal_eval(s)
        if isinstance(obj, dict):
            return str(obj.get("text", "") or "")
    except Exception:
        pass

    return s


def strip_think(response: object) -> str:
    """Remove ``<think>...</think>`` blocks and stray reasoning prefixes.

    Verbatim port of the Dify ``fuck think`` node logic.
    """
    text = _extract_text(response)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Keep content after the last closing </think>.
    parts = re.split(r"(?is)</think\s*>", text)
    if len(parts) > 1:
        text = parts[-1].strip()

    # Clean residual <think>...</think> blocks.
    text = re.sub(r"(?is)<think\s*>.*?</think\s*>", "", text).strip()

    # Fallback: some models prefix reasoning with "Thinking Process:" / "思考过程：".
    if re.match(r"(?is)^\s*(thinking process|reasoning|思考过程|推理过程)\s*[:：]", text):
        m = re.search(r"(?m)^(您好|好的|当然|以下|下面|#|##|###)", text)
        if m:
            text = text[m.start():].strip()
        else:
            text = re.sub(
                r"(?is)^\s*(thinking process|reasoning|思考过程|推理过程)\s*[:：].*",
                "",
                text,
            ).strip()

    return text


class ThinkStreamFilter:
    """Streaming filter that swallows ``<think>...</think>`` spans.

    Feed token chunks via :meth:`feed`; it returns the safe-to-emit portion
    (never contains ``<think>`` content). Call :meth:`flush` at stream end to
    release any buffered plain text that was held back while scanning for a
    possible opening tag.

    The answering model emits at most one leading ``<think>`` block, but this
    filter is robust to ``<think>`` appearing mid-stream as well.
    """

    _OPEN = re.compile(r"<think\s*>", re.IGNORECASE)
    _CLOSE = re.compile(r"</think\s*>", re.IGNORECASE)

    def __init__(self) -> None:
        self._buffer = ""
        self._in_think = False

    def feed(self, chunk: str) -> str:
        """Return the emit-safe substring for this chunk."""
        if not chunk:
            return ""
        self._buffer += chunk
        out: list[str] = []

        while self._buffer:
            if self._in_think:
                m = self._CLOSE.search(self._buffer)
                if m:
                    # Drop everything up to and including </think>.
                    self._buffer = self._buffer[m.end():]
                    self._in_think = False
                    continue
                # Closing tag not yet arrived; keep buffering, emit nothing.
                break
            else:
                m = self._OPEN.search(self._buffer)
                if m:
                    # Emit text before <think>, then enter think mode.
                    out.append(self._buffer[:m.start()])
                    self._buffer = self._buffer[m.end():]
                    self._in_think = True
                    # Strip a leading newline that often follows </think>.
                    continue
                # No opening tag yet. To avoid emitting a partial "<think"
                # prefix, hold back the tail that could be the start of one.
                safe = self._safe_emit_length(self._buffer)
                if safe > 0:
                    out.append(self._buffer[:safe])
                    self._buffer = self._buffer[safe:]
                break

        return "".join(out)

    @staticmethod
    def _safe_emit_length(buffer: str) -> int:
        """Length of buffer that cannot be a prefix of ``<think``."""
        open_tag = "<think"
        # Find the latest position where buffer could be starting a <think tag.
        for i in range(1, min(len(buffer), len(open_tag)) + 1):
            if open_tag.startswith(buffer[-i:]):
                return len(buffer) - i
        return len(buffer)

    def flush(self) -> str:
        """Release any remaining buffered plain text at end of stream."""
        out = ""
        if not self._in_think:
            # Whatever remains is plain text (a stray "<think" prefix with no
            # closer is treated as literal text and emitted).
            out = self._buffer
        self._buffer = ""
        self._in_think = False
        return out
