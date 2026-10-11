# Start the qmd HTTP server, run the given command with MAS_QMD_URL set, then stop ONLY the server this script started. (Windows)
#
# Usage (from the repo folder; everything after the script name is the command to run):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\with_qmd_server.ps1 uv run python scripts/probe_qmd_http.py
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\with_qmd_server.ps1 uv run --extra strands python scripts/run_strands_demo.py samples/db_inc_sw_engineer_ax.txt --max 5
#
# Why start, run and stop in one script:
#   Windows OpenSSH appears to kill processes started from an SSH session when the session ends
#   (a `qmd mcp --http --daemon` vanished with the connection on 2026-10-04). So everything runs inside one connection.
#
# The server listens on IPv6 only (::1) on this machine, so the URL uses `localhost`, not 127.0.0.1.
# Safety: only the PID started here is stopped. node.exe is never killed wholesale (Claude Code also runs on node).
#   The server is stopped in `finally` even if the command fails. It binds to localhost only and has no authentication.
#
# This file is ASCII on purpose: Windows PowerShell 5.1 reads UTF-8 without a BOM as ANSI, which garbles Korean text
# and can swallow quotes or newlines. Korean documentation lives in docs/experiments/002 and scripts/probe_qmd_http.py.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8  # keep the UTF-8 Korean output of Python readable
if (-not $Command -or $Command.Count -eq 0) { throw 'give the command to run after the script name' }

$node = (Get-Command node).Source
# npm's qmd.cmd sits next to node_modules; call the entry script with node directly (no cmd.exe in between).
$qmdEntry = Join-Path (Split-Path (Get-Command qmd).Source) 'node_modules\@tobilu\qmd\bin\qmd'
$log = Join-Path $env:USERPROFILE '.cache\qmd\mcp-probe.log'
$base = 'http://localhost:8181'

$server = Start-Process -FilePath $node -ArgumentList @($qmdEntry, 'mcp', '--http') -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err"
Write-Host "qmd server started: PID $($server.Id)"
try {
    $ready = $false
    for ($i = 0; $i -lt 90; $i++) {
        try { Invoke-RestMethod -Uri "$base/health" -TimeoutSec 2 | Out-Null; $ready = $true; break }
        catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "qmd server did not answer $base/health within 90 seconds (log: $log)" }
    Write-Host "server ready after about $i seconds; running: $($Command -join ' ')"

    $env:PYTHONUTF8 = '1'
    $env:MAS_QMD_URL = $base
    if ($Command.Count -gt 1) { & $Command[0] $Command[1..($Command.Count - 1)] } else { & $Command[0] }
    if ($LASTEXITCODE -ne 0) { Write-Host "command exited with code $LASTEXITCODE" }
}
finally {
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    Write-Host "qmd server stopped: PID $($server.Id)"
}
