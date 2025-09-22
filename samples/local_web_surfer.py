import argparse
import asyncio
from pathlib import Path
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.auth.azure import AzureTokenProvider
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from azure.identity import DefaultAzureCredential
from magentic_ui.agents.web_surfer_v3 import WebSurfer
from autogen_core import CancellationToken
import logging


from magentic_ui.tools.playwright import (
    HeadlessDockerPlaywrightBrowser,
    VncDockerPlaywrightBrowser,
    LocalPlaywrightBrowser,
)

from autogen_agentchat.messages import TextMessage,MultiModalMessage
from autogen_agentchat.base import Response
from autogen_core import Image as AGImage
# Configure logging
logging.basicConfig(level=logging.WARN)
logger = logging.getLogger("magentic_ui.tools.docker_browser").setLevel(logging.INFO)


async def main() -> None:
    """
    Main function to run the WebSurfer agent with a browser.

    Parses command line arguments, starts the browser, initializes agents,
    and runs the conversation.
    """
    parser = argparse.ArgumentParser(
        description="""
        Run WebSurfer with a Docker-based or local browser, supporting both headless and VNC (noVNC) modes.
        
        - By default, runs with a local Playwright browser.
        - Use --port to specify a port for a Dockerized Playwright browser (headless or with VNC).
        - Use --novnc-port to enable a noVNC web interface for browser interaction via your web browser.
        
        To view the browser via noVNC, open your web browser and navigate to:
            http://localhost:<novnc-port>/?autoconnect=1
        Replace <novnc-port> with the value you provide to --novnc-port (e.g., 6080).
        """
    )
    parser.add_argument(
        "--port",
        type=int,
        default=-1,
        help="Port to run the docker browser on (default: -1 means no browser)",
    )
    parser.add_argument(
        "--novnc-port",
        type=int,
        default=-1,
        help="""
        Port to run the noVNC server on (default: 6080). 
        If set, you can view and interact with the browser remotely by visiting 
        http://localhost:<novnc-port>/?autoconnect=1 in your web browser.
        """,
    )
    args = parser.parse_args()

    browser = VncDockerPlaywrightBrowser(
        bind_dir='/tmp',
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

    input_query = "鸣潮2.6版本的内容？"
    # input_query = """
    # 我在实践过程中发现，由于由大模型直接判断是否完成用户询问，导致了每次搜索结果过于简单，请你按照以下步骤帮我进行修改
    # 你是一个优秀的网站访问和信息收集报告撰写专家，我想知道鸣潮v2.5版本更新内容有哪些？你需要按照以下步骤进行。

    # 步骤一：收集相关的url链接
    # - 首先执行web_search工具，在Bing.com上进行搜索。
    # - 你需要在搜索结果中找到与用户查询最相关的7个链接，记住相关的位置元素。

    # 步骤二：访问每个链接并收集信息
    # - 通过click点击目标元素直接访问找到的链接，只要访问5个。
    # - 在访问的页面上，使用OCR技术提取页面上的所有可见文本，可以使用"page_up"、"page_down"等工具进行滚动。
    # - 如果页面上有可交互元素（如按钮、链接等），请使用hover工具将鼠标悬停在这些元素上，以便获取更多信息。
    # - 如果页面上有图片或其他媒体内容，请使用OCR技术提取相关的文本信息。
    # - 注意保存总结每个网页的信息

    # 步骤三：回答用户问题
    # - 在收集到足够的信息后，回答我的问题，并撰写一篇报告，需要包含所有找到的内容。
    #"""
    messages=[TextMessage(content=input_query, source="user")]

    web_surfer = WebSurfer(
        name="web_surfer",
        model_client=model_client,
        animate_actions=True,
        max_actions_per_step=20,
        single_tab_mode=False,
        downloads_folder="debug",
        debug_dir="debug",
        to_save_screenshots=False,
        browser=browser,
        multiple_tools_per_call=False,
        json_model_output=False,
        use_action_guard=False,
        use_deep_search=True,
        only_url=False,  # 设置为True以仅返回URL
    )
    await web_surfer.lazy_init()

    try:
         async for response in web_surfer.on_messages_stream(messages=messages, cancellation_token=CancellationToken()):
            if isinstance(response, Response):
                # 处理响应消息
                message = response.chat_message
                if isinstance(message, MultiModalMessage):
                    print("收到多模态消息：")
                    for content_part in message.content:
                        if isinstance(content_part, str):
                            print(content_part)
                        elif isinstance(content_part, AGImage):
                            print("包含图片内容")

                elif isinstance(message, TextMessage):
                    print(f"收到文本消息：{message.content}")
                else:
                    print(f"收到未知类型消息：{type(message)}")
            else:
                print(f"收到非响应类型：{type(response)}")
    finally:
        # Make sure to close the WebSurfer before stopping the browser
        await web_surfer.close()
    # try:
    #     async for response in web_surfer.on_messages_stream(messages=messages, cancellation_token=CancellationToken()):
    #         if isinstance(response, Response):
    #             # 处理响应消息
    #             message = response.chat_message
    #             if isinstance(message, MultiModalMessage):
    #                 for content_part in message.content:
    #                     if isinstance(content_part, str) and content_part.endswith("[DONE]"):
    #                         final_url=content_part
    #             elif isinstance(message, TextMessage) and message.content.endswith("[DONE]"):
    #                 final_url=message.content
    #             else:
    #                 final_url = message.content
         
    # except Exception as e:
    #     print(f"发生错误: {e}")
    # print(f"最终URL: {final_url}")   

    # final_url = brutal_extract_urls(final_url)
    # if final_url:
    #     print(f"最终提取的URL: {final_url}")
    # else:
    #     print("没有找到有效的URL")
if __name__ == "__main__":
    asyncio.run(main())
