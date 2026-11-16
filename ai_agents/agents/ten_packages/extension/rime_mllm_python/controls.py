"""Typed TEN commands and reply-review contracts."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class ProviderCommand(str, Enum):
    """Application controls accepted by the Prism extension."""

    UPDATE_SESSION = "prism_update_session"
    INPUT_START = "prism_input_audio_start"
    INPUT_COMMIT = "prism_input_audio_commit"
    INPUT_CLEAR = "prism_input_audio_clear"
    CREATE_RESPONSE = "prism_create_response"
    CANCEL_RESPONSE = "prism_cancel_response"


class ReviewAction(str, Enum):
    """A review may speak the chunk, replace it, or end the reply."""

    ALLOW = "allow"
    REPLACE = "replace"
    STOP = "stop"


class ReviewDecision(BaseModel):
    """The review command's decision property, copied to Prism's result."""

    model_config = ConfigDict(extra="forbid")
    action: ReviewAction
    text: str | None = None

    @model_validator(mode="after")
    def check_replacement(self):
        """Only replace carries text; the server owns its context-size limit."""
        if self.action == ReviewAction.REPLACE:
            if self.text is None or not self.text.strip():
                raise ValueError("replace requires nonblank text")
        elif self.text is not None:
            raise ValueError("text is valid only with replace")
        return self


class ReviewRequest(BaseModel):
    """One server-authored chunk awaiting the review extension's decision."""

    review_id: str = Field(min_length=1)
    response_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    index: StrictInt = Field(ge=0)
    text: str
    event_id: str | None = None
    server_time_ms: StrictInt | None = Field(default=None, ge=0)
