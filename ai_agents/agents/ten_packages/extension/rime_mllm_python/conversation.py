"""Reply identity and acknowledged tool rounds, independent of transport."""

from dataclasses import dataclass, field


@dataclass
class Reply:
    """One generated reply and the tools whose results it awaits."""

    generation: int
    transcript: str = ""
    completed: bool = False
    terminal: bool = False
    calls: set[str] = field(default_factory=set)
    confirmed_results: set[str] = field(default_factory=set)
    round_closed: bool = False


class Conversation:
    """Bounded reply state that never mistakes generation for playback."""

    def __init__(self):
        self.generation = 0
        self.replies: dict[str, Reply] = {}
        self.calls: dict[str, str] = {}
        self.result_events: dict[str, str] = {}

    def created(self, response_id: str) -> None:
        """Retain pending tool rounds while pruning completed old replies."""
        if response_id in self.replies:
            raise ValueError("duplicate response identity")
        for identity, reply in list(self.replies.items()):
            if len(self.replies) < 64:
                break
            if reply.terminal and (
                not reply.calls
                or reply.round_closed
                or not reply.completed
                or reply.generation != self.generation
            ):
                del self.replies[identity]
                for call_id in reply.calls:
                    self.calls.pop(call_id, None)
                self.result_events = {
                    event_id: call_id
                    for event_id, call_id in self.result_events.items()
                    if call_id not in reply.calls
                }
        if len(self.replies) >= 64:
            raise ValueError("too many pending replies")
        self.replies[response_id] = Reply(self.generation)

    def reply(self, response_id: str) -> Reply:
        """Require a response.created before a reply's output."""
        try:
            return self.replies[response_id]
        except KeyError as error:
            raise ValueError("output refers to an unknown reply") from error

    def accepts_output(self, response_id: str) -> bool:
        """Reject late audio/text from replies preceding an interruption."""
        return self.reply(response_id).generation == self.generation

    def interrupt(self) -> None:
        """Invalidate every reply whose audio may already be queued."""
        self.generation += 1

    def function_call(self, response_id: str, call_id: str) -> bool:
        """Record each call once, retaining its parent response identity."""
        reply = self.reply(response_id)
        if call_id in self.calls:
            if self.calls[call_id] != response_id:
                raise ValueError("tool call identity changed parent")
            return False
        if len(reply.calls) >= 32:
            raise ValueError("too many calls in one reply")
        self.calls[call_id] = response_id
        reply.calls.add(call_id)
        return True

    def result_sent(self, event_id: str, call_id: str) -> None:
        """Associate a submitted result with the acknowledgment it needs."""
        if call_id not in self.calls:
            raise ValueError("result refers to an unknown tool call")
        reply = self.reply(self.calls[call_id])
        if (
            call_id in reply.confirmed_results
            or call_id in self.result_events.values()
        ):
            raise ValueError("tool result was already submitted")
        self.result_events[event_id] = call_id

    def acknowledged(self, event_id: str) -> None:
        """Confirm a tool result only after conversation.item.created."""
        call_id = self.result_events.pop(event_id, None)
        if call_id is not None:
            self.reply(self.calls[call_id]).confirmed_results.add(call_id)

    def result_refused(self, event_id: str) -> bool:
        """Retire a rejected result without allowing a continuation."""
        return self.result_events.pop(event_id, None) is not None

    def done(self, response_id: str, status: str) -> None:
        """Close generation without declaring the audio played."""
        reply = self.reply(response_id)
        reply.terminal = True
        reply.completed = status == "completed"

    def close_round(self, response_id: str) -> None:
        """Record a server-closed tool round while accepting late results."""
        self.reply(response_id).round_closed = True

    def continuations(self) -> list[str]:
        """Claim each complete tool round exactly once."""
        ready = []
        for response_id, reply in self.replies.items():
            if (
                reply.completed
                and reply.generation == self.generation
                and reply.calls
                and reply.calls == reply.confirmed_results
                and not reply.round_closed
            ):
                reply.round_closed = True
                ready.append(response_id)
        return ready
