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
    """Load devices.yaml. Raises on missing/malformed file."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


def connect(device, logger):
    """
    Open a Netmiko SSH session to a device's management IP (VLAN 99 /
    loopback) and drop into enable mode. Returns the connection object,
    or None if the connection failed - callers must check for this.
    """
    conn_params = {
        "device_type": device["device_type"],
        "host": device["ip"],          # management IP, never data-plane IP
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

def configure_nat(device, conn, logger):

    hostname = device["hostname"]
    nat_cfg = device.get("nat")

    if not nat_cfg:
        logger.info(f"[{hostname}] No 'nat' block in inventory - not a NAT device, skipping")
        return

    acl_name = nat_cfg["acl_name"]
    inside_if = nat_cfg["inside_interface"]
    outside_if = nat_cfg["outside_interface"]
    networks = nat_cfg.get("permitted_networks", [])

    try:
        # --- 1. Standard ACL: permit only the allowed subnets ----------
        current_acl = conn.send_command(f"show run | section ip access-list standard {acl_name}")

        missing_lines = []
        for net in networks:
            line = f"permit {net['network']} {net['wildcard']}"
            if line in current_acl:
                logger.info(f"[{hostname}] NAT ACL: '{line}' already present - skipping (idempotent)")
            else:
                missing_lines.append(line)

        if missing_lines:
            acl_commands = [f"ip access-list standard {acl_name}"] + missing_lines
            output = conn.send_config_set(acl_commands)
            logger.info(f"[{hostname}] NAT ACL '{acl_name}' updated: {missing_lines}")
            logger.debug(output)
        else:
            logger.info(f"[{hostname}] NAT ACL '{acl_name}' already fully configured")

        # --- 2. Inside/outside interface marking ------------------------
        for iface_name, direction in [(inside_if, "inside"), (outside_if, "outside")]:
            iface_config = conn.send_command(f"show run interface {iface_name}")
            desired = f"ip nat {direction}"
            if desired in iface_config:
                logger.info(f"[{hostname}] {iface_name}: '{desired}' already present - skipping (idempotent)")
            else:
                output = conn.send_config_set([f"interface {iface_name}", desired])
                logger.info(f"[{hostname}] {iface_name}: applied '{desired}'")
                logger.debug(output)

        # --- 3. Cleanup: remove any STALE overload statement -------------
        #     bound to the same outside interface but referencing a
        #     different ACL than the one desired in devices.yaml.
        current_nat = conn.send_command("show run | include ip nat inside source")

        stale_pattern = re.compile(
            rf"ip nat inside source list (\S+) interface {re.escape(outside_if)} overload"
        )
        for line in current_nat.splitlines():
            match = stale_pattern.search(line.strip())
            if match and match.group(1) != acl_name:
                stale_acl = match.group(1)
                stale_line = line.strip()
                logger.warning(
                    f"[{hostname}] Found stale NAT overload bound to {outside_if} "
                    f"using ACL '{stale_acl}' - removing it"
                )
                output = conn.send_config_set([f"no {stale_line}"])
                logger.info(f"[{hostname}] Removed stale NAT statement: {stale_line}")
                logger.debug(output)

        # --- 4. Push the desired overload statement, if missing ----------
        current_nat = conn.send_command("show run | include ip nat inside source")  # re-read post-cleanup
        desired_nat_line = f"ip nat inside source list {acl_name} interface {outside_if} overload"

        if desired_nat_line in current_nat:
            logger.info(f"[{hostname}] NAT overload statement already present - skipping (idempotent)")
        else:
            output = conn.send_config_set([desired_nat_line])
            logger.info(f"[{hostname}] NAT overload configured: {desired_nat_line}")
            logger.debug(output)

    except Exception as e:
        logger.error(f"[{hostname}] Failed to configure NAT: {e}")



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
            configure_nat(device, conn, logger)
            # configure_acls(device, conn, logger)

        finally:
            conn.disconnect()
            logger.info(f"[{device['hostname']}] Disconnected")

    logger.info("=== EE8203 Router Automation Finished ===")


if __name__ == "__main__":
    main()
