import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

XLSX_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL_NS = '{http://schemas.openxmlformats.org/package/2006/relationships}'
DOC_REL_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

def read_shared_strings(zf):
    if 'xl/sharedStrings.xml' not in zf.namelist():
        return []
    root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
    values = []
    for si in root.findall(f'{XLSX_NS}si'):
        parts = []
        for t in si.iter(f'{XLSX_NS}t'):
            parts.append(t.text or '')
        values.append(''.join(parts))
    return values

def cell_text(cell, shared):
    typ = cell.attrib.get('t')
    if typ == 's':
        v = cell.find(f'{XLSX_NS}v')
        if v is None or v.text is None:
            return ''
        idx = int(v.text)
        return shared[idx] if idx < len(shared) else ''
    if typ == 'inlineStr':
        return ''.join(t.text or '' for t in cell.iter(f'{XLSX_NS}t'))
    v = cell.find(f'{XLSX_NS}v')
    return '' if v is None or v.text is None else v.text

def col_index(ref):
    letters = ''.join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch.upper()) - 64
    return n

path = Path(sys.argv[1])
with zipfile.ZipFile(path) as zf:
    shared = read_shared_strings(zf)
    workbook = ET.fromstring(zf.read('xl/workbook.xml'))
    sheets = []
    for sheet in workbook.find(f'{XLSX_NS}sheets'):
        sheets.append((sheet.attrib.get('name'), sheet.attrib.get(f'{DOC_REL_NS}id')))
    print('sheets:', [name for name, _ in sheets])

    rels = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
    rel_map = {rel.attrib['Id']: rel.attrib['Target'] for rel in rels.findall(f'{REL_NS}Relationship')}
    target = rel_map[sheets[0][1]].replace('worksheets/', 'xl/worksheets/')
    root = ET.fromstring(zf.read(target))
    for row in root.findall(f'.//{XLSX_NS}row')[:10]:
        cells = []
        for cell in row.findall(f'{XLSX_NS}c'):
            cells.append((col_index(cell.attrib.get('r', 'A1')), cell_text(cell, shared)))
        if not cells:
            print('')
            continue
        width = max(idx for idx, _ in cells)
        values = [''] * width
        for idx, text in cells:
            values[idx - 1] = text
        print(' | '.join(values[:40]))
