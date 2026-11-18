# my_website 管理ツール（GUI）
# 起動は menu.bat をダブルクリックする。

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$root    = $PSScriptRoot
$mainDir = Join-Path $root 'main'
$py      = Join-Path $mainDir '.venv\Scripts\python.exe'
$port    = 8000
$url     = "http://127.0.0.1:$port"
$utf8    = New-Object System.Text.UTF8Encoding($false)

$colGreen  = [System.Drawing.Color]::FromArgb(0, 130, 60)
$colRed    = [System.Drawing.Color]::FromArgb(200, 40, 40)
$colGray   = [System.Drawing.Color]::FromArgb(110, 110, 110)
$colOrange = [System.Drawing.Color]::FromArgb(210, 120, 0)

$script:serverPid     = $null
$script:startingUntil = [datetime]::MinValue
$script:job           = $null
$script:tick          = 0

# ---------- 画面 ----------
$form = New-Object System.Windows.Forms.Form
$form.Text          = 'my_website 管理'
$form.ClientSize    = New-Object System.Drawing.Size(640, 480)
$form.MinimumSize   = New-Object System.Drawing.Size(520, 360)
$form.StartPosition = 'CenterScreen'
$form.Font          = New-Object System.Drawing.Font('Yu Gothic UI', 10)

$lblServer = New-Object System.Windows.Forms.Label
$lblServer.Location = New-Object System.Drawing.Point(16, 14)
$lblServer.Size     = New-Object System.Drawing.Size(608, 28)
$lblServer.Font     = New-Object System.Drawing.Font('Yu Gothic UI', 13, [System.Drawing.FontStyle]::Bold)
$lblServer.Anchor   = 'Top,Left,Right'

function New-Button($text, $x) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text     = $text
    $b.Location = New-Object System.Drawing.Point($x, 52)
    $b.Size     = New-Object System.Drawing.Size(144, 40)
    $b.Anchor   = 'Top,Left'
    return $b
}
$btnStart = New-Button 'サーバー起動'  16
$btnStop  = New-Button 'サーバー停止' 168
$btnTest  = New-Button 'テスト実行'   320
$btnSave  = New-Button 'GitHubへ保存' 472

$lblResult = New-Object System.Windows.Forms.Label
$lblResult.Location = New-Object System.Drawing.Point(16, 104)
$lblResult.Size     = New-Object System.Drawing.Size(608, 24)
$lblResult.Font     = New-Object System.Drawing.Font('Yu Gothic UI', 10, [System.Drawing.FontStyle]::Bold)
$lblResult.Anchor   = 'Top,Left,Right'
$lblResult.ForeColor = $colGray
$lblResult.Text     = '結果: （まだ実行していません）'

$txt = New-Object System.Windows.Forms.TextBox
$txt.Location   = New-Object System.Drawing.Point(16, 134)
$txt.Size       = New-Object System.Drawing.Size(608, 330)
$txt.Anchor     = 'Top,Bottom,Left,Right'
$txt.Multiline  = $true
$txt.ReadOnly   = $true
$txt.ScrollBars = 'Both'
$txt.WordWrap   = $false
$txt.Font       = New-Object System.Drawing.Font('Consolas', 9)
$txt.BackColor  = [System.Drawing.Color]::White

$form.Controls.AddRange(@($lblServer, $btnStart, $btnStop, $btnTest, $btnSave, $lblResult, $txt))

# ---------- 補助関数 ----------
function Show-Msg($text, $buttons = 'OK', $icon = 'Information') {
    return [System.Windows.Forms.MessageBox]::Show($form, $text, $form.Text, $buttons, $icon)
}

function Add-Line($line) {
    $txt.AppendText($line + "`r`n")
}

function Set-Result($text, $color) {
    $lblResult.Text      = $text
    $lblResult.ForeColor = $color
}

function Test-PortListening {
    try {
        foreach ($ep in [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()) {
            if ($ep.Port -eq $port) { return $true }
        }
    } catch { }
    return $false
}

function Update-Buttons {
    $busy = ($null -ne $script:job)
    $btnTest.Enabled = -not $busy
    $btnSave.Enabled = -not $busy
}

function Update-Server {
    $running  = Test-PortListening
    $starting = ((Get-Date) -lt $script:startingUntil)
    if ($running) {
        $script:startingUntil = [datetime]::MinValue
        $starting = $false
        $lblServer.Text      = "サーバー: ● 起動中   $url"
        $lblServer.ForeColor = $colGreen
    } elseif ($starting) {
        $lblServer.Text      = 'サーバー: ● 起動処理中...'
        $lblServer.ForeColor = $colOrange
    } else {
        $script:serverPid    = $null
        $lblServer.Text      = 'サーバー: ● 停止中'
        $lblServer.ForeColor = $colGray
    }
    $btnStart.Enabled = (-not $running) -and (-not $starting)
    $btnStop.Enabled  = ($running -or $starting)
}

# ---------- 裏で実行して出力を欄に流す ----------
function Start-Work($title, $file, $arguments, $workDir, $onDone) {
    if ($script:job) { return }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName               = $file
    $psi.Arguments              = $arguments
    $psi.WorkingDirectory       = $workDir
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.StandardOutputEncoding = $utf8
    $psi.StandardErrorEncoding  = $utf8
    $psi.EnvironmentVariables['PYTHONUTF8']         = '1'
    $psi.EnvironmentVariables['PYTHONUNBUFFERED']   = '1'
    $psi.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'

    $txt.Clear()
    Set-Result "$title 実行中..." $colOrange
    $proc = [System.Diagnostics.Process]::Start($psi)
    $script:job = @{
        Proc   = $proc
        Out    = $proc.StandardOutput.ReadLineAsync()
        Err    = $proc.StandardError.ReadLineAsync()
        Lines  = New-Object 'System.Collections.Generic.List[string]'
        OnDone = $onDone
        Title  = $title
    }
    Update-Buttons
}

function Read-JobStream($j, $key) {
    $reader = if ($key -eq 'Out') { $j.Proc.StandardOutput } else { $j.Proc.StandardError }
    $count = 0
    while ($j[$key] -and $j[$key].IsCompleted -and $count -lt 300) {
        $line = $j[$key].Result
        if ($null -eq $line) {
            $j[$key] = $null
        } else {
            Add-Line $line
            $j.Lines.Add($line)
            $j[$key] = $reader.ReadLineAsync()
        }
        $count++
    }
}

function Update-Job {
    $j = $script:job
    if (-not $j) { return }
    try {
        Read-JobStream $j 'Out'
        Read-JobStream $j 'Err'
        if ((-not $j.Out) -and (-not $j.Err)) {
            $j.Proc.WaitForExit()
            $code = $j.Proc.ExitCode
            $script:job = $null
            Update-Buttons
            & $j.OnDone $code $j.Lines
        }
    } catch {
        $script:job = $null
        Update-Buttons
        Set-Result ("エラー: " + $_.Exception.Message) $colRed
    }
}

# ---------- 操作 ----------
function Start-Server {
    if (-not (Test-Path $py)) {
        Show-Msg "仮想環境が見つかりません:`n$py`nREADME.md のセットアップ手順を先に実行してください。" 'OK' 'Warning' | Out-Null
        return
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName         = $py
    $psi.Arguments        = '-m uvicorn main:app --reload'
    $psi.WorkingDirectory = $mainDir
    $psi.UseShellExecute  = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $script:serverPid     = $p.Id
    $script:startingUntil = (Get-Date).AddSeconds(20)
    Update-Server
}

function Stop-Server {
    $ids = New-Object 'System.Collections.Generic.List[int]'
    if ($script:serverPid) { $ids.Add([int]$script:serverPid) }
    try {
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
            ForEach-Object { $ids.Add([int]$_.OwningProcess) }
    } catch { }
    foreach ($id in ($ids | Select-Object -Unique)) {
        if ($id -gt 4) { & taskkill /PID $id /T /F 2>&1 | Out-Null }
    }
    $script:startingUntil = [datetime]::MinValue
    Start-Sleep -Milliseconds 500
    Update-Server
}

function Start-Tests {
    if (-not (Test-Path $py)) {
        Show-Msg "仮想環境が見つかりません:`n$py" 'OK' 'Warning' | Out-Null
        return
    }
    Start-Work 'テスト' $py '-m unittest discover -s tests -q' $mainDir {
        param($code, $lines)
        $ran = $lines | Where-Object { $_ -match '^Ran \d+ test' } | Select-Object -Last 1
        $res = $lines | Where-Object { $_ -match '^(OK|FAILED)' }  | Select-Object -Last 1
        if (-not $res) { $res = "終了コード $code" }
        $text = "テスト結果: $res"
        if ($ran) { $text += "  ($ran)" }
        if ($code -eq 0) { Set-Result $text $colGreen } else { Set-Result $text $colRed }
    }
}

function Start-Save {
    $status = (& git -C $root status --short 2>&1) -join "`n"
    if ([string]::IsNullOrWhiteSpace($status)) {
        Show-Msg '保存する変更がありません。' 'OK' 'Information' | Out-Null
        return
    }
    $shown = ($status -split "`n" | Select-Object -First 15) -join "`n"
    if (($status -split "`n").Count -gt 15) { $shown += "`n..." }
    $answer = Show-Msg ("次の変更を GitHub に保存（add → commit → push）しますか？`n`n" + $shown) 'YesNo' 'Question'
    if ($answer -ne 'Yes') { return }

    $stamp = Get-Date -Format 'yyyyMMddHHmmss'
    Start-Work 'GitHub保存' 'cmd.exe' "/c ""git add . && git commit -m $stamp && git push""" $root {
        param($code, $lines)
        if ($code -eq 0) { Set-Result 'GitHubへの保存が完了しました' $colGreen }
        else             { Set-Result "保存に失敗しました（終了コード $code）" $colRed }
    }
}

# ---------- イベント ----------
function Invoke-Guarded($action) {
    try { & $action } catch { Show-Msg $_.Exception.Message 'OK' 'Error' | Out-Null }
}

$btnStart.Add_Click({ Invoke-Guarded { Start-Server } })
$btnStop.Add_Click({  Invoke-Guarded { Stop-Server } })
$btnTest.Add_Click({  Invoke-Guarded { Start-Tests } })
$btnSave.Add_Click({  Invoke-Guarded { Start-Save } })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 200
$timer.Add_Tick({
    Update-Job
    $script:tick++
    if ($script:tick % 5 -eq 0) { Update-Server }
})

$form.Add_Shown({
    Update-Server
    Update-Buttons
    $timer.Start()
    # 非表示の PowerShell から起動すると他のウィンドウの後ろに隠れるため、手前に出す
    $form.TopMost = $true
    $form.Activate()
    $form.TopMost = $false
})

$form.Add_FormClosing({
    param($sender, $e)
    if ($script:job) {
        Show-Msg '処理を実行中です。終わってから閉じてください。' 'OK' 'Warning' | Out-Null
        $e.Cancel = $true
        return
    }
    if (Test-PortListening) {
        $answer = Show-Msg 'サーバーが起動中です。サーバーも停止しますか？' 'YesNoCancel' 'Question'
        if ($answer -eq 'Cancel') { $e.Cancel = $true; return }
        if ($answer -eq 'Yes') { Stop-Server }
    }
    $timer.Stop()
})

[void]$form.ShowDialog()
