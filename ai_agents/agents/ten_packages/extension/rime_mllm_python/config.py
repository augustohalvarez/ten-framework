"""Configuration and fixed PCM formats for Prism conversations."""

from enum import Enum
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    field_validator,
    model_validator,
)

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
MUTABLE_TURN_DETECTION_FIELDS = (
    "interrupt_response",
    "interrupt_min_words",
    "interrupt_phrases",
    "acknowledgment_phrases",
)
INPUT_FRAME_SECONDS = 0.02
INPUT_FRAME_BYTES = int(INPUT_SAMPLE_RATE * INPUT_FRAME_SECONDS) * SAMPLE_WIDTH


class Eagerness(str, Enum):
    """How readily Prism decides that the caller has finished speaking."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    AUTO = "auto"


class RecordingKind(str, Enum):
    """The data categories a session may request permission to retain."""

    TEXT = "text"
    TOOLS = "tools"
    CALLER_AUDIO = "caller_audio"
    AGENT_AUDIO = "agent_audio"


class RecordingSettings(BaseModel):
    """Initial retention request; the account and server remain authoritative."""

    model_config = ConfigDict(extra="forbid")
    keep: list[RecordingKind] | None = None
    external_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def refuse_null_fields(cls, value):
        """Explicit null is not an instruction to retain account defaults."""
        if isinstance(value, dict) and any(
            item is None for item in value.values()
        ):
            raise ValueError("recording fields must not contain null")
        return value

    @field_validator("keep")
    @classmethod
    def unique_kinds(cls, value):
        """A data category is requested at most once."""
        if value is not None and len(set(value)) != len(value):
            raise ValueError("recording kinds must be unique")
        return value


class ReviewTimeoutPolicy(str, Enum):
    """The server action when a review decision misses its deadline."""

    STOP = "stop"
    SPEAK = "speak"
    SAY = "say"


class ReviewSettings(BaseModel):
    """Initial review policy; timeout lines are distinct from replacements."""

    model_config = ConfigDict(extra="forbid")
    on_timeout: ReviewTimeoutPolicy
    timeout_ms: StrictInt = Field(default=1000, ge=100, le=5000)
    timeout_line: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def check_timeout_line(self):
        """Only the say policy has a nonblank timeout line."""
        if self.on_timeout == ReviewTimeoutPolicy.SAY:
            if not isinstance(self.timeout_line, str) or not str.strip(
                self.timeout_line
            ):
                raise ValueError("say requires a nonblank timeout_line")
        elif self.timeout_line is not None:
            raise ValueError("timeout_line is valid only with say")
        return self


class SessionControls(BaseModel):
    """Mutable session controls, shared by initial config and updates."""

    voice: str = "aurora"
    prompt: str = ""
    interrupt_response: bool = True
    speed: float | None = Field(default=None, ge=0.85, le=1.3, strict=True)
    interrupt_min_words: StrictInt | None = Field(default=None, ge=1, le=10)
    interrupt_phrases: list[str] | None = Field(default=None, max_length=100)
    acknowledgment_phrases: list[str] | None = Field(
        default=None, max_length=100
    )

    @field_validator("interrupt_phrases", "acknowledgment_phrases")
    @classmethod
    def nonblank_phrases(cls, value):
        """Reject blank entries; the server validates phrase matching."""
        if value is not None and any(not phrase.strip() for phrase in value):
            raise ValueError("phrases must not be blank")
        return value


class SessionSettingsUpdate(SessionControls):
    """Explicit mutable fields; initial-only and unknown settings are refused."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def no_null_updates(cls, value):
        """Omitting a setting keeps it; null does not reset it."""
        if isinstance(value, dict) and any(
            item is None for item in value.values()
        ):
            raise ValueError("settings updates must not contain null")
        return value


class RimeRealtimeConfig(SessionControls):
    """Backend credentials, session settings, and bounded startup policy."""

    api_key: SecretStr = SecretStr("")
    endpoint: str = "wss://prism.api.rime.ai/v1/realtime"
    eagerness: Eagerness = Eagerness.AUTO
    manual_turns: bool = False
    prism_end_of_turn_timeout_ms: StrictInt | None = Field(
        default=None, ge=1000, le=10000
    )
    end_of_turn_min_silence_ms: StrictInt | None = Field(
        default=None, ge=0, le=5000
    )
    prism_recording: RecordingSettings | None = None
    prism_review: ReviewSettings | None = None
    review_extension: str | None = Field(default=None, min_length=1)
    emit_server_events: bool = False
    startup_timeout: float = Field(default=15.0, gt=0, le=120)
    startup_retries: int = Field(default=2, ge=0, le=5)

    @model_validator(mode="after")
    def check_initial_settings(self):
        """Review has an explicit handler and turn timing has a valid interval."""
        if self.prism_review is not None and not self.review_extension:
            raise ValueError("prism_review requires review_extension")
        if (
            self.end_of_turn_min_silence_ms is not None
            and self.prism_end_of_turn_timeout_ms is not None
            and self.end_of_turn_min_silence_ms
            >= self.prism_end_of_turn_timeout_ms
        ):
            raise ValueError(
                "minimum silence must be below the end-of-turn timeout"
            )
        return self

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
        values = self.model_dump(mode="json", exclude_none=True)
        detection = {"eagerness": self.eagerness.value}
        for name in (
            *MUTABLE_TURN_DETECTION_FIELDS,
            "prism_end_of_turn_timeout_ms",
            "end_of_turn_min_silence_ms",
        ):
            if name in values:
                detection[name] = values[name]
        result = {
            "voice": self.voice,
            "instructions": self.prompt,
            "tools": tools,
            "turn_detection": None if self.manual_turns else detection,
        }
        for name in ("speed", "prism_recording", "prism_review"):
            if name in values:
                result[name] = values[name]
        return result
