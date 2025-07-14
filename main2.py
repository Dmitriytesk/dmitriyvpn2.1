import os
import subprocess
import logging
import html
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import (
    FSInputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
from config import BOT_TOKEN, WG_DIR, WG_SERVER_CONFIG, WG_SERVER_IP, WG_SERVER_PORT, WG_SERVER_PUBKEY, ADMIN_IDS

class AdminStates(StatesGroup):
    waiting_for_client_name = State()
    waiting_for_delete_confirmation = State()
    waiting_for_access_decision = State()
    waiting_for_broadcast_confirmation = State()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

access_requests = set()
ALLOWED_USERS_FILE = "allowed_users.txt"

logging.basicConfig(
    level=logging.DEBUG, 
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
def load_allowed_users():
    if os.path.exists(ALLOWED_USERS_FILE):
        with open(ALLOWED_USERS_FILE, 'r') as f:
            return [int(line.strip()) for line in f if line.strip()]
    return []

def save_allowed_user(user_id):
    allowed = load_allowed_users()
    if user_id not in allowed:
        allowed.append(user_id)
        with open(ALLOWED_USERS_FILE, 'w') as f:
            for uid in allowed:
                f.write(f"{uid}\n")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_allowed(user_id: int) -> bool:
    return user_id in ADMIN_IDS or user_id in load_allowed_users()
def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    buttons = [[KeyboardButton(text="ℹ️ Помощь")]]
    if is_admin(user_id):
        buttons.append([KeyboardButton(text="👑 Админ")])
        buttons[0].append(KeyboardButton(text="🆕 Создать конфиг"))
    elif is_allowed(user_id):
        buttons[0].append(KeyboardButton(text="🆕 Создать конфиг"))
    else:
        buttons.append([KeyboardButton(text="🔒 Запросить доступ")])
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

help_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="❓ Как подключиться")],
        [KeyboardButton(text="🔙 Назад")]
    ],
    resize_keyboard=True
)

admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🗑 Удалить конфиг"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🔄 Перезагрузить сервер"), KeyboardButton(text="👥 Запросы доступа")],
        [KeyboardButton(text="🔙 Назад")]
    ],
    resize_keyboard=True
)

def generate_keys(client_name: str) -> tuple[str, str]:
    try:
        priv_key = subprocess.check_output("wg genkey", shell=True, text=True).strip()
        pub_key = subprocess.check_output(f"echo '{priv_key}' | wg pubkey", shell=True, text=True).strip()
        psk = subprocess.check_output("wg genpsk", shell=True, text=True).strip()

        used_ips = set()
        if os.path.exists(WG_SERVER_CONFIG):
            with open(WG_SERVER_CONFIG, 'r') as f:
                for line in f:
                    if "AllowedIPs = 10.66.66." in line:
                        ip = int(line.split("=")[1].strip().split(".")[3].split("/")[0])
                        used_ips.add(ip)

        new_ip = next(i for i in range(2, 254) if i not in used_ips)
        client_ipv4 = f"10.66.66.{new_ip}/32"
        client_ipv6 = f"fd42:42:42::{new_ip}/128"

        client_conf = f"""[Interface]
PrivateKey = {priv_key}
Address = {client_ipv4}, {client_ipv6}
DNS = 1.1.1.1, 1.0.0.1

[Peer]
PublicKey = {WG_SERVER_PUBKEY}
PresharedKey = {psk}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = {WG_SERVER_IP}:{WG_SERVER_PORT}
PersistentKeepalive = 25
"""

        server_peer = f"\n[Peer]\n### {client_name}\nPublicKey = {pub_key}\nPresharedKey = {psk}\nAllowedIPs = {client_ipv4}, {client_ipv6}\n"
        
        with open(WG_SERVER_CONFIG, 'a') as f:
            f.write(server_peer)

        apply_wg_config()
        return client_conf, client_ipv4

    except Exception as e:
        logger.error(f"Ошибка генерации ключей: {e}")
        raise

def apply_wg_config():
    try:
        subprocess.run(["wg", "syncconf", "wg0", WG_SERVER_CONFIG], check=True)
    except subprocess.CalledProcessError:
        try:
            subprocess.run(["wg-quick", "down", "wg0"], check=True)
            subprocess.run(["wg-quick", "up", "wg0"], check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка перезагрузки WireGuard: {e}")
            raise

def get_client_names() -> list[str]:
    clients = []
    if os.path.exists(WG_SERVER_CONFIG):
        with open(WG_SERVER_CONFIG, 'r') as f:
            for line in f:
                if line.startswith("### "):
                    clients.append(line[4:].strip())
    return clients

def delete_client(client_name: str) -> bool:
    try:
        with open(WG_SERVER_CONFIG, 'r') as f:
            lines = f.readlines()

        start = next((i for i, line in enumerate(lines) if line.startswith(f"### {client_name}")), None)
        if start is None:
            return False

        end = next((i for i in range(start+1, len(lines)) if lines[i].strip() == ""), len(lines))
        
        with open(WG_SERVER_CONFIG, 'w') as f:
            f.writelines(lines[:start] + lines[end:])
        
        apply_wg_config()
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления клиента: {e}")
        return False

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="🔒 Запросить доступ")] if not is_allowed(message.from_user.id) else []
        ],
        resize_keyboard=True
    )
    
    if is_allowed(message.from_user.id):
        keyboard.keyboard[0].append(KeyboardButton(text="🆕 Создать конфиг"))
    
    if is_admin(message.from_user.id):
        keyboard.keyboard.append([KeyboardButton(text="👑 Админ")])
    
    await message.answer(
        "🔐 <b>DmitriyVPN Bot</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

@dp.message(F.text == "🆕 Создать конфиг")
async def create_config(message: types.Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой функции. Запросите доступ у администратора.")
        return
    
    await message.answer(
        "Введите имя для конфига (английскими буквами без пробелов):",
        reply_markup=ReplyKeyboardRemove()
    )

@dp.message(F.text == "📋 Список подключений")
async def list_connections(message: types.Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой функции. Запросите доступ у администратора.")
        return
        
    try:
        result = subprocess.run(
            ["wg", "show", "wg0"],
            capture_output=True, text=True, encoding='utf-8'
        )
        
        if result.returncode == 0 and result.stdout.strip():
            response = "🔷 <b>Активные подключения:</b>\n\n<pre>" + result.stdout + "</pre>"
        else:
            response = "ℹ️ Нет активных подключений"
        
        await message.answer(response, parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.answer("❌ Ошибка получения данных")
        logger.error(f"Ошибка list_connections: {e}")

@dp.message(F.text == "ℹ️ Помощь")
async def show_help(message: types.Message):
    await message.answer(
        "ℹ️ <b>Доступные команды:</b>\n\n"
        "🆕 Создать конфиг - новый VPN-конфиг\n"
        "❓ Как подключиться - инструкция",
        parse_mode=ParseMode.HTML,
        reply_markup=help_keyboard
    )

@dp.message(F.text == "❓ Как подключиться")
async def connection_guide(message: types.Message):
    await message.answer(
        "📱 <b>Как подключиться:</b>\n\n"
        "1. Установите WireGuard с официального сайта\n"
        "2. Импортируйте полученный конфиг\n"
        "3. Активируйте подключение\n\n"
        "Для ПК: импортируйте файл .conf\n"
        "Для телефона: отсканируйте QR-код",
        parse_mode=ParseMode.HTML,
        reply_markup=help_keyboard
    )

@dp.message(F.text == "🔒 Запросить доступ")
async def request_access(message: types.Message):
    user_id = message.from_user.id
    if is_allowed(user_id):
        await message.answer("✅ У вас уже есть доступ к функциям бота")
        return
    
    if user_id in access_requests:
        # Создаем ссылку на первого администратора
        admin_link = f"<a href='tg://user?id={ADMIN_IDS[0]}'>администратору</a>"
        await message.answer(
            f"⏳ Ваш запрос уже отправлен и ожидает рассмотрения")
        return
    
    access_requests.add(user_id)
    
    # Отправляем уведомление всем админам
    for admin_id in ADMIN_IDS:
        try:
            # Формируем ссылку на пользователя
            user_link = f"<a href='tg://user?id={user_id}'>{html.escape(message.from_user.full_name)}</a>"
            
            await bot.send_message(
                admin_id,
                f"🔔 Новый запрос на доступ:\n\n"
                f"👤 Пользователь: {user_link}\n"
                f"🆔 ID: {user_id}\n\n"
                f"Разрешить доступ?",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[
                        [KeyboardButton(text=f"✅ Разрешить {user_id}")],
                        [KeyboardButton(text=f"❌ Отклонить {user_id}")],
                        [KeyboardButton(text="🔙 Назад")]
                    ],
                    resize_keyboard=True
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка отправки запроса админу {admin_id}: {e}")
    admin_name = "администратору"        
    admin_link = f"<a href='tg://user?id={ADMIN_IDS[0]}'>администратору</a>"
    await message.answer(
        f"✅ Ваш запрос на доступ отправлен администратору. Ожидайте решения.\n"
        f"Произвести оплату - {admin_link}",
        reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.HTML
    )

@dp.message(F.text.startswith("✅ Разрешить "))
async def grant_access(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    try:
        user_id = int(message.text.split()[-1])
        if user_id in access_requests:
            access_requests.remove(user_id)
            save_allowed_user(user_id)
            
            # Уведомляем пользователя
            await bot.send_message(
                user_id,
                "🎉 Ваш запрос на доступ был одобрен! Теперь вы можете пользоваться ботом.",
                reply_markup=get_main_keyboard(user_id)
            )
            
            await message.answer(
                f"✅ Доступ для пользователя {user_id} разрешен",
                reply_markup=admin_keyboard
            )
        else:
            await message.answer("❌ Запрос не найден", reply_markup=admin_keyboard)
    except Exception as e:
        logger.error(f"Ошибка grant_access: {e}")
        await message.answer("❌ Ошибка обработки запроса", reply_markup=admin_keyboard)

@dp.message(F.text.startswith("❌ Отклонить "))
async def deny_access(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    try:
        user_id = int(message.text.split()[-1])
        if user_id in access_requests:
            access_requests.remove(user_id)
            
            # Уведомляем пользователя
            await bot.send_message(
                user_id,
                "❌ Ваш запрос на доступ был отклонён администратором.",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="🔒 Запросить доступ")]],
                    resize_keyboard=True
                )
            )
            
            await message.answer(
                f"❌ Доступ для пользователя {user_id} отклонён",
                reply_markup=admin_keyboard
            )
        else:
            await message.answer("❌ Запрос не найден", reply_markup=admin_keyboard)
    except Exception as e:
        logger.error(f"Ошибка deny_access: {e}")
        await message.answer("❌ Ошибка обработки запроса", reply_markup=admin_keyboard)

@dp.message(F.text == "👥 Запросы доступа")
async def show_access_requests(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    if not access_requests:
        await message.answer("ℹ️ Нет активных запросов на доступ", reply_markup=admin_keyboard)
        return
    
    requests_text = "\n".join(f"🆔 {user_id}" for user_id in access_requests)
    await message.answer(
        f"🔔 Активные запросы на доступ:\n\n{requests_text}",
        reply_markup=admin_keyboard
    )


class Form(StatesGroup):
    waiting_for_config_name = State()  # Состояние для ввода имени конфига

@dp.message(F.text == "🆕 Создать конфиг")
async def start_create_config(message: types.Message, state: FSMContext):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой функции")
        return
    
    await message.answer(
        "Введите имя для конфига (английскими буквами без пробелов):",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(Form.waiting_for_config_name)

@dp.message(Form.waiting_for_config_name)
async def process_config_name(message: types.Message, state: FSMContext):
    try:
        name = message.text.strip()
        
        # Проверка имени
        if not name.isalnum():
            await message.answer(
                "❌ Имя должно содержать только буквы и цифры\nПопробуйте еще раз:",
                reply_markup=ReplyKeyboardRemove()
            )
            return  # Оставляем в том же состоянии для повторного ввода
        
        # Создание конфига
        config, ip = generate_keys(name)
        
        # Сохранение временного файла
        conf_path = f"/tmp/{name}.conf"
        with open(conf_path, 'w') as f:
            f.write(config)
        
        # Отправка конфига
        await message.reply_document(
            FSInputFile(conf_path, filename=f"wg_{name}.conf"),
            caption=f"🔑 Конфиг {name} (IP: {ip.split('/')[0]})",
            reply_markup=get_main_keyboard(message.from_user.id)
        )

        # Генерация и отправка QR-кода
        qr_path = f"/tmp/{name}.png"
        subprocess.run(f"qrencode -o {qr_path} -t PNG < {conf_path}", shell=True)
        await message.reply_photo(FSInputFile(qr_path))

        # Удаление временных файлов
        os.unlink(conf_path)
        os.unlink(qr_path)
        
        # Очистка состояния
        await state.clear()

    except Exception as e:
        await message.answer(
            f"❌ Ошибка при создании конфига: {str(e)}",
            reply_markup=get_main_keyboard(message.from_user.id)
        )
        logger.error(f"Ошибка создания конфига: {e}")
        await state.clear()

# Удаляем старый обработчик process_config_name и заменяем его на:
@dp.message(
    F.text &
    ~F.text.startswith(("👑", "🔙")) &
    ~F.text.in_({
        "🆕 Создать конфиг", "ℹ️ Помощь",
        "❓ Как подключиться", "🗑 Удалить конфиг", "📊 Статистика", 
        "🔄 Перезагрузить сервер", "✅ Да", "❌ Нет", "🔒 Запросить доступ",
        "👥 Запросы доступа", "📋 Список подключений"
    })
)
async def handle_other_messages(message: types.Message):
    # Просто игнорируем все сообщения, не соответствующие командам
    pass

@dp.message(F.text == "👑 Админ")
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещен")
        return
    
    await message.answer(
        "👑 <b>Админ-панель</b>\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard
    )

@dp.message(F.text == "🗑 Удалить конфиг")
async def delete_config_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещен")
        return

    clients = get_client_names()
    if not clients:
        await message.answer("ℹ️ Нет клиентов для удаления", reply_markup=admin_keyboard)
        return
    
    clients_text = "\n".join(f"• {name}" for name in clients)
    await message.answer(
        f"🗑 <b>Выберите клиента для удаления:</b>\n{clients_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(AdminStates.waiting_for_client_name)

@dp.message(AdminStates.waiting_for_client_name)
async def delete_config_confirm(message: types.Message, state: FSMContext):
    client_name = message.text.strip()
    current_clients = get_client_names()
    
    if client_name not in current_clients:
        await message.answer(
            "❌ Клиент не найден\nПопробуйте еще раз:",
            reply_markup=admin_keyboard
        )
        await state.clear()
        return
    
    await state.update_data(client_name=client_name)
    await message.answer(
        f"⚠️ Удалить клиента <b>{client_name}</b>?\nЭто действие необратимо!",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminStates.waiting_for_delete_confirmation)

@dp.message(AdminStates.waiting_for_delete_confirmation, F.text == "✅ Да")
async def delete_config_execute(message: types.Message, state: FSMContext):
    data = await state.get_data()
    client_name = data["client_name"]
    
    if delete_client(client_name):
        await message.answer(
            f"✅ Клиент <b>{client_name}</b> удален",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_keyboard
        )
    else:
        await message.answer(
            "❌ Ошибка при удалении",
            reply_markup=admin_keyboard
        )
    await state.clear()

@dp.message(AdminStates.waiting_for_delete_confirmation, F.text == "❌ Нет")
async def delete_config_cancel(message: types.Message, state: FSMContext):
    await message.answer(
        "❌ Удаление отменено",
        reply_markup=admin_keyboard
    )
    await state.clear()

@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещен")
        return
    
    try:
        clients = get_client_names()
        result = subprocess.run(
            ["wg", "show", "wg0"],
            capture_output=True, text=True, encoding='utf-8'
        )
        
        stats = "ℹ️ Нет данных" if not result.stdout.strip() else f"<pre>{result.stdout}</pre>"
        
        await message.answer(
            f"📊 <b>Статистика сервера</b>\n\n"
            f"👤 Всего клиентов: {len(clients)}\n\n"
            f"{stats}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer("❌ Ошибка получения статистики")
        logger.error(f"Ошибка show_stats: {e}")

@dp.message(F.text == "🔄 Перезагрузить сервер")
async def restart_server(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещен")
        return
    
    try:
        subprocess.run(["wg-quick", "down", "wg0"], check=True)
        subprocess.run(["wg-quick", "up", "wg0"], check=True)
        await message.answer("🔄 Сервер успешно перезагружен")
    except subprocess.CalledProcessError as e:
        await message.answer(f"❌ Ошибка: {e.stderr}")
        logger.error(f"Ошибка restart_server: {e}")
    except Exception as e:
        await message.answer("❌ Неизвестная ошибка")
        logger.error(f"Ошибка restart_server: {e}")

@dp.message(F.text == "🔙 Назад")
async def back_to_main(message: types.Message):
    await message.answer(
        "Главное меню:",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

@dp.message(Command("send"))
async def cmd_send(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам")
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Укажите текст сообщения после команды /send")
        return
    
    await state.set_state(AdminStates.waiting_for_broadcast_confirmation)
    await state.update_data(broadcast_text=args[1])
    
    await message.answer(
        f"Подтвердите рассылку этого сообщения всем пользователям:\n\n{args[1]}",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="✅ Подтвердить рассылку")],
                [KeyboardButton(text="❌ Отменить")]
            ],
            resize_keyboard=True
        )
    )

@dp.message(F.text == "✅ Подтвердить рассылку", AdminStates.waiting_for_broadcast_confirmation)
async def confirm_broadcast(message: types.Message, state: FSMContext):
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text", "")
    await state.clear()
    
    if not broadcast_text:
        await message.answer("❌ Текст рассылки не найден", reply_markup=admin_keyboard)
        return
    
    all_users = set(ADMIN_IDS + load_allowed_users())
    success = 0
    failed = 0
    
    for user_id in all_users:
        try:
            await bot.send_message(
                user_id,
                f"📢 <b>Сообщение от администратора:</b>\n\n{broadcast_text}",
                parse_mode=ParseMode.HTML
            )
            success += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
            failed += 1
    
    await message.answer(
        f"✅ Рассылка завершена:\n"
        f"• Успешно: {success}\n"
        f"• Не удалось: {failed}",
        reply_markup=admin_keyboard
    )

@dp.message(F.text == "❌ Отменить", AdminStates.waiting_for_broadcast_confirmation)
async def cancel_broadcast(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Рассылка отменена", reply_markup=admin_keyboard)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())