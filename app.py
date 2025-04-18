from flask import Flask, request, render_template_string
import qianfan
import os
from dotenv import load_dotenv
import logging
import threading
from queue import Queue

# 禁用不必要的日志
logging.getLogger("redis_rate_limiter").setLevel(logging.WARNING)

# 加载环境变量（本地开发时从.env文件加载）
load_dotenv()

app = Flask(__name__)

# 全局变量存储异步结果（生产环境建议用数据库或Redis）
result_queue = Queue()

AK = os.environ.get('QIANFAN_AK')
SK = os.environ.get('QIANFAN_SK')

if not AK or not SK:
    raise ValueError("请设置QIANFAN_AK和QIANFAN_SK环境变量")

def generate_titles_async(product):
    """异步生成标题的函数"""
    try:
        chat = qianfan.ChatCompletion(ak=AK, sk=SK)
        resp = chat.do(
            model="ERNIE-Speed",  # 使用更快的模型
            messages=[{
                "role": "user",
                "content": f"生成5个小红书风格标题，关于{product}，带emoji和热点话题"
            }],
            timeout=5  # 设置API超时时间（秒）
        )
        result_queue.put({
            "product": product,
            "titles": [title.strip() for title in resp["result"].split("\n") if title.strip()],
            "error": None
        })
    except Exception as e:
        result_queue.put({
            "product": product,
            "titles": None,
            "error": f"API请求失败: {str(e)}"
        })

@app.route('/', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        product = request.form.get('product')
        if not product:
            return render_template_string(HTML_TEMPLATE, error="请输入产品名称！")

        # 检查是否有已完成的异步结果
        if not result_queue.empty():
            result = result_queue.get()
            if result["product"] == product:
                if result["error"]:
                    return render_template_string(HTML_TEMPLATE, error=result["error"])
                return render_template_string(HTML_TEMPLATE, titles=result["titles"], product=product)

        # 启动异步任务
        threading.Thread(
            target=generate_titles_async,
            args=(product,),
            daemon=True
        ).start()

        return render_template_string(HTML_TEMPLATE, 
                                   product=product,
                                   info="标题生成中，请稍后刷新页面...")

    return render_template_string(HTML_TEMPLATE)

# 完整HTML模板（新增info提示）
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>小红书标题生成器</title>
    <style>
        body { font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background-color: #fef6f6; color: #333; }
        h1 { color: #ff2442; text-align: center; margin-bottom: 30px; }
        .container { background-color: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); }
        form { display: flex; flex-direction: column; gap: 15px; }
        input[type="text"] { padding: 12px 15px; border: 1px solid #ffcdd2; border-radius: 8px; font-size: 16px; }
        input[type="text"]:focus { outline: none; border-color: #ff2442; }
        button { background-color: #ff2442; color: white; border: none; padding: 12px; border-radius: 8px; font-size: 16px; cursor: pointer; transition: background-color 0.3s; }
        button:hover { background-color: #e61e3c; }
        .error, .info { padding: 10px; border-radius: 6px; margin-bottom: 15px; }
        .error { color: #ff2442; background-color: #ffebee; }
        .info { color: #2465ff; background-color: #ebf0ff; }
        .result { margin-top: 30px; }
        .title-list { list-style-type: none; padding: 0; }
        .title-item { padding: 15px; margin-bottom: 10px; background-color: #fff9f9; border-left: 4px solid #ff2442; border-radius: 4px; }
        .product-name { color: #ff2442; font-weight: bold; }
        footer { margin-top: 30px; text-align: center; color: #888; font-size: 14px; }
        .tips { background-color: #fff8e1; padding: 10px; border-radius: 6px; margin-top: 20px; font-size: 14px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📝 小红书爆款标题生成器</h1>
        
        {% if error %}
            <div class="error">⚠️ {{ error }}</div>
        {% elif info %}
            <div class="info">ℹ️ {{ info }}</div>
        {% endif %}
        
        <form method="POST">
            <input type="text" name="product" placeholder="输入产品/主题名称（如：房产、化妆品、健身等）" 
                   value="{{ product if product else '' }}" required>
            <button type="submit">✨ 生成标题</button>
        </form>
        
        <div class="tips">
            💡 小贴士：输入越具体，生成的标题越精准！例如："上海学区房"、"抗衰老面霜"
        </div>
        
        {% if titles %}
        <div class="result">
            <h3>关于<span class="product-name">{{ product }}</span>的爆款标题建议：</h3>
            <ul class="title-list">
                {% for title in titles %}
                <li class="title-item">{{ title }}</li>
                {% endfor %}
            </ul>
        </div>
        {% endif %}
        
        <footer>
            <p>Powered by 百度千帆大模型 | © 2025 小红书标题生成器</p>
        </footer>
    </div>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

def create_app():
    return app