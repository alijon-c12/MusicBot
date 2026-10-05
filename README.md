# 🎵 O'zbek Qo'shiqlar Telegram Bot

## Loyiha tuzilishi
```
phyton.BOT/
├── bot.py          # Asosiy bot fayli
├── database.py     # Ma'lumotlar bazasi (SQLite)
├── music.py        # YouTube qidiruv va yuklab olish
├── requirements.txt
├── render.yaml     # Render.com konfiguratsiyasi
├── .env            # Token va sozlamalar (gitga qo'shilmaydi)
└── .gitignore
```

## Talablar
- Python 3.10+
- ffmpeg (audio konvertatsiya uchun)

## Lokal ishga tushirish

### 1. Virtual muhit yarating
```bash
python -m venv venv
venv\Scripts\activate   # Windows
```

### 2. Kutubxonalarni o'rnating
```bash
pip install -r requirements.txt
```

### 3. ffmpeg o'rnating
```bash
# Windows (chocolatey bilan)
choco install ffmpeg

# Yoki https://ffmpeg.org/download.html dan yuklab oling
```

### 4. .env faylini to'ldiring
```
BOT_TOKEN=8741648338:AAEcGNSLq_qPqDRaGXwqc8wB1xd9jfTYBLY
ADMIN_ID=<sizning Telegram ID raqamingiz>
CHANNEL_USERNAME=@aaaaaallllllllliiiii
```

> **ADMIN_ID ni qanday bilish mumkin?**
> @userinfobot ga /start yuboring — u sizning ID raqamingizni ko'rsatadi.

### 5. Botni ishga tushiring
```bash
python bot.py
```

---

## Render.com ga joylashtirish

### 1. GitHub repozitory yarating va kodlarni yuklang
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/SIZNING_USERNAME/uzbek-music-bot.git
git push -u origin main
```

> ⚠️ `.env` faylini gitga qo'shmang! `.gitignore` da allaqachon ko'rsatilgan.

### 2. Render.com da yangi servis yarating
1. https://render.com → **New** → **Background Worker**
2. GitHub reponi ulang
3. `Build Command`: `pip install -r requirements.txt`
4. `Start Command`: `python bot.py`

### 3. Environment Variables (Render dashboard'da)
| Key | Value |
|-----|-------|
| `BOT_TOKEN` | `8741648338:AAEcGNSLq_qPqDRaGXwqc8wB1xd9jfTYBLY` |
| `ADMIN_ID` | Sizning Telegram ID raqamingiz |
| `CHANNEL_USERNAME` | `@aaaaaallllllllliiiii` |

### 4. Deploy qiling!

---

## Bot imkoniyatlari
| Buyruq | Tavsif |
|--------|--------|
| `/start` | Botni boshlash |
| `/help` | Yordam |
| `/admin` | Admin panel (faqat admin uchun) |
| `Qo'shiq nomi` | Qo'shiq qidirish va yuklab olish |

## Admin panel
- 👥 Foydalanuvchilar soni
- 📋 Barcha foydalanuvchilar ro'yxati (username, ism, qo'shilgan sana)
- 📢 Hammaga xabar yuborish (broadcast)
