#!/usr/bin/env python3
import os
import io
import re
import zipfile
import base64
import imghdr
import logging
import asyncio
import json
from typing import Counter, Optional, Dict, List

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import PlainTextResponse
from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import markdownify as md

TIKA_SERVER = os.environ.get("TIKA_SERVER", "http://localhost:9998")
UNPACK_PATH = "/unpack/all"  # only endpoint needed for current tika version
logger = logging.getLogger("tika-fastapi")
logger.setLevel(logging.INFO)

app = FastAPI(title="Tika HTML->Markdown Inliner")


def sanitize_html(html: str) -> str:
    """
    Sanitize HTML to remove problematic control characters and all <title> tags.
    """
    if not html:
        return html

    # Remove explicit numeric NUL references (decimal and hex)
    html = re.sub(r'(?i)&#0+;|&#x0+;?', '', html)

    # Remove control characters except \t (09), \n (0A), \r (0D)
    html = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F]', '', html)

    # Parse and remove all <title> tags unconditionally
    try:
        soup = BeautifulSoup(html, "html.parser")
        for title in soup.find_all("title"):
            title.decompose()
        return str(soup)
    except Exception:
        # If parsing fails for any reason, return the cleaned html so far
        return html


async def fetch_attachments_zip(client: httpx.AsyncClient, body: bytes, timeout: int = 60) -> Optional[bytes]:
    """
    Only request the single /unpack/all endpoint (current tika version).
    Return zip bytes if valid zip received, otherwise None.
    """
    try:
        url = TIKA_SERVER.rstrip("/") + UNPACK_PATH
        resp = await client.put(url, content=body, timeout=timeout)
        if resp.status_code == 200 and resp.content:
            try:
                with zipfile.ZipFile(io.BytesIO(resp.content)) as _:
                    return resp.content
            except zipfile.BadZipFile:
                logger.debug("Response from %s not a valid zip", url)
                return None
    except httpx.RequestError:
        logger.debug("request to unpack endpoint failed", exc_info=True)
    return None


async def fetch_rmeta(client: httpx.AsyncClient, body: bytes, timeout: int = 60):
    """
    Fetch structured metadata records from Tika's /rmeta endpoint.
    Returns (main_content_html, embedded_records_list).
    - main_content_html: the X-TIKA:content from the first record (if present), sanitized
    - embedded_records_list: any remaining parsed records (attachments/embedded resources metadata)
    """
    url = TIKA_SERVER.rstrip("/") + "/rmeta"
    headers = {
        "Accept": "application/json",
    }

    resp = await client.put(url, content=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    text = resp.text

    records = []
    try:
        parsed = resp.json()
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            records = [parsed]
    except Exception:
        # fallback: try parse as ndjson (one json object per line)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append(obj)
            except Exception:
                # skip non-json lines
                continue

    main_content = ""
    embedded = []
    if records:
        # take first record as main document output
        main_content = records[0].get("X-TIKA:content", "") or ""
        embedded = records[1:]

    main_content = sanitize_html(main_content)
    return main_content, embedded


def extract_zip_files(zip_bytes: bytes) -> Dict[str, bytes]:
    """
    Return mapping basename -> bytes for all files in zip (recursive).
    If duplicate basenames occur, last wins.
    """
    files = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = os.path.basename(info.filename)
            if not name:
                continue
            try:
                data = z.read(info.filename)
            except Exception:
                continue
            files[name] = data
    return files


def _extract_sentence_fragment(text: str, which: str = "last", max_len: int = 200) -> str:
    """
    Given a text blob, return the last (which='last') or first (which='first') sentence-like fragment.
    Splits on common sentence terminators (Chinese and latin punctuation). Falls back to trimmed text.
    Truncates to max_len.
    """
    if not text:
        return ""
    # normalize whitespace
    s = re.sub(r'\s+', ' ', text).strip()
    # split preserving sentence terminators
    parts = re.split(r'(?<=[。\.!?！？])\s*', s)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        fragment = s
    else:
        fragment = parts[-1] if which == "last" else parts[0]
    if not fragment:
        fragment = s
    # truncate reasonably
    if len(fragment) > max_len:
        fragment = fragment[:max_len].rstrip()
        fragment = fragment + "…"
    return fragment


def _gather_text_from_previous(img_tag: Tag, max_ancestors: int = 4) -> str:
    # 1) previous siblings at same level
    for sib in img_tag.previous_siblings:
        if isinstance(sib, NavigableString):
            txt = sib.strip()
            if txt and not re.fullmatch(r'[\s\W_]+', txt):
                return txt
        elif isinstance(sib, Tag):
            txt = sib.get_text(" ", strip=True)
            if txt and not re.fullmatch(r'[\s\W_]+', txt):
                return txt

    # 2) up the ancestors, check previous siblings of each ancestor
    ancestors = list(img_tag.parents)[:max_ancestors]
    for ancestor in ancestors:
        for prev in getattr(ancestor, "previous_siblings", []):
            if isinstance(prev, NavigableString):
                txt = prev.strip()
                if txt and not re.fullmatch(r'[\s\W_]+', txt):
                    return txt
            elif isinstance(prev, Tag):
                txt = prev.get_text(" ", strip=True)
                if txt and not re.fullmatch(r'[\s\W_]+', txt):
                    return txt
    # 3) fallback: use find_previous(string=True)
    prev_node = img_tag.find_previous(string=True)
    if prev_node:
        ptxt = prev_node.strip()
        if ptxt and not re.fullmatch(r'[\s\W_]+', ptxt):
            return ptxt
    return ""


def _gather_text_from_next(img_tag: Tag, max_ancestors: int = 4) -> str:
    for sib in img_tag.next_siblings:
        if isinstance(sib, NavigableString):
            txt = sib.strip()
            if txt and not re.fullmatch(r'[\s\W_]+', txt):
                return txt
        elif isinstance(sib, Tag):
            txt = sib.get_text(" ", strip=True)
            if txt and not re.fullmatch(r'[\s\W_]+', txt):
                return txt

    ancestors = list(img_tag.parents)[:max_ancestors]
    for ancestor in ancestors:
        for nxt in getattr(ancestor, "next_siblings", []):
            if isinstance(nxt, NavigableString):
                txt = nxt.strip()
                if txt and not re.fullmatch(r'[\s\W_]+', txt):
                    return txt
            elif isinstance(nxt, Tag):
                txt = nxt.get_text(" ", strip=True)
                if txt and not re.fullmatch(r'[\s\W_]+', txt):
                    return txt

    next_node = img_tag.find_next(string=True)
    if next_node:
        ntxt = next_node.strip()
        if ntxt and not re.fullmatch(r'[\s\W_]+', ntxt):
            return ntxt
    return ""


def _build_title_from_context(img_tag: Tag) -> str:
    prev_text = _gather_text_from_previous(img_tag)
    next_text = _gather_text_from_next(img_tag)

    prev_frag = _extract_sentence_fragment(prev_text, which="last", max_len=120) if prev_text else ""
    next_frag = _extract_sentence_fragment(next_text, which="first", max_len=120) if next_text else ""

    if prev_frag and next_frag:
        title = f"{prev_frag} {next_frag}"
    elif prev_frag:
        title = prev_frag
    elif next_frag:
        title = next_frag
    else:
        title = ""

    return title.strip()


def inline_images_in_html(html: str, attachments: dict) -> str:
    """
    attachments: dict basename -> bytes
    returns processed html (string) with <img> src replaced to data: URIs when possible

    Behavior:
      - If attachment is WMF/EMF, try to rasterize to PNG via Pillow and inline PNG.
      - Overwrite alt text with generated context text; remove title.
    """
    if html and html[0] == "\x00":
        html = html.lstrip("\x00")

    soup = BeautifulSoup(html, "html.parser")
    imgs = soup.find_all("img")
    lower_map = {k.lower(): k for k in attachments.keys()}

    for img in imgs:
        src = img.get("src", "").strip()
        if not src or src.startswith("data:"):
            continue

        candidates = set()
        candidates.add(src)
        candidates.add(os.path.basename(src))
        if "?" in src:
            candidates.add(os.path.basename(src.split("?", 1)[0]))
        if "#" in src:
            candidates.add(os.path.basename(src.split("#", 1)[0]))
        if ":" in src:
            candidates.add(src.split(":", 1)[-1])
        if "/" in src:
            candidates.add(src.split("/")[-1])

        matched_data = None
        matched_name = None

        for c in candidates:
            cbase = os.path.basename(c).lower()
            if cbase in lower_map:
                matched_name = lower_map[cbase]
                matched_data = attachments[matched_name]
                break

        if matched_data:
            # Only inline common raster images that imghdr recognizes. Drop other formats (e.g. WMF/EMF).
            try:
                img_type = imghdr.what(None, h=matched_data)
            except Exception:
                img_type = None

            if not img_type:
                # cannot recognize raster image -> remove the <img> tag entirely
                try:
                    img.decompose()
                except Exception:
                    pass
                continue

            final_bytes = matched_data
            final_mime = f"image/{img_type}"

            # inline as data URI
            try:
                b64 = base64.b64encode(final_bytes).decode("ascii")
                data_uri = f"data:{final_mime};base64,{b64}"
                img["src"] = data_uri
            except Exception:
                logger.exception("failed to base64 image for %s", matched_name)
                try:
                    img.decompose()
                except Exception:
                    pass
                continue

            # remove any existing title attribute to ensure no title output
            if "title" in img.attrs:
                del img.attrs["title"]

            # ALWAYS replace alt text with generated context text (do not preserve existing alt)
            alt_text = _build_title_from_context(img)
            img["alt"] = alt_text if alt_text else ""

            continue

        # If no match in attachments, do NOT try to fetch remote images.
        # Leave src unchanged.
    return str(soup)


# Filename pattern heuristic used by non-content removal
_FILENAME_RE = re.compile(r'^_?\d{6,}\.\w+$|^[\w\-. ]{1,40}\.\w{1,6}$', re.I)


def _normalize_text(s: str) -> str:
    if not s:
        return ""
    return re.sub(r'\s+', ' ', s).strip()


def remove_non_content_blocks(
    html: str,
    attachments: Optional[dict] = None,
    min_header_repeat: int = 3,
    min_package_group: int = 2,
    tail_scan_limit: int = 20,
    max_header_text_len: int = 200,
) -> str:
    """
    Remove attachment-like / repeated header / tail filename-list blocks from HTML.

    Heuristics performed (conservative defaults):
      1) Find tags with class containing 'package-entry' whose H1/H2/H3 text looks like a filename.
         If >= min_package_group such nodes exist, remove them all.
         Otherwise, if they are concentrated at document tail, remove them.
      2) Remove any tag (<a>, <p>, <div>, <span>, headings, <li>) whose visible text exactly matches
         a basename in attachments (case-insensitive). If attachments is None/empty this step is skipped.
      3) Remove trailing nodes at document end that look like comma/space-separated filenames that are
         all in attachments (or look like filename pattern when attachments not provided).
      4) Detect repeated short top-level blocks (first consider_top_n children) and remove those whose
         normalized text appears >= min_header_repeat times (useful to drop page headers).
         Only consider short texts (<= max_header_text_len) to avoid removing actual paragraphs.

    Returns modified html string.
    """
    if not html:
        return html

    try:
        soup = BeautifulSoup(html, "html.parser")
        body = soup.body or soup

        # attachments basenames lowercase set for quick membership test
        attachments_basenames = set()
        if attachments:
            attachments_basenames = {name.lower() for name in attachments.keys()}

        # 1) package-entry handling
        package_tags = []
        for tag in soup.find_all(class_=lambda c: c and 'package-entry' in str(c).lower()):
            txt = ""
            for h in tag.find_all(['h1', 'h2', 'h3'], limit=1):
                txt = _normalize_text(h.get_text(" ", strip=True))
                break
            if not txt:
                txt = _normalize_text(tag.get_text(" ", strip=True))
            if txt and _FILENAME_RE.match(txt):
                package_tags.append((tag, txt))

        if len(package_tags) >= min_package_group:
            for tag, _ in package_tags:
                try:
                    tag.decompose()
                except Exception:
                    pass
        else:
            if package_tags:
                tail_children = [c for c in (body.contents or []) if isinstance(c, Tag)][-tail_scan_limit:]
                concentrated = all(
                    any(tag is child or tag in child.find_all(True) for child, _ in package_tags)
                    for child in tail_children
                )
                if concentrated:
                    for tag, _ in package_tags:
                        try:
                            tag.decompose()
                        except Exception:
                            pass

        # 2) remove nodes whose visible text exactly equals an attachment basename
        if attachments_basenames:
            for tag in soup.find_all(['a', 'p', 'div', 'span', 'li'] + [f'h{i}' for i in range(1,7)]):
                txt = _normalize_text(tag.get_text(" ", strip=True))
                if not txt:
                    continue
                if txt.lower() in attachments_basenames:
                    try:
                        tag.decompose()
                    except Exception:
                        pass
                    continue
                if tag.name == 'a':
                    href = tag.get('href', '')
                    if href:
                        href_basename = href.split('?', 1)[0].split('#', 1)[0].split('/')[-1].lower()
                        if href_basename in attachments_basenames:
                            try:
                                tag.decompose()
                            except Exception:
                                pass
                            continue

        # 3) remove trailing nodes that look like comma/space separated filenames
        body_children = [c for c in (body.contents or []) if isinstance(c, Tag)]
        for _ in range(3):
            if not body_children:
                break
            last = body_children[-1]
            txt = _normalize_text(last.get_text(" ", strip=True))
            if not txt:
                try:
                    last.decompose()
                except Exception:
                    pass
                body_children.pop()
                continue
            tokens = [t for t in re.split(r'[\s,;]+', txt) if t]
            if not tokens:
                break

            if attachments_basenames:
                if all(tok.lower() in attachments_basenames for tok in tokens):
                    try:
                        last.decompose()
                    except Exception:
                        pass
                    body_children.pop()
                    continue
            else:
                if all(_FILENAME_RE.match(tok) for tok in tokens):
                    try:
                        last.decompose()
                    except Exception:
                        pass
                    body_children.pop()
                    continue
            break

        # 4) repeated header detection among top-level children
        top_children = [c for c in body.contents if isinstance(c, Tag)]
        consider_top_n = min(8, len(top_children))
        candidates = []
        for child in top_children[:consider_top_n]:
            txt = _normalize_text(child.get_text(" ", strip=True))
            if txt and len(txt) <= max_header_text_len:
                candidates.append(txt)
        if candidates:
            counts = Counter(candidates)
            repeated = {t for t, cnt in counts.items() if cnt >= min_header_repeat}
            if repeated:
                for tag in soup.find_all():
                    if not isinstance(tag, Tag):
                        continue
                    txt = _normalize_text(tag.get_text(" ", strip=True))
                    if txt in repeated:
                        try:
                            tag.decompose()
                        except Exception:
                            pass

        return str(soup)
    except Exception:
        return html


def normalize_tables_use_first_row_as_header(html: str) -> str:
    """
    For tables that lack any <th>, take the first <tr> as header:
    - create a <thead> with that row's cells converted to <th>
    - remove the original first row from the table body
    This makes markdownify treat the first row as the header.
    """
    if not html:
        return html
    try:
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            # skip if any <th> already exists
            if table.find("th"):
                continue

            # find the first tr in the table
            first_tr = None
            # prefer rows inside tbody if present
            tbody = table.find("tbody")
            if tbody:
                first_tr = tbody.find("tr")
            if not first_tr:
                first_tr = table.find("tr")
            if not first_tr:
                continue

            # collect cells from that row
            cells = first_tr.find_all(["td", "th"])
            if not cells:
                continue

            thead = soup.new_tag("thead")
            tr_head = soup.new_tag("tr")
            for cell in cells:
                th = soup.new_tag("th")
                # move contents into the new th
                for c in list(cell.contents):
                    th.append(c)
                tr_head.append(th)
            thead.append(tr_head)

            # insert thead at the beginning of the table
            table.insert(0, thead)

            # remove the original first row
            first_tr.decompose()

        return str(soup)
    except Exception:
        # on any parsing error, return original html unchanged
        return html


def remove_page_header_footer_repeats(html: str, min_repeat: int = 3, max_header_text_len: int = 200) -> str:
    """
    Detect repeated short blocks at the start or end of per-page containers (e.g. <div class="page">)
    and remove them when they occur on at least `min_repeat` pages.

    This helps remove headers/footers that Tika emits inside each page block.
    """
    if not html:
        return html
    try:
        soup = BeautifulSoup(html, "html.parser")
        # find page containers (class contains 'page')
        pages = [p for p in soup.find_all(True, class_=lambda c: c and 'page' in str(c).lower())]
        if not pages:
            # fallback: if no page divs, nothing to do
            return html

        first_texts = []
        last_texts = []
        page_tags = []
        for p in pages:
            # collect candidate first/last meaningful tag text in this page
            tags = [t for t in p.find_all(['p','div','h1','h2','h3','h4','h5','h6','span'], recursive=True)]
            first_txt = ""
            last_txt = ""
            for t in tags:
                ttxt = _normalize_text(t.get_text(" ", strip=True))
                if ttxt and len(ttxt) <= max_header_text_len:
                    first_txt = ttxt
                    break
            for t in reversed(tags):
                ttxt = _normalize_text(t.get_text(" ", strip=True))
                if ttxt and len(ttxt) <= max_header_text_len:
                    last_txt = ttxt
                    break
            first_texts.append(first_txt)
            last_texts.append(last_txt)
            page_tags.append((p, tags))

        from collections import Counter as _Counter
        fcounts = _Counter([t for t in first_texts if t])
        lcounts = _Counter([t for t in last_texts if t])

        f_repeated = {t for t, cnt in fcounts.items() if cnt >= min_repeat}
        l_repeated = {t for t, cnt in lcounts.items() if cnt >= min_repeat}

        if not f_repeated and not l_repeated:
            return html

        # remove matching tags within each page
        for p, tags in page_tags:
            for tag in tags:
                try:
                    ttxt = _normalize_text(tag.get_text(" ", strip=True))
                except Exception:
                    continue
                if ttxt in f_repeated or ttxt in l_repeated:
                    try:
                        tag.decompose()
                    except Exception:
                        pass
        logger.warn("removed repeated page headers/footers: headers=%s footers=%s", list(f_repeated), list(l_repeated))
        return str(soup)
    except Exception:
        return html


@app.post("/", response_class=PlainTextResponse)
async def parse_file(file: UploadFile = File(...)):
    """
    Accept a multipart form file under 'file', call external Tika server to get HTML and attachments.
    Returns Markdown (text/markdown).
    Only inlines images that are present in Tika's /unpack/all attachments; remote images are left as-is.
    Fetch attachments only when the HTML actually contains <img> tags.
    """
    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="empty file")

    async with httpx.AsyncClient() as client:
        try:
            # Use /rmeta to obtain the main document content and embedded records.
            # This avoids the problem where /tika's HTML may include text extracted from embedded
            # resources (e.g., WMF formulas) appended into the main content.
            html, embedded_records = await fetch_rmeta(client, body)
        except Exception as e:
            logger.exception("failed to fetch rmeta from Tika")
            raise HTTPException(status_code=502, detail=f"tika /rmeta error: {e}")

        # Build a simple map of embedded resource basenames -> metadata from rmeta records
        embedded_meta = {}
        for rec in (embedded_records if 'embedded_records' in locals() and embedded_records else []):
            # resourceName is common; fallback to embedded path if present
            rname = rec.get('resourceName') or rec.get('X-TIKA:embedded_resource_path') or rec.get('X-TIKA:embedded_resource_name')
            if rname:
                b = os.path.basename(rname).lower()
                embedded_meta[b] = rec

        # check if HTML contains any <img> tags; only then fetch attachments
        soup_check = BeautifulSoup(html or "", "html.parser")
        has_img = bool(soup_check.find("img"))

        attachments = {}
        if has_img:
            zip_bytes = None
            try:
                zip_bytes = await fetch_attachments_zip(client, body)
            except Exception:
                logger.exception("failed to fetch attachments from Tika (ignored)")

            if zip_bytes:
                # extraction is CPU/IO bound -> run in thread to avoid blocking loop
                attachments = await asyncio.to_thread(extract_zip_files, zip_bytes)

        # inline images from attachments ONLY (if attachments empty, inline_images_in_html will leave imgs unchanged)
        html_inlined = await asyncio.to_thread(inline_images_in_html, html, attachments)

        # unified removal of attachments/headers/tail filename lists
        if attachments:
            html_inlined = await asyncio.to_thread(remove_non_content_blocks, html_inlined, attachments)
        else:
            html_inlined = await asyncio.to_thread(remove_non_content_blocks, html_inlined, None)

        # remove repeated per-page headers/footers emitted by Tika (e.g. <div class="page"> chunks)
        html_inlined = await asyncio.to_thread(remove_page_header_footer_repeats, html_inlined)

        # normalize tables: use first row as header if no <th> exists
        html_inlined = await asyncio.to_thread(normalize_tables_use_first_row_as_header, html_inlined)

        # convert HTML -> Markdown
        # set autolinks=False so markdownify will emit explicit [url](url) instead of <url>
        markdown = await asyncio.to_thread(md, html_inlined, heading_style="ATX", autolinks=False)

        return PlainTextResponse(content=markdown, media_type="text/markdown; charset=utf-8")