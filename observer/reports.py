import re


def parse_reports(text):
    """Three fields: pickup/other SSR/low. Four: pickup/other SSR/SR/R."""
    sections=re.split(r'[/;]',text)
    groups=[]
    for section in sections:
        tokens=section.replace(',',' ').split()
        if not tokens or any(not re.fullmatch(r'\d+',x) for x in tokens):
            raise ValueError('0 이상의 정수를 공백으로 구분해 입력하세요.')
        values=list(map(int,tokens))
        if len(values) in (3,4):chunks=[values]
        elif len(values)%3==0:chunks=[values[i:i+3] for i in range(0,len(values),3)]
        else:raise ValueError('한 모집은 숫자 3개 또는 4개입니다. 여러 모집은 /로 구분하세요.')
        for chunk in chunks:
            if sum(chunk)!=10:raise ValueError('각 10연차의 결과 수량 합계는 10이어야 합니다.')
            keys=('pickup','other_ssr','low') if len(chunk)==3 else ('pickup','other_ssr','sr','r')
            groups.append(dict(zip(keys,chunk)))
    return groups
