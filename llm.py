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
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{"name": name, "description": desc, "input_schema": schema}],
        tool_choice={"type": "tool", "name": name},
    )
    block = next((b for b in resp.content if b.type == "tool_use"), None)
    if block is None:
        raise RuntimeError("模型没有返回工具调用")
    return LLMResult(block.input, resp.usage.input_tokens, resp.usage.output_tokens, model)


def _openai_compat(system, user, name, desc, schema, model, max_tokens) -> LLMResult:
    from openai import OpenAI
    if "openai" not in _clients:
        _clients["openai"] = OpenAI(base_url=config.OPENAI_BASE_URL)
    resp = _clients["openai"].chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        tools=[{"type": "function", "function": {"name": name, "description": desc, "parameters": schema}}],
        tool_choice={"type": "function", "function": {"name": name}},
    )
    msg = resp.choices[0].message
    if not msg.tool_calls:
        raise RuntimeError("模型没有返回工具调用")
    data = json.loads(msg.tool_calls[0].function.arguments)
    return LLMResult(data, resp.usage.prompt_tokens, resp.usage.completion_tokens, model)
