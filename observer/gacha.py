from bisect import bisect_right
from fractions import Fraction
from math import lcm


class Gacha:
    """Exact integer weights, immutable between independent observations."""
    def __init__(self, roster, mode="banner"):
        if mode!='banner':raise ValueError('마리안 : 퀸 모집만 지원합니다.')
        self.mode = mode
        self.pickup_id = roster.banner["pickup_id"]
        pool = roster.pool(mode)
        pickup = [c for c in pool if c["id"] == self.pickup_id]
        other = [c for c in pool if c["rarity"] == "SSR" and c["id"] != self.pickup_id]
        low = [c for c in pool if c["rarity"] in ("SR", "R")]
        if len(pickup) != 1 or not other or not low:
            raise ValueError("확률 풀 구성이 올바르지 않습니다.")
        sr=[c for c in low if c['rarity']=='SR']
        common=[c for c in low if c['rarity']=='R']
        groups=[(pickup,1),(other,3),(sr,43),(common,53)]
        if any(not group for group,_ in groups):raise ValueError('등급별 모집 대상 누락')
        unit=lcm(*(len(group) for group,_ in groups))
        self.characters=[c for group,_ in groups for c in group]
        self.weights=[mass*unit//len(group) for group,mass in groups for _ in group]
        self.total = 100 * unit
        self.cumulative = []
        acc = 0
        for w in self.weights:
            acc += w
            self.cumulative.append(acc)
        assert acc == self.total
        self.probabilities = {c["id"]: Fraction(w, self.total) for c, w in zip(self.characters, self.weights)}

    def select(self, ticket):
        if type(ticket) is not int or not 0 <= ticket < self.total:
            raise ValueError("난수 범위 오류")
        return self.characters[bisect_right(self.cumulative, ticket)]

    def draw(self, rng):
        ticket = rng.randbelow(self.total)
        c = self.select(ticket)
        return c, self.probabilities[c["id"]], rng.last_mode
