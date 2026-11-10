#!/usr/bin/env python3
"""
extract_md.py - Turn a list of URLs (web pages or PDFs) into Markdown files.

Usage:
    python extract_md.py                 # interactive: pick a .txt file of URLs
    python extract_md.py urls.txt        # non-interactive: use this file
    python extract_md.py failed.txt      # retry the URLs that failed last run

Input:   a plain-text file with ONE raw URL per line (HTML or PDF, any order).
         Blank lines and lines starting with "#" are ignored.
Output:  markdown/<safe-file-name>.md   one Markdown file per URL
         report.md                      summary: successes, failures, reasons
         failed.txt                     failed URLs only, ready to be re-run

Setup:
    pip install -r requirements.txt     # crawl4ai, pymupdf, aiohttp
    crawl4ai-setup                      # installs the headless browser
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Hide Crawl4AI's promotional banner (must be set before importing crawl4ai)
os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")

import aiohttp  # noqa: E402
import pymupdf  # noqa: E402
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # noqa: E402

# ------------------------------- Settings ------------------------------------

DELAY_BETWEEN_REQUESTS = 2.0  # seconds to wait between two URLs (be polite)
CONCURRENCY = 1               # simultaneous requests (keep 1 to stay low-profile)
MIN_CONTENT_SIZE = 500        # characters: below this, extraction is considered suspicious
TIMEOUT = 45                  # per-page timeout, in seconds
OUTPUT_DIR = Path("markdown")
REPORT_FILE = Path("report.md")
FAILED_FILE = Path("failed.txt")

# Browser identity sent to websites. A regular desktop Chrome UA avoids the
# most basic bot filters; change it if you prefer to identify your crawler.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# -----------------------------------------------------------------------------
#  Input file selection (works from any terminal: macOS, Linux, Windows)
# -----------------------------------------------------------------------------


def _clean_path(raw: str) -> Path:
    """
    Normalise a path typed or drag-and-dropped into a terminal.

    Terminals wrap dropped paths differently: quotes on Windows/Linux,
    backslash-escaped spaces on macOS ("My\\ File.txt"). We undo both and
    expand "~" so the user can paste whatever their terminal gives them.
    """
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        raw = raw[1:-1]
    if os.name != "nt":  # on Windows the backslash is the path separator
        raw = re.sub(r"\\(.)", r"\1", raw)
    return Path(raw).expanduser()


def _native_file_dialog() -> Path | None:
    """
    Open the OS file picker and return the chosen file, or None.

    Tries, in order: AppleScript (macOS), zenity / kdialog (Linux desktops),
    then tkinter (bundled with the official Python installers on Windows/macOS).
    Every step fails silently so the caller can fall back to typing a path.
    """
    system = platform.system()
    start_dir = str(Path.cwd())

    try:
        if system == "Darwin" and shutil.which("osascript"):
            script = (
                'POSIX path of (choose file with prompt "Select the .txt file with your URLs" '
                f'default location (POSIX file "{start_dir}"))'
            )
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None

        has_display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        if system == "Linux" and has_display:
            if shutil.which("zenity"):
                cmd = ["zenity", "--file-selection", "--title=Select the .txt file with your URLs",
                       "--file-filter=Text files | *.txt", f"--filename={start_dir}/"]
            elif shutil.which("kdialog"):
                cmd = ["kdialog", "--getopenfilename", start_dir, "*.txt"]
            else:
                cmd = None
            if cmd:
                out = subprocess.run(cmd, capture_output=True, text=True)
                return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        pass

    # Generic fallback (Windows and any Python built with Tk support)
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()                     # no empty main window
        root.attributes("-topmost", True)   # bring the dialog to the front
        chosen = filedialog.askopenfilename(
            title="Select the .txt file with your URLs",
            initialdir=start_dir,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        root.destroy()
        return Path(chosen) if chosen else None
    except Exception:
        return None


def select_url_file() -> Path:
    """
    Interactively ask the user which .txt file contains the URLs.

    The user can:
      - type the number of a .txt file found in the current folder,
      - type "b" to browse with the system file picker,
      - type or drag-and-drop any path into the terminal.
    """
    candidates = sorted(p for p in Path.cwd().glob("*.txt") if not p.name.startswith("requirements"))

    print("\nWhich file contains your URLs? (one raw URL per line)\n")
    for i, p in enumerate(candidates, start=1):
        print(f"  [{i}] {p.name}")
    print("  [b] Browse with the system file picker")
    print("  ... or type / drag-and-drop a path, then press Enter.  [q] to quit\n")

    while True:
        try:
            answer = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)

        if not answer:
            continue
        if answer.lower() in {"q", "quit", "exit"}:
            sys.exit(0)

        if answer.isdigit() and 1 <= int(answer) <= len(candidates):
            return candidates[int(answer) - 1]

        if answer.lower() == "b":
            chosen = _native_file_dialog()
            if chosen and chosen.is_file():
                return chosen
            print("No file picker available or nothing selected - type or drop a path instead.")
            continue

        path = _clean_path(answer)
        if path.is_file():
            return path
        print(f"File not found: {path}  - try again.")


def load_urls(path: Path) -> list[str]:
    """
    Read the URL list. Only raw URLs are accepted: one per line, starting with
    http:// or https://. Comments (#) and blank lines are skipped; anything else
    is reported and ignored so a stray note does not crash the run.
    """
    urls, skipped = [], []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"^https?://\S+$", line):
            urls.append(line)
        else:
            skipped.append((n, line))

    for n, line in skipped:
        print(f"  ! line {n} ignored (not a raw URL): {line[:80]}")

    # Remove duplicates while preserving order
    return list(dict.fromkeys(urls))


# -----------------------------------------------------------------------------
#  Extraction
# -----------------------------------------------------------------------------


def url_to_filename(url: str) -> str:
    """Turn a URL into a safe, readable, collision-free file name."""
    slug = re.sub(r"https?://", "", url)
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", slug).strip("_")[:100]
    # Short hash of the full URL: two long URLs sharing a prefix stay distinct
    suffix = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"{slug}_{suffix}.md"


async def fetch_pdf(session: aiohttp.ClientSession, url: str) -> tuple[str | None, str]:
    """
    Download a PDF and extract its text with PyMuPDF.
    Returns (markdown_text_or_None, status_message).
    """
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            headers={"User-Agent": USER_AGENT},
        ) as resp:
            if resp.status != 200:
                return None, f"HTTP {resp.status}"
            ctype = resp.headers.get("Content-Type", "")
            data = await resp.read()
            # Trust either the Content-Type header or the PDF magic bytes
            if "pdf" not in ctype.lower() and not data.startswith(b"%PDF"):
                return None, "response is not a PDF"

        doc = pymupdf.open(stream=data, filetype="pdf")
        pages = []
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                pages.append(f"## Page {i}\n\n{text}")
        doc.close()

        if not pages:
            return None, "PDF has no extractable text (probably scanned, OCR needed)"
        return "\n\n---\n\n".join(pages), f"OK (PDF, {len(pages)} pages)"
    except Exception as exc:
        return None, f"PDF error: {exc}"


async def process_url(crawler: AsyncWebCrawler, session: aiohttp.ClientSession, url: str) -> tuple[str, bool, str]:
    """Process one URL: PDF -> PyMuPDF, anything else -> Crawl4AI. Returns (url, success, message)."""
    filename = OUTPUT_DIR / url_to_filename(url)
    header = f"# Extraction of {url}\n\n*Source: {url}*\n\n---\n\n"

    # --- Case 1: URL explicitly pointing to a PDF ----------------------------
    if url.lower().split("?")[0].endswith(".pdf"):
        text, msg = await fetch_pdf(session, url)
        if text:
            filename.write_text(header + text, encoding="utf-8")
            return url, True, msg
        return url, False, msg

    # --- Case 2: regular web page (rendered in a headless browser) ----------
    try:
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=TIMEOUT * 1000,
            verbose=False,
        )
        result = await crawler.arun(url=url, config=run_cfg)

        if not result.success:
            return url, False, f"crawl failed: {result.error_message}"

        # Depending on the Crawl4AI version, .markdown is a str or an object
        # exposing a cleaned-up "fit_markdown" variant: prefer the latter.
        md = result.markdown
        content = getattr(md, "fit_markdown", None) or str(md)

        if len(content.strip()) < MIN_CONTENT_SIZE:
            return url, False, f"content too short ({len(content)} chars) - page probably blocked or empty"

        filename.write_text(header + content, encoding="utf-8")
        return url, True, f"OK ({len(content)} chars)"
    except Exception as exc:
        return url, False, f"exception: {exc}"


def write_report(results: list[tuple[str, bool, str]]) -> None:
    """Write report.md and, if needed, failed.txt (re-runnable as input)."""
    success = [r for r in results if r[1]]
    failed = [r for r in results if not r[1]]

    lines = [
        "# Extraction report\n",
        f"- **Total**: {len(results)}",
        f"- **Success**: {len(success)} ({100 * len(success) // max(len(results), 1)} %)",
        f"- **Failed**: {len(failed)}\n",
    ]
    if failed:
        lines.append("## Failures\n")
        lines += [f"- ❌ `{u}` - {m}" for u, _, m in failed]
    lines.append("\n## Details\n")
    lines += [f"- {'✅' if ok else '❌'} `{u}` - {m}" for u, ok, m in results]
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if failed:
        FAILED_FILE.write_text("\n".join(u for u, _, _ in failed) + "\n", encoding="utf-8")
        print(f"\n⚠️  {len(failed)} failures listed in {FAILED_FILE} - retry later with:")
        print(f"    python extract_md.py {FAILED_FILE}")

    print(f"\nFull report: {REPORT_FILE}")


async def run(urls: list[str]) -> None:
    """Crawl every URL sequentially (with a polite delay) and write the report."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    results: list[tuple[str, bool, str]] = []

    browser_cfg = BrowserConfig(headless=True, user_agent=USER_AGENT)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    # One browser and one HTTP session are reused for the whole run
    async with AsyncWebCrawler(config=browser_cfg) as crawler, aiohttp.ClientSession() as session:
        for i, url in enumerate(urls, start=1):
            async with semaphore:
                url, ok, msg = await process_url(crawler, session, url)
                print(f"[{i}/{len(urls)}] {'✅' if ok else '❌'} {url} - {msg}")
                results.append((url, ok, msg))
                if i < len(urls):
                    await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

    write_report(results)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract web pages and PDFs to Markdown from a list of URLs.",
    )
    parser.add_argument(
        "url_file", nargs="?", type=Path,
        help="text file with one raw URL per line (omit it to pick a file interactively)",
    )
    args = parser.parse_args()

    if args.url_file:
        url_file = _clean_path(str(args.url_file))
        if not url_file.is_file():
            parser.error(f"file not found: {url_file}")
    else:
        url_file = select_url_file()

    print(f"\nReading URLs from: {url_file}")
    urls = load_urls(url_file)
    if not urls:
        print("No valid URL found. The file must contain one raw URL per line (https://...).")
        sys.exit(1)
    print(f"{len(urls)} URLs to process.\n")

    asyncio.run(run(urls))


if __name__ == "__main__":
    main()
