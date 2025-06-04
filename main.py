from flask import Flask, render_template, request
from flask_socketio import SocketIO
import os
import platform
import threading
import time
import shutil
import json
import re
import subprocess
from typing import Optional, List, Dict, Any, Tuple, Union
import errno

try:
    import llm
    import command_executor as executor
except ImportError as e:
    print(f"错误：导入模块失败 - {e}")
    print("请确保 llm.py 和 command_executor.py 文件位于同一目录下。")
    exit()

LLM_API_KEY = os.environ.get("LMSTUDIO_API_KEY", "lmstudio")
LLM_MODEL_NAME = os.environ.get("LMSTUDIO_MODEL", "nikolaykozloff/deepseek-r1-0528-qwen3-8b")
LLM_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://192.168.0.32:1234/v1")

DEFAULT_SYSTEM_PROMPT_TEMPLATE = (
    "你是一位精确、严谨、高效的AI自动化工程师，专注于为给定的项目自动配置Conda虚拟环境并安装所有必要的依赖。你的任务是分析项目信息和用户指令，然后生成一个结构化的JSON对象作为行动指令。不要有过多思考，尽快给出命令。"
    "\n当前操作系统环境是 Windows。"
    "\n--- 核心指令 ---"
    "\n1. 你的唯一输出**必须**是一个符合RFC 8259标准的、单一的、完整的JSON对象。严禁在此JSON对象前后包含任何额外的文本、解释、代码块标记(如```json)、注释或任何非JSON内容。"
    "\n2. 仔细阅读并严格遵守下方定义的“JSON对象结构规范”和“命令生成指南”。"
    "\n3. 你将分步骤接收信息：首先是项目基本信息（如Git URL，用户期望的环境名，README摘要），然后可能是你请求读取的文件内容，或者是先前命令的执行结果。"
    "\n4. 在每一步，你都需要根据当前所有已知信息（包括完整的对话历史）来决定下一步行动：是请求读取更多文件，还是生成执行命令，或者判断配置已完成。"
    "\n\n--- 当前任务状态 (此部分信息将在每次调用时更新，请务必参考！) ---"  # 强调参考
    "\n- **项目根目录**: <PROJECT_ROOT_PATH_PLACEHOLDER>"
    "\n- **目标Conda环境名称**: <ENV_NAME_PLACEHOLDER>"
    "\n- **原始README内容**: <README_CONTENT_PLACEHOLDER>"  # 新增README占位符
    "\n- **重要**: 你所有的决策和生成的命令都必须围绕以上指定的项目根目录和目标Conda环境名称进行。如果这些信息显示为“未指定”或“未提供”，请在你的`thought_summary`中指出需要这些信息才能继续，或者基于已有信息进行合理推断（例如，如果环境名未指定，你可以建议一个）。"
    "\n\n--- JSON对象结构规范 (必须严格遵守) ---"
    "\n{"
    "\n  \"thought_summary\": \"(字符串, 可选但强烈推荐) 对你当前决策的详细中文总结。解释你为什么选择读取这些文件或执行这些命令，你的分析过程，以及你期望此步骤完成后达成的状态或下一步计划。如果配置完成，请明确说明。\",\n"
    "  \"files_to_read\": [\"(字符串数组, 可选) 相对于<PROJECT_ROOT_PATH_PLACEHOLDER>的文件路径列表。仅用于读取纯文本文件以获取配置信息 (如 requirements.txt, setup.py, pyproject.toml, .md, .yaml, .json, Dockerfile 等)。严禁请求读取二进制文件、大型数据文件或压缩包。如果你在本轮指定了要读取的文件，则`commands_to_execute`数组(如果提供)将被忽略，系统会先读取文件并将内容反馈给你，然后你再决定下一步。如果无需读取文件，则此键可省略或设置为空数组 `[]`。\"],\n"
    "  \"commands_to_execute\": [ (对象数组, 可选) "
    "\n    // 每个对象代表一条独立的、按顺序执行的shell命令。"
    "\n    // 如果本轮指定了`files_to_read`，则此数组将被忽略，应设置为空数组 `[]` 或省略。"
    "\n    // 如果没有命令要执行（例如，等待文件读取结果，或配置已完成），则此键可省略或设置为空数组 `[]`。"
    "\n    { "
    "\n      \"command_line\": \"(字符串, 必需) 要执行的单行shell命令。严禁使用 `&&` 或 `;` 连接多个逻辑命令。每个逻辑操作应是数组中的一个独立命令对象。\",\n"
    "      \"description\": \"(字符串, 必需, 中文) 对该命令目的的简短中文描述。\"\n"
    "    }"
    "\n    // ... (更多命令对象) ... "
    "\n  ]\n"
    "}\n"
    "\n\n--- 命令生成指南 (Windows - 至关重要，必须严格遵守) ---"
    "\n1. **工作目录**: 所有需要访问项目文件的命令 (如 `pip install -r requirements.txt`, `python your_script.py`) 都将自动在上面“当前任务状态”中指定的 `<PROJECT_ROOT_PATH_PLACEHOLDER>` 下执行。你绝对禁止生成 `cd` 命令，也绝对禁止在任何命令 (包括 `conda run`) 中使用 `--cwd` 或其他方式指定工作目录。"
    "\n2. **Conda环境**: "
    "\n   - **创建**: 必须使用 `conda create -n <ENV_NAME_PLACEHOLDER> python=<版本号> -y`。请务必使用上面“当前任务状态”中指定的 `<ENV_NAME_PLACEHOLDER>` 作为环境名称。"
    "\n   - **在环境中执行命令**: 必须严格使用 `conda run -n <ENV_NAME_PLACEHOLDER> <命令>` 的格式。同样，请务必使用 `<ENV_NAME_PLACEHOLDER>`。例如: `conda run -n <ENV_NAME_PLACEHOLDER> python -m pip install -r requirements.txt`。"
    "\n   - **禁止**: 绝对禁止生成 `conda activate` 或 `source activate` 命令。在 `conda run` 中也绝对禁止使用 `--cwd`。"
    "\n3. **Pip**: 在Conda环境中使用pip时，必须通过 `conda run -n <ENV_NAME_PLACEHOLDER> python -m pip ...` 调用。"
    "\n4. **通用命令**: 你可以使用 `dir`, `tree /F` (查看目录结构), `type` (查看文件内容，但优先使用 `files_to_read` JSON字段让系统读取), `curl` (下载文件)。"
    "\n\n--- 交互流程与输出示例 (你的输出应仅为花括号内的JSON内容) ---"
    "\n**示例1: 初始分析，LLM决定先读取文件 (当前任务状态: 目标环境名 'proj_env', 项目根目录 'C:\\cloned\\my_proj', README摘要 '见下文')**"  # 更明确地指示状态
    "\n{\n"
    "  \"thought_summary\": \"项目已克隆。根据“当前任务状态”中提供的README摘要，项目似乎需要特定版本的库，且依赖可能在 'requirements.txt'。我将请求读取 'requirements.txt'。\",\n"
    "  \"files_to_read\": [\"requirements.txt\"],\n"
    "  \"commands_to_execute\": []\n"
    "}\n"
    "\n**(系统将读取文件，并将内容连同历史反馈给你。下一轮，“当前任务状态”中的项目根目录和环境名会保持，README摘要也会持续提供。)**\n"
    "\n**示例2: LLM收到文件内容后，决定创建环境并执行命令 (当前任务状态: 目标环境名 'proj_env', 项目根目录 'C:\\cloned\\my_proj', README摘要 '...')**"
    "\n{\n"
    "  \"thought_summary\": \"已收到 'requirements.txt' 内容。根据此信息和系统提示中指定的当前目标环境名称 'proj_env' 及Python版本建议（如有），我将创建Conda环境并安装依赖。\",\n"
    "  \"files_to_read\": [],\n"
    "  \"commands_to_execute\": [\n"
    "    {\"command_line\": \"conda create -n proj_env python=3.10 -y\", \"description\": \"创建Conda环境 proj_env (Python 3.10)\"},\n"
    "    {\"command_line\": \"conda run -n proj_env python -m pip install -r requirements.txt\", \"description\": \"在proj_env中安装requirements.txt内的依赖\"}\n"
    "  ]\n"
    "}\n"
    "\n--- 请严格按照上述指南和JSON结构规范生成你的唯一JSON响应 ---"
)

# 扩大上下文限制，同时注意性能和模型实际能力
MAX_TOTAL_PROMPT_CHARS_APPROX = 100000  # 约等于32k-50k tokens，取决于内容
MAX_CONVERSATION_HISTORY_CHARS = 80000  # 允许历史占据大部分空间
MAX_LLM_OUTPUT_TOKENS = 12000  # JSON输出通常不需要特别长，但thought_summary可能较长
MAX_LLM_RETRIES = 2
MAX_HISTORY_ITEMS = 70  # 增加历史轮次

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_please_change_it_now_!@#$%^&*()_+'
socketio = SocketIO(app, async_mode='threading')

llm_client: Optional[llm.LLMClient] = None
project_file_cache: Dict[str, str] = {}  # 文件路径 -> 文件内容 (可以是完整内容或大摘要)
conversation_history: List[Dict[str, Any]] = []

# 全局存储首次读取的README内容摘要，以便持续提供给LLM
# 也可以考虑将其作为 step_data 的一部分传递，但全局或LLMClient实例变量更简单
initial_readme_summary_for_llm: Optional[str] = None


def initialize_llm_client(system_prompt_template: str, sid: Optional[str] = None) -> bool:
    global llm_client, project_file_cache, conversation_history, initial_readme_summary_for_llm
    project_file_cache = {}
    conversation_history = []
    initial_readme_summary_for_llm = None  # Reset on re-init
    try:
        llm_client = llm.LLMClient(api_key=LLM_API_KEY, model_name=LLM_MODEL_NAME, base_url=LLM_BASE_URL,
                                   system_prompt=system_prompt_template,
                                   max_history_turns=0)
        msg = f"LLM客户端已使用模型 {LLM_MODEL_NAME} 初始化 (使用系统提示模板)。"
        print(msg if not sid else f"SID {sid}: {msg}")
        if sid: socketio.emit('status_update', {'message': msg, 'type': 'info'}, room=sid, namespace='/')
        return True
    except Exception as e:
        err_msg = f"LLM初始化错误: {e}"
        print(err_msg if not sid else f"SID {sid}: {err_msg}")
        if sid: socketio.emit('error_message', {'message': err_msg, 'type': 'error'}, room=sid, namespace='/')
        return False


initialize_llm_client(DEFAULT_SYSTEM_PROMPT_TEMPLATE)


def build_llm_input_for_client(
        current_user_query_segment: str,
        project_root_for_system_prompt: str,
        env_name_for_system_prompt: str,
        readme_summary_for_system_prompt: Optional[str]  # 新增参数
) -> Tuple[str, str]:
    global conversation_history, llm_client

    final_system_prompt = DEFAULT_SYSTEM_PROMPT_TEMPLATE
    final_system_prompt = final_system_prompt.replace("<PROJECT_ROOT_PATH_PLACEHOLDER>",
                                                      project_root_for_system_prompt or "当前未设置或未知")
    final_system_prompt = final_system_prompt.replace("<ENV_NAME_PLACEHOLDER>",
                                                      env_name_for_system_prompt or "当前未设置或未知")
    final_system_prompt = final_system_prompt.replace("<README_CONTENT_PLACEHOLDER>",
                                                      readme_summary_for_system_prompt or "未提供或未读取")

    history_header = "\n\n--- 对话历史回顾 (最近的交互在前，包含关键信息和你的先前决策，请仔细阅读以保持上下文连贯性) ---\n"
    history_accumulator_str = ""
    current_history_chars = 0

    # 预算: MAX_TOTAL_PROMPT_CHARS_APPROX - len(final_system_prompt) - len(current_user_query_segment) - len(history_header) - 安全buffer
    # 将安全buffer调小，因为我们希望最大化历史
    history_char_budget = MAX_TOTAL_PROMPT_CHARS_APPROX - len(final_system_prompt) - \
                          len(current_user_query_segment) - len(history_header) - 5000
    if history_char_budget > MAX_CONVERSATION_HISTORY_CHARS:
        history_char_budget = MAX_CONVERSATION_HISTORY_CHARS
    if history_char_budget < 0:
        history_char_budget = 0;
        print("[WARN] History char budget negative.")

    temp_history_parts = []
    # 为了确保README摘要(如果存在且重要)不被轻易截断，可以考虑将其作为高优先级历史项，
    # 或者在 current_user_query_segment 中明确包含（如果它与当前轮次相关）。
    # 目前，它通过系统提示中的 <README_CONTENT_PLACEHOLDER> 传递。

    for entry in reversed(conversation_history):
        entry_str = "";
        entry_type = entry.get("type");
        content = entry.get("content")
        env_name_hist = entry.get("env_name_at_time", env_name_for_system_prompt)

        # 截断标准输出和标准错误，以防它们过长导致LLM崩溃
        # 这些片段主要用于LLM理解命令是否成功及原因
        max_cmd_output_snippet = MAX_CONVERSATION_HISTORY_CHARS // (MAX_HISTORY_ITEMS or 1) // 3  # 每个历史条目中命令输出的最大长度
        if max_cmd_output_snippet < 500: max_cmd_output_snippet = 500  # 最小保证长度
        if max_cmd_output_snippet > 30000: max_cmd_output_snippet = 30000  # 最大片段，避免单个命令输出过长

        if entry_type == "command_execution_result" and isinstance(content, dict):
            stdout_s = str(content.get('stdout', ''))
            stderr_s = str(content.get('stderr', ''))
            # 智能截断：如果过长，取首尾
            if len(stdout_s) > max_cmd_output_snippet: stdout_s = stdout_s[
                                                                  :max_cmd_output_snippet // 2] + "\n...\n(输出过长已截断)\n...\n" + stdout_s[
                                                                                                                                     -max_cmd_output_snippet // 2:]
            if len(stderr_s) > max_cmd_output_snippet: stderr_s = stderr_s[
                                                                  :max_cmd_output_snippet // 2] + "\n...\n(输出过长已截断)\n...\n" + stderr_s[
                                                                                                                                     -max_cmd_output_snippet // 2:]

            entry_str = (f"\n[上一个系统操作结果 - 命令执行]:\n"
                         f"  命令: `{content.get('command_executed', 'N/A')}`\n"
                         f"  工作目录: `{content.get('working_directory', '默认')}`\n"
                         f"  返回码: {content.get('return_code', 'N/A')}\n"
                         f"  标准输出:\n```text\n{stdout_s or '(无标准输出)'}\n```\n"
                         f"  标准错误:\n```text\n{stderr_s or '(无标准错误)'}\n```\n")
        elif entry_type == "user_input_to_llm" and isinstance(content, dict):
            entry_str = f"\n[先前发送给你的指令上下文 (当时目标环境: '{env_name_hist}')]:\n{content.get('context_summary', '无总结')}\n"
        elif entry_type == "llm_structured_output" and isinstance(content, dict):
            summary = content.get('thought_summary', '(无总结)')
            files_req = content.get('files_to_read', [])
            cmds_req = content.get('commands_to_execute', [])
            entry_str = f"\n[你之前的JSON响应 (当时目标环境: '{env_name_hist}')]:\n  思考总结: {summary}\n"
            if files_req: entry_str += f"  请求读取文件: {files_req}\n"
            if cmds_req: entry_str += f"  请求执行命令: {[c.get('command_line', 'N/A') for c in cmds_req if isinstance(c, dict)]}\n"
        elif entry_type == "llm_raw_unparsable_output" and isinstance(content, str):
            entry_str = f"\n[你先前未解析的原始输出 (这通常表示格式错误)]:\n```text\n{content[:10000]}\n```\n"  # 保持较大摘要

        if current_history_chars + len(entry_str) <= history_char_budget:
            temp_history_parts.append(entry_str);
            current_history_chars += len(entry_str)
        else:
            print(
                f"DEBUG: History truncated. Budget: {history_char_budget} chars, Kept: {current_history_chars} chars.");
            break

    if temp_history_parts:
        history_accumulator_str = history_header + "".join(reversed(temp_history_parts))
    else:
        history_accumulator_str = history_header + "(当前无先前对话历史可供回顾，或历史过长已被截断)\n"

    final_user_input_with_history = history_accumulator_str + "\n--- 当前用户指令/反馈 (请基于此和上述历史及系统提示进行回应) ---\n" + current_user_query_segment
    return final_system_prompt, final_user_input_with_history


# ... (add_to_conversation_history, stream_command_output, extract_json_from_llm_response, read_project_files - 这些可以保持你之前的版本，确保它们与上下文限制协同工作)
# ... (我将使用你之前提供且已工作的这些函数的版本，仅做微小调整以适应新的上下文策略)

def add_to_conversation_history(entry_type: str, content: Any, env_name_at_time: Optional[str] = None):
    global conversation_history
    entry: Dict[str, Any] = {"type": entry_type, "content": content, "timestamp": time.time()}
    if env_name_at_time and (entry_type == "user_input_to_llm" or entry_type == "llm_structured_output"):
        entry["env_name_at_time"] = env_name_at_time
    conversation_history.append(entry)
    if len(conversation_history) > MAX_HISTORY_ITEMS:  # MAX_HISTORY_ITEMS 现在更大
        conversation_history = conversation_history[-MAX_HISTORY_ITEMS:]


def stream_command_output(sid: str, command_input: Union[str, List[str]], working_dir: Optional[str] = None) -> Dict[
    str, Any]:
    command_to_log_str: str
    if isinstance(command_input, list):
        command_to_log_str = subprocess.list2cmdline(command_input)
    else:
        command_to_log_str = command_input
    socketio.emit('command_stream', {'type': 'command_start', 'command': command_to_log_str}, room=sid, namespace='/')
    full_stdout_str, full_stderr_str, final_return_code = "", "", -1
    try:
        for stream_type, content in executor.execute_command_stream(command_input, working_directory=working_dir):
            # 限制发送到前端的单个块的大小，以防前端处理不过来导致刷屏问题
            # 但这可能会切断 \r\n 序列，前端需要能处理。
            # 对于后端日志，我们仍然累积完整的。
            # 这个问题更多是前端渲染性能问题。这里的 sleep(0.01) 或 0.02 可能更合适。
            if stream_type == 'stdout':
                full_stdout_str += content;
                # 为了避免前端刷屏，可以考虑在这里缓冲或只发送部分更新，但目前保持原样
                socketio.emit('command_stream', {'type': 'stdout_chunk', 'chunk': content}, room=sid, namespace='/')
            elif stream_type == 'stderr':
                full_stderr_str += content;
                socketio.emit('command_stream', {'type': 'stderr_chunk', 'chunk': content}, room=sid, namespace='/')
            elif stream_type == 'return_code':
                final_return_code = int(content)
            socketio.sleep(0.015)  # 稍微增加 sleep 时间，减少消息频率
        socketio.emit('command_stream',
                      {'type': 'command_end', 'command': command_to_log_str, 'return_code': final_return_code},
                      room=sid, namespace='/')
    except Exception as e:
        error_line = f"stream_command_output error for '{command_to_log_str}': {e}";
        print(f"MAIN_PY ERROR: {error_line}")
        socketio.emit('command_stream', {'type': 'stderr_chunk', 'chunk': error_line + "\n"}, room=sid, namespace='/');
        full_stderr_str += error_line + "\n";
        final_return_code = -9999
        socketio.emit('command_stream',
                      {'type': 'command_end', 'command': command_to_log_str, 'return_code': final_return_code},
                      room=sid, namespace='/')
    return {"stdout": full_stdout_str, "stderr": full_stderr_str, "return_code": final_return_code,
            "command_executed": command_to_log_str, "working_directory": working_dir or os.getcwd()}


# extract_json_from_llm_response - 使用你之前确认可用的版本
def extract_json_from_llm_response(raw_response: str) -> Optional[str]:
    if not raw_response:
        return None

    # 1. 移除 <think>...</think> 标签及其内容
    # 使用 re.DOTALL 使 . 匹配换行符，re.IGNORECASE 使其不区分大小写
    text_cleaned = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL | re.IGNORECASE).strip()

    # 2. 移除其他已有的清理规则 (确保 <think> 在它们之前或与它们一起处理)
    text_cleaned = re.sub(r"<tool_code>.*?</tool_code>", "", text_cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    text_cleaned = re.sub(r"<tool_code>.*", "", text_cleaned,
                          flags=re.DOTALL | re.IGNORECASE).strip()  # Handle unclosed
    text_cleaned = re.sub(r"<function_calls>.*?</function_calls>", "", text_cleaned,
                          flags=re.DOTALL | re.IGNORECASE).strip()
    text_cleaned = re.sub(r"\[TOOL_CALLS\]?.*?\[/TOOL_CALLS\]?", "", text_cleaned,
                          flags=re.DOTALL | re.IGNORECASE).strip()
    text_cleaned = re.sub(r"<tool_calls>.*?</tool_calls>", "", text_cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    # 你可以根据需要添加更多这类标签的移除规则

    # --- 后续的 JSON 提取逻辑保持不变 ---

    # 3. 尝试找到 ```json ... ``` 代码块
    match_json_block = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text_cleaned, re.DOTALL)
    if match_json_block:
        return match_json_block.group(1).strip()

    # 4. 如果没有代码块，尝试从第一个 '{' 匹配到最后一个 '}'
    # 这个逻辑更宽松，假设JSON是主要内容
    first_brace = text_cleaned.find('{')
    last_brace = text_cleaned.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text_cleaned[first_brace: last_brace + 1]
        try:
            # 尝试解析以验证它确实是JSON
            json.loads(candidate)
            return candidate.strip()
        except json.JSONDecodeError:
            # 如果这个宽松的提取不是有效的JSON，则继续尝试更严格的括号匹配
            pass

            # 5. 如果以上方法都失败，使用严格的括号匹配（原始逻辑）
    if first_brace != -1:  # 确保 first_brace 在清理后仍然有效
        open_braces = 0
        json_candidate_strict = ""  # 使用新变量以避免与上面的 candidate 混淆
        for i in range(first_brace, len(text_cleaned)):
            char = text_cleaned[i]
            # json_candidate_strict += char # 逐步构建是在严格括号匹配时的做法
            if char == '{':
                if open_braces == 0:  # 记录第一个真正JSON对象的开始
                    json_start_index = i
                open_braces += 1
            elif char == '}':
                open_braces -= 1
                if open_braces == 0 and 'json_start_index' in locals():  # 确保我们有一个开始
                    return text_cleaned[json_start_index: i + 1].strip()
        # 如果循环结束时 open_braces 不为0，说明括号不匹配

    print(
        f"DEBUG: extract_json_from_llm_response: No valid JSON found after cleaning. Cleaned text preview: {text_cleaned[:500]}")
    return None


# read_project_files - 使用你之前确认可用的版本，确保文件大小限制与上下文预算协调
def read_project_files(sid: str, project_root: str, relative_paths: List[str]) -> Dict[str, str]:
    global project_file_cache
    contents: Dict[str, str] = {};
    # 这些限制应小于 MAX_CONVERSATION_HISTORY_CHARS，因为它们会被放入历史
    max_file_size_chars = MAX_CONVERSATION_HISTORY_CHARS // 5  # 单个文件最大字符数
    total_chars_limit_this_call = MAX_CONVERSATION_HISTORY_CHARS // 2  # 本次调用读取文件总字符数
    current_read_chars_this_call = 0

    for rel_path_raw in relative_paths:
        rel_path = rel_path_raw.strip().replace("`", "").replace("'", "").replace("\"", "")
        if not rel_path: continue
        if ".." in rel_path or os.path.isabs(rel_path):
            msg = f"文件读取错误：不允许的路径格式 '{rel_path_raw}'。";
            socketio.emit('status_update', {'message': msg, 'type': 'error'}, room=sid, namespace='/')
            contents[rel_path_raw] = "[错误：不允许的路径格式]";
            project_file_cache[rel_path] = contents[rel_path_raw];
            continue
        abs_path = os.path.normpath(os.path.join(project_root, rel_path));
        norm_abs_path = os.path.normcase(abs_path);
        norm_project_root = os.path.normcase(os.path.abspath(project_root))
        if not (norm_abs_path.startswith(norm_project_root + os.sep) or norm_abs_path == norm_project_root):
            msg = f"文件读取错误：路径 '{rel_path_raw}' (解析为 '{abs_path}') 超出项目范围 ('{project_root}')。";
            socketio.emit('status_update', {'message': msg, 'type': 'error'}, room=sid, namespace='/')
            contents[rel_path_raw] = "[错误：路径超出项目范围]";
            project_file_cache[rel_path] = contents[rel_path_raw];
            continue

        if rel_path in project_file_cache and project_file_cache[rel_path].startswith(
                "[错误："):  # Always re-read if previous was error
            pass  # Force re-read
        elif rel_path in project_file_cache:  # Check cache for non-error entries
            cached_content = project_file_cache[rel_path]
            if current_read_chars_this_call + len(cached_content) <= total_chars_limit_this_call:
                contents[rel_path_raw] = cached_content;
                current_read_chars_this_call += len(cached_content)
                socketio.emit('status_update',
                              {'message': f"从缓存中获取文件 '{rel_path_raw}' ({len(cached_content)} chars)。",
                               'type': 'info'}, room=sid, namespace='/');
                continue
            else:
                msg = f"从缓存读取文件 '{rel_path_raw}' 将超出本次调用总字符数限制，已跳过。";
                socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
                contents[rel_path_raw] = "[错误：超出本次文件读取总字符数限制 (来自缓存)，未加载]";
                continue
        try:
            if os.path.exists(abs_path) and os.path.isfile(abs_path):
                file_size_bytes = os.path.getsize(abs_path)
                # Rough check: if bytes > 2*max_chars (very conservative for UTF-8), skip early
                if file_size_bytes > max_file_size_chars * 2:
                    msg = f"文件 '{rel_path_raw}' 初步判断过大 ({file_size_bytes}字节)，已跳过。"
                    socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
                    contents[rel_path_raw] = f"[错误：文件过大 ({file_size_bytes}B)，已跳过读取]";
                    project_file_cache[rel_path] = contents[rel_path_raw];
                    continue

                with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()

                if len(content) > max_file_size_chars:
                    msg = f"文件 '{rel_path_raw}' 内容过大 ({len(content)}字符 > {max_file_size_chars}字符)，已截断。"
                    socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/');
                    content = content[:max_file_size_chars]

                if current_read_chars_this_call + len(content) > total_chars_limit_this_call:
                    remaining_budget = total_chars_limit_this_call - current_read_chars_this_call
                    if remaining_budget > 0:
                        content = content[:remaining_budget]
                        msg = f"读取文件 '{rel_path_raw}' 内容后超出总字符数限制，已截断以适应剩余空间。"
                        socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
                    else:  # No budget left
                        msg = f"读取文件 '{rel_path_raw}' 因超出总字符数限制，未加载。"
                        socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
                        contents[rel_path_raw] = "[错误：超出本次文件读取总字符数限制，未加载]";
                        break

                if content:  # Only add if there's content after potential truncation
                    contents[rel_path_raw] = content;
                    project_file_cache[rel_path] = content
                    current_read_chars_this_call += len(content)
                    socketio.emit('status_update',
                                  {'message': f"已读取文件 '{rel_path_raw}' (大小: {len(content)}字符, 可能已截断)。",
                                   'type': 'info'}, room=sid, namespace='/')
                elif not (rel_path_raw in contents):  # If content became empty and not already marked as error
                    contents[rel_path_raw] = "[错误：文件内容截断后为空或读取问题]"

            else:  # File not found
                msg = f"LLM请求的文件 '{rel_path_raw}' (解析为 '{abs_path}') 不存在或不是一个文件。"
                socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
                contents[rel_path_raw] = "[错误：文件不存在或不是文件]";
                project_file_cache[rel_path] = contents[rel_path_raw]
        except Exception as e:
            msg = f"读取文件 '{rel_path_raw}' 时发生错误: {e}";
            socketio.emit('status_update', {'message': msg, 'type': 'error'}, room=sid, namespace='/')
            contents[rel_path_raw] = f"[读取文件时发生错误: {e}]";
            project_file_cache[rel_path] = contents[rel_path_raw]
    return contents


@socketio.on('connect')
def handle_connect():
    sid = request.sid;
    print(f'客户端连接: {sid}')
    socketio.emit('status_update', {'message': '后端已连接。请提供Git仓库URL开始配置。', 'type': 'info'}, room=sid,
                  namespace='/')
    if not initialize_llm_client(DEFAULT_SYSTEM_PROMPT_TEMPLATE, sid=sid):
        socketio.emit('error_message', {'message': 'LLM客户端自动初始化失败。', 'type': 'error'}, room=sid,
                      namespace='/')
    socketio.emit('system_prompt_update', {'system_prompt': DEFAULT_SYSTEM_PROMPT_TEMPLATE}, room=sid, namespace='/')


@socketio.on('disconnect')
def handle_disconnect(): print(f'客户端断开: {request.sid}')


@socketio.on('update_system_prompt')
def handle_update_system_prompt(data: Dict[str, str]):
    sid = request.sid;
    global DEFAULT_SYSTEM_PROMPT_TEMPLATE
    new_prompt_template = data.get('system_prompt', DEFAULT_SYSTEM_PROMPT_TEMPLATE).strip()
    if not new_prompt_template: socketio.emit('error_message', {'message': "系统提示词不能为空。", 'type': 'error'},
                                              room=sid, namespace='/'); return
    DEFAULT_SYSTEM_PROMPT_TEMPLATE = new_prompt_template
    if initialize_llm_client(DEFAULT_SYSTEM_PROMPT_TEMPLATE, sid=sid):  # This also clears history & readme cache
        socketio.emit('status_update', {'message': f"系统提示词模板已更新。对话历史已重置。", 'type': 'success'},
                      room=sid, namespace='/')
        socketio.emit('system_prompt_update', {'system_prompt': DEFAULT_SYSTEM_PROMPT_TEMPLATE}, room=sid,
                      namespace='/')
    else:
        socketio.emit('error_message', {'message': "更新提示词失败。", 'type': 'error'}, room=sid, namespace='/')


def process_setup_step(sid: str, step_data: Dict[str, Any], retry_count: int = 0):
    with app.app_context():
        global initial_readme_summary_for_llm  # To access and update the global README summary

        git_url = step_data.get('git_url')
        if not git_url: socketio.emit('error_message', {'message': "内部错误：git_url缺失。", 'type': 'error'}, room=sid,
                                      namespace='/'); return

        env_name_input = step_data.get('env_name', '').strip()
        project_name_from_url = git_url.split('/')[-1].replace('.git', '') if git_url else "unknown_project"

        # Consistently determine or retrieve env_name
        env_name = step_data.get('determined_env_name')
        if not env_name:
            raw_env_name_base = env_name_input if env_name_input else project_name_from_url.lower()
            safe_env_name_base = re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_env_name_base)
            env_name = safe_env_name_base if env_name_input and safe_env_name_base else safe_env_name_base + "_env"  # ensure suffix if auto-generated
            if not env_name or env_name == "_env": env_name = "default_project_env"
            step_data['determined_env_name'] = env_name

        project_name_for_dir = re.sub(r'[^a-zA-Z0-9_-]', '_', project_name_from_url)
        if not project_name_for_dir: project_name_for_dir = "cloned_project_default_name"

        project_cloned_root_path = step_data.get('project_cloned_root_path')
        initial_readme_name = step_data.get('initial_readme_name')  # Name of the readme file
        # current_readme_summary is the content that will be passed to build_llm_input_for_client
        current_readme_summary = step_data.get('readme_summary_for_llm', initial_readme_summary_for_llm)

        current_user_query_segment = ""

        if step_data.get('step_type') == 'initial_analysis':
            clone_base_dir = os.path.join(os.getcwd(), "cloned_projects_area")
            try:
                os.makedirs(clone_base_dir, exist_ok=True)
            except OSError as e:
                socketio.emit('error_message', {'message': f"创建克隆目录失败: {e}", 'type': 'error'}, room=sid,
                              namespace='/'); return

            project_cloned_root_path = os.path.abspath(os.path.join(clone_base_dir, project_name_for_dir))
            step_data['project_cloned_root_path'] = project_cloned_root_path

            socketio.emit('status_update', {'message': f"项目将在本地: {project_cloned_root_path}", 'type': 'info'},
                          room=sid, namespace='/')
            if os.path.exists(project_cloned_root_path):
                socketio.emit('status_update', {'message': f"清理旧项目: {project_cloned_root_path}", 'type': 'info'},
                              room=sid, namespace='/')
                try:
                    shutil.rmtree(project_cloned_root_path)
                except Exception as e:
                    socketio.emit('error_message', {'message': f"清理旧目录失败: {e}", 'type': 'error'}, room=sid,
                                  namespace='/'); return

            socketio.emit('status_update', {'message': f"开始克隆: {git_url}...", 'type': 'info'}, room=sid,
                          namespace='/')
            git_cmd_list = ["git", "clone", git_url, project_cloned_root_path]
            clone_res = stream_command_output(sid, git_cmd_list, os.getcwd())
            add_to_conversation_history("command_execution_result", clone_res, env_name_at_time=env_name)
            if clone_res.get('return_code', -1) != 0:
                socketio.emit('error_message',
                              {'message': f"Git克隆失败: {clone_res.get('stderr', '未知错误')}", 'type': 'error'},
                              room=sid, namespace='/');
                return
            socketio.emit('status_update', {'message': "仓库克隆成功。", 'type': 'success'}, room=sid, namespace='/')

            # Read README and store its summary globally and in step_data for this turn
            readme_content_for_prompt = "未找到README文件或读取时出错。"
            possible_readme_files = ["README.md", "readme.md", "README.rst", "README.txt", "README", "ReadMe.md"]
            for name in possible_readme_files:
                p = os.path.join(project_cloned_root_path, name)
                if os.path.isfile(p):
                    try:
                        with open(p, 'r', encoding='utf-8', errors='replace') as f:
                            # Read a significant portion for the initial prompt, but not necessarily the whole thing if huge
                            # The full content can be cached if LLM later asks for this specific file by name.
                            full_readme_content = f.read()
                            project_file_cache[name] = full_readme_content  # Cache full content
                            # For the prompt, use a large summary.
                            readme_content_for_prompt = full_readme_content[:MAX_CONVERSATION_HISTORY_CHARS // 8]
                            if len(full_readme_content) > len(readme_content_for_prompt):
                                readme_content_for_prompt += "\n... (README内容过长，此处为摘要)"

                        initial_readme_name = name;
                        step_data['initial_readme_name'] = name
                        initial_readme_summary_for_llm = readme_content_for_prompt  # Store globally for system prompt
                        current_readme_summary = readme_content_for_prompt  # Use for this turn's user query
                        step_data['readme_summary_for_llm'] = readme_content_for_prompt  # Pass in step_data too
                        socketio.emit('status_update', {'message': f"{name} 已找到并读取摘要。", 'type': 'info'},
                                      room=sid, namespace='/');
                        break
                    except Exception as e:
                        readme_content_for_prompt = f"读取{name}错误: {e}"; initial_readme_summary_for_llm = readme_content_for_prompt; break
            if not initial_readme_name:  # If no readme found after loop
                initial_readme_summary_for_llm = readme_content_for_prompt  # Store "not found" message
                current_readme_summary = readme_content_for_prompt
                step_data['readme_summary_for_llm'] = readme_content_for_prompt

            current_user_query_segment = (
                f"任务：为新克隆的Git仓库 '{git_url}' (项目名: {project_name_for_dir}) 进行Conda环境配置。\n"
                f"当前系统提示中已包含目标Conda环境名称 '{env_name}'、项目根目录 '{project_cloned_root_path}' 以及下方提供的README摘要。请基于这些信息进行分析。\n"
                f"项目 README ('{initial_readme_name or '未找到'}') 内容摘要如下:\n```text\n{readme_content_for_prompt}\n```\n\n"
                f"请进行初步分析。如果需要查看项目中的其他文件以获取更详细的配置信息，请在 `files_to_read` 中列出它们的相对路径。"
                f"如果你认为已有足够信息，请在 `commands_to_execute` 字段中提供操作命令。"
            )
            add_to_conversation_history("user_input_to_llm", {
                "context_summary": f"初始分析请求：项目 {project_name_for_dir}, README({initial_readme_name or 'N/A'})摘要已提供."},
                                        env_name_at_time=env_name)

        elif step_data.get('step_type') == 'llm_output_retry':
            current_user_query_segment = (
                f"\n[系统重要提示]: 你上一次的输出未能解析为预期的JSON格式，或缺少必要的指令/文件请求，或生成的命令不符合规范。本次是第 {retry_count + 1} 次尝试。\n"
                f"请严格检查并遵循系统提示中关于JSON结构、命令格式的所有规范。\n"
                f"回顾完整的对话历史和系统提示中提供的当前任务状态 (项目根目录 '{project_cloned_root_path}', 目标环境 '{env_name}', README摘要), 然后重新生成包含有效行动指令的JSON对象。"
            )

        else:  # 'feedback' or 'feedback_after_read'
            prev_res = step_data.get('previous_command_result', {})
            files_read = step_data.get('files_just_read_content', {})
            # ... (feedback_parts formatting - ensure it uses the new max_cmd_output_snippet logic if applied within build_llm_input_for_client) ...
            # For current_user_query_segment, provide concise feedback. Full details are in history.
            feedback_parts = []
            if files_read:
                feedback_parts.append("\n--- 系统已读取你请求的文件，内容如下（或摘要） ---")
                for path, content_val in files_read.items():
                    display_content = str(content_val)[:10000]  # Show a large snippet in this turn's prompt
                    if len(str(content_val)) > 10000: display_content += "\n...(文件内容过长，此处为摘要)..."
                    feedback_parts.append(f"文件 '{path}':\n```text\n{display_content}\n```\n")
            if prev_res and prev_res.get("command_executed"):
                feedback_parts.append(f"\n--- 上一步命令执行反馈 ---")
                feedback_parts.append(
                    f"命令: `{prev_res.get('command_executed', 'N/A')}` (返回码: {prev_res.get('return_code', 'N/A')})")
                if prev_res.get('return_code', -1) != 0:
                    feedback_parts.append("注意: 上一个命令执行失败。请分析标准输出/错误（已加入对话历史），并决定下一步。")
                else:
                    feedback_parts.append("上一个命令已成功执行。")

            current_user_query_segment = (
                    "".join(feedback_parts) +
                    f"\n\n--- 当前任务指令 ---\n"
                    f"请基于以上最新反馈、完整的对话历史以及系统提示中提供的“当前任务状态”（包括项目根目录 '{project_cloned_root_path}', 目标Conda环境名称 '{env_name}', 以及README摘要），继续进行配置。\n"
                    f"你需要读取更多文件吗？或者现在可以生成命令了？请给出你的JSON响应。"
            )
            add_to_conversation_history("user_input_to_llm",
                                        {"context_summary": "提供了命令执行结果和/或文件读取内容以供进一步决策."},
                                        env_name_at_time=env_name)

        if not llm_client: socketio.emit('error_message', {'message': "LLM客户端未初始化。", 'type': 'error'}, room=sid,
                                         namespace='/'); return

        final_system_prompt, full_user_input_with_history = build_llm_input_for_client(
            current_user_query_segment,
            project_cloned_root_path or "尚未确定",
            env_name,
            current_readme_summary  # Pass the current README summary
        )
        llm_client.system_prompt_content = final_system_prompt  # Update client's system prompt

        # ... (LLM call, JSON parsing, validation - use the stricter valid_json_structure from your last code)
        # ... (Action phase - ensure next_step_data_base includes 'determined_env_name': env_name and 'readme_summary_for_llm': current_readme_summary)
        socketio.emit('llm_prompt_sent', {
            'prompt_head': final_system_prompt[:3000] + f"... (SysPrompt Total: {len(final_system_prompt)} chars)",
            'prompt_tail': f"... (UserInput Total: {len(full_user_input_with_history)} chars) ..." + full_user_input_with_history[
                                                                                                     -3000:]},
                      room=sid, namespace='/')

        socketio.emit('status_update', {'message': "请求LLM分析及指令...", 'type': 'info'}, room=sid, namespace='/')
        accumulated_llm_text = ""
        socketio.emit('llm_stream_clear', {}, room=sid, namespace='/')
        try:
            for event_type, content_chunk_val in llm_client.get_response_stream(
                    full_user_input_with_history, max_tokens=MAX_LLM_OUTPUT_TOKENS, temperature=0.1):
                if event_type == "delta_content" and content_chunk_val is not None:
                    accumulated_llm_text += content_chunk_val
                    socketio.emit('llm_general_stream', {'token': content_chunk_val}, room=sid, namespace='/');
                    socketio.sleep(0.005)  # Slightly more sleep
                elif event_type == "error":
                    socketio.emit('error_message',
                                  {'message': f"LLM流式响应错误: {content_chunk_val}", 'type': 'error'}, room=sid,
                                  namespace='/'); break
                elif event_type == "stream_end":
                    socketio.emit('status_update', {'message': "LLM流式响应接收完毕。", 'type': 'info'}, room=sid,
                                  namespace='/'); break
        except Exception as e:
            socketio.emit('error_message', {'message': f"LLM get_response_stream调用错误: {e}", 'type': 'error'},
                          room=sid, namespace='/'); return

        if not accumulated_llm_text.strip(): socketio.emit('status_update',
                                                           {'message': "LLM响应为空。", 'type': 'warning'}, room=sid,
                                                           namespace='/')

        socketio.emit('llm_raw_response_debug', {'raw_response': accumulated_llm_text}, room=sid, namespace='/')
        json_string_candidate = extract_json_from_llm_response(accumulated_llm_text)
        json_object_parsed: Optional[Dict[str, Any]] = None;
        json_decode_error_occurred = False

        if json_string_candidate:
            try:
                json_object_parsed = json.loads(json_string_candidate)
            except json.JSONDecodeError as e:
                json_decode_error_occurred = True;
                print(f"SID {sid} JSON解析失败: {e}. Candidate: '{json_string_candidate}'")
                socketio.emit('status_update', {'message': f"LLM响应JSON解析失败: {e}", 'type': 'warning'}, room=sid,
                              namespace='/')
        else:
            json_decode_error_occurred = True

        has_cmds_key = "commands_to_execute" in (json_object_parsed or {})
        cmds_list = json_object_parsed.get("commands_to_execute", []) if json_object_parsed else []
        has_valid_cmds = has_cmds_key and isinstance(cmds_list, list) and len(cmds_list) > 0

        has_files_key = "files_to_read" in (json_object_parsed or {})
        files_list = json_object_parsed.get("files_to_read", []) if json_object_parsed else []
        has_valid_files = has_files_key and isinstance(files_list, list) and len(files_list) > 0

        has_thought = bool(json_object_parsed and json_object_parsed.get("thought_summary"))
        is_thought_only_valid = has_thought and not has_valid_cmds and not has_valid_files

        valid_json_structure = not json_decode_error_occurred and json_object_parsed is not None and \
                               (has_valid_cmds or has_valid_files or is_thought_only_valid)

        if not valid_json_structure:
            if retry_count < MAX_LLM_RETRIES:
                next_step_data = step_data.copy();
                next_step_data['step_type'] = 'llm_output_retry';
                next_step_data['last_bad_llm_output'] = accumulated_llm_text
                add_to_conversation_history("llm_raw_unparsable_output", accumulated_llm_text,
                                            env_name_at_time=env_name)
                socketio.emit('status_update', {'message': f"LLM输出无效，重试 ({retry_count + 1}/{MAX_LLM_RETRIES})。",
                                                'type': 'warning'}, room=sid, namespace='/')
                process_setup_step(sid, next_step_data, retry_count + 1);
                return
            else:
                socketio.emit('error_message',
                              {'message': f"LLM在 {MAX_LLM_RETRIES + 1} 次尝试后仍未能输出有效JSON。", 'type': 'error'},
                              room=sid, namespace='/');
                return

        add_to_conversation_history("llm_structured_output", json_object_parsed, env_name_at_time=env_name)
        socketio.emit('llm_structured_output_history', {'output': json_object_parsed}, room=sid, namespace='/')
        if json_object_parsed.get('thought_summary'): socketio.emit('llm_final_analysis_text', {
            'text': f"LLM决策总结:\n{json_object_parsed['thought_summary']}"}, room=sid, namespace='/')

        files_to_read_now = [f for f in files_list if
                             isinstance(f, str) and f.strip()]  # Use already fetched files_list

        actual_commands_to_run: List[Tuple[str, str]] = []
        for cmd_obj in cmds_list:  # Use already fetched cmds_list
            if isinstance(cmd_obj, dict) and isinstance(cmd_obj.get("command_line"), str) and cmd_obj[
                "command_line"].strip():
                original_cmd = cmd_obj["command_line"]
                cleaned_cmd = re.sub(r'\s*--cwd\s+([\'\"]?).*?\1\s*', ' ', original_cmd, flags=re.IGNORECASE).strip()
                cleaned_cmd = re.sub(r'\s*--working-directory\s+([\'\"]?).*?\1\s*', ' ', cleaned_cmd,
                                     flags=re.IGNORECASE).strip()
                if original_cmd != cleaned_cmd: socketio.emit('status_update', {
                    'message': f"警告：LLM命令含--cwd，已移除。原始: '{original_cmd}', 清理后: '{cleaned_cmd}'",
                    'type': 'warning'}, room=sid, namespace='/')
                if cleaned_cmd: actual_commands_to_run.append((cleaned_cmd, cmd_obj.get("description", "无描述")))
            else:
                socketio.emit('status_update',
                              {'message': f"警告：跳过格式不正确的命令对象: {cmd_obj}", 'type': 'warning'}, room=sid,
                              namespace='/')

        next_step_data_base = {'git_url': git_url, 'determined_env_name': env_name,
                               'initial_readme_name': initial_readme_name,
                               'project_cloned_root_path': project_cloned_root_path,
                               'readme_summary_for_llm': current_readme_summary}  # Carry over readme summary

        if files_to_read_now:
            socketio.emit('status_update',
                          {'message': f"LLM请求读取文件: {', '.join(files_to_read_now)}。", 'type': 'info'}, room=sid,
                          namespace='/')
            read_files_content = read_project_files(sid, project_cloned_root_path or "",
                                                    files_to_read_now) if project_cloned_root_path else {
                f: "[错误: 项目根路径未确定]" for f in files_to_read_now}
            if not project_cloned_root_path: socketio.emit('error_message', {
                'message': f"项目根路径 '{project_cloned_root_path}' 无效，无法读取文件。", 'type': 'error'}, room=sid,
                                                           namespace='/')
            next_step_data = next_step_data_base.copy();
            next_step_data['step_type'] = 'feedback_after_read';
            next_step_data['files_just_read_content'] = read_files_content
            next_step_data['previous_command_result'] = step_data.get('previous_command_result', {});
            process_setup_step(sid, next_step_data, 0);
            return

        if actual_commands_to_run:
            last_cmd_res = {};
            all_ok = True
            for i, (cmd_str, desc) in enumerate(actual_commands_to_run):
                socketio.emit('status_update',
                              {'message': f"执行 ({i + 1}/{len(actual_commands_to_run)}): {cmd_str} ({desc})",
                               'type': 'info'}, room=sid, namespace='/')
                cmd_cwd = None
                if not ("conda create" in cmd_str.lower() or "conda env create" in cmd_str.lower()):
                    if project_cloned_root_path and os.path.isdir(project_cloned_root_path):
                        cmd_cwd = project_cloned_root_path
                    else:
                        socketio.emit('status_update', {
                            'message': f"警告: 项目路径 '{project_cloned_root_path}' 无效，命令 '{cmd_str}' 将在默认目录执行。",
                            'type': 'warning'}, room=sid, namespace='/')
                last_cmd_res = stream_command_output(sid, cmd_str, working_dir=cmd_cwd)
                add_to_conversation_history("command_execution_result", last_cmd_res, env_name_at_time=env_name)
                if last_cmd_res.get('return_code', -1) != 0:
                    socketio.emit('error_message',
                                  {'message': f"命令 '{cmd_str}' 执行失败 (RC: {last_cmd_res.get('return_code')})。",
                                   'type': 'error'}, room=sid, namespace='/');
                    all_ok = False;
                    break
            next_step_data = next_step_data_base.copy();
            next_step_data['step_type'] = 'feedback';
            next_step_data['previous_command_result'] = last_cmd_res
            socketio.emit('status_update', {'message': "当前批次LLM指令执行完毕。" if all_ok else "批次指令因错误中断。",
                                            'type': 'success' if all_ok else 'warning'}, room=sid, namespace='/')
            process_setup_step(sid, next_step_data, 0);
            return

        socketio.emit('status_update', {'message': "LLM指示配置完成或无更多行动指令。", 'type': 'success'}, room=sid,
                      namespace='/')
        socketio.emit('setup_complete', {'env_name': env_name, 'project_path': project_cloned_root_path}, room=sid,
                      namespace='/')


@socketio.on('start_initial_setup')
def handle_start_initial_setup(data: Dict[str, Any]):
    sid = request.sid;
    git_url = data.get('git_url');
    env_name_frontend = data.get('env_name', '').strip()
    global project_file_cache, conversation_history, initial_readme_summary_for_llm
    project_file_cache = {};
    conversation_history = [];
    initial_readme_summary_for_llm = None  # Reset global readme summary
    socketio.emit('clear_history_display', {}, room=sid, namespace='/')
    if not initialize_llm_client(DEFAULT_SYSTEM_PROMPT_TEMPLATE, sid=sid):
        socketio.emit('error_message', {'message': '开始任务前LLM客户端初始化失败。', 'type': 'error'}, room=sid,
                      namespace='/');
        return
    initial_step_data = {
        'step_type': 'initial_analysis', 'git_url': git_url,
        'env_name': env_name_frontend,  # User's preferred name, will be processed into determined_env_name
        'determined_env_name': None,
        'initial_readme_name': None,
        'readme_summary_for_llm': None,  # Will be filled after reading
        'project_cloned_root_path': None,
        'previous_command_result': {}, 'files_just_read_content': {}
    }
    thread = threading.Thread(target=process_setup_step, args=(sid, initial_step_data, 0));
    thread.daemon = True;
    thread.start()


@app.route('/')
def index(): return render_template('run.html', current_system_prompt=DEFAULT_SYSTEM_PROMPT_TEMPLATE)


if __name__ == '__main__':
    print("启动Flask服务器及SocketIO...")
    print(f"LLM配置: 模型='{LLM_MODEL_NAME}', API Key='{LLM_API_KEY[:5]}...', Base URL='{LLM_BASE_URL}'")
    if 'llm' not in globals() or 'executor' not in globals():
        print("严重错误: llm.py 或 command_executor.py 未正确加载。")
    else:
        print(f"系统提示词模板长度 (不含动态部分): {len(DEFAULT_SYSTEM_PROMPT_TEMPLATE)} chars")
        # Ensure executor paths are found at startup
        if platform.system() == "Windows":
            executor.find_and_set_conda_paths()
        else:
            executor.find_and_set_conda_paths()
        socketio.run(app, debug=True, host='0.0.0.0', port=5000, use_reloader=False, allow_unsafe_werkzeug=True)