# Image Processing API

REST API built with **FastAPI** and **Pillow** for common image operations: resize, crop, rotate, flip, filters, format conversion, watermarking, and metadata inspection.

## Features

- Multipart file uploads with content-type and extension checks
- Responses streamed as binary image bodies with correct `Content-Type`
- Upload size limit (20 MB)
- Clear validation errors for bad dimensions, crop boxes, filters, and formats

## Supported operations

| Endpoint | Description |
|----------|-------------|
| `POST /api/images/resize` | Scale to given width and height |
| `POST /api/images/crop` | Crop using `left`, `top`, `right`, `bottom` (pixel box) |
| `POST /api/images/rotate` | Rotate by angle in degrees (positive = clockwise) |
| `POST /api/images/flip` | Mirror horizontally or flip vertically |
| `POST /api/images/filter` | `blur`, `sharpen`, `grayscale`, `sepia`, `contour`, `edge_enhance` |
| `POST /api/images/convert` | Encode as `png`, `jpeg`, `webp`, or `bmp` |
| `POST /api/images/watermark` | Semi-transparent text overlay |
| `POST /api/images/info` | JSON metadata (format, mode, dimensions, file size) |

**Watermark positions:** `center`, `top-left`, `top-right`, `bottom-left`, `bottom-right`, `top-center`, `bottom-center`.

**Input types:** JPEG, PNG, WebP, GIF, BMP (first frame for animated GIFs).

**Output format** for image responses defaults from the original filename extension when it is `png`, `jpeg`/`jpg`, `webp`, or `bmp`; otherwise **PNG** is used.

## Tech stack

- Python 3.10+
- FastAPI
- Uvicorn
- Pillow (PIL)
- python-multipart (form uploads)

## Installation

```bash
cd 16-Image-Processing-API
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run the server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## API examples (curl)

Replace `sample.png` with your file path. On Windows PowerShell, `curl` is an alias for `Invoke-WebRequest`; use `curl.exe` for the examples below.

**Resize**

```bash
curl.exe -X POST "http://127.0.0.1:8000/api/images/resize" -F "image=@sample.png" -F "width=400" -F "height=300" --output resized.png
```

**Crop** (box must lie inside the image)

```bash
curl.exe -X POST "http://127.0.0.1:8000/api/images/crop" -F "image=@sample.png" -F "left=10" -F "top=20" -F "right=200" -F "bottom=180" --output cropped.png
```

**Rotate**

```bash
curl.exe -X POST "http://127.0.0.1:8000/api/images/rotate" -F "image=@sample.png" -F "angle=45" --output rotated.png
```

**Flip**

```bash
curl.exe -X POST "http://127.0.0.1:8000/api/images/flip" -F "image=@sample.png" -F "direction=horizontal" --output flipped.png
```

**Filter**

```bash
curl.exe -X POST "http://127.0.0.1:8000/api/images/filter" -F "image=@sample.png" -F "filter_name=grayscale" --output filtered.png
```

**Convert**

```bash
curl.exe -X POST "http://127.0.0.1:8000/api/images/convert" -F "image=@sample.png" -F "target_format=webp" --output out.webp
```

**Watermark**

```bash
curl.exe -X POST "http://127.0.0.1:8000/api/images/watermark" -F "image=@sample.png" -F "text=Hello" -F "position=bottom-right" --output watermarked.png
```

**Info** (JSON)

```bash
curl.exe -X POST "http://127.0.0.1:8000/api/images/info" -F "image=@sample.png"
```

## Project structure

```
16-Image-Processing-API/
├── main.py              # FastAPI application and routes
├── requirements.txt     # Python dependencies
└── README.md            # This file
```

## License

Use freely for learning and projects.
