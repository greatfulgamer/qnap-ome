#!/usr/bin/env python3
"""
QNAP-OME: LLM-powered Anomaly Analysis
Uses Ollama to generate intelligent root cause analysis and recommendations.

Falls back to rule-based analysis if LLM unavailable.
"""

import os
import sys
import json
import yaml
import logging
from datetime import datetime
from pathlib import Path
import requests

DATA_DIR = Path(__file__).parent.parent / "data"
CONFIG_DIR = Path(__file__).parent.parent / "config"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
DEFAULT_MODEL = os.environ.get('LLM_MODEL', 'qwen2.5:3b')


def load_anomalies():
    """Load current anomalies from file."""
    anomaly_file = DATA_DIR / "current_anomalies.json"
    if not anomaly_file.exists():
        logger.error("No anomalies file found. Run parse_events.py first.")
        return None
    
    with open(anomaly_file) as f:
        return json.load(f)


def load_sensor_history(server_name, limit=10):
    """Load recent sensor history for context."""
    import sqlite3
    db_file = DATA_DIR / "fleet.db"
    
    if not db_file.exists():
        return []
    
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT timestamp, temp_cpu, temp_ambient, fan_rpm_avg, power_watts
        FROM sensor_readings
        WHERE server_name = ? AND success = 1
        ORDER BY timestamp DESC LIMIT ?
    ''', (server_name, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    history = []
    for row in rows:
        history.append({
            'timestamp': row[0],
            'cpu_temp': row[1],
            'ambient_temp': row[2],
            'avg_fan_rpm': row[3],
            'power_watts': row[4]
        })
    
    return history


def build_prompt(anomalies_data):
    """Build LLM prompt with context and anomalies."""
    anomalies = anomalies_data['anomalies']
    
    # Group by server
    servers = set(a['server_name'] for a in anomalies)
    
    context_parts = []
    for server in servers:
        history = load_sensor_history(server)
        if history:
            latest = history[0]
            context_parts.append(f"""
Server: {server}
Recent History (most recent first):
- CPU Temp: {latest.get('cpu_temp', 'N/A')}°C
- Ambient: {latest.get('ambient_temp', 'N/A')}°C  
- Fan RPM (avg): {latest.get('avg_fan_rpm', 'N/A')}
- Power: {latest.get('power_watts', 'N/A')}W
""")
    
    context = "\n".join(context_parts)
    
    anomaly_list = "\n".join([
        f"- [{a['severity'].upper()}] {a['server_name']}: {a['message']}"
        for a in anomalies
    ])
    
    prompt = f"""You are a Dell PowerEdge server infrastructure expert. Analyze the following hardware anomalies and provide actionable recommendations.

CONTEXT:
{context}

CURRENT ANOMALIES:
{anomaly_list}

TASK:
1. Identify patterns and correlations between anomalies
2. Determine root cause (hardware failure, environmental, configuration)
3. Assess risk level (imminent failure vs routine maintenance)
4. Provide specific, actionable recommendations
5. Estimate urgency (IMMEDIATE/SOON/ROUTINE/NONE)

OUTPUT FORMAT (strictly follow this structure):
## Analysis
[Brief summary of what's happening]

## Root Cause Assessment
- [Likely cause 1]
- [Likely cause 2 if applicable]

## Recommendations
1. [Immediate action if needed]
2. [Follow-up actions]
3. [Preventive measures]

## Urgency: [IMMEDIATE|SOON|ROUTINE|NONE]
## Confidence: [HIGH|MEDIUM|LOW]
## Estimated Time to Failure: [if applicable]

Be concise but thorough. Prioritize safety and data protection. If hardware replacement is needed, provide Dell part numbers where known."""

    return prompt


def query_ollama(prompt, model=None):
    """Query Ollama API for LLM response."""
    model = model or DEFAULT_MODEL
    
    try:
        # Check if Ollama is running
        resp = requests.get(f"{OLLAMA_URL}/api/version", timeout=5)
        if resp.status_code != 200:
            raise ConnectionError("Ollama not responding")
        
        # Generate response
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,  # Lower for more deterministic output
                "top_p": 0.9
            }
        }
        
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=60
        )
        
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama error: {resp.text}")
        
        result = resp.json()
        return result.get('response', '')
        
    except ConnectionError:
        logger.warning("Ollama unavailable - using fallback analysis")
        return None
    except Exception as e:
        logger.error(f"LLM query failed: {e}")
        return None


def fallback_analysis(anomalies):
    """Rule-based fallback when LLM unavailable."""
    critical_count = sum(1 for a in anomalies if a['severity'] == 'critical')
    warning_count = sum(1 for a in anomalies if a['severity'] == 'warning')
    
    analysis = """## Analysis
Multiple hardware anomalies detected. Automated rule-based analysis follows.

## Root Cause Assessment
"""
    
    for a in anomalies:
        if 'temperature' in a['metric']:
            analysis += f"- Thermal issue on {a['server_name']}: Check cooling, airflow, thermal paste\n"
        elif 'fan' in a['metric']:
            analysis += f"- Fan issue on {a['server_name']}: Replace fan module immediately\n"
        elif 'power' in a['metric']:
            analysis += f"- Power anomaly on {a['server_name']}: Check PSU, power cables, workload\n"
        elif 'connectivity' in a['metric']:
            analysis += f"- Network issue: Verify iDRAC connectivity, cable, switch port\n"
        elif 'sel' in a['metric']:
            analysis += f"- SEL event on {a['server_name']}: Review full hardware logs\n"
    
    analysis += f"""
## Recommendations
1. Address all CRITICAL alerts immediately
2. Schedule maintenance for WARNING items within 7 days
3. Monitor affected servers closely until resolved

## Urgency: {'IMMEDIATE' if critical_count > 0 else 'SOON' if warning_count > 0 else 'ROUTINE'}
## Confidence: LOW (fallback analysis - LLM unavailable)
## Note: Deploy Ollama for enhanced analysis
"""
    
    return analysis


def save_analysis(analysis_text, anomalies_data):
    """Save analysis to file."""
    DATA_DIR.mkdir(exist_ok=True)
    
    analysis_file = DATA_DIR / "latest_analysis.txt"
    with open(analysis_file, 'w') as f:
        f.write(f"Analyzed at: {datetime.utcnow().isoformat()}\n")
        f.write(f"Anomaly count: {anomalies_data['count']}\n")
        f.write("=" * 50 + "\n\n")
        f.write(analysis_text)
    
    logger.info(f"Analysis saved to {analysis_file}")
    return analysis_file


def main():
    anomalies_data = load_anomalies()
    if not anomalies_data:
        sys.exit(1)
    
    anomalies = anomalies_data['anomalies']
    logger.info(f"Processing {len(anomalies)} anomalies")
    
    # Build prompt
    prompt = build_prompt(anomalies_data)
    
    # Query LLM
    logger.info(f"Querying Ollama with model: {DEFAULT_MODEL}")
    analysis = query_ollama(prompt)
    
    # Fallback if LLM unavailable
    if not analysis:
        logger.warning("Using fallback rule-based analysis")
        analysis = fallback_analysis(anomalies)
    
    # Save and display
    save_analysis(analysis, anomalies_data)
    
    print("\n=== LLM ANALYSIS ===")
    print(analysis)
    print("==================\n")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
