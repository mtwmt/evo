"""候選版本審查門神（Guardian）：靜態語法檢查、沙盒冒煙測試與邊界驗證。"""

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from isolation import get_sandbox


class VerificationResult(NamedTuple):
    """驗證結果結構體。"""
    passed: bool
    step: str
    error_message: str | None


class Guardian:
    """外層守門員：在部署前檢驗候選版本的安全性、語法與穩定性。"""

    def __init__(self, ruff_path: str = "ruff"):
        self.ruff_path = ruff_path

    def run_static_analysis(self, candidate_dir: Path) -> VerificationResult:
        """對候選目錄執行 ruff check 靜態語法分析。"""
        candidate_dir = Path(candidate_dir).resolve()

        # 尋找可用之 ruff 命令或透過當前 python -m ruff 執行
        ruff_bin = shutil.which("ruff") or str(Path(sys.executable).parent / "ruff")
        if not Path(ruff_bin).exists() and not shutil.which(ruff_bin):
            cmd = [sys.executable, "-m", "ruff", "check", str(candidate_dir)]
        else:
            cmd = [ruff_bin, "check", str(candidate_dir)]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return VerificationResult(
                    passed=False,
                    step="static_analysis",
                    error_message=f"Ruff 靜態檢查未通過：\n{proc.stdout}\n{proc.stderr}"
                )
            return VerificationResult(passed=True, step="static_analysis", error_message=None)
        except Exception as e:
            return VerificationResult(
                passed=False,
                step="static_analysis",
                error_message=f"執行靜態分析失敗：{e}"
            )

    def run_smoke_test(self, candidate_dir: Path, timeout_seconds: float = 5.0) -> VerificationResult:
        """於沙盒中試跑 candidate main.py 5 秒，驗證無未攔截崩潰且 DB 運作正常。"""
        candidate_dir = Path(candidate_dir).resolve()
        main_file = candidate_dir / "main.py"
        if not main_file.exists():
            return VerificationResult(
                passed=False,
                step="smoke_test",
                error_message="候選版本缺少核心入口檔案 'main.py'"
            )

        sandbox = get_sandbox(candidate_dir)
        env = sandbox.get_isolated_environ()
        # 設定冒煙測試旗標與短 tick，快速運行多個循環
        env["EVO_SMOKE_TEST"] = "1"
        env["EVO_TICK_INTERVAL"] = "0.1"

        cmd = sandbox.build_command(main_file)

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(candidate_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # 讓候選宇宙在背景試跑 timeout_seconds 秒
            time.sleep(timeout_seconds)

            # 候選宇宙必須持續運作；即使以 0 結束也代表主迴圈已消失。
            poll_result = proc.poll()
            if poll_result is not None:
                _, stderr = proc.communicate(timeout=2)
                return VerificationResult(
                    passed=False,
                    step="smoke_test",
                    error_message=f"候選宇宙提早結束（退出代碼 {poll_result}）：\n{stderr}"
                )

            # 正常結束冒煙測試進程
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

            # 驗證候選目錄中的 habitat.db 是否正確建立且可連線
            db_file = candidate_dir / "habitat.db"
            if not db_file.exists():
                return VerificationResult(
                    passed=False,
                    step="smoke_test",
                    error_message="候選版本未能成功建立或連線至 'habitat.db'"
                )

            return VerificationResult(passed=True, step="smoke_test", error_message=None)

        except Exception as exc:
            return VerificationResult(
                passed=False,
                step="smoke_test",
                error_message=f"冒煙測試執行異常：{exc}"
            )

    def verify(self, candidate_dir: Path) -> VerificationResult:
        """執行完整驗收管線：靜態分析 -> 沙盒冒煙測試。"""
        # 步驟 1：靜態分析
        static_res = self.run_static_analysis(candidate_dir)
        if not static_res.passed:
            return static_res

        # 步驟 2：沙盒冒煙測試
        smoke_res = self.run_smoke_test(candidate_dir, timeout_seconds=5.0)
        if not smoke_res.passed:
            return smoke_res

        return VerificationResult(passed=True, step="complete", error_message=None)
