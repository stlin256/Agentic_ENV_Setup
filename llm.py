# llm.py

import requests
import json
from typing import List, Dict, Optional, Iterator, Tuple
import re


class LLMClient:
    def __init__(
            self,
            api_key: str,
            model_name: str,
            base_url: str,
            system_prompt: str = "You are a helpful AI assistant.",
            max_history_turns: int = 5,  # 注意：在main.py中我们为指令生成任务设置了历史长度
            timeout: int = 300  # 增加超时时间以应对可能较慢的流
    ):
        if not api_key:
            raise ValueError("API key cannot be empty.")
        if not model_name:
            raise ValueError("Model name cannot be empty.")
        if not base_url:
            raise ValueError("Base URL cannot be empty.")

        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url.rstrip('/')
        self.system_prompt_content = system_prompt
        self.max_history_messages = max_history_turns * 2
        self.timeout = timeout
        self.history: List[Dict[str, str]] = []

    def _prepare_messages(self, user_message_content: str) -> List[Dict[str, str]]:
        current_user_message = {"role": "user", "content": user_message_content}
        messages_for_api = [{"role": "system", "content": self.system_prompt_content}]
        # 对于指令生成任务，我们通常在 main.py 中为每次请求构建完整上下文，所以这里的 history 可能为空或不被主要依赖
        messages_for_api.extend(self.history)
        messages_for_api.append(current_user_message)
        return messages_for_api

    def _trim_and_update_history(self, user_message_content: str, assistant_message_content: str):
        # 这个方法主要用于对话型应用，对于单次指令生成的场景，历史管理可能在调用方进行
        self.history.append({"role": "user", "content": user_message_content})
        self.history.append({"role": "assistant", "content": assistant_message_content})
        if len(self.history) > self.max_history_messages:
            num_to_remove = len(self.history) - self.max_history_messages
            self.history = self.history[num_to_remove:]

    def get_response_stream(
            self,
            user_message: str,
            temperature: float = 0.7,
            max_tokens: int = 34374  # 确保有足够的max_tokens
    ) -> Iterator[Tuple[str, Optional[str]]]:
        if not user_message:
            yield "error", "用户消息不能为空。"
            return

        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        messages_payload = self._prepare_messages(user_message)
        data = {
            "model": self.model_name,
            "messages": messages_payload,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }

        # print(f"DEBUG LLM Request: POST {endpoint}, Data: {json.dumps(data, indent=2, ensure_ascii=False)}")

        try:
            with requests.post(endpoint, headers=headers, json=data, timeout=self.timeout, stream=True) as response:
                response.raise_for_status()
                # print(f"DEBUG LLM Response Status: {response.status_code}")

                processed_chunks_count = 0
                # has_received_content_after_last_potential_stop = False # 用于更精细的停止逻辑

                for line in response.iter_lines():
                    if line:
                        decoded_line = line.decode('utf-8', errors='replace')
                        # print(f"LLM_STREAM_RAW_LINE: {decoded_line}") # 非常详细的日志

                        if decoded_line.startswith('data: '):
                            json_str = decoded_line[len('data: '):].strip()
                            if json_str == "[DONE]":
                                print("LLM_STREAM_EVENT: [DONE] received, stopping stream.")
                                break
                            if not json_str:
                                # print("LLM_STREAM_EVENT: Empty data line skipped.")
                                continue

                            try:
                                chunk = json.loads(json_str)
                                processed_chunks_count += 1
                                # print(f"LLM_STREAM_CHUNK_{processed_chunks_count}: {json.dumps(chunk, ensure_ascii=False)}")

                                if chunk.get("choices") and len(chunk["choices"]) > 0:
                                    choice = chunk["choices"][0]
                                    delta = choice.get("delta", {})  # 确保delta存在
                                    finish_reason = choice.get("finish_reason")

                                    delta_content_str = None
                                    if "content" in delta and delta["content"] is not None:
                                        delta_content_str = delta["content"]
                                        # print(f"LLM_STREAM_YIELDING_CONTENT: '{delta_content_str}'")
                                        yield "delta_content", delta_content_str
                                        # has_received_content_after_last_potential_stop = True

                                    # print(f"LLM_STREAM_DEBUG: DeltaContent='{delta_content_str}', FinishReason='{finish_reason}'")

                                    # 结束条件：只有当收到明确的 "stop" 或 "length" finish_reason，
                                    # 并且当前块没有实际内容时，才中断。
                                    # [DONE] 是更优先的结束信号。
                                    if finish_reason in ["stop", "length"]:
                                        if not delta_content_str:  # 如果这个块没有内容，并且是stop/length
                                            print(
                                                f"LLM_STREAM_INFO: Breaking due to finish_reason: '{finish_reason}' and no new content in this chunk.")
                                            break
                                        else:  # 这个块有内容，即使有stop/length，也处理完这个块的内容
                                            print(
                                                f"LLM_STREAM_INFO: Processed content with finish_reason: '{finish_reason}'. Will break if next is [DONE] or similar.")
                                            # 让循环自然结束或等待 [DONE]
                                            # has_received_content_after_last_potential_stop = False
                                    elif finish_reason and finish_reason != "null":  # 其他非空的finish_reason
                                        print(
                                            f"LLM_STREAM_WARNING: Received unusual finish_reason: '{finish_reason}' with content: '{delta_content_str}'. Continuing stream.")
                                        # 不因为其他 finish_reason (如 tool_calls) 而中断

                            except json.JSONDecodeError as e:
                                print(f"LLM_STREAM_ERROR: JSONDecodeError on chunk: '{json_str}'. Error: {e}")
                                yield "error", f"流式响应JSON解析错误: {json_str}"
                        elif decoded_line.strip():
                            print(f"LLM_STREAM_UNHANDLED_LINE: '{decoded_line}'")  # 其他非data:开头的行
                    # else:
                    # print("LLM_STREAM_EVENT: Empty line from iter_lines.")

                print(f"LLM_STREAM_INFO: iter_lines loop finished. Processed {processed_chunks_count} data chunks.")

        except requests.exceptions.HTTPError as http_err:
            error_details = f"HTTP错误: {http_err}"
            try:
                error_details += f" - 响应状态: {response.status_code} - 响应内容: {response.text}"
            except:
                pass
            yield "error", error_details
            print(f"LLM_STREAM_ERROR: {error_details}")
        except requests.exceptions.ConnectionError as conn_err:
            yield "error", f"连接错误: {conn_err}"
            print(f"LLM_STREAM_ERROR: Connection error: {conn_err}")
        except requests.exceptions.Timeout as timeout_err:
            yield "error", f"超时错误: {timeout_err}"
            print(f"LLM_STREAM_ERROR: Timeout error: {timeout_err}")
        except requests.exceptions.RequestException as req_err:
            yield "error", f"请求错误: {req_err}"
            print(f"LLM_STREAM_ERROR: RequestException: {req_err}")
        except Exception as e:
            import traceback
            error_msg = f"获取流式响应时发生意外错误: {e}\n{traceback.format_exc()}"
            yield "error", error_msg
            print(f"LLM_STREAM_ERROR: Unexpected error in get_response_stream: {error_msg}")

        print("LLM_STREAM_INFO: get_response_stream generator is about to exit and yield stream_end.")
        yield "stream_end", None

    def clear_history(self):
        self.history = []
        print("对话历史已清除。")

    def set_system_prompt(self, new_system_prompt: str):
        if not new_system_prompt:
            print("警告：尝试设置空的系统提示词。将保留旧的提示词。")
            return
        self.system_prompt_content = new_system_prompt
        print(f"系统提示词已更新为： '{new_system_prompt}'")


# Example Usage (for llm.py standalone testing)
if __name__ == "__main__":
    print("LLM 客户端流式响应示例 (需要有效的API凭据和端点)")

    # 使用你提供的配置
    API_KEY = "lmstudio"
    MODEL_NAME = "nikolaykozloff/deepseek-r1-0528-qwen3-8b"
    BASE_URL = "http://192.168.0.32:1234/v1"

    # SYSTEM_PROMPT_FOR_JSON_TEST
    SYSTEM_PROMPT_FOR_JSON_TEST = (
        "你是一个专业的软件工程师助手，负责协助用户自动化配置项目环境。"
        "你的思考过程请放在 <think> 和 </think> 标签之间，这部分内容会被流式显示给用户。"
        "在所有思考结束后，你的最终输出必须是一个严格的JSON对象，包含 'thought' (总结性思考，字符串) 和 'commands_to_execute' (shell命令列表，字符串数组) 键。"
        "例如："
        "<think>用户想知道如何安装python。首先我会检查他们是否已经安装了。如果没有，我会建议使用conda。我会生成conda create命令。</think>"
        "{\"thought\": \"建议使用conda安装python 3.9\", \"commands_to_execute\": [\"conda create -n myenv python=3.9 -y\"]}"
    )
    json_user_message = (
        "我有一个Git仓库 `https://github.com/someuser/sample-python-project.git`。\n"
        "它的README.md内容如下（这是一个简化的例子）：\n"
        "```text\n"
        "# 示例Python项目\n依赖: requests, numpy\n请使用 Python 3.8+。\n通过 `pip install -r requirements.txt` 安装依赖。\n"
        "```\n"
        "请为这个项目在Conda中创建一个名为 'sample_env_demo' 的环境，并安装依赖。"
        "请先思考，然后给出JSON指令。"
    )

    try:
        client = LLMClient(
            api_key=API_KEY,
            model_name=MODEL_NAME,
            base_url=BASE_URL,
            system_prompt=SYSTEM_PROMPT_FOR_JSON_TEST,  # 使用要求JSON的prompt
            timeout=120  # 增加超时
        )
        print(f"\nLLM客户端已使用模型 {MODEL_NAME} 初始化。")

        print(f"\n发送消息: {json_user_message[:100]}...")  # 只打印部分用户消息
        print("\nAssistant (流式): \n--------------------", flush=True)

        full_response_content = ""
        error_occurred = False

        for event_type, content in client.get_response_stream(json_user_message, temperature=0.1,
                                                              max_tokens=0):
            if event_type == "delta_content":
                print(content, end="", flush=True)
                full_response_content += content
            elif event_type == "error":
                print(f"\n[流式响应错误]: {content}")
                error_occurred = True
                break
            elif event_type == "stream_end":
                print("\n--------------------\n[模型流式输出处理结束]")
                break

        if not error_occurred:
            print(f"\n\n--- 累积的完整响应 (长度: {len(full_response_content)}) ---\n")
            print(full_response_content)
            print("--- 完整响应结束 ---")

            # 尝试解析（模拟main.py中的逻辑）
            if "<think>" in full_response_content:  # 假设模型输出了思考
                print("\n--- 从累积响应中提取的思考内容 (非流式) ---")
                think_matches = re.findall(r"<think>(.*?)</think>", full_response_content, re.DOTALL | re.IGNORECASE)
                for i, thought in enumerate(think_matches):
                    print(f"思考块 {i + 1}:\n{thought.strip()}\n---")

            print("\n--- 尝试解析JSON部分 ---")
            # 移除 <think>
            json_candidate_no_think = re.sub(r"<think>.*?</think>", "", full_response_content,
                                             flags=re.DOTALL | re.IGNORECASE).strip()
            # 尝试从可能包含前后文本的字符串中提取JSON
            final_json_str_match = re.search(r'\{[\s\S]*\}', json_candidate_no_think)
            if final_json_str_match:
                final_json_str = final_json_str_match.group(0)
                print(f"提取用于解析的JSON字符串: \n{final_json_str}\n---")
                try:
                    parsed_json = json.loads(final_json_str)
                    print(f"成功解析JSON: \n{json.dumps(parsed_json, indent=2, ensure_ascii=False)}")
                except json.JSONDecodeError as e:
                    print(f"JSON解析失败: {e}")
            else:
                print("未能从响应中提取出JSON对象。")
        else:
            print("由于发生错误，未进一步处理响应。")


    except ValueError as ve:
        print(f"配置错误: {ve}")
    except Exception as e:
        import traceback

        print(f"示例中发生意外错误: {e}\n{traceback.format_exc()}")