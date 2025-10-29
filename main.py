import os
import logging
import requests
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Lưu trữ cảnh báo (trong thực tế nên dùng database, nhưng để đơn giản dùng dict)
user_alerts = {}

# Hàm lấy giá cổ phiếu từ API VN
def get_stock_price(symbol):
    """Lấy giá cổ phiếu từ SSI API hoặc VietStock"""
    try:
        # Thử SSI API trước
        url = f"https://iboard.ssi.com.vn/dchart/api/1.1/defaultAllStocks"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            for stock in data:
                if stock.get('stockSymbol', '').upper() == symbol.upper():
                    return {
                        'price': stock.get('lastPrice', 0),
                        'change': stock.get('priceChange', 0),
                        'change_percent': stock.get('percentPriceChange', 0)
                    }
        
        # Nếu không tìm thấy, thử API khác
        url2 = f"https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker={symbol.upper()}&type=stock&resolution=D&from=0&to=9999999999"
        response2 = requests.get(url2, timeout=10)
        
        if response2.status_code == 200:
            data2 = response2.json()
            if data2.get('data') and len(data2['data']) > 0:
                latest = data2['data'][-1]
                return {
                    'price': latest.get('close', 0),
                    'change': 0,
                    'change_percent': 0
                }
    except Exception as e:
        logger.error(f"Error getting stock price: {e}")
    
    return None

# Hàm kiểm tra sự kiện GDKHQ
def check_gdkhq_event(symbol):
    """Kiểm tra ngày GDKHQ trong 3 tháng tới"""
    try:
        # API lịch sự kiện từ các nguồn công khai
        urls = [
            f"https://apipubaws.tcbs.com.vn/tcanalysis/v1/company/{symbol.upper()}/event-timeline",
            f"https://finfo-api.vndirect.com.vn/v4/events?q=ticker:{symbol.upper()}~type:GDKHQ~from:{datetime.now().strftime('%Y-%m-%d')}~to:{(datetime.now() + timedelta(days=90)).strftime('%Y-%m-%d')}"
        ]
        
        for url in urls:
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(url, headers=headers, timeout=10)
                
                # Bỏ qua lỗi 403, 404
                if response.status_code in [403, 404]:
                    continue
                    
                if response.status_code == 200:
                    data = response.json()
                    
                    # Xử lý data từ TCBS
                    if 'listEventQuarter' in data:
                        for event in data.get('listEventQuarter', []):
                            event_type = event.get('ticker', '')
                            if 'GDKHQ' in event_type or 'Record' in event_type:
                                event_date = event.get('exrightDate', '')
                                if event_date:
                                    return {
                                        'has_event': True,
                                        'date': event_date,
                                        'type': 'GDKHQ'
                                    }
                    
                    # Xử lý data từ VNDirect
                    if 'data' in data and isinstance(data['data'], list):
                        for event in data['data']:
                            if event.get('type') == 'GDKHQ':
                                event_date = event.get('recordDate', '')
                                if event_date:
                                    return {
                                        'has_event': True,
                                        'date': event_date,
                                        'type': 'GDKHQ'
                                    }
            except:
                continue
        
        return {'has_event': False, 'date': '', 'type': ''}
    except Exception as e:
        logger.error(f"Error checking GDKHQ: {e}")
        return {'has_event': False, 'date': '', 'type': ''}

# Lệnh /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hướng dẫn sử dụng bot"""
    welcome_text = """
🤖 *Chào mừng đến với Stock Alert Bot VN!*

📌 *Các lệnh có sẵn:*
/alert MaCK Gia - Tạo cảnh báo giá
   Ví dụ: /alert VNM 80000

/list - Xem danh sách cảnh báo

/delete SoThuTu - Xóa cảnh báo
   Ví dụ: /delete 1

/price MaCK - Xem giá hiện tại
   Ví dụ: /price VNM

/help - Xem hướng dẫn

💡 Bot sẽ tự động kiểm tra giá mỗi 5 phút và thông báo khi đạt mục tiêu!
    """
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

# Lệnh /alert
async def alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tạo cảnh báo giá"""
    user_id = update.effective_user.id
    
    if len(context.args) < 2:
        await update.message.reply_text("❌ Sai cú pháp! Dùng: /alert MaCK Gia\nVí dụ: /alert VNM 80000")
        return
    
    symbol = context.args[0].upper()
    try:
        target_price = float(context.args[1])
    except:
        await update.message.reply_text("❌ Giá không hợp lệ! Vui lòng nhập số.")
        return
    
    # Kiểm tra giá hiện tại
    stock_info = get_stock_price(symbol)
    if not stock_info:
        await update.message.reply_text(f"❌ Không tìm thấy mã {symbol}. Vui lòng kiểm tra lại!")
        return
    
    # Kiểm tra sự kiện GDKHQ
    await update.message.reply_text(f"⏳ Đang kiểm tra sự kiện GDKHQ cho {symbol}...")
    gdkhq_info = check_gdkhq_event(symbol)
    
    # Lưu cảnh báo
    if user_id not in user_alerts:
        user_alerts[user_id] = []
    
    alert_data = {
        'symbol': symbol,
        'target_price': target_price,
        'current_price': stock_info['price'],
        'created_at': datetime.now(),
        'gdkhq_info': gdkhq_info,
        'gdkhq_notified_1month': False
    }
    user_alerts[user_id].append(alert_data)
    
    # Thông báo kết quả
    gdkhq_text = ""
    if gdkhq_info['has_event']:
        gdkhq_text = f"\n\n⚠️ *Cảnh báo GDKHQ:*\nNgày: {gdkhq_info['date']}\nBot sẽ nhắc lại khi còn 1 tháng!"
    else:
        gdkhq_text = "\n\n✅ *GDKHQ:* SAFE (Không có sự kiện trong 3 tháng tới)"
    
    response = f"""
✅ *Đã tạo cảnh báo!*

📊 Mã: {symbol}
💰 Giá hiện tại: {stock_info['price']:,.0f}
🎯 Giá mục tiêu: {target_price:,.0f}
📈 Thay đổi: {stock_info['change_percent']:.2f}%{gdkhq_text}
    """
    
    await update.message.reply_text(response, parse_mode='Markdown')

# Lệnh /list
async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem danh sách cảnh báo"""
    user_id = update.effective_user.id
    
    if user_id not in user_alerts or len(user_alerts[user_id]) == 0:
        await update.message.reply_text("📭 Bạn chưa có cảnh báo nào!")
        return
    
    response = "*📋 Danh sách cảnh báo của bạn:*\n\n"
    for idx, alert in enumerate(user_alerts[user_id], 1):
        stock_info = get_stock_price(alert['symbol'])
        current_price = stock_info['price'] if stock_info else alert['current_price']
        
        gdkhq_status = "✅ SAFE" if not alert['gdkhq_info']['has_event'] else f"⚠️ GDKHQ: {alert['gdkhq_info']['date']}"
        
        response += f"""
{idx}. *{alert['symbol']}*
   Giá hiện tại: {current_price:,.0f}
   Mục tiêu: {alert['target_price']:,.0f}
   {gdkhq_status}
   
"""
    
    response += "\n💡 Dùng /delete <số> để xóa cảnh báo"
    await update.message.reply_text(response, parse_mode='Markdown')

# Lệnh /delete
async def delete_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xóa cảnh báo"""
    user_id = update.effective_user.id
    
    if len(context.args) < 1:
        await update.message.reply_text("❌ Sai cú pháp! Dùng: /delete <số thứ tự>")
        return
    
    try:
        index = int(context.args[0]) - 1
        if user_id in user_alerts and 0 <= index < len(user_alerts[user_id]):
            deleted = user_alerts[user_id].pop(index)
            await update.message.reply_text(f"✅ Đã xóa cảnh báo {deleted['symbol']}!")
        else:
            await update.message.reply_text("❌ Không tìm thấy cảnh báo này!")
    except:
        await update.message.reply_text("❌ Số thứ tự không hợp lệ!")

# Lệnh /price
async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem giá cổ phiếu"""
    if len(context.args) < 1:
        await update.message.reply_text("❌ Sai cú pháp! Dùng: /price MaCK")
        return
    
    symbol = context.args[0].upper()
    stock_info = get_stock_price(symbol)
    
    if not stock_info:
        await update.message.reply_text(f"❌ Không tìm thấy mã {symbol}")
        return
    
    response = f"""
📊 *{symbol}*

💰 Giá: {stock_info['price']:,.0f}
📈 Thay đổi: {stock_info['change']:,.0f} ({stock_info['change_percent']:.2f}%)
    """
    await update.message.reply_text(response, parse_mode='Markdown')

# Hàm kiểm tra cảnh báo định kỳ
async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Kiểm tra tất cả cảnh báo mỗi 5 phút"""
    for user_id, alerts in user_alerts.items():
        for alert in alerts[:]:
            stock_info = get_stock_price(alert['symbol'])
            if not stock_info:
                continue
            
            current_price = stock_info['price']
            
            # Kiểm tra đạt mục tiêu
            if current_price >= alert['target_price']:
                message = f"""
🎯 *CẢNH BÁO GIÁ ĐẠT MỤC TIÊU!*

📊 Mã: {alert['symbol']}
💰 Giá hiện tại: {current_price:,.0f}
🎯 Giá mục tiêu: {alert['target_price']:,.0f}
✅ Đã đạt mục tiêu!
                """
                
                try:
                    await context.bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
                    alerts.remove(alert)
                except:
                    pass
            
            # Kiểm tra GDKHQ còn 1 tháng
            if alert['gdkhq_info']['has_event'] and not alert['gdkhq_notified_1month']:
                try:
                    event_date = datetime.strptime(alert['gdkhq_info']['date'], '%Y-%m-%d')
                    days_left = (event_date - datetime.now()).days
                    
                    if days_left <= 30 and days_left > 0:
                        message = f"""
⚠️ *NHẮC NHỞ GDKHQ*

📊 Mã: {alert['symbol']}
📅 Ngày GDKHQ: {alert['gdkhq_info']['date']}
⏰ Còn {days_left} ngày nữa!
                        """
                        await context.bot.send_message(chat_id=user_id, text=message, parse_mode='Markdown')
                        alert['gdkhq_notified_1month'] = True
                except:
                    pass

# Main
def main():
    """Khởi chạy bot"""
    TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        print("❌ Lỗi: Chưa có TELEGRAM_BOT_TOKEN! Vui lòng thêm token vào biến môi trường.")
        return
    
    # Tạo application
    application = Application.builder().token(TOKEN).build()
    
    # Thêm handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("alert", alert))
    application.add_handler(CommandHandler("list", list_alerts))
    application.add_handler(CommandHandler("delete", delete_alert))
    application.add_handler(CommandHandler("price", price))
    
    # Thêm job kiểm tra cảnh báo mỗi 5 phút
    job_queue = application.job_queue
    job_queue.run_repeating(check_alerts, interval=300, first=10)
    
    # Chạy bot
    print("✅ Bot đang chạy...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
