import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from better_profanity import profanity
import imagehash
from nudenet import NudeDetector
from PIL import Image, ImageSequence, ImageEnhance, ImageFilter, ImageOps
import pytesseract

import config

profanity.load_censor_words()

if config.TESSERACT_PATH and Path(config.TESSERACT_PATH).exists():
    pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_PATH
else:
    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        str(Path.home() / "AppData/Local/Programs/Tesseract-OCR/tesseract.exe"),
        "/usr/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
    ):
        if Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            break


@dataclass
class ModerationResult:
    flagged: bool
    reason: str = ""


def compute_image_hash(data: bytes, is_gif: bool = False) -> str | None:
    try:
        img = Image.open(io.BytesIO(data))
        if is_gif and getattr(img, "is_animated", False):
            img.seek(0)
        return str(imagehash.phash(img.convert("RGB")))
    except Exception as e:
        print(f"[moderation] hashing failed: {e}")
        return None


def hashes_similar(hash_a: str, hash_b: str, threshold: int = 6) -> bool:
    try:
        return (imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b)) <= threshold
    except Exception:
        return False


def _load_custom_wordlist() -> list[str]:
    path = Path(__file__).parent / "wordlist.txt"
    words = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line.lower())
    return words


WORDLIST_PATH = Path(__file__).parent / "wordlist.txt"
CUSTOM_WORDS = _load_custom_wordlist()


def reload_custom_wordlist() -> None:
    global CUSTOM_WORDS
    CUSTOM_WORDS = _load_custom_wordlist()


def add_custom_word(word: str) -> bool:
    word = word.strip().lower()
    if not word or word in CUSTOM_WORDS:
        return False
    with open(WORDLIST_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n{word}")
    reload_custom_wordlist()
    return True


def remove_custom_word(word: str) -> bool:
    word = word.strip().lower()
    if word not in CUSTOM_WORDS:
        return False
    lines = WORDLIST_PATH.read_text(encoding="utf-8").splitlines()
    kept = [l for l in lines if l.strip().lower() != word]
    WORDLIST_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
    reload_custom_wordlist()
    return True


def _check_custom_wordlist(text: str) -> ModerationResult:
    lowered = text.lower()
    for word in CUSTOM_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return ModerationResult(True, f"matched custom filtered term")
    return ModerationResult(False)


def _check_local_profanity_list(text: str) -> ModerationResult:
    if profanity.contains_profanity(text):
        return ModerationResult(True, "matched local profanity/slur word list")
    return ModerationResult(False)


async def check_text(text: str) -> ModerationResult:
    if not text or not text.strip():
        return ModerationResult(False)

    custom_hit = _check_custom_wordlist(text)
    if custom_hit.flagged:
        return custom_hit

    local_hit = _check_local_profanity_list(text)
    if local_hit.flagged:
        return local_hit

    if config.MODERATION_BACKEND == "huggingface":
        return await _check_text_huggingface(text)
    return ModerationResult(False)


async def _check_text_huggingface(text: str) -> ModerationResult:
    if not config.HUGGINGFACE_API_KEY:
        return ModerationResult(False)

    url = f"https://api-inference.huggingface.co/models/{config.HF_MODEL}"
    headers = {"Authorization": f"Bearer {config.HUGGINGFACE_API_KEY}"}
    body = {"inputs": text}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 503:
                    print("[moderation] Hugging Face model is loading, skipping this check")
                    return ModerationResult(False)
                if resp.status == 429:
                    print("[moderation] Hugging Face rate limited, skipping this check")
                    return ModerationResult(False)
                data = await resp.json()

                if isinstance(data, dict) and "error" in data:
                    print(f"[moderation] Hugging Face error: {data['error']}")
                    return ModerationResult(False)

                scores = data[0] if (isinstance(data, list) and data and isinstance(data[0], list)) else data
                for entry in scores:
                    label = entry.get("label", "").lower()
                    score = entry.get("score", 0)
                    if label in config.HF_FLAGGED_LABELS and score >= config.HF_TOXICITY_THRESHOLD:
                        return ModerationResult(True, f"flagged by Hugging Face model: {label} ({score:.2f})")
                return ModerationResult(False)
    except Exception as e:
        print(f"[moderation] Hugging Face check failed: {e}")
        return ModerationResult(False)


def _preprocess_for_ocr(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    w, h = img.size
    scale = max(1, 1400 // max(1, min(w, h)))
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.LANCZOS)
    img = img.filter(ImageFilter.SHARPEN)
    img = ImageOps.autocontrast(img)
    img = ImageEnhance.Contrast(img).enhance(2.0)
    return img


def _ocr_frame(img: Image.Image) -> str:
    processed = _preprocess_for_ocr(img)
    thresholded = processed.point(lambda p: 255 if p > 160 else 0)

    results = []
    for variant in (processed, thresholded):
        for psm in ("6", "11"):
            try:
                results.append(pytesseract.image_to_string(variant, config=f"--psm {psm}"))
            except Exception as e:
                print(f"[moderation] OCR (psm {psm}) failed: {e}")

    combined = "\n".join(r for r in results if r.strip())
    alnum_count = sum(c.isalnum() for c in combined)
    if alnum_count < 6:
        for angle in (15, -15, 30, -30, 45, -45):
            try:
                rotated = processed.rotate(angle, expand=True, fillcolor=255)
                results.append(pytesseract.image_to_string(rotated, config="--psm 11"))
            except Exception as e:
                print(f"[moderation] OCR rotation {angle} failed: {e}")

    return "\n".join(r for r in results if r.strip())


def _extract_text_from_image_bytes(data: bytes, is_gif: bool) -> str:
    texts = []
    try:
        img = Image.open(io.BytesIO(data))
        if is_gif and getattr(img, "is_animated", False):
            frames = list(ImageSequence.Iterator(img))
            step = max(1, len(frames) // 6)
            for frame in frames[::step][:6]:
                texts.append(_ocr_frame(frame.convert("RGB")))
        else:
            texts.append(_ocr_frame(img.convert("RGB")))
    except Exception as e:
        print(f"[moderation] OCR failed: {e}")
    return "\n".join(t for t in texts if t.strip())


async def check_image_ocr(data: bytes, is_gif: bool = False) -> ModerationResult:
    text = _extract_text_from_image_bytes(data, is_gif)
    if not text.strip():
        print("[moderation] OCR found no text in this image/gif")
        return ModerationResult(False)

    print(f"[moderation] OCR extracted text: {text!r}")

    result = await check_text(text)
    if result.flagged:
        print(f"[moderation] OCR'd text FLAGGED: {result.reason}")
        return ModerationResult(True, f"OCR'd text in image/gif flagged: {result.reason}")
    print("[moderation] OCR'd text did not match any filter")
    return ModerationResult(False)


async def check_image(image_url: str) -> ModerationResult:
    return ModerationResult(False)


_nsfw_detector = None


def _get_nsfw_detector():
    global _nsfw_detector
    if _nsfw_detector is None:
        _nsfw_detector = NudeDetector()
    return _nsfw_detector


def _check_frame_nsfw(img: Image.Image) -> tuple[bool, str, str]:
    detector = _get_nsfw_detector()
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    try:
        os.close(fd)
        img.convert("RGB").save(tmp_path, format="JPEG")
        detections = detector.detect(tmp_path)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    best_label = ""
    best_score = 0.0
    for d in detections:
        label = d.get("class", "")
        score = d.get("score", 0)
        if label in config.NSFW_FLAGGED_LABELS and score > best_score:
            best_label, best_score = label, score
        if label in config.NSFW_FLAGGED_LABELS and score >= config.NSFW_THRESHOLD:
            return True, f"{label} ({score:.2f})", ""

    near_miss = f"{best_label} ({best_score:.2f})" if best_label else "nothing flaggable detected"
    return False, "", near_miss


async def check_image_nsfw(data: bytes, is_gif: bool = False) -> ModerationResult:
    if not config.ENABLE_NSFW_DETECTION:
        return ModerationResult(False)

    try:
        img = Image.open(io.BytesIO(data))
        if is_gif and getattr(img, "is_animated", False):
            all_frames = list(ImageSequence.Iterator(img))
            step = max(1, len(all_frames) // 6)
            frames = [f.convert("RGB") for f in all_frames[::step][:6]]
        else:
            frames = [img.convert("RGB")]

        print(f"[moderation] NSFW check running on {len(frames)} frame(s), threshold={config.NSFW_THRESHOLD}")
        highest_seen = ""
        for i, frame in enumerate(frames):
            flagged, detail, near_miss = _check_frame_nsfw(frame)
            if flagged:
                print(f"[moderation] NSFW content detected on frame {i}: {detail}")
                return ModerationResult(True, f"NSFW content detected: {detail}")
            print(f"[moderation] frame {i}: {near_miss}")
        return ModerationResult(False)
    except Exception as e:
        print(f"[moderation] NSFW check failed: {e}")
        return ModerationResult(False)
