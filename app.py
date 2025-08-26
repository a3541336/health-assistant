import json
from datetime import datetime
import sys

from flask import Flask, url_for
from flask import request, abort
from google.api_core.retry import if_exception_type
from linebot import (LineBotApi, WebhookHandler)
from linebot.exceptions import (InvalidSignatureError)
from linebot.models import (MessageEvent, TextMessage, TextSendMessage, ImageMessage, FlexSendMessage,QuickReply, QuickReplyButton, MessageAction)
import threading
import time
import os
from pyngrok import ngrok

# 自己新創的檔案
import const
import user_repo
import handle_message
import chat_gpt
import calculate



app = Flask(__name__, static_url_path='/static')

# Line - Channel access token (long-lived)
line_bot_api = LineBotApi(const.LINE_CHANNEL_ACCESS_TOKEN)

# Line - Channel secret
handler = WebhookHandler(const.LINE_CHANNEL_SECRET)

# Line - Set Rich Menu
handle_message.set_line_main_menu()

public_url = None

user_context = {}

# Line - Webhook URL
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']

    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

# Line - 接收文字
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.strip()
    userid = event.source.user_id

    # 建立 UserRepo 實例
    repo = user_repo.UserRepo()

    # 先判斷使用者是否為已註冊過
    message_type = 'registering'
    if repo.get_user_by_userid(userid):
        message_type = 'logging'

    if message_type == 'logging' and text == "查閱健康紀錄":
        # 取得健康紀錄
        line_flex_template = handle_message.line_flex_template_record(repo.get_all_record(userid))
        messages_to_send = [
            FlexSendMessage(alt_text='您的健康紀錄', contents=line_flex_template),
        ]

    elif message_type == 'logging' and text == "記錄健康":
        reply_message = handle_message.get_record_health_text()
        messages_to_send = [
            TextSendMessage(text=reply_message),
        ]

    elif message_type == 'logging' and text == "記錄飲食":
        reply_message = handle_message.get_record_diet_text()
        messages_to_send = [
            TextSendMessage(text=reply_message),
        ]

    elif message_type == 'logging' and text == "飲食&運動建議":
        reply_message = chat_gpt.chatgpt_health_suggetion(userid)
        cleaned_message = reply_message.replace("```python", "").replace("```", "").strip()
        message_dictionary = json.loads(cleaned_message)
        line_flex_template = handle_message.line_flex_template_suggetion(message_dictionary)
        messages_to_send = [
            FlexSendMessage(alt_text='您的飲食&運動建議',contents=line_flex_template),
        ]

    else:
        if userid in user_context and text =='加入飲食紀錄':

            format_diet = chat_gpt.chatgpt_format_diet_image(user_context[userid])
            record_date = datetime.today().strftime('%Y-%m-%d')
            for food in format_diet.strip().split('\n'):
                repo.create_diet_record({'userid': userid, '飲食內容': food.strip(), '紀錄日期': record_date})
            reply_message = '✅ 飲食紀錄已儲存！'
            info_type = 'break'
        else:
            info_type = chat_gpt.chatgpt_detect_info_type(text)

        user_context.pop(userid) if userid in user_context else None
        if info_type == 'basic':
            formated_text = chat_gpt.chatgpt_format_basic_profile(text)
            print(formated_text)
            error = False
            if '請確認' in formated_text:
                error = True
                messages_to_send = [
                    TextSendMessage(text=formated_text),
                ]

            if not error:
                # 轉換型別
                records = repo.convert_types(formated_text)
                # 新增或更新使用者基本資料
                my_user = repo.get_user_by_userid(userid)
                if my_user is None:
                    repo.create_user(records, userid)
                else:
                    repo.update_user(userid, records)

                # chat gpt 回覆「健康風險評估與建議」
                reply = handle_message.basic_record_description(records)
                question = reply[1]
                reply_text = reply[0] + "\n\n" + "📍 健康風險評估與建議:" + "\n" + chat_gpt.chatgpt_basic(question)
                messages_to_send = [
                    TextSendMessage(text=reply_text),
                ]

        elif message_type == 'registering':
            messages_to_send = [
                TextSendMessage(text=handle_message.get_first_login_text())
            ]

        elif info_type == 'health':
            format_health = chat_gpt.chatgpt_format_health_record(text)
            if format_health != 'false':
                health_data = repo.convert_types(format_health)

                # 新增健康紀錄
                health_data['紀錄日期'] = record_date = datetime.today().strftime('%Y-%m-%d')
                my_health = repo.get_health_records_by_userid(userid, record_date)
                if my_health is None:
                    repo.create_health_record({**health_data, 'userid': userid})
                else:
                    repo.update_health_record(userid, record_date, health_data)

                reply_message = '✅ 健康紀錄已儲存！'
            else:
                reply_message = '資料錯誤，請依格式輸入健康資訊。'
            messages_to_send = [
                TextSendMessage(text=reply_message)
            ]

        elif info_type == 'diet':
            format_diet = chat_gpt.chatgpt_format_diet_record(text)
            if format_diet != 'false':
                record_date = datetime.today().strftime('%Y-%m-%d')
                for food in format_diet.strip().split('\n'):
                    repo.create_diet_record({'userid': userid, '飲食內容': food.strip(), '紀錄日期': record_date})
                reply_message = '✅ 飲食紀錄已儲存！'
            else:
                reply_message = '資料錯誤，請輸入「食物名稱」或「料理名稱」。'
            messages_to_send = [
                TextSendMessage(text=reply_message),
            ]
        elif info_type == 'break':
            messages_to_send = [
                TextSendMessage(text=reply_message),
            ] 
        else:
            if calculate.chinese_char_count(text) > 5:
                reply_message = chat_gpt.chatgpt_normal_question_reply(text)
            else:
                reply_message = "請點擊功能選單 ⬇️"
            messages_to_send = [
                TextSendMessage(text=reply_message),
            ]

    line_bot_api.reply_message(event.reply_token, messages_to_send)

# Line - 接收圖片
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_id = event.message.id
    
    # 確保圖片儲存到 static/images 資料夾
    static_folder = os.path.join(app.root_path, 'static', 'images')
    if not os.path.exists(static_folder):
        os.makedirs(static_folder)

    # 以訊息 ID 作為檔名
    filename = f"{message_id}.jpg"
    image_path = os.path.join(static_folder, filename)

    # 取得圖片內容並儲存到本地
    response = line_bot_api.get_message_content(message_id)
    with open(image_path, 'wb') as f:
        for chunk in response.iter_content():
            f.write(chunk)

    image_url_path = url_for('static', filename=f"images/{filename}")

    # 結合 ngrok URL 和圖片路徑，得到完整的公開 URL
    public_image_url = f"{public_url}{image_url_path}"
    print(f"可透過 ngrok 訪問的公開 URL：{public_image_url}")

    gpt_response = chat_gpt.chatgpt_image(public_image_url)
    user_context[event.source.user_id] = gpt_response  # 儲存 GPT 回覆到使用者上下文
    quick_reply_buttons = [
        QuickReplyButton(
            action=MessageAction(label='加入飲食紀錄', text='加入飲食紀錄')
        )
    ]
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=gpt_response,quick_reply=QuickReply(items=quick_reply_buttons))
    )

# 主程式
if __name__ == '__main__':
    # flask 與 ngrok 的連線端口
    FLASK_PORT = const.FLASK_PORT

    print(f"Flask 應用程式將在 http://127.0.0.1:{FLASK_PORT} 運行。")

    # 啟動 flask
    def run_flask_app():
        app.run(host=const.FLASK_HOST, port=FLASK_PORT, debug=True, use_reloader=False)

    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.start()

    print("等待 Flask 應用程式啟動...")
    time.sleep(3)

    # 啟動 ngrok，打通本機網路對外連線
    ngrok_auth_token = const.NGROK_AUTH_TOKEN
    ngrok.set_auth_token(ngrok_auth_token)
    print("正在啟動 ngrok 隧道...")

    # 建立 ngrok 隧道，連接到 Flask 應用程式的埠號
    public_url = ngrok.connect(FLASK_PORT).public_url
    print("-" * 50)
    print(f"ngrok 隧道已啟動！")
    print(f"您的 Flask 應用程式現可透過此 URL 訪問：{public_url}")

    # flask 需持續運行，ngrok 隧道才能維持開啟
    while True:
       time.sleep(1)