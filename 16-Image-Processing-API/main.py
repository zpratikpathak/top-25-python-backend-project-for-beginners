from __future__ import annotations

from io import BytesIO
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/bmp",
        "image/x-ms-bmp",
    }
)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

FILTER_NAMES = frozenset(
    {"blur", "sharpen", "grayscale", "sepia", "contour", "edge_enhance"}
)
TARGET_FORMATS = frozenset({"png", "jpeg", "webp", "bmp"})
WATERMARK_POSITIONS = frozenset(
    {
        "center",
        "top-left",
        "top-right",
        "bottom-left",
        "bottom-right",
        "top-center",
        "bottom-center",
    }
)

FORMAT_TO_MEDIA = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "bmp": "image/bmp",
}

app = FastAPI(title="Image Processing API", version="1.0.0")


def _validate_upload(content_type: str | None, filename: str | None) -> None:
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct in ALLOWED_CONTENT_TYPES:
        return
    if filename:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in {"jpg", "jpeg", "png", "webp", "gif", "bmp"}:
            return
    raise HTTPException(
        status_code=400,
        detail="Unsupported file type. Use JPEG, PNG, WebP, GIF, or BMP.",
    )


async def _open_upload_image(upload: UploadFile) -> tuple[Image.Image, int]:
    _validate_upload(upload.content_type, upload.filename)
    raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")
    size_bytes = len(raw)
    if size_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except OSError:
        raise HTTPException(status_code=400, detail="Invalid or corrupted image.")
    return img, size_bytes


async def _load_image(upload: UploadFile) -> Image.Image:
    img, _ = await _open_upload_image(upload)
    return img


def _guess_output_format(filename: str | None) -> str:
    ext = (filename or "image.png").rsplit(".", 1)[-1].lower()
    if ext == "jpg":
        ext = "jpeg"
    return ext if ext in TARGET_FORMATS else "png"


def _image_to_stream(img: Image.Image, fmt: str) -> tuple[BytesIO, str]:
    fmt = fmt.lower()
    if fmt not in TARGET_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid format. Choose one of: {', '.join(sorted(TARGET_FORMATS))}.",
        )
    buf = BytesIO()
    save_kwargs: dict = {}
    if fmt == "jpeg":
        save_kwargs["quality"] = 90
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
    elif fmt == "webp":
        save_kwargs["quality"] = 90
    elif fmt == "bmp" and img.mode == "RGBA":
        img = img.convert("RGB")
    pil_format = "JPEG" if fmt == "jpeg" else fmt.upper()
    try:
        img.save(buf, format=pil_format, **save_kwargs)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Could not encode image: {e}") from e
    buf.seek(0)
    return buf, FORMAT_TO_MEDIA[fmt]


def _apply_sepia(img: Image.Image) -> Image.Image:
    base = img.convert("RGB")
    out = []
    for r, g, b in base.getdata():
        tr = min(255, int(0.393 * r + 0.769 * g + 0.189 * b))
        tg = min(255, int(0.349 * r + 0.686 * g + 0.168 * b))
        tb = min(255, int(0.272 * r + 0.534 * g + 0.131 * b))
        out.append((tr, tg, tb))
    result = Image.new("RGB", base.size)
    result.putdata(out)
    return result


def _watermark_xy(
    position: str, img_w: int, img_h: int, tw: int, th: int, margin: int
) -> tuple[int, int]:
    if position == "center":
        return (img_w - tw) // 2, (img_h - th) // 2
    if position == "top-left":
        return margin, margin
    if position == "top-right":
        return img_w - tw - margin, margin
    if position == "bottom-left":
        return margin, img_h - th - margin
    if position == "bottom-right":
        return img_w - tw - margin, img_h - th - margin
    if position == "top-center":
        return (img_w - tw) // 2, margin
    if position == "bottom-center":
        return (img_w - tw) // 2, img_h - th - margin
    raise HTTPException(status_code=400, detail="Invalid watermark position.")


@app.post("/api/images/resize")
async def resize_image(
    image: UploadFile = File(...),
    width: int = Form(..., ge=1, le=8192),
    height: int = Form(..., ge=1, le=8192),
):
    img = await _load_image(image)
    try:
        out = img.resize((width, height), Image.Resampling.LANCZOS)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    fmt = _guess_output_format(image.filename)
    buf, media = _image_to_stream(out, fmt)
    return StreamingResponse(buf, media_type=media)


@app.post("/api/images/crop")
async def crop_image(
    image: UploadFile = File(...),
    left: int = Form(..., ge=0),
    top: int = Form(..., ge=0),
    right: int = Form(..., ge=1),
    bottom: int = Form(..., ge=1),
):
    img = await _load_image(image)
    w, h = img.size
    if left >= right or top >= bottom:
        raise HTTPException(
            status_code=400, detail="Invalid box: require left < right and top < bottom."
        )
    if right > w or bottom > h:
        raise HTTPException(
            status_code=400,
            detail=f"Crop box exceeds image dimensions ({w}x{h}).",
        )
    try:
        out = img.crop((left, top, right, bottom))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    fmt = _guess_output_format(image.filename)
    buf, media = _image_to_stream(out, fmt)
    return StreamingResponse(buf, media_type=media)


@app.post("/api/images/rotate")
async def rotate_image(
    image: UploadFile = File(...),
    angle: float = Form(...),
):
    img = await _load_image(image)
    out = img.rotate(-angle, expand=True, resample=Image.Resampling.BICUBIC)
    fmt = _guess_output_format(image.filename)
    buf, media = _image_to_stream(out, fmt)
    return StreamingResponse(buf, media_type=media)


@app.post("/api/images/flip")
async def flip_image(
    image: UploadFile = File(...),
    direction: Literal["horizontal", "vertical"] = Form(...),
):
    img = await _load_image(image)
    if direction == "horizontal":
        out = ImageOps.mirror(img)
    else:
        out = ImageOps.flip(img)
    fmt = _guess_output_format(image.filename)
    buf, media = _image_to_stream(out, fmt)
    return StreamingResponse(buf, media_type=media)


@app.post("/api/images/filter")
async def filter_image(
    image: UploadFile = File(...),
    filter_name: str = Form(...),
):
    name = filter_name.lower().strip()
    if name not in FILTER_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown filter. Use one of: {', '.join(sorted(FILTER_NAMES))}.",
        )
    img = await _load_image(image)
    if name == "blur":
        out = img.filter(ImageFilter.GaussianBlur(radius=2))
    elif name == "sharpen":
        out = img.filter(ImageFilter.SHARPEN)
    elif name == "grayscale":
        out = ImageOps.grayscale(img).convert("RGB")
    elif name == "sepia":
        out = _apply_sepia(img)
    elif name == "contour":
        out = img.filter(ImageFilter.CONTOUR).convert("RGB")
    else:
        out = img.filter(ImageFilter.EDGE_ENHANCE)
    fmt = _guess_output_format(image.filename)
    buf, media = _image_to_stream(out, fmt)
    return StreamingResponse(buf, media_type=media)


@app.post("/api/images/convert")
async def convert_image(
    image: UploadFile = File(...),
    target_format: str = Form(...),
):
    tf = target_format.lower().strip()
    if tf == "jpg":
        tf = "jpeg"
    if tf not in TARGET_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target_format. Use one of: {', '.join(sorted(TARGET_FORMATS))}.",
        )
    img = await _load_image(image)
    buf, media = _image_to_stream(img, tf)
    return StreamingResponse(buf, media_type=media)


@app.post("/api/images/watermark")
async def watermark_image(
    image: UploadFile = File(...),
    text: str = Form(..., min_length=1, max_length=200),
    position: str = Form(default="bottom-right"),
):
    pos = position.lower().strip()
    if pos not in WATERMARK_POSITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid position. Use one of: {', '.join(sorted(WATERMARK_POSITIONS))}.",
        )
    img = await _load_image(image)
    layer = img.convert("RGBA")
    overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    margin = 12
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = _watermark_xy(pos, layer.size[0], layer.size[1], tw, th, margin)
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 200))
    combined = Image.alpha_composite(layer, overlay)
    fmt = _guess_output_format(image.filename)
    buf, media = _image_to_stream(combined, fmt)
    return StreamingResponse(buf, media_type=media)


@app.post("/api/images/info")
async def image_info(image: UploadFile = File(...)):
    img, size_bytes = await _open_upload_image(image)
    fmt = img.format or "unknown"
    return JSONResponse(
        {
            "filename": image.filename,
            "content_type": image.content_type,
            "format": fmt,
            "mode": img.mode,
            "width": img.size[0],
            "height": img.size[1],
            "size_bytes": size_bytes,
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
