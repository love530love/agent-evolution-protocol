$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = (Join-Path $root 'src')
python -c "import json,os; from agent_evolution_protocol.coordination.desktop_discovery import persist; print(json.dumps(persist(os.path.join(r'$root','coordination')), ensure_ascii=False, indent=2))"
