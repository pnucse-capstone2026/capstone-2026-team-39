"""Local, network-denied HWP conversion worker. Not a retrieval/parser replacement."""
from __future__ import annotations

import base64
import hashlib
import imghdr
import json
from pathlib import Path
import re
import resource
import sys
from urllib.parse import urlsplit

WARNING = 'HWP 변환 미리보기입니다. 표·글꼴·쪽 배치와 일부 그림은 원본과 다를 수 있습니다. 행·열 관계나 예외 조건이 불명확하면 원본을 확인하고, 확인하지 못한 항목은 체크하지 마세요.'
POLICY = "default-src 'none'; script-src 'none'; connect-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'"
ALLOWED = set('html head body title style div p span br hr table thead tbody tfoot tr td th colgroup col caption ul ol li h1 h2 h3 h4 h5 h6 b strong em i u s sup sub img pre blockquote'.split())
ATTRS = {'class', 'style', 'colspan', 'rowspan', 'width', 'height', 'align', 'valign', 'lang', 'alt'}


def local_asset(folder, value):
    url = urlsplit(value)
    if url.scheme or url.netloc or url.query or url.fragment:
        raise ValueError('nonlocal_asset')
    path = folder / url.path
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError('asset_path_escape')
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('asset_missing_or_large')
    return path.read_bytes()


def safe_css(value):
    # Generated layout CSS only. CSP is an additional no-network boundary.
    value = re.sub(r'/\*.*?\*/', '', value, flags=re.S)
    if re.search(r'url\s*\(|@import|expression\s*\(|[\\<>]|/\*', value, re.I):
        return ''
    return value


def bundle(folder):
    from lxml import etree
    pages = sorted([*folder.glob('*.xhtml'), *folder.glob('*.html')])
    if len(pages) != 1:
        raise ValueError('unexpected_html_output')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    tree = etree.fromstring(pages[0].read_bytes(), parser)
    stats = {'table_count': 0, 'embedded_images': 0, 'omitted_images': 0, 'removed_elements': 0, 'removed_styles': 0}
    for node in list(tree.iter()):
        if not isinstance(node.tag, str):
            if node.getparent() is not None:
                node.getparent().remove(node)
            continue
        node.tag = etree.QName(node).localname.lower()
    # Replace local CSS links before pruning other active/foreign elements.
    for node in list(tree.iter('link')):
        if node.get('rel') == 'stylesheet':
            style = etree.Element('style')
            value = local_asset(folder, node.get('href', '')).decode('utf-8')
            style.text = safe_css(value)
            stats['removed_styles'] += bool(value and not style.text)
            node.getparent().replace(node, style)
    for node in list(tree.iter()):
        if not isinstance(node.tag, str):
            continue
        if node.tag not in ALLOWED:
            stats['removed_elements'] += 1
            if node.tag in ('script', 'iframe', 'object', 'embed', 'svg', 'math'):
                node.clear()
                node.text = '[변환 미지원 요소 · 원본 확인 필요]'
            node.tag = 'span'
        if node.tag == 'table':
            stats['table_count'] += 1
        image_source = node.get('src', '') if node.tag == 'img' else None
        for name in list(node.attrib):
            if name not in ATTRS:
                del node.attrib[name]
        if node.tag == 'style':
            original = node.text or ''
            node.text = safe_css(original)
            stats['removed_styles'] += bool(original and not node.text)
        if node.get('style'):
            original = node.get('style')
            node.set('style', safe_css(original))
            stats['removed_styles'] += bool(original and not node.get('style'))
        if image_source is not None:
            try:
                raw = local_asset(folder, image_source)
                kind = imghdr.what(None, raw)
                if kind not in ('png', 'jpeg', 'gif', 'bmp', 'webp'):
                    raise ValueError('unsupported_image')
                node.set('src', 'data:image/' + kind + ';base64,' + base64.b64encode(raw).decode())
                stats['embedded_images'] += 1
            except (ValueError, OSError):
                node.tag = 'span'
                node.text = '[그림 표시 불가 · 원본 확인 필요]'
                stats['omitted_images'] += 1
    head = tree.find('head')
    if head is None:
        head = etree.Element('head')
        tree.insert(0, head)
    meta = etree.Element('meta', {'http-equiv': 'Content-Security-Policy', 'content': POLICY})
    head.insert(0, meta)
    head.insert(0, etree.Element('meta', {'charset': 'utf-8'}))
    style = etree.SubElement(head, 'style')
    style.text = 'body{margin:16px;background:white;color:#192d4a} .conversion-notice{font:16px/1.6 sans-serif;padding:14px;border-left:4px solid #99630a;background:#fff0d3;margin-bottom:20px} table{max-width:none} img{max-width:100%}'
    body = tree.find('body')
    if body is None or not ''.join(body.itertext()).strip():
        raise ValueError('empty_conversion')
    notice = etree.Element('div', {'class': 'conversion-notice'})
    notice.text = WARNING
    body.insert(0, notice)
    html = '<!doctype html>\n' + etree.tostring(tree, encoding='unicode', method='html')
    return html, stats


def main():
    from hwp5.hwp5html import main as convert
    source, output = map(Path, sys.argv[1:])
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    folder = output.parent / 'converted'
    raw = source.read_bytes()
    sys.argv = ['hwp5html', '--output', str(folder), str(source)]
    convert()
    files = list(folder.rglob('*'))
    if any(p.is_symlink() for p in files) or sum(p.stat().st_size for p in files if p.is_file()) > 64 * 1024 * 1024:
        raise ValueError('conversion_size_limit')
    html, stats = bundle(folder)
    payload = {'html': html, 'stats': stats, 'warning': WARNING, 'converter': 'pyhwp==0.1b15',
               'source_sha256': hashlib.sha256(raw).hexdigest(),
               'html_sha256': hashlib.sha256(html.encode()).hexdigest(),
               'original_modified': False, 'external_upload': False, 'review_state_changed': False}
    with output.open('x', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True)


if __name__ == '__main__':
    main()
