import json
import math
from pathlib import Path
from .banners import BANNERS

MIN_QRNG_REQUEST_INTERVAL = 1.1
DEFAULTS = {
    "commander": "", "pool_mode": "banner", "rng_mode": "csprng", "qrng_provider": "lfdr",
    "animation_seconds": 1.0, "observation_seconds": 10.0,
    "game_entry_seconds": 30.0, "qrng_timeout_seconds": 5.0,
    "failure_threshold": 200, "qrng_fallback": False,
    "sound": True, "initial_gold_mileage": 0,
    "randomize_animation": True, "animation_min_seconds": 1.0, "animation_max_seconds": 1.1,
    "skip_first_auto_signal": True,
    "signal_rule": "cumulative_target", "target_probability": "0.99", "auto_batch_size": 10,
    "qrng_min_request_interval_seconds": MIN_QRNG_REQUEST_INTERVAL,
    "planned_ten_pulls": 12, "ask_plan_on_start": True,
}


def validate(c):
    if set(c) != set(DEFAULTS):
        raise ValueError("설정 항목이 올바르지 않습니다. config.json을 확인하세요.")
    if not isinstance(c["commander"], str) or len(c["commander"]) > 40:
        raise ValueError("지휘관 이름은 40자 이내로 입력하세요.")
    if any(ord(x) < 32 or ord(x) == 127 for x in c["commander"]):
        raise ValueError("이름에는 제어 문자를 사용할 수 없습니다.")
    for k in ("animation_seconds", "observation_seconds", "game_entry_seconds", "qrng_timeout_seconds", "animation_min_seconds", "animation_max_seconds"):
        v = c[k]
        if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 3600:
            raise ValueError(f"{k}: 0~3600 사이의 유한한 숫자를 입력하세요.")
        if k != "animation_seconds" and v == 0:
            raise ValueError(f"{k}: 0보다 커야 합니다.")
    for k in ("failure_threshold", "initial_gold_mileage"):
        if type(c[k]) is not int or c[k] < (1 if k == "failure_threshold" else 0):
            raise ValueError(f"{k}: 올바른 정수를 입력하세요.")
    for k in ("qrng_fallback", "sound", "randomize_animation", "skip_first_auto_signal", "ask_plan_on_start"):
        if type(c[k]) is not bool:
            raise ValueError(f"{k}: true 또는 false여야 합니다.")
    if c["pool_mode"] not in BANNERS or c["rng_mode"] not in ("csprng", "qrng"):
        raise ValueError("pool_mode / rng_mode 설정을 확인하세요.")
    if c["qrng_provider"] not in ("lfdr", "anu"):
        raise ValueError("qrng_provider는 lfdr 또는 anu여야 합니다.")
    if c['animation_min_seconds'] > c['animation_max_seconds']:
        raise ValueError('애니메이션 최솟값은 최댓값 이하여야 합니다.')
    if c['signal_rule'] not in ('failure_count','cumulative_target','planned_pulls'):
        raise ValueError('신호 방식은 failure_count, cumulative_target, planned_pulls 중 하나입니다.')
    from .strategy import target_decimal
    target_decimal(c['target_probability'])
    if type(c['qrng_min_request_interval_seconds']) not in (int,float) or not math.isfinite(c['qrng_min_request_interval_seconds']) or not MIN_QRNG_REQUEST_INTERVAL<=c['qrng_min_request_interval_seconds']<=3600:
        raise ValueError('QRNG 최소 요청 간격은 1.1~3600초입니다.')
    if c['auto_batch_size'] not in (1,10) or type(c['auto_batch_size']) is not int:
        raise ValueError('자동 모집 단위는 1 또는 10입니다.')
    if type(c['planned_ten_pulls']) is not int or not 0<=c['planned_ten_pulls']<=10000:
        raise ValueError('예정 10연차 횟수는 0~10000 사이 정수입니다.')
    if c['signal_rule']=='planned_pulls' and not c['planned_ten_pulls']:
        raise ValueError('계획 횟수 기준을 사용하려면 10연차 횟수를 입력하세요.')
    return c


def load(path):
    path = Path(path)
    c = dict(DEFAULTS)
    if path.exists():
        c.update(json.loads(path.read_text(encoding="utf-8-sig")))
    interval=c['qrng_min_request_interval_seconds']
    if type(interval) in (int,float) and 1<=interval<MIN_QRNG_REQUEST_INTERVAL:
        c['qrng_min_request_interval_seconds']=MIN_QRNG_REQUEST_INTERVAL
    return validate(c)


def save(path, c):
    validate(c)
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
