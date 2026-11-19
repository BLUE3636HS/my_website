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
$maxLogChars = 1000000

$colGreen  = [System.Drawing.Color]::FromArgb(0, 130, 60)
$colRed    = [System.Drawing.Color]::FromArgb(200, 40, 40)
$colGray   = [System.Drawing.Color]::FromArgb(110, 110, 110)
$colOrange = [System.Drawing.Color]::FromArgb(210, 120, 0)

$script:server        = $null   # このウィンドウから起動したサーバー
$script:startingUntil = [datetime]::MinValue
$script:job           = $null   # テスト・保存の実行中の処理
$script:tick          = 0

# ---------- 画面 ----------
$form = New-Object System.Windows.Forms.Form
$form.Text          = 'my_website 管理'
$form.ClientSize    = New-Object System.Drawing.Size(720, 540)
$form.MinimumSize   = New-Object System.Drawing.Size(560, 400)
$form.StartPosition = 'CenterScreen'
$form.Font          = New-Object System.Drawing.Font('Yu Gothic UI', 10)

$lblServer = New-Object System.Windows.Forms.Label
$lblServer.Location = New-Object System.Drawing.Point(16, 14)
$lblServer.Size     = New-Object System.Drawing.Size(688, 28)
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
$lblResult.Size     = New-Object System.Drawing.Size(688, 24)
$lblResult.Font     = New-Object System.Drawing.Font('Yu Gothic UI', 10, [System.Drawing.FontStyle]::Bold)
$lblResult.Anchor   = 'Top,Left,Right'
$lblResult.ForeColor = $colGray
$lblResult.Text     = '結果: （まだ実行していません）'

function New-LogBox {
    $t = New-Object System.Windows.Forms.TextBox
    $t.Dock       = 'Fill'
    $t.Multiline  = $true
    $t.ReadOnly   = $true
    $t.ScrollBars = 'Both'
    $t.WordWrap   = $false
    $t.Font       = New-Object System.Drawing.Font('Consolas', 9)
    $t.BackColor  = [System.Drawing.Color]::White
    return $t
}
$txtServer = New-LogBox
$txt       = New-LogBox

$tabs = New-Object System.Windows.Forms.TabControl
$tabs.Location = New-Object System.Drawing.Point(16, 134)
$tabs.Size     = New-Object System.Drawing.Size(688, 390)
$tabs.Anchor   = 'Top,Bottom,Left,Right'
$tabServer = New-Object System.Windows.Forms.TabPage
$tabServer.Text = 'サーバーログ'
$tabServer.Controls.Add($txtServer)
$tabWork = New-Object System.Windows.Forms.TabPage
$tabWork.Text = 'テスト・保存'
$tabWork.Controls.Add($txt)
$tabs.TabPages.AddRange(@($tabServer, $tabWork))

$form.Controls.AddRange(@($lblServer, $btnStart, $btnStop, $btnTest, $btnSave, $lblResult, $tabs))

# ---------- 補助関数 ----------
function Show-Msg($text, $buttons = 'OK', $icon = 'Information') {
    return [System.Windows.Forms.MessageBox]::Show($form, $text, $form.Text, $buttons, $icon)
}

function Add-Line($box, $line) {
    if ($box.TextLength -gt $maxLogChars) {
        # 長くなりすぎたら古い半分を捨てる
        $box.Text = $box.Text.Substring($box.TextLength / 2)
    }
    $box.AppendText($line + "`r`n")
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
    $starting = ((Get-Date) -lt $script:startingUntil) -and ($null -ne $script:server)
    if ($running) {
        $script:startingUntil = [datetime]::MinValue
        $starting = $false
        $lblServer.Text      = "サーバー: ● 起動中   $url"
        $lblServer.ForeColor = $colGreen
    } elseif ($starting) {
        $lblServer.Text      = 'サーバー: ● 起動処理中...'
        $lblServer.ForeColor = $colOrange
    } else {
        $lblServer.Text      = 'サーバー: ● 停止中'
        $lblServer.ForeColor = $colGray
    }
    $btnStart.Enabled = (-not $running) -and (-not $starting) -and ($null -eq $script:server)
    $btnStop.Enabled  = $running -or $starting -or ($null -ne $script:server)
}

# ---------- 子プロセスを出力付きで起動し、出力を欄に流す ----------
function Start-Redirected($file, $arguments, $workDir) {
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
    $psi.EnvironmentVariables['PYTHONUTF8']          = '1'
    $psi.EnvironmentVariables['PYTHONUNBUFFERED']    = '1'
    $psi.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $proc = [System.Diagnostics.Process]::Start($psi)
    return @{
        Proc  = $proc
        Out   = $proc.StandardOutput.ReadLineAsync()
        Err   = $proc.StandardError.ReadLineAsync()
        Lines = New-Object 'System.Collections.Generic.List[string]'
    }
}

# 読み終わった行を欄に書き出す。出力が両方とも終わったら $true を返す
function Read-Output($state, $box, $keepLines) {
    foreach ($key in 'Out', 'Err') {
        $reader = if ($key -eq 'Out') { $state.Proc.StandardOutput } else { $state.Proc.StandardError }
        $count = 0
        while ($state[$key] -and $state[$key].IsCompleted -and $count -lt 300) {
            $line = $state[$key].Result
            if ($null -eq $line) {
                $state[$key] = $null
            } else {
                Add-Line $box $line
                if ($keepLines) { $state.Lines.Add($line) }
                $state[$key] = $reader.ReadLineAsync()
            }
            $count++
        }
    }
    return ((-not $state.Out) -and (-not $state.Err))
}

function Start-Work($title, $file, $arguments, $workDir, $onDone) {
    if ($script:job) { return }
    $txt.Clear()
    $tabs.SelectedTab = $tabWork
    Set-Result "$title 実行中..." $colOrange
    $state = Start-Redirected $file $arguments $workDir
    $state.OnDone = $onDone
    $script:job = $state
    Update-Buttons
}

function Update-Job {
    $j = $script:job
    if (-not $j) { return }
    try {
        if (Read-Output $j $txt $true) {
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

function Update-ServerLog {
    $s = $script:server
    if (-not $s) { return }
    try {
        if (Read-Output $s $txtServer $false) {
            $s.Proc.WaitForExit()
            if ($s.Stopping) { Add-Line $txtServer '---- サーバーを停止しました ----' }
            else { Add-Line $txtServer ("---- サーバーが終了しました（終了コード " + $s.Proc.ExitCode + "） ----") }
            $script:server = $null
            $script:startingUntil = [datetime]::MinValue
            Update-Server
        }
    } catch {
        Add-Line $txtServer ("---- ログの読み取りでエラー: " + $_.Exception.Message + " ----")
        $script:server = $null
        Update-Server
    }
}

# ---------- 操作 ----------
function Start-Server {
    if (-not (Test-Path $py)) {
        Show-Msg "仮想環境が見つかりません:`n$py`nREADME.md のセットアップ手順を先に実行してください。" 'OK' 'Warning' | Out-Null
        return
    }
    if ($script:server) { return }
    $txtServer.Clear()
    $tabs.SelectedTab = $tabServer
    Add-Line $txtServer "---- サーバーを起動します: $url ----"
    $script:server        = Start-Redirected $py '-m uvicorn main:app --reload' $mainDir
    $script:startingUntil = (Get-Date).AddSeconds(20)
    Update-Server
}

function Stop-Server {
    $ids = New-Object 'System.Collections.Generic.List[int]'
    if ($script:server) {
        $script:server.Stopping = $true
        $ids.Add([int]$script:server.Proc.Id)
    }
    try {
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
            ForEach-Object { $ids.Add([int]$_.OwningProcess) }
    } catch { }
    foreach ($id in ($ids | Select-Object -Unique)) {
        if ($id -gt 4) { & taskkill /PID $id /T /F 2>&1 | Out-Null }
    }
    $script:startingUntil = [datetime]::MinValue
    Start-Sleep -Milliseconds 500
    Update-ServerLog
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
    Update-ServerLog
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
    if ($script:server) {
        # ログをこのウィンドウで受けているため、閉じるとサーバーも止める
        $answer = Show-Msg 'このウィンドウを閉じるとサーバーも停止します。閉じますか？' 'OKCancel' 'Question'
        if ($answer -ne 'OK') { $e.Cancel = $true; return }
        Stop-Server
    } elseif (Test-PortListening) {
        $answer = Show-Msg 'サーバーが起動中です。サーバーも停止しますか？' 'YesNoCancel' 'Question'
        if ($answer -eq 'Cancel') { $e.Cancel = $true; return }
        if ($answer -eq 'Yes') { Stop-Server }
    }
    $timer.Stop()
})

[void]$form.ShowDialog()
