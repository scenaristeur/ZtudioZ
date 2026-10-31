from __future__ import annotations

import json
import logging
import os
import random
import re
import string
import sys
from typing import Any, Optional

from camel.agents import ChatAgent
from camel.messages import BaseMessage
from camel.models.openai_compatible_model import OpenAICompatibleModel
from camel.toolkits import FunctionTool
from camel.types import RoleType
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)

from pipeline.config import get_service_config
from pipeline.consumption import get_tracker

logger = logging.getLogger(__name__)


def _extract_tool_calls_block(text: str) -> list[tuple[str, str, int, int]]:
    results = []
    marker = "[TOOL_CALLS]"
    i = 0
    while True:
        pos = text.find(marker, i)
        if pos == -1:
            break
        j = pos + len(marker)
        while j < len(text) and text[j] in " \t\n\r":
            j += 1
        name_start = j
        while j < len(text) and (text[j].isalnum() or text[j] in "_"):
            j += 1
        name = text[name_start:j]
        if not name:
            i = j
            continue
        while j < len(text) and text[j] in " \t\n\r":
            j += 1
        if j >= len(text) or text[j] != "{":
            i = j
            continue
        depth = 0
        start = j
        in_str = False
        escape = False
        while j < len(text):
            ch = text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        args = text[start : j + 1]
                        results.append((name, args, pos, j + 1))
                        j += 1
                        break
            j += 1
        i = j
    return results


def _repair_json_args(args_str: str) -> Optional[str]:
    try:
        json.loads(args_str)
        return args_str
    except json.JSONDecodeError:
        pass
    candidates = [args_str, args_str.replace("'", '"')]
    for c in candidates:
        try:
            json.loads(c)
            return c
        except json.JSONDecodeError:
            pass
    for arg_name in ("prompt", "content", "filename", "model", "message"):
        for c in candidates:
            fixed = _repair_string_arg(c, arg_name)
            if fixed is not None:
                return fixed
    return None


def _repair_string_arg(raw: str, arg_name: str) -> Optional[str]:
    idx = raw.find(f'"{arg_name}"')
    if idx == -1:
        return None
    colon = raw.find(":", idx + len(arg_name) + 2)
    if colon == -1:
        return None
    search_start = colon + 1
    while search_start < len(raw) and raw[search_start] in " \t\n\r":
        search_start += 1
    if search_start >= len(raw) or raw[search_start] != '"':
        return None
    content_start = search_start + 1
    i = content_start
    while i < len(raw):
        ch = raw[i]
        if ch == "\\":
            i += 2
            continue
        if ch != '"':
            i += 1
            continue
        after = i + 1
        while after < len(raw) and raw[after] in " \t\n\r":
            after += 1
        if after >= len(raw) or raw[after] in ",}":
            raw_val = raw[content_start:i]
            raw_val = raw_val.replace("\\", "\\\\")
            raw_val = raw_val.replace('"', '\\"')
            raw_val = raw_val.replace("\n", "\\n")
            raw_val = raw_val.replace("\r", "\\r")
            raw_val = raw_val.replace("\t", "\\t")
            prefix = raw[:content_start]
            suffix = raw[i:]
            fixed = prefix + raw_val + suffix
            try:
                json.loads(fixed)
                return fixed
            except json.JSONDecodeError:
                return None
        i += 1
    return None


def _convert_tool_calls_in_response(response: ChatCompletion) -> ChatCompletion:
    if not response.choices:
        return response
    choice = response.choices[0]
    content = choice.message.content or ""
    if "[TOOL_CALLS]" not in content:
        return response
    blocks = _extract_tool_calls_block(content)
    if not blocks:
        return response
    clean = content
    for _name, _args, start, end in reversed(blocks):
        clean = clean[:start] + clean[end:]
    clean_content = clean.strip()
    tool_calls = []
    for i, (name, args, _start, _end) in enumerate(blocks):
        if name == "write_to_file":
            name = "write_file"
        try:
            json.loads(args)
        except json.JSONDecodeError:
            repaired = _repair_json_args(args)
            if repaired is not None:
                args = repaired
            else:
                logger.warning("Skipping tool call %s: invalid JSON", name)
                continue
        call_id = "".join(random.choices(string.ascii_letters + string.digits, k=9))
        tool_calls.append(
            ChatCompletionMessageToolCall(
                id=call_id,
                type="function",
                function=Function(name=name, arguments=args),
            )
        )
    new_msg = ChatCompletionMessage(
        role="assistant",
        content=clean_content or None,
        tool_calls=tool_calls,
    )
    new_choice = choice.__class__(
        index=choice.index,
        finish_reason="tool_calls",
        message=new_msg,
        logprobs=choice.logprobs,
    )
    return ChatCompletion(
        id=response.id,
        choices=[new_choice],
        created=response.created,
        model=response.model,
        object=response.object,
        usage=response.usage,
    )


class ToolCallingModel(OpenAICompatibleModel):
    verbose: bool = False

    def _request_chat_completion(self, messages: Any, tools: Any = None) -> ChatCompletion:
        if self.verbose:
            print("\n══════ LLM REQUEST ══════", file=sys.stderr)
            for m in messages:
                role = m.get("role", "?")
                content = m.get("content", "")
                if content:
                    print(f"  [{role}]: {str(content)[:600]}", file=sys.stderr)
                if m.get("tool_calls"):
                    for tc in m["tool_calls"]:
                        if isinstance(tc, dict):
                            fn = tc.get("function", {})
                            fname, fargs = fn.get("name", "?"), fn.get("arguments", "")
                        else:
                            fname, fargs = tc.function.name, tc.function.arguments
                        print(f"  [{role}] TOOL CALL: {fname}({str(fargs)[:200]})", file=sys.stderr)
                if m.get("tool_call_id"):
                    print(f"  [{role}] TOOL RESULT: {str(content)[:200]}", file=sys.stderr)
            print("══════ END REQUEST ══════\n", file=sys.stderr)

        msgs = list(messages)
        fixed = []
        for m in msgs:
            if m["role"] == "user" and fixed and fixed[-1]["role"] == "tool":
                fixed.append({"role": "assistant", "content": "Suite."})
            fixed.append(m)
        msgs = fixed
        if msgs and msgs[-1].get("role") == "assistant":
            msgs.append({"role": "user", "content": "Suite."})
        msgs = tuple(msgs) if isinstance(messages, tuple) else msgs
        resp = super()._request_chat_completion(msgs, tools)
        resp = _convert_tool_calls_in_response(resp)
        if self.verbose:
            choice = resp.choices[0]
            msg = choice.message
            print("\n══════ LLM RESPONSE ══════", file=sys.stderr)
            if msg.content:
                print(f"  {msg.content[:800]}", file=sys.stderr)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    print(f"  TOOL CALL: {tc.function.name}({tc.function.arguments[:300]})", file=sys.stderr)
            print(f"  finish_reason: {choice.finish_reason}", file=sys.stderr)
            print("══════ END RESPONSE ══════\n", file=sys.stderr)
        return resp


class LLMService:
    def __init__(self, config: dict | None = None):
        self.config = config or get_service_config("llm")
        self._model: ToolCallingModel | None = None

    def _get_model(self) -> ToolCallingModel:
        if self._model is None:
            self._model = ToolCallingModel(
                model_type=self.config["model"],
                api_key=self.config["api_key"],
                url=self.config["base_url"],
                model_config_dict={"temperature": self.config.get("temperature", 0.2)},
            )
        return self._model

    def chat(self, messages: list[dict], tools: list | None = None, model: str | None = None) -> str:
        import time
        t0 = time.time()
        cfg = dict(self.config)
        if model:
            cfg["model"] = model
        m = ToolCallingModel(
            model_type=cfg["model"],
            api_key=cfg["api_key"],
            url=cfg["base_url"],
            model_config_dict={"temperature": cfg.get("temperature", 0.2)},
        )
        resp = m._request_chat_completion(messages, tools)
        elapsed = (time.time() - t0) * 1000

        usage = resp.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0

        msg = resp.choices[0].message
        action = "tool_call" if msg.tool_calls else "chat"
        status = "ok"
        error = ""

        if msg.tool_calls:
            result = json.dumps([
                {"name": tc.function.name, "arguments": tc.function.arguments}
                for tc in msg.tool_calls
            ])
        else:
            result = msg.content or ""

        get_tracker().track(
            service="llm",
            model=cfg["model"],
            action=action,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=elapsed,
            status=status,
        )
        return result

    def chat_structured(self, system: str, user: str, response_schema: dict) -> dict:
        messages = [
            {"role": "system", "content": system + f"\nRéponds UNIQUEMENT avec du JSON valide respectant ce schéma : {json.dumps(response_schema)}"},
            {"role": "user", "content": user},
        ]
        result = self.chat(messages)
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON, attempt to repair")
            try:
                match = re.search(r"\{.*\}", result, re.DOTALL)
                if match:
                    return json.loads(match.group())
            except (json.JSONDecodeError, AttributeError):
                pass
            return {"error": f"Invalid JSON response: {result[:200]}"}

    def vision(self, images: list[str], prompt: str, model: str | None = None) -> str:
        import time
        t0 = time.time()
        from openai import OpenAI
        cfg = get_service_config("vision")
        client = OpenAI(
            api_key=cfg.get("api_key", self.config.get("api_key", "")),
            base_url=cfg.get("base_url", self.config.get("base_url", "")),
        )
        content: list[dict] = [{"type": "text", "text": prompt}]
        for img in images:
            if img:
                resolved = self._resolve_image(img)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": resolved},
                })
        model_name = model or cfg.get("model", "gemma-4-31b-it")
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": content}],
            temperature=cfg.get("temperature", 0.1),
            max_tokens=cfg.get("max_tokens", 2000),
        )
        elapsed = (time.time() - t0) * 1000
        usage = resp.usage
        get_tracker().track(
            service="llm",
            model=model_name,
            action="vision",
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            duration_ms=elapsed,
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def _resolve_image(path_or_url: str) -> str:
        import base64
        from pathlib import Path
        if path_or_url.startswith(("http://", "https://", "data:")):
            return path_or_url
        p = Path(path_or_url)
        if p.exists():
            mimetype = "image/png"
            if p.suffix in (".jpg", ".jpeg"):
                mimetype = "image/jpeg"
            elif p.suffix == ".webp":
                mimetype = "image/webp"
            b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
            return f"data:{mimetype};base64,{b64}"
        return path_or_url


def create_agent(
    role_name: str,
    system_message: str,
    tools: list | None = None,
    model_type: str | None = None,
    verbose: bool = False,
) -> ChatAgent:
    cfg = get_service_config("llm")
    model_type = model_type or cfg.get("model", "openai/gpt-oss-120b")
    model = ToolCallingModel(
        model_type=model_type,
        api_key=cfg["api_key"],
        url=cfg["base_url"],
        model_config_dict={"temperature": cfg.get("temperature", 0.2)},
    )
    if verbose:
        model.verbose = True
    return ChatAgent(
        system_message=BaseMessage(
            role_name=role_name,
            role_type=RoleType.ASSISTANT,
            meta_dict=None,
            content=system_message,
        ),
        model=model,
        tools=tools or [],
        output_language="french",
        token_limit=cfg.get("max_tokens", 4000),
    )


def tool(func):
    return FunctionTool(func)
