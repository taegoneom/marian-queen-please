import time
import uuid
from enum import Enum
from .storage import utcnow
from .signals import Signal
from .timers import Deadline, random_duration_us
from .strategy import signal_threshold


class State(str, Enum):
    READY = "READY"
    DRAWING = "DRAWING"
    WAITING = "WAITING_FOR_OBSERVATION"
    OBSERVED = "OBSERVED"


class Observation:
    def __init__(self, storage, roster, gacha, rng, config, session_id, clock=time.monotonic):
        self.storage, self.roster, self.gacha, self.rng = storage, roster, gacha, rng
        self.config, self.session_id, self.clock = dict(config), session_id, clock
        self.state = State.READY
        self.batch_id = None
        self.pending = 0
        self.position = 0
        self.signal = None

    def start(self, count=1):
        if self.state != State.READY or self.pending or count not in (1, 10):
            raise ValueError("READY 상태에서 1회 또는 10회 모집을 시작하세요.")
        self.batch_id, self.pending, self.position = str(uuid.uuid4()), count, 0
        self.started_at = utcnow()
        self.animation_us = random_duration_us(self.config['animation_min_seconds'],self.config['animation_max_seconds']) if self.config['randomize_animation'] else round(self.config['animation_seconds']*1_000_000)
        self.animation_seconds = self.animation_us/1_000_000
        with self.storage.db:
            self.storage.event("batch_started", self.batch_id, {"count": count, "mode": self.gacha.mode, "commander": self.config["commander"], "requested_animation_us":self.animation_us})
        self.state = State.DRAWING
        self.animation = Deadline(self.animation_seconds, self.clock)

    def prepare_observation(self):
        if self.state != State.DRAWING or not self.animation.expired:
            raise ValueError("모집 애니메이션이 아직 완료되지 않았습니다.")
        self.state = State.WAITING
        self.deadline = Deadline(self.config["observation_seconds"], self.clock)

    def observe(self, automatic=False, defer_signal=False):
        if self.state != State.WAITING:
            raise ValueError("관측 대기 상태가 아닙니다.")
        if self.deadline.expired:
            self.cancel("observation_timeout")
            return None
        # Manual Enter or explicitly enabled automatic observation reaches here.
        c, probability, rng_mode = self.gacha.draw(self.rng)
        previous, fired = self.storage.observation_state(self.gacha.mode)
        streak = 0 if c["id"] == self.gacha.pickup_id else previous + 1
        fired = False if streak == 0 else fired
        signal_id = None
        if streak >= signal_threshold(self.config) and not fired and not defer_signal:
            signal_id, fired = str(uuid.uuid4()), True
        record = {
            "session_id": self.session_id, "batch_id": self.batch_id, "position": self.position + 1,
            "started_at": self.started_at, "revealed_at": utcnow(), "commander": self.config["commander"],
            "pool_mode": self.gacha.mode, "character_id": c["id"], "name_ko": c["name_ko"],
            "rarity": c["rarity"], "probability": str(probability), "failure_streak": streak,
            "signal_fired": int(fired), "rng_mode": rng_mode, "data_version": self.roster.version,
            "observation_method": "automatic" if automatic else "manual",
        }
        record['number']=self.storage.record_observation(record, signal_id, {"streak": streak, "seconds": self.config["game_entry_seconds"], "mode": self.gacha.mode})
        self.position += 1
        self.pending -= 1
        self.state = State.OBSERVED
        if signal_id:
            self.signal = Signal(self.storage, signal_id, self.config["game_entry_seconds"], self.clock)
        return record

    def advance(self):
        if self.state != State.OBSERVED:
            raise ValueError("확정 관측 후에만 다음 결과로 진행할 수 있습니다.")
        if self.signal:
            self.signal.refresh()
            if self.signal.status == "pending":
                raise ValueError("진행 중인 실제 모집 신호를 먼저 처리하세요.")
        if self.pending:
            self.state = State.WAITING
            self.deadline = Deadline(self.config["observation_seconds"], self.clock)
        else:
            self.state = State.READY
            with self.storage.db:
                self.storage.event("batch_completed", self.batch_id, {"observed": self.position})

    def cancel(self, reason="user_cancelled"):
        if self.signal:
            self.signal.cancel()
        if self.pending:
            with self.storage.db:
                self.storage.event("batch_cancelled", self.batch_id, {"reason": reason, "unobserved": self.pending, "observed": self.position})
        self.pending = 0
        self.state = State.READY
