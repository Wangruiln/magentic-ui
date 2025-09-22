import requests
import json

def stream_chat():
    url = "http://localhost:6888/query"
    data = {"query": "用英文取标题，为[0, 1, 1, 2, 3, 5, 8, 13, 21, 34]这个数据画一个折线图。"}

    # 用于存储“最后答案”（coder_agent-llm来源的内容）
    final_answer = ""

    # 发送流式请求
    with requests.post(url, json=data, stream=True) as response:
        for line in response.iter_lines():
            if line:  # 跳过空行
                try:
                    msg = json.loads(line.decode("utf-8"))
                    print(f"接收到消息: {msg['type']}")  # 调试输出，查看接收到的消息内容
                except json.JSONDecodeError:
                    print(f"跳过无效数据: {line.decode('utf-8')}")
                    continue

                # 获取消息关键信息
                msg_type = msg.get("type")
                content = msg.get("content", "")
                source = msg.get("source", "unknown")  # 重点：获取source字段

                # 1. 判断是否为“最后答案”（source="coder_agent-llm"）
                if source == "coder_agent":
                    final_answer += content  # 累加内容（如果是流式片段）
                    print(f"\n【最后答案（{source}）】\n{content}", end="", flush=True)

                # 2. 处理其他来源的消息（如执行结果、系统通知等）
                else:
                    if msg_type == "system_message":
                        print(f"\n【辅助信息（{source}）】\n{content}")
                    elif msg_type == "image":
                        print(f"收到图片：{msg['image_data']['base64'][:30]}...")  # 仅显示前30个字符的base64编码
if __name__ == "__main__":
    stream_chat()