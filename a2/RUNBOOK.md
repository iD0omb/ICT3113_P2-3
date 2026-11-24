# A2 speed-test runbook

Settings (fixed, `a2/results/speed_setup.json`): S = 2.47 s, 729 tickets/hour, 5 min run, 300 s timeout,
6 min pause. About 61 tickets per run; JMeter stays up about 11 min. Nine runs in total.

## Once: networking
Laptop: Ethernet to the router, Wi-Fi off, network profile Private, `ipconfig` for both Ethernet IPv4 addresses
(not `vEthernet`), then in an administrator PowerShell:

    New-NetFirewallRule -DisplayName "Ticket triage 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -RemoteAddress <desktop IP> -Action Allow -Profile Private

Mains power, Best performance, ASUS Performance profile, no sleep on either machine.

Desktop:

    Test-NetConnection <laptop IP> -Port 5000                                   # TcpTestSucceeded : True
    curl.exe -o NUL -s -w "%{time_total}\n" http://<laptop IP>:5000/health       # note the time for Slide 3
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
    .\jmeter\run_a2_speed.ps1 -Config llama -Run 1 -SutHost <laptop IP> -DryRun  # expect S = 2.47 s; 729 tickets/hour ...

## Per configuration: set .env on the laptop, then `docker compose up -d --build`

| Config | MODEL | CASCADE_SMALL | CASCADE_ESCALATE | CASCADE_SETTING |
|---|---|---|---|---|
| llama | llama3.1:8b | (empty) | (empty) | (empty) |
| cascade-A | llama3.1:8b | qwen3:4b | Credit card,Debt collection | A |
| cascade-B | llama3.1:8b | qwen3:4b | Credit reporting,Debt collection | B |

Commit nothing in `app/` or `docker-compose.yaml` between runs (the prepare script refuses uncommitted changes).

## Per run (N = 1, 2, 3), in this order: llama r1-r3, cascade-A r1-r3, cascade-B r1-r3
1. Laptop: `python scripts/a2_prepare_run.py --config <config> --run N`. Continue only on `READY`.
2. Desktop: `.\jmeter\run_a2_speed.ps1 -Config <config> -Run N -SutHost <laptop IP>`. Do not touch either
   machine until it prints `Done` (~11 min).
3. Laptop: `python scripts/a2_save_log.py --run-id speed-<config>-rN-warmup --run-id speed-<config>-rN --out a2/results/speed/<config>/runN/requests.log`
4. Note any warning or odd output in `a2/results/speed/run_notes.md` (what, which run, time).

A failed or interrupted run: do not delete anything. Write it down in run_notes.md and ask before re-running.

## After all nine
Commit `a2/results/speed/` from both machines (laptop: prepare.json, ollama_ps.txt, requests.log;
desktop: results.jtl, jmeter.log, run.json). Then the speed analysis (to be written).
