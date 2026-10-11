#!/usr/bin/env python3
"""
QNAP-OME: Dell iDRAC8 Fleet Monitor
Polls iDRAC Redfish API for hardware health metrics.

Usage:
    python3 poll_idrac.py --test          # Test single server
    python3 poll_idrac.py --all           # Poll all servers
    python3 poll_idrac.py --verbose       # Debug output
"""

import os
import sys
import json
import yaml
import argparse
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# Suppress SSL warnings (iDRAC uses self-signed certs)
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# Configuration
CONFIG_DIR = Path(__file__).parent.parent / "config"
DATA_DIR = Path(__file__).parent.parent / "data"
LOGS_DIR = Path(__file__).parent.parent / "logs"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGS_DIR / "poll.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def load_config():
    """Load server configuration from YAML."""
    config_file = CONFIG_DIR / "servers.yaml"
    if not config_file.exists():
        logger.error(f"Config not found: {config_file}")
        sys.exit(1)
    
    with open(config_file) as f:
        return yaml.safe_load(f)


def get_password(server_name, password_env):
    """Retrieve iDRAC password from environment variable."""
    password = os.environ.get(password_env)
    if not password:
        logger.warning(f"No password found for {server_name} (env: {password_env})")
        return None
    return password


def poll_idrac(server_config, verbose=False):
    """
    Poll a single iDRAC via Redfish API.
    
    Returns dict with:
    - server_name
    - timestamp
    - success (bool)
    - data: {temperatures, fans, power, sel_events}
    - error (if failed)
    """
    name = server_config['name']
    ip = server_config['idrac_ip']
    username = server_config.get('username', 'root')
    password = get_password(name, server_config['password_env'])
    
    if not password:
        return {
            'server_name': name,
            'timestamp': datetime.utcnow().isoformat(),
            'success': False,
            'error': 'Password not configured'
        }
    
    base_url = f"https://{ip}"
    
    try:
        # Session login
        session_url = urljoin(base_url, "/redfish/v1/SessionService/Sessions")
        session_data = {
            "UserName": username,
            "Password": password
        }
        
        resp = requests.post(
            session_url,
            json=session_data,
            verify=False,
            timeout=10
        )
        
        if resp.status_code != 201:
            return {
                'server_name': name,
                'timestamp': datetime.utcnow().isoformat(),
                'success': False,
                'error': f"Login failed: {resp.status_code} {resp.text[:200]}"
            }
        
        auth_header = resp.headers.get('X-Auth-Token')
        headers = {'X-Auth-Token': auth_header}
        
        result = {
            'server_name': name,
            'timestamp': datetime.utcnow().isoformat(),
            'success': True,
            'data': {}
        }
        
        # Collect temperatures
        sensors_url = urljoin(base_url, "/redfish/v1/Chassis/System.Embedded.1/Sensors")
        resp = requests.get(sensors_url, headers=headers, verify=False, timeout=10)
        if resp.status_code == 200:
            sensors = resp.json()
            temperatures = []
            for member in sensors.get('Members', []):
                sensor_url = member['@odata.id']
                sensor_resp = requests.get(urljoin(base_url, sensor_url), headers=headers, verify=False, timeout=5)
                if sensor_resp.status_code == 200:
                    sensor_data = sensor_resp.json()
                    if 'PhysicalContext' in sensor_data and 'Temperature' in sensor_data.get('ReadingUnits', ''):
                        temperatures.append({
                            'name': sensor_data.get('Name', 'Unknown'),
                            'reading': sensor_data.get('Reading', 0),
                            'unit': sensor_data.get('ReadingUnits', 'C'),
                            'status': sensor_data.get('Status', {})
                        })
            result['data']['temperatures'] = temperatures
        
        # Collect fan speeds
        chassis_url = urljoin(base_url, "/redfish/v1/Chassis/System.Embedded.1/")
        resp = requests.get(chassis_url, headers=headers, verify=False, timeout=10)
        if resp.status_code == 200:
            chassis_data = resp.json()
            fans = []
            if 'Fans' in chassis_data:
                for fan in chassis_data['Fans'].get('Members', []):
                    fan_url = fan['@odata.id']
                    fan_resp = requests.get(urljoin(base_url, fan_url), headers=headers, verify=False, timeout=5)
                    if fan_resp.status_code == 200:
                        fan_data = fan_resp.json()
                        fans.append({
                            'name': fan_data.get('Name', 'Unknown'),
                            'rpm': fan_data.get('LowerThresholdCritical', 0),  # Adjust based on actual schema
                            'status': fan_data.get('Status', {})
                        })
            result['data']['fans'] = fans
        
        # Collect power consumption
        power_url = urljoin(base_url, "/redfish/v1/Chassis/System.Embedded.1/Power")
        resp = requests.get(power_url, headers=headers, verify=False, timeout=10)
        if resp.status_code == 200:
            power_data = resp.json()
            result['data']['power'] = {
                'consumption_watts': power_data.get('PowerControl', [{}])[0].get('PowerConsumedWatts', 0),
                'status': power_data.get('Status', {})
            }
        
        # Collect System Event Log (SEL) - recent entries
        sel_url = urljoin(base_url, "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/SEL/Entries")
        resp = requests.get(sel_url, headers=headers, verify=False, timeout=10)
        if resp.status_code == 200:
            sel_entries = resp.json()
            recent = []
            for entry in sel_entries.get('Members', [])[:10]:  # Last 10 events
                entry_url = entry['@odata.id']
                entry_resp = requests.get(urljoin(base_url, entry_url), headers=headers, verify=False, timeout=5)
                if entry_resp.status_code == 200:
                    entry_data = entry_resp.json()
                    recent.append({
                        'id': entry_data.get('Id', ''),
                        'message': entry_data.get('Message', ''),
                        'severity': entry_data.get('Severity', 'OK'),
                        'created': entry_data.get('Created', '')
                    })
            result['data']['sel_events'] = recent
        
        # Cleanup: delete session
        requests.delete(session_url, headers=headers, verify=False, timeout=5)
        
        if verbose:
            logger.info(f"Successfully polled {name}")
        
        return result
        
    except requests.exceptions.RequestException as e:
        return {
            'server_name': name,
            'timestamp': datetime.utcnow().isoformat(),
            'success': False,
            'error': str(e)
        }
    except Exception as e:
        return {
            'server_name': name,
            'timestamp': datetime.utcnow().isoformat(),
            'success': False,
            'error': f"Unexpected error: {str(e)}"
        }


def save_results(results):
    """Save polling results to JSON cache and SQLite."""
    DATA_DIR.mkdir(exist_ok=True)
    
    # JSON cache (latest snapshot)
    cache_file = DATA_DIR / "latest_poll.json"
    with open(cache_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    # SQLite for historical tracking
    import sqlite3
    db_file = DATA_DIR / "fleet.db"
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sensor_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            server_name TEXT,
            success INTEGER,
            temp_cpu REAL,
            temp_ambient REAL,
            fan_rpm_avg REAL,
            power_watts REAL,
            error TEXT
        )
    ''')
    
    for r in results:
        temps = r.get('data', {}).get('temperatures', [])
        fans = r.get('data', {}).get('fans', [])
        power = r.get('data', {}).get('power', {})
        
        cpu_temp = next((t['reading'] for t in temps if 'CPU' in t['name']), None)
        ambient_temp = next((t['reading'] for t in temps if 'Ambient' in t['name']), None)
        avg_fan_rpm = sum(f.get('rpm', 0) for f in fans) / len(fans) if fans else None
        power_watts = power.get('consumption_watts', 0) if power else None
        
        cursor.execute('''
            INSERT INTO sensor_readings 
            (timestamp, server_name, success, temp_cpu, temp_ambient, fan_rpm_avg, power_watts, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            r['timestamp'],
            r['server_name'],
            1 if r['success'] else 0,
            cpu_temp,
            ambient_temp,
            avg_fan_rpm,
            power_watts,
            r.get('error')
        ))
    
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description='Poll Dell iDRAC servers via Redfish API')
    parser.add_argument('--test', action='store_true', help='Test single server')
    parser.add_argument('--all', action='store_true', help='Poll all servers')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()
    
    config = load_config()
    servers = config.get('servers', [])
    
    if not servers:
        logger.error("No servers configured in servers.yaml")
        sys.exit(1)
    
    results = []
    
    if args.test:
        # Test first server
        logger.info(f"Testing: {servers[0]['name']}")
        result = poll_idrac(servers[0], verbose=True)
        print(json.dumps(result, indent=2))
        results = [result]
    
    elif args.all:
        logger.info(f"Polling {len(servers)} servers...")
        for server in servers:
            result = poll_idrac(server, verbose=args.verbose)
            results.append(result)
            if not result['success']:
                logger.warning(f"Failed: {result['server_name']} - {result.get('error')}")
    
    else:
        # Default: poll all
        logger.info(f"Polling {len(servers)} servers...")
        for server in servers:
            result = poll_idrac(server, verbose=args.verbose)
            results.append(result)
    
    # Save results
    save_results(results)
    
    # Summary
    success_count = sum(1 for r in results if r['success'])
    logger.info(f"Completed: {success_count}/{len(results)} servers successful")
    
    return 0 if success_count == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
