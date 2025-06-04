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

try:
    import llm
    import command_executor as executor  # 假设 command_executor.py 已经添加了 write_file_content 函数
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
    "\n4. 在每一步，你都需要根据当前所有已知信息（包括完整的对话历史）来决定下一步行动：是请求读取更多文件，或是访问互联网查找信息，还是生成执行命令，或者判断配置已完成。"
    "\n\n--- 当前任务状态 (此部分信息将在每次调用时更新，请务必参考！) ---"  # 强调参考
    "\n- **项目根目录**: <PROJECT_ROOT_PATH_PLACEHOLDER>"
    "\n- **目标Conda环境名称**: <ENV_NAME_PLACEHOLDER>"
    "\n- **从README提取的关键信息**: <README_CONTENT_PLACEHOLDER>"  # 修改：明确这是提取的信息
    "\n- **重要**: 你所有的决策和生成的命令都必须围绕以上指定的项目根目录和目标Conda环境名称进行。如果这些信息显示为“未指定”或“未提供”，请在你的`thought_summary`中指出需要这些信息才能继续，或者基于已有信息进行合理推断（例如，如果环境名未指定，你可以建议一个）。"
    "\n\n--- JSON对象结构规范 (必须严格遵守) ---"
    "\n{"
    "\n  \"thought_summary\": \"(字符串, 可选但强烈推荐) 对你当前决策的详细中文总结。解释你为什么选择读取这些文件或执行这些命令，你的分析过程，以及你期望此步骤完成后达成的状态或下一步计划。如果配置完成，请明确说明。\",\n"
    "  \"files_to_read\": [\"(字符串数组, 可选) 相对于<PROJECT_ROOT_PATH_PLACEHOLDER>的文件路径列表。仅用于读取纯文本文件以获取配置信息 (如 requirements.txt, setup.py, pyproject.toml, .md, .yaml, .json, Dockerfile 等)。严禁请求读取二进制文件、大型数据文件或压缩包。如果你在本轮指定了要读取的文件，则`commands_to_execute`数组(如果提供)将被忽略，系统会先读取文件并将内容反馈给你，然后你再决定下一步。如果无需读取文件，则此键可省略或设置为空数组 `[]`。\"],你读取README全文一次后，接下来的三次操作内容不能再读取README。\n"
    "  \"commands_to_execute\": [ (对象数组, 可选) "
    "\n    // 每个对象代表一条独立的、按顺序执行的shell命令。"
    "\n    // 如果本轮指定了`files_to_read`，则此数组将被忽略，应设置为空数组 `[]` 或省略。"
    "\n    // 如果没有命令要执行（例如，等待文件读取结果，或配置已完成），则此键可省略或设置为空数组 `[]`。"
    "\n    { "
    "\n      \"command_line\": \"(字符串, 必需) 要执行的单行shell命令。每个逻辑操作应是数组中的一个独立命令对象，但是如果需要设置环境变量等必须一次执行多个命令的场景，可以使用 `;` 来连接，严禁使用 `&&` 连接多个逻辑命令。\",\n"
    "      \"description\": \"(字符串, 必需, 中文) 对该命令目的的简短中文描述。\"\n"
    "    }"
    "\n    // ... (更多命令对象) ... "
    "\n  ]\n"
    "}\n"  # 您提供的代码中这里缺少一个逗号，如果 files_to_write 在此之后
    "  \"files_to_write\": [ (对象数组, 可选) "  # 假设您已在提示中正确添加了此部分
    "\n    // 每个对象代表一个要写入或创建的文件。"
    "\n    // 如果本轮指定了`files_to_read`或`commands_to_execute`，此字段通常应为空或省略，除非你明确希望在执行命令或读取文件之前/之后写入文件（通常不推荐混合）。"
    "\n    // 如果没有文件要写入，则此键可省略或设置为空数组 `[]`。"
    "\n    {"
    "\n      \"path\": \"(字符串, 必需) 相对于<PROJECT_ROOT_PATH_PLACEHOLDER>的文件路径。例如 'src/config.json' 或 'requirements-dev.txt'。\",\n"
    "\n      \"content\": \"(字符串, 必需) 要写入文件的完整文本内容。\",\n"
    "\n      \"description\": \"(字符串, 可选, 中文) 对写入该文件目的的简短中文描述。\"\n"
    "\n    }"
    "\n    // ... (更多文件写入对象) ... "
    "\n  ],\n"  # 确保这里有逗号，如果后面还有字段
    "\n\n--- 命令生成指南 (Windows - 至关重要，必须严格遵守) ---"
    "\n1. **工作目录**: 所有需要访问项目文件的命令 (如 `pip install -r requirements.txt`, `python your_script.py`) 都将自动在上面“当前任务状态”中指定的 `<PROJECT_ROOT_PATH_PLACEHOLDER>` 下执行。你可以使用cd指令改变工作文件夹。"
    "\n2. **Conda环境**: "
    "\n   - **创建**: 必须使用 `conda create -n <ENV_NAME_PLACEHOLDER> python=<版本号> -y`。请务必使用上面“当前任务状态”中指定的 `<ENV_NAME_PLACEHOLDER>` 作为环境名称。"
    "\n   - **在环境中执行命令**: 必须严格使用 `conda run -n <ENV_NAME_PLACEHOLDER> <命令>` 的格式。同样，请务必使用 `<ENV_NAME_PLACEHOLDER>`。例如: `conda run -n <ENV_NAME_PLACEHOLDER> python -m pip install -r requirements.txt`。"
    "\n   - **禁止**: 绝对禁止生成 `conda activate` 或 `source activate` 命令。在 `conda run` 中也绝对禁止使用 `--cwd`。"
    "\n3. **Pip**: 在Conda环境中使用pip时，必须通过 `conda run -n <ENV_NAME_PLACEHOLDER> python -m pip ...` 调用。"
    "\n4. **通用命令**: 你可以使用 `dir`, `tree /F` (查看目录结构), `curl` (下载文件)。你还可以使用命令来修改文件内容，或者创建文件副本。"
    # 假设您已在此处添加了关于 files_to_write 的指南
    "\n\n--- 交互流程与输出示例 (你的输出应仅为花括号内的JSON内容) ---"
    "\n**示例1: 初始分析，LLM决定先读取文件 (当前任务状态: 目标环境名 'proj_env', 项目根目录 'C:\\cloned\\my_proj', README提取信息 '见下文')**"
    "\n{\n"
    "  \"thought_summary\": \"项目已克隆。根据从README中提取的关键信息，项目似乎需要特定版本的库，且依赖可能在 'requirements.txt'。我将请求读取 'requirements.txt'。如果提取的README信息不明确或不足，我会考虑请求读取完整的README文件。\",\n"
    "  \"files_to_read\": [\"requirements.txt\"],\n"
    "  \"commands_to_execute\": []\n"
    "}\n"
    "\n**(系统将读取文件，并将内容连同历史反馈给你。下一轮，“当前任务状态”中的项目根目录和环境名会保持，README提取信息也会持续提供。)**\n"
    "\n**示例2: LLM收到文件内容后，决定创建环境并执行命令 (当前任务状态: 目标环境名 'proj_env', 项目根目录 'C:\\cloned\\my_proj', README提取信息 '...')**"
    "\n{\n"
    "  \"thought_summary\": \"已收到 'requirements.txt' 内容。根据此信息和从README提取的关键信息，以及系统提示中指定的当前目标环境名称 'proj_env'，我将创建Conda环境并安装依赖。\",\n"
    "  \"files_to_read\": [],\n"
    "  \"commands_to_execute\": [\n"
    "    {\"command_line\": \"conda create -n proj_env python=3.10 -y\", \"description\": \"创建Conda环境 proj_env (Python 3.10)\"},\n"
    "    {\"command_line\": \"conda run -n proj_env python -m pip install -r requirements.txt\", \"description\": \"在proj_env中安装requirements.txt内的依赖\"}\n"
    "  ]\n"
    "}\n"
    "\n--- 请严格按照上述指南和JSON结构规范生成你的唯一JSON响应 ---"
)

# 新增：README提取专用系统提示
README_EXTRACTION_SYSTEM_PROMPT = (
    "你是一个专门负责从项目README文件中提取关键信息的AI助手。你的任务是仔细阅读给定的README全文，然后识别并提取与项目【安装】、【配置】、【依赖】和【基本使用/运行方法】相关的所有重要文本片段。"
    "\n你的输出**必须**是一个符合RFC 8259标准的、单一的、完整的JSON对象。严禁在此JSON对象前后包含任何额外的文本、解释、代码块标记(如```json)、注释或任何非JSON内容。"
    "\nJSON对象结构规范如下："
    "\n{"
    "\n  \"installation_instructions\": \"(字符串, 可选) 提取到的关于如何安装项目或其环境的明确步骤、命令或说明。如果存在多个步骤，请按順序列出，并用换行符分隔。\",\n"
    "\n  \"configuration_details\": \"(字符串, 可选) 提取到的关于项目运行前可能需要的配置信息，例如环境变量设置、配置文件修改、API密钥等。\",\n"
    "\n  \"dependencies\": \"(字符串, 可选) 提取到的关于项目依赖库、工具或特定版本要求的描述。这可能包括Python版本、pip依赖列表、conda环境文件引用等。\",\n"
    "\n  \"usage_examples\": \"(字符串, 可选) 提取到的关于如何运行项目、执行主要功能或查看示例的基本命令或代码片段。\",\n"
    "\n  \"other_relevant_info\": \"(字符串, 可选) 其他你认为对于自动化配置和初步理解项目至关重要的、但无法明确归入以上分类的信息。\",\n"
    "\n  \"extraction_summary\": \"(字符串, 必需) 对本次提取过程的简要总结，例如指明哪些部分的信息被找到了，哪些部分可能缺失或不明确。\"\n"
    "\n}"
    "\n请确保提取的内容尽可能完整且准确。如果某些部分在README中没有明确提及，则对应的JSON字段可以省略或其值设为null。"
    "\n如果README内容过短或信息不充分，请在 `extraction_summary` 中说明。"
    "\n请直接开始分析以下提供的README内容，并严格按照上述JSON格式输出你的提取结果。"
)

# 扩大上下文限制，同时注意性能和模型实际能力
MAX_TOTAL_PROMPT_CHARS_APPROX = 100000
MAX_CONVERSATION_HISTORY_CHARS = 80000
MAX_LLM_OUTPUT_TOKENS = 12000
MAX_LLM_RETRIES = 2
MAX_HISTORY_ITEMS = 70

# 新增：LLM提示总长度的硬性限制 (系统提示 + 用户输入部分)
MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT = 25000  # 保持不变，按用户要求

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_please_change_it_now_!@#$%^&*()_+'
socketio = SocketIO(app, async_mode='threading')

llm_client: Optional[llm.LLMClient] = None
project_file_cache: Dict[str, str] = {}
conversation_history: List[Dict[str, Any]] = []
initial_readme_summary_for_llm: Optional[str] = None  # 现在存储的是提取后的JSON字符串或错误信息


def initialize_llm_client(system_prompt_template: str, sid: Optional[str] = None) -> bool:
    global llm_client, project_file_cache, conversation_history, initial_readme_summary_for_llm
    project_file_cache = {}
    conversation_history = []
    initial_readme_summary_for_llm = None
    try:
        llm_client = llm.LLMClient(api_key=LLM_API_KEY, model_name=LLM_MODEL_NAME, base_url=LLM_BASE_URL,
                                   system_prompt=system_prompt_template,
                                   max_history_turns=0)  # 主LLM客户端历史由我们自己管理
        msg = f"LLM客户端已使用模型 {LLM_MODEL_NAME} 初始化。"
        print(msg if not sid else f"SID {sid}: {msg}")
        if sid: socketio.emit('status_update', {'message': msg, 'type': 'info'}, room=sid, namespace='/')
        return True
    except Exception as e:
        err_msg = f"LLM初始化错误: {e}"
        print(err_msg if not sid else f"SID {sid}: {err_msg}")
        if sid: socketio.emit('error_message', {'message': err_msg, 'type': 'error'}, room=sid, namespace='/')
        return False


initialize_llm_client(DEFAULT_SYSTEM_PROMPT_TEMPLATE)


# 新增函数：使用LLM提取README信息
def extract_readme_info_with_llm(sid: str, readme_full_content: str, readme_filename: str) -> str:
    global llm_client
    if not llm_client:
        socketio.emit('error_message', {'message': "LLM客户端未初始化，无法提取README信息。", 'type': 'error'}, room=sid,
                      namespace='/')
        return json.dumps(
            {"error": "LLM client not initialized.", "extraction_summary": "LLM客户端未初始化，无法提取信息。"})

    # 确保README内容不会超长到让提取LLM崩溃
    # 这个长度限制应该远小于主LLM的限制，因为提取任务本身不需要太长上下文
    max_readme_len_for_extraction = 30000  # 例如30k字符，大约7-8k token
    if len(readme_full_content) > max_readme_len_for_extraction:
        readme_content_to_extract = readme_full_content[:max_readme_len_for_extraction] + \
                                    "\n\n[注意：README内容过长，已截断末尾部分进行分析]"
        socketio.emit('status_update', {
            'message': f"README '{readme_filename}' 内容过长({len(readme_full_content)} chars)，已截断至 {max_readme_len_for_extraction} chars 进行提取分析。",
            'type': 'warning'}, room=sid, namespace='/')
    else:
        readme_content_to_extract = readme_full_content

    extraction_prompt = f"README 文件名: '{readme_filename}'\nREADME 内容全文如下:\n```markdown\n{readme_content_to_extract}\n```\n请提取关键信息。"

    socketio.emit('status_update',
                  {'message': f"开始使用LLM提取 '{readme_filename}' 中的关键配置信息...", 'type': 'info'}, room=sid,
                  namespace='/')

    original_system_prompt = llm_client.system_prompt_content
    llm_client.system_prompt_content = README_EXTRACTION_SYSTEM_PROMPT  # 临时切换系统提示

    accumulated_extraction_text = ""
    try:
        temp_conv_hist_for_extraction = []
        for event_type, content_chunk in llm_client.get_response_stream(
                extraction_prompt, max_tokens=MAX_LLM_OUTPUT_TOKENS // 2, temperature=0.0):
            if event_type == "delta_content" and content_chunk is not None:
                accumulated_extraction_text += content_chunk
            elif event_type == "error":
                raise Exception(f"LLM提取响应错误: {content_chunk}")
            elif event_type == "stream_end":
                break
        if not accumulated_extraction_text.strip():
            raise Exception("LLM提取返回为空。")
    except Exception as e:
        error_msg = f"使用LLM提取 '{readme_filename}' 信息时发生错误: {e}"
        socketio.emit('error_message', {'message': error_msg, 'type': 'error'}, room=sid, namespace='/')
        llm_client.system_prompt_content = original_system_prompt
        return json.dumps({"error": str(e), "extraction_summary": f"提取过程中发生错误: {e}"})
    finally:
        llm_client.system_prompt_content = original_system_prompt

    extracted_json_str = extract_json_from_llm_response(accumulated_extraction_text)
    if extracted_json_str:
        try:
            json.loads(extracted_json_str)
            socketio.emit('status_update', {'message': f"成功从 '{readme_filename}' 提取结构化信息。", 'type': 'info'},
                          room=sid, namespace='/')
            return extracted_json_str
        except json.JSONDecodeError:
            summary_msg = f"LLM声称提取了JSON，但解析失败。将原始内容作为文本摘要。"
            socketio.emit('status_update',
                          {'message': f"警告：LLM为 '{readme_filename}' 返回的JSON解析失败。将使用原始文本。",
                           'type': 'warning'}, room=sid, namespace='/')
            max_fallback_len = 20000  # min(MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT // 8, 20000)
            fallback_content = accumulated_extraction_text[:max_fallback_len]
            if len(accumulated_extraction_text) > max_fallback_len:
                fallback_content += "\n...(内容过长已截断)..."
            return json.dumps({
                "error": "Failed to parse extracted JSON from LLM.",
                "raw_extraction_output": fallback_content,
                "extraction_summary": summary_msg
            })
    else:
        summary_msg = f"未能从LLM的响应中提取有效的JSON结构化信息。可能LLM未按预期格式返回。"
        socketio.emit('status_update',
                      {'message': f"警告：未能从 '{readme_filename}' 的LLM响应中提取JSON。", 'type': 'warning'}, room=sid,
                      namespace='/')
        max_fallback_len = 20000  # min(MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT // 8, 20000)
        fallback_content = accumulated_extraction_text[:max_fallback_len]
        if len(accumulated_extraction_text) > max_fallback_len:
            fallback_content += "\n...(内容过长已截断)..."
        return json.dumps({
            "error": "No valid JSON structure extracted from LLM response.",
            "raw_extraction_output": fallback_content,
            "extraction_summary": summary_msg
        })


def build_llm_input_for_client(
        current_user_query_segment: str,
        project_root_for_system_prompt: str,
        env_name_for_system_prompt: str,
        readme_summary_for_system_prompt: Optional[str]
) -> Tuple[str, str]:
    global conversation_history

    HISTORY_HEADER_TEXT = "\n\n--- 对话历史回顾 (最近的交互在前，包含关键信息和你的先前决策，请仔细阅读以保持上下文连贯性) ---\n"
    CURRENT_QUERY_INTRO_TEXT = "\n--- 当前用户指令/反馈 (请基于此和上述历史及系统提示进行回应) ---\n"
    TRUNCATION_MESSAGE_OTHER = "\n(注意：为满足长度限制，部分较早的常规对话历史（如用户输入、命令结果等非LLM结构化输出部分）已被截断。LLM结构化输出历史已优先完整保留。)\n"
    TRUNCATION_MESSAGE_CRITICAL = "\n(警告：为满足长度限制，对话历史（包括部分较早的LLM结构化输出）已被截断。已优先保留最新的LLM结构化输出。)\n"
    NO_HISTORY_MESSAGE = "(当前无先前对话历史可供回顾)\n"

    final_system_prompt = DEFAULT_SYSTEM_PROMPT_TEMPLATE
    final_system_prompt = final_system_prompt.replace("<PROJECT_ROOT_PATH_PLACEHOLDER>",
                                                      project_root_for_system_prompt or "当前未设置或未知")
    final_system_prompt = final_system_prompt.replace("<ENV_NAME_PLACEHOLDER>",
                                                      env_name_for_system_prompt or "当前未设置或未知")

    readme_placeholder_content = "未提供或未成功提取README信息。"
    if readme_summary_for_system_prompt:
        try:
            parsed_readme_json = json.loads(readme_summary_for_system_prompt)
            readme_placeholder_content = f"```json\n{json.dumps(parsed_readme_json, indent=2, ensure_ascii=False)}\n```"
        except json.JSONDecodeError:
            max_len_fallback_readme = 20000  # min(MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT // 7, 20000)
            if len(readme_summary_for_system_prompt) > max_len_fallback_readme:
                readme_placeholder_content = readme_summary_for_system_prompt[
                                             :max_len_fallback_readme] + "\n...(README提取内容过长已截断)..."
            else:
                readme_placeholder_content = readme_summary_for_system_prompt

    final_system_prompt = final_system_prompt.replace("<README_CONTENT_PLACEHOLDER>", readme_placeholder_content)
    len_sys_prompt = len(final_system_prompt)

    current_query_block_str = CURRENT_QUERY_INTRO_TEXT + current_user_query_segment
    len_current_query_block = len(current_query_block_str)

    budget_for_history_accumulator = MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT - (len_sys_prompt + len_current_query_block)
    history_accumulator_str = ""

    if budget_for_history_accumulator < len(HISTORY_HEADER_TEXT) + len(NO_HISTORY_MESSAGE) + 50:
        print(
            f"[WARNING] SID {request.sid if request else 'N/A'}: 可用于历史记录的空间不足 (预算: {budget_for_history_accumulator} chars)。")
        history_accumulator_str = HISTORY_HEADER_TEXT + NO_HISTORY_MESSAGE
        if budget_for_history_accumulator < 0:
            print(
                f"[CRITICAL WARNING] SID {request.sid if request else 'N/A'}: 系统提示和当前查询已超限 ({len_sys_prompt + len_current_query_block} > {MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT}).")
            if len_sys_prompt + len(
                    CURRENT_QUERY_INTRO_TEXT) < MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT:  # 尝试截断current_user_query_segment
                max_len_current_query_segment = MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT - len_sys_prompt - len(
                    CURRENT_QUERY_INTRO_TEXT) - 10  # 10 for safety
                if max_len_current_query_segment > 0 and len(
                        current_user_query_segment) > max_len_current_query_segment:
                    current_user_query_segment = current_user_query_segment[
                                                 :max_len_current_query_segment] + "\n...(当前指令过长，已被截断)...\n"
                    current_query_block_str = CURRENT_QUERY_INTRO_TEXT + current_user_query_segment
            else:  # 连系统提示都放不下了，或者没有空间给当前指令
                current_query_block_str = CURRENT_QUERY_INTRO_TEXT + "\n...(当前指令过长，且无足够空间显示，已被严重截断)...\n"
            return final_system_prompt, current_query_block_str

    notice_lengths = [len(TRUNCATION_MESSAGE_OTHER), len(TRUNCATION_MESSAGE_CRITICAL), len(NO_HISTORY_MESSAGE)]
    budget_for_history_content_actual = budget_for_history_accumulator - len(HISTORY_HEADER_TEXT) - max(
        notice_lengths) - 20

    if budget_for_history_content_actual <= 0:
        history_accumulator_str = HISTORY_HEADER_TEXT + NO_HISTORY_MESSAGE
        print(f"[INFO] SID {request.sid if request else 'N/A'}: 预算不足以容纳任何历史内容。")
    else:
        llm_structured_outputs_formatted_recent_first = []
        other_history_items_formatted_recent_first = []
        max_cmd_output_snippet = 20000  # 保持不变

        for entry in reversed(conversation_history):
            entry_str = ""
            entry_type = entry.get("type")
            content = entry.get("content")
            env_name_hist = entry.get("env_name_at_time", env_name_for_system_prompt)

            if entry_type == "command_execution_result" and isinstance(content, dict):
                stdout_s = str(content.get('stdout', ''))
                stderr_s = str(content.get('stderr', ''))
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
                files_write_req = content.get('files_to_write', [])  # +++ Added for history +++
                entry_str = f"\n[你之前的JSON响应 (当时目标环境: '{env_name_hist}')]:\n  思考总结: {summary}\n"
                if files_req: entry_str += f"  请求读取文件: {files_req}\n"
                if files_write_req: entry_str += f"  请求写入文件: {[fw.get('path', 'N/A') for fw in files_write_req if isinstance(fw, dict)]}\n"  # +++ Added for history +++
                if cmds_req: entry_str += f"  请求执行命令: {[c.get('command_line', 'N/A') for c in cmds_req if isinstance(c, dict)]}\n"
            elif entry_type == "llm_raw_unparsable_output" and isinstance(content, str):
                raw_output_snippet = content[:2000]  # 保持不变
                if len(content) > 2000: raw_output_snippet += "\n...(原始输出过长已截断)...\n"
                entry_str = f"\n[你先前未解析的原始输出 (这通常表示格式错误)]:\n```text\n{raw_output_snippet}\n```\n"
            # +++ START OF NEW CODE for file_write_result history +++
            elif entry_type == "file_write_result" and isinstance(content, dict):
                filepath = content.get('filepath', 'N/A')
                success = content.get('success', False)
                message = content.get('message', '无消息')
                entry_str = (f"\n[上一个系统操作结果 - 文件写入]:\n"
                             f"  文件路径: `{filepath}`\n"
                             f"  操作状态: {'成功' if success else '失败'}\n"
                             f"  详细信息: {message}\n")
            # +++ END OF NEW CODE for file_write_result history +++

            if entry_str:
                if entry_type == "llm_structured_output":
                    llm_structured_outputs_formatted_recent_first.append(entry_str)
                else:
                    other_history_items_formatted_recent_first.append(entry_str)

        structured_history_parts_chronological = list(reversed(llm_structured_outputs_formatted_recent_first))
        other_history_parts_chronological = list(reversed(other_history_items_formatted_recent_first))

        final_history_content_parts = []
        current_history_content_len = 0
        structured_truncated = False
        other_truncated = False

        for part in structured_history_parts_chronological:
            if current_history_content_len + len(part) <= budget_for_history_content_actual:
                final_history_content_parts.append(part)
                current_history_content_len += len(part)
            else:
                structured_truncated = True;
                break

        if structured_truncated:
            other_truncated = True
        else:
            for part in other_history_parts_chronological:
                if current_history_content_len + len(part) <= budget_for_history_content_actual:
                    final_history_content_parts.append(part)
                    current_history_content_len += len(part)
                else:
                    other_truncated = True;
                    break

        history_content_str = "".join(final_history_content_parts)
        truncation_notice_to_display = ""
        if not history_content_str and not structured_history_parts_chronological and not other_history_parts_chronological:
            truncation_notice_to_display = NO_HISTORY_MESSAGE
        elif structured_truncated:
            truncation_notice_to_display = TRUNCATION_MESSAGE_CRITICAL
        elif other_truncated:
            truncation_notice_to_display = TRUNCATION_MESSAGE_OTHER

        history_accumulator_str = HISTORY_HEADER_TEXT + history_content_str + truncation_notice_to_display

    final_user_input_with_history = history_accumulator_str + current_query_block_str
    return final_system_prompt, final_user_input_with_history


def add_to_conversation_history(entry_type: str, content: Any, env_name_at_time: Optional[str] = None):
    global conversation_history
    entry: Dict[str, Any] = {"type": entry_type, "content": content, "timestamp": time.time()}
    if env_name_at_time and (entry_type == "user_input_to_llm" or entry_type == "llm_structured_output"):
        entry["env_name_at_time"] = env_name_at_time
    conversation_history.append(entry)
    if len(conversation_history) > MAX_HISTORY_ITEMS:
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
            if stream_type == 'stdout':
                full_stdout_str += content;
                socketio.emit('command_stream', {'type': 'stdout_chunk', 'chunk': content}, room=sid, namespace='/')
            elif stream_type == 'stderr':
                full_stderr_str += content;
                socketio.emit('command_stream', {'type': 'stderr_chunk', 'chunk': content}, room=sid, namespace='/')
            elif stream_type == 'return_code':
                final_return_code = int(content)
            socketio.sleep(0.015)
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


def extract_json_from_llm_response(raw_response: str) -> Optional[str]:
    if not raw_response:
        return None
    text_cleaned = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL | re.IGNORECASE).strip()
    text_cleaned = re.sub(r"<tool_code>.*?</tool_code>", "", text_cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    text_cleaned = re.sub(r"<tool_code>.*", "", text_cleaned, flags=re.DOTALL | re.IGNORECASE).strip()
    text_cleaned = re.sub(r"<function_calls>.*?</function_calls>", "", text_cleaned,
                          flags=re.DOTALL | re.IGNORECASE).strip()
    text_cleaned = re.sub(r"\[TOOL_CALLS\]?.*?\[/TOOL_CALLS\]?", "", text_cleaned,
                          flags=re.DOTALL | re.IGNORECASE).strip()
    text_cleaned = re.sub(r"<tool_calls>.*?</tool_calls>", "", text_cleaned, flags=re.DOTALL | re.IGNORECASE).strip()

    match_json_block = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text_cleaned, re.DOTALL)
    if match_json_block:
        return match_json_block.group(1).strip()

    first_brace = text_cleaned.find('{')
    last_brace = text_cleaned.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text_cleaned[first_brace: last_brace + 1]
        try:
            json.loads(candidate)
            return candidate.strip()
        except json.JSONDecodeError:
            pass

    if first_brace != -1:
        open_braces = 0
        json_start_index = -1
        for i in range(first_brace, len(text_cleaned)):
            char = text_cleaned[i]
            if char == '{':
                if open_braces == 0:
                    json_start_index = i
                open_braces += 1
            elif char == '}':
                open_braces -= 1
                if open_braces == 0 and json_start_index != -1:
                    return text_cleaned[json_start_index: i + 1].strip()
    print(f"DEBUG: extract_json_from_llm_response: No valid JSON found. Preview: {text_cleaned[:500]}")
    return None


def read_project_files(sid: str, project_root: str, relative_paths: List[str]) -> Dict[str, str]:
    global project_file_cache
    contents: Dict[str, str] = {};

    max_file_size_chars = 20000  # min(MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT // 10, 20000)  # 保持不变
    total_chars_limit_this_call = 20000  # min(MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT // 5, 12000)  # 保持不变
    current_read_chars_this_call = 0

    for rel_path_raw in relative_paths:
        rel_path = rel_path_raw.strip()#.replace("`", "").replace("'", "").replace("\"", "")
        if not rel_path: continue
        if ".." in rel_path or os.path.isabs(rel_path):
            msg = f"文件读取错误：不允许的路径格式 '{rel_path_raw}'。"
            socketio.emit('status_update', {'message': msg, 'type': 'error'}, room=sid, namespace='/')
            contents[rel_path_raw] = "[错误：不允许的路径格式]"
            project_file_cache[rel_path] = contents[rel_path_raw]
            continue

        abs_path = os.path.normpath(os.path.join(project_root, rel_path))
        norm_abs_path = os.path.normcase(abs_path)
        norm_project_root = os.path.normcase(os.path.abspath(project_root))

        if not (norm_abs_path.startswith(norm_project_root + os.sep) or norm_abs_path == norm_project_root):
            msg = f"文件读取错误：路径 '{rel_path_raw}' (解析为 '{abs_path}') 超出项目范围 ('{project_root}')。"
            socketio.emit('status_update', {'message': msg, 'type': 'error'}, room=sid, namespace='/')
            contents[rel_path_raw] = "[错误：路径超出项目范围]"
            project_file_cache[rel_path] = contents[rel_path_raw]
            continue

        if rel_path in project_file_cache and project_file_cache[rel_path].startswith("[错误："):
            pass
        elif rel_path in project_file_cache:
            cached_content = project_file_cache[rel_path]
            if 1:  # current_read_chars_this_call + len(cached_content) <= total_chars_limit_this_call: # 根据您的要求，注释掉长度限制
                contents[rel_path_raw] = cached_content
                current_read_chars_this_call += len(cached_content)
                socketio.emit('status_update',
                              {'message': f"从缓存中获取文件 '{rel_path_raw}' ({len(cached_content)} chars)。",
                               'type': 'info'}, room=sid, namespace='/')
                continue
            # else: # 这部分逻辑现在不会执行
            #     msg = f"从缓存读取文件 '{rel_path_raw}' 将超出本次调用总字符数限制，已跳过。"
            #     socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
            #     contents[rel_path_raw] = "[错误：超出本次文件读取总字符数限制 (来自缓存)，未加载]"
            #     continue
        try:
            if os.path.exists(abs_path) and os.path.isfile(abs_path):
                file_size_bytes = os.path.getsize(abs_path)
                # 根据您的要求，注释掉初步大小判断，但保留一个非常大的安全上限防止内存问题
                # if file_size_bytes > max_file_size_chars * 4:
                #     msg = f"文件 '{rel_path_raw}' 初步判断过大 ({file_size_bytes}字节)，已跳过。"
                #     socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
                #     contents[rel_path_raw] = f"[错误：文件过大 ({file_size_bytes}B)，已跳过读取]"
                #     project_file_cache[rel_path] = contents[rel_path_raw]
                #     continue
                very_large_file_safety_limit_chars = 500000  # 安全上限
                with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read(very_large_file_safety_limit_chars + 1)  # 读取，带安全检查

                if len(content) > very_large_file_safety_limit_chars:  # 如果文件真的超级大
                    content = content[:very_large_file_safety_limit_chars]
                    msg = f"文件 '{rel_path_raw}' 内容极大 (超过 {very_large_file_safety_limit_chars} 字符)，已截断以保护内存。将尝试送入LLM。"
                    socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')

                # 根据您的要求，注释掉这里的 max_file_size_chars 和 total_chars_limit_this_call 截断逻辑
                # if len(content) > max_file_size_chars:
                #     content = content[:max_file_size_chars]
                #     msg = f"文件 '{rel_path_raw}' 内容过大 (超过 {max_file_size_chars} 字符)，已截断。"
                #     socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')

                # if current_read_chars_this_call + len(content) > total_chars_limit_this_call:
                #     remaining_budget_for_file = total_chars_limit_this_call - current_read_chars_this_call
                #     if remaining_budget_for_file > 0:
                #         content = content[:remaining_budget_for_file]
                #         msg = f"读取文件 '{rel_path_raw}' 内容后超出总字符数限制，已截断以适应剩余空间。"
                #         socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
                #     else:
                #         msg = f"读取文件 '{rel_path_raw}' 因超出总字符数限制，未加载。"
                #         socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
                #         contents[rel_path_raw] = "[错误：超出本次文件读取总字符数限制，未加载]"
                #         break

                if content and (rel_path_raw not in contents or not contents[rel_path_raw].startswith("[错误：")):
                    contents[rel_path_raw] = content
                    project_file_cache[rel_path] = content
                    current_read_chars_this_call += len(content)  # 仍然追踪，但不再基于它做截断
                    socketio.emit('status_update',
                                  {'message': f"已读取文件 '{rel_path_raw}' (大小: {len(content)}字符)。",  # 不再提示“可能已截断”
                                   'type': 'info'}, room=sid, namespace='/')
                elif not (rel_path_raw in contents):
                    contents[rel_path_raw] = "[错误：文件内容为空或读取问题]"  # 修改了这里的错误信息
            else:
                msg = f"LLM请求的文件 '{rel_path_raw}' (解析为 '{abs_path}') 不存在或不是一个文件。"
                socketio.emit('status_update', {'message': msg, 'type': 'warning'}, room=sid, namespace='/')
                contents[rel_path_raw] = "[错误：文件不存在或不是文件]"
                project_file_cache[rel_path] = contents[rel_path_raw]
        except Exception as e:
            msg = f"读取文件 '{rel_path_raw}' 时发生错误: {e}"
            socketio.emit('status_update', {'message': msg, 'type': 'error'}, room=sid, namespace='/')
            contents[rel_path_raw] = f"[读取文件时发生错误: {e}]"
            project_file_cache[rel_path] = contents[rel_path_raw]
    return contents


@socketio.on('connect')
def handle_connect():
    sid = request.sid
    print(f'客户端连接: {sid}')
    socketio.emit('status_update', {'message': '后端已连接。请提供Git仓库URL开始配置。', 'type': 'info'}, room=sid,
                  namespace='/')
    if not initialize_llm_client(DEFAULT_SYSTEM_PROMPT_TEMPLATE, sid=sid):
        socketio.emit('error_message', {'message': 'LLM客户端自动初始化失败。', 'type': 'error'}, room=sid,
                      namespace='/')
    socketio.emit('system_prompt_update', {'system_prompt': DEFAULT_SYSTEM_PROMPT_TEMPLATE}, room=sid, namespace='/')


@socketio.on('disconnect')
def handle_disconnect():
    print(f'客户端断开: {request.sid}')


@socketio.on('update_system_prompt')
def handle_update_system_prompt(data: Dict[str, str]):
    sid = request.sid
    global DEFAULT_SYSTEM_PROMPT_TEMPLATE
    new_prompt_template = data.get('system_prompt', DEFAULT_SYSTEM_PROMPT_TEMPLATE).strip()
    if not new_prompt_template:
        socketio.emit('error_message', {'message': "系统提示词不能为空。", 'type': 'error'}, room=sid, namespace='/')
        return
    DEFAULT_SYSTEM_PROMPT_TEMPLATE = new_prompt_template
    if initialize_llm_client(DEFAULT_SYSTEM_PROMPT_TEMPLATE, sid=sid):
        socketio.emit('status_update', {'message': f"系统提示词模板已更新。对话历史已重置。", 'type': 'success'},
                      room=sid, namespace='/')
        socketio.emit('system_prompt_update', {'system_prompt': DEFAULT_SYSTEM_PROMPT_TEMPLATE}, room=sid,
                      namespace='/')
    else:
        socketio.emit('error_message', {'message': "更新提示词失败。", 'type': 'error'}, room=sid, namespace='/')


def process_setup_step(sid: str, step_data: Dict[str, Any], retry_count: int = 0):
    with app.app_context():
        global initial_readme_summary_for_llm, project_file_cache

        git_url = step_data.get('git_url')
        if not git_url:
            socketio.emit('error_message', {'message': "内部错误：git_url缺失。", 'type': 'error'}, room=sid,
                          namespace='/');
            return

        env_name_input = step_data.get('env_name', '').strip()
        project_name_from_url = git_url.split('/')[-1].replace('.git', '') if git_url else "unknown_project"

        env_name = step_data.get('determined_env_name')
        if not env_name:
            raw_env_name_base = env_name_input if env_name_input else project_name_from_url.lower()
            safe_env_name_base = re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_env_name_base)
            env_name = safe_env_name_base if env_name_input and safe_env_name_base else safe_env_name_base + "_env"
            if not env_name or env_name == "_env": env_name = "default_project_env"
            step_data['determined_env_name'] = env_name

        project_name_for_dir = re.sub(r'[^a-zA-Z0-9_-]', '_', project_name_from_url)
        if not project_name_for_dir: project_name_for_dir = "cloned_project_default_name"

        project_cloned_root_path = step_data.get('project_cloned_root_path')
        initial_readme_name = step_data.get('initial_readme_name')

        current_readme_summary = step_data.get('readme_summary_for_llm', initial_readme_summary_for_llm)
        current_user_query_segment = ""

        if step_data.get('step_type') == 'initial_analysis':
            clone_base_dir = os.path.join(os.getcwd(), "cloned_projects_area")
            try:
                os.makedirs(clone_base_dir, exist_ok=True)
            except OSError as e:
                socketio.emit('error_message', {'message': f"创建克隆目录失败: {e}", 'type': 'error'}, room=sid,
                              namespace='/');
                return

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
                                  namespace='/');
                    return

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

            dir_listing_content = "无法获取项目根目录的列表。"
            dir_command_to_execute_list = ["cmd", "/c", "dir"]
            dir_command_display_for_log = subprocess.list2cmdline(dir_command_to_execute_list)
            socketio.emit('status_update', {
                'message': f"正在获取项目根目录 '{project_cloned_root_path}' 的文件列表 (使用 '{dir_command_display_for_log}' 命令)...",
                'type': 'info'}, room=sid, namespace='/')
            dir_execution_result = stream_command_output(sid, dir_command_to_execute_list,
                                                         working_dir=project_cloned_root_path)
            if dir_execution_result.get('return_code', -1) == 0:
                raw_dir_stdout = dir_execution_result.get('stdout', "").strip()
                max_len_for_dir_output_in_query = 6000  # min(MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT // 5, 6000)
                if len(raw_dir_stdout) > max_len_for_dir_output_in_query:
                    dir_listing_content = raw_dir_stdout[
                                          :max_len_for_dir_output_in_query] + f"\n... (目录列表过长，此处显示前 {max_len_for_dir_output_in_query} 字符)"
                else:
                    dir_listing_content = raw_dir_stdout
                if not dir_listing_content: dir_listing_content = "(目录列表为空)"
                socketio.emit('status_update', {'message': f"成功获取项目根目录文件列表。", 'type': 'info'}, room=sid,
                              namespace='/')
            else:
                error_detail = dir_execution_result.get('stderr', "").strip()
                base_error_message = f"获取项目根目录列表失败 (命令: '{subprocess.list2cmdline(dir_command_to_execute_list)}', RC: {dir_execution_result.get('return_code')})."
                dir_listing_content = f"{base_error_message}\n错误详情: {error_detail or dir_execution_result.get('stdout', '').strip() or '(无额外输出)'}"
                socketio.emit('status_update', {'message': f"获取项目根目录文件列表失败.", 'type': 'warning'}, room=sid,
                              namespace='/')

            readme_extracted_info_json_str = json.dumps({"error": "README not found or read error.",
                                                         "extraction_summary": "未找到README文件或读取时发生错误。"})
            full_readme_content_from_file = ""
            readme_filename_found = None
            possible_readme_files = ["README.md", "readme.md", "README.rst", "README.txt", "README", "ReadMe.md"]
            for name in possible_readme_files:
                p = os.path.join(project_cloned_root_path, name)
                if os.path.isfile(p):
                    try:
                        with open(p, 'r', encoding='utf-8', errors='replace') as f:
                            full_readme_content_from_file = f.read()
                        project_file_cache[name] = full_readme_content_from_file
                        readme_filename_found = name
                        step_data['initial_readme_name'] = name
                        socketio.emit('status_update', {
                            'message': f"README文件 '{name}' 已找到并读取全文 ({len(full_readme_content_from_file)} chars)。",
                            'type': 'info'}, room=sid, namespace='/')
                        break
                    except Exception as e:
                        read_error_msg = f"读取README文件 '{name}' 时发生错误: {e}"
                        socketio.emit('error_message', {'message': read_error_msg, 'type': 'error'}, room=sid,
                                      namespace='/')
                        readme_extracted_info_json_str = json.dumps(
                            {"error": read_error_msg, "extraction_summary": f"读取 {name} 出错。"})
                        break
            if readme_filename_found and full_readme_content_from_file:
                readme_extracted_info_json_str = extract_readme_info_with_llm(sid, full_readme_content_from_file,
                                                                              readme_filename_found)
            elif not readme_filename_found:
                socketio.emit('status_update', {'message': "未在项目中找到常见的README文件名。", 'type': 'warning'},
                              room=sid, namespace='/')

            initial_readme_summary_for_llm = readme_extracted_info_json_str
            current_readme_summary = readme_extracted_info_json_str
            step_data['readme_summary_for_llm'] = readme_extracted_info_json_str

            current_user_query_segment = (
                f"任务：为新克隆的Git仓库 '{git_url}' (项目名: {project_name_for_dir}) 进行Conda环境配置。\n"
                f"当前系统提示中已包含目标Conda环境名称 '{env_name}'、项目根目录 '{project_cloned_root_path}' 以及下方从README文件 ('{readme_filename_found or '未找到/读取失败'}') 中提取的关键信息。请基于这些信息进行分析。\n\n"
                f"以下是当前项目根目录 (`{project_cloned_root_path}`) 下的目录列表:\n"
                f"```text\n{dir_listing_content}\n```\n\n"
                f"从README ('{readme_filename_found or '未找到/提取失败'}') 提取的关键配置信息如下 (JSON格式):\n"
                f"{current_readme_summary}\n\n"
                f"请进行初步分析。如果上述提取的README信息不明确、不完整或有疑问，你可以通过在 `files_to_read` 中指定 '{readme_filename_found}' 来请求读取原始README文件全文。"
                f"如果需要查看项目中的其他文件以获取更详细的配置信息，请在 `files_to_read` 中列出它们的相对路径。"
                f"如果你认为已有足够信息，请在 `commands_to_execute` 字段中提供操作命令。"
            )
            add_to_conversation_history("user_input_to_llm", {
                "context_summary": f"初始分析请求：项目 {project_name_for_dir}, 目录列表和从README({readme_filename_found or 'N/A'})提取的结构化信息已提供."},
                                        env_name_at_time=env_name)

        elif step_data.get('step_type') == 'llm_output_retry':
            current_user_query_segment = (
                f"\n[系统重要提示]: 你上一次的输出未能解析为预期的JSON格式，或缺少必要的指令/文件请求，或生成的命令不符合规范。本次是第 {retry_count + 1} 次尝试。\n"
                f"请严格检查并遵循系统提示中关于JSON结构、命令格式的所有规范。\n"
                f"回顾完整的对话历史和系统提示中提供的当前任务状态 (项目根目录 '{project_cloned_root_path}', 目标环境 '{env_name}', README提取信息), 然后重新生成包含有效行动指令的JSON对象。"
            )
        else:
            prev_res = step_data.get('previous_command_result', {})
            files_read = step_data.get('files_just_read_content', {})
            feedback_parts = []
            if files_read:
                feedback_parts.append("\n--- 系统已读取你请求的文件，内容如下 ---")  # 移除了“（或摘要）”
                max_len_single_file_feedback = int(MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT * 0.8)  # 大幅放宽，优先文件内容
                total_len_files_feedback = 0
                # max_total_len_files_feedback 现在不严格限制，因为最终由 build_llm_input_for_client 控制
                # 但我们仍然可以有一个非常大的上限，以防止极端情况
                very_large_total_files_feedback_limit = MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT * 2  # 允许超过单个提示，因为 build_llm_input 会处理

                for path, content_val in files_read.items():
                    display_content = str(content_val)  # 假设 content_val 已经是读取并经过初步安全截断的

                    current_file_header = f"文件 '{path}':\n```text\n"
                    current_file_footer = "\n```\n"

                    # 为 current_user_query_segment 中的预览做一个适度的截断，但目标是尽量完整
                    # 真正的完整性由 build_llm_input_for_client 保证
                    preview_len_this_file = len(display_content)
                    suffix_if_truncated = ""
                    if len(current_file_header) + preview_len_this_file + len(
                            current_file_footer) + total_len_files_feedback > very_large_total_files_feedback_limit:
                        available_space = very_large_total_files_feedback_limit - total_len_files_feedback - len(
                            current_file_header) - len(current_file_footer) - 50  # 50 for suffix
                        if available_space > 100:  # 至少能显示一点
                            preview_len_this_file = available_space
                            suffix_if_truncated = "\n...(内容较长，此处为部分预览，将尝试完整送入LLM)..."
                        else:  # 连预览都放不下了
                            feedback_parts.append(
                                f"\n...(文件 '{path}' 内容过长，本地预览空间不足，将尝试完整送入LLM)...\n")
                            continue  # 跳过这个文件的预览

                    display_content_for_feedback = display_content[:preview_len_this_file] + suffix_if_truncated

                    feedback_parts.append(current_file_header + display_content_for_feedback + current_file_footer)
                    total_len_files_feedback += len(current_file_header) + len(display_content_for_feedback) + len(
                        current_file_footer)
                    if total_len_files_feedback >= very_large_total_files_feedback_limit:
                        feedback_parts.append(
                            f"\n...(更多文件读取内容因本地显示长度限制未在此处完全展示，将尝试送入LLM)...\n");
                        break

            # +++ START OF MODIFICATION for file_write_result feedback +++
            if prev_res and prev_res.get("operation_type") == "file_writes":
                feedback_parts.append(f"\n--- 上一步文件写入操作反馈 ---")
                summary_msg = prev_res.get("message", "文件写入操作已完成。")
                feedback_parts.append(summary_msg)
                results_list = prev_res.get("results_summary", [])
                if results_list:
                    for idx, res_item in enumerate(results_list):
                        if isinstance(res_item, dict):
                            fb_path = res_item.get('filepath', f'条目{idx + 1}')
                            fb_succ = res_item.get('success', False)
                            fb_msg = res_item.get('message', '无详情')
                            feedback_parts.append(f"  - 文件 '{fb_path}': {'成功' if fb_succ else '失败'} - {fb_msg}")
                if not prev_res.get("all_successful", True):
                    feedback_parts.append("注意: 部分或全部文件写入操作失败。请分析上述详情，并决定下一步。")
            elif prev_res and prev_res.get("command_executed"):  # 原有的命令执行反馈
                # +++ END OF MODIFICATION for file_write_result feedback +++
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
                    f"请基于以上最新反馈、完整的对话历史以及系统提示中提供的“当前任务状态”（包括项目根目录 '{project_cloned_root_path}', 目标Conda环境名称 '{env_name}', 以及从README提取的信息），继续进行配置。\n"
                    f"如果先前提取的README信息不足或有疑问，你可以通过在 `files_to_read` 中指定 '{initial_readme_name or 'README.md'}' 来请求读取原始README文件全文。"
                    f"你需要读取更多其他文件吗？或者现在可以生成命令了？请给出你的JSON响应。"
            )
            add_to_conversation_history("user_input_to_llm",
                                        {"context_summary": "提供了命令执行结果和/或文件读取内容以供进一步决策."},
                                        env_name_at_time=env_name)

        if not llm_client:
            socketio.emit('error_message', {'message': "LLM客户端未初始化。", 'type': 'error'}, room=sid, namespace='/');
            return

        final_system_prompt, full_user_input_with_history = build_llm_input_for_client(
            current_user_query_segment,
            project_cloned_root_path or "尚未确定",
            env_name,
            current_readme_summary
        )
        llm_client.system_prompt_content = final_system_prompt

        display_sys_prompt_len = len(final_system_prompt)
        display_user_input_len = len(full_user_input_with_history)
        socketio.emit('llm_prompt_sent', {
            'prompt_head': final_system_prompt[:1000] + (
                f"... (SysPrompt Total: {display_sys_prompt_len} chars)" if display_sys_prompt_len > 1000 else ""),
            'prompt_tail': (
                               f"... (UserInput Start, Total: {display_user_input_len} chars) ..." if display_user_input_len > 2000 else "") + full_user_input_with_history[
                                                                                                                                               -1000:]
        }, room=sid, namespace='/')
        print(
            f"SID {sid}: Sending prompt to LLM. System prompt length: {display_sys_prompt_len}, User input length: {display_user_input_len}, Total: {display_sys_prompt_len + display_user_input_len}")

        socketio.emit('status_update', {'message': "请求LLM分析及指令...", 'type': 'info'}, room=sid, namespace='/')
        accumulated_llm_text = ""
        socketio.emit('llm_stream_clear', {}, room=sid, namespace='/')
        try:
            for event_type, content_chunk_val in llm_client.get_response_stream(
                    full_user_input_with_history, max_tokens=MAX_LLM_OUTPUT_TOKENS, temperature=0.1):
                if event_type == "delta_content" and content_chunk_val is not None:
                    accumulated_llm_text += content_chunk_val
                    socketio.emit('llm_general_stream', {'token': content_chunk_val}, room=sid, namespace='/');
                    socketio.sleep(0.005)
                elif event_type == "error":
                    socketio.emit('error_message',
                                  {'message': f"LLM流式响应错误: {content_chunk_val}", 'type': 'error'}, room=sid,
                                  namespace='/');
                    break
                elif event_type == "stream_end":
                    socketio.emit('status_update', {'message': "LLM流式响应接收完毕。", 'type': 'info'}, room=sid,
                                  namespace='/');
                    break
        except Exception as e:
            socketio.emit('error_message', {'message': f"LLM get_response_stream调用错误: {e}", 'type': 'error'},
                          room=sid, namespace='/');
            return

        if not accumulated_llm_text.strip():
            socketio.emit('status_update', {'message': "LLM响应为空。", 'type': 'warning'}, room=sid, namespace='/')

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

        # +++ START OF ADDED CODE for files_to_write check +++
        has_files_to_write_key = "files_to_write" in (json_object_parsed or {})
        files_to_write_list = json_object_parsed.get("files_to_write", []) if json_object_parsed else []
        has_valid_files_to_write = has_files_to_write_key and isinstance(files_to_write_list, list) and len(
            files_to_write_list) > 0
        # +++ END OF ADDED CODE for files_to_write check +++

        has_thought = bool(json_object_parsed and json_object_parsed.get("thought_summary"))
        is_thought_only_valid = has_thought and not has_valid_cmds and not has_valid_files and not has_valid_files_to_write  # +++ Modified +++

        valid_json_structure = not json_decode_error_occurred and json_object_parsed is not None and \
                               (
                                           has_valid_cmds or has_valid_files or is_thought_only_valid or has_valid_files_to_write)  # +++ Modified +++

        if not valid_json_structure:
            if retry_count < MAX_LLM_RETRIES:
                next_step_data = step_data.copy();
                next_step_data['step_type'] = 'llm_output_retry'
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

        files_to_read_now = [f for f in files_list if isinstance(f, str) and f.strip()]

        # +++ START OF NEW CODE for files_to_write processing +++
        actual_files_to_write_requests: List[Dict[str, str]] = []
        if has_valid_files_to_write:
            for file_write_obj in files_to_write_list:
                if isinstance(file_write_obj, dict) and \
                        isinstance(file_write_obj.get("path"), str) and file_write_obj["path"].strip() and \
                        isinstance(file_write_obj.get("content"), str):
                    actual_files_to_write_requests.append({
                        "path": file_write_obj["path"].strip(),
                        "content": file_write_obj["content"],
                        "description": file_write_obj.get("description", "无描述")
                    })
                else:
                    socketio.emit('status_update',
                                  {'message': f"警告：跳过格式不正确的 'files_to_write' 对象: {file_write_obj}",
                                   'type': 'warning'}, room=sid,
                                  namespace='/')
        # +++ END OF NEW CODE for files_to_write processing +++

        actual_commands_to_run: List[Tuple[str, str]] = []
        for cmd_obj in cmds_list:
            if isinstance(cmd_obj, dict) and isinstance(cmd_obj.get("command_line"), str) and cmd_obj[
                "command_line"].strip():
                original_cmd = cmd_obj["command_line"]
                cleaned_cmd = re.sub(r'\s*--cwd\s+([\'\"]?).*?\1\s*', ' ', original_cmd, flags=re.IGNORECASE).strip()
                cleaned_cmd = re.sub(r'\s*--working-directory\s+([\'\"]?).*?\1\s*', ' ', cleaned_cmd,
                                     flags=re.IGNORECASE).strip()
                if original_cmd != cleaned_cmd: socketio.emit('status_update',
                                                              {'message': f"警告：LLM命令含--cwd，已移除...",
                                                               'type': 'warning'}, room=sid, namespace='/')
                if cleaned_cmd: actual_commands_to_run.append((cleaned_cmd, cmd_obj.get("description", "无描述")))
            else:
                socketio.emit('status_update',
                              {'message': f"警告：跳过格式不正确的命令对象: {cmd_obj}", 'type': 'warning'}, room=sid,
                              namespace='/')

        next_step_data_base = {'git_url': git_url, 'determined_env_name': env_name,
                               'initial_readme_name': initial_readme_name,
                               'project_cloned_root_path': project_cloned_root_path,
                               'readme_summary_for_llm': current_readme_summary}

        # --- 行动执行顺序和暂存逻辑 ---
        pending_files_to_write_next = step_data.get('pending_files_to_write', [])
        pending_commands_to_execute_next = step_data.get('pending_commands_to_execute', [])

        if files_to_read_now:
            socketio.emit('status_update',
                          {'message': f"LLM请求读取文件: {', '.join(files_to_read_now)}。", 'type': 'info'}, room=sid,
                          namespace='/')
            read_files_content = read_project_files(sid, project_cloned_root_path or "",
                                                    files_to_read_now) if project_cloned_root_path else {
                f: "[错误: 项目根路径未确定]" for f in files_to_read_now}
            if not project_cloned_root_path: socketio.emit('error_message', {'message': f"项目根路径无效，无法读取文件。",
                                                                             'type': 'error'}, room=sid, namespace='/')
            next_step_data = next_step_data_base.copy();
            next_step_data['step_type'] = 'feedback_after_read'
            next_step_data['files_just_read_content'] = read_files_content
            next_step_data['previous_command_result'] = step_data.get('previous_command_result',
                                                                      {});  # Carry over if any
            next_step_data[
                'pending_files_to_write'] = actual_files_to_write_requests or pending_files_to_write_next  # Prioritize new requests
            next_step_data['pending_commands_to_execute'] = actual_commands_to_run or pending_commands_to_execute_next
            process_setup_step(sid, next_step_data, 0);
            return

        # 如果没有读取请求，处理暂存或新的写入请求
        current_files_to_write_action = actual_files_to_write_requests or pending_files_to_write_next
        if current_files_to_write_action:
            socketio.emit('status_update',
                          {'message': f"LLM请求写入 {len(current_files_to_write_action)} 个文件...", 'type': 'info'},
                          room=sid, namespace='/')
            all_writes_ok = True
            last_write_results_summary = []
            if not project_cloned_root_path:
                socketio.emit('error_message', {'message': f"项目根路径无效，无法写入文件。", 'type': 'error'}, room=sid,
                              namespace='/')
                all_writes_ok = False
            else:
                for i, req in enumerate(current_files_to_write_action):
                    path = req["path"];
                    content = req["content"];
                    desc = req["description"]
                    socketio.emit('status_update', {
                        'message': f"写入文件 ({i + 1}/{len(current_files_to_write_action)}): '{path}' ({desc})",
                        'type': 'info'}, room=sid, namespace='/')
                    write_result = executor.write_file_content(filepath_relative=path, content_to_write=content,
                                                               working_directory=project_cloned_root_path)
                    add_to_conversation_history("file_write_result", write_result, env_name_at_time=env_name)
                    last_write_results_summary.append(write_result)
                    if not write_result.get("success"):
                        all_writes_ok = False
                        socketio.emit('error_message',
                                      {'message': f"写入文件 '{path}' 失败: {write_result.get('message', '未知错误')}",
                                       'type': 'error'}, room=sid, namespace='/')

            next_step_data = next_step_data_base.copy()
            next_step_data['step_type'] = 'feedback'
            next_step_data['previous_command_result'] = {
                "operation_type": "file_writes", "all_successful": all_writes_ok,
                "results_summary": last_write_results_summary,
                "message": "文件写入操作已执行。" if all_writes_ok else "部分或全部文件写入操作失败。"
            }
            next_step_data['pending_commands_to_execute'] = actual_commands_to_run or pending_commands_to_execute_next
            next_step_data['pending_files_to_write'] = []  # Clear processed writes
            process_setup_step(sid, next_step_data, 0)
            return

        # 如果没有读取和写入请求，处理暂存或新的命令执行请求
        current_commands_to_run_action = actual_commands_to_run or pending_commands_to_execute_next
        if current_commands_to_run_action:
            last_cmd_res = {};
            all_ok = True
            for i, (cmd_str, desc) in enumerate(current_commands_to_run_action):
                socketio.emit('status_update',
                              {'message': f"执行 ({i + 1}/{len(current_commands_to_run_action)}): {cmd_str} ({desc})",
                               'type': 'info'}, room=sid, namespace='/')
                cmd_cwd = None
                if not ("conda create" in cmd_str.lower() or "conda env create" in cmd_str.lower()):
                    if project_cloned_root_path and os.path.isdir(project_cloned_root_path):
                        cmd_cwd = project_cloned_root_path
                    else:
                        socketio.emit('status_update',
                                      {'message': f"警告: 项目路径无效，命令将在默认目录执行。", 'type': 'warning'},
                                      room=sid, namespace='/')
                last_cmd_res = stream_command_output(sid, cmd_str, working_dir=cmd_cwd)
                add_to_conversation_history("command_execution_result", last_cmd_res, env_name_at_time=env_name)
                if last_cmd_res.get('return_code', -1) != 0:
                    socketio.emit('error_message',
                                  {'message': f"命令 '{cmd_str}' 执行失败 (RC: {last_cmd_res.get('return_code')})。",
                                   'type': 'error'}, room=sid, namespace='/');
                    all_ok = False;
                    break

            next_step_data = next_step_data_base.copy()
            next_step_data['step_type'] = 'feedback'
            next_step_data['previous_command_result'] = last_cmd_res
            socketio.emit('status_update', {'message': "当前批次LLM指令执行完毕。" if all_ok else "批次指令因错误中断。",
                                            'type': 'success' if all_ok else 'warning'}, room=sid, namespace='/')
            next_step_data['pending_commands_to_execute'] = []  # Clear processed commands
            next_step_data['pending_files_to_write'] = []  # Should be empty already
            process_setup_step(sid, next_step_data, 0)
            return

        # 如果所有队列都空了
        socketio.emit('status_update', {'message': "LLM指示配置完成或无更多行动指令。", 'type': 'success'}, room=sid,
                      namespace='/')
        socketio.emit('setup_complete', {'env_name': env_name, 'project_path': project_cloned_root_path}, room=sid,
                      namespace='/')


@socketio.on('get_llm_config')
def handle_get_llm_config():
    sid = request.sid
    config_to_send = {
        'base_url': LLM_BASE_URL, 'model_name': LLM_MODEL_NAME,
        'api_key_present': bool(LLM_API_KEY and LLM_API_KEY.lower() != "none" and LLM_API_KEY.lower() != "lmstudio")
    }
    socketio.emit('current_llm_config', {'config': config_to_send}, room=sid, namespace='/')


@socketio.on('update_llm_config')
def handle_update_llm_config(data: Dict[str, str]):
    sid = request.sid
    global LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME;
    updated_fields = []
    new_base_url = data.get('base_url', '').strip();
    new_api_key = data.get('api_key', '')
    new_model_name = data.get('model_name', '').strip()

    if new_base_url and new_base_url != LLM_BASE_URL: LLM_BASE_URL = new_base_url; updated_fields.append("Base URL")
    if new_api_key != LLM_API_KEY:
        LLM_API_KEY = new_api_key if new_api_key else os.environ.get("LMSTUDIO_API_KEY", "lmstudio")
        updated_fields.append("API Key")
    if new_model_name and new_model_name != LLM_MODEL_NAME:
        LLM_MODEL_NAME = new_model_name;
        updated_fields.append("Model Name")
    elif not new_model_name and LLM_MODEL_NAME != os.environ.get("LMSTUDIO_MODEL",
                                                                 "nikolaykozloff/deepseek-r1-0528-qwen3-8b"):
        LLM_MODEL_NAME = os.environ.get("LMSTUDIO_MODEL", "nikolaykozloff/deepseek-r1-0528-qwen3-8b")
        updated_fields.append("Model Name (reverted to default)")

    if not updated_fields:
        socketio.emit('llm_config_updated', {'message': 'LLM 配置未发生变化。', 'type': 'info',
                                             'config': {'base_url': LLM_BASE_URL, 'model_name': LLM_MODEL_NAME,
                                                        'api_key_present': bool(
                                                            LLM_API_KEY and LLM_API_KEY.lower() != "none" and LLM_API_KEY.lower() != "lmstudio")}},
                      room=sid, namespace='/')
        return

    if initialize_llm_client(DEFAULT_SYSTEM_PROMPT_TEMPLATE,
                             sid=sid):
        msg = f"LLM 配置已更新: {', '.join(updated_fields)}. LLM 客户端已重新初始化。"
        print(f"SID {sid}: {msg}")
        socketio.emit('llm_config_updated', {'message': msg, 'type': 'success',
                                             'config': {'base_url': LLM_BASE_URL, 'model_name': LLM_MODEL_NAME,
                                                        'api_key_present': bool(
                                                            LLM_API_KEY and LLM_API_KEY.lower() != "none" and LLM_API_KEY.lower() != "lmstudio")}},
                      room=sid, namespace='/')
    else:
        err_msg = "LLM 配置更新后，客户端重新初始化失败。"
        print(f"SID {sid}: {err_msg}")
        socketio.emit('llm_config_updated', {'message': err_msg, 'type': 'error',
                                             'config': {'base_url': new_base_url, 'model_name': new_model_name,
                                                        'api_key_present': bool(new_api_key)}}, room=sid, namespace='/')


@socketio.on('start_initial_setup')
def handle_start_initial_setup(data: Dict[str, Any]):
    sid = request.sid
    git_url = data.get('git_url')
    env_name_frontend = data.get('env_name', '').strip()

    global project_file_cache, conversation_history, initial_readme_summary_for_llm
    project_file_cache = {};
    conversation_history = [];
    initial_readme_summary_for_llm = None
    socketio.emit('clear_history_display', {}, room=sid, namespace='/')

    if not initialize_llm_client(DEFAULT_SYSTEM_PROMPT_TEMPLATE, sid=sid):
        socketio.emit('error_message', {'message': '开始任务前LLM客户端初始化失败。', 'type': 'error'}, room=sid,
                      namespace='/');
        return

    initial_step_data = {
        'step_type': 'initial_analysis', 'git_url': git_url, 'env_name': env_name_frontend,
        'determined_env_name': None, 'initial_readme_name': None, 'readme_summary_for_llm': None,
        'project_cloned_root_path': None, 'previous_command_result': {},
        'files_just_read_content': {},
        'pending_files_to_write': [],  # +++ Initialize pending queues +++
        'pending_commands_to_execute': []  # +++ Initialize pending queues +++
    }
    thread = threading.Thread(target=process_setup_step, args=(sid, initial_step_data, 0))
    thread.daemon = True;
    thread.start()


@app.route('/')
def index():
    return render_template('run.html', current_system_prompt=DEFAULT_SYSTEM_PROMPT_TEMPLATE)


if __name__ == '__main__':
    print("启动Flask服务器及SocketIO...")
    key_display = LLM_API_KEY[:5] + "..." if LLM_API_KEY and len(LLM_API_KEY) > 5 else (
        LLM_API_KEY if LLM_API_KEY else "未设置")
    print(f"LLM配置: 模型='{LLM_MODEL_NAME}', API Key='{key_display}', Base URL='{LLM_BASE_URL}'")
    print(f"LLM提示硬性总长度限制: {MAX_TOTAL_PROMPT_CHARS_HARD_LIMIT} chars")
    if 'llm' not in globals() or 'executor' not in globals():
        print("严重错误: llm.py 或 command_executor.py 未正确加载。")
    else:
        print(f"系统提示词模板长度 (不含动态部分): {len(DEFAULT_SYSTEM_PROMPT_TEMPLATE)} chars")
        if platform.system() == "Windows":
            executor.find_and_set_conda_paths()
        else:
            executor.find_and_set_conda_paths()
        socketio.run(app, debug=True, host='0.0.0.0', port=5000, use_reloader=False, allow_unsafe_werkzeug=True)