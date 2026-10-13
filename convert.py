#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
convert.py - преобразование выписки Альфа-Банка (расходы.pdf) в CSV формата sell.csv.

Что делает скрипт:
  1. Читает PDF-выписку (по умолчанию "расходы.pdf" рядом со скриптом).
  2. Извлекает все операции: дата, сумма, торговая точка.
  3. Датой операции считает 8-значную дату из описания (ГГГГММДД);
     если её нет - берёт дату проводки (левую колонку выписки).
  4. Категорию берёт из встроенной таблицы CATEGORY_RULES (сравнение без учёта
     регистра, по вхождению подстроки). Если торговой точки в таблице нет -
     в категорию пишет саму торговую точку "как есть".
  5. Расходы (сумма < 0) пишет в sell.csv, а поступления/возвраты (сумма >= 0) -
     в отдельный файл возвраты.csv.

Формат результата повторяет существующий sell.csv:
    date,amount,category
    <пустая строка>
    YYYY-MM-DD,amount,category
    ...
Дни идут по убыванию, между днями - пустая строка. Кодировка UTF-8, без BOM.

Внешних зависимостей нет - только стандартная библиотека Python 3.

Запуск:
    python convert.py [путь_к_pdf]
"""

import os
import re
import sys
import zlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PDF = "расходы.pdf"
EXPENSES_CSV = "sell.csv"
INFLOWS_CSV = "возвраты.csv"

LINE_SEP = "\n"
CSV_HEADER = "date,amount,category"

# Маркер "покупки" в описании операции.
PURCHASE_MARKER = "Покупка товара / получение услуг "

# Встроенная таблица: торговая точка (подстрока) -> категория.
# Проверка идёт сверху вниз, поэтому более специфичные правила - выше.
CATEGORY_RULES = [
    ("TRANSPORT PAYBYCARD.BY", "Транспорт"),
    ("WHOOSH.BIKE", "WHOOSH"),
    ("YANDEX.GO", "YANDEXGO"),
    ("OZON", "OZON"),
    ("WILDBERRIES", "WB"),
    ("APTEKA", "Аптека"),
    ("ABASOVA", "ABASOVA"),
    ("KAFE-STOLOVAYA WILKI", "Обед"),
    ("KAFE PICCA LISICCA", "Пицца"),
    ("InSync (ERIP)", "Коммуналка"),
    ('SHOP "SANTA-250"', "Продукты"),
    ('SUPERMARKET "GREEN-106', "Продукты"),
    ("magazin Evroopt", "Продукты"),
    ('SHOP "SOSEDI"', "Продукты"),
    ("Torgovyy obekt Svetofor", "Продукты"),
    ("TO Gippo", "Продукты"),
    ('SHOP "SEM PYATNITS"', "Продукты"),
    ("SHOP N9 RAYPISCHETORG", "Продукты"),
    ('SHOP "MARK FORMEL"', "Продукты"),
    ("TORGOVY OSTROV BSB", "Продукты"),
    ("MERENGA", "Продукты"),
    ("SHOP MILA", "Продукты"),
    ("Eleven (mob prilozheni", "Eleven"),

    ("CHAYNAYA PLANTACIYA", "Продукты"),
    ("Gipermarket Gippo", "Продукты"),
    ("Gippo", "Продукты"),
    ("LC WAIKIKI", "Одежда"),
    ("MAGAZIN GROSHYK", "Продукты"),
    ("MAGAZIN PRIBORY ChIP I D", "Электроника"),
    ("KFC", "KFC"),
    ("GALAMART", "GALAMART"),
    ("MEGATOP", "Одежда"),
    ("YANDEX", "YANDEX"),
    ("Groshyk", "Продукты"),
    ("банкомате", "Наличные"),
    ("AYSBERG", "Хоккей"),
    ("BAR A1", "Хоккей"),
    ("MKEBAB", "Продукты"),
    ("4mobile", "Электроника"),
    ("chipdip.by", "Электроника"),
    ("MOB.PRILOZHENIE 018 KE", "Продукты"),

    ("BELCHIP.BY", "Электроника"),
    ("BAR", "BAR"),
    ("CINNABON", "Продукты"),
    ("Cinnabon", "Продукты"),
    ("SPIZZA", "Продукты"),
    ("GLORIA JEANS", "Одежда"),
    ("SANTA", "Продукты"),
    ("DONER", "Продукты"),
    ("PYATEROCHKA", "Продукты"),
    ("NEW YORKER", "Одежда"),
    ("EVROOPT", "Продукты"),
    ("Spotify", "Spotify"),
    ("SUSHI", "Продукты"),
    



    
]


def matched_category(merchant):
    """Категория из встроенной таблицы, или None, если правила нет."""
    low = merchant.lower()
    for needle, category in CATEGORY_RULES:
        if needle.lower() in low:
            return category
    return None


def category_for(merchant):
    """Категория по торговой точке: из таблицы, иначе сама торговая точка."""
    return matched_category(merchant) or merchant


# ---------------------------------------------------------------------------
# Разбор PDF
# ---------------------------------------------------------------------------

def find_objects(data):
    """Номер объекта -> его тело (без заголовка 'N 0 obj')."""
    starts = [
        (int(m.group(1)), m.end())
        for m in re.finditer(rb"(?<![0-9])(\d+)\s+0\s+obj\b", data)
    ]
    objects = {}
    for i, (num, body_start) in enumerate(starts):
        if num in objects:
            continue
        body_end = starts[i + 1][1] if i + 1 < len(starts) else len(data)
        body = data[body_start:body_end]
        stream_pos = body.find(b"stream")
        if stream_pos != -1:
            end = body.find(b"endstream", stream_pos)
            if end != -1:
                body = body[:end + len(b"endstream")]
        else:
            end = body.find(b"endobj")
            if end != -1:
                body = body[:end]
        objects[num] = body
    return objects


def object_stream(body):
    """Данные потока объекта (с распаковкой FlateDecode), или None."""
    m = re.search(rb"stream\r?\n", body)
    if not m:
        return None
    header = body[:m.start()]
    raw = body[m.end():]
    lm = re.search(rb"/Length\s+(\d+)", header)
    if lm:
        raw = raw[:int(lm.group(1))]
    else:
        end = raw.find(b"endstream")
        if end != -1:
            raw = raw[:end]
    if b"/FlateDecode" in header:
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            return None
    return raw


def pages_in_order(objects):
    """Список номеров объектов-страниц в порядке следования в документе."""
    root = None
    for num, body in objects.items():
        if re.search(rb"/Type\s*/Catalog", body):
            m = re.search(rb"/Pages\s+(\d+)\s+0\s+R", body)
            if m:
                root = int(m.group(1))
                break
    if root is None:
        for num, body in objects.items():
            if re.search(rb"/Type\s*/Pages", body) and b"/Kids" in body:
                root = num
                break

    order = []
    seen = set()

    def walk(num):
        if num in seen:
            return
        seen.add(num)
        body = objects.get(num, b"")
        m = re.search(rb"/Kids\s*\[(.*?)\]", body, re.S)
        if m:
            for kid in re.findall(rb"(\d+)\s+0\s+R", m.group(1)):
                walk(int(kid))
            return
        order.append(num)

    if root is not None:
        walk(root)
    return order


def page_fonts(objects, page_body):
    """Имя ресурса шрифта -> номер объекта шрифта."""
    m = re.search(rb"/Font\s*<<(.*?)>>", page_body, re.S)
    fonts = {}
    if m:
        for name, num in re.findall(rb"/([A-Za-z0-9]+)\s+(\d+)\s+0\s+R", m.group(1)):
            fonts[name.decode("latin-1")] = int(num)
    return fonts


def parse_cmap(data):
    """ToUnicode CMap -> {код: символ}."""
    text = data.decode("latin-1")
    cmap = {}
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, uni in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            base = int(uni, 16)
            for k in range(hi_i - lo_i + 1):
                cmap[lo_i + k] = chr(base + k)
        for lo, hi, arr in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", block, re.S):
            lo_i = int(lo, 16)
            for k, u in enumerate(re.findall(r"<([0-9A-Fa-f]+)>", arr)):
                cmap[lo_i + k] = chr(int(u, 16))
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for code, uni in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            cmap[int(code, 16)] = chr(int(uni, 16))
    return cmap


def font_decoders(objects, fonts):
    """Имя шрифта -> (двухбайтовый?, CMap)."""
    decoders = {}
    for name, num in fonts.items():
        body = objects.get(num, b"")
        two_byte = b"/Type0" in body or b"/Identity-H" in body
        cmap = {}
        m = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", body)
        if m:
            cmap = parse_cmap(object_stream(objects.get(int(m.group(1)), b"")) or b"")
        decoders[name] = (two_byte, cmap)
    return decoders


# ---------------------------------------------------------------------------
# Разбор потока содержимого страницы (текстовые операторы PDF)
# ---------------------------------------------------------------------------

_ESCAPES = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
            0x28: 40, 0x29: 41, 0x5C: 92}
_WHITESPACE = b" \t\r\n\f\x00"


def unescape_pdf(raw):
    """Раскодировать escape-последовательности строковой константы PDF."""
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == 0x5C and i + 1 < n:
            nxt = raw[i + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                i += 2
            elif 0x30 <= nxt <= 0x37:
                digits = b""
                j = i + 1
                while j < n and 0x30 <= raw[j] <= 0x37 and len(digits) < 3:
                    digits += bytes([raw[j]])
                    j += 1
                out.append(int(digits, 8) & 0xFF)
                i = j
            else:
                out.append(nxt)
                i += 2
        else:
            out.append(c)
            i += 1
    return bytes(out)


def read_literal(content, start):
    """Прочитать строку (...) начиная с '(', вернуть (байты, позиция после ')')."""
    out = bytearray()
    depth = 0
    j, n = start, len(content)
    while j < n:
        c = content[j]
        if c == 0x5C:
            if j + 1 < n:
                out += content[j:j + 2]
                j += 2
            else:
                j += 1
            continue
        if c == 0x28:
            depth += 1
            if depth > 1:
                out.append(c)
            j += 1
            continue
        if c == 0x29:
            depth -= 1
            if depth == 0:
                j += 1
                break
            out.append(c)
            j += 1
            continue
        out.append(c)
        j += 1
    return bytes(out), j


def decode_bytes(raw, font, decoders):
    """Байты строки -> текст согласно кодировке шрифта."""
    raw = unescape_pdf(raw)
    two_byte, cmap = decoders.get(font, (False, {}))
    if two_byte:
        if not cmap:
            return raw.decode("latin-1")
        chars = []
        for i in range(0, len(raw) - 1, 2):
            code = raw[i] * 256 + raw[i + 1]
            ch = cmap.get(code)
            if ch:
                chars.append(ch)
        return "".join(chars)
    return raw.decode("latin-1")


def translate(tx, ty, matrix):
    a, b, c, d, e, f = matrix
    return [a, b, c, d, tx * a + ty * c + e, tx * b + ty * d + f]


def parse_content(content, decoders):
    """Поток содержимого -> список (x, y, текст) для каждой текстовой строки."""
    items = []
    font = None
    tm = [1, 0, 0, 1, 0, 0]
    tlm = [1, 0, 0, 1, 0, 0]
    leading = 0.0
    stack = []
    i, n = 0, len(content)
    while i < n:
        c = content[i]
        if c in _WHITESPACE:
            i += 1
            continue
        if c == 0x28:                                   # (
            raw, i = read_literal(content, i)
            stack.append(("str", raw))
            continue
        if c == 0x3C:                                   # < (hex-строка или словарь)
            j = content.find(b">", i)
            if j == -1:
                break
            seg = re.sub(rb"\s", b"", content[i + 1:j])
            if re.fullmatch(rb"[0-9A-Fa-f]*", seg):
                if len(seg) % 2:
                    seg += b"0"
                stack.append(("str", bytes.fromhex(seg.decode("ascii"))))
            else:
                stack.append(("other", None))
            i = j + 1
            continue
        if c == 0x5B:                                   # [
            stack.append(("arr_start", None))
            i += 1
            continue
        if c == 0x5D:                                   # ]
            collected = []
            while stack and stack[-1][0] != "arr_start":
                collected.append(stack.pop())
            if stack:
                stack.pop()
            collected.reverse()
            stack.append(("arr", [v for t, v in collected if t == "str"]))
            i += 1
            continue
        if c == 0x2F:                                   # /имя
            m = re.match(rb"/([^\s/\[\]<>(){}]*)", content[i:])
            stack.append(("name", m.group(1).decode("latin-1")))
            i += 1 + len(m.group(1))
            continue
        m = re.match(rb"[^\s\[\]<>(){}]+", content[i:])
        token = m.group(0)
        i += len(token)
        try:
            stack.append(("num", float(token)))
            continue
        except ValueError:
            pass
        op = token.decode("latin-1")
        nums = [v for t, v in stack if t == "num"]
        if op == "BT":
            tm = [1, 0, 0, 1, 0, 0]
            tlm = list(tm)
        elif op == "Tf":
            names = [v for t, v in stack if t == "name"]
            if names:
                font = names[-1]
        elif op == "Tm" and len(nums) >= 6:
            tm = nums[-6:]
            tlm = list(tm)
        elif op in ("Td", "TD") and len(nums) >= 2:
            if op == "TD":
                leading = -nums[-1]
            tlm = translate(nums[-2], nums[-1], tlm)
            tm = list(tlm)
        elif op == "T*":
            tlm = translate(0.0, -leading, tlm)
            tm = list(tlm)
        elif op == "TL" and nums:
            leading = nums[-1]
        elif op in ("Tj", "TJ"):
            if op == "Tj":
                strs = [v for t, v in stack if t == "str"]
                raw = strs[-1] if strs else b""
            else:
                arrs = [v for t, v in stack if t == "arr"]
                raw = b"".join(arrs[-1]) if arrs else b""
            items.append((tm[4], tm[5], decode_bytes(raw, font, decoders)))
        stack = []
    return items


# ---------------------------------------------------------------------------
# Сборка операций из текстовых строк страницы
# ---------------------------------------------------------------------------

DATE_COL_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
OP_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})")
AMOUNT_RE = re.compile(r"-?\d+(?:\.\d+)?")

DATE_COL_MAX_X = 60.0    # колонка "дата проводки"
DESC_MAX_X = 430.0       # описание операции
AMOUNT_MIN_X = 430.0     # сумма в валюте счёта
AMOUNT_MAX_X = 505.0     # дальше идёт вторая колонка (валюта операции)
ROW_TOLERANCE = 10.0     # допуск привязки строки к дате


def iso_date(ddmmyyyy):
    day, month, year = ddmmyyyy.split(".")
    return "%s-%s-%s" % (year, month, day)


def page_rows(items):
    """Строки страницы -> список (дата проводки, сумма или None, описание)."""
    anchors = [
        (x, y, text.strip())
        for x, y, text in items
        if x < DATE_COL_MAX_X and DATE_COL_RE.match(text.strip())
    ]
    anchors.sort(key=lambda a: -a[1])

    buckets = [[] for _ in anchors]
    for x, y, text in items:
        if x < DATE_COL_MAX_X or not text.strip():
            continue
        best, best_dist = None, ROW_TOLERANCE + 1.0
        for k, (_ax, ay, _at) in enumerate(anchors):
            dist = abs(y - ay)
            if dist < best_dist:
                best, best_dist = k, dist
        if best is not None:
            buckets[best].append((x, y, text))

    rows = []
    for k, (_ax, _ay, posting) in enumerate(anchors):
        desc_parts, amount_parts = [], []
        for x, y, text in buckets[k]:
            if x < DESC_MAX_X:
                desc_parts.append((y, x, text))
            elif AMOUNT_MIN_X <= x < AMOUNT_MAX_X:
                amount_parts.append((x, y, text))
        desc_parts.sort(key=lambda t: (-t[0], t[1]))
        description = " ".join(part.strip() for _y, _x, part in desc_parts)
        description = re.sub(r"\s+", " ", description).strip()

        amount = None
        for _x, _y, text in sorted(amount_parts):
            found = AMOUNT_RE.search(text)
            if found:
                amount = float(found.group(0))
                break
        rows.append((iso_date(posting), amount, description))
    return rows


def make_transactions(rows):
    """Добавить к строкам дату операции, торговую точку и категорию."""
    transactions = []
    for index, (posting_date, amount, description) in enumerate(rows):
        if amount is None or not description:
            continue
        op_date = None
        found = OP_DATE_RE.match(description)
        if found:
            op_date = "%s-%s-%s" % found.groups()

        merchant = description
        pos = description.find(PURCHASE_MARKER)
        if pos >= 0:
            merchant = description[pos + len(PURCHASE_MARKER):].strip()
        if not merchant:
            merchant = description

        from_table = matched_category(merchant)
        transactions.append({
            "date": op_date or posting_date,
            "posting_date": posting_date,
            "amount": amount,
            "category": from_table or merchant,
            "merchant": merchant,
            "from_table": from_table is not None,
            "index": index,
        })
    return transactions


# ---------------------------------------------------------------------------
# Запись CSV
# ---------------------------------------------------------------------------

def csv_field(value):
    text = str(value)
    if any(ch in text for ch in ',"\r\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def format_amount(value):
    text = "%.2f" % abs(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def render_csv(transactions):
    """Формат как в sell.csv: заголовок, затем группы по датам (по убыванию)."""
    by_date = {}
    for tx in transactions:
        by_date.setdefault(tx["date"], []).append(tx)

    lines = [CSV_HEADER]
    for date in sorted(by_date, reverse=True):
        lines.append("")
        for tx in sorted(by_date[date], key=lambda t: t["index"]):
            lines.append(",".join([
                date,
                format_amount(tx["amount"]),
                csv_field(tx["category"]),
            ]))
    return LINE_SEP.join(lines) + LINE_SEP


def write_csv(path, transactions):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(render_csv(transactions))


# ---------------------------------------------------------------------------
# Чтение PDF целиком
# ---------------------------------------------------------------------------

def page_content(objects, page_body):
    """Содержимое страницы: один поток или массив потоков."""
    m = re.search(rb"/Contents\s+(\d+)\s+0\s+R", page_body)
    if m:
        return object_stream(objects.get(int(m.group(1)), b"")) or b""
    m = re.search(rb"/Contents\s*\[(.*?)\]", page_body, re.S)
    if not m:
        return b""
    parts = []
    for num in re.findall(rb"(\d+)\s+0\s+R", m.group(1)):
        parts.append(object_stream(objects.get(int(num), b"")) or b"")
    return b"".join(parts)


def read_pdf_pages(pdf_path):
    """Список страниц, для каждой - список текстовых строк (x, y, текст)."""
    with open(pdf_path, "rb") as handle:
        data = handle.read()
    objects = find_objects(data)
    pages = []
    for page_num in pages_in_order(objects):
        body = objects.get(page_num, b"")
        decoders = font_decoders(objects, page_fonts(objects, body))
        content = page_content(objects, body)
        if content:
            pages.append(parse_content(content, decoders))
    return pages


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    pdf_path = argv[1] if len(argv) > 1 else os.path.join(SCRIPT_DIR, DEFAULT_PDF)
    if not os.path.isfile(pdf_path):
        print("Не найден PDF-файл: %s" % pdf_path)
        print("Укажите путь аргументом: python convert.py путь\\к\\выписке.pdf")
        return 1

    rows = []
    for page_items in read_pdf_pages(pdf_path):
        rows.extend(page_rows(page_items))

    transactions = make_transactions(rows)
    expenses = [t for t in transactions if t["amount"] < 0]
    inflows = [t for t in transactions if t["amount"] >= 0]

    expenses_csv = os.path.join(SCRIPT_DIR, EXPENSES_CSV)
    inflows_csv = os.path.join(SCRIPT_DIR, INFLOWS_CSV)
    write_csv(expenses_csv, expenses)
    write_csv(inflows_csv, inflows)

    dates = sorted({t["date"] for t in transactions})
    print("PDF: %s" % pdf_path)
    print("Всего операций: %d (строк без суммы пропущено: %d)"
          % (len(transactions), len(rows) - len(transactions)))
    if dates:
        print("Период (по дате операции): %s - %s" % (dates[0], dates[-1]))
    print("Расходов: %d на сумму %.2f -> %s"
          % (len(expenses), sum(abs(t["amount"]) for t in expenses), expenses_csv))
    print("Возвратов/поступлений: %d на сумму %.2f -> %s"
          % (len(inflows), sum(t["amount"] for t in inflows), inflows_csv))

    unknown = sorted({t["category"] for t in transactions if not t["from_table"]})
    if unknown:
        print("")
        print("Торговые точки без правила (в CSV попали как категория):")
        for name in unknown:
            group = [t for t in transactions if t["category"] == name]
            total = sum(abs(t["amount"]) for t in group)
            print("  %-45s %2d шт.  %.2f" % (name[:45], len(group), total))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
