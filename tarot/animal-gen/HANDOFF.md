# 맥미니(taro 세션)에서 고양이·강아지 카드 그림 만들기

대상: Apple Silicon 맥미니 48GB. Apple GPU(MPS)로 SDXL 모델을 돌린다.
결과물: `tarot/web/images/<덱>/<카드id>.jpg` (덱 4개 x 78장 = 312장). 웹 페이지가 이 경로를 바로 읽는다.

## 1. GitHub에서 받기
저장소: https://github.com/jykim82/project  브랜치: `claude-upbeat-tesla-999xra`

처음이면:
```bash
git clone -b claude-upbeat-tesla-999xra https://github.com/jykim82/project.git
cd project
```
이미 받아둔 폴더가 있으면:
```bash
git fetch origin claude-upbeat-tesla-999xra
git checkout claude-upbeat-tesla-999xra
git pull origin claude-upbeat-tesla-999xra
```
받는 것: 원본 카드 이미지(`tarot/rider-waite-smith/`, `tarot/web/images/original/`), 웹 페이지(`tarot/web/`), 프롬프트(`tarot/animal-prompts/`), 생성 스크립트(이 폴더).

## 2. 파이썬 환경 (한 번만)
```bash
python3 -m venv ~/tarot-venv
~/tarot-venv/bin/pip install torch diffusers transformers accelerate safetensors pillow peft
```
첫 실행 때 Hugging Face에서 모델을 받는다(SDXL 약 7GB, 시간이 걸림).

## 3. 샘플 먼저 (덱마다 2장)
```bash
~/tarot-venv/bin/python tarot/animal-gen/generate.py --only major_00 major_01
```
`tarot/web/images/black_cat/major_00.jpg` 등을 열어보고, 사람이 아닌 동물로 그려졌는지 확인한다.
- 마음에 안 들면 `--overwrite --seed 다른숫자` 로 다시 만든다.
- 너무 느리면 빠른 모드 `--lightning` (4단계, 품질은 조금 낮음).

## 4. 전체 생성
```bash
~/tarot-venv/bin/python tarot/animal-gen/generate.py
```
- 이미 만든 파일은 건너뛰므로, 중간에 멈춰도 같은 명령으로 이어서 만든다.
- 덱 하나만: `--decks black_cat` / 카드 몇 장만 다시: `--only cups_03 --overwrite`
- 프롬프트만 보기: `--dry-run`

## 5. 페이지에서 확인
```bash
cd tarot/web && python3 -m http.server 8000
```
브라우저에서 http://localhost:8000 → 덱 고르기 → 카드 섞고 펼치기. 동물 그림이 있는 카드는 '원본 그림' 표시가 사라진다.

## 6. GitHub에 올리기
```bash
git add tarot/web/images
git commit -m "Add generated cat/dog tarot card images"
git push origin claude-upbeat-tesla-999xra
```
올리고 나면 클라우드 세션에서 게시 페이지(claude.ai artifact)를 동물 그림으로 다시 게시할 수 있다.

## taro 세션에 줄 한 줄 지시
> jykim82/project 저장소의 claude-upbeat-tesla-999xra 브랜치를 받아서 tarot/animal-gen/HANDOFF.md 순서대로 고양이·강아지 타로 그림을 만들고, 샘플 확인 후 전체 생성해서 GitHub에 푸시해줘.
