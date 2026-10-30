import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from observer import config
from observer.gacha import Gacha
from observer.roster import Roster
from observer.storage import Storage
from observer.terminal import Terminal

COMMANDER_NAME = "여기에지휘관닉네임을입력해주세요"

ROOT = Path(__file__).resolve().parent


class SingleInstance:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        if not self.file.read(1):
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("같은 저장소를 사용하는 프로그램이 실행 중입니다.") from None
        return self

    def __exit__(self, *_):
        self.file.close()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="NIKKE Quantum Observation Gacha")
    parser.add_argument("--validate", action="store_true", help="실행 없이 데이터/확률 검증")
    parser.add_argument("--stats", action="store_true", help="저장된 통계 출력")
    parser.add_argument("--export", type=Path, help="전체 이력 JSON 내보내기")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--db", type=Path, default=ROOT / "runtime" / "observations.sqlite3")
    parser.add_argument('--auto',action='store_true',help='지휘관 이름 확인 후 자동 관측 시작')
    parser.add_argument('--record-real',nargs='+',type=int,metavar='수량',help='픽업 기타SSR 나머지 또는 픽업 기타SSR SR R. 실행 중에도 기록 가능')
    parser.add_argument('--free-count',type=int,default=0,help='실제 수량 기록 중 무료 모집 횟수')
    args = parser.parse_args()
    try:
        roster = Roster(ROOT / "data")
        c = config.load(args.config if args.config.exists() else ROOT / "config.example.json")
        if COMMANDER_NAME != "여기에지휘관닉네임을입력해주세요":
            c["commander"] = COMMANDER_NAME
        if args.record_real is not None:
            if not c['commander'].strip():raise ValueError('지휘관 이름을 먼저 설정하세요.')
            store=Storage(args.db,c['initial_gold_mileage'])
            try:
                if len(args.record_real) not in (3,4):raise ValueError('수량을 3개 또는 4개 입력하세요.')
                keys=('pickup','other_ssr','low') if len(args.record_real)==3 else ('pickup','other_ssr','sr','r')
                counts=dict(zip(keys,args.record_real))
                number=store.record_real_batch(c['commander'],counts,args.free_count)
                print(f'실제 결과 묶음 #{number} 저장: {counts}, 무료 {args.free_count}회, 골드 마일리지 {store.gold}')
                return 0
            finally:store.close()
        if args.validate:
            out = {"counts": roster.data["counts"], "data_hash": roster.version, "official_banner_pool_verified": roster.banner["pool_verified"], "modes": {}}
            for mode in ("banner",):
                g = Gacha(roster, mode)
                out["modes"][mode] = {"characters": len(g.characters), "sum": str(sum(g.probabilities.values())), "pickup": str(g.probabilities[g.pickup_id]), "ssr": str(sum(g.probabilities[x['id']] for x in g.characters if x['rarity']=='SSR'))}
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if (args.stats or args.export) and not args.db.exists():
            print("저장된 기록이 없습니다.")
            return 0
        with SingleInstance(args.db.with_suffix(".lock")):
            store = Storage(args.db, c["initial_gold_mileage"])
            try:
                if args.stats:
                    print(json.dumps(store.stats(c["pool_mode"]), ensure_ascii=False, indent=2))
                if args.export:
                    store.export(args.export)
                    print(f"내보내기 완료: {args.export.resolve()}")
                if args.stats or args.export:
                    return 0
                count = store.recover()
                if count:
                    print(f"중단된 모집 {count}개를 복구했습니다. 확정 결과는 유지하고 미관측 시도는 취소했습니다.")
                return Terminal(roster, store, c, args.config).run(args.auto)
            finally:
                store.close()
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"실행을 중단했습니다: {exc}", file=sys.stderr)
        print("기록 파일을 보존했습니다. 설정과 저장소 접근 권한을 확인하세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
