# 고양이·강아지 타로 리딩 페이지

`index.html` 을 웹 서버로 열면 됩니다 (예: `cd tarot/web && python3 -m http.server 8000` 후 http://localhost:8000).

1. 덱 고르기: 검정 고양이 / 노랑 고양이 / 흰 고양이 / 강아지 / 원본(라이더-웨이트)
2. 손님 이름과 질문 입력, 역방향 포함 여부 선택
3. "카드 섞고 펼치기" → 켈틱 크로스 10자리에 놓임 → 한 장씩 눌러 뒤집기 (한 번 더 누르면 크게 보기)

## 동물 그림 넣기
`images/<덱>/<카드id>.jpg` 로 넣으면 자동으로 사용됩니다.
- 덱 폴더: `black_cat`, `yellow_cat`, `white_cat`, `dog`
- 카드 id: `major_00` ~ `major_21`, `cups_01` ~ `cups_14`, `wands_..`, `swords_..`, `pentacles_..` (`../animal-prompts/prompts.json` 의 id 와 같음)
- 그림이 없는 카드는 `images/original/` 의 원본 그림으로 대신 보여주고 '원본 그림' 표시를 붙입니다.

`make_images.py` 는 `../rider-waite-smith/` 원본을 높이 1000px 로 줄여 `images/original/` 을 만듭니다.
