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
from magentic_ui.agents.web_surfer_v2 import WebSurfer
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
    url: str
    query: Optional[str] = None
    cancellation_token: Optional[dict] = None

# 响应模型
class ResponseContent(BaseModel):
    ret_code: int
    msg: str
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



@app.post("/query")
async def process_query(query: UserQuery):
    url = query.url
    question = query.query
    cancellation_token = CancellationToken()
    web_surfer, browser, work_dir = await initialize_web_surfer()
    cancellation_token = CancellationToken()
    
    try:
        if question is None or question.strip() == "":
            question = ""
        # 生成响应内容
        result= await web_surfer._execute_tool_visit_url_and_page_summary(
            url=url, 
            question=question,
            cancellation_token=cancellation_token
        )
        return {"ret_code": 0, "msg": "", "content": result}
    except Exception as e:
        return {"ret_code": 1, "msg": str(e), "content": ""}
    finally:
        # 清理当前查询的资源
        await cleanup_resources(web_surfer, browser, work_dir)
        print(f"已清理查询资源: {work_dir}")
    
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
    parser.add_argument("--port", type=int, default=37367, help="Docker浏览器端口 (-1表示不使用Docker)")
    parser.add_argument("--novnc-port", type=int, default=6080, help="noVNC端口 (用于可视化浏览器)")
    parser.add_argument("--fastapi-port", type=int, default=8005, help="FastAPI服务端口 (默认8000)")
    args = parser.parse_args()
    
    # 启动FastAPI服务
    uvicorn.run(app, host="0.0.0.0", port=args.fastapi_port, log_level="info")

if __name__ == "__main__":
    main()
