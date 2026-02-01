import asyncio
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait, RPCError
from motor.motor_asyncio import AsyncIOMotorClient

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("OWNER_ID"))
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID"))

MIN_DELAY = int(os.getenv("MIN_DELAY", 3))
HEALTH_INTERVAL = int(os.getenv("HEALTH_INTERVAL", 300))
# =========================================


# ================= BOT ====================
bot = Client(
    "assistant-manager",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ================= DB =====================
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo.assistant_manager

admins_db = db.admins
assistants_db = db.assistants
logs_db = db.logs

# ============ RUNTIME =====================
assistants = {}   # assistant_id -> Client
state = {}        # user_id -> state
temp = {}         # user_id -> data


# ================= HELPERS =================
def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


async def send_log(text: str):
    try:
        await bot.send_message(LOG_GROUP_ID, text)
        await logs_db.insert_one({"text": text, "time": now()})
    except:
        pass


async def is_admin(uid: int):
    if uid == OWNER_ID:
        return True
    return await admins_db.find_one({"user_id": uid}) is not None


# ================= UI =====================
def main_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Add Account ➕", callback_data="add_assistant")],
            [
                InlineKeyboardButton("📋 List Assistants", callback_data="list_assistant"),
                InlineKeyboardButton(",🛑 Remove Account", callback_data="remove_assistant"),
            ],
            [
                InlineKeyboardButton("➕ Join Mode", callback_data="join"),
                InlineKeyboardButton("➖ Leave Mode", callback_data="leave"),
            ],
            [
                InlineKeyboardButton("✅ Health", callback_data="health"),
                InlineKeyboardButton("📩 Specific Msg", callback_data="specific_msg"),
            ],
            [
                InlineKeyboardButton("👮 Manage Admin", callback_data="manage_admin"),
                InlineKeyboardButton("😉 Accounts Detail", callback_data="assist_detail"),
            ],
        ]
    )


# ================= START ===================
@bot.on_message(filters.command("start"))
async def start(_, m):
    if not await is_admin(m.from_user.id):
        return await m.reply("❌ Unauthorized")

    await m.reply("🤖 **Assistant Manager Bot**", reply_markup=main_menu())
    await send_log(
        f"🚀 BOT STARTED\n"
        f"By: {m.from_user.id}\n"
        f"Time: {now()}"
    )


# ================= CALLBACKS ===============
@bot.on_callback_query()
async def callbacks(_, q):
    uid = q.from_user.id
    if not await is_admin(uid):
        return await q.answer("Unauthorized", show_alert=True)

    d = q.data

    # ---------- ADD ASSISTANT ----------
    if d == "add_assistant":
        state[uid] = "ADD_API_ID"
        temp[uid] = {}
        return await q.message.edit_text("🧩 Send API ID")

    # ---------- LIST ----------
    if d == "list_assistant":
        text = "📋 **Assistants**\n\n"
        i = 1
        async for a in assistants_db.find():
            text += f"{i}) @{a['username']}\n"
            i += 1
        return await q.message.edit_text(text or "No assistants", reply_markup=main_menu())

    # ---------- REMOVE ----------
    if d == "remove_assistant":
        buttons = []
        async for a in assistants_db.find():
            if uid == OWNER_ID or a["added_by"] == uid:
                buttons.append(
                    [InlineKeyboardButton(
                        f"@{a['username']}",
                        callback_data=f"rm_{a['assistant_id']}"
                    )]
                )
        if not buttons:
            return await q.answer("No assistants you can remove", show_alert=True)
        return await q.message.edit_text(
            "❌ Select assistant",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    if d.startswith("rm_"):
        aid = int(d.split("_")[1])
        doc = await assistants_db.find_one({"assistant_id": aid})
        if not doc:
            return
        if uid != OWNER_ID and doc["added_by"] != uid:
            return await q.answer("Not allowed", show_alert=True)

        try:
            if aid in assistants:
                await assistants[aid].stop()
                assistants.pop(aid)
            await assistants_db.delete_one({"assistant_id": aid})
            await send_log(f"❌ Assistant removed\n@{doc['username']}\nBy: {uid}\nTime: {now()}")
            return await q.message.edit_text("✅ Removed", reply_markup=main_menu())
        except Exception as e:
            return await q.message.edit_text(f"❌ Error `{e}`", reply_markup=main_menu())

    # ---------- JOIN / LEAVE ----------
    if d in ["join", "leave"]:
        state[uid] = d.upper()
        temp[uid] = {}
        return await q.message.edit_text("🔗 Send group/channel link")

    # ---------- HEALTH ----------
    if d == "health":
        text = "🩺 **Assistant Health**\n\n"
        i = 1
        async for a in assistants_db.find():
            s = a.get("health", "UNKNOWN")
            emoji = "🟢" if s == "ONLINE" else "🟡" if s == "FLOOD" else "🔴"
            text += f"{i}) @{a['username']} — {emoji} {s}\n"
            i += 1
        return await q.message.edit_text(text, reply_markup=main_menu())

    # ---------- SPECIFIC MSG ----------
    if d == "specific_msg":
        state[uid] = "SPECIFIC_LINK"
        temp[uid] = {}
        return await q.message.edit_text("🔗 Send group/channel link")

    # ---------- ADMIN ----------
    if d == "manage_admin" and uid == OWNER_ID:
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("➕ Add Admin", callback_data="add_admin"),
                    InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="back")],
            ]
        )
        return await q.message.edit_text("👮 Manage Admin", reply_markup=kb)

    if d in ["add_admin", "remove_admin"]:
        state[uid] = d.upper()
        return await q.message.edit_text("👤 Send User ID")

    if d == "back":
        return await q.message.edit_text("🏠 Main Menu", reply_markup=main_menu())


# ================= INPUT HANDLER ===========
@bot.on_message(filters.private & filters.text & ~filters.regex("^/"))
async def input_handler(_, m):
    uid = m.from_user.id
    if not await is_admin(uid):
        return

    st = state.get(uid)
    if not st:
        return

    data = temp.setdefault(uid, {})

    try:
        # ===== ADD ASSISTANT FLOW =====
        if st == "ADD_API_ID":
            data["api_id"] = int(m.text)
            state[uid] = "ADD_API_HASH"
            return await m.reply("🔑 Send API HASH")

        if st == "ADD_API_HASH":
            data["api_hash"] = m.text
            state[uid] = "ADD_SESSION"
            return await m.reply("📎 Send SESSION STRING")

        if st == "ADD_SESSION":
            data["session"] = m.text
            client = Client(
                f"assistant_{len(assistants)+1}",
                api_id=data["api_id"],
                api_hash=data["api_hash"],
                session_string=data["session"],
                no_updates=True,
            )
            await client.start()
            me = await client.get_me()

            assistants[me.id] = client
            await assistants_db.insert_one({
                "assistant_id": me.id,
                "username": me.username,
                "api_id": data["api_id"],
                "api_hash": data["api_hash"],
                "session": data["session"],
                "added_by": uid,
                "health": "ONLINE"
            })

            await send_log(
                f"➕ Assistant Added\n"
                f"@{me.username}\n"
                f"API_ID: {data['api_id']}\n"
                f"By: {uid}\nTime: {now()}"
            )
            await m.reply("✅ Account added", reply_markup=main_menu())

        # ===== JOIN / LEAVE =====
        elif st in ["JOIN", "LEAVE"]:
            if "link" not in data:
                data["link"] = m.text
                return await m.reply("🔢 How many accounts?")

            if "count" not in data:
                c = int(m.text)
                if c > len(assistants):
                    await m.reply("❌ Not enough accounts")
                    return
                data["count"] = c
                return await m.reply("⏱ Delay (seconds)")

            delay = max(int(m.text), MIN_DELAY)
            selected = list(assistants.values())[: data["count"]]

            for a in selected:
                try:
                    if st == "JOIN":
                        await a.join_chat(data["link"])
                    else:
                        await a.leave_chat(data["link"])
                    await asyncio.sleep(delay)
                except FloodWait as fw:
                    await asyncio.sleep(fw.value)

            await send_log(
                f"📌 {st}\n"
                f"By: {uid}\n"
                f"Link: {data['link']}\n"
                f"Count: {data['count']}\n"
                f"Delay: {delay}\n"
                f"Time: {now()}"
            )
            await m.reply("✅ Done", reply_markup=main_menu())

        # ===== SPECIFIC MESSAGE =====
        elif st == "SPECIFIC_LINK":
            data["link"] = m.text
            text = "Select accounts (comma separated):\n\n"
            for i, a in enumerate(assistants.values(), start=1):
                text += f"{i}) @{a.me.username}\n"
            state[uid] = "SPECIFIC_SELECT"
            await m.reply(text)

        elif st == "SPECIFIC_SELECT":
            data["indexes"] = [int(x.strip())-1 for x in m.text.split(",")]
            state[uid] = "SPECIFIC_MSG"
            await m.reply("✉️ Write message")

        elif st == "SPECIFIC_MSG":
            data["message"] = m.text
            state[uid] = "SPECIFIC_DELAY"
            await m.reply("⏱ Delay per assistant")

        elif st == "SPECIFIC_DELAY":
            delay = max(int(m.text), MIN_DELAY)
            selected = list(assistants.values())
            for i in data["indexes"]:
                try:
                    await selected[i].send_message(data["link"], data["message"])
                    await asyncio.sleep(delay)
                except FloodWait as fw:
                    await asyncio.sleep(fw.value)

            await send_log(
                f"📩 SPECIFIC MESSAGE\n"
                f"By: {uid}\nLink: {data['link']}\nTime: {now()}"
            )
            await m.reply("✅ Message sent", reply_markup=main_menu())

        # ===== ADMIN =====
        elif st == "ADD_ADMIN" and uid == OWNER_ID:
            await admins_db.insert_one({"user_id": int(m.text)})
            await send_log(f"➕ Admin Added\nUser: {m.text}\nBy: {uid}\nTime: {now()}")
            await m.reply("✅ Admin added", reply_markup=main_menu())

        elif st == "REMOVE_ADMIN" and uid == OWNER_ID:
            await admins_db.delete_one({"user_id": int(m.text)})
            await send_log(f"➖ Admin Removed\nUser: {m.text}\nBy: {uid}\nTime: {now()}")
            await m.reply("✅ Admin removed", reply_markup=main_menu())

    except Exception as e:
        await m.reply(f"❌ Error: `{e}`")

    finally:
        state.pop(uid, None)
        temp.pop(uid, None)


# ================= HEALTH MONITOR ==========
async def health_monitor():
    while True:
        for aid, client in list(assistants.items()):
            try:
                await client.get_me()
                await assistants_db.update_one(
                    {"assistant_id": aid},
                    {"$set": {"health": "ONLINE", "last_check": now()}}
                )
            except FloodWait:
                await assistants_db.update_one(
                    {"assistant_id": aid},
                    {"$set": {"health": "FLOOD"}}
                )
            except:
                await assistants_db.update_one(
                    {"assistant_id": aid},
                    {"$set": {"health": "OFFLINE"}}
                )
        await asyncio.sleep(HEALTH_INTERVAL)


# ================= LOAD ASSISTANTS =========
async def load_assistants():
    async for a in assistants_db.find():
        try:
            cl = Client(
                f"assistant_{a['assistant_id']}",
                api_id=a["api_id"],
                api_hash=a["api_hash"],
                session_string=a["session"],
                no_updates=True,
            )
            await cl.start()
            assistants[a["assistant_id"]] = cl
        except:
            pass


# ================= RUN =====================
async def main():
    await bot.start()
    await load_assistants()
    asyncio.create_task(health_monitor())
    print("Assistant Manager Bot Running...")
    await asyncio.Event().wait()

asyncio.run(main())
