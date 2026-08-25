"""映像解析→言語化の実験用に、解釈しやすい単純な絵をPillowで描く。

キャプション文字は一切含めない。図形の組み合わせだけで場面を表現し、
Claude自身がキャプション情報なしで見て言語化できるかを試す。
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "media" / "pictogram_samples"
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 640, 480


def scene_ball_and_goal() -> Image.Image:
    img = Image.new("RGB", (W, H), "#8fce8f")
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 60, W, H], fill="#5a9e5a")
    d.rectangle([W - 90, 120, W - 20, 260], outline="white", width=8)
    d.ellipse([260, H - 140, 320, H - 80], fill="white", outline="black", width=3)
    d.ellipse([150, 200, 210, 320], fill="#e07a3f")
    d.line([180, 320, 160, H - 60], fill="#e07a3f", width=8)
    d.line([180, 320, 210, H - 60], fill="#e07a3f", width=8)
    return img


def scene_book() -> Image.Image:
    img = Image.new("RGB", (W, H), "#dceeff")
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 40, W, H], fill="#b8d8f0")
    d.ellipse([220, 260, 300, 420], fill="#e07a3f")
    d.ellipse([255, 150, 305, 200], fill="#f2c49b")
    d.polygon([(180, 340), (320, 340), (320, 400), (250, 420), (180, 400)], fill="white", outline="black")
    d.line([250, 340, 250, 415], fill="black", width=2)
    return img


def scene_dog_walk() -> Image.Image:
    img = Image.new("RGB", (W, H), "#c9e8c9")
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 50, W, H], fill="#8fc98f")
    d.ellipse([150, 200, 200, 340], fill="#3a6ea5")
    d.ellipse([160, 150, 200, 200], fill="#f2c49b")
    d.ellipse([320, 300, 420, 360], fill="#a5703a")
    d.polygon([(420, 320), (450, 300), (450, 330)], fill="#a5703a")
    d.line([200, 260, 330, 320], fill="black", width=3)
    return img


def scene_pot_steam() -> Image.Image:
    img = Image.new("RGB", (W, H), "#f5e6c8")
    d = ImageDraw.Draw(img)
    d.rectangle([220, 260, 420, 380], fill="#555555")
    d.arc([230, 230, 410, 300], start=200, end=340, fill="#333333", width=10)
    for x in (260, 320, 380):
        d.line([x, 240, x - 20, 180, x + 10, 140], fill="#cccccc", width=6, joint="curve")
    d.ellipse([260, 380, 380, 420], fill="#e2725b")
    return img


scenes = {
    "pictogram01": scene_ball_and_goal(),
    "pictogram02": scene_book(),
    "pictogram03": scene_dog_walk(),
    "pictogram04": scene_pot_steam(),
}

for name, img in scenes.items():
    out_path = OUT_DIR / f"{name}.png"
    img.save(out_path)
    print(f"saved {out_path}")
