# test_persistent_shell.py

import time
import os  # 用于清除屏幕等（可选）

try:
    import shell_manager  # 假设 shell_manager.py 在同一目录
except ImportError:
    print("错误：无法导入 'shell_manager.py'。")
    print("请确保 'shell_manager.py' 文件与此脚本在同一目录下，")
    print("并且其中包含了 'PersistentShell' 类。")
    exit()


def clear_screen():
    """清空终端屏幕以便于阅读（跨平台）。"""
    os.system('cls' if os.name == 'nt' else 'clear')


def main():
    clear_screen()
    print("--- 持久化 Shell 交互测试工具 ---")

    shell_choice = ""
    while shell_choice not in ["powershell", "cmd"]:
        shell_choice = input(
            "请选择要启动的 Shell 类型 (powershell/cmd) [powershell]: ").lower().strip() or "powershell"
        if shell_choice not in ["powershell", "cmd"]:
            print("无效的选择，请输入 'powershell' 或 'cmd'。")

    print(f"正在启动持久化 {shell_choice} Shell...")

    # 使用 with 语句确保 shell 在结束时被正确关闭
    try:
        with shell_manager.PersistentShell(shell_type=shell_choice, startup_timeout=10.0) as pshell:
            if not pshell.is_ready:
                print(f"错误: {shell_choice} Shell 未能成功启动或准备就绪。程序将退出。")
                return

            print(f"\n持久化 {shell_choice} 已启动。")
            print(f"初始 Shell 工作目录: {pshell.current_working_directory or '未知 (可能在获取中...)'}")
            print("----------------------------------------------------")
            print("你可以输入任何想在该 Shell 中执行的命令。")
            print("特殊命令:")
            print("  'exit_shell' - 关闭持久 Shell 并退出本测试程序。")
            print("  'get_cwd'    - (内部调用) 获取并打印 Shell 当前工作目录。")
            print("  'clear'      - 清空本测试程序的终端屏幕。")
            print("----------------------------------------------------")

            while True:
                try:
                    # 显示当前的近似CWD作为提示符的一部分
                    prompt_cwd = pshell.current_working_directory or f"未知CWD ({pshell.shell_type})"
                    command_input_str = input(f"\n[{prompt_cwd}]$ ")

                    if command_input_str.lower() == 'exit_shell':
                        print("正在关闭持久 Shell 并退出...")
                        break

                    if command_input_str.lower() == 'clear':
                        clear_screen()
                        print("--- 持久化 Shell 交互测试工具 ---")  # 重新打印标题
                        print(f"当前持久 Shell: {pshell.shell_type}, CWD: {pshell.current_working_directory or '未知'}")
                        print("----------------------------------------------------")
                        continue

                    if not command_input_str.strip():
                        continue  # 用户只按了回车

                    print(f"\n>>> 准备在持久 Shell 中执行: {command_input_str}")
                    print("--- Shell 输出开始 ---")

                    # 定义回调函数来处理从持久Shell接收到的流式输出
                    def stream_handler(stream_type: str, line_content: str):
                        # 根据流类型进行不同颜色的打印（如果终端支持ANSI转义序列）
                        if stream_type == "stdout":
                            print(f"\033[92m  [STDOUT] {line_content}\033[0m")
                        elif stream_type == "stderr":
                            print(f"\033[91m  [STDERR] {line_content}\033[0m")
                        elif stream_type == "warning":
                            print(f"\033[93m  [WARNING] {line_content}\033[0m")
                        elif stream_type == "info":
                            print(f"\033[96m  [INFO] {line_content}\033[0m")
                        else:  # 其他可能的类型
                            print(f"  [{stream_type.upper()}] {line_content}")

                    # 调用 PersistentShell 的 execute_command_and_get_results 方法
                    # 这个方法会阻塞，直到命令执行完毕 (EOC标记返回)
                    results = pshell.execute_command_and_get_results(command_input_str, stream_handler)

                    print("--- Shell 输出结束 ---")
                    print(f"命令 '{results.get('command_executed')}' 执行分析:")
                    print(f"  返回码 (从Shell获取): {results.get('return_code')}")
                    print(f"  执行后 Shell CWD (从Shell获取): {results.get('final_cwd')}")
                    # pshell.current_working_directory 应该已经被 execute_command_and_get_results 更新了
                    # print(f"  (PersistentShell 实例 CWD 更新为: {pshell.current_working_directory})")


                except KeyboardInterrupt:
                    print("\n程序被用户中断 (Ctrl+C)。正在关闭持久 Shell...")
                    break
                except RuntimeError as r_err:  # 可能来自 shell_manager 中的 Shell 错误
                    print(f"\n运行时错误: {r_err}")
                    print("持久 Shell 可能已关闭或遇到问题。建议退出程序。")
                    break
                except Exception as e:
                    import traceback
                    print(f"\n发生了一个意外错误: {e}")
                    traceback.print_exc()
                    # 可以选择 break 或者让用户继续尝试

    except RuntimeError as shell_start_err:  # 捕获 PersistentShell 初始化时可能抛出的 RuntimeError
        print(f"无法启动持久 Shell: {shell_start_err}")
    except ImportError:
        # 已在脚本开头处理，但作为双重保障
        pass
    except Exception as e_global:
        import traceback
        print(f"测试脚本顶层发生意外错误: {e_global}")
        traceback.print_exc()
    finally:
        print("\n测试程序已结束。")


if __name__ == "__main__":
    main()