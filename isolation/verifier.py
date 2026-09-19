"""以不含 Python audit hook 的探針驗證原生 OS 沙盒邊界。"""

import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from isolation import get_sandbox
from isolation.common.sandbox import SandboxUnavailableError


def verify_sandbox_boundaries(workspace_path: Path) -> bool:
    """驗證合法 SQLite 可用，且原生政策阻擋各種工作區外操作。

    每個越界探針只接受作業系統回報的 EPERM 或 EACCES。一般程式錯誤、
    連線拒絕、缺少檔案或其他例外都會使驗證失敗。
    """
    workspace = Path(workspace_path).resolve()
    probe_dir = None
    probe_script = None
    inside_db = None

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        # 所有探針檔案都放在唯一目錄，清理時不會碰到既有使用者檔案。
        probe_dir = Path(tempfile.mkdtemp(prefix=".evo-boundary-probe-", dir=workspace))
        probe_script = probe_dir / "probe.py"
        inside_db = probe_dir / "inside.sqlite"
        sandbox = get_sandbox(workspace)
        build_probe_command = getattr(sandbox, "build_native_probe_command", None)
        if build_probe_command is None:
            sandbox.build_command(probe_script)
            raise SandboxUnavailableError("平台沒有原生 OS 沙盒探針。")

        with tempfile.TemporaryDirectory(
            prefix=".evo-sandbox-boundary-", dir=workspace.parent
        ) as outside_dir:
            outside = Path(outside_dir).resolve()
            if outside.is_relative_to(workspace):
                raise SandboxUnavailableError("越界探針目錄意外落在沙盒工作區內。")

            read_target = outside / "read-target.txt"
            parent_target = workspace / ".." / outside.name / "read-target.txt"
            delete_target = outside / "delete-target.txt"
            rename_source = outside / "rename-source.txt"
            rename_target = outside / "rename-target.txt"
            write_target = outside / "os-open-target.txt"
            sqlite_target = outside / "external.sqlite"
            read_target.write_text("probe", encoding="utf-8")
            delete_target.write_text("probe", encoding="utf-8")
            rename_source.write_text("probe", encoding="utf-8")
            with sqlite3.connect(sqlite_target) as connection:
                connection.execute("CREATE TABLE probe (value TEXT NOT NULL)")
                connection.execute("INSERT INTO probe VALUES ('probe')")

            # 在進入原生沙盒前建立 symlink，目標僅指向本次建立的暫存 canary。
            symlink_file = probe_dir / "outside-file-link"
            symlink_dir = probe_dir / "outside-dir-link"
            symlink_file.symlink_to(read_target)
            symlink_dir.symlink_to(outside, target_is_directory=True)

            probe_script.write_text(
                textwrap.dedent(
                    f"""
                    import errno
                    import os
                    import socket
                    import sqlite3
                    import subprocess
                    import sys
                    from pathlib import Path

                    results = {{}}

                    def must_be_denied(name, operation):
                        try:
                            operation()
                        except OSError as error:
                            if error.errno not in (errno.EPERM, errno.EACCES):
                                raise RuntimeError(
                                    f"{{name}} returned unexpected errno {{error.errno}}: {{error}}"
                                ) from error
                            results[name] = True
                        else:
                            raise RuntimeError(f"{{name}} unexpectedly succeeded")

                    def read_outside():
                        Path({str(read_target)!r}).read_text(encoding="utf-8")

                    def read_parent_traversal():
                        Path({str(parent_target)!r}).read_text(encoding="utf-8")

                    def enumerate_outside_directory():
                        os.listdir({str(outside)!r})

                    def read_symlink_file():
                        Path({str(symlink_file)!r}).read_text(encoding="utf-8")

                    def write_symlink_file():
                        Path({str(symlink_file)!r}).write_text("changed", encoding="utf-8")

                    def read_symlink_directory():
                        Path({str(symlink_dir / 'read-target.txt')!r}).read_text(encoding="utf-8")

                    def write_symlink_directory():
                        Path({str(symlink_dir / 'symlink-write.txt')!r}).write_text("probe", encoding="utf-8")

                    def hardlink_external_file():
                        os.link({str(read_target)!r}, {str(probe_dir / 'external-hardlink')!r})

                    def open_external_sqlite():
                        uri = {sqlite_target.as_uri() + '?mode=rw'!r}
                        try:
                            connection = sqlite3.connect(uri, uri=True, timeout=1)
                            try:
                                connection.execute("SELECT value FROM probe").fetchone()
                                connection.execute("INSERT INTO probe VALUES ('changed')")
                            finally:
                                connection.close()
                        except sqlite3.OperationalError as error:
                            if getattr(error, "sqlite_errorcode", None) != sqlite3.SQLITE_CANTOPEN:
                                raise RuntimeError(
                                    "外部 SQLite 未回報 SQLITE_CANTOPEN："
                                    f"{{error.sqlite_errorcode}} {{error}}"
                                ) from error
                            return
                        raise RuntimeError("外部 SQLite 意外可讀寫")

                    def write_with_os_open():
                        descriptor = os.open(
                            {str(write_target)!r},
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                        )
                        os.close(descriptor)

                    def delete_outside():
                        os.unlink({str(delete_target)!r})

                    def rename_outside():
                        os.rename({str(rename_source)!r}, {str(rename_target)!r})

                    def connect_network():
                        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        try:
                            client.settimeout(1)
                            client.connect(("127.0.0.1", 9))
                        finally:
                            client.close()

                    def start_external_process():
                        process = subprocess.Popen(["/usr/bin/true"])
                        try:
                            process.wait(timeout=1)
                        finally:
                            if process.poll() is None:
                                process.kill()
                                process.wait()

                    inside_db = Path({str(inside_db)!r})
                    # 正向檢查工作區內一般檔案操作是否可用。
                    inside_dir = inside_db.parent / "positive"
                    inside_dir.mkdir()
                    original = inside_dir / "before.txt"
                    renamed = inside_dir / "after.txt"
                    original.write_text("workspace", encoding="utf-8")
                    if original.read_text(encoding="utf-8") != "workspace":
                        raise RuntimeError("工作區檔案讀取內容不符")
                    original.rename(renamed)
                    renamed.unlink()
                    inside_dir.rmdir()
                    with sqlite3.connect(inside_db) as connection:
                        connection.execute("CREATE TABLE probe (id INTEGER)")
                        connection.execute("INSERT INTO probe VALUES (1)")
                        connection.commit()
                        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                            raise RuntimeError("合法工作區 SQLite integrity_check 失敗")

                    must_be_denied("external_read", read_outside)
                    must_be_denied("parent_traversal_read", read_parent_traversal)
                    must_be_denied("outside_directory_enumeration", enumerate_outside_directory)
                    must_be_denied("symlink_file_read", read_symlink_file)
                    must_be_denied("symlink_file_write", write_symlink_file)
                    must_be_denied("symlink_directory_read", read_symlink_directory)
                    must_be_denied("symlink_directory_write", write_symlink_directory)
                    must_be_denied("external_hardlink", hardlink_external_file)
                    open_external_sqlite()
                    must_be_denied("os_open_write", write_with_os_open)
                    must_be_denied("external_delete", delete_outside)
                    must_be_denied("external_rename", rename_outside)
                    must_be_denied("network", connect_network)
                    must_be_denied("external_process", start_external_process)
                    print("EVO_OS_BOUNDARIES_VERIFIED", flush=True)
                    """
                ),
                encoding="utf-8",
            )

            command = build_probe_command(probe_script)
            environment = sandbox.get_isolated_environ()
            try:
                result = subprocess.run(
                    command,
                    cwd=str(workspace),
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                print(f"[原生沙盒驗證] 探針逾時，隔離能力未能確認：{exc}", file=sys.stderr)
                return False
            except PermissionError as exc:
                raise SandboxUnavailableError(
                    f"目前執行環境拒絕啟動 sandbox-exec：{exc}"
                ) from exc

            details = (result.stdout + "\n" + result.stderr).strip()
            if (
                result.returncode == 71
                and "sandbox_apply" in details
                and "Operation not permitted" in details
            ):
                raise SandboxUnavailableError(
                    "外層執行環境拒絕套用 sandbox-exec；原生隔離能力無法在此環境驗證。"
                )

            if result.returncode != 0 or "EVO_OS_BOUNDARIES_VERIFIED" not in result.stdout:
                print(
                    "[原生沙盒驗證] 未通過；可能是原生政策功能失敗，或外層執行環境"
                    f"禁止套用 sandbox-exec（exit {result.returncode}）：\n{details}",
                    file=sys.stderr,
                )
                return False

            # 再確認拒絕操作沒有修改本次建立的外部 canary。
            if (
                read_target.read_text(encoding="utf-8") != "probe"
                or not delete_target.exists()
                or not rename_source.exists()
                or rename_target.exists()
                or write_target.exists()
                or (outside / "symlink-write.txt").exists()
                or (probe_dir / "external-hardlink").exists()
            ):
                print("[原生沙盒驗證] 越界探針改動了外部 canary。", file=sys.stderr)
                return False

            # SQLite 將原生權限拒絕轉成 SQLITE_CANTOPEN；比對已建立的有效資料庫，
            # 避免將檔案不存在誤認成成功隔離。
            with sqlite3.connect(sqlite_target) as connection:
                rows = connection.execute("SELECT value FROM probe").fetchall()
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if rows != [("probe",)] or integrity != ("ok",):
                print("[原生沙盒驗證] 外部 SQLite canary 被讀寫或損壞。", file=sys.stderr)
                return False

            return True
    except SandboxUnavailableError:
        raise
    except (OSError, ValueError) as exc:
        print(f"[原生沙盒驗證] 隔離不可用，拒絕通過：{exc}", file=sys.stderr)
        return False
    finally:
        if probe_dir is not None:
            import shutil

            shutil.rmtree(probe_dir, ignore_errors=True)


if __name__ == "__main__":
    test_dir = Path(__file__).resolve().parent.parent / "habitat"
    success = verify_sandbox_boundaries(test_dir)
    if success:
        print("✓ 原生 OS 沙盒邊界驗證通過。")
        sys.exit(0)
    print("✗ 原生 OS 沙盒邊界未通過，拒絕啟動宇宙。", file=sys.stderr)
    sys.exit(1)
