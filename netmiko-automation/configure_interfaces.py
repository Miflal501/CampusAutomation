import os
import re
import sys
import yaml
import logging
from datetime import datetime
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoTimeoutException,
    NetmikoAuthenticationException,
)

INVENTORY_FILE = "devices.yaml"


def setup_logging():

    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = f"logs/configure_routers_{timestamp}.log"

    logger = logging.getLogger("configure_routers")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Logging to {log_path}")
    return logger

def load_inventory(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def connect(device, logger):
    conn_params = {
        "device_type": device["device_type"],
        "host": device["ip"],          # management IP
        "username": device["username"],
        "password": device["password"],
        "secret": device.get("secret", ""),
        "timeout": 10,
    }

    try:
        conn = ConnectHandler(**conn_params)
        conn.enable()
        logger.info(f"[{device['hostname']}] Connected via {device['ip']}")
        return conn

    except NetmikoAuthenticationException:
        logger.error(f"[{device['hostname']}] Authentication failed - check username/password/secret")
    except NetmikoTimeoutException:
        logger.error(f"[{device['hostname']}] Connection timed out - check IP/reachability")
    except Exception as e:
        logger.error(f"[{device['hostname']}] Unexpected connection error: {e}")

    return None

def configure_interfaces(device, conn, logger):
    hostname = device["hostname"]
    interfaces = device.get("interfaces", [])

    if not interfaces:
        logger.warning(f"[{hostname}] No 'interfaces' defined in inventory - skipping")
        return

    for iface in interfaces:
        name = iface["name"]
        ip = iface["ip"]
        mask = iface["mask"]
        description = iface.get("description", "")

        try:
            # --- Idempotency check ---------------------------------
            current_config = conn.send_command(f"show run interface {name}")
            desired_line = f"ip address {ip} {mask}"

            if desired_line in current_config:
                logger.info(f"[{hostname}] {name} already has {ip} {mask} - skipping (idempotent)")
                continue

            # --- Push config -----------------------------------------
            config_commands = [
                f"interface {name}",
                f"description {description}",
                f"ip address {ip} {mask}",
                "no shutdown",
            ]

            output = conn.send_config_set(config_commands)
            logger.info(f"[{hostname}] Configured {name} -> {ip} {mask}")
            logger.debug(output)

        except Exception as e:
            # Catch per-interface errors so one bad interface entry
            # doesn't abort the whole device's configuration run.
            logger.error(f"[{hostname}] Failed to configure {name}: {e}")


def main():
    logger = setup_logging()
    logger.info("=== EE8203 Router Automation Started ===")

    try:
        inventory = load_inventory(INVENTORY_FILE)
    except FileNotFoundError:
        logger.error(f"Inventory file '{INVENTORY_FILE}' not found")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse '{INVENTORY_FILE}': {e}")
        sys.exit(1)

    routers = inventory.get("routers", [])
    if not routers:
        logger.error("No routers found in inventory - nothing to do")
        sys.exit(1)

    for device in routers:
        conn = connect(device, logger)
        if conn is None:
            continue  # move on to next router rather than crashing the whole run

        try:
            configure_interfaces(device, conn, logger)
            
        finally:
            conn.disconnect()
            logger.info(f"[{device['hostname']}] Disconnected")

    logger.info("=== EE8203 Router Automation Finished ===")


if __name__ == "__main__":
    main()
