# Uses only the official VK API. Default mode checks access without deleting.
param([switch]$Delete)
$ErrorActionPreference = 'Stop'
$ownerId = -206233289
$script:vkToken = $null

function Invoke-TamaraVk {
    param([string]$Method, [hashtable]$Parameters = @{})
    $body = @{ access_token = $script:vkToken; v = '5.199' }
    foreach ($name in $Parameters.Keys) { $body[$name] = $Parameters[$name] }
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        Start-Sleep -Milliseconds 450
        try {
            $result = Invoke-RestMethod -Method Post -Uri ("https://api.vk.com/method/" + $Method) -Body $body -TimeoutSec 30
        } catch {
            if ($attempt -eq 4) { throw 'VK network request failed. No request details are printed.' }
            Start-Sleep -Seconds ([math]::Pow(2, $attempt))
            continue
        }
        if ($null -ne $result.error) {
            $code = [int]$result.error.error_code
            if (($code -in @(6, 10)) -and $attempt -lt 4) {
                Start-Sleep -Seconds ([math]::Pow(2, $attempt))
                continue
            }
            # Do not print the full VK error object: it contains request parameters.
            throw ("VK " + $Method + " error " + $code + ": " + ([string]$result.error.error_msg).Replace($script:vkToken, '[REDACTED]'))
        }
        if ($null -eq $result.response) { throw 'VK response missing.' }
        return $result.response
    }
}

try {
    $secureToken = Read-Host 'Paste the USER token with wall permission (input hidden)' -AsSecureString
    $credential = New-Object System.Management.Automation.PSCredential('VK', $secureToken)
    $script:vkToken = $credential.GetNetworkCredential().Password.Trim()
    if (-not $script:vkToken) { throw 'Token is empty.' }
    $users = @(Invoke-TamaraVk 'users.get')
    if ($users.Count -ne 1 -or -not $users[0].id) { throw 'User authorization was not verified.' }
    $result = Invoke-TamaraVk 'groups.getById' @{ group_ids = 'tamara_vkurse' }
    if ($null -ne $result.groups) { $groups = @($result.groups) } else { $groups = @($result) }
    if ($groups.Count -ne 1 -or [long]$groups[0].id -ne 206233289 -or $groups[0].screen_name -ne 'tamara_vkurse') {
        throw 'Target community mismatch. Stopped.'
    }
    $page = Invoke-TamaraVk 'wall.get' @{ owner_id = $ownerId; filter = 'all'; count = 100 }
    Write-Host ("Target: tamara_vkurse (206233289). Published posts: " + $page.count)
    if (-not $Delete) {
        Write-Host 'Access check passed. Nothing deleted. Run again with -Delete to clear this wall.'
        exit 0
    }
    $deleted = 0
    $seen = @{}
    while ([long]$page.count -gt 0) {
        $items = @($page.items)
        if ($items.Count -eq 0) { throw 'Wall reports posts but returns no items. Stopped.' }
        foreach ($post in $items) {
            if ([long]$post.owner_id -ne $ownerId -or $post.post_type -notin @('post', 'copy')) {
                throw 'Unexpected post owner or type. Stopped.'
            }
            $postId = [long]$post.id
            if ($seen.ContainsKey($postId)) { throw 'VK returned an already deleted post. Stopped; check the wall before restarting.' }
            $response = Invoke-TamaraVk 'wall.delete' @{ owner_id = $ownerId; post_id = $postId }
            if ($response -ne 1) { throw ("Deletion was not confirmed for " + $postId) }
            $seen[$postId] = $true
            $deleted++
            Write-Host ("Deleted " + $deleted + ": wall" + $ownerId + "_" + $postId)
        }
        $page = Invoke-TamaraVk 'wall.get' @{ owner_id = $ownerId; filter = 'all'; count = 100 }
    }
    $remaining = Invoke-TamaraVk 'wall.get' @{ owner_id = $ownerId; filter = 'all'; count = 1 }
    if ([long]$remaining.count -ne 0) { throw 'Wall is not empty. Stopped.' }
    Write-Host ("SUCCESS: deleted " + $deleted + " posts. Verified: published wall is empty.")
} catch {
    Write-Host ('STOPPED: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    $script:vkToken = $null
    $credential = $null
    $secureToken = $null
}
