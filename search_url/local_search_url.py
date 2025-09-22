import argparse
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Union, Optional
from fastapi.responses import StreamingResponse
import uvicorn
import logging
import json

# 原有导入保持不变
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.auth.azure import AzureTokenProvider
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from azure.identity import DefaultAzureCredential
from magentic_ui.agents.web_surfer_v3 import WebSurfer
from autogen_core import CancellationToken
from magentic_ui.tools.playwright import (
    HeadlessDockerPlaywrightBrowser,
    VncDockerPlaywrightBrowser,
    LocalPlaywrightBrowser,
)
from autogen_agentchat.messages import TextMessage, MultiModalMessage
from autogen_agentchat.base import Response
from autogen_core import Image as AGImage

from playwright.async_api import (
    BrowserContext,
    Playwright,
    Browser,
    async_playwright,
)
from openai import AzureOpenAI
AZURE_API_KEY = ""
AZURE_ENDPOINT = "https://midas-openai2.openai.azure.com/"
AZURE_DEPLOYMENT = "gpt-4.1"  # 你的 Azure 部署名称
AZURE_API_VERSION = "2024-12-01-preview"

# 配置日志
logging.basicConfig(level=logging.WARN)
logger = logging.getLogger("magentic_ui.tools.docker_browser").setLevel(logging.INFO)

# 全局参数变量，供所有函数访问
args = None

# FastAPI 应用初始化
app = FastAPI()

# 请求模型
class UserQuery(BaseModel):
    query: str
    cancellation_token: Optional[dict] = None

# 响应模型
class ResponseContent(BaseModel):
    content: Union[str, List[Union[str, dict]]]  # 文本或多模态内容

async def initialize_web_surfer():
    # 使用全局参数
    global args
    
    # 为每个查询创建唯一的工作目录
    work_dir = Path("debug") / f"query_{int(asyncio.get_event_loop().time() * 1000)}"
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # 启动浏览器
    browser = VncDockerPlaywrightBrowser(
        bind_dir=work_dir,
        playwright_port=args.port,
        novnc_port=args.novnc_port,
        playwright_websocket_path="default",
        inside_docker=False,
    )
    print(f"Browser remote view: {browser.vnc_address}?autoconnect=1")

    model_client = AzureOpenAIChatCompletionClient(
        model="gpt-4.1",
        api_version="2024-12-01-preview",
        azure_endpoint="https://midas-openai2.openai.azure.com",
        azure_deployment="gpt-4.1",
        api_key='',  # 注意：实际部署时不要硬编码密钥
    )

    # 初始化 WebSurfer
    web_surfer = WebSurfer(
        name="web_surfer",
        model_client=model_client,
        animate_actions=True,
        max_actions_per_step=10,
        single_tab_mode=False,
        downloads_folder=str(work_dir),
        debug_dir=str(work_dir),
        to_save_screenshots=False,
        browser=browser,
        multiple_tools_per_call=False,
        json_model_output=False,
        use_action_guard=False,
        use_deep_search=True,
        only_url=False, 
    )
    await web_surfer.lazy_init()
    return web_surfer, browser, work_dir


# 辅助函数：清理资源
async def cleanup_resources(web_surfer, browser, work_dir):
    try:
        if web_surfer:
            await web_surfer.close()
        # 清理工作目录
        if work_dir and work_dir.exists():
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)
    except Exception as e:
        logging.error(f"Error cleaning up resources: {e}")

# 处理单个查询的生成器
async def process_single_query(query_str):
    messages = [TextMessage(content=query_str, source="user")]
    web_surfer, browser, work_dir = await initialize_web_surfer()
    cancellation_token = CancellationToken()
    
    try:
        # 生成响应内容
        async for response in web_surfer.on_messages_stream(
            messages=messages, 
            cancellation_token=cancellation_token
        ):
            # 处理并发送响应
            if isinstance(response, Response):
                message = response.chat_message
                if isinstance(message, MultiModalMessage):
                    yield f"{message.content}\n\n"
                elif isinstance(message, TextMessage):
                        # if message.content.startswith("Reading the entire page"):
                        #     continue
                        # # 可以在这里添加查询标识，让客户端知道这是哪个查询的结果
                        # if message.content.startswith("[Sub-page]") or message.content.startswith("[Summary]"):
                        yield f"{message.content}\n\n"
            else:
                continue
    finally:
        # 清理当前查询的资源
        await cleanup_resources(web_surfer, browser, work_dir)
        print(f"已清理查询资源: {work_dir}")

@app.post("/query")
async def process_query(query: UserQuery):
    try:
        # 创建消息列表
        prompt = """
        你是一个问题理解和改写专家，对于用户的输入，你需要将其转换为一个或者多个清晰的查询，能够包含所有的关键词。
        你可能收到一句话或一段文本，可能包含多个内容。你的任务是将这些内容分别提取出来，转换为适合网页搜索的句式。
        
        注意：
        - 确保一个查询语句只包含一个主题
        - 输出一定是一个列表
        - 对于一个主题的不需要改写成多个查询
        以json格式输出
        {
          "query": ["查询1", "查询2", ...]
        }
        """
        try:
            client = AzureOpenAI(
                api_key=AZURE_API_KEY,
                azure_endpoint=AZURE_ENDPOINT,
                api_version=AZURE_API_VERSION
            )
            response = client.chat.completions.create(
                model=AZURE_DEPLOYMENT,  # 使用 Azure 部署名称
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": query.query},
                ],
                temperature=0.1  # 确保结果稳定，不省略内容
            )
            query_data = response.choices[0].message.content
            try:
                query_data = json.loads(query_data)  # 解析 JSON 格式的查询
                query_data = query_data.get("query")
                if not isinstance(query_data, list):
                    query_data = [query.query]  # 确保是列表格式
            except json.JSONDecodeError as e:
                query_data = [query.query]
        except Exception as e:
            query_data = [query.query]  # 如果改写失败，使用原始查询
        
        # 合并所有查询的结果流
        async def combined_generator():
            # 遍历所有查询并依次处理
            for idx, query_str in enumerate(query_data):
                # 可以添加查询分隔符，方便客户端解析
                yield f"===== 开始查询第 {idx+1} 个问题: {query_str} =====\n\n"
                # 处理当前查询并yield结果
                async for chunk in process_single_query(query_str):
                    yield chunk
                yield f"===== 第 {idx+1} 个查询处理完毕 =====\n\n"
        
        # 返回合并后的流
        return StreamingResponse(combined_generator(), media_type="text/plain")
        
    except Exception as e:
        # 处理初始化或其他早期错误
        logging.error(f"处理查询时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"处理查询时出错: {str(e)}")
    
# API 端点：获取浏览器状态
@app.get("/browser/status")
async def get_browser_status():
    try:
        return {"status": "active", "message": "浏览器服务可用", "novnc_port": args.novnc_port if args else None}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# 主函数：统一解析命令行参数
def main():
    global args
    parser = argparse.ArgumentParser(description="WebSurfer 远程服务 (FastAPI)")
    parser.add_argument("--port", type=int, default=-1, help="Docker浏览器端口 (-1表示不使用Docker)")
    parser.add_argument("--novnc-port", type=int, default=-1, help="noVNC端口 (用于可视化浏览器)")
    parser.add_argument("--fastapi-port", type=int, default=8080, help="FastAPI服务端口 (默认8000)")
    args = parser.parse_args()
    
    # 启动FastAPI服务
    uvicorn.run(app, host="0.0.0.0", port=args.fastapi_port, log_level="info")

if __name__ == "__main__":
    main()
