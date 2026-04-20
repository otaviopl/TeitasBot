from __future__ import annotations

import importlib
from typing import Any

from assistant_connector.models import ToolDefinition, ToolExecutionContext, ToolHandler


class ToolRegistry:
    def __init__(self, tool_definitions: dict[str, ToolDefinition]):
        self._tool_definitions = dict(tool_definitions)
        self._handler_cache: dict[str, ToolHandler] = {}

    def get_tool_definition(self, tool_name: str) -> ToolDefinition:
        if tool_name not in self._tool_definitions:
            raise ValueError(f"Unknown tool: {tool_name}")
        return self._tool_definitions[tool_name]

    def get_openai_tools(self, tool_names: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in self._resolve_tools(tool_names)
        ]

    def describe_tools(self, tool_names: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "write_operation": tool.write_operation,
                "input_schema": tool.input_schema,
                "prompt_guidance": tool.prompt_guidance,
                "guidance_priority": tool.guidance_priority,
            }
            for tool in self._resolve_tools(tool_names)
        ]

    def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        tool_definition = self.get_tool_definition(tool_name)
        handler = self._resolve_handler(tool_definition.handler)
        result = handler(arguments, context)
        if not isinstance(result, dict):
            raise ValueError(f"Tool '{tool_name}' must return a dict payload")
        return result

    def _resolve_tools(self, tool_names: list[str]) -> list[ToolDefinition]:
        return [self.get_tool_definition(tool_name) for tool_name in tool_names]

    def _resolve_handler(self, handler_path: str) -> ToolHandler:
        if handler_path in self._handler_cache:
            return self._handler_cache[handler_path]

        module_path, _, function_name = handler_path.partition(":")
        if not module_path or not function_name:
            raise ValueError(f"Invalid handler path: {handler_path}")

        module = importlib.import_module(module_path)
        handler = getattr(module, function_name, None)
        if not callable(handler):
            raise ValueError(f"Handler is not callable: {handler_path}")

        self._handler_cache[handler_path] = handler
        return handler
