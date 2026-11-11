"""Configuration and fixed PCM formats for Prism conversations."""

from enum import Enum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
INPUT_FRAME_SECONDS = 0.02
INPUT_FRAME_BYTES = int(INPUT_SAMPLE_RATE * INPUT_FRAME_SECONDS) * SAMPLE_WIDTH


class Eagerness(str, Enum):
    """How readily Prism decides that the caller has finished speaking."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    AUTO = "auto"


class RimeRealtimeConfig(BaseModel):
    """Backend credentials, session settings, and bounded startup policy."""

    api_key: SecretStr = SecretStr("")
    endpoint: str = "wss://prism.api.rime.ai/v1/realtime"
    voice: str = "aurora"
    prompt: str = ""
    interrupt_response: bool = True
    eagerness: Eagerness = Eagerness.AUTO
    startup_timeout: float = Field(default=15.0, gt=0, le=120)
    startup_retries: int = Field(default=2, ge=0, le=5)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        """Accept TLS endpoints and local WebSocket test servers."""
        parsed = urlsplit(value)
        # Accessing port validates numeric syntax and the TCP port range.
        _ = parsed.port
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in value)
            or parsed.scheme not in ({"ws", "wss"} if local else {"wss"})
        ):
            raise ValueError(
                "endpoint must be a WebSocket URL without credentials or query"
            )
        return value

    def session_settings(self, tools: list[dict]) -> dict:
        """Build only settings accepted by Prism's session.update."""
        return {
            "voice": self.voice,
            "instructions": self.prompt,
            "tools": tools,
            "turn_detection": {
                "interrupt_response": self.interrupt_response,
                "eagerness": self.eagerness.value,
            },
        }
