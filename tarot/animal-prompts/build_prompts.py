# Rider-Waite-Smith 78장의 장면을 바탕으로 동물(고양이/강아지) 버전 이미지 프롬프트를 만든다.
# 실행: python3 tarot/animal-prompts/build_prompts.py
# 결과: 같은 폴더의 prompts.json, prompts.md
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 동물 변형. {A}=단수(인물 1명), {AS}=복수(인물 여러 명)
VARIANTS = {
    "black_cat": ("a sleek black cat", "black cats"),
    "yellow_cat": ("a ginger (orange-yellow) tabby cat", "ginger tabby cats"),
    "white_cat": ("a fluffy white cat", "white cats"),
    "dog": ("a friendly golden retriever dog", "dogs of various breeds"),
}

STYLE = (
    "Vertical tarot card illustration in the style of the 1909 Rider-Waite-Smith deck: "
    "hand-drawn bold black ink outlines, flat muted watercolor colors, white border, "
    "caption banner at the bottom reading \"{title}\"{numeral}. "
    "All human figures are replaced by anthropomorphic animals standing upright and wearing the original clothing. "
    "Scene: {scene} Keep the original composition and symbols. Aspect ratio 11:19, high detail."
)

# (영문 이름, 한글 이름, 로마 숫자, 장면) - 장면에서 {A}/{AS}가 동물로 바뀐다
MAJOR = [
    ("THE FOOL", "바보", "0", "{A} in a flowered tunic and feathered cap steps toward a cliff edge, holding a white rose and a bundle on a staff, a small white dog leaping at its feet, yellow sky, white sun, snowy mountains."),
    ("THE MAGICIAN", "마법사", "I", "{A} in a red robe over white tunic raises a wand to the sky and points down, an infinity sign above its head, a table with a cup, a sword, a wand and a pentacle, roses and lilies around, yellow background."),
    ("THE HIGH PRIESTESS", "여사제", "II", "{A} in blue robes and a horned moon crown sits between a black pillar marked B and a white pillar marked J, holding a scroll labeled TORA, crescent moon at its feet, pomegranate veil behind."),
    ("THE EMPRESS", "여황제", "III", "{A} wearing a crown of twelve stars and a pomegranate-patterned gown reclines on cushions in a golden wheat field, holding a scepter, a heart-shaped shield with the Venus symbol, a forest and waterfall behind."),
    ("THE EMPEROR", "황제", "IV", "{A} with a long white beard in red robes and armor sits on a stone throne decorated with ram heads, holding an ankh scepter and an orb, barren orange mountains behind."),
    ("THE HIEROPHANT", "교황", "V", "{A} in red papal robes and a triple crown sits between two grey pillars, raising a hand in blessing and holding a triple cross, two kneeling tonsured {AS} before it, two crossed keys at its feet."),
    ("THE LOVERS", "연인", "VI", "Two {AS} stand nude in a garden, an angel with red wings above them in a bright sun, a tree with flames behind one, a tree with an apple and a serpent behind the other, a mountain between them."),
    ("THE CHARIOT", "전차", "VII", "{A} in armor with a star crown stands in a stone chariot under a starry canopy, holding a wand, two sphinxes, one black and one white, lie before the chariot, a walled city behind."),
    ("STRENGTH", "힘", "VIII", "{A} in a white gown with a flower garland and an infinity sign above its head gently closes the jaws of a red lion, yellow sky, mountain in the distance."),
    ("THE HERMIT", "은둔자", "IX", "{A} with a long grey beard in a grey hooded cloak stands on a snowy peak at night, holding up a lantern with a six-pointed star inside and leaning on a staff."),
    ("WHEEL OF FORTUNE", "운명의 수레바퀴", "X", "A large golden wheel with letters T A R O and alchemical symbols floats in a blue sky with clouds, a sphinx with a sword on top, a red jackal-headed figure rising on the right, a yellow serpent descending on the left, winged angel, eagle, bull and lion with books in the four corners."),
    ("JUSTICE", "정의", "XI", "{A} in a red robe and green cloak with a crown sits between two grey pillars and a purple veil, holding an upright sword in one paw and golden scales in the other."),
    ("THE HANGED MAN", "매달린 사람", "XII", "{A} in a blue tunic and red tights hangs upside down by one foot from a T-shaped living wooden tree, the other leg bent behind, hands behind its back, a golden halo around its head, calm expression."),
    ("DEATH", "죽음", "XIII", "A skeleton in black armor rides a white horse carrying a black flag with a white rose, a fallen king on the ground, a praying bishop, a kneeling maiden and a child, all of them {AS}, a sun rising between two towers on the horizon."),
    ("TEMPERANCE", "절제", "XIV", "{A} as an angel with red wings in a white robe with a triangle in a square on its chest pours water between two golden cups, one foot on land and one in a pool, irises on the bank, a path leading to a crown of light over mountains."),
    ("THE DEVIL", "악마", "XV", "A horned devil with bat wings and an inverted pentagram on its brow squats on a black pedestal holding a torch downward, two nude {AS} with small horns and tails stand chained loosely to the pedestal, black background."),
    ("THE TOWER", "탑", "XVI", "A tall grey tower on a rocky peak is struck by lightning, its golden crown blown off, flames from the windows, two {AS} falling headfirst through the dark sky among yellow falling sparks."),
    ("THE STAR", "별", "XVII", "{A} kneels nude by a pool under a large eight-pointed yellow star and seven smaller white stars, pouring water from two jugs, one into the pool and one onto the land, a bird on a tree in the background."),
    ("THE MOON", "달", "XVIII", "A large moon with a face in a crescent sun shines between two grey towers, a dog and a wolf howl at it, a crayfish crawls out of a pool onto a winding path toward the mountains, yellow dew drops falling."),
    ("THE SUN", "태양", "XIX", "A large smiling sun shines over a grey stone wall with sunflowers, {A} as a happy child with a feather on its head rides a white horse bareback, holding a large red banner."),
    ("JUDGEMENT", "심판", "XX", "The angel Gabriel blows a trumpet with a white flag with a red cross from the clouds, below grey {AS} rise from open coffins floating on water, arms outstretched, snowy mountains behind."),
    ("THE WORLD", "세계", "XXI", "{A} dancing within a large green laurel wreath tied with red ribbons, wrapped in a purple sash, holding a wand in each paw, an angel, eagle, bull and lion in the four corners among clouds, blue sky."),
]

SUITS = {
    "cups": ("CUPS", "컵"),
    "wands": ("WANDS", "완드"),
    "swords": ("SWORDS", "소드"),
    "pentacles": ("PENTACLES", "펜타클"),
}
RANKS = ["ACE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN",
         "PAGE", "KNIGHT", "QUEEN", "KING"]
RANKS_KO = ["에이스", "2", "3", "4", "5", "6", "7", "8", "9", "10", "페이지", "나이트", "퀸", "킹"]

MINOR = {
    "cups": [
        "A large hand emerges from a cloud holding a golden chalice overflowing with five streams of water, a white dove holding a wafer descends into the cup, water lilies on a pond below.",
        "Two {AS}, one in a yellow tunic and one in a blue and white gown, exchange cups and pledge to each other, a caduceus with a winged lion head above them, a house on a hill behind.",
        "Three {AS} in flowing gowns dance in a circle raising their cups in celebration, a harvest of fruit and pumpkins at their feet.",
        "{A} sits under a tree with arms crossed looking at three cups on the grass, a hand from a cloud offers a fourth cup.",
        "{A} in a long black cloak bows its head over three spilled cups, two upright cups stand behind it, a river and a bridge to a castle in the distance.",
        "Two young {AS} in a garden of an old village, one offers the other a cup filled with white flowers, six cups with flowers around them.",
        "{A} seen from behind faces seven golden cups floating on clouds, filled with a head, a veiled glowing figure, a snake, a castle, jewels, a laurel wreath and a dragon.",
        "{A} in a red cloak with a staff walks away from eight stacked cups toward barren mountains at night under a crescent moon with a face.",
        "{A} sits contented on a wooden bench with arms crossed, nine golden cups arranged on a curved blue-draped table behind it.",
        "A couple of {AS} embrace with arms raised toward a rainbow of ten cups in the sky, two young {AS} dance beside them, a house among green hills.",
        "{A} as a page in a blue tunic with a floral pattern and a blue hat stands by the sea, holding a cup from which a small fish peeks out.",
        "{A} as a knight in armor with winged helmet rides a calm white horse, holding out a cup, a river and a valley behind.",
        "{A} as a queen sits on a throne decorated with cherubs at the seashore, gazing at an ornate closed golden cup.",
        "{A} as a king sits on a stone throne floating on a rough sea, holding a cup and a scepter, a fish amulet on its neck, a ship and a leaping fish behind.",
    ],
    "wands": [
        "A hand emerges from a cloud holding a sprouting wooden wand with green leaves, a castle on a hill in the landscape below.",
        "{A} in a brown cloak and red hat stands on castle battlements holding a small globe, one wand in its paw and one fixed to the wall, looking over sea and land.",
        "{A} seen from behind stands on a cliff with three planted wands, looking out over a golden sea where ships sail.",
        "Four wands with a garland of flowers and fruit form a canopy, two {AS} raise bouquets in celebration, a castle and a crowd behind.",
        "Five young {AS} in different colored tunics brandish wands in a playful chaotic struggle.",
        "{A} wearing a laurel wreath rides a white horse through a crowd of {AS}, holding a wand with a wreath, others raise wands.",
        "{A} on a hilltop defends itself with a wand against six wands rising from below, wearing mismatched shoes.",
        "Eight flowering wands fly diagonally through a clear sky over a green landscape with a river and a house on a hill.",
        "{A} with a bandaged head stands guard holding a wand, looking cautiously over its shoulder, eight wands planted behind it like a fence.",
        "{A} bent over carries a heavy bundle of ten wands toward a house in the distance.",
        "{A} as a page in a tunic with salamander pattern and a feathered hat stands in a desert, holding a tall sprouting wand, pyramids behind.",
        "{A} as a knight in armor with a tunic patterned with salamanders rides a rearing orange horse, holding a sprouting wand, pyramids in the desert.",
        "{A} as a queen sits on a throne decorated with lions, holding a wand and a sunflower, a black cat sits at the throne's feet.",
        "{A} as a king in a red robe with salamander patterns sits on a throne decorated with lions and salamanders, holding a sprouting wand, a small salamander on the ground.",
    ],
    "swords": [
        "A hand emerges from a cloud gripping an upright sword topped with a golden crown hung with an olive branch and a palm branch, golden drops falling, mountains below.",
        "{A} blindfolded in a white robe sits on a stone bench by the sea at night, holding two crossed swords over its chest, a crescent moon above.",
        "A red heart pierced by three swords floats against a grey stormy sky with heavy rain clouds.",
        "{A} as a knight lies in effigy on a tomb with paws joined in prayer, three swords hang on the wall above, one sword along the tomb, a stained glass window.",
        "{A} smirking gathers three swords while two defeated {AS} walk away toward the shore, two swords on the ground, ragged clouds.",
        "{A} as a ferryman poles a boat with six upright swords across water, carrying a cloaked passenger and a child, also {AS}, toward a far shore.",
        "{A} sneaks away from a military camp carrying five swords, looking back over its shoulder, two swords left standing in the ground.",
        "{A} bound and blindfolded stands in a marsh surrounded by eight swords planted in the ground, a castle on a cliff behind.",
        "{A} sits up in bed with its face in its paws, nine swords hang horizontally on the black wall behind, a quilt with roses and zodiac signs.",
        "{A} lies face down on the shore under a black sky with ten swords in its back, a yellow dawn over calm water on the horizon.",
        "{A} as a page stands on a windy hill holding a sword upright with both paws, looking alert, birds in a cloudy sky.",
        "{A} as a knight in armor charges at full gallop on a white horse with sword raised, storm clouds and wind-bent trees.",
        "{A} as a queen sits on a throne decorated with butterflies and a cherub, holding an upright sword and raising one paw, clouds below, a single bird in the sky.",
        "{A} as a king in blue robes sits on a throne decorated with butterflies, holding an upright sword, tall trees and clouds behind.",
    ],
    "pentacles": [
        "A hand emerges from a cloud holding a large golden pentacle, a garden of white lilies below, a hedge archway leading to mountains.",
        "{A} in a tall hat dances while juggling two pentacles linked by an infinity loop, ships ride high waves behind.",
        "{A} as a stone mason works on a cathedral arch decorated with three pentacles, two {AS} in robes, a monk and a noble, hold architectural plans.",
        "{A} wearing a crown sits on a stone block clutching a pentacle to its chest, one pentacle on its crown and two under its feet, a city behind.",
        "Two poor {AS}, one on crutches with a bandaged leg, walk through snow past a lit stained glass church window with five pentacles.",
        "{A} as a merchant in red holds balanced scales and gives coins to one of two kneeling beggar {AS}, six pentacles float above.",
        "{A} as a farmer leans on a hoe looking at a green vine bush bearing seven pentacles.",
        "{A} as a craftsman sits at a bench carving a pentacle, finished pentacles hang on a post, a town in the distance.",
        "{A} in a fine robe with flower pattern stands in a vineyard of grapes, a hooded falcon on its gloved paw, nine pentacles in the vines, a manor behind.",
        "{A} as an old patriarch in a patterned robe sits under an archway with two dogs, a family of {AS} in the courtyard, ten pentacles arranged in the Tree of Life pattern.",
        "{A} as a page in a green tunic and red hat stands in a flowered field, gazing at a pentacle held up in its paws, plowed fields and trees behind.",
        "{A} as a knight in armor sits still on a heavy black horse, holding a pentacle, plowed fields behind.",
        "{A} as a queen sits on a throne decorated with fruit and goats in a garden of roses, holding a pentacle in its lap, a rabbit in the corner.",
        "{A} as a king in a robe of grapevines sits on a throne decorated with bulls, holding a scepter and a pentacle, a castle behind.",
    ],
}


def fill(scene, variant):
    one, many = VARIANTS[variant]
    s = scene.replace("{AS}", many).replace("{A}", one)
    return s[0].upper() + s[1:]


def make_prompt(title, numeral, scene, variant):
    num = f' and the Roman numeral "{numeral}" at the top' if numeral else ""
    return STYLE.format(title=title, numeral=num, scene=fill(scene, variant))


def main():
    cards = []
    for i, (title, ko, numeral, scene) in enumerate(MAJOR):
        cards.append({
            "id": f"major_{i:02d}",
            "source_image": f"rider-waite-smith/major/{i:02d}_{title.lower().replace(' ', '_')}.jpg",
            "name_en": title.title(), "name_ko": ko,
            "prompts": {v: make_prompt(title, numeral, scene, v) for v in VARIANTS},
        })
    for suit, (suit_en, suit_ko) in SUITS.items():
        for r, (rank, scene) in enumerate(zip(RANKS, MINOR[suit]), 1):
            title = f"{rank} of {suit_en}"
            cards.append({
                "id": f"{suit}_{r:02d}",
                "source_image": f"rider-waite-smith/{suit}/{suit}_{r:02d}_{rank.lower()}.jpg",
                "name_en": title.title().replace(" Of ", " of "),
                "name_ko": f"{suit_ko} {RANKS_KO[r - 1]}",
                "prompts": {v: make_prompt(title, None, scene, v) for v in VARIANTS},
            })
    # 카드 수가 맞지 않으면 파일을 쓰지 않고 중단
    if len(cards) != 78:
        raise SystemExit(f"card count {len(cards)} != 78")

    with open(os.path.join(HERE, "prompts.json"), "w", encoding="utf-8") as f:
        json.dump({"variants": {k: v[0] for k, v in VARIANTS.items()}, "cards": cards},
                  f, ensure_ascii=False, indent=1)

    lines = ["# 동물 타로 프롬프트 (78장 x 4버전)", ""]
    for c in cards:
        lines += [f"## {c['id']} · {c['name_en']} ({c['name_ko']})", "",
                  f"원본: `../{c['source_image']}`", ""]
        for v, p in c["prompts"].items():
            lines += [f"**{v}**", "", "```", p, "```", ""]
    with open(os.path.join(HERE, "prompts.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(len(cards), "cards")


if __name__ == "__main__":
    main()
