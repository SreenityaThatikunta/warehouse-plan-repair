from __future__ import annotations

from .grid import Cell

MSG_TYPES = ('ALERT', 'STATUS', 'REQUEST', 'PROPOSE', 'ACCEPT', 'REJECT', 'COMMIT',
             'UPDATE', 'TASK_ANNOUNCE', 'BID', 'AWARD')


class MessageBus:
    def __init__(self):
        self.total = 0

    @staticmethod
    def in_range(a: Cell, b: Cell, radius: int) -> bool:
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) <= radius

    def send(self, t: int, sender: int, receiver: int, kind: str) -> None:
        assert kind in MSG_TYPES, kind
        self.total += 1

    def broadcast(self, t: int, sender: int, receivers: list[int], kind: str) -> None:
        for r in receivers:
            self.send(t, sender, r, kind)
