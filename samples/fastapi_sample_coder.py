import asyncio
import argparse
import json
import os
import random
import shutil
import string
import base64
from typing import AsyncGenerator, Dict, List, Optional, Union, cast

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from autogen_core.models import RequestUsage

from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.base import Response, TaskResult
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    ModelClientStreamingChunkEvent,
    MultiModalMessage,
    UserInputRequestedEvent,
)
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from autogen_agentchat.conditions import TextMentionTermination
from magentic_ui.agents import CoderAgent
from magentic_ui.teams import RoundRobinGroupChat


# 定义输出消息结构（含图片字段）
class ConsoleOutput:
    def __init__(
        self,
        type: str,
        content: str,
        source: Optional[str] = None,
        stats: Optional[Dict[str, int]] = None,
        finish_reason: Optional[str] = None,
        is_chunk: bool = False,
        image_data: Optional[Dict] = None,  # 图片数据字段
    ):
        self.type = type
        self.content = content
        self.source = source
        self.stats = stats
        self.finish_reason = finish_reason
        self.is_chunk = is_chunk
        self.image_data = image_data

    def to_dict(self) -> Dict:
        return {
            "type": self.type,
            "content": self.content,
            "source": self.source,
            "stats": self.stats,
            "finish_reason": self.finish_reason,
            "is_chunk": self.is_chunk,
            "image_data": self.image_data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# 生成随机目录
def generate_random_workdir(base_dir: str = "debug") -> str:
    random_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    work_dir = f"{base_dir}_{random_suffix}"
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


# 检查目录中的图片并转为base64
def get_images_from_dir(work_dir: str) -> List[Dict]:
    image_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')
    images = []
    if not os.path.exists(work_dir):
        return images
    for filename in os.listdir(work_dir):
        if filename.lower().endswith(image_extensions):
            file_path = os.path.join(work_dir, filename)
            try:
                with open(file_path, 'rb') as f:
                    base64_str = base64.b64encode(f.read()).decode('utf-8')
                images.append({
                    "filename": filename,
                    "format": filename.split('.')[-1].lower(),
                    "base64": base64_str
                })
            except Exception as e:
                print(f"读取图片失败 {file_path}：{e}")
    return images


# 流式输出转换（实时检测图片+增量处理）
async def StreamConsole(
    stream: AsyncGenerator[BaseAgentEvent | BaseChatMessage | TaskResult | Response, None],
    work_dir: str,
    output_stats: bool = False,
) -> AsyncGenerator[str, None]:
    start_time = asyncio.get_event_loop().time()
    total_usage = RequestUsage(prompt_tokens=0, completion_tokens=0)
    streaming_chunks: List[str] = []
    is_stream_ended = False

    # 新增：记录已处理的图片（避免重复发送）
    processed_images = set()  # 存储已处理的图片文件名

    try:
        async for message in stream:
            # 1. 先处理当前消息（正常输出代码/文本）
            # 过滤无用的Checkpoint消息
            if hasattr(message, 'source') and message.source == "orchestrator" and message.to_text() == "Checkpoint":
                continue

            # 处理任务完成结果
            if isinstance(message, TaskResult):
                duration = asyncio.get_event_loop().time() - start_time
                output = ConsoleOutput(
                    type="query_complete",
                    content="查询处理完成",
                    stats={
                        "total_prompt_tokens": total_usage.prompt_tokens,
                        "total_completion_tokens": total_usage.completion_tokens,
                        "duration": round(duration, 2),
                    } if output_stats else None,
                    finish_reason=message.stop_reason
                ).to_json()
                is_stream_ended = True

            # 处理核心响应内容
            elif isinstance(message, Response):
                content = message.chat_message.to_text() if not isinstance(message.chat_message, MultiModalMessage) else message.chat_message.to_text()
                if message.chat_message.models_usage and output_stats:
                    total_usage.completion_tokens += message.chat_message.models_usage.completion_tokens
                    total_usage.prompt_tokens += message.chat_message.models_usage.prompt_tokens

                output = ConsoleOutput(
                    type="response",
                    content=content,
                    source=message.chat_message.source,
                    stats={
                        "prompt_tokens": message.chat_message.models_usage.prompt_tokens if message.chat_message.models_usage else 0,
                        "completion_tokens": message.chat_message.models_usage.completion_tokens if message.chat_message.models_usage else 0,
                    } if output_stats else None
                ).to_json()
                yield output + "\n"

            # 处理流式生成的片段
            elif isinstance(message, ModelClientStreamingChunkEvent):
                output = ConsoleOutput(
                    type="stream_chunk",
                    content=message.content,
                    source=message.source,
                    is_chunk=True
                ).to_json()
                yield output + "\n"
                streaming_chunks.append(message.content)

            # 处理其他有效系统消息
            else:
                message = cast(BaseAgentEvent | BaseChatMessage, message)
                content = message.to_text() if hasattr(message, 'to_text') else str(message)
                if content.strip():
                    output = ConsoleOutput(
                        type="system_message",
                        content=content,
                        source=message.source
                    ).to_json()
                    yield output + "\n"

            # 2. 关键：当前消息输出后，立即检查新增图片（增量处理）
            current_images = get_images_from_dir(work_dir)
            for img in current_images:
                img_filename = img["filename"]
                # 只处理未发送过的图片
                if img_filename not in processed_images:
                    # a. 发送图片
                    output = ConsoleOutput(
                        type="image",
                        content=f"检测到图片：{img_filename}",
                        source="system",
                        image_data=img
                    ).to_json()
                    yield output + "\n"
                    # b. 标记为已处理
                    processed_images.add(img_filename)
                    # c. 发送后立即删除该图片文件（释放空间）
                    img_path = os.path.join(work_dir, img_filename)
                    if os.path.exists(img_path):
                        try:
                            os.remove(img_path)
                            print(f"已删除图片文件：{img_path}")
                        except Exception as e:
                            print(f"删除图片失败 {img_path}：{e}")

        # 循环结束：如果未标记结束，手动标记
        if not is_stream_ended:
            duration = asyncio.get_event_loop().time() - start_time
            output = ConsoleOutput(
                type="query_complete",
                content="查询流已结束",
                stats={"duration": round(duration, 2)} if output_stats else None,
                finish_reason="Stream completed normally"
            ).to_json()
            yield output + "\n"
            is_stream_ended = True

    finally:
        # 最后清理：删除目录（即使有残留文件也会被清理）
        if os.path.exists(work_dir):
            try:
                shutil.rmtree(work_dir)
                print(f"已删除临时目录：{work_dir}")
            except Exception as e:
                print(f"删除目录失败 {work_dir}：{e}")


# FastAPI应用初始化
app = FastAPI(title="查询式流式响应服务（图片处理优化版）")


# 初始化智能体
async def init_agent(work_dir: str):
    model_client = AzureOpenAIChatCompletionClient(
        model="gpt-4o",
        api_version="2024-12-01-preview",
        azure_endpoint="https://midas-openai2.openai.azure.com",
        azure_deployment="us-gpt-4o",
        api_key='',  # 生产环境用环境变量
    )

    coder = CoderAgent(
        name="coder_agent",
        model_client=model_client,
        work_dir=work_dir,
        bind_dir=work_dir,
    )
    user_proxy = UserProxyAgent(name="user_proxy")

    team = RoundRobinGroupChat(
        participants=[coder, user_proxy],
        max_turns=1,
        termination_condition=TextMentionTermination("EXITT"),
    )
    await team.lazy_init()
    return team, coder


# 查询接口
@app.post("/query")
async def query_endpoint(request: Request):
    data = await request.json()
    user_query = data.get("query", "")
    work_dir = generate_random_workdir(base_dir="debug")
    print(f"当前请求使用临时目录：{work_dir}")

    team, coder = await init_agent(work_dir)
    try:
        stream = team.run_stream(task=user_query)

        async def wrapper_stream():
            # 先执行原有的流处理
            async for item in StreamConsole(stream, work_dir=work_dir, output_stats=True):
                yield item
            # 流处理完成后，关闭coder
            try:
                if hasattr(coder, 'close') and callable(coder.close):
                    await coder.close()
                    print(f"已成功关闭coder代理")
                else:
                    print("coder对象没有close方法或该方法不可调用")
            except Exception as e:
                print(f"关闭coder代理时出错: {e}")

        return StreamingResponse(
            wrapper_stream(),
            media_type="text/event-stream"
        )
    except Exception as e:
        print(f"查询处理过程中发生错误: {e}")
        # 发生异常时也尝试关闭coder
        try:
            if hasattr(coder, 'close') and callable(coder.close):
                await coder.close()
                print(f"已成功关闭coder代理(异常处理)")
        except Exception as close_err:
            print(f"异常处理中关闭coder代理时出错: {close_err}")
        raise


# 启动入口
if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=6000)
    args = parser.parse_args()
    uvicorn.run(app, host="0.0.0.0", port=args.port)