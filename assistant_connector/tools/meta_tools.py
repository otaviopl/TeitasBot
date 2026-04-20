from __future__ import annotations


def list_available_tools(_arguments, context):
    return {
        "agent_id": context.agent.agent_id,
        "tools": context.available_tools,
    }


def list_available_agents(_arguments, context):
    return {
        "active_agent_id": context.agent.agent_id,
        "agents": context.available_agents,
    }
