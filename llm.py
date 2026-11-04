"""统一的结构化输出调用：用强制 tool calling 让模型按 JSON Schema 返回结果。
支持 Anthropic 和兼容 OpenAI 接口的模型（DeepSeek、Kimi、通义千问等）。"""
import copy
import json
from dataclasses import dataclass

import config

_clients: dict = {}


@dataclass
class LLMResult:
    data: dict
    input_tokens: int
    output_tokens: int
    model: str
    raw_text: str = ""       # 模型返回的原始参数文本，校验失败时用来诊断
    finish_reason: str = ""
    attempts: int = 1        # 含重试在内的调用次数，tokens 也是所有尝试的累计


class LLMOutputError(Exception):
    """模型输出无法解析或不符合 schema。带上原始文本和 finish_reason，便于判断是否被截断。"""

    def __init__(self, summary: str, raw_text: str, finish_reason: str | None,
                 output_tokens: int, input_tokens: int = 0):
        self.summary = summary
        self.raw_text = raw_text
        self.finish_reason = finish_reason
        self.output_tokens = output_tokens
        self.input_tokens = input_tokens
        # 重试耗尽后由 analyze 填入累计值
        self.attempts = 1
        self.total_input_tokens = input_tokens
        self.total_output_tokens = output_tokens
        super().__init__(f"{summary}\nfinish_reason={finish_reason}\n原始输出:\n{raw_text}")


def _inline_refs(schema: dict) -> dict:
    """pydantic 生成的 schema 带 $defs/$ref，展开后各家接口兼容性更好。"""
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)


def call_structured(system: str, user: str, tool_name: str, tool_desc: str,
                    schema: dict, model: str | None = None, max_tokens: int = 2000) -> LLMResult:
    schema = _inline_refs(schema)
    model = model or config.MODEL
    if config.PROVIDER == "anthropic":
        return _anthropic(system, user, tool_name, tool_desc, schema, model, max_tokens)
    if config.PROVIDER == "openai_compat":
        return _openai_compat(system, user, tool_name, tool_desc, schema, model, max_tokens)
    raise ValueError(f"未知的 LLM_PROVIDER: {config.PROVIDER}")


def _anthropic(system, user, name, desc, schema, model, max_tokens) -> LLMResult:
    import anthropic
    if "anthropic" not in _clients:
        _clients["anthropic"] = anthropic.Anthropic()
    resp = _clients["anthropic"].messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=config.LLM_TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{"name": name, "description": desc, "input_schema": schema}],
        tool_choice={"type": "tool", "name": name},
    )
    block = next((b for b in resp.content if b.type == "tool_use"), None)
    if block is None:
        raise LLMOutputError("模型没有返回工具调用", str(resp.content), resp.stop_reason,
                             resp.usage.output_tokens, resp.usage.input_tokens)
    return LLMResult(block.input, resp.usage.input_tokens, resp.usage.output_tokens, model,
                     json.dumps(block.input, ensure_ascii=False), resp.stop_reason or "")


def _openai_compat(system, user, name, desc, schema, model, max_tokens) -> LLMResult:
    from openai import OpenAI
    if "openai" not in _clients:
        _clients["openai"] = OpenAI(base_url=config.OPENAI_BASE_URL)
    resp = _clients["openai"].chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=config.LLM_TEMPERATURE,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        tools=[{"type": "function", "function": {"name": name, "description": desc, "parameters": schema}}],
        tool_choice={"type": "function", "function": {"name": name}},
    )
    msg = resp.choices[0].message
    if not msg.tool_calls:
        raise LLMOutputError("模型没有返回工具调用", msg.content or "", resp.choices[0].finish_reason,
                             resp.usage.completion_tokens, resp.usage.prompt_tokens)
    raw = msg.tool_calls[0].function.arguments
    finish_reason = resp.choices[0].finish_reason
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMOutputError(f"JSONDecodeError: {e}", raw, finish_reason,
                             resp.usage.completion_tokens, resp.usage.prompt_tokens) from e
    return LLMResult(data, resp.usage.prompt_tokens, resp.usage.completion_tokens, model, raw, finish_reason)
