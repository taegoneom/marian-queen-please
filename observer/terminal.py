import json
import os
import sys
import time
from contextlib import nullcontext
from datetime import datetime,timezone,timedelta
from fractions import Fraction
from . import config as settings
from .gacha import Gacha
from .observation import Observation, State
from .rng import build, RNGError
from .timers import Deadline
from .strategy import at_least_one,signal_threshold,cumulative_percent,target_percent
from .reports import parse_reports


class Keyboard:
    def __enter__(self):
        self.windows = os.name == "nt"
        if self.windows:
            import msvcrt
            self.native = msvcrt
        else:
            import termios
            import tty
            self.termios = termios
            self.old = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        return self

    def __exit__(self, *_):
        if not self.windows:
            self.termios.tcsetattr(sys.stdin, self.termios.TCSADRAIN, self.old)

    def read(self):
        if self.windows:
            if not self.native.kbhit():
                return None
            c = self.native.getwch()
            if c in ("\x00", "\xe0"):
                if self.native.kbhit():
                    self.native.getwch()
                return None
            if c == "\x03":
                raise KeyboardInterrupt
            return "\n" if c == "\r" else c.lower()
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1).lower()
        return None

    def flush(self):
        while self.read() is not None:
            pass

    def wait(self):
        while True:
            key = self.read()
            if key is not None:
                return key
            time.sleep(0.03)

    def line(self, prompt):
        self.flush()
        if self.windows:
            return input(prompt).strip()
        self.termios.tcsetattr(sys.stdin, self.termios.TCSADRAIN, self.old)
        try:
            return input(prompt).strip()
        finally:
            import tty
            tty.setcbreak(sys.stdin.fileno())


def enable_color():
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    import ctypes
    handle = ctypes.windll.kernel32.GetStdHandle(-11)
    mode = ctypes.c_ulong()
    if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return bool(ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 4))
    return False


class Terminal:
    def __init__(self, roster, storage, config, config_path):
        self.roster, self.storage = roster, storage
        self.config, self.config_path = config, config_path
        self.color = enable_color()
        self.engine = None

    def ink(self, text, code="36"):
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def beep(self,repeats=1):
        if self.config["sound"]:
            for index in range(repeats):
                if os.name == "nt":
                    import winsound
                    try:winsound.Beep(1000,140)
                    except RuntimeError:winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                else:
                    print("\a", end="", flush=True)
                if index+1<repeats:time.sleep(.15)

    def new_engine(self):
        session = self.storage.begin_session(self.config["commander"])
        self.engine = Observation(self.storage, self.roster, Gacha(self.roster, self.config["pool_mode"]), build(self.config), self.config, session)

    def clear_screen(self):
        if sys.stdout.isatty():
            if self.color:print('\033[2J\033[H',end='',flush=True)
            elif os.name=='nt':os.system('cls')
            else:print('\033[2J\033[H',end='',flush=True)

    def header(self,ready=True):
        s = self.storage.stats(self.config["pool_mode"])
        print(self.ink("\n╔══════════════ NIKKE · OBSERVATION ══════════════╗"))
        print(f"  COMMANDER  {self.config['commander']} 지휘관")
        print("  SPECIAL RECRUIT    마리안 : 퀸  /  PICK UP 1.0000%")
        if self.config['pool_mode']=='banner':
            print('  기타 SSR 0.023810% ×126 · SR 4.777778% ×9 · R 5.888889% ×9')
        source = {'anu': 'ANU', 'lfdr': 'LfD / Quantis · 무료 공개 API'}[self.config['qrng_provider']]
        print(f"  난수원  {source + ' QRNG · 관측 시 요청' if self.config['rng_mode']=='qrng' else 'OS CSPRNG'}")
        print(f"  {self.roster.label(self.config['pool_mode'])}")
        streak=s['현재 관측 연속 실패']
        print(f"  연속 미등장 {streak} / {signal_threshold(self.config)}  ·  총 관측 {s['총 관측 관측']}회")
        print(f"  누적 관측 기준 {cumulative_percent(streak)}%")
        if self.config['signal_rule']=='cumulative_target':print(f"  마리안 : 퀸 알림 목표 {target_percent(self.config)}%")
        print(f"  GOLD MILEAGE {self.storage.gold}  ·  200장 교환은 별도 수동 기록")
        print(self.ink("╚════════════════════════════════════════════════╝"))
        print("[SPACE] 1회  [B] 10연차  [ENTER] 관측  [S] 통계  [L] 이력")
        print("[A] 자동 관측  [P] 확률표  [C] 설정  [R] 실제 결과  [M] 마일리지  [Q] 종료")
        if ready:print("준비 완료 · 지휘관님의 입력을 기다립니다.", flush=True)

    def auto_dashboard(self,status):
        self.clear_screen()
        self.header(ready=False)
        count=self.storage.db.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
        print(self.ink(f'\n전체 관측  {count:09,d} 회   |   10연차 {count//10:07,d} 묶음','97'))
        print('[A/P] 일시정지  [R] 게임 결과 기록  [Q] 종료')
        print(status)
        print('─'*60)
        print('최근 모집 결과')
        for line in getattr(self,'dashboard_results',[]):print(line)
        print('─'*60,flush=True)

    def pause(self):
        self.kb.flush()
        print("\n아무 키나 누르면 돌아갑니다.", flush=True)
        self.kb.wait()
        self.kb.flush()

    def real_results(self, signal_id=None):
        print(self.ink("\n실제 니케 모집 · 직접 확인한 결과만 기록", "35"))
        if signal_id is None:
            pending = [sid for sid, state in self.storage.signal_statuses().items() if state == "started"]
            if pending:
                print("시작 확인 후 기록을 마치지 않은 신호:")
                for i, sid in enumerate(pending, 1):
                    count = self.storage.db.execute("SELECT COUNT(*) FROM real_results WHERE signal_id=?", (sid,)).fetchone()[0]
                    print(f"  {i}: {sid[:8]} · 현재 기록 {count}회")
                text = self.kb.line("신호 번호 / M: 신호와 별도 기록 / Enter: 돌아가기 > ")
                if not text:
                    return
                if text.lower() != "m":
                    try:
                        n = int(text)
                        if not 1 <= n <= len(pending):
                            raise ValueError
                        signal_id = pending[n - 1]
                    except ValueError:
                        print("번호를 확인하세요.")
                        return
        print("[1] 픽업 성공  [2] 기타 SSR  [3] 픽업 실패(SR/R)")
        print("[T] 빠른 입력: 0 0 10 / 1 0 9 등  [B] 항목별 수량 입력")
        print("[F] 다음 1회 무료 여부 전환  [X] 기록 완료  [Q] 나중에 기록")
        free = False
        self.kb.flush()
        while True:
            print(f"다음 실제 1회: {'무료 · 마일리지 +0' if free else '쥬얼/고급티켓 · 마일리지 +1'}", flush=True)
            key = self.kb.wait()
            if key == "f":
                free = not free
            elif key=='t':
                self.quick_report(signal_id)
            elif key == 'b':
                try:
                    labels={'pickup':'픽업 마리안 : 퀸','other_ssr':'기타 SSR','sr':'SR(레어)','r':'R(커먼)'}
                    counts={k:int(self.kb.line(f'{label} 획득 수 [빈칸=0] > ') or '0') for k,label in labels.items()}
                    free_count=int(self.kb.line('이 모집 중 무료 모집 횟수 [빈칸=0] > ') or '0')
                    print(f"실제 모집 {sum(counts.values())}회: {counts} · 무료 {free_count}회")
                    print('[ENTER] 이 수량으로 기록 / 다른 키: 취소',flush=True)
                    self.kb.flush()
                    if self.kb.wait()=='\n':
                        number=self.storage.record_real_batch(self.config['commander'],counts,free_count,signal_id)
                        print(f'실제 묶음 #{number} 저장 · 골드 마일리지 {self.storage.gold}')
                except ValueError as exc:
                    print(f'저장하지 않았습니다: {exc}')
                self.kb.flush()
            elif key in ("1", "2", "3"):
                outcome = {"1": "pickup", "2": "other_ssr", "3": "failure"}[key]
                number = self.storage.record_real(self.config["commander"], outcome, free, signal_id)
                print(f"실제 결과 #{number} 저장 · 골드 마일리지 {self.storage.gold}")
                free = False
                self.kb.flush()
            elif key == "x":
                if signal_id:
                    with self.storage.db:
                        self.storage.event("signal_completed", signal_id)
                return
            elif key == "q":
                with self.storage.db:
                    self.storage.event("real_recording_deferred", signal_id)
                return

    def quick_report(self,signal_id=None):
        print('\n게임 모집 결과 입력 · 입력 중에는 관측을 기다립니다.')
        print('픽업 / 기타 SSR / 나머지 순서: 0 0 10, 1 0 9, 0 1 9')
        print('SR/R 구분은 숫자 4개: 픽업 기타SSR SR R. 여러 10연차는 /로 구분.')
        while True:
            text=self.kb.line('결과 > [빈칸: 나중에 기록] ')
            if not text:
                with self.storage.db:self.storage.event('real_recording_deferred',signal_id)
                return
            try:
                groups=parse_reports(text)
                free_text=self.kb.line(f'무료 모집 횟수 {len(groups)}개 [빈칸: 모두 0] > ')
                frees=list(map(int,free_text.split())) if free_text else [0]*len(groups)
                if len(frees)!=len(groups) or any(not 0<=x<=10 for x in frees):
                    raise ValueError('각 10연차의 무료 횟수를 순서대로 입력하세요. 기본값은 0입니다.')
                for counts,free_count in zip(groups,frees):
                    number=self.storage.record_real_batch(self.config['commander'],counts,free_count,signal_id)
                    print(f'게임 결과 #{number} 저장: {counts} · 무료 {free_count}회')
                if signal_id:
                    with self.storage.db:self.storage.event('signal_completed',signal_id)
                print(f'골드 마일리지 {self.storage.gold}장 · 다음 관측으로 진행합니다.',flush=True)
                return
            except ValueError as exc:
                print(f'입력을 확인하세요: {exc}')

    def handle_signal(self, automatic=False):
        signal = self.engine.signal
        if not signal or signal.refresh() != "pending":
            return
        streak, _ = self.storage.observation_state(self.config["pool_mode"])
        self.beep(2)
        ordinal = sum(e['kind']=='signal_pending' for e in self.storage.events())
        print(self.ink(f"\n[관측 신호 #{ordinal} 발생] {self.config['commander']} 지휘관님", "93"))
        print(f"마리안 : 퀸 {streak}회 연속 미등장 · 누적 관측 기준 {cumulative_percent(streak)}%")
        print(f"지금부터 {self.config['game_entry_seconds']:g}초 안에 폰에서 10명 모집 버튼을 누르고 개봉하세요.")
        print("[D / ENTER] 뽑았어요  [N] 안 뽑았어요 · 다음 관측  [Q] 중지", flush=True)
        self.kb.flush()
        last=None
        quit_requested=False
        while signal.refresh() == "pending":
            remaining=signal.deadline.display()
            if remaining!=last:
                print(f"\r남은 시간: {remaining}   ", end="", flush=True)
                last=remaining
            key = self.kb.read()
            if key in ('d',"\n"):
                if signal.confirm():
                    print("\n모집 실행 확인을 저장했습니다.")
                    self.quick_report(signal.id)
                    break
            elif key in ("n", "x", "q"):
                signal.cancel()
                quit_requested=key=='q'
                break
            time.sleep(0.05)
        if signal.status == "expired":
            print("\n[신호 만료] 제한 시간이 지났습니다. 이 신호는 더 이상 유효하지 않습니다.")
            self.beep()
        elif signal.status == "cancelled":
            print("\n[신호 취소] 직접 취소했습니다.")
        self.kb.flush()
        return quit_requested

    def draw(self, count):
        e = self.engine
        e.start(count)
        self.kb.flush()
        print(self.ink(f"\n{self.config['commander']} 지휘관 · {count}회 모집", "36"))
        while not e.animation.expired:
            duration = e.animation_seconds
            progress = 1 - e.animation.remaining / duration
            bars = min(20, int(progress * 20))
            print(f"\r모집 진행 중... {'█'*bars}{'░'*(20-bars)} {progress*100:5.1f}%  [Q] 취소", end="", flush=True)
            if self.kb.read() == "q":
                e.cancel()
                print("\n모집 취소 · 결과를 생성하지 않았습니다.")
                return
            time.sleep(0.05)
        print("\r모집 진행 중... ████████████████████ 100%                         ")
        self.kb.flush()
        e.prepare_observation()
        while e.state == State.WAITING:
            print(f"\n[{e.position+1:02}/{count:02}] 미관측 · ENTER로 결과를 확정하세요. [Q] 남은 모집 취소", flush=True)
            warned = False
            while e.state == State.WAITING:
                if e.deadline.expired:
                    e.cancel("observation_timeout")
                    print("\n관측 시간 초과 · 남은 시도 취소 · 미관측 결과/실패 증가 없음")
                    break
                print(f"\r관측 대기: {e.deadline.display()}   ", end="", flush=True)
                if e.deadline.remaining <= 3 and not warned:
                    self.beep()
                    warned = True
                key = self.kb.read()
                if key == "q":
                    e.cancel()
                    print("\n남은 관측을 취소했습니다.")
                    break
                if key == "\n":
                    print("\n관측 요청 · " + (f"{self.config['qrng_provider'].upper()} 양자 난수 수신 중..." if self.config["rng_mode"] == "qrng" else "결과 확정 중..."), flush=True)
                    try:
                        result = e.observe()
                    except RNGError as exc:
                        e.cancel("rng_error")
                        print(f"관측 취소: {exc} · 결과/실패 증가 없음")
                        break
                    if result is None:
                        print("관측 시간 초과 · 결과 없음")
                        break
                    if e.rng.last_warning:
                        print(self.ink(e.rng.last_warning, "93"))
                    color = {"SSR": "93", "SR": "95", "R": "94"}[result["rarity"]]
                    title = "★ PICK UP ★" if result["character_id"] == e.gacha.pickup_id else "관측 결과"
                    print(self.ink(f"\n  {title}  {result['rarity']} — {result['name_ko']}", color))
                    p = Fraction(result["probability"])
                    print(f"  개별 확률 {float(p)*100:.6f}% · {result['rng_mode']}")
                    print(f"  연속 미등장: {result['failure_streak']}회 · {e.position}/{count} 저장 완료", flush=True)
                    self.handle_signal()
                    self.kb.flush()
                    if e.pending:
                        print("[N] 다음 관측 준비  [Q] 남은 모집 취소", flush=True)
                        while True:
                            next_key = self.kb.wait()
                            if next_key == "q":
                                e.cancel()
                                break
                            if next_key == "n":
                                self.kb.flush()
                                e.advance()
                                break
                    else:
                        e.advance()
                    break
                time.sleep(0.05)

    def stats(self):
        print("\n" + self.roster.label(self.config["pool_mode"]))
        s = self.storage.stats(self.config["pool_mode"])
        print(json.dumps(s, ensure_ascii=False, indent=2))
        if s["실제 모집 기록"]:
            print(f"실제 기록상 픽업 비율: {100*s['실제 픽업 성공']/s['실제 모집 기록']:.3f}% · {s['실제 모집 기록']}회 표본 · 인과 효과 판정 아님")
        print("무당첨 이론값: 120회 29.9380% / 200회 13.3980%")
        self.pause()

    def history(self):
        for r in reversed(self.storage.history(30)):
            local=datetime.fromisoformat(r['revealed_at']).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S KST')
            print(f"#{r['number']:5} {local}  {r['commander']}  {r['rarity']} {r['name_ko']} · 미등장 {r['failure_streak']} · {r['rng_mode']}")
        events = self.storage.events()[-10:]
        print("최근 상태 기록:")
        for event in events:
            local=datetime.fromisoformat(event['at']).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S KST')
            print(f"  {local} {event['kind']} {event['payload']}")
        self.pause()

    def probabilities(self):
        g = self.engine.gacha
        print("\n" + self.roster.label(g.mode))
        if g.mode == "banner":
            print(self.roster.banner["warning_ko"])
        print('픽업 1% · 기타 SSR 합계 3% · SR 합계 43% · R 합계 53%.' if g.mode=='banner' else '전체 출시 SR/R 29명에 96%를 균등 분배합니다.')
        for i, c in enumerate(g.characters):
            p = g.probabilities[c["id"]]
            status = {True: "공식 모집 포함", False: "관측 전용 대원", None: "모집 포함 미확인"}[c["banner_included"]]
            print(f"{c['rarity']:3} {c['name_ko']} / {c['name_en']}  {float(p)*100:.6f}% · {status}")
            if (i + 1) % 20 == 0 and i + 1 < len(g.characters):
                print("[아무 키] 다음 페이지 / [Q] 돌아가기", flush=True)
                self.kb.flush()
                if self.kb.wait() == "q":
                    return
        self.pause()

    def configure(self):
        labels = {
            "commander": "지휘관 이름", "pool_mode": "모집 풀 banner / all", "rng_mode": "난수 csprng / qrng",
            "qrng_provider": "양자 난수 제공자 lfdr(무료·키 불필요) / anu(API 키 필요)",
            "animation_seconds": "애니메이션 초", "observation_seconds": "관측 제한 초",
            "game_entry_seconds": "실제 진입 제한 초", "qrng_timeout_seconds": "QRNG 제한 초",
            "failure_threshold": "미등장 기준 횟수(failure_count 방식)", "qrng_fallback": "QRNG 실패 시 CSPRNG 전환 true / false",
            "sound": "경고음 true / false", "initial_gold_mileage": "새 DB 초기 마일리지(기존 잔액은 M에서 수정)",
            "randomize_animation": "모집 시간을 무작위로 선택 true / false",
            "animation_min_seconds": "무작위 모집 시간 최소 초", "animation_max_seconds": "무작위 모집 시간 최대 초",
            "skip_first_auto_signal": "자동 관측 첫 신호를 연습으로 처리 true / false",
            "signal_rule": "신호 방식 cumulative_target / failure_count",
            "target_probability": "목표 누적 비율(99.9999999999999% = 0.999999999999999)", "auto_batch_size": "자동 모집 인원 1 / 10",
            "qrng_min_request_interval_seconds": "QRNG 최소 요청 간격 초(1초 이상)",
        }
        keys = list(labels)
        for n, key in enumerate(keys, 1):
            print(f"{n:2}. {labels[key]} = {self.config[key]}")
        text = self.kb.line("변경할 번호 / Enter: 돌아가기 > ")
        if not text:
            return
        try:
            index = int(text)
            if not 1 <= index <= len(keys):
                raise ValueError("항목 번호를 확인하세요.")
            key = keys[index - 1]
            text = self.kb.line(f"{labels[key]} 새 값 > ")
            value = json.loads(text) if type(settings.DEFAULTS[key]) is not str else text
            proposed = self.config | {key: value}
            settings.validate(proposed)
            if not proposed["commander"].strip():
                raise ValueError("지휘관 이름을 입력하세요.")
            settings.save(self.config_path, proposed)
            self.config = proposed
            self.new_engine()
            print("설정을 저장했습니다.")
            if key == "rng_mode" and value == "qrng":
                print("LfD는 키 없이 사용 가능합니다. ANU 선택 시 ANU_QRNG_API_KEY가 필요합니다.")
        except (ValueError, TypeError) as exc:
            print(f"설정 변경 없음: {exc}")

    def mileage(self):
        print(f"\n보유 골드 마일리지: {self.storage.gold}장")
        print("[M] 현재 보유량 수동 수정  [E] 실제로 완료한 200장 교환 기록  [Q] 돌아가기", flush=True)
        self.kb.flush()
        key = self.kb.wait()
        try:
            if key == "m":
                value = int(self.kb.line("실제 게임에서 확인한 현재 보유량 > "))
                self.storage.set_gold(value)
            elif key == "e":
                text = self.kb.line("게임에서 마리안 : 퀸 교환을 완료했다면 '교환완료' 입력 > ")
                if text == "교환완료":
                    self.storage.exchange()
                    print("실제 교환을 기록했습니다. 픽업 당첨 통계에는 합산하지 않습니다.")
        except ValueError as exc:
            print(f"변경 없음: {exc}")

    def automatic(self):
        e=self.engine
        print(self.ink('\n자동 관측 시작 · 결과를 순서대로 확정하여 화면과 로그에 공개합니다.','35'))
        print('[A/P] 자동 관측 일시정지  [R] 잠시 멈추고 실제 결과 기록  [Q] 종료',flush=True)
        self.kb.flush()
        while True:
            key=self.kb.read()
            if key in ('a','p','q','r'):
                if key=='r':self.real_results()
                return key=='q'
            count=self.config['auto_batch_size']
            e.start(count)
            self.auto_dashboard(f'{count}명 모집 · 준비 시간 {e.animation_seconds:.6f}초')
            last=None
            while not e.animation.expired:
                remaining=e.animation.display()
                if remaining!=last:
                    self.auto_dashboard(f'모집 준비 · 남은 시간 {remaining} · 설정 {e.animation_seconds:.6f}초')
                    last=remaining
                key=self.kb.read()
                if key in ('a','p','q','r'):
                    e.cancel('auto_paused')
                    print('\n자동 관측을 멈췄습니다.')
                    if key=='r':self.real_results()
                    return key=='q'
                time.sleep(.02)
            e.prepare_observation()
            self.auto_dashboard('결과 관측 중 · 난수 응답을 기다립니다.')
            self.dashboard_results=[]
            batch_context=e.rng.batch() if hasattr(e.rng,'batch') else nullcontext()
            with batch_context:
                while e.pending:
                    key=self.kb.read()
                    if key in ('q','p','a'):
                        e.cancel('auto_paused')
                        return key=='q'
                    try:
                        result=e.observe(automatic=True,defer_signal=e.pending>1)
                    except RNGError as exc:
                        e.cancel('rng_error')
                        print(f'\nQRNG 관측 취소: {exc} · 자동 관측을 멈췄습니다.',flush=True)
                        return False
                    if result is None:return False
                    if e.rng.last_warning:print('\n'+e.rng.last_warning)
                    color={'SSR':'93','SR':'95','R':'94'}[result['rarity']]
                    self.dashboard_results.append(self.ink(f"#{result['number']:07} [{e.position:02}/{count:02}] {result['rarity']} — {result['name_ko']} ({float(Fraction(result['probability']))*100:.6f}%)",color))
                    if e.pending==0:
                        self.auto_dashboard(f"관측 완료 · 마리안 : 퀸 연속 미등장 {result['failure_streak']} / {signal_threshold(self.config)}")
                    quit_requested=False
                    if e.signal and e.signal.refresh()=='pending':
                        rehearsed=any(x['kind']=='auto_rehearsal_done' for x in self.storage.events())
                        if self.config['skip_first_auto_signal'] and not rehearsed:
                            e.signal.rehearse()
                            with self.storage.db:self.storage.event('auto_rehearsal_done',e.signal.id)
                            self.beep()
                            print(self.ink('[첫 알림 · 연습] 다음 출격 신호부터 폰에서 모집하시면 됩니다.','93'),flush=True)
                        else:
                            quit_requested=bool(self.handle_signal(automatic=True))
                    e.advance()
                    if quit_requested:return True

    def run(self, auto_start=False):
        if not sys.stdin.isatty():
            print("키 입력이 가능한 PowerShell / Windows Terminal에서 python main.py를 실행하세요.")
            return 2
        with Keyboard() as self.kb:
            if not self.config["commander"].strip():
                while True:
                    name = self.kb.line("지휘관 이름을 입력하세요 > ")
                    try:
                        proposed = self.config | {"commander": name}
                        settings.validate(proposed)
                        if not name:
                            raise ValueError("이름을 입력하세요.")
                        self.config = proposed
                        settings.save(self.config_path, self.config)
                        break
                    except ValueError as exc:
                        print(exc)
            self.new_engine()
            print("NIKKE 관측 모집 · 대한민국 표준시 KST")
            try:
                self.header()
                if auto_start:
                    if self.automatic():return 0
                    self.header()
                while True:
                    key = self.kb.wait()
                    if key == "q":
                        break
                    if key in (" ", "b"):
                        self.draw(1 if key == " " else 10)
                    elif key == "s":
                        self.stats()
                    elif key == "l":
                        self.history()
                    elif key == "p":
                        self.probabilities()
                    elif key == "c":
                        self.configure()
                    elif key == "r":
                        self.real_results()
                    elif key == "m":
                        self.mileage()
                    elif key == 'a':
                        if self.automatic():break
                    else:
                        continue
                    self.kb.flush()
                    self.header()
            except (KeyboardInterrupt, EOFError):
                print("\n종료 요청을 받았습니다.")
            finally:
                self.engine.cancel("exit")
                with self.storage.db:
                    self.storage.event("session_ended", self.engine.session_id)
        print("관측 기록을 저장했습니다. 지휘관님, 다음에 뵙겠습니다.")
        return 0
