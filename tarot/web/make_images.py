# 원본 카드(rider-waite-smith/)를 웹용 크기로 줄여 web/images/original/<id>.jpg 로 저장한다.
# id는 animal-prompts/prompts.json 의 id 와 같다 (major_00, cups_01 ...).
# 동물 그림은 web/images/<black_cat|yellow_cat|white_cat|dog>/<id>.jpg 로 넣으면 페이지가 사용한다.
import json, os
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
cards = json.load(open(os.path.join(ROOT, "animal-prompts", "prompts.json"), encoding="utf-8"))["cards"]
out = os.path.join(HERE, "images", "original")
os.makedirs(out, exist_ok=True)
H = 1000
def save(src, dst):
    im = Image.open(src).convert("RGB")
    im = im.resize((round(im.width * H / im.height), H), Image.LANCZOS)
    im.save(dst, quality=84, optimize=True, progressive=True)
for c in cards:
    save(os.path.join(ROOT, c["source_image"]), os.path.join(out, c["id"] + ".jpg"))
save(os.path.join(ROOT, "rider-waite-smith", "card_back.jpg"), os.path.join(out, "card_back.jpg"))
for v in ("black_cat", "yellow_cat", "white_cat", "dog"):
    os.makedirs(os.path.join(HERE, "images", v), exist_ok=True)
print(len(cards), "cards")
