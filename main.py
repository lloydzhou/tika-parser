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
from typing import Counter, Optional, Dict, List, BinaryIO

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


def replace_pict_with_drawing(bytes: bytes) -> bytes:
    """
    在内存中处理 DOCX 文件，将 w:pict 替换为 w:drawing。

    Args:
        input_bytes: 输入的 DOCX 文件字节内容。

    Returns:
        处理后的 DOCX 文件字节内容，或者原始的 input_bytes（如果处理失败）。
    """
    try:
        # 创建内存中的 Zip 文件对象
        with zipfile.ZipFile(io.BytesIO(bytes), 'r') as in_zip:
            # 在内存中创建输出 Zip 文件
            out_buffer = io.BytesIO()
            with zipfile.ZipFile(out_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as out_zip:
                # 获取所有文件列表
                file_list = in_zip.namelist()
                # 处理文档主体
                if 'word/document.xml' in file_list:
                    # 读取文档内容
                    with in_zip.open('word/document.xml') as doc_file:
                        doc_content = doc_file.read()
                    
                    # 使用 lxml 解析 XML，并保留命名空间
                    namespaces = {
                        'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
                        'v': 'urn:schemas-microsoft-com:vml',
                        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
                    }
                    
                    # 解析 XML，注意需要获取根元素
                    root = etree.fromstring(doc_content)
                    
                    # 查找所有 w:pict 元素
                    # 使用 XPath 表达式，并传入命名空间映射
                    pict_elements = root.xpath('.//w:pict', namespaces=namespaces)
                    
                    for pict_element in pict_elements:
                        # 获取父元素 - 这就是 lxml 方便的地方
                        parent = pict_element.getparent()
                        if parent is not None:
                            # 创建一个新的 w:drawing 元素（这里需要你根据实际情况构建）
                            # 例如，可以从 pict_element 中提取信息来构建 drawing_element
                            drawing_element = create_drawing_from_pict(pict_element, namespaces)
                            if drawing_element is not None:
                                # 将 pict_element 替换为新创建的 drawing_element
                                parent.replace(pict_element, drawing_element)
                    
                    # 将修改后的 XML 序列化回字节
                    modified_doc_content = etree.tostring(root, encoding='UTF-8', xml_declaration=True)
                    # 将修改后的 document.xml 写回输出 Zip
                    out_zip.writestr('word/document.xml', modified_doc_content)

                # 复制其他未修改的文件
                for file_name in file_list:
                    if file_name != 'word/document.xml':
                        with in_zip.open(file_name) as source_file:
                            out_zip.writestr(file_name, source_file.read())

        return out_buffer.getvalue()
    except Exception as e:
        logger.exception("Error replacing pict with drawing: %s", e)
        return bytes  # 返回原始内容以防出错


def create_drawing_from_pict(pict_element, namespaces):
    """
    根据 w:pict 元素创建一个新的 w:drawing 元素。
    这是一个示例函数，你需要根据实际的 DOCX 结构和图片信息来完善它。

    Args:
        pict_element: w:pict XML 元素。
        namespaces: 命名空间映射。

    Returns:
        一个新创建的 w:drawing 元素，如果无法转换则返回 None。
    """
    # 这个函数需要你根据实际的 DOCX 文件结构和图片转换逻辑来实现
    # 例如，从 pict_element 中提取图片的引用、尺寸、位置等信息
    # 然后使用 lxml 的 Element 或 SubElement 方法构建一个完整的 w:drawing 元素

    # 假设我们从一个 pict 元素中找到了一个图片的引用 ID
    imagedata = pict_element.find('.//v:imagedata', namespaces)
    if imagedata is not None:
        r_id = imagedata.get('{%s}id' % namespaces['r'])
        if r_id:
            # 这里是简化的 drawing 元素创建，实际结构要复杂得多
            # 你需要参考 Office Open XML 标准来构建正确的结构
            drawing_xml = f'''
            <w:drawing xmlns:w="{namespaces['w']}">
                <wp:inline distT="0" distB="0" distL="0" distR="0" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
                    <wp:extent cx="3048000" cy="2286000"/>
                    <wp:effectExtent l="0" t="0" r="0" b="0"/>
                    <wp:docPr id="1" name="Converted Image"/>
                    <wp:cNvGraphicFramePr>
                        <a:graphicFrameLocks noChangeAspect="1" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>
                    </wp:cNvGraphicFramePr>
                    <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
                        <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
                            <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
                                <pic:nvPicPr>
                                    <pic:cNvPr id="0" name="Converted Picture"/>
                                    <pic:cNvPicPr/>
                                </pic:nvPicPr>
                                <pic:blipFill>
                                    <a:blip r:embed="{r_id}" xmlns:r="{namespaces['r']}"/>
                                    <a:stretch>
                                        <a:fillRect/>
                                    </a:stretch>
                                </pic:blipFill>
                                <pic:spPr>
                                    <a:xfrm>
                                        <a:off x="0" y="0"/>
                                        <a:ext cx="3048000" cy="2286000"/>
                                    </a:xfrm>
                                    <a:prstGeom prst="rect">
                                        <a:avLst/>
                                    </a:prstGeom>
                                </pic:spPr>
                            </pic:pic>
                        </a:graphicData>
                    </a:graphic>
                </wp:inline>
            </w:drawing>
            '''
            try:
                # 解析 XML 字符串为元素
                new_element = etree.fromstring(drawing_xml)
                return new_element
            except etree.XMLSyntaxError as e:
                print(f"创建 drawing 元素时 XML 语法错误: {e}")
                return None
    return None


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
    page_size = 0
    embedded = []
    if records:
        # take first record as main document output
        main_content = records[0].get("X-TIKA:content", "") or ""
        page_size_str = (
            records[0].get("xmpTPg:NPages")
            or records[0].get("Page-Count")
            or records[0].get("meta:page-count")
            or "0"
        )
        try:
            page_size = int(page_size_str)
        except (ValueError, TypeError):
            page_size = 0
        embedded = records[1:]

    return main_content, page_size, embedded

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


def _get_text_content(element):
    """Get text content from an lxml element, similar to BeautifulSoup's get_text().
    Treat elements containing images as having content (using img alt/title/src) so image-only blocks
    are not considered empty and won't be removed by heuristics.
    """
    if element is None:
        return ""

    # Prefer visible text nodes
    text = "".join(element.itertext()).strip()
    if text:
        return text

    # If element itself or any descendant is an <img>, prefer using its alt/title/src as the text.
    try:
        # fast path: element is an img
        tag = getattr(element, "tag", "")
        if isinstance(tag, str) and tag.split("}")[-1].lower() == "img":
            alt = (element.get("alt") or element.get("title") or element.get("src") or "").strip()
            return alt if alt else "[image]"

        # otherwise look for descendant imgs (prefer the first/closest)
        imgs = element.xpath(".//img") if hasattr(element, "xpath") else []
        if imgs:
            first = imgs[0]
            alt = (first.get("alt") or first.get("title") or first.get("src") or "").strip()
            return alt if alt else "[image]"
    except Exception:
        # fall through to more thorough scanning below
        pass
    return ""


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

    title = f"上文：{prev_frag or ''};下文：{next_frag or ''}"
    # Normalize whitespace, remove newlines and strip markdown-sensitive/control characters
    clean = title.strip()
    # replace newlines and carriage returns with spaces
    clean = re.sub(r'[\r\n]+', ' ', clean)
    # remove characters that commonly break Markdown image/link syntax or can produce unexpected formatting
    # (square/bracket, parentheses, angle brackets, backticks, asterisks, underscores, tildes, exclamation)
    clean = re.sub(r'[\[\]\(\)<>`*_~!]', ' ', clean)
    # remove other control characters
    clean = re.sub(r'[\x00-\x1F\x7F]', '', clean)
    # collapse multiple whitespace to single space and trim
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def inline_images_in_html(tree, attachments: Dict[str, bytes]):
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

        # Remove title attribute if present
        if "title" in img.attrib:
            del img.attrib["title"]

        # Inline image data if available
        img_name = img.get("src")
        if ":" in img_name:
            # embedded:image5.jpeg
            img_name = img_name.split(":", 1).pop()
        if img_name and img_name in attachments:
            img_data = attachments[img_name]
            img.set("src", f"data:image/png;base64,{base64.b64encode(img_data).decode()}")
    
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
                        logger.warn("Removing package-entry element: %r", element)
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
                                logger.warn("Removing package-entry element: %r", element)
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
                        logger.warn("Removing trailing element: %r", last)
                        parent.remove(last)
                        body_children.pop()
                except Exception:
                    pass
                continue

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
    # Special handling for .docx files to replace w:pict with w:drawing
    if file.filename and file.filename.lower().endswith(".docx"):
        try:
            body = replace_pict_with_drawing(body)
        except Exception as e:
            logger.exception("Error processing file for pict->drawing replacement: %s", e)

    logger.debug("Received file: filename=%s content_type=%s size=%d", file.filename, file.content_type, len(body))
    logger.debug("Reading file took %.3f seconds", asyncio.get_event_loop().time() - start_time)

    async with httpx.AsyncClient() as client:
        try:
            # Use /rmeta to obtain the main document content and embedded records.
            # Start rmeta and attachments fetch in parallel to save time
            rmeta_task = asyncio.create_task(fetch_rmeta(client, body))
            attach_task = asyncio.create_task(fetch_attachments_zip(client, body))

            # Await rmeta (main document) first
            html, page_size, embedded_records = await rmeta_task

            # Always await attachments (no heuristic) so both tasks run in parallel
            attachments = {}
            try:
                zip_bytes = await attach_task
                # If we got a zip, extract in a thread to avoid blocking the event loop
                if zip_bytes:
                    try:
                        attachments = await asyncio.to_thread(extract_zip_files, zip_bytes)
                    except Exception:
                        logger.exception("failed to extract attachments (ignored)")
                        attachments = {}
            except Exception:
                logger.exception("failed to fetch attachments from Tika (ignored)")

        except Exception as e:
            logger.exception("failed to fetch rmeta from Tika")
            raise HTTPException(status_code=502, detail=f"tika /rmeta error: {e}")
        logger.debug("Fetched rmeta: main content length=%d, embedded records=%d", len(html) if html else 0, len(embedded_records) if embedded_records else 0)
        logger.debug("Fetching rmeta took %.3f seconds", asyncio.get_event_loop().time() - start_time)

        # parse once into lxml etree and reuse
        tree = sanitize_html(html)

        # remove repeated per-page headers/footers emitted by Tika (e.g. <div class="page"> chunks)
        min_repeat = max(3, page_size // 2) if page_size and page_size > 0 else 3
        tree = await asyncio.to_thread(remove_page_header_footer_repeats, tree, min_repeat=min_repeat)

        logger.debug("After remove_page_header_footer_repeats: content length=%d", len(etree.tostring(tree, encoding='unicode')) if tree is not None else 0)
        logger.debug("Processing remove_page_header_footer_repeats took %.3f seconds", asyncio.get_event_loop().time() - start_time)

        # normalize tables: use first row as header if no <th> exists
        tree = await asyncio.to_thread(normalize_tables_use_first_row_as_header, tree)

        logger.debug("After normalize_tables_use_first_row_as_header: content length=%d", len(etree.tostring(tree, encoding='unicode')) if tree is not None else 0)
        logger.debug("Processing normalize_tables_use_first_row_as_header took %.3f seconds", asyncio.get_event_loop().time() - start_time)

        has_img = bool(tree.xpath(".//img"))
        if has_img:
            # process img tags to generate alt text
            tree = await asyncio.to_thread(inline_images_in_html, tree, attachments)   # only generate alt text
            logger.debug("After inline_images_in_html: content length=%d, attachments inlined=%d", len(etree.tostring(tree, encoding='unicode')) if tree is not None else 0, len(attachments) if attachments else 0)
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