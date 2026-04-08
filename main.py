import telebot, sqlite3, time, threading, json
from telebot import types
from curl_cffi import requests
from datetime import datetime, timedelta

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8202414409:AAHTvwYSftKeCKPjti-ai6vsKxTPaqYjT_8"
PUB_ID = "9cdf72e4-aa1d-45e8-9fd3-faaca804ffd1"

bot = telebot.TeleBot(TOKEN)
DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
ALL_GROUPS = {
    "01-25.ГД.ОФ.9": "46", "01-25.Д.ОД.9": "58", "01-25.Д.ОФ.9": "48",
    "01-25.ИИ, СИСА, КС, ГД, ТЭиОРП, РКИ.ОД.9": "59",
    "01-25.ИИ.ОФ.9, ОИБ.ОФ.9, ТЭиОРП.ОФ.9": "60",
    "01-25.ИСИП.ОД.9": "57", "01-25.ИСИП.ОФ.9": "52",
    "01-25.Р.ОФ.9": "47", "01-25.РКИ.ОФ.9": "56",
    "01-25.СИСА.ОФ.9, 01-25.КС.ОФ.9": "51",
    "02-25.Д.ОФ.9": "49", "02-25.ИСИП.ОФ.9": "53",
    "03-25.ИСИП.ОФ.9": "54", "04-25.ИСИП.ОФ.9, 03-25.Д.ОФ.9": "55"
}

# --- БД ---
def init_db():
    with sqlite3.connect('college_bot.db') as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, group_id TEXT, last_schedule TEXT)')

def get_db(uid):
    with sqlite3.connect('college_bot.db') as conn:
        return conn.execute('SELECT group_id, last_schedule FROM users WHERE user_id = ?', (uid,)).fetchone()

def update_db(uid, gid=None, sched_json=None):
    with sqlite3.connect('college_bot.db') as conn:
        if gid: conn.execute('INSERT OR REPLACE INTO users (user_id, group_id) VALUES (?, ?)', (uid, gid))
        if sched_json: conn.execute('UPDATE users SET last_schedule = ? WHERE user_id = ?', (sched_json, uid))

# --- API И ЛОГИКА ---
def get_api_data(gid, date_obj):
    monday = date_obj - timedelta(days=date_obj.weekday())
    payload = {"groupId": gid, "date": monday.strftime("%Y-%m-%d"), "publicationId": PUB_ID}
    headers = {"Content-Type": "application/json", "Referer": f"https://schedule.mstimetables.ru/publications/{PUB_ID}"}
    try:
        r = requests.post("https://schedule.mstimetables.ru/api/publications/group/lessons", json=payload, headers=headers, impersonate="chrome110", timeout=15)
        return r.json() if r.status_code == 200 else None
    except: return None

def format_day(data, target_date):
    if not data or 'lessons' not in data: return "❌ Нет данных."
    day_num = target_date.isoweekday() 
    day_lessons = [l for l in data['lessons'] if int(l.get('weekday', 0)) == day_num]
    lessons = sorted(day_lessons, key=lambda x: x.get('lesson', 0))
    bells = {b['lesson']: (b['startTime'], b['endTime']) for b in data.get('bells', []) if b['weekday'] == day_num}
    res = f"🗓 <b>{DAYS_RU[day_num-1]} ({target_date.strftime('%d.%m')})</b>\n" + "—"*15 + "\n\n"
    if not lessons: return res + "Пар нет! 🎉"
    for l in lessons:
        n = l.get('lesson')
        t = bells.get(n, ("??:??", "??:??"))
        s_obj, c_obj = l.get('subject'), l.get('cabinet')
        subj = s_obj.get('name', '---') if s_obj else '---'
        cab = c_obj.get('name', '---') if c_obj else '---'
        res += f"<b>{n} пара</b> ({t[0]} - {t[1]})\n📘 {subj}\n📍 Каб: <b>{cab}</b>\n\n"
    return res

def extract_clean_schedule(data, day_num):
    """Вытаскивает ТОЛЬКО текст: 'Урок-Предмет-Кабинет'. Игнорирует системный мусор."""
    if not data or 'lessons' not in data: return ""
    lessons = [l for l in data['lessons'] if int(l.get('weekday', 0)) == day_num]
    lessons = sorted(lessons, key=lambda x: x.get('lesson', 0))
    schedule_hash = []
    for l in lessons:
        n = l.get('lesson', 0)
        s_obj, c_obj = l.get('subject'), l.get('cabinet')
        subj = s_obj.get('name', '') if s_obj else ''
        cab = c_obj.get('name', '') if c_obj else ''
        schedule_hash.append(f"{n}-{subj}-{cab}")
    return " | ".join(schedule_hash)

# --- АВТОПРОВЕРКА ---
def auto_check():
    while True:
        try:
            with sqlite3.connect('college_bot.db') as conn:
                users = conn.execute('SELECT user_id, group_id, last_schedule FROM users').fetchall()
            
            for uid, gid, last_raw in users:
                if not gid: continue
                data = get_api_data(gid, datetime.now())
                if not data or 'lessons' not in data: continue
                
                # Теперь мы храним не JSON, а чистые строки с текстом пар
                current_days = {str(i): extract_clean_schedule(data, i) for i in range(1, 7)}
                
                if last_raw:
                    old_days = json.loads(last_raw)
                    new_last_schedule = old_days.copy()
                    was_changed = False
                    
                    for d_num in ["1", "2", "3", "4", "5", "6"]:
                        # Сравниваем чистый текст
                        if old_days.get(d_num) != current_days.get(d_num):
                            monday = datetime.now() - timedelta(days=datetime.now().weekday())
                            target_date = monday + timedelta(days=int(d_num)-1)
                            
                            bot.send_message(uid, f"🔔 <b>Изменение: {DAYS_RU[int(d_num)-1]}</b>", parse_mode="HTML")
                            bot.send_message(uid, format_day(data, target_date), parse_mode="HTML")
                            
                            # Перезаписываем только этот день
                            new_last_schedule[d_num] = current_days[d_num]
                            was_changed = True
                    
                    if was_changed:
                        update_db(uid, sched_json=json.dumps(new_last_schedule))
                else:
                    update_db(uid, sched_json=json.dumps(current_days))
            
            time.sleep(1200) 
        except:
            time.sleep(60)

# --- ИНТЕРФЕЙС ---
@bot.message_handler(commands=['start'])
def start(m):
    bot.send_message(m.chat.id, "🔍 Введи группу:", reply_markup=types.ReplyKeyboardRemove())

@bot.message_handler(func=lambda m: m.text in ["Сегодня", "Завтра", "Сменить группу"])
def menu(m):
    user = get_db(m.from_user.id)
    if not user or m.text == "Сменить группу": return start(m)
    date = datetime.now() if m.text == "Сегодня" else datetime.now() + timedelta(days=1)
    bot.send_chat_action(m.chat.id, 'typing')
    bot.send_message(m.chat.id, format_day(get_api_data(user[0], date), date), parse_mode="HTML", reply_markup=main_menu())

@bot.message_handler(func=lambda m: True)
def search(m):
    q = m.text.lower().strip()
    matches = {n: i for n, i in ALL_GROUPS.items() if q in n.lower()}
    if matches:
        kb = types.InlineKeyboardMarkup()
        for n, i in matches.items(): kb.add(types.InlineKeyboardButton(n, callback_data=f"set_{i}"))
        bot.send_message(m.chat.id, "🎯 Выбери группу:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("set_"))
def set_g(c):
    update_db(c.from_user.id, gid=c.data.split("_")[1])
    bot.edit_message_text("✅ Готово!", c.message.chat.id, c.message.message_id)
    bot.send_message(c.message.chat.id, "Кнопки:", reply_markup=main_menu())

def main_menu():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.row("Сегодня", "Завтра").row("Сменить группу")
    return m

if __name__ == "__main__":
    init_db()
    threading.Thread(target=auto_check, daemon=True).start()
    bot.polling(none_stop=True)
