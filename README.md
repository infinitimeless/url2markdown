# url2markdown

Turn a list of URLs (web pages **and** PDFs) into clean Markdown files, one per URL.
It's handy for feeding documentation, articles or a whole website into an LLM, a RAG
pipeline or a note-taking app.

- **Web pages** are rendered in a headless browser ([Crawl4AI](https://github.com/unclecode/crawl4ai)), so JavaScript-heavy sites work.
- **PDFs** are downloaded and their text is extracted with [PyMuPDF](https://pymupdf.readthedocs.io/).
- It's **polite by default**: one request at a time, with a 2-second pause between URLs.
- You get a **report** (`report.md`) plus a **`failed.txt`** that you can feed straight back to the script to retry.

---

## Installation

Requires **Python 3.10+**.

```bash
git clone https://github.com/infinitimeless/url2markdown.git
cd url2markdown

python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
crawl4ai-setup        # downloads the headless browser used for rendering pages
```

`requirements.lock.txt` lists the exact versions this was tested with
(`pip install -r requirements.lock.txt` for a reproducible install).

---

## Usage

### 1. Prepare a URL file

Create a plain `.txt` file that contains **only raw URLs, one per line**:

```text
https://example.com/
https://example.com/docs/getting-started
https://example.com/files/whitepaper.pdf
```

> ⚠️ **Raw URLs only.** No titles, bullets (`-`, `*`), numbering, Markdown links
> (`[text](url)`), commas or extra text on the line. Each line must start with
> `http://` or `https://`.
> Blank lines and lines starting with `#` are ignored (use them for comments).
> Any other line is skipped with a warning. Duplicates are removed.

See [`urls.example.txt`](urls.example.txt) for a template.

### 2. Run the script

```bash
python extract_md.py
```

The script asks which file to use. This works in any terminal (macOS, Linux, Windows):

```
Which file contains your URLs? (one raw URL per line)

  [1] urls.example.txt
  [2] my_site.txt
  [b] Browse with the system file picker
  ... or type / drag-and-drop a path, then press Enter.  [q] to quit
```

- Type a **number** to pick a `.txt` file from the current folder.
- Type **`b`** to open your OS file picker (Finder on macOS, zenity/kdialog on Linux
  desktops, the standard dialog on Windows). If no graphical picker is available,
  for example over SSH, you just type the path instead.
- Or **type or drag-and-drop** the file into the terminal window and press Enter.

You can also pass the file directly, which is useful in scripts or cron jobs:

```bash
python extract_md.py path/to/urls.txt
```

### 3. Results

| Output              | Content                                                    |
|---------------------|------------------------------------------------------------|
| `markdown/*.md`     | One Markdown file per URL (name built from the URL)        |
| `report.md`         | Totals, successes, failures and the reason for each one    |
| `failed.txt`        | Only the URLs that failed, so you can run them again       |

To retry the failures later:

```bash
python extract_md.py failed.txt
```

### Settings

Edit the constants at the top of [`extract_md.py`](extract_md.py):

| Setting                  | Default    | Meaning                                                  |
|--------------------------|------------|----------------------------------------------------------|
| `DELAY_BETWEEN_REQUESTS` | `2.0`      | Seconds to wait between URLs                             |
| `CONCURRENCY`            | `1`        | Simultaneous requests                                    |
| `MIN_CONTENT_SIZE`       | `500`      | Pages shorter than this count as failed (blocked/empty)  |
| `TIMEOUT`                | `45`       | Per-page timeout in seconds                              |
| `OUTPUT_DIR`             | `markdown` | Output folder                                            |
| `USER_AGENT`             | Chrome UA  | Browser identity sent to websites                        |

### Limitations

- Scanned PDFs (images only) have no text layer, so they fail with an "OCR needed" message.
- Pages behind a login, a paywall or strong anti-bot protection may come back empty.
- A PDF is only detected when its URL ends in `.pdf`. Other URLs are treated as web pages.

---

## Tip: generate the URL list with a vision-enabled LLM

Writing hundreds of URLs by hand is tedious. Most websites build their URLs from a
predictable pattern: section, category, slug, page number, year, ID and so on. A
**vision-enabled LLM** (Claude, GPT-4o, Gemini, or a local model such as Qwen-VL or
LLaVA in LM Studio/Ollama) can work that pattern out from a few screenshots and
example URLs, then write the full list for you.

### What to give the model

1. **Screenshots** of the site's navigation: the menu, a sitemap or index page, the
   category listings, the pagination and any dropdowns or filters. These show the model
   *which pages exist*.
2. **3 to 10 example URLs** of pages you've opened, ideally from different sections or
   levels, so the model can see the pattern. Pair each URL with its screenshot when
   you can.
3. **What you want**: the whole site, one section, only the PDFs, a range of years, and so on.

### Prompt template

Attach your screenshots and paste:

```text
You are helping me build an input file for a scraper.

Attached are screenshots of the website <SITE NAME> (navigation menu, section
pages, pagination). Here are real URLs of some of the pages shown:

https://example.com/docs/guide/introduction
https://example.com/docs/guide/installation
https://example.com/docs/api/authentication
https://example.com/blog/2024/03/release-notes-v2
https://example.com/blog/page/2

Task:
1. Work out the rules the site uses to build its URLs (base domain, path
   segments, slug formatting, pagination, dates, IDs).
2. Using the pages, titles and menu entries visible in the screenshots, list
   every URL of <SCOPE: e.g. "the whole /docs section">.
3. Only use slugs you can read on the screenshots or infer with high confidence.
   Do not invent pages.

Output format, strictly:
- A single plain-text code block that I will save as urls.txt
- One raw URL per line, starting with https://
- No numbering, bullets, titles, Markdown links or comments
- No duplicates

After the code block, briefly list the URL rules you inferred and any URLs you
were unsure about.
```

### Then

1. Copy the code block into a file, for example `my_site.txt`.
2. **Spot-check a few URLs** in your browser. LLMs can guess slugs that look plausible
   but don't exist. These won't break anything, they'll just end up in `failed.txt`.
3. Run `python extract_md.py` and pick the file.

> 💡 If the site has a `sitemap.xml` (try `https://<site>/sitemap.xml` or check
> `robots.txt`), you can also paste its contents to the LLM, or just extract the
> `<loc>` entries, which is often faster and more accurate than screenshots.

---

## Responsible use

Respect each website's terms of service and `robots.txt`, keep the request rate low
(the defaults are deliberately slow), and only redistribute content you have the right
to share.

## License

[MIT](LICENSE)
