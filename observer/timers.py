import math
import time
import secrets


def random_duration_us(minimum_seconds, maximum_seconds, randbelow=secrets.randbelow):
    minimum = round(minimum_seconds * 1_000_000)
    maximum = round(maximum_seconds * 1_000_000)
    return minimum + randbelow(maximum - minimum + 1)


class Deadline:
    def __init__(self, seconds, clock=time.monotonic):
        self.clock = clock
        self.end = clock() + seconds

    @property
    def remaining(self):
        return max(0.0, self.end - self.clock())

    @property
    def expired(self):
        return self.clock() >= self.end

    def display(self):
        seconds = math.ceil(self.remaining)
        return f"{seconds // 60:02}:{seconds % 60:02}"
