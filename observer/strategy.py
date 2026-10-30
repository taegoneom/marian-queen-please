"""Planning probabilities and descriptive account statistics, never draw weights."""
import math
from decimal import Decimal, InvalidOperation, localcontext, ROUND_CEILING
from functools import lru_cache


def target_decimal(value):
    try:
        if type(value) not in (int,float,str):raise ValueError
        target=Decimal(str(value))
        if not target.is_finite() or not 0<target<1:raise ValueError
        if len(str(value))>70:raise ValueError
        return target
    except (ValueError,InvalidOperation):
        raise ValueError('목표 누적 비율은 0 초과 1 미만인 숫자입니다.') from None


def cumulative_percent(n):
    with localcontext() as ctx:
        ctx.prec=80
        return format((1-Decimal('0.99')**n)*100,'.15f')


def target_percent(config):
    with localcontext() as ctx:
        ctx.prec=80
        return format(target_decimal(config['target_probability'])*100,'f')


def at_least_one(n, p=.01):
    if n < 0 or not 0 < p < 1:
        raise ValueError('확률 계산 범위 오류')
    return -math.expm1(n * math.log1p(-p))


def binomial_summary(n,p=.01):
    if type(n) is not int or n<0 or not 0<p<1:raise ValueError('시행 횟수 범위 오류')
    zero=math.exp(n*math.log1p(-p))
    one=zero*n*p/(1-p) if n else 0.0
    two=one*(n-1)*p/(2*(1-p)) if n>=2 else 0.0
    return {'zero':zero,'at_least_one':max(0.0,1-zero),
            'at_least_two':max(0.0,1-zero-one),'at_least_three':max(0.0,1-zero-one-two)}


def required_draws(target, batch_size=10, p=.01):
    target=target_decimal(target)
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError('목표 비율은 0과 1 사이여야 합니다.')
    return _required_cached(str(target),batch_size,str(p))


@lru_cache(maxsize=128)
def _required_cached(target_text,batch_size,p_text):
    with localcontext() as ctx:
        ctx.prec=80
        target=Decimal(target_text)
        q=1-Decimal(p_text)
        groups=int(((1-target).ln()/q.ln()/batch_size).to_integral_value(rounding=ROUND_CEILING))
        while 1-q**(groups*batch_size)<target:groups+=1
        return groups*batch_size


def signal_threshold(config):
    if config['signal_rule']=='planned_pulls':return config['planned_ten_pulls']*10
    if config['signal_rule']=='cumulative_target':
        return required_draws(config['target_probability'],config['auto_batch_size'])
    return config['failure_threshold']


def wilson_interval(successes, trials, z=1.959963984540054):
    if trials==0:return None
    if not 0<=successes<=trials:raise ValueError('성공 횟수 범위 오류')
    phat=successes/trials
    divisor=1+z*z/trials
    center=(phat+z*z/(2*trials))/divisor
    half=z*math.sqrt(phat*(1-phat)/trials+z*z/(4*trials*trials))/divisor
    return max(0,center-half),min(1,center+half)
