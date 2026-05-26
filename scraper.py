from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from urllib3.util.retry import Retry


console = Console(force_terminal=True)
# https://e-hentai.org/g/3957694/0fdbde5aa0/
COOKIES: dict[str, str] = {
    "ipb_member_id": "8569968",
    "ipb_pass_hash": "8e729fb038bd7034112ff0c519d1d4dc",
    "igneous": "71m7sfxcr5foek1s8",
}

SAVE_DIR = Path("D:/Downloads")
REQUEST_TIMEOUT = 30
DOWNLOAD_CHUNK_SIZE = 1024 * 128
REQUEST_DELAY = 0.4


def input_gallery_url() -> str:
    gallery_url = input("please input the gallery url you want to scrape: ").strip()
    parsed = urlparse(gallery_url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.path.startswith("/g/"):
        raise ValueError("请输入有效的 e-hentai gallery URL，例如: https://e-hentai.org/g/3957694/0fdbde5aa0/")

    clean_path = parsed.path.rstrip("/") + "/"
    return urlunparse((parsed.scheme, parsed.netloc, clean_path, "", "", ""))


def build_session() -> requests.Session:
    session = requests.Session()
    session.cookies.update(COOKIES)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            )
        }
    )

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def clean_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return name.rstrip(". ") or "gallery"


def fetch_soup(session: requests.Session, url: str) -> BeautifulSoup:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.content, "lxml")


def get_gallery_page_urls(soup: BeautifulSoup, gallery_url: str) -> list[str]:
    page_links = soup.select("table.ptt a")
    page_numbers = [
        int(link.get_text(strip=True))
        for link in page_links
        if link.get_text(strip=True).isdigit()
    ]
    total_pages = max(page_numbers, default=1)
    return [f"{gallery_url}?p={page}" for page in range(total_pages)]


def get_image_url(session: requests.Session, page_url: str) -> tuple[str, str]:
    soup = fetch_soup(session, page_url)
    image = soup.select_one("img#img")
    if not image or not image.get("src"):
        raise RuntimeError(f"找不到普通图片链接: {page_url}")

    original_links = soup.select("div#i6 a")
    original_link = original_links[-1] if original_links else None
    if not original_link or not original_link.get("href"):
        raise RuntimeError(f"找不到原图链接: {page_url}")
    return image["src"], original_link["href"]


def get_links_and_download(
    session: requests.Session,
    gallery_url: str,
    save_dir: Path = SAVE_DIR,
) -> tuple[str, int]:
    successful_count = 0
    image_index = 0
    first_page = fetch_soup(session, gallery_url)
    length = int(first_page.select("td.gdt2")[-2].get_text().split()[0])
    title_el = first_page.select_one("h1#gn")
    title = clean_filename(title_el.get_text(strip=True) if title_el else "gallery")
    console.print(title)
    gallery_pages = get_gallery_page_urls(first_page, gallery_url)
    save_path = save_dir / title
    save_path.mkdir(parents=True, exist_ok=True)

    with Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        "{task.completed}/{task.total}",
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        refresh_per_second=10,
    ) as progress:
        page_task = progress.add_task("Images in total", total=length)
        download_task = progress.add_task("Waiting", total=1)
        for gallery_page in gallery_pages:
            soup = first_page if gallery_page == f"{gallery_url}?p=0" else fetch_soup(session, gallery_page)
            image_pages = [a["href"] for a in soup.select("div#gdt a[href]")]
            for image_page in image_pages:
                normal_link = ""
                file_path: Path | None = None
                try:
                    normal_link, link = get_image_url(session, image_page)
                    image_index += 1
                    file_path = save_path / f"{image_index:04d}{image_extension(link)}"
                    head_response = session.head(
                        link,
                        allow_redirects=True,
                        timeout=REQUEST_TIMEOUT,
                    )
                    image_size = int(head_response.headers.get("content-length") or 0)

                    if file_path.exists() and file_path.stat().st_size == image_size:
                        progress.update(
                            download_task,
                            description=f"Skipped {file_path.name}",
                            total=1,
                            completed=1,
                        )
                        progress.update(page_task, advance=1)
                        continue

                    with session.get(link, stream=True, timeout=REQUEST_TIMEOUT) as response:
                        response.raise_for_status()
                        total = int(response.headers.get("content-length") or 0)
                        progress.update(
                            download_task,
                            description=f"Downloading {file_path.name}",
                            total=total or None,
                            completed=0,
                        )

                        with file_path.open("wb") as f:
                            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                                if chunk:
                                    f.write(chunk)
                                    progress.update(download_task, advance=len(chunk))
                        progress.update(page_task, advance=1)
                    successful_count += 1
                    time.sleep(REQUEST_DELAY)
                except requests.RequestException as exc:
                    fallback_name = file_path.name if file_path else f"{image_index:04d}.jpg"
                    fallback_path = file_path or save_path / fallback_name
                    progress.console.print(f"[red]原图请求失败[/red] {fallback_name}，下载普通质量图: {exc}")
                    if not normal_link:
                        progress.update(page_task, advance=1)
                        continue

                    with session.get(normal_link, stream=True, timeout=REQUEST_TIMEOUT) as response:
                        response.raise_for_status()
                        total = int(response.headers.get("content-length") or 0)
                        progress.update(
                            download_task,
                            description=f"Downloading {fallback_name}",
                            total=total or None,
                            completed=0,
                        )
                        with fallback_path.open("wb") as f:
                            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                                if chunk:
                                    f.write(chunk)
                                    progress.update(download_task, advance=len(chunk))
                        progress.update(page_task, advance=1)
                        successful_count += 1
                except RuntimeError as exc:
                    progress.console.print(f"[yellow]{exc}[/yellow]")
                    progress.update(page_task, advance=1)

    return title, successful_count

def image_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    return suffix if suffix else ".jpg"
def main() -> None:
    session = build_session()
    gallery_url = input_gallery_url()
    title, cnt = get_links_and_download(session, gallery_url)
    console.print(f"成功下载 {cnt} 张图片，保存到: {SAVE_DIR / title}")


if __name__ == "__main__":
    main()
