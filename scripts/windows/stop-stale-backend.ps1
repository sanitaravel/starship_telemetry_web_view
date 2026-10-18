# Stop backend processes left over from an earlier run that still hold the port.
#
# `uvicorn --reload` runs a reloader and a worker process (multiprocessing
# spawn) several levels below the backend window. Closing that window kills
# only the top of the tree; the worker survives, keeps listening, and Windows
# lets a new backend bind the same port next to it, so the browser can end up
# talking to old code. This finds every process behind a listener on the port
# (including a worker whose parent has already exited) and stops it.

param([int]$Port = 8000)

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
if (-not $listeners) { return }

$all = Get-CimInstance Win32_Process
$stale = @{}
foreach ($owner in $listeners) {
    $proc = $all | Where-Object { $_.ProcessId -eq $owner }
    if ($proc) { $stale[$proc.ProcessId] = $proc }
    # Workers spawned by this owner (they hold the socket if the owner exited)
    $all | Where-Object { $_.CommandLine -match "spawn_main\(parent_pid=$owner\b" } |
        ForEach-Object { $stale[$_.ProcessId] = $_ }
}

foreach ($proc in $stale.Values) {
    Write-Host "  Stopping leftover backend process $($proc.ProcessId) ($($proc.Name)) on port $Port" -ForegroundColor Yellow
    & taskkill /PID $proc.ProcessId /T /F *> $null
}
