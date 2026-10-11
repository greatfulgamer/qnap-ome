# QNAP-OME: LLM-Powered Dell Fleet Automation

## Intent

A lightweight, event-driven monitoring and automation stack for home labs with Dell PowerEdge servers. Runs on QNAP NAS (or any Linux box) to provide enterprise-grade fleet visibility without the overhead of full OpenManage Enterprise deployments.

### What This Solves

- **3-5 R730s** with iDRAC8 need centralized monitoring
- **Manual iDRAC logins** are tedious across multiple nodes
- **Firmware updates** scattered across individual consoles
- **Alert fatigue** from unstructured hardware warnings
- **LLM integration** for intelligent anomaly analysis and recommendations

### What This Is NOT

- ❌ Not a replacement for OpenManage Enterprise (OME)
- ❌ Not a full ITSM platform
- ❌ Not designed for 100+ node fleets
- ❌ Not pushing firmware updates automatically (yet)

### Philosophy

> **"Poll everything, analyze selectively, act intelligently"**

- **Redfish-first**: Native iDRAC API, zero agents on servers
- **Event-driven**: LLM only invoked when anomalies detected (95% compute savings)
- **Local fallback**: Cloud API fails? Local Ollama picks up the slack
- **Human-in-the-loop**: Recommendations, not autonomous actions (safety first)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    QNAP NAS (18c/32GB)                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ poll_idrac.py│──│parse_events.py│──│llm_analyzer.py│      │
│  │ (Redfish)    │  │(thresholds)  │  │(Ollama)      │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         │                │                │               │
│         ▼                ▼                ▼               │
│  ┌──────────────────────────────────────────────┐        │
│  │          notify.py (Telegram/Email/ntfy)     │        │
│  └──────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────┘
                        │
                        │ 1Gb dedicated management line
                        ▼
    ┌──────────┐  ┌──────────┐  ┌──────────┐
    │ R730 #1  │  │ R730 #2  │  │ R730 #3  │
    │ iDRAC8   │  │ iDRAC8   │  │ iDRAC8   │
    └──────────┘  └──────────┘  └──────────┘
```

### Data Flow

1. **Polling** (every 60s): Redfish API → JSON cache + SQLite
2. **Detection** (every 120s): Threshold checks → anomaly flags
3. **Analysis** (on-demand): Anomaly JSON → LLM → recommendations
4. **Notification** (immediate): Structured alerts → chosen channels

---

## Tech Stack

| Component | Technology | Why |
|-----------|------------|-----|
| **API** | Redfish (native iDRAC8) | No agents, vendor-standard, secure |
| **Language** | Python 3.9+ | Rich ecosystem, easy scripting |
| **LLM** | Ollama (qwen2.5:3B or similar) | Local inference, GPU/CPU hybrid |
| **Database** | SQLite | Zero-config, portable, sufficient for <20 nodes |
| **Notifications** | Telegram/Email/ntfy | Fast, reliable, mobile-friendly |
| **Dashboard** | Flask + HTML/JS | Simple, no frontend framework needed |

---

## Deployment Model

### Minimal (Home Lab)
- **QNAP** running all scripts as cron jobs
- **Ollama** container with GPU passthrough
- **No OME** required (optional layer later)

### Enhanced (Hybrid)
- **OME appliance** on one R730 for firmware/updates
- **QNAP** handles custom analytics + LLM intelligence
- **Both** talk to same iDRAC endpoints (no conflict)

### Future-Proof (Scale-Up)
- Add **Prometheus/Grafana** for time-series viz
- Integrate **Ansible** via `dell/dellemc-openmanage-ansible-modules`
- Push **firmware updates** through OME REST API

---

## Getting Started

### Prerequisites
- QNAP with SSH access (or any Linux box)
- iDRAC8 IPs reachable from QNAP (dedicated mgmt network preferred)
- Ollama installed (`curl -fsSL https://ollama.com/install.sh | sh`)
- Python 3.9+ with `requests`, `pyyaml`, `flask` packages

### Quick Start
```bash
# 1. Clone this repo
git clone https://github.com/YOUR_USERNAME/qnap-ome.git
cd qnap-ome

# 2. Configure servers
cp config/servers.yaml.example config/servers.yaml
# Edit with your iDRAC IPs, usernames, passwords

# 3. Set environment variables (or use .env file)
export IDRAC_PASSWORD_01="your_password_here"
export IDRAC_PASSWORD_02="..."

# 4. Test polling
python3 scripts/poll_idrac.py --test

# 5. Enable cron (see crontab.example)
crontab crontab.example

# 6. Start dashboard (optional)
python3 scripts/health_dashboard.py &
```

### First Run Checklist
- [ ] Can reach all iDRAC IPs from QNAP (`ping`, `curl`)
- [ ] Ollama responds (`curl http://localhost:11434/api/version`)
- [ ] Test script returns valid JSON for at least one server
- [ ] Notifications configured (Telegram bot token, email SMTP, etc.)
- [ ] Cron jobs active (`crontab -l`)
- [ ] Dashboard accessible (`http://qnap-ip:8080`)

---

## Configuration Guide

### servers.yaml
```yaml
servers:
  - name: r730-gpu-01
    idrac_ip: 192.168.100.11
    username: root
    password_env: IDRAC_PASSWORD_01
    
  - name: r730-gpu-02
    idrac_ip: 192.168.100.12
    username: root
    password_env: IDRAC_PASSWORD_02
```

### thresholds.yaml
```yaml
temperatures:
  cpu_warning: 80
  cpu_critical: 90
  ambient_warning: 35
  ambient_critical: 40

fans:
  rpm_low_warning: 2000
  rpm_high_warning: 15000
  
power:
  spike_threshold_watts: 500  # Sudden jump detection
```

### notifications.yaml
```yaml
telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
  chat_id: ${TELEGRAM_CHAT_ID}
  levels: [critical, warning]  # Send these alerts

email:
  smtp_server: smtp.gmail.com
  smtp_port: 587
  username: ${EMAIL_USER}
  password: ${EMAIL_PASS}
  recipients: [admin@example.com]
  levels: [critical]  # Only critical alerts

ntfy:
  topic: my-home-lab-alerts
  server: https://ntfy.sh
  levels: [info, warning, critical]
```

---

## LLM Integration

### Model Selection
| Model | VRAM | Speed (4GB GPU) | Use Case |
|-------|------|-----------------|----------|
| **qwen2.5:3b** | ~2GB | 50-80 tok/s | Balanced choice |
| **phi4-mini:3.8b** | ~2.5GB | 40-60 tok/s | Higher accuracy |
| **llama3.2:3b** | ~2GB | 50-70 tok/s | Good generalist |

### Prompt Engineering
The LLM receives:
1. **Context**: Recent sensor history (last 10 polls)
2. **Anomalies**: Current flagged issues
3. **Task**: Root cause analysis + recommendations

Example output:
```
## Analysis
Fan 3 RPM dropped to 1800 while CPU temp rose to 88°C. 
Pattern suggests fan failure, not thermal throttling.

## Root Cause Assessment
- Fan 3 bearing degradation (progressive RPM drop over 4 hours)
- CPU cooling compromised on socket 2

## Recommendations
1. IMMEDIATE: Schedule fan 3 replacement within 24h
2. Monitor CPU temp closely until replaced
3. Prepare spare fan (Dell PN: YC6H8 for R730)

## Urgency: HIGH
## Confidence: HIGH
```

---

## Security Considerations

### Network Segmentation
- **Management VLAN**: iDRACs only accessible from QNAP mgmt NIC
- **No internet exposure**: iDRAC ports never NATed outward
- **Firewall rules**: Block iDRAC traffic except Redfish (443) + SSH (22)

### Credential Management
- **Env vars or .env file**: Never hardcode passwords in scripts
- **iDRAC users**: Create dedicated read-only monitoring account
- **TLS verification**: Redfish uses HTTPS, validate certs in prod

### LLM Privacy
- **Local-only**: No server metrics sent to cloud APIs
- **Data minimization**: Only send anomaly summaries, not raw dumps
- **Retention**: Clear cached JSON after 24h (configurable)

---

## Monitoring & Maintenance

### Health Checks
```bash
# Check if cron jobs ran recently
tail -20 /shared/Cache/fleet-monitor/logs/poll.log

# Verify database integrity
sqlite3 data/fleet.db "SELECT COUNT(*) FROM sensor_readings;"

# Test LLM connectivity
curl http://localhost:11434/api/generate -d '{"model":"qwen2.5:3b","prompt":"test"}'
```

### Log Rotation
```bash
# Add to crontab (daily at 3am)
0 3 * * * find /shared/Cache/fleet-monitor/logs -name "*.log" -mtime +7 -delete
```

### Backup Strategy
- **Git**: Commit config changes (exclude secrets)
- **SQLite**: Daily dump to QNAP backup volume
- **Scripts**: Version-controlled in this repo

---

## Contributing

### Adding New Sensors
1. Edit `scripts/poll_idrac.py` → add Redfish endpoint
2. Update `thresholds.yaml` → define warning/critical values
3. Modify `templates/alert_prompt.txt` → include in LLM context

### Extending Notifications
1. Add provider to `notify.py` (e.g., Slack, Discord, Pushover)
2. Document in `README.md` with setup instructions
3. Add config example to `notifications.yaml.example`

### Integrating OME
1. Deploy OME appliance (OVA) on one R730
2. Point QNAP scripts at OME REST API instead of direct iDRAC
3. Use `dell/OpenManage-PowerShell-Modules` for firmware tasks

---

## Troubleshooting

### Common Issues

**"Connection refused" to iDRAC**
- Check physical connectivity (dedicated mgmt line?)
- Verify firewall rules (port 443 open?)
- Test manually: `curl -k https://idrac-ip/redfish/v1/`

**LLM not responding**
- Ollama container running? (`docker ps | grep ollama`)
- GPU passthrough working? (`nvidia-smi` inside container)
- Try CPU fallback: `ollama run qwen2.5:3b` (slower but functional)

**False positive alerts**
- Adjust thresholds in `thresholds.yaml`
- Increase polling interval (reduce noise)
- Add cooldown periods in `parse_events.py`

### Debug Mode
```bash
# Run scripts interactively with verbose logging
python3 scripts/poll_idrac.py --verbose --server r730-gpu-01

# Test LLM prompt manually
python3 scripts/llm_analyzer.py --debug --anomaly-file data/latest_anomaly.json
```

---

## Roadmap

### Phase 1 (Current) ✅
- [x] Redfish polling
- [x] Threshold-based anomaly detection
- [x] LLM-powered analysis
- [x] Multi-channel notifications
- [x] Basic web dashboard

### Phase 2 (Next)
- [ ] Firmware version tracking + update reminders
- [ ] Grafana integration (Prometheus exporter)
- [ ] Automated ticket creation (Jira, GitHub Issues)
- [ ] Predictive failure analysis (ML on historical data)

### Phase 3 (Future)
- [ ] Ansible integration for remediation playbooks
- [ ] OME REST API wrapper (hybrid mode)
- [ ] Multi-site support (geo-distributed labs)
- [ ] Mobile app (React Native wrapper for dashboard)

---

## License

MIT License - Free for personal and commercial use. See `LICENSE` file.

---

## Acknowledgments

- **Dell Technologies**: For robust iDRAC Redfish implementation
- **Ollama**: Making local LLM inference accessible
- **Home lab community**: Inspiration from r/homelab and r/DataHoarder

---

## Contact

Built for a retro-gaming + LLM homelab. Questions? Open an issue or PR.

*"Monitoring shouldn't require a datacenter budget."*
