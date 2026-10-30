import hashlib
import json
import re
from pathlib import Path
from .banners import BANNERS


class Roster:
    def __init__(self, directory):
        directory = Path(directory)
        raw = (directory / "nikke_roster.json").read_bytes()
        banner_bytes={mode:(directory/info['file']).read_bytes() for mode,info in BANNERS.items()}
        self.version = hashlib.sha256(raw + b''.join(banner_bytes.values())).hexdigest()[:20]
        self.data = json.loads(raw)
        self.banners={mode:json.loads(value) for mode,value in banner_bytes.items()}
        self.banner = self.banners['banner']
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
                if c.get('release_date_verification')!='official_scheduled' or not any(b['pickup_id']==c['id'] and b['pool_verified'] for b in self.banners.values()):
                    raise ValueError("공식 예정 모집에서 확인하지 않은 미출시 캐릭터 포함")
        for mode,banner in self.banners.items():
            if banner['pickup_id']!=BANNERS[mode]['pickup_id'] or banner['pickup_id'] not in self.by_id:
                raise ValueError("픽업 캐릭터 누락 또는 불일치")
            ids=banner['candidate_ids']
            if len(ids)!=len(set(ids)) or banner['pickup_id'] not in ids:
                raise ValueError('모집 후보 ID 중복 또는 픽업 누락')
            for cid in ids:
                c=self.by_id[cid]
                if c['rarity']=='SSR' and (c['limited'] or c['collaboration'] or c['acquisition']=='event_or_liberation'):
                    raise ValueError(f'제외 대상 포함: {cid}')
                if c['banner_included'] is False:
                    raise ValueError(f'제외 확정 대상 포함: {cid}')

    def banner_for(self, mode):
        if mode not in self.banners:raise ValueError('지원하지 않는 모집입니다.')
        return self.banners[mode]

    def pool(self, mode):
        return [self.by_id[x] for x in self.banner_for(mode)['candidate_ids']]

    def label(self, mode):
        self.banner_for(mode)
        return '특수 모집 · '+BANNERS[mode]['name']
