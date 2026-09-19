"""Evo 宇宙啟動前之安全邊界越界驗證器。

在啟動宇宙前執行主動越界測試：
若沙盒未能成功攔截外部寫入或非法指令，則依法拒絕啟動宇宙（依據 README 5.9）。
"""

import subprocess
import sys
from pathlib import Path

from isolation import get_sandbox


def verify_sandbox_boundaries(workspace_path: Path) -> bool:
    """執行主動越界滲透測試。

    若所有惡意/越界操作均被攔截且合法寫入正常運作，回傳 True；
    若有任何越界外洩則回傳 False。
    """
    workspace = Path(workspace_path).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    sandbox = get_sandbox(workspace)
    env = sandbox.get_isolated_environ()

    # 測試目標：嘗試寫入工作空間外部的父目錄暫存檔
    outside_target = workspace.parent / "_evo_breach_test.tmp"
    breach_script = workspace / "_test_breach.py"

    breach_code = f"""
import sys
from pathlib import Path

# 測試 1：嘗試向工作區外部寫入檔案
try:
    with open(r'{outside_target}', 'w') as f:
        f.write('BREACH_SUCCESS')
    print('BREACH_LEAK: 檔案成功寫入工作區外部！')
    sys.exit(101)
except (PermissionError, Exception):
    # 預期行為：操作被攔截
    pass

# 測試 2：嘗試未授權執行系統子進程
try:
    import subprocess
    subprocess.Popen(['echo', 'hacked'])
    print('BREACH_LEAK: 子進程成功執行！')
    sys.exit(102)
except (PermissionError, Exception):
    # 預期行為：操作被攔截
    pass

# 測試 3：合法操作測試，確認工作區內部寫入正常
try:
    inside_target = Path(r'{workspace}') / '_legit_test.tmp'
    with open(inside_target, 'w') as f:
        f.write('OK')
    inside_target.unlink(missing_ok=True)
except Exception as e:
    print(f'LEGITIMATE_FAIL: 工作區內合法寫入失敗：{{e}}')
    sys.exit(103)

print('BOUNDARY_VERIFICATION_PASSED')
sys.exit(0)
"""
    try:
        breach_script.write_text(breach_code, encoding="utf-8")
        cmd = sandbox.build_command(breach_script)

        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

        # 確保外部檔案絕對沒有被建立
        if outside_target.exists():
            outside_target.unlink(missing_ok=True)
            return False

        if proc.returncode != 0 or "BOUNDARY_VERIFICATION_PASSED" not in proc.stdout:
            return False

        return True
    except Exception as exc:
        print(f"[邊界驗證器] 執行驗證時發生異常：{exc}", file=sys.stderr)
        return False
    finally:
        breach_script.unlink(missing_ok=True)
        if outside_target.exists():
            outside_target.unlink(missing_ok=True)


if __name__ == "__main__":
    test_dir = Path(__file__).resolve().parent.parent / "habitat"
    success = verify_sandbox_boundaries(test_dir)
    if success:
        print("✓ 宇宙啟動前安全邊界驗證通過。")
        sys.exit(0)
    else:
        print("✗ 宇宙啟動前安全邊界驗證失敗！拒絕啟動宇宙。", file=sys.stderr)
        sys.exit(1)
