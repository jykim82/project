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
ANIMALS = {
    "black_cat":  ("a cute black cat with a cat head, whiskers, black fur and paws", "the black cat", "black cats"),
    "yellow_cat": ("a cute ginger orange tabby cat with a cat head, whiskers, orange striped fur and paws", "the ginger cat", "ginger tabby cats"),
    "white_cat":  ("a cute fluffy white cat with a cat head, whiskers, white fur and paws", "the white cat", "white cats"),
    "dog":        ("a cute golden retriever dog with a dog head, floppy ears, golden fur and paws", "the dog", "dogs"),
}
STYLE = "Tarot card illustration, storybook art, ink outlines, flat vintage colors, full scene. "
NEGATIVE = ("human, person, man, woman, girl, boy, human face, human skin, human hands, "
            "text, letters, words, watermark, signature, frame, blurry, deformed, extra limbs")

SDXL_DEFAULT = "stabilityai/stable-diffusion-xl-base-1.0"


def card_list():
    cards = []
    for i, (title, _ko, _num, scene) in enumerate(bp.MAJOR):
        cards.append((f"major_{i:02d}", scene))
    for suit in bp.SUITS:
        for r, scene in enumerate(bp.MINOR[suit], 1):
            cards.append((f"{suit}_{r:02d}", scene))
    if len(cards) != 78:
        raise SystemExit(f"card count {len(cards)} != 78")
    return cards


def make_prompt(deck, scene):
    desc, one, many = ANIMALS[deck]
    return STYLE + desc + ". " + scene.replace("{AS}", many).replace("{A}", one)


def pick_device(torch):
    if torch.backends.mps.is_available():
        return "mps", torch.float16
    if torch.cuda.is_available():
        return "cuda", torch.float16
    return "cpu", torch.float32


def load_pipe(model, lightning, device, dtype):
    import torch
    from diffusers import AutoPipelineForText2Image, AutoencoderKL, EulerDiscreteScheduler
    kw = {"torch_dtype": dtype}
    is_xl = "xl" in model.lower()
    if is_xl and dtype == torch.float16:
        # SDXL 기본 VAE는 fp16에서 검은 화면(NaN)이 나와서 고친 VAE를 쓴다
        kw["vae"] = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=dtype)
    if dtype == torch.float16:
        kw["variant"] = "fp16"
    try:
        pipe = AutoPipelineForText2Image.from_pretrained(model, **kw)
    except Exception:
        kw.pop("variant", None)
        pipe = AutoPipelineForText2Image.from_pretrained(model, **kw)
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
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="프롬프트만 출력하고 생성하지 않음")
    a = ap.parse_args()

    cards = card_list()
    if a.only:
        known = {c for c, _ in cards}
        bad = [c for c in a.only if c not in known]
        if bad:
            raise SystemExit(f"알 수 없는 카드 id: {bad}")
        cards = [c for c in cards if c[0] in a.only]

    jobs = []
    for deck in a.decks:
        out_dir = os.path.join(TAROT, "web", "images", deck)
        os.makedirs(out_dir, exist_ok=True)
        for idx, (cid, scene) in enumerate(cards):
            path = os.path.join(out_dir, cid + ".jpg")
            if os.path.exists(path) and not a.overwrite:
                continue
            jobs.append((deck, cid, make_prompt(deck, scene), path))

    print(f"생성할 그림: {len(jobs)}장")
    if a.dry_run:
        for deck, cid, p, _ in jobs:
            print(f"[{deck}/{cid}] {p}")
        return
    if not jobs:
        return

    import torch
    device, dtype = pick_device(torch)
    steps = a.steps or (4 if a.lightning else 30)
    guidance = a.guidance if a.guidance is not None else (0.0 if a.lightning else 6.0)
    print(f"device={device} model={a.model} lightning={a.lightning} steps={steps} guidance={guidance}")
    pipe, _ = load_pipe(a.model, a.lightning, device, dtype)

    for n, (deck, cid, prompt, path) in enumerate(jobs, 1):
        t = time.time()
        # 카드마다 고정 시드 -> 다시 만들어도 같은 그림
        seed = a.seed + sum(map(ord, cid))
        args = dict(prompt=prompt, width=a.width, height=a.height, num_inference_steps=steps,
                    guidance_scale=guidance, generator=torch.Generator("cpu").manual_seed(seed))
        if guidance > 1.0:
            args["negative_prompt"] = NEGATIVE
        img = pipe(**args).images[0]
        img.convert("RGB").save(path, quality=90, optimize=True)
        print(f"[{n}/{len(jobs)}] {deck}/{cid}  {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
