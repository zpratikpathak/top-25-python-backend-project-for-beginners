import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from sqlalchemy import DateTime, Integer, String, Text, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = "sqlite:///./scraper_history.db"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 WebScraperAPI/1.0"
)
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_summary: Mapped[str] = mapped_column(Text, nullable=False)


app = FastAPI(title="Web Scraper API", version="1.0.0")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _log_job(url: str, status: str, result_summary: str) -> None:
    db = SessionLocal()
    try:
        job = ScrapeJob(
            url=url,
            timestamp=datetime.now(timezone.utc),
            status=status,
            result_summary=result_summary[:2000],
        )
        db.add(job)
        db.commit()
    finally:
        db.close()


async def fetch_page(url: str) -> tuple[str | None, str | None]:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None, "invalid_url"
    except Exception:
        return None, "invalid_url"

    headers = {"User-Agent": USER_AGENT}
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            headers=headers,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "text/html" not in ctype and "application/xhtml" not in ctype:
                return None, f"unsupported_content_type:{ctype[:80]}"
            return resp.text, None
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.InvalidURL:
        return None, "invalid_url"
    except httpx.HTTPStatusError as e:
        return None, f"http_error:{e.response.status_code}"
    except httpx.RequestError as e:
        return None, f"connection_error:{type(e).__name__}"


def _summarize_payload(data: Any, max_len: int = 400) -> str:
    try:
        s = json.dumps(data, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(data)
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


async def run_scrape(
    url_str: str,
    work: Any,
    error_prefix: str = "error",
) -> Any:
    html, err = await fetch_page(url_str)
    if err:
        summary = f"{error_prefix}:{err}"
        await asyncio.to_thread(_log_job, url_str, "error", summary)
        if err == "invalid_url":
            raise HTTPException(status_code=400, detail="Invalid or unsupported URL")
        if err == "timeout":
            raise HTTPException(status_code=504, detail="Request timed out")
        if err.startswith("http_error:"):
            code = err.split(":", 1)[1]
            raise HTTPException(status_code=502, detail=f"Upstream HTTP error: {code}")
        if err.startswith("unsupported_content_type:"):
            raise HTTPException(status_code=415, detail="URL did not return HTML")
        raise HTTPException(status_code=502, detail=err.replace("_", " "))

    try:
        result = work(html, url_str)
    except Exception as e:
        summary = f"{error_prefix}:parse:{type(e).__name__}"
        await asyncio.to_thread(_log_job, url_str, "error", summary)
        raise HTTPException(status_code=500, detail="Failed to parse response") from e

    summary = _summarize_payload(result)
    await asyncio.to_thread(_log_job, url_str, "success", summary)
    return result


class ScrapeSelectorsBody(BaseModel):
    url: HttpUrl
    selectors: dict[str, str]


class UrlBody(BaseModel):
    url: HttpUrl


def extract_by_selectors(html: str, base_url: str, selectors: dict[str, str]) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, Any] = {}
    for key, sel in selectors.items():
        if not (sel or "").strip():
            out[key] = []
            continue
        elements = soup.select(sel)
        values: list[str] = []
        for el in elements:
            text = el.get_text(strip=True)
            if text:
                values.append(text)
            elif el.get("href"):
                values.append(urljoin(base_url, el["href"]))
            elif el.get("src"):
                values.append(urljoin(base_url, el["src"]))
        out[key] = values
    return out


def extract_visible_text(html: str, _base: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [ln for ln in (ln.strip() for ln in text.splitlines()) if ln]
    return {"text": "\n".join(lines)}


def extract_links(html: str, base_url: str) -> dict[str, list[dict[str, str]]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        label = a.get_text(strip=True)
        items.append({"href": href, "text": label})
    return {"links": items}


def extract_images(html: str, base_url: str) -> dict[str, list[str]]:
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    seen: set[str] = set()
    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            u = urljoin(base_url, src)
            if u not in seen:
                seen.add(u)
                urls.append(u)
        srcset = img.get("srcset")
        if srcset:
            for part in srcset.split(","):
                u_raw = part.strip().split()[0] if part.strip() else ""
                if u_raw:
                    u = urljoin(base_url, u_raw)
                    if u not in seen:
                        seen.add(u)
                        urls.append(u)
    return {"images": urls}


def extract_metadata(html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    meta: dict[str, Any] = {
        "title": None,
        "description": None,
        "og": {},
        "favicon": None,
    }
    if soup.title and soup.title.string:
        meta["title"] = soup.title.string.strip()
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or "").lower()
        prop = (tag.get("property") or "").lower()
        content = tag.get("content")
        if not content:
            continue
        if name == "description":
            meta["description"] = content.strip()
        if prop.startswith("og:"):
            key = prop[3:] or "unknown"
            meta["og"][key] = content.strip()
    for link in soup.find_all("link", href=True):
        rel_attr = link.get("rel")
        if isinstance(rel_attr, list):
            rel_str = " ".join(rel_attr).lower()
        else:
            rel_str = (rel_attr or "").lower()
        if "icon" in rel_str or rel_str == "shortcut icon":
            meta["favicon"] = urljoin(base_url, link["href"])
            break
    if not meta["favicon"]:
        link = soup.find("link", href=re.compile(r"favicon", re.I))
        if link and link.get("href"):
            meta["favicon"] = urljoin(base_url, link["href"])
    return meta


@app.post("/api/scrape")
async def scrape_selectors(body: ScrapeSelectorsBody):
    url_str = str(body.url)

    def work(html: str, base: str) -> dict[str, Any]:
        return extract_by_selectors(html, base, body.selectors)

    return await run_scrape(url_str, work)


@app.post("/api/scrape/text")
async def scrape_text(body: UrlBody):
    url_str = str(body.url)
    return await run_scrape(url_str, extract_visible_text)


@app.post("/api/scrape/links")
async def scrape_links(body: UrlBody):
    url_str = str(body.url)
    return await run_scrape(url_str, extract_links)


@app.post("/api/scrape/images")
async def scrape_images(body: UrlBody):
    url_str = str(body.url)
    return await run_scrape(url_str, extract_images)


@app.post("/api/scrape/metadata")
async def scrape_metadata_endpoint(body: UrlBody):
    url_str = str(body.url)
    return await run_scrape(url_str, extract_metadata)


@app.get("/api/scrape/history")
async def scrape_history(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    total = db.scalar(select(func.count(ScrapeJob.id))) or 0
    stmt = (
        select(ScrapeJob).order_by(ScrapeJob.timestamp.desc()).offset(skip).limit(limit)
    )
    rows = db.scalars(stmt).all()
    items = [
        {
            "id": r.id,
            "url": r.url,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "status": r.status,
            "result_summary": r.result_summary,
        }
        for r in rows
    ]
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": items,
    }
