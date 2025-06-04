#!/usr/bin/env python3
import os
import subprocess
import shlex
import shutil
import re
import tempfile

# --- 内置配置 ---
DEFAULT_CONFIG = {
    'PROMPT_FORMAT': "PyTerm:{cwd} $ ",
    'STARTUP_COMMANDS': [
        "echo 'Welcome to PyTerminal (Selected Shell Modes)!'",
        "echo 'Type \"help_pyterm\" for PyTerminal specific commands.'",
    ],
    'ALIASES': {"ll": "ls -alF", "la": "ls -A", "l": "ls -CF", "gs": "git status"},
    'CUSTOM_PYTHON_COMMANDS': {}
}
# --- /内置配置 ---

config = {}
command_history = []
CONDA_CONDABIN_BAT_PATH = None  # Path to conda.bat or conda.exe, preferably from condabin
ANACONDA_ACTIVATE_BAT_PATH = None  # Path to activate.bat for activating environments
ANACONDA_BASE_PATH = None  # Deduced root of Anaconda/Miniconda installation

shell_execution_mode = "4"  # Default to "Auto" mode (now option 4)


def find_conda_paths():
    global CONDA_CONDABIN_BAT_PATH, ANACONDA_ACTIVATE_BAT_PATH, ANACONDA_BASE_PATH

    conda_exe_in_path = shutil.which("conda.exe")
    conda_bat_in_path = shutil.which("conda.bat")

    # Determine CONDA_CONDABIN_BAT_PATH (for direct conda calls like `run`)
    if conda_bat_in_path and os.path.join("condabin", "conda.bat").lower() in conda_bat_in_path.lower():
        CONDA_CONDABIN_BAT_PATH = conda_bat_in_path
    elif conda_exe_in_path and os.path.join("condabin", "conda.exe").lower() in conda_exe_in_path.lower():
        CONDA_CONDABIN_BAT_PATH = conda_exe_in_path
    elif conda_bat_in_path:
        CONDA_CONDABIN_BAT_PATH = conda_bat_in_path
    elif conda_exe_in_path:
        CONDA_CONDABIN_BAT_PATH = conda_exe_in_path

    if CONDA_CONDABIN_BAT_PATH:
        print(f"[INFO] Conda executable for direct calls (e.g., 'run'): {CONDA_CONDABIN_BAT_PATH}")
        try:
            deduced_base_from_conda_exec = os.path.dirname(os.path.dirname(CONDA_CONDABIN_BAT_PATH))
            potential_activate = os.path.join(deduced_base_from_conda_exec, "Scripts", "activate.bat")
            if os.path.isfile(potential_activate):
                ANACONDA_ACTIVATE_BAT_PATH = potential_activate
                if os.path.isdir(deduced_base_from_conda_exec): ANACONDA_BASE_PATH = deduced_base_from_conda_exec
                print(f"[DEBUG] Deduced activate.bat from conda executable: {ANACONDA_ACTIVATE_BAT_PATH}")
        except:
            pass

    if not ANACONDA_ACTIVATE_BAT_PATH:
        shutil_activate = shutil.which("activate.bat")
        if shutil_activate and os.path.isfile(shutil_activate):
            ANACONDA_ACTIVATE_BAT_PATH = shutil_activate
            print(f"[DEBUG] Found activate.bat via shutil.which: {ANACONDA_ACTIVATE_BAT_PATH}")
            try:
                potential_base = os.path.dirname(os.path.dirname(shutil_activate))
                if os.path.isdir(potential_base) and os.path.isdir(os.path.join(potential_base, "conda-meta")):
                    ANACONDA_BASE_PATH = potential_base
                else:
                    print(f"[DEBUG] Deduced path {potential_base} from activate.bat doesn't look like a conda root.")
            except:
                ANACONDA_BASE_PATH = None
            if not ANACONDA_BASE_PATH: print(
                f"[WARN] Found activate.bat at {ANACONDA_ACTIVATE_BAT_PATH} but ANACONDA_BASE_PATH could not be reliably determined.")

    if not CONDA_CONDABIN_BAT_PATH: print(
        "[WARN] `conda.bat/exe` (preferably condabin) not reliably found. Direct `conda run` might fail.")
    if ANACONDA_ACTIVATE_BAT_PATH:
        print(f"[INFO] Anaconda activate.bat for general activation: {ANACONDA_ACTIVATE_BAT_PATH}")
        if ANACONDA_BASE_PATH:
            print(f"[INFO] Associated Anaconda base path: {ANACONDA_BASE_PATH}")
        else:
            print("[WARN] ANACONDA_BASE_PATH not set; activate.bat might be called without base path argument.")
    else:
        print(
            "[WARN] Anaconda activate.bat not found. Fallback conda execution (for non-run commands in auto mode) may be limited.")


def load_config(is_reload=False): global config; config = DEFAULT_CONFIG.copy(); print(
    f"[*] Configuration {'re' if is_reload else ''}loaded.")


def run_startup_commands():
    if config.get('STARTUP_COMMANDS'):
        print("[*] Running startup commands...")
        for cmd_str in config['STARTUP_COMMANDS']: print(f">>> {cmd_str}"); execute_command(cmd_str)
        print("[*] Startup commands finished.")


def get_prompt():
    try:
        return config.get('PROMPT_FORMAT', "{cwd} $ ").format(cwd=os.getcwd())
    except Exception as e:
        print(f"[!] Prompt error: {e}"); return f"{os.getcwd()} $ "


def apply_aliases(parts):
    aliases = config.get('ALIASES', {});
    if parts and parts[0] in aliases:
        try:
            return shlex.split(aliases[parts[0]]) + parts[1:]
        except ValueError as e:
            print(f"[!] Alias error '{parts[0]}': {e}"); return parts
    return parts


def execute_command(command_str):
    global command_history, shell_execution_mode
    cmd_orig_str = command_str.strip();
    if not cmd_orig_str: return

    if not command_history or command_history[-1] != cmd_orig_str:
        if cmd_orig_str.lower() != "history" or \
                (cmd_orig_str.lower() == "history" and (
                        not command_history or command_history[-1].lower() != "history")):
            command_history.append(cmd_orig_str)

    try:
        parts_after_shlex = shlex.split(cmd_orig_str)
    except ValueError:
        print(f"pyterm: syntax error: {cmd_orig_str}"); return
    if not parts_after_shlex: return

    parts_after_alias = apply_aliases(parts_after_shlex)
    effective_command_name = parts_after_alias[0]
    final_parts_for_execution = list(parts_after_alias)
    match_builtin_cmd = effective_command_name.lower()

    # Built-in commands
    if match_builtin_cmd in ["exit", "quit"]:
        raise SystemExit("Exiting...")
    elif match_builtin_cmd == "cd":
        target_dir = os.path.expanduser(parts_after_alias[1] if len(parts_after_alias) > 1 else "~")
        try:
            os.chdir(target_dir)
        except FileNotFoundError:
            print(f"pyterm: cd: no such dir: {target_dir}")
        except Exception as e:
            print(f"pyterm: cd: {e}")
        return
    elif match_builtin_cmd == "history":
        hist = command_history[:-1] if command_history and command_history[
            -1].strip().lower() == "history" else command_history
        for i, c in enumerate(hist): print(f"{i:4d}  {c}")
        return
    elif match_builtin_cmd == "reload_config":
        load_config(is_reload=True); return
    elif match_builtin_cmd == "help_pyterm":
        print("PyTerminal specific commands: exit, quit, cd, history, reload_config, help_pyterm, select_shell_mode")
        if os.name == 'nt': print("  pyterm_extern_conda <cmd...>: Runs conda command in new prompt.")
        print(f"\nSelected Conda execution mode: {get_shell_mode_description(shell_execution_mode)}")
        print("  For non-'conda run' commands, 'Auto' mode uses a temp .bat with activation.")
        print(
            "  Other modes (Popen, call, run) execute non-'conda run' commands directly, which might require manual activation or fail.")
        return
    elif match_builtin_cmd == "select_shell_mode":
        select_shell_execution_mode_interactive(); return
    elif match_builtin_cmd == "pyterm_extern_conda" and os.name == 'nt':
        if len(parts_after_alias) > 1:
            actual_conda_cmd_parts = parts_after_alias[1:]
            if not ANACONDA_ACTIVATE_BAT_PATH: print("[ERROR] pyterm_extern_conda: activate.bat not found."); return

            activate_cmd_list = [ANACONDA_ACTIVATE_BAT_PATH]
            if ANACONDA_BASE_PATH:
                activate_cmd_list.append(ANACONDA_BASE_PATH)
            else:
                print(
                    "[WARN] pyterm_extern_conda: ANACONDA_BASE_PATH unknown. Calling activate.bat without base path arg.")

            activate_cmd_str = subprocess.list2cmdline(activate_cmd_list)
            user_conda_cmd_str = subprocess.list2cmdline(actual_conda_cmd_parts)
            cfnp = f'CALL {activate_cmd_str} && {user_conda_cmd_str}'
            feli = f'start "PyTerm Ext Conda" cmd.exe /K "{cfnp}"'
            print(f"[INFO] External prompt: {user_conda_cmd_str}\n[DEBUG] Launch: {feli}")
            try:
                subprocess.Popen(feli, shell=True)
            except Exception as e:
                print(f"[ERROR] Failed to start external prompt: {e}")
        else:
            print("Usage: pyterm_extern_conda <your_full_conda_command_here>"); return
        return

    # --- Conda command execution on Windows ---
    is_conda_command = effective_command_name.lower() == "conda"
    is_conda_run_cmd = is_conda_command and len(final_parts_for_execution) > 1 and final_parts_for_execution[
        1].lower() == "run"

    if is_conda_command and os.name == 'nt':
        conda_executable = CONDA_CONDABIN_BAT_PATH
        if not conda_executable:
            print("[ERROR] Conda executable (CONDA_CONDABIN_BAT_PATH) not found. Cannot execute conda command.")
            return

        # Prepare command parts with full path to conda executable
        parts_with_conda_path = list(final_parts_for_execution)
        if parts_with_conda_path[0].lower() == "conda": parts_with_conda_path[0] = conda_executable

        # Determine if the conda executable is a .bat file
        is_bat_executable = conda_executable.lower().endswith(".bat")

        # `conda run` specific handling (preferred via subprocess.run)
        if is_conda_run_cmd:
            if shell_execution_mode == "1":  # Popen for conda run
                execute_with_subprocess_popen(parts_with_conda_path, is_bat=is_bat_executable, is_conda_run=True)
            elif shell_execution_mode == "2":  # call for conda run
                execute_with_subprocess_call(parts_with_conda_path, is_bat=is_bat_executable, is_conda_run=True)
            elif shell_execution_mode == "3" or shell_execution_mode == "4":  # run or Auto (for conda run)
                execute_with_subprocess_run(parts_with_conda_path, is_bat=is_bat_executable)
            else:  # Should not happen if menu is restricted
                print(f"[WARN] Invalid mode '{shell_execution_mode}' for conda run. Defaulting to subprocess.run.")
                execute_with_subprocess_run(parts_with_conda_path, is_bat=is_bat_executable)
            return

        # Handling for other conda commands (non-run)
        else:
            if shell_execution_mode == "4":  # Auto mode for non-run: use temp .bat
                if ANACONDA_ACTIVATE_BAT_PATH:  # Base path is optional for temp bat
                    execute_conda_via_temp_bat(final_parts_for_execution)  # original parts for temp bat
                else:
                    print("[WARN] Auto mode for non-run conda cmd: activate.bat missing. Attempting direct exec.")
                    # Fallback to direct execution if activate.bat is missing for temp bat
                    if shell_execution_mode == "1":
                        execute_with_subprocess_popen(parts_with_conda_path, is_bat=is_bat_executable)
                    elif shell_execution_mode == "2":
                        execute_with_subprocess_call(parts_with_conda_path, is_bat=is_bat_executable)
                    else:
                        execute_with_subprocess_run(parts_with_conda_path,
                                                    is_bat=is_bat_executable)  # Default direct to run
            elif shell_execution_mode == "1":  # Popen direct for non-run
                print("[INFO] Executing non-run conda command with Popen (no pre-activation). May fail.")
                execute_with_subprocess_popen(parts_with_conda_path, is_bat=is_bat_executable)
            elif shell_execution_mode == "2":  # call direct for non-run
                print("[INFO] Executing non-run conda command with call (no pre-activation). May fail.")
                execute_with_subprocess_call(parts_with_conda_path, is_bat=is_bat_executable)
            elif shell_execution_mode == "3":  # run direct for non-run
                print("[INFO] Executing non-run conda command with run (no pre-activation). May fail.")
                execute_with_subprocess_run(parts_with_conda_path, is_bat=is_bat_executable)
            else:  # Should not happen
                print(
                    f"[WARN] Invalid mode '{shell_execution_mode}' for non-run conda. Defaulting to temp .bat if possible.")
                if ANACONDA_ACTIVATE_BAT_PATH:
                    execute_conda_via_temp_bat(final_parts_for_execution)
                else:
                    execute_with_subprocess_run(parts_with_conda_path, is_bat=is_bat_executable)
            return

    # Fallback for non-conda commands, or non-Windows, or if conda specific handling wasn't triggered
    execute_general_command_with_subprocess_run(subprocess.list2cmdline(final_parts_for_execution))


def get_clean_env():
    env = {}
    essential_vars = ["SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE",
                      "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
                      "ALLUSERSPROFILE", "PUBLIC", "COMPUTERNAME", "SystemDrive",
                      "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA"]
    for var in essential_vars:
        if var in os.environ: env[var] = os.environ[var]
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def execute_conda_via_temp_bat(final_parts_for_execution):
    """For non-run conda commands in 'Auto' mode, uses temp .bat with pre-activation."""
    print("[INFO] Auto mode: Executing non-run conda command via temp .bat (activates base).")
    if not ANACONDA_ACTIVATE_BAT_PATH:
        print("[ERROR] Temp .bat: ANACONDA_ACTIVATE_BAT_PATH missing. Cannot pre-activate.")
        # Fallback to a direct run if activation isn't possible for temp bat
        parts_with_conda_path = list(final_parts_for_execution)
        if CONDA_CONDABIN_BAT_PATH and parts_with_conda_path[0].lower() == "conda":
            parts_with_conda_path[0] = CONDA_CONDABIN_BAT_PATH
        execute_with_subprocess_run(parts_with_conda_path, is_bat=CONDA_CONDABIN_BAT_PATH.lower().endswith(
            ".bat") if CONDA_CONDABIN_BAT_PATH else False)
        return

    activate_cmd_list = [ANACONDA_ACTIVATE_BAT_PATH]
    if ANACONDA_BASE_PATH: activate_cmd_list.append(ANACONDA_BASE_PATH)
    q_act_and_base = subprocess.list2cmdline(activate_cmd_list)

    conda_cmd_parts_for_bat = list(final_parts_for_execution)
    if CONDA_CONDABIN_BAT_PATH and conda_cmd_parts_for_bat[0].lower() == "conda":
        conda_cmd_parts_for_bat[0] = CONDA_CONDABIN_BAT_PATH
    user_conda_cmd_str = subprocess.list2cmdline(conda_cmd_parts_for_bat)

    temp_bat_content = f"@echo off\r\nCALL {q_act_and_base}\r\n{user_conda_cmd_str}\r\nEXIT /B %ERRORLEVEL%\r\n"
    temp_bat_file_path = None
    try:
        fd, temp_bat_file_path = tempfile.mkstemp(suffix=".bat", text=False)
        os.close(fd)
        with open(temp_bat_file_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(temp_bat_content)
        cmd_list_for_run = ['cmd.exe', '/D', '/C', temp_bat_file_path]
        print(
            f"[DEBUG] Temp .bat for non-run. Content:\n{temp_bat_content.strip()}\n[DEBUG] Executing: {cmd_list_for_run}")
        result = subprocess.run(cmd_list_for_run, capture_output=True, text=True, check=False, cwd=os.getcwd(),
                                env=get_clean_env())
        if result.stdout: print(result.stdout, end='')
        if result.stderr: print(result.stderr, end='')
    except Exception as e:
        print(f"Error with temp .bat: {e}")
    finally:
        if temp_bat_file_path and os.path.exists(temp_bat_file_path):
            try:
                os.remove(temp_bat_file_path)
            except:
                pass


def execute_with_subprocess_popen(cmd_parts, is_bat=False, is_conda_run=False):
    # `is_conda_run` flag helps decide if special handling for conda run is needed here
    print(f"[INFO] Using subprocess.Popen: {cmd_parts}")
    cmd_to_run = cmd_parts

    if is_bat:  # cmd_parts[0] is a .bat file, needs cmd.exe /C
        bat_cmd_str = subprocess.list2cmdline(cmd_parts)  # e.g., "C:\path\conda.BAT run -n env ..."
        cmd_to_run = ['cmd.exe', '/D', '/C', bat_cmd_str]
        print(f"[DEBUG] Popen for .bat via cmd.exe: {cmd_to_run}")
    # else: cmd_parts is ['executable.exe', 'arg1', ...]

    try:
        proc = subprocess.Popen(cmd_to_run, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=os.getcwd(), env=get_clean_env())
        out, err = proc.communicate()
        if out: print(out, end='')
        if err: print(err, end='')
    except FileNotFoundError:
        print(f"[ERROR] Popen: Command not found: {cmd_to_run[0]}")
    except Exception as e:
        print(f"[ERROR] Popen execution failed: {e}")


def execute_with_subprocess_call(cmd_parts, is_bat=False, is_conda_run=False):
    print(f"[INFO] Using subprocess.call: {cmd_parts}")
    cmd_to_run = cmd_parts
    if is_bat:
        bat_cmd_str = subprocess.list2cmdline(cmd_parts)
        cmd_to_run = ['cmd.exe', '/D', '/C', bat_cmd_str]
        print(f"[DEBUG] Call for .bat via cmd.exe: {cmd_to_run}")
    try:
        # Output goes to PyTerm's stdout/stderr directly
        subprocess.call(cmd_to_run, shell=False, cwd=os.getcwd(), env=get_clean_env())
    except FileNotFoundError:
        print(f"[ERROR] Call: Command not found: {cmd_to_run[0]}")
    except Exception as e:
        print(f"[ERROR] Call execution failed: {e}")


def execute_with_subprocess_run(cmd_parts, is_bat=False):
    # This is the primary method for `conda run` in Auto mode.
    print(f"[INFO] Using subprocess.run: {cmd_parts}")
    cmd_to_run = cmd_parts
    if is_bat:
        bat_cmd_str = subprocess.list2cmdline(cmd_parts)
        cmd_to_run = ['cmd.exe', '/D', '/C', bat_cmd_str]
        print(f"[DEBUG] Run for .bat via cmd.exe: {cmd_to_run}")
    try:
        result = subprocess.run(cmd_to_run, shell=False, capture_output=True, text=True, check=False, cwd=os.getcwd(),
                                env=get_clean_env())
        if result.stdout: print(result.stdout, end='')
        if result.stderr: print(result.stderr, end='')
    except FileNotFoundError:
        print(f"[ERROR] Run: Command not found: {cmd_to_run[0]}")
    except Exception as e:
        print(f"[ERROR] Run execution failed: {e}")


def execute_general_command_with_subprocess_run(cmd_str_for_shell_true):
    """General command execution using subprocess.run, assuming shell=True."""
    print(f"[INFO] General command with subprocess.run (shell=True): {cmd_str_for_shell_true}")
    try:
        result = subprocess.run(cmd_str_for_shell_true, shell=True, capture_output=True, text=True, check=False,
                                cwd=os.getcwd(), env=get_clean_env())
        if result.stdout: print(result.stdout, end='')
        if result.stderr: print(result.stderr, end='')
    except FileNotFoundError:
        print(f"[ERROR] General command not found.")
    except Exception as e:
        print(f"[ERROR] Error in general command execution: {e}")


def get_shell_mode_description(mode_code):
    modes = {
        "1": "subprocess.Popen()",
        "2": "subprocess.call()",
        "3": "subprocess.run()",
        "4": "Auto (Recommended: `conda run` via subprocess.run; others via temp .bat with activation)"
    }
    return modes.get(mode_code, "Unknown (" + str(mode_code) + ")")


def select_shell_execution_mode_interactive():
    global shell_execution_mode
    print("\nSelect Conda execution mode (Windows only, for `conda` commands):")
    modes_desc = [
        ("1",
         "subprocess.Popen() - Direct execution. `conda run` works. Others may need manual activation if chosen directly."),
        ("2",
         "subprocess.call() - Direct execution, output to console. `conda run` works. Others may need manual activation."),
        ("3",
         "subprocess.run() - Direct execution, captures output. `conda run` works. Others may need manual activation."),
        ("4",
         "Auto (Default & Recommended) - Uses `subprocess.run()` for `conda run` (no pre-activation). Uses temp `.bat` file (with pre-activation) for other `conda` commands.")
    ]
    for code, desc in modes_desc: print(f"  {code}: {desc}")

    current_desc = get_shell_mode_description(shell_execution_mode)
    while True:
        choice = input(
            f"Enter choice (1-4) or press Enter to keep current ({shell_execution_mode}: {current_desc}): ").strip()
        if not choice: break
        if choice in [m[0] for m in modes_desc]:
            shell_execution_mode = choice
            print(f"Conda execution mode set to: {get_shell_mode_description(shell_execution_mode)}")
            break
        else:
            print("Invalid choice.")


def main_loop():
    print(f"PyTerminal. Python {'.'.join(map(str, os.sys.version_info[:3]))}. Type 'exit' or 'help_pyterm'.")
    if os.name == 'nt': print("[INFO] Windows detected.")

    while True:
        try:
            user_input_str = input(get_prompt())
            if user_input_str: execute_command(user_input_str)
        except KeyboardInterrupt:
            print("\nInterrupt (Ctrl+C)")
        except EOFError:
            print("exit"); break
        except SystemExit as e:
            print(e); break
        except Exception as e_main:
            print(f"[!] Unhandled error in main loop: {e_main}")
            # import traceback; traceback.print_exc() # For deeper debugging


if __name__ == "__main__":
    if os.name == 'nt': find_conda_paths()
    load_config()
    select_shell_execution_mode_interactive()
    run_startup_commands()
    main_loop()