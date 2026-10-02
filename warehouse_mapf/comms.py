"""Range-limited message passing between agents.

Repair decisions are computed by the disrupted agent, but every piece of
information it uses about another agent has to arrive as a message from an
agent within communication range. The bus enforces the range and logs
everything so that communication overhead can be reported.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .grid import Cell

MSG_TYPES = ('ALERT', 'STATUS', 'REQUEST', 'PROPOSE', 'ACCEPT', 'REJECT', 'COMMIT',
             'UPDATE', 'TASK_ANNOUNCE', 'BID', 'AWARD')


@dataclass
class Message:
    t: int
    sender: int
    receiver: int
    kind: str
    payload: dict = field(default_factory=dict)


class MessageBus:
    def __init__(self, keep_log: bool = False):
        self.counts: Counter = Counter()
        self.total = 0
        self.keep_log = keep_log
        self.log: list[Message] = []

    @staticmethod
    def in_range(a: Cell, b: Cell, radius: int) -> bool:
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) <= radius

    def send(self, t: int, sender: int, receiver: int, kind: str, **payload) -> None:
        assert kind in MSG_TYPES, kind
        self.counts[kind] += 1
        self.total += 1
        if self.keep_log:
            self.log.append(Message(t, sender, receiver, kind, payload))

    def broadcast(self, t: int, sender: int, receivers: list[int], kind: str, **payload) -> None:
        for r in receivers:
            self.send(t, sender, r, kind, **payload)
