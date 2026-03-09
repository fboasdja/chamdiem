# discord_bot.py - Module để chạy bot Discord
# Import discord package (không phải file local)
import sys
import os

# Đảm bảo import đúng package discord, không phải file local
if os.path.exists('discord.py'):
    print("WARNING: File discord.py tồn tại, có thể gây conflict!")
    
# Import discord package
try:
    import discord
    from discord import app_commands
    from discord.ext import commands
except ImportError as e:
    print(f"Lỗi import discord: {e}")
    raise

import aiohttp
import asyncio

# Đọc token từ file
def get_token():
    # 1️⃣ Ưu tiên biến môi trường (Render)
    token = os.environ.get("TOKEN")
    if token:
        return token.strip()

# Đọc danh sách admin IDs từ file
def get_admin_ids():
    """Đọc danh sách admin IDs từ ids.txt"""
    try:
        with open('ids.txt', 'r', encoding='utf-8') as f:
            ids = []
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):  # Bỏ qua dòng trống và comment
                    ids.append(line)
            return ids
    except FileNotFoundError:
        print("Không tìm thấy file ids.txt, tạo file mới")
        # Tạo file ids.txt nếu chưa có
        with open('ids.txt', 'w', encoding='utf-8') as f:
            f.write("# Mỗi dòng là 1 Discord User ID của admin\n")
        return []

# Kiểm tra quyền admin
def is_admin(user_id):
    """Kiểm tra xem user có phải admin không"""
    admin_ids = get_admin_ids()
    return str(user_id) in admin_ids

# URL của API Flask (thay đổi nếu cần)
# Dùng 127.0.0.1 để tránh vấn đề DNS/IPv6 với localhost trên Windows
API_BASE = "http://127.0.0.1:5000"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Biến để track xem đã sync chưa
_commands_synced = False

@bot.event
async def on_ready():
    print(f'{bot.user} đã kết nối!')
    
    # Tắt sync tự động để tránh rate limit
    # Commands sẽ được sync tự động bởi Discord khi bot khởi động lần đầu
    # Hoặc có thể sync thủ công bằng lệnh nếu cần
    print("✅ Bot sẵn sàng! Commands sẽ tự động sync khi cần.")
    
    # Chỉ sync nếu file .force_sync tồn tại (để sync thủ công khi cần)
    import os
    if os.path.exists('.force_sync'):
        print("🔄 File .force_sync được tìm thấy, đang sync commands...")
        global _commands_synced
        if not _commands_synced:
            await sync_commands_with_retry()
            _commands_synced = True
        os.remove('.force_sync')
        print("✅ Đã xóa file .force_sync")

async def sync_commands_with_retry(max_retries=3, initial_delay=5):
    """Sync commands với retry logic và delay để tránh rate limit"""
    # Kiểm tra xem có cần sync không (chỉ sync khi bot mới khởi động)
    import os
    sync_file = '.commands_synced'
    
    # Nếu đã sync gần đây (trong vòng 1 giờ), bỏ qua
    if os.path.exists(sync_file):
        import time
        if time.time() - os.path.getmtime(sync_file) < 3600:  # 1 giờ
            print("⏭️ Commands đã được sync gần đây, bỏ qua.")
            return True
    
    for attempt in range(max_retries):
        try:
            # Delay trước khi sync để tránh rate limit
            if attempt > 0:
                delay = initial_delay * (2 ** attempt)  # Exponential backoff
                print(f"⏳ Đợi {delay} giây trước khi retry sync commands...")
                await asyncio.sleep(delay)
            else:
                # Delay lớn hơn cho lần đầu để tránh rate limit
                print("⏳ Đợi 5 giây trước khi sync commands...")
                await asyncio.sleep(5)
            
            synced = await bot.tree.sync()
            print(f"✅ Đã đồng bộ {len(synced)} lệnh slash thành công.")
            # Lưu thời gian sync
            with open(sync_file, 'w') as f:
                f.write(str(time.time()))
            return True
        except discord.errors.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = e.retry_after if hasattr(e, 'retry_after') else (initial_delay * (2 ** attempt))
                print(f"⚠️ Rate limited. Đợi {retry_after} giây...")
                await asyncio.sleep(retry_after)
            else:
                print(f"❌ Lỗi HTTP khi sync commands: {e}")
                if attempt == max_retries - 1:
                    print(f"⚠️ Không thể sync commands sau {max_retries} lần thử. Bot vẫn hoạt động bình thường.")
                    return False
        except Exception as e:
            print(f"❌ Lỗi khi đồng bộ lệnh: {e}")
            if attempt == max_retries - 1:
                print(f"⚠️ Không thể sync commands sau {max_retries} lần thử. Bot vẫn hoạt động bình thường.")
                return False
    
    return False

@bot.tree.command(name="addaccount", description="(Đã tắt) Tạo tài khoản hiện chỉ làm trên web")
async def addaccount(interaction: discord.Interaction):
    await interaction.response.send_message(
        "❌ Lệnh này đã bị tắt. Vui lòng tạo tài khoản trực tiếp trên web (tab Manage của admin).",
        ephemeral=True
    )

@bot.tree.command(name="getuserid", description="Lấy Discord User ID")
@app_commands.describe(user="Người dùng cần lấy ID (mention hoặc để trống để lấy ID của bạn)")
async def getuserid(interaction: discord.Interaction, user: discord.Member = None):
    """Lệnh để lấy Discord User ID"""
    target_user = user if user else interaction.user
    
    await interaction.response.send_message(
        f"**Discord User ID:**\n"
        f"Tên: {target_user.display_name}\n"
        f"ID: `{target_user.id}`\n"
        f"Mention: <@{target_user.id}>",
        ephemeral=True
    )

@bot.tree.command(name="help", description="Xem hướng dẫn sử dụng")
async def help_command(interaction: discord.Interaction):
    """Lệnh help"""
    embed = discord.Embed(
        title="📖 Hướng dẫn sử dụng",
        description="Các lệnh có sẵn:",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="/addaccount",
        value="(Đã tắt) Tạo tài khoản hiện chỉ làm trên web (tab Manage của admin).",
        inline=False
    )
    
    embed.add_field(
        name="/getuserid",
        value="Lấy Discord User ID\n"
              "**Cú pháp:** `/getuserid [user:<mention>]`\n"
              "**Ví dụ:** `/getuserid` hoặc `/getuserid user:@someone`",
        inline=False
    )
    
    embed.add_field(
        name="Phân quyền:",
        value="• **admin**: Full quyền\n"
              "• **editer**: Xem và chỉnh sửa Main, xem Thống kê\n"
              "• **user**: Chỉ xem Thống kê",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

def run_bot():
    """Hàm để chạy bot"""
    token = get_token()
    if token:
        bot.run(token)
    else:
        print("Không thể khởi động bot vì thiếu token!")