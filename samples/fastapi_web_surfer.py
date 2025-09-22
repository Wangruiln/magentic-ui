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
from magentic_ui.agents import WebSurfer
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
    type: str  # "text" 或 "multimodal"
    content: Union[str, List[Union[str, dict]]]  # 文本或多模态内容
    source: str
    models_usage: Optional[dict] = None
    metadata: Optional[dict] = None

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
        model="gpt-4o",
        api_version="2024-12-01-preview",
        azure_endpoint="https://midas-openai2.openai.azure.com",
        azure_deployment="us-gpt-4o",
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

@app.post("/query")
async def process_query(query: UserQuery):
    try:
        # 创建消息列表
        messages = [TextMessage(content=query.query, source="user")]
        
        # 为每个查询创建新的 WebSurfer 和浏览器
        web_surfer, browser, work_dir = await initialize_web_surfer()
        
        # 处理取消令牌
        cancellation_token = CancellationToken()
        
        # 异步处理查询，包含资源清理逻辑
        async def generate_response():
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
                            content = []
                            base64_img = None
                            for content_part in message.content:
                                if isinstance(content_part, str):
                                    content.append(content_part)
                                elif isinstance(content_part, AGImage):
                                    base64_img = content_part.to_base64()
                            yield json.dumps({
                                "type": "multimodal",
                                "content": content,
                                "data": base64_img,
                                "source": message.source,
                                "metadata": message.metadata if message.metadata else None,
                            }) + "\n"
                        elif isinstance(message, TextMessage):
                            yield json.dumps({
                                "type": "text",
                                "content": message.content,
                                "source": message.source,
                                "metadata": message.metadata if message.metadata else None,
                            }) + "\n"
                        else:
                            yield json.dumps({
                                "type": "unknown",
                                "content": f"Received unknown message type: {type(message)}",
                                "source": "system"
                            }) + "\n"
                    else:
                        yield json.dumps({
                            "type": "system",
                            "content": f"Received non-response type: {type(response)}",
                            "source": "system"
                        }) + "\n"
            finally:
                # 确保在所有响应发送完毕后清理资源
                await cleanup_resources(web_surfer, browser, work_dir)
                print(f"已清理查询资源: {work_dir}")
        
        return StreamingResponse(generate_response(), media_type="application/jsonlines")
    
    except Exception as e:
        # 处理初始化或其他早期错误
        if 'web_surfer' in locals() and web_surfer:
            await cleanup_resources(web_surfer, browser, work_dir)
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
    parser.add_argument("--fastapi-port", type=int, default=8000, help="FastAPI服务端口 (默认8000)")
    args = parser.parse_args()
    
    # 启动FastAPI服务
    uvicorn.run(app, host="0.0.0.0", port=args.fastapi_port, log_level="info")

if __name__ == "__main__":
    main()