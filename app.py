import os
import io
from flask import Flask, render_template, request, send_file
from PIL import Image, ImageDraw, ImageFont
import qrcode
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
import tempfile
import json

app = Flask(__name__)

# Настройка шрифтов для разных ОС
def get_font_paths():
    """Получаем пути к шрифтам в зависимости от ОС"""
    import platform
    
    if platform.system() == 'Windows':
        return [
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'arial.ttf'),
            os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts', 'calibri.ttf'),
        ]
    else:  # Linux/Unix (Render servers)
        return [
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
            '/System/Library/Fonts/Arial.ttf',  # macOS
        ]

FONT_PATHS = get_font_paths()

# --- Генерация этикетки ---
LABEL_WIDTH_MM = 50  # 5 см
LABEL_HEIGHT_MM = 60  # 6 см
DPI = 300
width_px = 500
height_px = 600

LAST_NUMBERS_FILE = 'last_numbers.json'

def get_last_number(prefix):
    if not os.path.exists(LAST_NUMBERS_FILE):
        return None
    with open(LAST_NUMBERS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get(prefix)

def set_last_number(prefix, number):
    if os.path.exists(LAST_NUMBERS_FILE):
        with open(LAST_NUMBERS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {}
    data[prefix] = number
    with open(LAST_NUMBERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def extract_prefix_and_number(art):
    import re
    m = re.search(r'(.*?)(\d+)(?!.*\d)', art)
    if not m:
        return art, None, 0
    prefix = m.group(1)
    number = int(m.group(2))
    num_len = len(m.group(2))
    return prefix, number, num_len

def get_font(size):
    """Получаем шрифт нужного размера"""
    for font_path in FONT_PATHS:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            continue
    # Если ничего не найдено, используем дефолтный шрифт с увеличенным размером
    try:
        return ImageFont.load_default().font_variant(size=size)
    except:
        return ImageFont.load_default()

def create_label_image(text, width_px=500, height_px=600, font_size=80):  # Увеличили базовый размер
    font = get_font(font_size)

    img = Image.new('RGB', (width_px, height_px), 'white')
    # Главный QR-код по центру
    qr_size = int(height_px * 0.55)
    main_qr = qrcode.make(text).resize((qr_size, qr_size), Image.LANCZOS)
    qr_x = (width_px - qr_size) // 2
    qr_y = (height_px - qr_size) // 2 - 40  # чуть выше центра, чтобы текст влезал
    img.paste(main_qr, (qr_x, qr_y))

    # Маленькие QR по углам
    small_qr_size = int(height_px * 0.15)
    small_qr = qrcode.make(text).resize((small_qr_size, small_qr_size), Image.LANCZOS)
    img.paste(small_qr, (0, 0))
    img.paste(small_qr, (width_px - small_qr_size, 0))
    img.paste(small_qr, (0, height_px - small_qr_size))
    img.paste(small_qr, (width_px - small_qr_size, height_px - small_qr_size))

    # Текст - увеличиваем размер до 85% ширины этикетки
    draw = ImageDraw.Draw(img)
    
    # Целевая ширина - 85% от ширины этикетки
    target_width = int(width_px * 0.85)
    
    temp_font_size = font_size
    temp_font = font
    text_width = draw.textlength(text, font=temp_font)
    
    # Увеличиваем размер шрифта, пока текст помещается в 85% ширины
    while text_width < target_width and temp_font_size < 300:
        temp_font_size += 3
        temp_font = get_font(temp_font_size)
        new_text_width = draw.textlength(text, font=temp_font)
        if new_text_width > target_width:
            # Откатываемся назад
            temp_font_size -= 3
            temp_font = get_font(temp_font_size)
            break
        text_width = new_text_width
    
    # Если текст слишком широкий, уменьшаем размер
    while text_width > target_width and temp_font_size > 20:
        temp_font_size -= 2
        temp_font = get_font(temp_font_size)
        text_width = draw.textlength(text, font=temp_font)
    
    # Основная надпись снизу
    text_x = (width_px - text_width) // 2
    text_y = height_px - small_qr_size - 60
    draw.text((text_x, text_y), text, font=temp_font, fill='black')
    return img

def generate_labels(base, count):
    # Определяем, где номер (ищем последние цифры)
    import re
    m = re.search(r'(\d+)(?!.*\d)', base)
    if not m:
        return [base]*count
    start_num = int(m.group(1))
    num_len = len(m.group(1))
    prefix = base[:m.start(1)]
    suffix = base[m.end(1):]
    return [f"{prefix}{str(start_num+i).zfill(num_len)}{suffix}" for i in range(count)]

# --- Веб-интерфейс ---
@app.route('/', methods=['GET', 'POST'])
def index():
    labels = []
    base = ''
    count = 1
    next_number = None
    num_len = 7
    if request.method == 'POST':
        base = request.form.get('base', '').strip()
        try:
            count = int(request.form.get('count', '1'))
        except Exception:
            count = 1
        if base and count > 0:
            prefix, number, num_len = extract_prefix_and_number(base)
            last = get_last_number(prefix)
            if last is not None and number <= last:
                number = last + 1
                base = f"{prefix}{str(number).zfill(num_len)}"
            labels = generate_labels(base, count)
            set_last_number(prefix, number + count - 1)
            next_number = number + count
    elif request.method == 'GET':
        base = ''
        count = 1
    return render_template('index.html', labels=labels, base=base, count=count, next_number=next_number, num_len=num_len)

@app.route('/label_img/<text>')
def label_img(text):
    img = create_label_image(text)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/pdf')
def pdf():
    base = request.args.get('base', '').strip()
    try:
        count = int(request.args.get('count', '1'))
    except Exception:
        count = 1
    labels = generate_labels(base, count)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(LABEL_WIDTH_MM*mm, LABEL_HEIGHT_MM*mm))
    for text in labels:
        img = create_label_image(text, width_px=width_px, height_px=height_px, font_size=55)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            img.save(tmp, format='PNG')
            tmp_path = tmp.name
        c.setPageSize((LABEL_WIDTH_MM*mm, LABEL_HEIGHT_MM*mm))
        c.drawImage(tmp_path, 0, 0, width=LABEL_WIDTH_MM*mm, height=LABEL_HEIGHT_MM*mm, mask='auto')
        os.remove(tmp_path)
        c.showPage()
    c.save()
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name='labels.pdf')

@app.route('/next_number')
def next_number():
    art = request.args.get('art', '').strip()
    prefix, number, num_len = extract_prefix_and_number(art)
    last = get_last_number(prefix)
    if last is not None:
        # Если пользователь ввёл только префикс, всё равно показываем следующий номер
        if num_len == 0:
            # Определяем длину номера по последнему сохранённому номеру
            num_len = len(str(last))
            # Добавим ведущие нули, если в базе есть пример артикула
            # Пробуем найти пример артикула в базе last_numbers.json
            try:
                with open(LAST_NUMBERS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for k in data:
                    if k == prefix and isinstance(data[k], int):
                        num_len = max(num_len, len(str(data[k])))
            except Exception:
                pass
        next_num = last + 1
    elif number:
        next_num = number
    else:
        next_num = 1
        # Если только префикс, попробуем взять длину номера из других артикулов
        if num_len == 0:
            import json
            try:
                with open(LAST_NUMBERS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for k in data:
                    if k == prefix and isinstance(data[k], int):
                        num_len = len(str(data[k]))
            except Exception:
                pass
    return {
        'prefix': prefix,
        'next_number': next_num,
        'num_len': num_len
    }

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False) 
