"""
Dynamic skill loader — reads skills/*.md at import time and registers each as a @tool.

Frontmatter fields:
  name         (str)  — tool name, snake_case
  model        (str)  — Ollama model to use
  temperature  (float, default 0.1)
  description  (str)  — shown to the orchestrator LLM
  parameters   (dict) — {param_name: {type, description?, default?}}

The markdown body (after the second ---) becomes the system prompt.
Strings like {language} in the body are filled from call arguments at runtime.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from agent.config import MODEL_GENERAL, make_llm

_SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _parse_skill_md(path: Path) -> dict[str, Any]:
    """Return parsed frontmatter dict + body string from a skill.md file."""
    text = path.read_text(encoding="utf-8")
    # Split on first two --- fences
    parts = re.split(r"^---\s*$", text, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        raise ValueError(f"{path}: missing YAML frontmatter")
    meta = yaml.safe_load(parts[1]) or {}
    meta["_body"] = parts[2].strip()
    return meta


def _make_skill_tool(meta: dict[str, Any]) -> StructuredTool:
    name: str = meta["name"]
    model: str = meta.get("model", MODEL_GENERAL)
    temperature: float = float(meta.get("temperature", 0.1))
    description: str = meta.get("description", f"Skill: {name}")
    body: str = meta["_body"]
    params: dict = meta.get("parameters", {})

    # Build a typed dict for the function signature (StructuredTool infers schema from it)
    # We use **kwargs and build a Pydantic schema manually via args_schema injection.
    from pydantic import BaseModel, Field, create_model

    field_defs: dict[str, Any] = {}
    for pname, pinfo in params.items():
        ptype = str  # all params are strings for now
        default = pinfo.get("default", ...)
        pdesc = pinfo.get("description", pname)
        if default is ...:
            field_defs[pname] = (ptype, Field(..., description=pdesc))
        else:
            field_defs[pname] = (ptype, Field(default=default, description=pdesc))

    ArgsModel = create_model(f"{name}_args", **field_defs)

    def _run(**kwargs: Any) -> str:
        system_prompt = body
        # Fill {param} placeholders in the system prompt
        try:
            system_prompt = system_prompt.format(**kwargs)
        except KeyError:
            pass

        # Build user message from first positional param (first key)
        first_key = next(iter(params), None)
        user_content = kwargs.get(first_key, str(kwargs)) if first_key else str(kwargs)

        return make_llm(model, temperature).invoke([
            SystemMessage(system_prompt),
            HumanMessage(str(user_content)),
        ]).content

    tool = StructuredTool.from_function(
        func=_run,
        name=name,
        description=description,
        args_schema=ArgsModel,
    )
    return tool


def _load_skills() -> list[StructuredTool]:
    tools = []
    if not _SKILLS_DIR.exists():
        return tools
    for path in sorted(_SKILLS_DIR.glob("*.md")):
        try:
            meta = _parse_skill_md(path)
            tools.append(_make_skill_tool(meta))
        except Exception as exc:
            import warnings
            warnings.warn(f"Failed to load skill {path.name}: {exc}")
    return tools


SKILL_TOOLS: list[StructuredTool] = _load_skills()
