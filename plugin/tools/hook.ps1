param(
    [Parameter(Mandatory)]
    [ValidateSet("postToolUse", "preToolUse", "userPromptSubmitted")]
    [string] $Event
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$payload = [Console]::In.ReadToEnd()
[string[]] $options = switch ($Event) {
    "postToolUse" { @("--post-hook") }
    "preToolUse" { @("--hook") }
    "userPromptSubmitted" { @("--record-acting") }
}
if ($env:AGENT_ORG_HOOK_MODE) {
    $options += @("--mode", $env:AGENT_ORG_HOOK_MODE)
}
$options += @("--provider-hook", $PSCommandPath)
$payload | python -X utf8 (Join-Path $PSScriptRoot "owner_validator.py") @options
exit $LASTEXITCODE
