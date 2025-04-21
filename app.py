from flask import Flask, request, render_template_string
import os
from dotenv import load_dotenv
import logging
import threading
from queue import Queue
from functools import wraps
import time
import requests

# 禁用不必要的日志
logging.getLogger("redis_rate_limiter").setLevel(logging.WARNING)

# 加载环境变量（本地开发时从.env文件加载）
load_dotenv()

app = Flask(__name__)

# 全局变量存储异步结果（生产环境建议用数据库或Redis）
result_queue = Queue()
request_in_progress = False
last_request_time = 0

AK = os.environ.get('QIANFAN_AK')

if not AK:
    raise ValueError("请设置QIANFAN_AK环境变量")

logging.debug(f"QIANFAN_AK: {AK}")

def with_timeout(timeout):
    """自定义超时装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = [None]
            exception = [None]
            
            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e
            
            thread = threading.Thread(target=target)
            thread.daemon = True
            thread.start()
            thread.join(timeout)
            
            if thread.is_alive():
                raise TimeoutError(f"Function timed out after {timeout} seconds")
            if exception[0] is not None:
                raise exception[0]
            return result[0]
        return wrapper
    return decorator

@with_timeout(25)  # 设置为比 Vercel 的 30 秒稍短
def call_qianfan_api(messages):
    """调用千帆API的核心函数（使用Access Token直连）"""
    ak = os.environ.get('QIANFAN_AK')
    url = "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/completions"
    
    # 使用access_token参数直连
    params = {
        "access_token": ak  # 直接使用AK作为access_token
    }
    
    data = {
        "model": "ERNIE-Speed",
        "messages": messages
    }
    
    try:
        logging.debug("Starting API request with access_token")
        # 设置更短的超时时间（连接5秒，读取20秒）
        response = requests.post(
            url,
            params=params,  # 参数放在URL中
            headers={"Content-Type": "application/json"},
            json=data,
            timeout=(5, 20)
        )
        logging.debug(f"API response status: {response.status_code}")
        if response.status_code != 200:
            raise Exception(f"API返回错误状态码: {response.status_code}")
        return response.json()
    except requests.exceptions.Timeout:
        logging.error("API请求超时，请稍后重试")
        raise TimeoutError("API请求超时，请稍后重试")
    except Exception as e:
        logging.error(f"API请求失败: {str(e)}")
        raise Exception(f"API请求失败: {str(e)}")
        
def generate_titles_async(product):
    """异步生成标题的函数"""
    global request_in_progress, last_request_time
    
    if request_in_progress:
        result_queue.put({
            "product": product,
            "titles": None,
            "error": "已有请求在处理中，请稍后再试"
        })
        return
    
    request_in_progress = True
    last_request_time = time.time()
    
    try:
        resp = call_qianfan_api([{
            "role": "user",
            "content": f"生成5个小红书风格标题，关于{product}，带emoji和热点话题"
        }])
        
        result_queue.put({
            "product": product,
            "titles": [title.strip() for title in resp["result"].split("\n") if title.strip()],
            "error": None
        })
    except TimeoutError:
        result_queue.put({
            "product": product,
            "titles": None,
            "error": "API请求超时，请稍后重试"
        })
    except Exception as e:
        result_queue.put({
            "product": product,
            "titles": None,
            "error": f"API请求失败: {str(e)}"
        })
    finally:
        request_in_progress = False

@app.route('/', methods=['GET', 'POST'])
def home():
    global last_request_time
    
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

        # 检查上次请求时间，避免频繁调用
        if time.time() - last_request_time < 5:
            return render_template_string(HTML_TEMPLATE, 
                                       product=product,
                                       info="操作过于频繁，请稍后再试")

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

@app.route('/test-connection', methods=['GET'])
def test_connection():
    """测试API连接的端点（简化版）"""
    ak = os.environ.get('QIANFAN_AK')
    if not ak:
        return "错误：未设置QIANFAN_AK环境变量"
    
    test_url = "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/completions"
    params = {"access_token": ak}
    data = {
        "model": "ERNIE-Speed",
        "messages": [{
            "role": "user",
            "content": "测试API连通性"
        }]
    }
    
    try:
        start_time = time.time()
        response = requests.post(
            test_url,
            params=params,
            headers={"Content-Type": "application/json"},
            json=data,
            timeout=10
        )
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            return f"""
            <h1>连接测试成功</h1>
            <p>响应时间: {elapsed:.2f}秒</p>
            <p>Access Token: {ak[:4]}...{ak[-4:]}</p>
            <pre>{response.text}</pre>
            """
        else:
            return f"""
            <h1>连接测试失败</h1>
            <p>状态码: {response.status_code}</p>
            <pre>{response.text}</pre>
            """
    except Exception as e:
        return f"""
        <h1>连接测试异常</h1>
        <p>错误类型: {type(e).__name__}</p>
        <p>详细信息: {str(e)}</p>
        """

# HTML模板保持不变
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
        .refresh-btn { 
            background-color: #2465ff; 
            margin-top: 10px;
            display: inline-block;
        }
        .refresh-btn:hover {
            background-color: #1a50d9;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1> 小红书爆款标题生成器</h1>
        
        {% if error %}
            <div class="error">️ {{ error }}</div>
        {% elif info %}
            <div class="info">ℹ️ {{ info }}</div>
        {% endif %}
        
        <form method="POST">
            <input type="text" name="product" placeholder="输入产品/主题名称（如：房产、化妆品、健身等）" 
                   value="{{ product if product else '' }}" required>
            <button type="submit"> 生成标题</button>
        </form>
        
        <div class="tips">
             小贴士：输入越具体，生成的标题越精准！例如："上海学区房"、"抗衰老面霜"
        </div>
        
        {% if titles %}
        <div class="result">
            <h3>关于<span class="product-name">{{ product }}</span>的爆款标题建议：</h3>
            <ul class="title-list">
                {% for title in titles %}
                <li class="title-item">{{ title }}</li>
                {% endfor %}
            </ul>
            <form method="POST">
                <input type="hidden" name="product" value="{{ product }}">
                <button type="submit" class="refresh-btn"> 重新生成</button>
            </form>
        </div>
        {% endif %}
        
        <footer>
            <p>Powered by 百度千帆大模型 |  2025 小红书标题生成器</p>
        </footer>
    </div>
    <script>
        // 如果有生成中的提示，5秒后自动刷新
        {% if info and '生成中' in info %}
        setTimeout(() => {
            window.location.reload();
        }, 5000);
        {% endif %}
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

def create_app():
    return app