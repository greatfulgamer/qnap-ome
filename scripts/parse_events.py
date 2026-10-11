#!/usr/bin/env python3
"""
QNAP-OME: Anomaly Detection Engine
Analyzes polling results against configurable thresholds.

Triggers LLM analysis only when anomalies detected (event-driven).
"""

import os
import sys
import json
import yaml
import logging
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CONFIG_DIR = Path(__file__).parent.parent / "config"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_config():
    """Load threshold configuration."""
    config_file = CONFIG_DIR / "thresholds.yaml"
    if not config_file.exists():
        logger.error(f"Config not found: {config_file}")
        sys.exit(1)
    
    with open(config_file) as f:
        return yaml.safe_load(f)


def load_latest_poll():
    """Load the most recent polling results."""
    cache_file = DATA_DIR / "latest_poll.json"
    if not cache_file.exists():
        logger.warning("No polling data found. Run poll_idrac.py first.")
        return None
    
    with open(cache_file) as f:
        return json.load(f)


def detect_anomalies(results, thresholds):
    """
    Compare sensor readings against thresholds.
    
    Returns list of anomalies:
    [{
        'server_name': str,
        'metric': str,
        'value': float,
        'threshold': float,
        'severity': 'warning'|'critical',
        'message': str
    }]
    """
    anomalies = []
    
    for result in results:
        if not result['success']:
            anomalies.append({
                'server_name': result['server_name'],
                'metric': 'connectivity',
                'value': 0,
                'threshold': 1,
                'severity': 'critical',
                'message': f"Cannot reach iDRAC: {result.get('error', 'Unknown error')}"
            })
            continue
        
        data = result.get('data', {})
        server = result['server_name']
        
        # Temperature checks
        temps = data.get('temperatures', [])
        for temp in temps:
            name = temp.get('name', '').lower()
            reading = temp.get('reading', 0)
            
            if 'cpu' in name:
                warn = thresholds.get('temperatures', {}).get('cpu_warning', 80)
                crit = thresholds.get('temperatures', {}).get('cpu_critical', 90)
                
                if reading >= crit:
                    anomalies.append({
                        'server_name': server,
                        'metric': 'cpu_temperature',
                        'value': reading,
                        'threshold': crit,
                        'severity': 'critical',
                        'message': f"CPU temperature critical: {reading}°C (threshold: {crit}°C)"
                    })
                elif reading >= warn:
                    anomalies.append({
                        'server_name': server,
                        'metric': 'cpu_temperature',
                        'value': reading,
                        'threshold': warn,
                        'severity': 'warning',
                        'message': f"CPU temperature elevated: {reading}°C (threshold: {warn}°C)"
                    })
            
            elif 'ambient' in name:
                warn = thresholds.get('temperatures', {}).get('ambient_warning', 35)
                crit = thresholds.get('temperatures', {}).get('ambient_critical', 40)
                
                if reading >= crit:
                    anomalies.append({
                        'server_name': server,
                        'metric': 'ambient_temperature',
                        'value': reading,
                        'threshold': crit,
                        'severity': 'critical',
                        'message': f"Ambient temperature critical: {reading}°C"
                    })
                elif reading >= warn:
                    anomalies.append({
                        'server_name': server,
                        'metric': 'ambient_temperature',
                        'value': reading,
                        'threshold': warn,
                        'severity': 'warning',
                        'message': f"Ambient temperature elevated: {reading}°C"
                    })
        
        # Fan checks
        fans = data.get('fans', [])
        for fan in fans:
            rpm = fan.get('rpm', 0)
            name = fan.get('name', '')
            
            # Low RPM warning (potential failure)
            low_warn = thresholds.get('fans', {}).get('rpm_low_warning', 2000)
            if rpm < low_warn:
                anomalies.append({
                    'server_name': server,
                    'metric': 'fan_rpm_low',
                    'value': rpm,
                    'threshold': low_warn,
                    'severity': 'critical',
                    'message': f"Fan {name} RPM critically low: {rpm} (threshold: {low_warn})"
                })
            
            # High RPM warning (thermal stress)
            high_warn = thresholds.get('fans', {}).get('rpm_high_warning', 15000)
            if rpm > high_warn:
                anomalies.append({
                    'server_name': server,
                    'metric': 'fan_rpm_high',
                    'value': rpm,
                    'threshold': high_warn,
                    'severity': 'warning',
                    'message': f"Fan {name} running fast: {rpm} RPM (threshold: {high_warn})"
                })
        
        # Power spike detection
        power = data.get('power', {})
        current_watts = power.get('consumption_watts', 0)
        spike_threshold = thresholds.get('power', {}).get('spike_threshold_watts', 500)
        
        if current_watts > spike_threshold:
            # Check historical average for this server
            history = get_historical_power(server)
            if history:
                avg_watts = sum(history) / len(history)
                if current_watts > avg_watts * 1.5:  # 50% spike
                    anomalies.append({
                        'server_name': server,
                        'metric': 'power_spike',
                        'value': current_watts,
                        'threshold': avg_watts * 1.5,
                        'severity': 'warning',
                        'message': f"Power spike detected: {current_watts}W (avg: {avg_watts:.0f}W)"
                    })
        
        # SEL event checks
        sel_events = data.get('sel_events', [])
        for event in sel_events:
            if event.get('severity') in ['Critical', 'Warning']:
                anomalies.append({
                    'server_name': server,
                    'metric': 'sel_event',
                    'value': 1,
                    'threshold': 0,
                    'severity': 'critical' if event['severity'] == 'Critical' else 'warning',
                    'message': f"SEL: {event.get('message', 'Unknown')} [{event['severity']}]"
                })
    
    return anomalies


def get_historical_power(server_name, hours=24):
    """Get recent power readings from SQLite for baseline comparison."""
    import sqlite3
    db_file = DATA_DIR / "fleet.db"
    
    if not db_file.exists():
        return []
    
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    
    cursor.execute('''
        SELECT power_watts FROM sensor_readings
        WHERE server_name = ? AND power_watts IS NOT NULL
        AND timestamp > ?
        ORDER BY timestamp DESC LIMIT 50
    ''', (server_name, cutoff))
    
    results = [row[0] for row in cursor.fetchall() if row[0]]
    conn.close()
    
    return results


def deduplicate_anomalies(anomalies, cooldown_minutes=15):
    """
    Remove duplicate anomalies within cooldown period.
    Keeps only the most recent occurrence.
    """
    seen = {}
    cooldown = timedelta(minutes=cooldown_minutes)
    now = datetime.utcnow()
    
    for anomaly in anomalies:
        key = (anomaly['server_name'], anomaly['metric'])
        
        # Check if we've seen this anomaly recently
        if key in seen:
            last_time = seen[key]['last_seen']
            if now - last_time < cooldown:
                continue  # Skip duplicate
        
        seen[key] {
            'last_seen': now,
            'anomaly': anomaly
        }
    
    return [item['anomaly'] for item in seen.values()]


def save_anomalies(anomalies):
    """Save anomalies to file for LLM processing."""
    DATA_DIR.mkdir(exist_ok=True)
    
    anomaly_file = DATA_DIR / "current_anomalies.json"
    with open(anomaly_file, 'w') as f:
        json.dump({
            'detected_at': datetime.utcnow().isoformat(),
            'count': len(anomalies),
            'anomalies': anomalies
        }, f, indent=2)
    
    logger.info(f"Saved {len(anomalies)} anomalies to {anomaly_file}")


def should_invoke_llm(anomalies):
    """
    Decide if LLM analysis is warranted.
    
    Invoke LLM when:
    - Any critical anomaly
    - Multiple warnings (>3)
    - Connectivity issues
    """
    critical_count = sum(1 for a in anomalies if a['severity'] == 'critical')
    warning_count = sum(1 for a in anomalies if a['severity'] == 'warning')
    
    return critical_count > 0 or warning_count > 3 or any(a['metric'] == 'connectivity' for a in anomalies)


def main():
    config = load_config()
    results = load_latest_poll()
    
    if not results:
        logger.warning("No polling data available")
        sys.exit(0)
    
    # Detect anomalies
    anomalies = detect_anomalies(results, config)
    logger.info(f"Detected {len(anomalies)} potential anomalies")
    
    # Deduplicate
    anomalies = deduplicate_anomalies(anomalies)
    logger.info(f"After dedup: {len(anomalies)} unique anomalies")
    
    if not anomalies:
        logger.info("No anomalies detected. System healthy.")
        sys.exit(0)
    
    # Save for LLM processing
    save_anomalies(anomalies)
    
    # Print summary
    critical = [a for a in anomalies if a['severity'] == 'critical']
    warnings = [a for a in anomalies if a['severity'] == 'warning']
    
    print("\n=== ANOMALY SUMMARY ===")
    for a in critical:
        print(f"[CRITICAL] {a['server_name']}: {a['message']}")
    for a in warnings:
        print(f"[WARNING]  {a['server_name']}: {a['message']}")
    print("======================\n")
    
    # Signal if LLM should be invoked
    if should_invoke_llm(anomalies):
        logger.info("LLM analysis recommended")
        sys.exit(2)  # Special exit code for LLM trigger
    else:
        logger.info("Minor anomalies - monitoring only")
        sys.exit(0)


if __name__ == '__main__':
    main()
