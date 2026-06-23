# Set authorization and base URL
# $env:ANTHROPIC_AUTH_TOKEN=$env:CBORG_API_KEY
$env:ANTHROPIC_BASE_URL="https://api.cborg.lbl.gov"

# Model selection -- set to latest version of each model
$env:ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5"
$env:ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-4-6"
$env:ANTHROPIC_DEFAULT_OPUS_MODEL="claude-opus-4-8"

# Default conversation model
$env:ANTHROPIC_MODEL="claude-sonnet-4-6"

# Default subagent model
$env:CLAUDE_CODE_SUBAGENT_MODEL="claude-haiku-4-5"

# Recommended setting
$env:DISABLE_NON_ESSENTIAL_MODEL_CALLS="1"

# Recommended setting
$env:DISABLE_TELEMETRY="1"

# Recommended setting for compatibility
$env:CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS="1"

# Recommended setting to reduce model throttling
$env:CLAUDE_CODE_MAX_OUTPUT_TOKENS="64000"

# Recommended setting to suppress terminal flicker
$env:CLAUDE_CODE_NO_FLICKER="1"

C:\Users\lab\.local\bin\claude.exe