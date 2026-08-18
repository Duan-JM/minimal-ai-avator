import time
import re
from collections import deque
from openai import OpenAI
from src.basereal import BaseReal
from src.log import logger
from src.config import get_llm_api_key, get_llm_base_url, get_llm_model_name


api_key = get_llm_api_key()
base_url = get_llm_base_url()
model_name = get_llm_model_name()
show_api_key = api_key[:10] + "..."
client = OpenAI(
    api_key=api_key,
    base_url=base_url,
    timeout=60.0,
    max_retries=1,
)
logger.debug(f"llm api_key: {show_api_key}, llm base_url: {base_url}, llm model: {model_name}")

# 全局对话历史管理
# key: session_id, value: deque of messages (最多保留10轮，即20条消息)
_conversation_history = {}
MAX_HISTORY_ROUNDS = 10  # 最多保留10轮对话
MAX_HISTORY_MESSAGES = MAX_HISTORY_ROUNDS * 2  # 每轮2条消息（user + assistant）

# 系统提示词
SYSTEM_PROMPT = (
    "You are a helpful assistant."
    "请直接回答用户问题，尽量简洁。"
    "只输出最终给用户看的答复，不要输出思考过程、分析、自我对话、推理步骤或隐藏提示。"
    "回答必须是纯 text，不要 markdown。"
)

REASONING_PREFIXES = (
    '好的，用户', '我需要', '我认为', '要注意', '另外', '可能', '检查', '确认', '不过',
    '再想想', '所以', '最终', '首先', '按照指示', '不需要', '选择', '或者', '保持最',
    '用户要求', '简单回答用户问题', 'The user', 'I need', 'I should', 'Let me',
)


def sanitize_assistant_output(text: str) -> str:
    """过滤掉模型可能泄露的思考过程，只保留面向用户的最终答复。"""
    if not text:
        return ""

    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r'<think>.*$', '', cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = cleaned.replace('</think>', '')

    parts = [part.strip() for part in re.split(r'[\r\n]+', cleaned) if part.strip()]
    visible_parts = []

    for part in parts:
        normalized = part.strip().strip('“”"\'')
        if normalized.startswith(REASONING_PREFIXES):
            continue
        visible_parts.append(normalized)

    final_text = visible_parts[-1] if visible_parts else (parts[-1] if parts else "")
    final_text = re.sub(r'^(最终答案|答案|回复|答复)\s*[：:]\s*', '', final_text).strip()
    return final_text


def get_conversation_history(session_id):
    """获取指定session的对话历史"""
    if session_id not in _conversation_history:
        _conversation_history[session_id] = deque(maxlen=MAX_HISTORY_MESSAGES)
    return _conversation_history[session_id]


def add_to_history(session_id, role, content):
    """添加消息到对话历史
    
    Args:
        session_id: 会话ID
        role: 'user' 或 'assistant'
        content: 消息内容
    """
    history = get_conversation_history(session_id)
    history.append({'role': role, 'content': content})


def clear_conversation_history(session_id=None):
    """清除对话历史
    
    Args:
        session_id: 指定session_id清除特定会话，None则清除所有
    """
    global _conversation_history
    if session_id is None:
        _conversation_history.clear()
        logger.info("All conversation history cleared")
    elif session_id in _conversation_history:
        del _conversation_history[session_id]
        logger.info(f"Session {session_id} conversation history cleared")


def build_messages(session_id, current_message):
    """构建完整的消息列表（系统提示词 + 历史 + 当前消息）
    
    Args:
        session_id: 会话ID
        current_message: 当前用户消息
    
    Returns:
        完整的消息列表
    """
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    
    # 添加历史消息
    history = get_conversation_history(session_id)
    messages.extend(list(history))
    
    # 添加当前消息
    messages.append({'role': 'user', 'content': current_message})
    
    return messages


def llm_response(message, nerfreal: BaseReal, session_id=None):
    """生成LLM响应（支持多轮对话）
    
    Args:
        message: 用户输入消息
        nerfreal: BaseReal实例
        session_id: 会话ID，用于区分不同用户/会话的对话历史
    """
    # 如果没有提供session_id，使用nerfreal的sessionid
    if session_id is None:
        session_id = getattr(nerfreal, 'sessionid', 'default')
    
    logger.debug(f"Session {session_id}: User message: {message}")
    
    # 构建包含历史的消息列表
    messages = build_messages(session_id, message)
    
    start = time.perf_counter()
    completion = client.chat.completions.create(
        model=model_name,  # 使用配置文件中的模型名称
        messages=messages,
        stream=True,
        stream_options={"include_usage": True}
    )
    
    raw_response = ""
    first = True
    
    for chunk in completion:
        if len(chunk.choices) > 0:
            if first:
                end = time.perf_counter()
                logger.debug(f"llm Time to first chunk: {end - start}s")
                first = False
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, 'reasoning_content', None)
            if reasoning:
                logger.debug(f"Filtered reasoning_content chunk: {reasoning[:80]}")

            msg = getattr(delta, 'content', None)
            if msg:
                raw_response += msg
    
    end = time.perf_counter()
    assistant_response = sanitize_assistant_output(raw_response)
    if assistant_response and assistant_response != raw_response.strip():
        logger.debug(f"Filtered assistant output from: {raw_response[:120]} -> {assistant_response[:120]}")
    if assistant_response:
        nerfreal.put_msg_txt(assistant_response)
    
    # 保存到对话历史
    add_to_history(session_id, 'user', message)
    add_to_history(session_id, 'assistant', assistant_response)
    
