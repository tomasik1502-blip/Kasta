#!/usr/bin/env python3
"""
Конвертер фида CRM (HugeProfit, формат Rozetka) -> фид для Kasta HUB.

Запуск:
    python3 crm_to_kasta.py <вход.xml | URL> <выход.xml> [отчёт.json] [--prev-count N] [--min-ratio 0.7]

Все бизнес-решения вынесены в блок НАСТРОЙКИ ниже.
"""
import sys, re, json, html, datetime, collections, urllib.request
import xml.etree.ElementTree as ET

# ============================ НАСТРОЙКИ ============================

# Цены. 'base'   -> price = price_old = <price> из CRM (без перечёркнутой цены)
#       'online' -> price = цена "Онлайн", price_old = <price> (покупатель видит скидку)
PRICE_MODE = 'base'

# Передавать ли цену "Акция" как акционную (только если акция согласована с менеджером Kasta)
SEND_PROMO = False

# Замена брендов-заглушек и унификация написания
BRAND_MAP = {
    'HugeProfit': 'Без бренду',
    'Поставщик': 'Без бренду',
    'Мавка': 'MAVKA',
}

# Исключать товары с чужими люксовыми брендами в бренде или названии (реплики)
EXCLUDE_REPLICAS = True
LUX = r'cartier|gucci|fendi|dior|chanel|louis vuitton|prada|hermes|versace|balenciaga|ysl|saint laurent|tiffany|bvlgari|bulgari|burberry|armani|dolce|valentino|givenchy|miu miu|celine|bottega|tom ford|michael kors|swarovski|pandora|van cleef'
# Персонажи по лицензии — не исключаем, но помечаем
CHARACTERS = r'губка боб|spongebob|hello kitty|міккі|мінні|disney|marvel|покемон|pokemon|гаррі поттер'

# Переименование параметра "Розмір" по категориям (рекомендация Kasta: разные имена для разных сеток)
SIZE_PARAM_BY_CAT = {
    'Бюстгальтер': 'Розмір бюстгальтера',
    'Труси жіночі': 'Розмір трусів жіночих',
    'Труси чоловічі': 'Розмір трусів чоловічих',
    'Комплекти': 'Розмір комплекту білизни',
    'Ремні': 'Довжина ременя',
    'Колготки': 'Розмір колготок',
}

# Разделение смешанных категорий: категория -> [(regex по названию, подкатегория или id существующей)]
SPLIT_RULES = {
    'Прикраси': [(r'пакуванн', 'Подарункове пакування'), (r'каблучк', 'Каблучки'), (r'сережк', 'Сережки'),
                 (r'браслет', 'Браслети'), (r'ланцюж', 'Ланцюжки'), (r'підвіск', 'Підвіски'),
                 (r'брошк', 'Брошки'), (r'діадем', 'Діадеми'), (r'намист', 'Намиста')],
    'Одяг': [(r'спідниц', 'Спідниці'), (r'перчат|рукавич', 'Рукавички'), (r'капелюх', 'Капелюхи')],
    'Іграшки': [(r'шкарпет', '@83654'), (r'новорічн', 'Новорічні іграшки')],
    'Колготки': [(r'панчох', '@217507'), (r'гольф', 'Гольфи')],
    'Шкарпетки': [(r'панчох', '@217507')],
    'Сумки': [(r'рюкзак', 'Рюкзаки')],
    'Помада': [(r'олівець', 'Олівці для губ')],
    'Для порожнини рота': [(r'щітк', 'Зубні щітки'), (r'паст|порошок', 'Зубні пасти та порошки')],
    'Догляд за волоссям': [(r'шампун', 'Шампуні'), (r'кондиціонер', 'Кондиціонери для волосся'),
                           (r'маск', 'Маски для волосся'), (r'олі[яї]', 'Олії для волосся'),
                           (r'капсул', 'Капсули для волосся'), (r'сироватк', 'Сироватки для волосся'),
                           (r'спрей|термозах', 'Спреї для волосся')],
}

# Точечные правки русских слов в украинских названиях
RU_FIX = {'эфектом': 'ефектом', 'стрэпами': 'стрепами', 'Капсулы': 'Капсули', 'капсулы': 'капсули',
          'сиворотка': 'сироватка', 'Сиворотка': 'Сироватка', 'карандаш': 'олівець', 'Карандаш': 'Олівець',
          'перчаткі': 'рукавички', 'Перчаткі': 'Рукавички', 'Перчатки': 'Рукавички', 'перчатки': 'рукавички',
          'волос ': 'волосся ', 'пакуваня': 'пакування', 'комуфляж': 'камуфляж'}

MAX_DESC = 5000
MAX_PICS = 20
# ===================================================================

RU_CH = re.compile(r'[ёъыэЁЪЫЭ]'); UA_CH = re.compile(r'[іїєґІЇЄҐ]')
lux_re = re.compile(r'\b(' + LUX + r')\b', re.I)
char_re = re.compile(CHARACTERS, re.I)


def load(src):
    if src.startswith('http'):
        req = urllib.request.Request(src, headers={'User-Agent': 'Mozilla/5.0 (kasta-feed-sync)'})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
        root = ET.fromstring(data)          # битый/обрезанный XML -> исключение, фид не перезапишется
        if root.tag != 'yml_catalog':
            raise SystemExit(f'CRM вернула не YML-каталог (корень <{root.tag}>)')
        return root
    return ET.parse(src).getroot()


def clean_name(n):
    """Возвращает (чистое название, цвет из названия, вес в кг из названия)."""
    color = weight = None
    n = re.sub(r'\s*штрихкод:\s*\d+\.?', '', n, flags=re.I)
    m = re.search(r'(?:Цвет|Колір)\s*:\s*([^.;(]+)', n)
    if m: color = m.group(1).strip()
    n = re.sub(r'[.;]?\s*(?:Цвет|Колір)\s*:\s*[^.;]+[;.]?', '', n)
    m = re.search(r'Вес\s*:\s*(\d+(?:[.,]\d+)?)', n)
    if m: weight = round(float(m.group(1).replace(',', '.')) / 1000, 3)
    n = re.sub(r'[.;]?\s*Вес\s*:\s*\d+(?:[.,]\d+)?\.?', '', n)
    n = re.sub(r'\s*\(\s*[\w./-]*\d[\w./-]*\s*\)', '', n)          # коды артикулов в скобках
    n = re.sub(r'(\bml|\bмл)\s+\d{4,}\b', r'\1', n, flags=re.I)    # "100 ml 28535" -> "100 ml"
    for a, b in RU_FIX.items(): n = n.replace(a, b)
    n = re.sub(r'\s+', ' ', n).strip(' .;,')
    if n: n = n[0].upper() + n[1:]
    return n, color, weight


def trim_desc(d):
    if len(d) <= MAX_DESC: return d
    cut = d[:MAX_DESC]
    cut = cut[:max(cut.rfind('<br'), cut.rfind('. '), MAX_DESC - 300)]
    return re.sub(r'<[^>]*$', '', cut)


def num(x):
    try: return float(str(x).replace(',', '.'))
    except Exception: return None


def fmt(x):
    return str(int(x)) if float(x).is_integer() else f'{x:.2f}'


def convert(root):
    shop = root.find('shop')
    cats = {c.get('id'): (c.text or '').strip() for c in shop.find('categories')}
    offers = shop.find('offers').findall('offer')
    excluded, warns = [], collections.defaultdict(list)
    new_cats = {}                       # id -> (name, parentId)
    sub_ids = {}
    for cid, cn in cats.items(): new_cats[cid] = (cn, None)

    def sub_cat(parent_id, sub):
        """ID подкатегории = parentId*100 + номер правила в SPLIT_RULES.
        Не зависит от порядка товаров в CRM, поэтому маппинг в HUB не «съезжает» при обновлениях.
        Новые правила добавляйте в КОНЕЦ списка категории, чтобы не сдвинуть существующие ID."""
        if sub.startswith('@'): return sub[1:]
        key = (parent_id, sub)
        if key not in sub_ids:
            rules = SPLIT_RULES[cats[parent_id]]
            idx = [s for _, s in rules].index(sub) + 1
            sub_ids[key] = str(int(parent_id) * 100 + idx)
            new_cats[sub_ids[key]] = (sub, parent_id)
        return sub_ids[key]

    items = []
    for o in offers:
        g = lambda t: (o.findtext(t) or '').strip()
        oid = o.get('id'); raw_name = g('name'); vendor = g('vendor'); cid = g('categoryId')
        cname = cats.get(cid, '')
        pics = [p.text.strip() for p in o.findall('picture') if p.text and p.text.strip()]
        price = num(g('price'))
        ex = lambda reason: excluded.append({'offer_id': oid, 'article': g('article'), 'name': raw_name,
                                             'vendor': vendor, 'category': cname, 'reason': reason})
        name_wo_dupe = re.sub(r'\(аромат схожий на[^)]*\)', '', raw_name, flags=re.I)
        if EXCLUDE_REPLICAS and (lux_re.search(vendor) or lux_re.search(name_wo_dupe) or 'копія' in raw_name.lower()):
            ex('Чужий люксовий бренд у бренді/назві (ризик реплік, блок модерації та претензій правовласника)'); continue
        if 'сертифікат' in raw_name.lower():
            ex('Подарунковий сертифікат: не товар для маркетплейсу, містить стоп-слово «грн»'); continue
        if not pics:
            ex('Немає фото (обовʼязковий тег picture)'); continue
        if price is None or price <= 0:
            ex('Ціна відсутня або 0'); continue
        if cid not in cats:
            ex('categoryId відсутній у блоці categories'); continue

        name, ncolor, nweight = clean_name(raw_name)
        if re.search(r'схожий на', raw_name, re.I): warns['Назва згадує чужий бренд («аромат схожий на»): ризик модерації'].append(oid)
        if char_re.search(raw_name): warns['Ліцензійний персонаж у назві: Kasta може запросити документи'].append(oid)
        if RU_CH.search(name): warns['У назві лишились російські літери: перевірте вручну'].append(oid)

        params = []
        pmap = {}
        for p in o.findall('param'):
            pn = (p.get('name') or '').strip(); pv = (p.text or '').strip()
            if not pv or pn.lower() in ('цена юань',): continue
            if pn.lower() == 'штрихкод': pmap['barcode'] = pv; continue
            if pn == 'Розмір': pn = SIZE_PARAM_BY_CAT.get(cname, 'Розмір')
            pmap[pn] = pv; params.append((pn, pv))
        if ncolor and 'Колір' not in pmap: params.append(('Колір', ncolor)); pmap['Колір'] = ncolor
        if nweight: params.append(('Вага в упаковці, кг', fmt(nweight)))

        # подкатегории
        new_cid = cid
        for rx, sub in SPLIT_RULES.get(cname, []):
            if re.search(rx, raw_name, re.I): new_cid = sub_cat(cid, sub); break

        desc = g('description')
        if desc == raw_name or not desc: desc = name          # опис-дубль назви з кодами/«Цвет:» -> чиста назва
        desc_ru = bool(RU_CH.search(desc)) and not UA_CH.search(desc)
        if len(desc) > MAX_DESC: warns['Опис > 5000 символів: обрізано'].append(oid)
        if re.search(r'https?://|www\.', desc): desc = re.sub(r'https?://\S+|www\.\S+', '', desc); warns['Посилання в описі: видалено'].append(oid)
        desc = trim_desc(desc)

        pr = {p.get('name'): num(p.findtext('value')) for p in o.findall('prices/price')}
        if PRICE_MODE == 'online' and pr.get('Онлайн'):
            new_price, old_price = pr['Онлайн'], max(price, pr['Онлайн'])
        else:
            new_price = old_price = price
        promo = pr.get('Акция') if SEND_PROMO and pr.get('Акция') and pr['Акция'] < new_price else None

        stock = g('stock_quantity')
        stock = int(stock) if re.fullmatch(r'\d+', stock) else 0
        avail = o.get('available', 'true')
        if avail == 'false': stock = 0

        art = g('article') or oid
        if not g('article'): warns['Немає артикула: підставлено offer id'].append(oid)

        items.append(dict(id=oid, group=o.get('group_id'), avail=avail, name=name, vendor=BRAND_MAP.get(vendor, vendor),
                          article=art, cid=new_cid, src_cat=cname, price=new_price, old=old_price, promo=promo,
                          stock=stock, pics=pics[:MAX_PICS], desc=desc, desc_ru=desc_ru, params=params,
                          barcode=pmap.get('barcode')))
        if len(pics) > MAX_PICS: warns['Більше 20 фото: передано перші 20'].append(oid)

    # склейка вариантов: общий article и одинаковое название внутри group_id
    groups = collections.defaultdict(list)
    for it in items:
        if it['group']: groups[it['group']].append(it)
    used_bases = {(it['article'], it['vendor']) for it in items if not it['group']}
    for gid, its in groups.items():
        if len(its) < 2: continue
        toks = [it['article'].split('-') for it in its]
        pref = []
        for parts in zip(*toks):
            if len(set(parts)) == 1: pref.append(parts[0])
            else: break
        base = '-'.join(pref) if pref else f'G{gid}'
        vend = its[0]['vendor']
        if (base, vend) in used_bases:        # у другой группы тот же префикс (напр. бюстгальтер и трусы одной серии)
            base = f'{base}-{its[0]["cid"]}'
        used_bases.add((base, vend))
        for it in its:
            it['article'] = base; it['name'] = its[0]['name']; it['vendor'] = its[0]['vendor']; it['cid'] = its[0]['cid']
        combos = collections.Counter(tuple(v for k, v in it['params'] if k == 'Колір' or 'Розмір' in k or 'Довжина' in k) for it in its)
        if any(c > 1 for c in combos.values()):
            warns['Група з однаковими колір+розмір у різних SKU (конфлікт склейки)'].append(gid)

    # автосклейка вариантов без group_id: одинаковые бренд+категория+название и общий артикул до последнего "-"
    auto = collections.defaultdict(list)
    for it in items:
        if it['group'] or '-' not in it['article']: continue
        auto[(it['vendor'], it['cid'], it['name'], it['article'].rsplit('-', 1)[0])].append(it)
    for (v, c, n, base), its in auto.items():
        keys = [tuple(val for k, val in it['params'] if k == 'Колір' or 'Розмір' in k) for it in its]
        if len(its) > 1 and all(keys) and len(set(keys)) == len(keys):
            for it in its: it['article'] = base
            warns['Автосклейка варіантів без group_id (перевірте)'].extend(it['id'] for it in its)

    # одежда без размера
    need_size = {'Шкарпетки', 'Одяг', 'Колготки', 'Бюстгальтер', 'Труси жіночі', 'Труси чоловічі', 'Комплекти'}
    for it in items:
        if it['src_cat'] in need_size and not any('Розмір' in k for k, _ in it['params']):
            warns['Одяг/білизна без розміру: Kasta не виставить у продаж (SIZE_NOT_PROVIDED)'].append(it['id'])
    for it in items:
        if not any(k == 'Вага в упаковці, кг' for k, _ in it['params']):
            warns['Немає ваги та габаритів в упаковці: Kasta підставить середні по категорії'].append(it['id'])

    used = {it['cid'] for it in items}
    used |= {new_cats[c][1] for c in used if new_cats.get(c, (0, None))[1]}
    return items, {k: v for k, v in new_cats.items() if k in used}, excluded, warns


def esc(s): return html.escape(str(s), quote=True)


def write(items, cats, path):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<yml_catalog date="{now}">', '<shop>',
           '<currencies><currency id="UAH" rate="1"/></currencies>', '<categories>']
    for cid, (n, parent) in sorted(cats.items(), key=lambda x: int(x[0])):
        pa = f' parentId="{parent}"' if parent else ''
        out.append(f'<category id="{cid}"{pa}>{esc(n)}</category>')
    out += ['</categories>', '<offers>']
    for it in items:
        o = [f'<offer id="{esc(it["id"])}" available="{it["avail"]}">',
             f'<name_ua>{esc(it["name"])}</name_ua>', f'<article>{esc(it["article"])}</article>',
             f'<vendor>{esc(it["vendor"])}</vendor>', '<currencyId>UAH</currencyId>',
             f'<categoryId>{it["cid"]}</categoryId>', f'<price>{fmt(it["price"])}</price>',
             f'<price_old>{fmt(it["old"])}</price_old>']
        if it['promo']: o.append(f'<price_promo>{fmt(it["promo"])}</price_promo>')
        o.append(f'<stock_quantity>{it["stock"]}</stock_quantity>')
        o += [f'<picture>{esc(p)}</picture>' for p in it['pics']]
        if it['barcode']: o.append(f'<barcode>{esc(it["barcode"])}</barcode>')
        tag = 'description' if it['desc_ru'] else 'description_ua'
        if it['desc']: o.append(f'<{tag}>{esc(it["desc"])}</{tag}>')
        o += [f'<param name="{esc(k)}">{esc(v)}</param>' for k, v in it['params']]
        o.append('</offer>')
        out.append(''.join(o))
    out += ['</offers>', '</shop>', '</yml_catalog>']
    with open(path, 'w', encoding='utf-8') as f: f.write('\n'.join(out))


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='CRM (Rozetka YML) -> Kasta YML')
    ap.add_argument('src', help='путь к XML или URL выгрузки CRM')
    ap.add_argument('dst', help='куда записать фид Kasta')
    ap.add_argument('report', nargs='?', help='JSON-отчёт (необязательно)')
    ap.add_argument('--prev-count', type=int, default=0, help='сколько товаров было в прошлом фиде')
    ap.add_argument('--min-ratio', type=float, default=0.7,
                    help='защита: не публиковать, если товаров стало меньше этой доли от прошлого раза')
    a = ap.parse_args()
    items, cats, excluded, warns = convert(load(a.src))
    # Защита: если CRM отдала пустой/урезанный фид, не публикуем его — иначе при включённой
    # галочке «Зняти з продажу товари, які відсутні в фіді» Kasta снимет товары с продажи.
    if not items:
        raise SystemExit('Защита: в фиде 0 товаров, публикация отменена')
    if a.prev_count and len(items) < a.prev_count * a.min_ratio:
        raise SystemExit(f'Защита: товаров {len(items)} против {a.prev_count} в прошлый раз '
                         f'(меньше {a.min_ratio:.0%}). Публикация отменена. Если сокращение плановое, '
                         f'запустите workflow вручную с force=true.')
    src, dst = a.src, a.dst
    write(items, cats, dst)
    rep = {'in_feed': len(items), 'excluded': excluded, 'warnings': {k: v for k, v in warns.items()},
           'categories': {k: {'name': v[0], 'parent': v[1]} for k, v in cats.items()}}
    if a.report:
        with open(a.report, 'w', encoding='utf-8') as f: json.dump(rep, f, ensure_ascii=False, indent=1)
    print(f'В фиде: {len(items)}, исключено: {len(excluded)}')
    for k, v in warns.items(): print(f'  ! {k}: {len(v)}')
