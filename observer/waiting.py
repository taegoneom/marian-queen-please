"""First run-of-misses timing references; network and UI time excluded."""
import math
from fractions import Fraction
from functools import lru_cache


@lru_cache(maxsize=32)
def survival_table(run_length, no_pickup, batches):
    if run_length>batches:return (1.0,)*(batches+1)
    states=[1.0]+[0.0]*(run_length-1)
    survival=[1.0]
    for _ in range(batches):
        states=[(1-no_pickup)*sum(states)]+[no_pickup*x for x in states[:-1]]
        survival.append(sum(states))
    return tuple(survival)


def uniform_sum_cdf(n,x):
    if x<=0:return 0.0
    if x>=n:return 1.0
    if x>Fraction(n,2):return 1-uniform_sum_cdf(n,Fraction(n)-x)
    a,b=x.numerator,x.denominator
    numerator=sum((-1)**k*math.comb(n,k)*(a-b*k)**n for k in range(a//b+1))
    return float(Fraction(numerator,b**n*math.factorial(n)))


@lru_cache(maxsize=32)
def reference(threshold=460,batch_size=10,minimum=1.0,maximum=1.1):
    if threshold%batch_size or not 0<minimum<=maximum:return None
    run=threshold//batch_size
    no_pickup=.99**batch_size
    lo,hi=Fraction(str(minimum)),Fraction(str(maximum))
    fast=int(Fraction(300)/lo)
    slow=int(Fraction(300)/hi)
    if fast>10000:return None
    survival=survival_table(run,no_pickup,fast)
    cdf=1-survival[slow]
    if hi>lo:
        for n in range(slow+1,fast+1):
            completion=uniform_sum_cdf(n,(Fraction(300)-n*lo)/(hi-lo))
            cdf+=(survival[n-1]-survival[n])*completion
    try:expected=math.expm1(-run*math.log(no_pickup))/(1-no_pickup)
    except OverflowError:expected=math.inf
    return {'first_batches':run,'first_chance':no_pickup**run,
            'earliest_min_seconds':run*minimum,'earliest_max_seconds':run*maximum,
            'over_five_minutes':max(0.0,min(1.0,1-cdf)),
            'mean_seconds':expected*(minimum+maximum)/2}
