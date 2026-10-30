import time
from .timers import Deadline


class Signal:
    def __init__(self, storage, signal_id, seconds, clock=time.monotonic):
        self.storage, self.id = storage, signal_id
        self.deadline = Deadline(seconds, clock)
        self.status = "pending"

    def refresh(self):
        if self.status == "pending" and self.deadline.expired:
            self._set("expired", {"reason": "deadline"})
        return self.status

    def _set(self, status, payload=None):
        with self.storage.db:
            self.storage.event("signal_" + status, self.id, payload)
        self.status = status

    def confirm(self):
        if self.refresh() != "pending":
            return False
        self._set("started")
        return True

    def cancel(self):
        if self.refresh() == "pending":
            self._set("cancelled")

    def rehearse(self):
        if self.refresh() == 'pending':
            self._set('rehearsal', {'reason':'first_auto_signal'})
