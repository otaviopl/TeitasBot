from __future__ import annotations

import json
import os

from assistant_connector.models import AgentDefinition, ToolDefinition


DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__),
    "config",
    "agents.json",
)


class AssistantConfiguration:
    def __init__(
        self,
        agents: dict[str, AgentDefinition],
        tools: dict[str, ToolDefinition],
    ):
        self.agents = agents
        self.tools = tools

    def get_agent(self, agent_id: str) -> AgentDefinition:
        if agent_id not in self.agents:
            raise ValueError(f"Unknown assistant agent id: {agent_id}")
        return self.agents[agent_id]

    def get_agent_summaries(self) -> list[dict[str, str]]:
        return [
            {
                "id": agent.agent_id,
                "description": agent.description,
                "model": agent.model,
            }
            for agent in self.agents.values()
        ]


def load_assistant_configuration(config_path: str | None = None) -> AssistantConfiguration:
    file_path = config_path or DEFAULT_CONFIG_PATH
    with open(file_path, "r", encoding="utf-8") as config_file:
        raw_config = json.load(config_file)

    raw_tools = raw_config.get("tools", [])
    raw_agents = raw_config.get("agents", [])
    if not raw_tools:
        raise ValueError("Assistant configuration must define at least one tool")
    if not raw_agents:
        raise ValueError("Assistant configuration must define at least one agent")

    tool_names = [tool["name"] for tool in raw_tools]
    if len(set(tool_names)) != len(tool_names):
        raise ValueError("Assistant configuration contains duplicated tool names")
    tools = {
        tool["name"]: ToolDefinition(
            name=tool["name"],
            description=tool["description"],
            input_schema=tool.get("input_schema", {"type": "object", "properties": {}}),
            handler=tool["handler"],
            write_operation=bool(tool.get("write_operation", False)),
            auto_confirm=bool(tool.get("auto_confirm", False)),
            prompt_guidance=str(tool.get("prompt_guidance", "")).strip(),
            guidance_priority=int(tool.get("guidance_priority", 100)),
        )
        for tool in raw_tools
    }

    agents: dict[str, AgentDefinition] = {}
    agent_ids = [agent["id"] for agent in raw_agents]
    if len(set(agent_ids)) != len(agent_ids):
        raise ValueError("Assistant configuration contains duplicated agent ids")
    for raw_agent in raw_agents:
        agent_tools = raw_agent.get("tools", [])
        if not agent_tools:
            raise ValueError(f"Agent {raw_agent.get('id')} must include at least one tool")
        unknown_tools = [tool_name for tool_name in agent_tools if tool_name not in tools]
        if unknown_tools:
            raise ValueError(
                f"Agent {raw_agent.get('id')} references unknown tools: {', '.join(unknown_tools)}"
            )

        max_tool_rounds = int(raw_agent.get("max_tool_rounds", 6))
        memory_window = int(raw_agent.get("memory_window", 20))
        if max_tool_rounds < 1:
            raise ValueError(f"Agent {raw_agent.get('id')} max_tool_rounds must be >= 1")
        if memory_window < 1:
            raise ValueError(f"Agent {raw_agent.get('id')} memory_window must be >= 1")

        agent = AgentDefinition(
            agent_id=raw_agent["id"],
            description=raw_agent["description"],
            model=raw_agent.get("model", "gpt-4.1-mini"),
            system_prompt=raw_agent["system_prompt"],
            tools=agent_tools,
            max_tool_rounds=max_tool_rounds,
            memory_window=memory_window,
        )
        agents[agent.agent_id] = agent

    return AssistantConfiguration(agents=agents, tools=tools)
