import subprocess
import os
import shutil
import shlex
import tempfile
import platform
import select
import threading
import queue
from typing import List, Dict, Union, Iterator, Tuple, Optional, Any

# Conditional import for fcntl
if platform.system() != "Windows":
    try:
        import fcntl
    except ImportError:
        print(
            "[WARN] command_executor: fcntl module not found, non-blocking pipe reads might be limited on this Unix-like system.")
        fcntl = None  # type: ignore
else:
    fcntl = None  # type: ignore

# --- Globals, find_and_set_conda_paths, get_clean_env_for_conda, _threaded_read_pipe ---
# (These functions remain the same as the previous version where get_clean_env_for_conda
# was replaced with your original more restrictive version)

CONDA_EXE_PATH: Optional[str] = None
CONDA_BAT_PATH: Optional[str] = None
CONDA_ROOT_PATH: Optional[str] = None
CONDA_SCRIPTS_PATH: Optional[str] = None
CONDA_CONDABIN_PATH: Optional[str] = None
CONDA_LIBRARY_BIN_PATH: Optional[str] = None
ANACONDA_ACTIVATE_BAT_PATH: Optional[str] = None
ANACONDA_BASE_PATH: Optional[str] = None
END_OF_COMMAND_MARKER = f"__EOC_MARKER__{os.urandom(8).hex()}__"
RETURN_CODE_MARKER_PREFIX = "__RC_MARKER__:"


def find_and_set_conda_paths():
    global CONDA_EXE_PATH, CONDA_BAT_PATH, CONDA_ROOT_PATH, \
        CONDA_SCRIPTS_PATH, CONDA_CONDABIN_PATH, CONDA_LIBRARY_BIN_PATH, \
        ANACONDA_ACTIVATE_BAT_PATH, ANACONDA_BASE_PATH
    CONDA_BAT_PATH = shutil.which("conda.bat")
    CONDA_EXE_PATH = shutil.which("conda.exe")
    env_conda_exe_var = os.environ.get("CONDA_EXE")
    if env_conda_exe_var and os.path.isfile(env_conda_exe_var):
        CONDA_EXE_PATH = env_conda_exe_var
        print(f"[INFO] Found CONDA_EXE from environment variable: {CONDA_EXE_PATH}")
    primary_deduction_source = None
    if platform.system() == "Windows":
        if CONDA_BAT_PATH:
            primary_deduction_source = CONDA_BAT_PATH
        elif CONDA_EXE_PATH and CONDA_EXE_PATH.lower().endswith(".bat"):
            primary_deduction_source = CONDA_EXE_PATH
    else:
        if CONDA_EXE_PATH: primary_deduction_source = CONDA_EXE_PATH
    if primary_deduction_source:
        print(f"[INFO] Primary Conda executable for path deduction: {primary_deduction_source}")
        try:
            script_dir_guess = os.path.dirname(primary_deduction_source)
            parent_of_script_dir = os.path.dirname(script_dir_guess)
            if os.path.exists(os.path.join(parent_of_script_dir, "conda-meta")):
                CONDA_ROOT_PATH = parent_of_script_dir;
                ANACONDA_BASE_PATH = CONDA_ROOT_PATH
                _scripts = os.path.join(CONDA_ROOT_PATH, "Scripts");
                if os.path.isdir(_scripts): CONDA_SCRIPTS_PATH = _scripts
                _condabin = os.path.join(CONDA_ROOT_PATH, "condabin");
                if os.path.isdir(_condabin): CONDA_CONDABIN_PATH = _condabin
                _library_bin = os.path.join(CONDA_ROOT_PATH, "Library", "bin");
                if os.path.isdir(_library_bin): CONDA_LIBRARY_BIN_PATH = _library_bin
                if platform.system() == "Windows":
                    _act_cb = os.path.join(_condabin, "activate.bat") if _condabin else None
                    _act_s = os.path.join(_scripts, "activate.bat") if _scripts else None
                    if _act_cb and os.path.isfile(_act_cb):
                        ANACONDA_ACTIVATE_BAT_PATH = _act_cb
                    elif _act_s and os.path.isfile(_act_s):
                        ANACONDA_ACTIVATE_BAT_PATH = _act_s
                    _conda_bat_cb = os.path.join(_condabin, "conda.bat") if _condabin else None
                    _conda_bat_s = os.path.join(_scripts, "conda.bat") if _scripts else None
                    if _conda_bat_cb and os.path.isfile(_conda_bat_cb):
                        CONDA_BAT_PATH = _conda_bat_cb
                    elif _conda_bat_s and os.path.isfile(_conda_bat_s) and (
                            not CONDA_BAT_PATH or CONDA_BAT_PATH != _conda_bat_s):
                        CONDA_BAT_PATH = _conda_bat_s
            else:
                if os.path.normcase(script_dir_guess).endswith(os.path.normcase("scripts")):
                    CONDA_SCRIPTS_PATH = script_dir_guess
                elif os.path.normcase(script_dir_guess).endswith(os.path.normcase("condabin")):
                    CONDA_CONDABIN_PATH = script_dir_guess
        except Exception as e:
            print(f"[WARN] Error deducing Conda paths from '{primary_deduction_source}': {e}")
    else:
        print("[WARN] No primary Conda executable found for path deduction.")
    if not CONDA_ROOT_PATH: print("[WARN] CONDA_ROOT_PATH could not be determined reliably.")
    if platform.system() == "Windows":
        if not CONDA_BAT_PATH: print("[WARN] CONDA_BAT_PATH (for Windows) not found.")
        if not ANACONDA_ACTIVATE_BAT_PATH: print("[WARN] ANACONDA_ACTIVATE_BAT_PATH (for Windows) not found.")
    elif not CONDA_EXE_PATH:
        print("[WARN] CONDA_EXE_PATH (for Unix) not found.")
    print(f"--- Final Deduced Paths ---");
    print(f"CONDA_EXE_PATH: {CONDA_EXE_PATH}");
    print(f"CONDA_BAT_PATH (Win): {CONDA_BAT_PATH}");
    print(f"CONDA_ROOT_PATH: {CONDA_ROOT_PATH}");
    print(f"CONDA_SCRIPTS_PATH: {CONDA_SCRIPTS_PATH}");
    print(f"CONDA_CONDABIN_PATH: {CONDA_CONDABIN_PATH}");
    print(f"CONDA_LIBRARY_BIN_PATH: {CONDA_LIBRARY_BIN_PATH}");
    print(f"ANACONDA_ACTIVATE_BAT_PATH (Win): {ANACONDA_ACTIVATE_BAT_PATH}");
    print(f"ANACONDA_BASE_PATH: {ANACONDA_BASE_PATH}")


find_and_set_conda_paths()


def get_clean_env_for_conda() -> Dict[str, str]:
    env = {}
    essential_vars = ["SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "USERNAME", "PROGRAMFILES",
                      "PROGRAMFILES(X86)", "PROGRAMDATA", "ALLUSERSPROFILE", "PUBLIC", "COMPUTERNAME", "SystemDrive",
                      "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA"]
    for var in essential_vars:
        if var in os.environ: env[var] = os.environ[var]
    paths_to_prepend = []
    if CONDA_CONDABIN_PATH and os.path.isdir(CONDA_CONDABIN_PATH): paths_to_prepend.append(CONDA_CONDABIN_PATH)
    if platform.system() == "Windows":
        if CONDA_SCRIPTS_PATH and os.path.isdir(CONDA_SCRIPTS_PATH): paths_to_prepend.append(CONDA_SCRIPTS_PATH)
    elif CONDA_EXE_PATH:
        conda_exe_dir = os.path.dirname(CONDA_EXE_PATH)
        if os.path.isdir(conda_exe_dir) and conda_exe_dir not in paths_to_prepend: paths_to_prepend.append(
            conda_exe_dir)
    if CONDA_LIBRARY_BIN_PATH and os.path.isdir(CONDA_LIBRARY_BIN_PATH): paths_to_prepend.append(CONDA_LIBRARY_BIN_PATH)
    if CONDA_ROOT_PATH and os.path.isdir(
        CONDA_ROOT_PATH) and CONDA_ROOT_PATH not in paths_to_prepend: paths_to_prepend.append(CONDA_ROOT_PATH)
    if platform.system() == "Windows" and CONDA_ROOT_PATH and os.path.isdir(CONDA_ROOT_PATH):
        mingw_paths = [os.path.join(CONDA_ROOT_PATH, "Library", "mingw-w64", "bin"),
                       os.path.join(CONDA_ROOT_PATH, "Library", "usr", "bin")]
        for p in mingw_paths:
            if os.path.isdir(p) and p not in paths_to_prepend: paths_to_prepend.append(p)
    seen_paths = set();
    unique_paths_to_prepend = []
    for p_item in paths_to_prepend:
        norm_p = os.path.normcase(os.path.normpath(p_item))
        if norm_p not in seen_paths: unique_paths_to_prepend.append(p_item); seen_paths.add(norm_p)
    current_process_path = os.environ.get('PATH', '')
    if unique_paths_to_prepend:
        env['PATH'] = os.pathsep.join(unique_paths_to_prepend) + os.pathsep + current_process_path
        print(f"[INFO] Restricted Env - Prepended to PATH: {os.pathsep.join(unique_paths_to_prepend)}")
    else:
        env['PATH'] = current_process_path
        print("[WARN] Restricted Env - No Conda specific paths found to prepend, using current PATH.")
    if CONDA_ROOT_PATH: env['CONDA_ROOT'] = CONDA_ROOT_PATH
    env["PYTHONUTF8"] = "1";
    env["PYTHONIOENCODING"] = "utf-8";
    env["PYTHONUNBUFFERED"] = "1"
    if platform.system() == "Windows":
        comspec = os.environ.get("COMSPEC", "C:\\WINDOWS\\system32\\cmd.exe")
        if "COMSPEC" not in env: env["COMSPEC"] = comspec
    return env


def _threaded_read_pipe(pipe, q, stream_type, encoding, errors_policy):
    try:
        for byte_chunk in iter(lambda: pipe.read(128), b''):
            try:
                decoded_chunk = byte_chunk.decode(encoding, errors=errors_policy)
                q.put((stream_type, decoded_chunk))
            except Exception as e_decode:
                q.put(('stderr', f"[_threaded_read_pipe decode error: {e_decode}]"))
    except BrokenPipeError:
        pass
    except Exception as e_read:
        q.put(('stderr', f"[_threaded_read_pipe read error: {e_read}]"))
    finally:
        if pipe and not pipe.closed: pipe.close()
        q.put((stream_type, None))


def execute_command_stream(command: Union[str, List[str]],
                           working_directory: Optional[str] = None
                           ) -> Iterator[Tuple[str, Any]]:
    cmd_list_for_exec: List[str];
    cmd_str_for_log: str
    if isinstance(command, str):
        command_clean = command.strip()
        if not command_clean: yield "stderr", "错误：接收到空的字符串命令."; yield "return_code", -1; return
        try:
            cmd_list_for_exec = shlex.split(command_clean,
                                            posix=(platform.system() != "Windows")); cmd_str_for_log = command_clean
        except ValueError as e:
            yield "stderr", f"错误：命令字符串分割失败: {e} (原始命令: '{command_clean}')"; yield "return_code", -1; return
    elif isinstance(command, list):
        if not command: yield "stderr", "错误：接收到空的命令列表."; yield "return_code", -1; return
        cmd_list_for_exec = command;
        cmd_str_for_log = subprocess.list2cmdline(command)
    else:
        yield "stderr", "错误：命令参数类型无效."; yield "return_code", -1; return
    if not cmd_list_for_exec: yield "stderr", "错误：处理后命令列表为空."; yield "return_code", -1; return

    first_arg_lower = cmd_list_for_exec[0].lower()
    is_conda_cmd_on_windows = platform.system() == "Windows" and \
                              ((CONDA_BAT_PATH and os.path.normcase(cmd_list_for_exec[0]) == os.path.normcase(
                                  CONDA_BAT_PATH)) or \
                               first_arg_lower == "conda")
    is_conda_cmd_on_unix = platform.system() != "Windows" and \
                           ((CONDA_EXE_PATH and os.path.normcase(cmd_list_for_exec[0]) == os.path.normcase(
                               CONDA_EXE_PATH)) or \
                            first_arg_lower == "conda")
    is_conda_cmd = is_conda_cmd_on_windows or is_conda_cmd_on_unix
    is_conda_run = is_conda_cmd and len(cmd_list_for_exec) > 1 and cmd_list_for_exec[1].lower() == "run"

    final_popen_cmd_list: List[str];
    shell_for_popen = False;
    temp_bat_file_path: Optional[str] = None
    output_encoding = 'utf-8';
    errors_policy = 'replace'
    current_env = get_clean_env_for_conda()
    popen_kwargs: Dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "cwd": working_directory,
                                    "env": current_env, "universal_newlines": False,
                                    "close_fds": platform.system() != "Windows"}

    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            import ctypes; codepage = ctypes.windll.kernel32.GetACP(); output_encoding = f"cp{codepage}"
        except Exception:
            output_encoding = 'cp936'
        if is_conda_cmd:
            if not CONDA_BAT_PATH: yield "stderr", "错误: Windows 上未找到 conda.bat"; yield "return_code", -105; return
            cmd_list_for_exec[0] = CONDA_BAT_PATH
            if is_conda_run:
                final_popen_cmd_list = ['cmd.exe', '/D', '/C'] + cmd_list_for_exec;
                shell_for_popen = False
                print(f"EXECUTOR_DEBUG: Win Conda Run (via cmd /C): {final_popen_cmd_list}")
            else:
                if not ANACONDA_ACTIVATE_BAT_PATH:
                    yield "stderr", "警告: Win 未找到 activate.bat";
                    final_popen_cmd_list = ['cmd.exe', '/D', '/C'] + cmd_list_for_exec;
                    shell_for_popen = False
                    print(
                        f"EXECUTOR_DEBUG: Win Conda Non-Run (direct cmd /C, activate.bat missing): {final_popen_cmd_list}")
                else:
                    print(f"EXECUTOR_INFO: Win Conda Non-Run: Using temp .bat: {' '.join(cmd_list_for_exec)}")
                    temp_bat_content = f"@echo off\r\nCALL {subprocess.list2cmdline([ANACONDA_ACTIVATE_BAT_PATH])}\r\n{subprocess.list2cmdline(cmd_list_for_exec)}\r\nEXIT /B %ERRORLEVEL%\r\n"
                    try:
                        fd, temp_bat_file_path = tempfile.mkstemp(suffix=".bat", text=False,
                                                                  dir=working_directory or ".")
                        os.close(fd)
                        with open(temp_bat_file_path, "w", encoding=output_encoding, errors=errors_policy,
                                  newline="\r\n") as f_bat:
                            f_bat.write(temp_bat_content)
                        final_popen_cmd_list = ['cmd.exe', '/D', '/C', temp_bat_file_path];
                        shell_for_popen = False
                        print(
                            f"EXECUTOR_DEBUG: Win Conda Non-Run (Temp .bat: {temp_bat_file_path}): {final_popen_cmd_list}")
                    except Exception as e_temp_bat:
                        yield "stderr", f"错误：创建临时批处理文件失败: {e_temp_bat}"; yield "return_code", -1; return
        else:
            final_popen_cmd_list = cmd_list_for_exec; shell_for_popen = False; print(
                f"EXECUTOR_DEBUG: Win General (shell=False): {final_popen_cmd_list}")
    else:
        output_encoding = 'utf-8'
        if is_conda_cmd and CONDA_EXE_PATH: cmd_list_for_exec[0] = CONDA_EXE_PATH
        final_popen_cmd_list = cmd_list_for_exec;
        shell_for_popen = False
        print(f"EXECUTOR_DEBUG: Unix command (shell=False): {final_popen_cmd_list}")

    process = None
    try:
        print(
            f"EXECUTOR_FINAL_POPEN: Popen CMD List='{final_popen_cmd_list}', shell={shell_for_popen}, cwd={popen_kwargs.get('cwd')}, decode_as='{output_encoding}'")
        process = subprocess.Popen(final_popen_cmd_list, shell=shell_for_popen, **popen_kwargs)

        # *** MODIFICATION HERE: Check if fcntl is available before using it ***
        use_threaded_reader = platform.system() == "Windows" or fcntl is None  # Use threads if on Windows OR fcntl is not imported

        if use_threaded_reader:
            print("[INFO] Using threaded reader for process output.")
            q: queue.Queue[Tuple[str, Optional[str]]] = queue.Queue()
            stdout_thread = threading.Thread(target=_threaded_read_pipe,
                                             args=(process.stdout, q, 'stdout', output_encoding, errors_policy))
            stderr_thread = threading.Thread(target=_threaded_read_pipe,
                                             args=(process.stderr, q, 'stderr', output_encoding, errors_policy))
            stdout_thread.daemon = True;
            stderr_thread.daemon = True
            stdout_thread.start();
            stderr_thread.start()
            streams_open = 2
            while streams_open > 0:
                try:
                    stream_type, chunk = q.get(timeout=0.05)
                    if chunk is None: streams_open -= 1; continue
                    yield stream_type, chunk
                except queue.Empty:
                    if process.poll() is not None and not stdout_thread.is_alive() and not stderr_thread.is_alive():
                        while not q.empty():
                            try:
                                s_type, chk = q.get_nowait();
                            except queue.Empty:
                                break
                            if chk is None:
                                pass
                            else:
                                yield s_type, chk
                        break
                    elif process.poll() is not None and streams_open == 0:
                        break
            if stdout_thread.is_alive(): stdout_thread.join(timeout=0.5)
            if stderr_thread.is_alive(): stderr_thread.join(timeout=0.5)
        else:  # Unix with fcntl (we know fcntl is not None here)
            print("[INFO] Using fcntl/select reader for process output.")
            streams_map = {}
            if process.stdout: fcntl.fcntl(process.stdout.fileno(), fcntl.F_SETFL, os.O_NONBLOCK); streams_map[
                process.stdout.fileno()] = ('stdout', process.stdout)  # type: ignore
            if process.stderr: fcntl.fcntl(process.stderr.fileno(), fcntl.F_SETFL, os.O_NONBLOCK); streams_map[
                process.stderr.fileno()] = ('stderr', process.stderr)  # type: ignore
            active_filenos = list(streams_map.keys())
            while active_filenos:
                readable_fds, _, _ = select.select(active_filenos, [], [], 0.05)
                for fd in readable_fds:
                    stream_type, stream_obj = streams_map[fd]
                    try:
                        byte_chunk = stream_obj.read(256)
                        if byte_chunk:
                            yield stream_type, byte_chunk.decode(output_encoding, errors_policy)
                        else:
                            stream_obj.close(); active_filenos.remove(fd)
                    except BlockingIOError:
                        pass
                    except Exception as e_read_unix:
                        yield "stderr", f"[Unix read error on {stream_type}: {e_read_unix}]"
                        if fd in active_filenos: stream_obj.close(); active_filenos.remove(fd)
                if process.poll() is not None and not active_filenos: break
                if process.poll() is not None and not readable_fds:
                    for fd_check in list(active_filenos):
                        s_type, s_obj = streams_map[fd_check]
                        if not s_obj.closed:
                            try:
                                last_byte_chunk = s_obj.read(4096)
                            except:
                                last_byte_chunk = b''
                            if last_byte_chunk: yield s_type, last_byte_chunk.decode(output_encoding, errors_policy)
                            s_obj.close()
                        if fd_check in active_filenos: active_filenos.remove(fd_check)
                    if not active_filenos: break
        return_code = process.wait()
        yield "return_code", return_code
    except FileNotFoundError:
        cmd_name_fnf = final_popen_cmd_list[0]
        yield "stderr", f"错误：命令 '{cmd_name_fnf}' 的执行程序未找到。"
        yield "return_code", -101
    except Exception as e_outer:
        import traceback
        yield "stderr", f"执行命令时发生未知错误: {e_outer}\n{traceback.format_exc()}"
        yield "return_code", -99
    finally:
        if process:
            for p_stream in [process.stdout, process.stderr]:
                if p_stream and not p_stream.closed: p_stream.close()
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                except OSError:
                    pass
        if temp_bat_file_path and os.path.exists(temp_bat_file_path):
            try:
                os.remove(temp_bat_file_path)
            except Exception as e_del:
                print(f"[WARN] 删除临时批处理文件失败: {temp_bat_file_path}, Error: {e_del}")


# git_clone and scan_directory (remain same)
def git_clone(git_url: str, local_path: str, clean_before_clone: bool = False) -> Dict[str, Any]:
    if clean_before_clone and os.path.exists(local_path):
        try:
            shutil.rmtree(local_path)
        except Exception as e:
            return {"command_executed": f"shutil.rmtree('{local_path}')", "return_code": -1, "stdout": [],
                    "stderr": [f"清理目录失败: {e}"]}
    command = ["git", "clone", git_url, local_path];
    command_str = subprocess.list2cmdline(command)
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                check=False)
        return {"command_executed": command_str, "return_code": result.returncode,
                "stdout": result.stdout.splitlines() if result.stdout else [],
                "stderr": result.stderr.splitlines() if result.stderr else []}
    except FileNotFoundError:
        return {"command_executed": command_str, "return_code": -1, "stdout": [], "stderr": ["错误：'git' 命令未找到。"]}
    except Exception as e:
        return {"command_executed": command_str, "return_code": -1, "stdout": [],
                "stderr": [f"执行 git clone 时发生错误: {e}"]}


def scan_directory(directory_path: str, max_depth: int = -1) -> Dict[str, Any]:
    if not os.path.isdir(directory_path): return {"error": f"错误：路径 '{directory_path}' 不是一个有效的目录。"}
    files: List[str] = [];
    dirs: List[str] = []
    try:
        for root, d_names, f_names in os.walk(directory_path, topdown=True):
            current_depth = root.count(os.sep) - directory_path.count(os.sep)
            if max_depth != -1 and current_depth >= max_depth: d_names[:] = []
            rel_root = os.path.relpath(root, directory_path);
            if rel_root == '.': rel_root = ''
            for f_name in f_names: files.append(os.path.join(rel_root, f_name).replace("\\", "/"))
            for d_name in d_names: dirs.append(os.path.join(rel_root, d_name).replace("\\", "/"))
        return {"files": files, "directories": dirs, "base_path": directory_path}
    except Exception as e:
        return {"error": f"扫描目录时发生错误: {e}"}


if __name__ == '__main__':
    print("Command Executor (Strict PyTerm Conda Logic V4 - fcntl fix) - Direct Test Mode")
    test_commands = [
        "conda run -n d2l python --version",
    ]
    custom_cmd = input(f"Enter command for [{os.getcwd()}] (or Enter for sequence): $ ")
    if custom_cmd.strip(): test_commands = [custom_cmd.strip()]
    for cmd_input in test_commands:
        if cmd_input.lower() in ["exit", "quit"]: break
        if not cmd_input.strip(): continue
        print(f"\n--- Executing: {cmd_input} ---")
        for stream_type, content in execute_command_stream(cmd_input, working_directory=os.getcwd()):
            color = "\033[92m" if stream_type == "stdout" else "\033[91m" if stream_type == "stderr" else "\033[94m"
            if stream_type == "return_code":
                print(f"\n{color}Return Code: {content}\033[0m")
            else:
                print(f"{color}{content}\033[0m", end='', flush=True)
        print(f"\n--- End of '{cmd_input}' ---")