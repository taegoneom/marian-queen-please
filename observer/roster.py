import hashlib
import json
import re
from pathlib import Path


class Roster:
    def __init__(self, directory):
        directory = Path(directory)
        raw = (directory / "nikke_roster.json").read_bytes()
        banner_raw = (directory / "banner_marian_queen.json").read_bytes()
        self.version = hashlib.sha256(raw + banner_raw).hexdigest()[:20]
        self.data = json.loads(raw)
        self.banner = json.loads(banner_raw)
        self.characters = self.data["characters"]
        self.by_id = {c["id"]: c for c in self.characters}
        self.validate()

    def validate(self):
        if len(self.by_id) != len(self.characters):
            raise ValueError("캐릭터 ID 중복")
        for c in self.characters:
            for key in ("id", "name_ko", "name_en", "rarity", "sources", "last_verified"):
                if not c.get(key):
                    raise ValueError(f"필수 데이터 누락: {key}")
            if not re.search("[가-힣]", c["name_ko"]) and c["name_ko"] not in ("D", "K", "E.H.", "2B", "A2", "N102"):
                raise ValueError(f"한국어 명칭 확인 필요: {c['name_ko']}")
            if c["rarity"] not in ("SSR", "SR", "R"):
                raise ValueError("잘못된 등급")
            if c["release_date"] and c["release_date"] > self.data["as_of"]:
                raise ValueError("미출시 캐릭터 포함")
        if self.banner["pickup_id"] not in self.by_id:
            raise ValueError("픽업 캐릭터 누락")
        ids = self.banner["candidate_ids"]
        if len(ids) != len(set(ids)):
            raise ValueError("모집 후보 ID 중복")
        for cid in ids:
            c = self.by_id[cid]
            if c["rarity"] == "SSR" and (c["limited"] or c["collaboration"] or c["acquisition"] == "event_or_liberation"):
                raise ValueError(f"제외 대상이 A 모드에 포함됨: {cid}")
            if c["banner_included"] is False:
                raise ValueError(f"제외 확정 대상 포함: {cid}")

    def pool(self, mode):
        if mode == "banner":
            return [self.by_id[x] for x in self.banner["candidate_ids"]]
        if mode == "all":
            return list(self.characters)
        raise ValueError("알 수 없는 모집 모드")

    def label(self, mode):
        if mode == "banner":
            return "특수 모집 · 마리안 : 퀸"
        return "전체 대원 관측"
