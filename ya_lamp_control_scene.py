import os
import time
import threading
import requests
import numpy as np
from mss import mss
from PIL import Image
import colorsys
from flask import Flask, request, render_template_string
import logging
from typing import Tuple, Optional

OAUTH_TOKEN = os.getenv("YANDEX_OAUTH_TOKEN", "ваш_токен")
DEVICE_ID = os.getenv("YANDEX_DEVICE_ID", "айдишник_лампочки")
API_URL = "https://api.iot.yandex.net/v1.0/devices/actions"

HEADERS = {
    "Authorization": f"Bearer {OAUTH_TOKEN}",
    "Content-Type": "application/json"
}

# Настройки по умолчанию
settings = {
    "update_interval": 0.5,
    "brightness_step": 5,
    "min_brightness": 6,
    "monitor_number": 1,
    "saturation_boost": 1.0
}

last_brightness: Optional[int] = None
running = True
settings_lock = threading.Lock()
mss_lock = threading.Lock()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === ОСНОВНАЯ ЛОГИКА ===
def get_screen_color_and_brightness() -> Tuple[int, int, int, int]:
    try:
        with settings_lock:
            monitor_num = settings["monitor_number"]
        with mss_lock:
            with mss() as sct:
                monitor = sct.monitors[monitor_num]
                screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        # Разрешение, с которого будет читаться яркость и цвета
        img = img.resize((800, 800), Image.Resampling.LANCZOS)
        pixels = np.array(img)
        avg_color = pixels.mean(axis=(0, 1))
        r, g, b = [int(x) for x in avg_color]
        brightness = int(np.clip(avg_color.mean() / 255 * 100, 1, 100))
        return r, g, b, brightness
        
    except Exception as e:
        logger.error(f"Ошибка при захвате экрана: {e}")
        return 0, 0, 0, 0

def get_available_monitors():
    try:
        with mss_lock:
            with mss() as sct:
                return list(enumerate(sct.monitors))
    except Exception as e:
        logger.error(f"Ошибка при получении списка мониторов: {e}")
        return [(0, {'width': 1920, 'height': 1080})]

def set_lamp_state(r: int, g: int, b: int, brightness: int) -> bool:
    global last_brightness
    
    try:
        saturation_boost = settings.get("saturation_boost", 1.0)
        h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
        s = min(1.0, s * saturation_boost)
        h = int(h * 360)
        s = int(s * 100)
        v = brightness
        if brightness < settings["min_brightness"]:
            payload = {
                "devices": [{
                    "id": DEVICE_ID,
                    "actions": [{
                        "type": "devices.capabilities.on_off",
                        "state": {"instance": "on", "value": False}
                    }]
                }]
            }
            
            response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info("💤 Лампа выключена (слишком темно)")
            last_brightness = 0
            return True
        brightness_step = settings["brightness_step"]
        if last_brightness is not None and abs(brightness - last_brightness) < brightness_step:
            brightness = last_brightness
            v = brightness
        payload = {
            "devices": [{
                "id": DEVICE_ID,
                "actions": [
                    {
                        "type": "devices.capabilities.on_off",
                        "state": {"instance": "on", "value": True}
                    },
                    {
                        "type": "devices.capabilities.color_setting",
                        "state": {
                            "instance": "hsv", 
                            "value": {"h": h, "s": s, "v": v}
                        }
                    },
                    {
                        "type": "devices.capabilities.range",
                        "state": {"instance": "brightness", "value": brightness}
                    }
                ]
            }]
        }

        # Отправляем команду
        response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=10)
        response.raise_for_status()
        
        logger.info(f"🎨 Цвет: HSV({h}, {s}, {v}), Яркость: {brightness}%")
        last_brightness = brightness
        return True
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Нет связи: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Нежданчик: {e}")
        return False

def lamp_loop():
    error_count = 0
    max_errors = 5
    while running:
        try:
            r, g, b, brightness = get_screen_color_and_brightness()
            success = set_lamp_state(r, g, b, brightness)
            if success:
                error_count = 0
            else:
                error_count += 1
                if error_count >= max_errors:
                    logger.error("⚠️ Слишком много ошибок, перекур 10 секунд")
                    time.sleep(10)
                    error_count = 0

            with settings_lock:
                interval = settings["update_interval"]
            time.sleep(interval)
            
        except Exception as e:
            logger.error(f"❌ АХТУНГ! Критическая ошибка в основном цикле: {e}")
            time.sleep(5)

# === FLASK ВЕБ-СЕРВЕР ===
app = Flask(__name__)

HTML_PAGE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Превращение лампы в Ambilight через костыли</title>
    <style>
        body { 
            font-family: Arial, sans-serif; 
            max-width: 600px; 
            margin: 0 auto; 
            padding: 20px; 
            background: #f5f5f5; 
        }
        .container { 
            background: white; 
            padding: 30px; 
            border-radius: 10px; 
            box-shadow: 0 2px 10px rgba(0,0,0,0.1); 
        }
        h2 { color: #333; margin-top: 0; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input, select { 
            width: 100%; 
            padding: 10px; 
            border: 1px solid #ddd; 
            border-radius: 5px; 
            box-sizing: border-box; 
        }
        button { 
            background: #007cba; 
            color: white; 
            padding: 12px 24px; 
            border: none; 
            border-radius: 5px; 
            cursor: pointer; 
            font-size: 16px; 
        }
        .stop-btn { background: #d9534f; }
        .status { 
            padding: 10px; 
            border-radius: 5px; 
            margin: 20px 0; 
            text-align: center; 
        }
        .success { background: #dff0d8; color: #3c763d; }
        .error { background: #f2dede; color: #a94442; }
        .monitor-info { 
            background: #f0f8ff; 
            padding: 10px; 
            border-radius: 5px; 
            font-size: 14px; 
            margin: 10px 0; 
        }
    </style>
</head>
<body>
    <div class="container">
        <h2>💡 Руль от лампочки </h2>
        
        {% if message %}
        <div class="status {{ 'success' if message_type == 'success' else 'error' }}">
            {{ message }}
        </div>
        {% endif %}

        <div class="monitor-info">
            <strong>Доступные мониторы:</strong><br>
            {% for i, monitor in monitors %}
            {{ i }}: {{ monitor.width }}x{{ monitor.height }} 
            {% if i == 0 %}(все мониторы){% endif %}<br>
            {% endfor %}
        </div>

        <form method="post" action="/update">
            <div class="form-group">
                <label for="monitor_number">Номер монитора:</label>
                <select id="monitor_number" name="monitor_number">
                    {% for i, monitor in monitors %}
                    <option value="{{ i }}" {{ 'selected' if i == monitor_number }}>
                        Монитор {{ i }} ({{ monitor.width }}x{{ monitor.height }})
                    </option>
                    {% endfor %}
                </select>
            </div>

            <div class="form-group">
                <label for="update_interval">Частота обновления (секунды):</label>
                <input type="number" step="0.1" min="0.1" max="5" name="update_interval" value="{{ update_interval }}">
            </div>

            <div class="form-group">
                <label for="brightness_step">Шаг яркости (%):</label>
                <input type="number" min="1" max="20" name="brightness_step" value="{{ brightness_step }}">
            </div>

            <div class="form-group">
                <label for="min_brightness">Минимальная яркость (выключение):</label>
                <input type="number" min="0" max="50" name="min_brightness" value="{{ min_brightness }}">
            </div>

            <div class="form-group">
                <label for="saturation_boost">Усиление насыщенности (1.0 = нормально):</label>
                <input type="number" step="0.1" min="0.5" max="3.0" name="saturation_boost" value="{{ saturation_boost }}">
            </div>

            <button type="submit">💾 Сохранить настройки</button>
        </form>

        <hr>

        <form method="post" action="/stop" onsubmit="return confirm('Остановить скрипт?')">
            <button type="submit" class="stop-btn">⛔ Остановить скрипт</button>
        </form>

        <form method="post" action="/start" style="margin-top: 10px;">
            <button type="submit">▶️ Перезапустить скрипт</button>
        </form>
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    monitors_list = get_available_monitors()
    return render_template_string(HTML_PAGE, 
                                monitors=monitors_list,
                                message=request.args.get('message'),
                                message_type=request.args.get('type'),
                                **settings)

@app.route("/update", methods=["POST"])
def update_settings():
    try:
        with settings_lock:
            settings["monitor_number"] = int(request.form.get("monitor_number", settings["monitor_number"]))
            settings["update_interval"] = float(request.form.get("update_interval", settings["update_interval"]))
            settings["brightness_step"] = int(request.form.get("brightness_step", settings["brightness_step"]))
            settings["min_brightness"] = int(request.form.get("min_brightness", settings["min_brightness"]))
            settings["saturation_boost"] = float(request.form.get("saturation_boost", settings["saturation_boost"]))
        
        logger.info("Настройки обновлены")
        return index()
        
    except Exception as e:
        logger.error(f"Ошибка при обновлении настроек: {e}")
        return index()

@app.route("/stop", methods=["POST"])
def stop_script():
    global running
    running = False
    logger.info("Скрипт остановлен пользователем")
    return "✅ Скрипт остановлен! Страница закроется через 3 секунды.<script>setTimeout(()=>window.close(),3000)</script>"

@app.route("/start", methods=["POST"])
def start_script():
    global running
    running = True
    if not any(thread.name == "lamp_thread" and thread.is_alive() for thread in threading.enumerate()):
        t = threading.Thread(target=lamp_loop, daemon=True, name="lamp_thread")
        t.start()
        logger.info("Поток управления лампой запущен")
    logger.info("Скрипт перезапущен")
    return index()
def main():
    global running
    print("🚀 Запуск Ambilight системы для лампы")
    monitors = get_available_monitors()
    print("📊 Доступные мониторы:")
    for i, monitor in monitors:
        print(f"   {i}: {monitor['width']}x{monitor['height']} {'(все мониторы)' if i == 0 else ''}")
    print(f"🌐 Веб-интерфейс доступен по адресу: http://localhost:5000")
    print("⏹️  Для остановки нажмите Ctrl+C или используйте веб-интерфейс\n")
    lamp_thread = threading.Thread(target=lamp_loop, daemon=True, name="lamp_thread")
    lamp_thread.start()
    
    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    except KeyboardInterrupt:
        print("\n🛑 Остановка скрипта...")
        running = False
        lamp_thread.join(timeout=5)
        print("👋 Завершено!")

if __name__ == "__main__":
    main()