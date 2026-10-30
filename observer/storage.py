import json
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from .strategy import wilson_interval


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Storage:
    def __init__(self, path, initial_gold=300):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, started_at TEXT NOT NULL, commander TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(
          number INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
          batch_id TEXT NOT NULL, position INTEGER NOT NULL, started_at TEXT NOT NULL,
          revealed_at TEXT NOT NULL, commander TEXT NOT NULL, pool_mode TEXT NOT NULL,
          character_id TEXT NOT NULL, name_ko TEXT NOT NULL, rarity TEXT NOT NULL,
          probability TEXT NOT NULL, failure_streak INTEGER NOT NULL,
          signal_fired INTEGER NOT NULL, rng_mode TEXT NOT NULL, data_version TEXT NOT NULL,
          observation_method TEXT NOT NULL DEFAULT 'manual',
          UNIQUE(batch_id, position));
        CREATE TABLE IF NOT EXISTS real_results(
          number INTEGER PRIMARY KEY, at TEXT NOT NULL, commander TEXT NOT NULL,
          signal_id TEXT, outcome TEXT NOT NULL CHECK(outcome IN ('pickup','other_ssr','failure')),
          free INTEGER NOT NULL CHECK(free IN (0,1)));
        CREATE TABLE IF NOT EXISTS mileage(
          number INTEGER PRIMARY KEY, at TEXT NOT NULL, delta INTEGER NOT NULL,
          reason TEXT NOT NULL, real_result INTEGER REFERENCES real_results(number));
        CREATE TABLE IF NOT EXISTS real_batches(
          number INTEGER PRIMARY KEY, at TEXT NOT NULL, commander TEXT NOT NULL,
          signal_id TEXT, pickup INTEGER NOT NULL, other_ssr INTEGER NOT NULL,
          sr INTEGER NOT NULL, r INTEGER NOT NULL, free_count INTEGER NOT NULL,
          unknown_low INTEGER NOT NULL DEFAULT 0);
        """)
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(observations)')}
        if 'observation_method' not in columns:
            self.db.execute("ALTER TABLE observations ADD COLUMN observation_method TEXT NOT NULL DEFAULT 'manual'")
        if 'unknown_low' not in {r[1] for r in self.db.execute('PRAGMA table_info(real_batches)')}:
            self.db.execute('ALTER TABLE real_batches ADD COLUMN unknown_low INTEGER NOT NULL DEFAULT 0')
        for table in ("sessions", "events", "observations", "real_results", "real_batches", "mileage"):
            for action in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only log'); END")
        if not self.db.execute("SELECT 1 FROM mileage LIMIT 1").fetchone():
            with self.db:
                self._mileage(initial_gold, "initial_user_setting")

    def close(self):
        self.db.close()

    def event(self, kind, subject=None, payload=None):
        self.db.execute("INSERT INTO events(at,kind,subject,payload) VALUES(?,?,?,?)", (utcnow(), kind, subject, json.dumps(payload or {}, ensure_ascii=False)))

    def begin_session(self, commander):
        sid = str(uuid.uuid4())
        with self.db:
            self.db.execute("INSERT INTO sessions VALUES(?,?,?)", (sid, utcnow(), commander))
        return sid

    def events(self):
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in self.db.execute("SELECT * FROM events ORDER BY id")]

    def signal_statuses(self):
        statuses = {}
        for e in self.events():
            if e["kind"].startswith("signal_"):
                statuses[e["subject"]] = e["kind"][7:]
        return statuses

    def recover(self):
        """Monotonic deadlines cannot survive a reboot; never revive an offer."""
        events = self.events()
        batches = {}
        for e in events:
            if e["kind"].startswith("batch_"):
                batches[e["subject"]] = e["kind"]
        recovered = 0
        with self.db:
            for batch, state in batches.items():
                if state == "batch_started":
                    self.event("batch_interrupted", batch, {"reason": "restart", "unobserved_results": "cancelled_without_rng"})
                    recovered += 1
            for sid, status in self.signal_statuses().items():
                if status == "pending":
                    self.event("signal_expired", sid, {"reason": "restart"})
        return recovered

    def observation_state(self, mode):
        anchor=self.observation_anchor(mode)
        row = self.db.execute("SELECT failure_streak,signal_fired FROM observations WHERE pool_mode=? AND number>? ORDER BY number DESC LIMIT 1", (mode,anchor['after_number'])).fetchone()
        return (row[0], bool(row[1])) if row else (0, False)

    def observation_anchor(self, mode):
        row=self.db.execute("SELECT at,payload FROM events WHERE kind='observation_reanchored' AND subject=? ORDER BY id DESC LIMIT 1",(mode,)).fetchone()
        return (json.loads(row['payload'])|{'at':row['at']}) if row else {'after_number':0,'at':None}

    def reanchor_observation(self, mode):
        number=self.db.execute('SELECT COALESCE(MAX(number),0) FROM observations').fetchone()[0]
        with self.db:
            self.event('observation_reanchored',mode,{'after_number':number,'game_total':self.game_totals()['total']})
        return number

    def game_summary(self):
        singles=self.db.execute("SELECT COUNT(*) AS total,COALESCE(SUM(outcome='pickup'),0) AS pickup FROM real_results").fetchone()
        batches=self.db.execute('SELECT COALESCE(SUM(pickup+other_ssr+sr+r+unknown_low),0) AS total,COALESCE(SUM(pickup),0) AS pickup FROM real_batches').fetchone()
        total=singles['total']+batches['total'];pickup=singles['pickup']+batches['pickup']
        interval=wilson_interval(pickup,total)
        return {'total':total,'pickup':pickup,'percent':100*pickup/total if total else None,
                'interval':tuple(100*x for x in interval) if interval else None}

    def record_observation(self, record, signal_id=None, signal_payload=None):
        fields = list(record)
        with self.db:
            result = self.db.execute(f"INSERT INTO observations({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", list(record.values()))
            if signal_id:
                self.event("signal_pending", signal_id, signal_payload)
        return result.lastrowid

    def history(self, limit=20):
        return [dict(r) for r in self.db.execute("SELECT * FROM observations ORDER BY number DESC LIMIT ?", (limit,))]

    def observation_summary(self, mode, after_number=0):
        row=self.db.execute("""SELECT COUNT(*) AS total,
            COALESCE(SUM(character_id='marian-queen'),0) AS pickup,
            COALESCE(SUM(rarity='SSR' AND character_id!='marian-queen'),0) AS other_ssr,
            COALESCE(SUM(rarity='SR'),0) AS sr,
            COALESCE(SUM(rarity='R'),0) AS r
            FROM observations WHERE pool_mode=? AND number>?""",(mode,after_number)).fetchone()
        result={key:int(row[key]) for key in ('total','pickup','other_ssr','sr','r')}
        result['percentages']={key:100*result[key]/result['total'] if result['total'] else 0.0 for key in ('pickup','other_ssr','sr','r')}
        return result

    def game_totals(self):
        single=self.db.execute('SELECT COUNT(*) AS total,COALESCE(SUM(free),0) AS free FROM real_results').fetchone()
        batches=self.db.execute('SELECT COALESCE(SUM(pickup+other_ssr+sr+r+unknown_low),0) AS total,COALESCE(SUM(free_count),0) AS free FROM real_batches').fetchone()
        total=single['total']+batches['total'];free=single['free']+batches['free']
        return {'total':total,'free':free,'paid':total-free}

    def stats(self, mode):
        rows = self.db.execute("SELECT * FROM observations WHERE pool_mode=? ORDER BY number", (mode,)).fetchall()
        ssr = pickup = current = maximum = 0
        completed = Counter()
        for r in rows:
            ssr += r["rarity"] == "SSR"
            if r["failure_streak"] == 0:
                pickup += 1
                completed[current] += 1
            current = r["failure_streak"]
            maximum = max(maximum, current)
        real = self.db.execute("SELECT * FROM real_results ORDER BY number").fetchall()
        events = self.events()
        batches = self.db.execute("SELECT * FROM real_batches ORDER BY number").fetchall()
        recorded_ids = {e['payload']['number'] for e in events if e['kind']=='real_result_recorded'}
        minimum = maximum_real = 0
        # Older versions' individual records precede new event-ordered entries.
        for r in real:
            if r['number'] not in recorded_ids:
                minimum = 0 if r['outcome']=='pickup' else minimum+1
                maximum_real = 0 if r['outcome']=='pickup' else maximum_real+1
        for e in events:
            if e['kind']=='real_result_recorded':
                minimum = 0 if e['payload']['outcome']=='pickup' else minimum+1
                maximum_real = 0 if e['payload']['outcome']=='pickup' else maximum_real+1
            elif e['kind']=='real_batch_recorded':
                counts=e['payload']['counts'];failures=sum(counts.values())-counts['pickup']
                if counts['pickup']:
                    minimum,maximum_real=0,failures
                else:
                    minimum+=failures;maximum_real+=failures
        actual_total = len(real)+sum(b['pickup']+b['other_ssr']+b['sr']+b['r']+b['unknown_low'] for b in batches)
        current,_=self.observation_state(mode)
        actual_pickup = sum(r['outcome']=='pickup' for r in real)+sum(b['pickup'] for b in batches)
        interval=wilson_interval(actual_pickup,actual_total)
        return {
            "총 관측": len(rows), "SSR 관측": ssr, "픽업 관측": pickup,
            "현재 관측 연속 실패": current, "최대 관측 연속 실패": maximum,
            "완료된 실패 구간 분포": dict(sorted(completed.items())),
            "진행 중 실패 구간": current,
            "신호 발생(전체 모드)": sum(e["kind"] == "signal_pending" for e in events),
            "실제 게임 진입 확인": sum(e["kind"] == "signal_started" for e in events),
            "실제 모집 기록": actual_total, "실제 픽업 성공": actual_pickup,
            "실제 픽업 비율(%)": round(100*actual_pickup/actual_total,4) if actual_total else None,
            "실제 픽업 비율 Wilson 95% 구간(%, 참고)": [round(100*x,4) for x in interval] if interval else None,
            "실제 기타 SSR": sum(r['outcome']=='other_ssr' for r in real)+sum(b['other_ssr'] for b in batches),
            "실제 SR(레어)": sum(b['sr'] for b in batches), "실제 R(커먼)": sum(b['r'] for b in batches),
            "실제 SR/R 미구분 실패": sum(r['outcome']=='failure' for r in real)+sum(b['unknown_low'] for b in batches),
            "실제 연속 실패": minimum if minimum==maximum_real else f"{minimum}~{maximum_real} (묶음 내 순서 미입력)",
            "골드 마일리지": self.gold,
        }

    @property
    def gold(self):
        return self.db.execute("SELECT COALESCE(SUM(delta),0) FROM mileage").fetchone()[0]

    def _mileage(self, delta, reason, real_result=None):
        self.db.execute("INSERT INTO mileage(at,delta,reason,real_result) VALUES(?,?,?,?)", (utcnow(), delta, reason, real_result))

    def set_gold(self, value):
        if type(value) is not int or value < 0:
            raise ValueError("마일리지는 0 이상의 정수여야 합니다.")
        with self.db:
            self._mileage(value - self.gold, "manual_balance_correction")

    def exchange(self):
        if self.gold < 200:
            raise ValueError("200장이 필요합니다.")
        with self.db:
            self._mileage(-200, "manual_marian_exchange")
            self.event("real_exchange", payload={"character": "마리안 : 퀸", "tickets": 200})

    def record_real(self, commander, outcome, free=False, signal_id=None):
        if outcome not in ("pickup", "other_ssr", "failure"):
            raise ValueError("실제 결과를 직접 선택하세요.")
        if signal_id and self.signal_statuses().get(signal_id) != "started":
            raise ValueError("시작 확인된 신호가 아닙니다.")
        with self.db:
            r = self.db.execute("INSERT INTO real_results(at,commander,signal_id,outcome,free) VALUES(?,?,?,?,?)", (utcnow(), commander, signal_id, outcome, int(free)))
            if not free:
                self._mileage(1, "recorded_paid_special_recruit", r.lastrowid)
            self.event('real_result_recorded', signal_id, {'number':r.lastrowid,'outcome':outcome,'free':free})
        return r.lastrowid

    def record_real_batch(self, commander, counts, free_count=0, signal_id=None):
        if set(counts) not in ({'pickup','other_ssr','sr','r'},{'pickup','other_ssr','low'},{'pickup','other_ssr','sr','r','low'}):
            raise ValueError('결과 항목을 확인하세요.')
        counts={'sr':0,'r':0,'low':0}|counts
        if any(type(v) is not int or v<0 for v in counts.values()):
            raise ValueError('픽업·기타 SSR·SR·R 수량은 0 이상의 정수로 입력하세요.')
        total=sum(counts.values())
        if not 1<=total<=10000 or type(free_count) is not int or not 0<=free_count<=total:
            raise ValueError('총 모집 1~10000회, 무료 횟수는 총 횟수 이하여야 합니다.')
        if signal_id and self.signal_statuses().get(signal_id)!='started':
            raise ValueError('시작 확인된 신호가 아닙니다.')
        with self.db:
            result=self.db.execute('INSERT INTO real_batches(at,commander,signal_id,pickup,other_ssr,sr,r,free_count,unknown_low) VALUES(?,?,?,?,?,?,?,?,?)',
                (utcnow(),commander,signal_id,counts['pickup'],counts['other_ssr'],counts['sr'],counts['r'],free_count,counts['low']))
            self._mileage(total-free_count, f'recorded_real_batch:{result.lastrowid}')
            self.event('real_batch_recorded',signal_id,{'number':result.lastrowid,'counts':counts,'free_count':free_count,'within_batch_order':'not_supplied'})
        return result.lastrowid

    def export(self, path):
        result = {"observations": [dict(r) for r in self.db.execute("SELECT * FROM observations ORDER BY number")],
                  "real_results": [dict(r) for r in self.db.execute("SELECT * FROM real_results ORDER BY number")],
                  "real_batches": [dict(r) for r in self.db.execute("SELECT * FROM real_batches ORDER BY number")],
                  "mileage": [dict(r) for r in self.db.execute("SELECT * FROM mileage ORDER BY number")],
                  "sessions": [dict(r) for r in self.db.execute("SELECT * FROM sessions")], "events": self.events()}
        Path(path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
