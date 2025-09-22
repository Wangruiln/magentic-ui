import asyncio
import argparse
import logging
from typing import List, Optional
from autogen_core import CancellationToken
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_agentchat.base import Response
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from magentic_ui.agents import CoderAgent
from autogen_ext.code_executors.docker import DockerCommandLineCodeExecutor

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("coderagent_debug")

# 全局参数变量，供所有函数访问
args = None
coder_agent = None

# 辅助函数：初始化 CoderAgent
async def initialize_coder_agent():
    # 使用全局参数
    global args
    
    model_client = AzureOpenAIChatCompletionClient(
        model="gpt-4o",
        api_version="2024-12-01-preview",
        azure_endpoint="https://midas-openai2.openai.azure.com",
        azure_deployment="us-gpt-4o",
        api_key='',  # 注意：实际部署时不要硬编码密钥
    )

    coder = CoderAgent(
        name="coder_agent",
        model_client=model_client,
        work_dir=args.work_dir,
        bind_dir=args.work_dir,
        use_local_executor=True,
    )
    
    # 初始化代理
    await coder.lazy_init()
    return coder

# 辅助函数：清理资源
async def cleanup_resources(coder_agent):
    try:
        if coder_agent:
            await coder_agent.close()
    except Exception as e:
        logging.error(f"Error closing CoderAgent: {e}")

# 处理用户查询并显示响应
async def process_query(query_text: str):
    try:
        global coder_agent
        if not coder_agent:
            logger.error("CoderAgent 未初始化")
            return
        
        # 创建消息列表
        messages = [TextMessage(content=query_text, source="user")]
        
        # 处理取消令牌
        cancellation_token = CancellationToken()
        
        # 异步处理查询
        async for response in coder_agent.on_messages_stream(
            messages=messages, 
            cancellation_token=cancellation_token
        ):
            if isinstance(response, Response):
                message = response.chat_message
                if isinstance(message, TextMessage):
                    print(f"[响应] {message.content}")
                    if message.metadata:
                        print(f"[元数据] {message.metadata}")
            elif isinstance(response, BaseChatMessage):
                print(f"[文本] {response.content}")
                if hasattr(response, 'metadata') and response.metadata:
                    print(f"[元数据] {response.metadata}")
            else:
                # 处理其他类型的响应
                print(f"[系统] 收到未知类型的响应: {type(response)}")
    
    except Exception as e:
        print(f"处理查询时出错: {str(e)}")

# 主函数：统一解析命令行参数
async def main():
    global args, coder_agent
    parser = argparse.ArgumentParser(description="CoderAgent 本地调试工具")
    parser.add_argument(
        "--work_dir",
        type=str,
        default="debug",
        help="Directory where coder will save files",
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="要提交给 CoderAgent 的查询内容"
    )
    args = parser.parse_args()
    
    # 初始化 CoderAgent
    try:
        print("正在初始化 CoderAgent...")
        coder_agent = await initialize_coder_agent()
        print("CoderAgent 初始化完成")
        
        # 处理指定的查询
        print(f"\n处理查询: {args.query}")
        await process_query(args.query)
                
    finally:
        # 清理资源
        await cleanup_resources(coder_agent)
        print("资源已清理，程序退出")

if __name__ == "__main__":
    asyncio.run(main())