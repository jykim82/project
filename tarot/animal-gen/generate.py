# 고양이/강아지 타로 카드 그림 생성기 (맥 Apple Silicon / NVIDIA / CPU 모두 동작)
#
# 결과: tarot/web/images/<덱>/<카드id>.jpg  -> 웹 페이지(tarot/web/index.html)가 바로 사용
# 이미 있는 파일은 건너뛰므로 중간에 멈춰도 다시 실행하면 이어서 만든다.
#
# 예)
#   python tarot/animal-gen/generate.py --decks black_cat --only major_00 major_01   # 샘플 2장
#   python tarot/animal-gen/generate.py                                             # 4덱 x 78장 전부
#   python tarot/animal-gen/generate.py --lightning                                 # 빠른 모드(4단계)
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TAROT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(TAROT, "animal-prompts"))
import build_prompts as bp  # noqa: E402  카드 장면 설명(MAJOR, MINOR)을 재사용

# 덱별 동물 묘사. 동물 얼굴/털을 프롬프트 맨 앞에 둬야 사람이 아닌 동물로 그려진다.
# cat = '달빛 고양이' 덱 (고양이 버전 단일 덱)
ANIMALS = {
    "cat": ("a cute black cat with a cat head, whiskers, black fur, golden eyes and paws", "the black cat", "black cats"),
    "dog": ("a cute golden retriever dog with a dog head, floppy ears, golden fur and paws", "the dog", "dogs"),
}
# 섬세한 아르누보 화풍. 짧게 유지해야 장면의 상징(심벌)이 77토큰 안에 들어간다.
STYLE = "Ornate art nouveau tarot card, intricate fine linework, rich jewel colors, highly detailed. "
NEGATIVE = ("human, person, man, woman, girl, boy, human face, human skin, human hands, "
            "text, letters, words, watermark, signature, frame, blurry, deformed, extra limbs")

SDXL_DEFAULT = "stabilityai/stable-diffusion-xl-base-1.0"


def card_list():
    # (id, scene, 영문 명칭, 한글 명칭)
    cards = []
    for i, (title, ko, num, scene) in enumerate(bp.MAJOR):
        cards.append((f"major_{i:02d}", scene, f"{num} · {title}", ko))
    for suit, (suit_en, suit_ko) in bp.SUITS.items():
        for r, scene in enumerate(bp.MINOR[suit], 1):
            cards.append((f"{suit}_{r:02d}", scene,
                          f"{bp.RANKS[r - 1]} OF {suit_en}",
                          f"{suit_ko} {bp.RANKS_KO[r - 1]}"))
    if len(cards) != 78:
        raise SystemExit(f"card count {len(cards)} != 78")
    return cards


def find_font(candidates):
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


FONT_EN = find_font(["/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
                     "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"])
FONT_KO = find_font(["/System/Library/Fonts/AppleSDGothicNeo.ttc",
                     "/System/Library/Fonts/Supplemental/AppleGothic.ttf"])


def fit_font(draw, path, text, size, max_w):
    from PIL import ImageFont
    while size > 10:
        f = ImageFont.truetype(path, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return ImageFont.truetype(path, size)


def add_title(img, name_en, name_ko):
    # 하단에 어두운 띠를 깔고 금색 영문 명칭 + 한글 명칭을 폰트로 입힌다
    from PIL import Image, ImageDraw
    img = img.convert("RGBA")
    W, H = img.size
    band_h = round(H * 0.105)
    band = Image.new("RGBA", (W, band_h), (26, 18, 10, 225))
    d = ImageDraw.Draw(band)
    d.rectangle([0, 0, W, 2], fill=(212, 175, 90, 255))  # 금색 윗줄
    f_en = fit_font(d, FONT_EN, name_en, round(band_h * 0.34), W * 0.9)
    f_ko = fit_font(d, FONT_KO, name_ko, round(band_h * 0.26), W * 0.9)
    y1 = round(band_h * 0.14)
    d.text((W / 2, y1), name_en, font=f_en, fill=(222, 186, 100), anchor="ma")
    y2 = y1 + round(band_h * 0.42)
    d.text((W / 2, y2), name_ko, font=f_ko, fill=(232, 220, 192), anchor="ma")
    img.paste(band, (0, H - band_h), band)
    return img.convert("RGB")


def make_prompt(deck, scene):
    desc, one, many = ANIMALS[deck]
    return STYLE + desc + ". " + scene.replace("{AS}", many).replace("{A}", one)


def pick_device(torch):
    if torch.backends.mps.is_available():
        return "mps", torch.float16
    if torch.cuda.is_available():
        return "cuda", torch.float16
    return "cpu", torch.float32


def load_pipe(model, lightning, device, dtype, img2img=False, controlnet=False):
    import torch
    from diffusers import (AutoPipelineForText2Image, AutoPipelineForImage2Image,
                           AutoencoderKL, EulerDiscreteScheduler)
    cls = AutoPipelineForImage2Image if img2img else AutoPipelineForText2Image
    kw = {"torch_dtype": dtype}
    if controlnet:
        from diffusers import StableDiffusionXLControlNetPipeline, ControlNetModel
        cls = StableDiffusionXLControlNetPipeline
        kw["controlnet"] = ControlNetModel.from_pretrained(
            "diffusers/controlnet-canny-sdxl-1.0", torch_dtype=dtype)
    is_xl = "xl" in model.lower()
    if is_xl and dtype == torch.float16:
        # SDXL 기본 VAE는 fp16에서 검은 화면(NaN)이 나와서 고친 VAE를 쓴다
        kw["vae"] = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=dtype)
    if dtype == torch.float16:
        kw["variant"] = "fp16"
    try:
        pipe = cls.from_pretrained(model, **kw)
    except Exception:
        kw.pop("variant", None)
        pipe = cls.from_pretrained(model, **kw)
    if lightning:
        if not is_xl:
            raise SystemExit("--lightning 은 SDXL 모델에서만 쓸 수 있습니다")
        pipe.load_lora_weights("ByteDance/SDXL-Lightning", weight_name="sdxl_lightning_4step_lora.safetensors")
        pipe.fuse_lora()
        pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config, timestep_spacing="trailing")
    pipe = pipe.to(device)
    if device == "mps":
        pipe.enable_attention_slicing()
    pipe.set_progress_bar_config(disable=True)
    return pipe, is_xl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", nargs="+", default=list(ANIMALS), choices=list(ANIMALS))
    ap.add_argument("--only", nargs="+", help="카드 id만 골라서 생성 (예: major_00 cups_03)")
    ap.add_argument("--model", default=SDXL_DEFAULT)
    ap.add_argument("--lightning", action="store_true", help="SDXL-Lightning 4단계 빠른 모드")
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--guidance", type=float, default=None)
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=1344)
    ap.add_argument("--seed", type=int, default=1909)
    ap.add_argument("--strength", type=float, default=0.55,
                    help="원본 라이더-웨이트 그림을 밑그림으로 쓰는 정도. "
                         "낮을수록 원본 구도/심벌 유지, 높을수록 자유롭게. 1.0이면 밑그림 없이 생성")
    ap.add_argument("--controlnet", action="store_true",
                    help="원본의 윤곽선(canny)만 뼈대로 쓰고 화풍은 자유롭게 생성. --strength 무시")
    ap.add_argument("--cn-scale", type=float, default=0.55,
                    help="controlnet 윤곽선을 따르는 정도 (0.3 느슨 ~ 0.8 엄격)")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="프롬프트만 출력하고 생성하지 않음")
    ap.add_argument("--retitle", action="store_true",
                    help="그림은 다시 만들지 않고 raw/ 원본에 명칭 배너만 다시 입힌다")
    a = ap.parse_args()

    cards = card_list()
    if a.only:
        known = {c[0] for c in cards}
        bad = [c for c in a.only if c not in known]
        if bad:
            raise SystemExit(f"알 수 없는 카드 id: {bad}")
        cards = [c for c in cards if c[0] in a.only]

    if a.retitle:
        from PIL import Image
        n = 0
        for deck in a.decks:
            for cid, _scene, name_en, name_ko in cards:
                raw = os.path.join(HERE, "raw", deck, cid + ".jpg")
                if not os.path.exists(raw):
                    continue
                out = os.path.join(TAROT, "web", "images", deck, cid + ".jpg")
                add_title(Image.open(raw), name_en, name_ko).save(out, quality=90, optimize=True)
                n += 1
        print(f"배너 다시 입힘: {n}장")
        return

    jobs = []
    for deck in a.decks:
        out_dir = os.path.join(TAROT, "web", "images", deck)
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(os.path.join(HERE, "raw", deck), exist_ok=True)
        for cid, scene, name_en, name_ko in cards:
            path = os.path.join(out_dir, cid + ".jpg")
            if os.path.exists(path) and not a.overwrite:
                continue
            jobs.append((deck, cid, make_prompt(deck, scene), path, name_en, name_ko))

    print(f"생성할 그림: {len(jobs)}장")
    if a.dry_run:
        for deck, cid, p, _path, _en, _ko in jobs:
            print(f"[{deck}/{cid}] {p}")
        return
    if not jobs:
        return

    import torch
    from PIL import Image
    device, dtype = pick_device(torch)
    img2img = a.strength < 0.999 and not a.controlnet
    # img2img는 실제 스텝 수가 steps * strength 라서 기본 스텝을 올려 보정한다
    steps = a.steps or (4 if a.lightning else (round(30 / a.strength) if img2img else 30))
    guidance = a.guidance if a.guidance is not None else (0.0 if a.lightning else 6.0)
    print(f"device={device} model={a.model} lightning={a.lightning} steps={steps} "
          f"guidance={guidance} "
          f"{'controlnet scale=' + str(a.cn_scale) if a.controlnet else 'strength=' + (str(a.strength) if img2img else '없음(text2img)')}")
    pipe, _ = load_pipe(a.model, a.lightning, device, dtype, img2img=img2img, controlnet=a.controlnet)

    for n, (deck, cid, prompt, path, name_en, name_ko) in enumerate(jobs, 1):
        t = time.time()
        # 카드마다 고정 시드 -> 다시 만들어도 같은 그림
        seed = a.seed + sum(map(ord, cid))
        args = dict(prompt=prompt, num_inference_steps=steps,
                    guidance_scale=guidance, generator=torch.Generator("cpu").manual_seed(seed))
        if img2img or a.controlnet:
            # 원본 라이더-웨이트 카드를 밑그림으로 -> 구도, 자세, 심벌 배치가 원본을 따라간다
            orig = os.path.join(TAROT, "web", "images", "original", cid + ".jpg")
            if not os.path.exists(orig):
                raise SystemExit(f"원본 그림 없음: {orig}")
            im = Image.open(orig).convert("RGB")
            W, H = im.size
            # 흰 테두리/로마숫자/하단 제목 띠를 잘라낸다 -> 깨진 글자로 생성되는 것 방지
            im = im.crop((round(W * 0.05), round(H * 0.06), round(W * 0.95), round(H * 0.885)))
            im = im.resize((a.width, a.height), Image.LANCZOS)
            if a.controlnet:
                # 윤곽선만 뼈대로 쓰고 색/질감/화풍은 프롬프트가 결정한다
                import cv2
                import numpy as np
                edges = cv2.Canny(np.array(im), 100, 200)
                args["image"] = Image.fromarray(np.stack([edges] * 3, axis=-1))
                args["controlnet_conditioning_scale"] = a.cn_scale
                args["width"], args["height"] = a.width, a.height
            else:
                args["image"] = im
                args["strength"] = a.strength
        else:
            args["width"], args["height"] = a.width, a.height
        if guidance > 1.0:
            args["negative_prompt"] = NEGATIVE
        img = pipe(**args).images[0].convert("RGB")
        # 명칭 배너를 입히기 전 원본을 보관 -> 배너 디자인만 바꿀 때 --retitle 로 재생성 없이 처리
        img.save(os.path.join(HERE, "raw", deck, cid + ".jpg"), quality=92)
        add_title(img, name_en, name_ko).save(path, quality=90, optimize=True)
        print(f"[{n}/{len(jobs)}] {deck}/{cid}  {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
