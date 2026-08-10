import os
import yaml
import logging
from datetime import datetime
from netmiko import ConnectHandler

os.makedirs("logs", exist_ok=True)  
timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
logging.basicConfig(
    filename=f"logs/netmiko_test_{timestamp}.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

with open("devices.yaml") as f:
    inventory = yaml.safe_load(f)   # yaml file -> use yaml.safe_load, not json.load

all_devices = inventory.get("routers", []) + inventory.get("switches", [])

for device in all_devices:
    try:
        print(f"Connecting to {device['hostname']} ({device['ip']})...")
        logging.info(f"Attempting connection to {device['hostname']} ({device['ip']})")

        connection = ConnectHandler(
            device_type=device["device_type"],
            host=device["ip"],
            username=device["username"],
            password=device["password"],
            secret=device.get("secret", "")
        )
        connection.enable()
        output = connection.send_command("show ip interface brief")
        print(output)
        logging.info(f"Success on {device['hostname']}:\n{output}")
        connection.disconnect()

    except Exception as e:
        print(f"Failed to connect to {device['hostname']}: {e}")
        logging.error(f"Failed on {device['hostname']}: {e}")
