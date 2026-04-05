from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


ToolHandler = Callable[[dict[str, Any], "ToolExecutionContext"], dict[str, Any]]


class ResponseAttachments:
    """Mutable container for media attachments produced during a single chat turn.

    Tools receive this object via ToolExecutionContext and can append image paths
    (or other future media) that the runtime will forward to the calling channel.
    """

    def __init__(self) -> None:
        self.images: list[str] = []

    def add_image(self, path: str) -> None:
        """Register a local image file path to be delivered alongside the text response."""
        cleaned = str(path or "").strip()
        if cleaned:
            self.images.append(cleaned)

    def __bool__(self) -> bool:
        return bool(self.images)


@dataclass
class ChatResponse:
    """Structured response returned by AssistantRuntime and AssistantService.

    Carries the text reply and any media attachments (image file paths) that
    should be delivered to the user in addition to the text.
    """

    text: str
    image_paths: list[str] = field(default_factory=list)

    @property
    def has_images(self) -> bool:
        return bool(self.image_paths)



@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: str
    write_operation: bool = False
    auto_confirm: bool = False
    prompt_guidance: str = ""
    guidance_priority: int = 100


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    description: str
    model: str
    system_prompt: str
    tools: list[str]
    max_tool_rounds: int = 6
    memory_window: int = 20


@dataclass(frozen=True)
class ToolExecutionContext:
    session_id: str
    user_id: str
    channel_id: str
    guild_id: str | None
    project_logger: Any
    agent: AgentDefinition
    available_tools: list[dict[str, Any]]
    available_agents: list[dict[str, Any]]
    user_credential_store: Any = None  # UserCredentialStore | None
    memories_dir: str | None = None
    file_store: Any = None  # FileStore | None
    memory_store: Any = None  # ConversationMemoryStore | None
    response_attachments: ResponseAttachments | None = None  # mutable; frozen ref, mutable contents
