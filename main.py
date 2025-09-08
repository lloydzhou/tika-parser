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
from lxml import etree, html as lxml_html
from io import StringIO

TIKA_SERVER = os.environ.get("TIKA_SERVER", "http://localhost:9998")
UNPACK_PATH = "/unpack/all"  # only endpoint needed for current tika version
logger = logging.getLogger("tika-fastapi")
logger.setLevel(logging.INFO)

app = FastAPI(title="Tika HTML->Markdown Inliner")


def _ensure_etree(html_or_etree):
    """Return an lxml etree. If given an etree already, return it.
    Use lxml parser for speed when parsing strings.
    """
    if isinstance(html_or_etree, etree._Element):
        return html_or_etree
    html = html_or_etree or ""
    parser = etree.HTMLParser(recover=True, huge_tree=True)
    try:
        # Use forgiving parser that allows huge trees and recovers from malformation
        return lxml_html.fromstring(html, parser=parser)
    except Exception:
        # Fallback for malformed HTML
        try:
            return lxml_html.fragment_fromstring(html, parser=parser)
        except Exception:
            # Last resort: create empty document
            return lxml_html.fromstring("<html><body></body></html>", parser=parser)


def sanitize_html(html: str) -> etree._Element:
    """
    Sanitize HTML to remove problematic control characters and all <title> tags.
    Returns lxml etree element.
    """
    if not html:
        return lxml_html.fromstring("<html><body></body></html>")

    # Remove explicit numeric NUL references (decimal and hex)
    html = re.sub(r'(?i)&#0+;|&#x0+;?', '', html)

    # Remove control characters except \t (09), \n (0A), \r (0D)
    html = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F]', '', html)

    return _ensure_etree(html)


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

    return main_content, embedded


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


def _get_text_content(element):
    """Get text content from an lxml element, similar to BeautifulSoup's get_text()"""
    if element is None:
        return ""
    return "".join(element.itertext()).strip() or ""
    # return (element.text_content() or "").strip()


def _gather_text_from_previous(img_tag, max_ancestors: int = 4) -> str:
    """Gather meaningful text from previous siblings/ancestors using lxml"""
    current = img_tag.getprevious()
    while current is not None:
        text = _get_text_content(current)
        if text and not re.fullmatch(r'[\s\W_]+', text):
            return text
        current = current.getprevious()
    
    # Check ancestors
    parent = img_tag.getparent()
    ancestors_checked = 0
    while parent is not None and ancestors_checked < max_ancestors:
        prev_sibling = parent.getprevious()
        while prev_sibling is not None:
            text = _get_text_content(prev_sibling)
            if text and not re.fullmatch(r'[\s\W_]+', text):
                return text
            prev_sibling = prev_sibling.getprevious()
        parent = parent.getparent()
        ancestors_checked += 1
    
    return ""


def _gather_text_from_next(img_tag, max_ancestors: int = 4) -> str:
    """Gather meaningful text from next siblings/ancestors using lxml"""
    current = img_tag.getnext()
    while current is not None:
        text = _get_text_content(current)
        if text and not re.fullmatch(r'[\s\W_]+', text):
            return text
        current = current.getnext()
    
    # Check ancestors
    parent = img_tag.getparent()
    ancestors_checked = 0
    while parent is not None and ancestors_checked < max_ancestors:
        next_sibling = parent.getnext()
        while next_sibling is not None:
            text = _get_text_content(next_sibling)
            if text and not re.fullmatch(r'[\s\W_]+', text):
                return text
            next_sibling = next_sibling.getnext()
        parent = parent.getparent()
        ancestors_checked += 1
    
    return ""


def _build_title_from_context(img_tag) -> str:
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


def inline_images_in_html(tree):
    """
    Process img tags in lxml etree to generate alt text from context.
    Only generates alt text, doesn't actually inline images for performance.
    Returns the modified tree.
    """
    tree = _ensure_etree(tree)
    
    # Find all img elements using xpath
    imgs = tree.xpath(".//img")

    for img in imgs:
        alt_text = _build_title_from_context(img)
        if alt_text:
            img.set("alt", alt_text)
        else:
            img.set("alt", "")
        
        # Remove title attribute if present
        if "title" in img.attrib:
            del img.attrib["title"]
    
    return tree


# Filename pattern heuristic used by non-content removal
_FILENAME_RE = re.compile(r'^_?\d{6,}\.\w+$|^[\w\-. ]{1,40}\.\w{1,6}$', re.I)


def _normalize_text(s: str) -> str:
    if not s:
        return ""
    return re.sub(r'\s+', ' ', s).strip()


def remove_non_content_blocks(tree, min_header_repeat: int = 3, min_package_group: int = 2, tail_scan_limit: int = 20, max_header_text_len: int = 200):
    """
    Remove attachment-like / repeated header / tail filename-list blocks from HTML using lxml.
    Returns the modified tree.
    """
    tree = _ensure_etree(tree)
    if tree is None:
        return tree

    try:
        # Get body or use tree itself
        body_elements = tree.xpath(".//body")
        body = body_elements[0] if body_elements else tree

        # 1) package-entry handling - find elements with class containing 'package-entry'
        package_tags = []
        for element in tree.xpath(".//*[contains(@class, 'package-entry')]"):
            txt = ""
            # Look for h1, h2, h3 first
            headers = element.xpath(".//h1 | .//h2 | .//h3")
            if headers:
                txt = _normalize_text(_get_text_content(headers[0]))
            else:
                txt = _normalize_text(_get_text_content(element))
            
            if txt and _FILENAME_RE.match(txt):
                package_tags.append((element, txt))

        if len(package_tags) >= min_package_group:
            for element, _ in package_tags:
                try:
                    parent = element.getparent()
                    if parent is not None:
                        parent.remove(element)
                except Exception:
                    pass
        else:
            # Check if package tags are concentrated at document tail
            if package_tags:
                all_children = list(body)
                tail_children = all_children[-tail_scan_limit:] if len(all_children) > tail_scan_limit else all_children
                concentrated = all(
                    any(element == child or element in child.iter() for element, _ in package_tags)
                    for child in tail_children
                )
                if concentrated:
                    for element, _ in package_tags:
                        try:
                            parent = element.getparent()
                            if parent is not None:
                                parent.remove(element)
                        except Exception:
                            pass

        # 3) remove trailing nodes that look like comma/space separated filenames
        body_children = list(body)
        for _ in range(3):
            if not body_children:
                break
            last = body_children[-1]
            txt = _normalize_text(_get_text_content(last))
            if not txt:
                try:
                    parent = last.getparent()
                    if parent is not None:
                        parent.remove(last)
                        body_children.pop()
                except Exception:
                    pass
                continue
                
            tokens = [t for t in re.split(r'[\s,;]+', txt) if t]
            if not tokens:
                break

            should_remove = all(_FILENAME_RE.match(tok) for tok in tokens)
               
            if should_remove:
                try:
                    parent = last.getparent()
                    if parent is not None:
                        parent.remove(last)
                        body_children.pop()
                except Exception:
                    pass
                continue
            break

        # 4) repeated header detection among top-level children
        top_children = list(body)
        consider_top_n = min(8, len(top_children))
        candidates = []
        for child in top_children[:consider_top_n]:
            txt = _normalize_text(_get_text_content(child))
            if txt and len(txt) <= max_header_text_len:
                candidates.append(txt)
                
        if candidates:
            counts = Counter(candidates)
            repeated = {t for t, cnt in counts.items() if cnt >= min_header_repeat}
            if repeated:
                for tag in tree.iter():
                    if hasattr(tag, 'tag'):  # Make sure it's an element, not a comment/text
                        txt = _normalize_text(_get_text_content(tag))
                        if txt in repeated:
                            try:
                                parent = tag.getparent()
                                if parent is not None:
                                    logger.warning("Removing repeated header/footer element: %s %r", txt, tag)
                                    parent.remove(tag)
                            except Exception:
                                pass

        return tree
    except Exception as e:
        logger.error(e)
        return tree


def normalize_tables_use_first_row_as_header(tree):
    """
    For tables that lack any <th>, take the first <tr> as header using lxml.
    Returns the modified tree.
    """
    tree = _ensure_etree(tree)
    if tree is None:
        return tree
        
    try:
        for table in tree.xpath(".//table"):
            # Check if table already has th elements
            if table.xpath(".//th"):
                continue

            # Find first tr
            first_tr = None
            tbody_elements = table.xpath(".//tbody")
            if tbody_elements:
                first_tr_elements = tbody_elements[0].xpath(".//tr")
                if first_tr_elements:
                    first_tr = first_tr_elements[0]
            
            if first_tr is None:
                first_tr_elements = table.xpath(".//tr")
                if first_tr_elements:
                    first_tr = first_tr_elements[0]
                    
            if first_tr is None:
                continue

            # Get cells from first row
            cells = first_tr.xpath(".//td | .//th")
            if not cells:
                continue

            # Create thead element
            thead = etree.Element("thead")
            tr_head = etree.Element("tr")
            
            for cell in cells:
                th = etree.Element("th")
                # Copy all content from cell to th
                th.text = cell.text
                th.tail = cell.tail
                for child in cell:
                    th.append(child)
                # Copy attributes
                for key, value in cell.attrib.items():
                    th.set(key, value)
                tr_head.append(th)
                
            thead.append(tr_head)
            
            # Insert thead at the beginning of table
            table.insert(0, thead)
            
            # Remove the original first row
            parent = first_tr.getparent()
            if parent is not None:
                parent.remove(first_tr)

        return tree
    except Exception as e:
        logger.error(e)
        return tree


def remove_page_header_footer_repeats(tree, min_repeat: int = 3, max_header_text_len: int = 200):
    """
    Detect repeated short blocks at the start or end of per-page containers using lxml.
    Returns the modified tree.
    """
    tree = _ensure_etree(tree)
    if tree is None:
        return tree
        
    try:
        # Find page elements - look for elements with class containing 'page'
        pages = tree.xpath(".//*[contains(@class, 'page')]")
        if not pages:
            return tree

        first_texts = []
        last_texts = []
        page_tags = []
        
        for page in pages:
            # Find all text-bearing elements in this page
            tags = page.xpath(".//p | .//div | .//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6 | .//span")
            
            first_txt = ""
            last_txt = ""
            
            # Find first meaningful text
            for tag in tags:
                txt = _normalize_text(_get_text_content(tag))
                if txt and len(txt) <= max_header_text_len:
                    first_txt = txt
                    break
                    
            # Find last meaningful text
            for tag in reversed(tags):
                txt = _normalize_text(_get_text_content(tag))
                if txt and len(txt) <= max_header_text_len:
                    last_txt = txt
                    break
                    
            first_texts.append(first_txt)
            last_texts.append(last_txt)
            page_tags.append((page, tags))

        fcounts = Counter([t for t in first_texts if t])
        lcounts = Counter([t for t in last_texts if t])

        f_repeated = {t for t, cnt in fcounts.items() if cnt >= min_repeat}
        l_repeated = {t for t, cnt in lcounts.items() if cnt >= min_repeat}

        if not f_repeated and not l_repeated:
            return tree

        # Remove repeated elements
        for page, tags in page_tags:
            for tag in tags:
                try:
                    txt = _normalize_text(_get_text_content(tag))
                except Exception:
                    continue
                    
                if txt in f_repeated or txt in l_repeated:
                    try:
                        parent = tag.getparent()
                        if parent is not None:
                            parent.remove(tag)
                    except Exception:
                        pass

        logger.warning("removed repeated page headers/footers: headers=%s footers=%s", list(f_repeated), list(l_repeated))
        return tree
    except Exception as e:
        logger.error(e)
        return tree


def etree_to_markdown(tree) -> str:
    """
    High-performance conversion from lxml etree to markdown using iterwalk (event-driven).
    Replaces recursive _process_element to avoid truncation issues on large/heterogeneous trees.
    """
    if tree is None:
        return ""

    output = StringIO()

    # safe localname extraction (handles namespaces)
    def _localname(elem):
        try:
            return etree.QName(elem).localname.lower()
        except Exception:
            # fallback if QName fails
            tag = getattr(elem, "tag", "")
            return (tag or "").split("}")[-1].lower() if isinstance(tag, str) else ""

    # stack to track open formatting/list context
    stack = []

    for event, elem in etree.iterwalk(tree, events=("start", "end")):
        # skip comments / PIs / non-elements
        if not isinstance(elem.tag, str):
            continue

        tag = _localname(elem)

        if event == "start":
            # headings
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag[1])
                output.write("\n" + "#" * level + " ")
                if elem.text and elem.text.strip():
                    output.write(elem.text.strip())
            elif tag == "p":
                output.write("\n")
                if elem.text and elem.text.strip():
                    output.write(elem.text.strip())
            elif tag in ("strong", "b"):
                output.write("**")
                stack.append("**")
                if elem.text and elem.text.strip():
                    output.write(elem.text.strip())
            elif tag in ("em", "i"):
                output.write("*")
                stack.append("*")
                if elem.text and elem.text.strip():
                    output.write(elem.text.strip())
            elif tag == "a":
                href = elem.get("href", "")
                output.write("[")
                stack.append(("a", href))
                if elem.text and elem.text.strip():
                    output.write(elem.text.strip())
            elif tag == "img":
                alt = elem.get("alt", "")
                src = elem.get("src", "")
                output.write(f"![{alt}]({src})")
            elif tag in ("ul", "ol"):
                stack.append(tag)  # push list context
            elif tag == "li":
                # find nearest list context
                list_type = next((t for t in reversed(stack) if t in ("ul", "ol")), "ul")
                if list_type == "ul":
                    output.write("\n- ")
                else:
                    # keep numeric lists simple as '-' to avoid expensive index calculations
                    output.write("\n- ")
                if elem.text and elem.text.strip():
                    output.write(elem.text.strip())
            elif tag == "br":
                output.write("\n")
            elif tag == "pre":
                output.write("\n```\n")
                if elem.text:
                    output.write(elem.text)
            else:
                # default: write element.text if present
                if elem.text and elem.text.strip():
                    output.write(elem.text.strip())

        else:  # event == "end"
            if tag in ("strong", "b", "em", "i"):
                # pop matching formatter if present
                if stack:
                    top = stack.pop()
                    output.write(top if isinstance(top, str) else "")
            elif tag == "a":
                if stack:
                    top = stack.pop()
                    if isinstance(top, tuple) and top[0] == "a":
                        href = top[1]
                        output.write(f"]({href})")
            elif tag in ("ul", "ol"):
                # pop list context if present
                if stack and stack[-1] in ("ul", "ol"):
                    stack.pop()
                output.write("\n")
            elif tag == "pre":
                output.write("\n```\n\n")

            # always handle tail text
            if elem.tail and elem.tail.strip():
                output.write(" " + elem.tail.strip())

    result = output.getvalue()
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip()
    return result


@app.post("/", response_class=PlainTextResponse)
async def parse_file(file: UploadFile = File(...)):
    """
    Accept a multipart form file under 'file', call external Tika server to get HTML and attachments.
    Returns Markdown (text/markdown).
    High-performance implementation using lxml for HTML processing and direct etree-to-markdown conversion.
    """
    start_time = asyncio.get_event_loop().time()
    logger.debug("Starting request processing %r", start_time)
    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="empty file")

    logger.debug("Received file: filename=%s content_type=%s size=%d", file.filename, file.content_type, len(body))
    logger.debug("Reading file took %.3f seconds", asyncio.get_event_loop().time() - start_time)

    async with httpx.AsyncClient() as client:
        try:
            # Use /rmeta to obtain the main document content and embedded records.
            html, embedded_records = await fetch_rmeta(client, body)
        except Exception as e:
            logger.exception("failed to fetch rmeta from Tika")
            raise HTTPException(status_code=502, detail=f"tika /rmeta error: {e}")

        logger.debug("Fetched rmeta: main content length=%d, embedded records=%d", len(html) if html else 0, len(embedded_records) if embedded_records else 0)
        logger.debug("Fetching rmeta took %.3f seconds", asyncio.get_event_loop().time() - start_time)

        # parse once into lxml etree and reuse
        tree = sanitize_html(html)

        # remove repeated per-page headers/footers emitted by Tika (e.g. <div class="page"> chunks)
        tree = await asyncio.to_thread(remove_page_header_footer_repeats, tree)

        logger.debug("After remove_page_header_footer_repeats: content length=%d", len(etree.tostring(tree, encoding='unicode')) if tree is not None else 0)
        logger.debug("Processing remove_page_header_footer_repeats took %.3f seconds", asyncio.get_event_loop().time() - start_time)

        # normalize tables: use first row as header if no <th> exists
        tree = await asyncio.to_thread(normalize_tables_use_first_row_as_header, tree)

        logger.debug("After normalize_tables_use_first_row_as_header: content length=%d", len(etree.tostring(tree, encoding='unicode')) if tree is not None else 0)
        logger.debug("Processing normalize_tables_use_first_row_as_header took %.3f seconds", asyncio.get_event_loop().time() - start_time)

        # process img tags to generate alt text
        tree = await asyncio.to_thread(inline_images_in_html, tree)   # only generate alt text
        logger.debug("After inline_images_in_html: content length=%d, attachments inlined=%d", len(etree.tostring(tree, encoding='unicode')) if tree is not None else 0, len(embedded_records) if embedded_records else 0)
        logger.debug("Processing inline_images_in_html took %.3f seconds", asyncio.get_event_loop().time() - start_time)

        # unified removal of attachments/headers/tail filename lists
        tree = await asyncio.to_thread(remove_non_content_blocks, tree)
        logger.debug("After remove_non_content_blocks: content length=%d", len(etree.tostring(tree, encoding='unicode')) if tree is not None else 0)
        logger.debug("Processing remove_non_content_blocks took %.3f seconds", asyncio.get_event_loop().time() - start_time)

        # convert etree -> Markdown using high-performance direct traversal
        markdown = await asyncio.to_thread(etree_to_markdown, tree)
        logger.debug("Converted to markdown: length=%d", len(markdown) if markdown else 0)
        logger.debug("Total processing took %.3f seconds", asyncio.get_event_loop().time() - start_time)

        return PlainTextResponse(content=markdown, media_type="text/markdown; charset=utf-8")