#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import requests
from requests.exceptions import RequestException

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

ROUTER_IP = "192.168.1.1"
BASE_URL = f"http://{ROUTER_IP}/cgi-bin"
HEADERS = {
    "Accept": "text/plain, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Cookie": "password=MTkxOTE5MTk=",
    "Referer": f"http://{ROUTER_IP}/",
    "X-Requested-With": "XMLHttpRequest",
}

DEBUG = False

console = Console()


def debug_req(method, url, payload, status, response_text=""):
    if not DEBUG:
        return
    console.print(
        f"\n[bold yellow]>>> DEBUG REQ[/bold yellow] [bold cyan]{method}[/bold cyan] {url}"
    )
    if payload:
        console.print(f"[bold yellow]  Payload:[/bold yellow] {payload}")
    console.print(
        f"[bold yellow]  Status:[/bold yellow] [bold green]{status}[/bold green]"
    )
    if response_text:
        snippet = response_text[:500]
        console.print(f"[bold yellow]  Response:[/bold yellow] {snippet}")
    console.print()


# --- Utility Functions ---
def b64dec(data):
    """Safely decode base64 strings with padding fix."""
    if not data:
        return "None"
    data += "=" * (-len(data) % 4)
    try:
        return base64.b64decode(data).decode("utf-8", errors="ignore")
    except Exception:
        return data


def b64enc(data):
    """Safely encode strings to base64."""
    if not data:
        return ""
    return base64.b64encode(data.encode("utf-8")).decode("utf-8")


def clean_json(raw_text):
    """Removes trailing commas before closing braces/brackets to fix malformed router JSON."""
    cleaned = re.sub(r",\s*([\]}])", r"\1", raw_text)
    return json.loads(cleaned)


# --- API Functions ---
def fetch_router_data(app_name, referer_path=""):
    """Fetch and parse data from skk_get.cgi"""
    try:
        headers = HEADERS.copy()
        if referer_path:
            headers["Referer"] = f"http://{ROUTER_IP}/{referer_path}"

        payload = {"mode_name": "skk_get", "wl_link": 0, "app": app_name}
        url = f"{BASE_URL}/skk_get.cgi"
        res = requests.post(url, headers=headers, data=payload, timeout=5)
        debug_req("POST", url, payload, res.status_code, res.text)
        res.raise_for_status()
        return clean_json(res.text)
    except RequestException as e:
        console.print(f"[bold red]Connection Error:[/bold red] {e}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        console.print(
            f"[bold red]JSON Parsing Error:[/bold red] Failed to parse {app_name} data. {e}"
        )
        sys.exit(1)


def update_admin_password(password: str):
    """Update admin password via skk_set.cgi"""
    encoded_pwd = b64enc(password)
    payload = {
        "password": encoded_pwd,
        "new_pwd_confirm": encoded_pwd,
        "passwd_set": "passwd_set",
        "mode_name": "skk_set",
        "app": "passwd",
        "wl_link": "0",
    }

    try:
        url = f"{BASE_URL}/skk_set.cgi"
        res = requests.post(url, headers=HEADERS, data=payload, timeout=5)
        debug_req("POST", url, payload, res.status_code, res.text)
        # The router usually returns a timeout error intentionally after saving, we check if request went through
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException as e:
        debug_req("POST", f"{BASE_URL}/skk_set.cgi", payload, "EXCEPTION", str(e))
        # Timeouts are common upon successful saving/restarting wlan interface
        return True

    return False


def update_wlan(wl_idx, ssid=None, password=None, broad=None, enabled=None):
    """Update Wi-Fi settings via skk_set.cgi"""
    # Fetch current state first so we don't overwrite other settings with nulls
    data = fetch_router_data("wlan", "wlan_setup.html")

    current_net = next(
        (net for net in data.get("wlan", []) if net.get("id") == str(wl_idx)), None
    )
    if not current_net:
        console.print(
            f"[bold red]Error:[/bold red] Wi-Fi ID {wl_idx} not found on the router."
        )
        return False

    # Determine final payload values, falling back to current if not provided
    final_ssid = b64enc(ssid) if ssid is not None else current_net.get("ssid", "")
    final_pass = (
        b64enc(password) if password is not None else current_net.get("wpaPsk", "")
    )

    if enabled is not None:
        final_enabled = "1" if enabled else "0"
    else:
        final_enabled = current_net.get("wlanEnabled", "1")

    if broad is not None:
        final_broad = str(broad)
    else:
        final_broad = current_net.get("broadSSID", "1")
    payload = {
        "mode_name": "skk_set",
        "wl_base_set": "save",
        "wscConfigured": "1",
        "app": "base",
        "wl_link": "0",
        "wl_idx": str(int(wl_idx) - 1),
        "wlanEnabled": final_enabled,
        "broadSSID": final_broad,
        "ssid": final_ssid,
        "wpaPsk": final_pass,
        "encrypt": "4",
        "wlanBand": "11",
        "channelWidth": "1",
        "channel": "0",
    }

    try:
        url = f"{BASE_URL}/skk_set.cgi"
        res = requests.post(url, headers=HEADERS, data=payload, timeout=5)
        debug_req("POST", url, payload, res.status_code, res.text)
        # The router usually returns a timeout error intentionally after saving, we check if request went through
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException as e:
        debug_req("POST", f"{BASE_URL}/skk_set.cgi", payload, "EXCEPTION", str(e))
        # Timeouts are common upon successful saving/restarting wlan interface
        return True

    return False


# --- Display Functions (TUI) ---
def print_router_info():
    data = fetch_router_data("routerInfo", "router_info.html")
    info_text = (
        f"[bold cyan]Model:[/bold cyan] {data.get('model', 'Unknown')} | "
        f"[bold cyan]Version:[/bold cyan] {data.get('version', 'Unknown')}\n"
        f"[bold cyan]CPU Usage:[/bold cyan] {data.get('cpu', 'N/A')} | "
        f"[bold cyan]RAM Usage:[/bold cyan] {data.get('mem', 'N/A')}\n"
        f"[bold cyan]WAN IP:[/bold cyan] {data.get('st_wanIpAddr', 'N/A')} | "
        f"[bold cyan]LAN IP:[/bold cyan] {data.get('st_lanIpAddr', 'N/A')}"
    )
    console.print(
        Panel(info_text, title="Router Hardware & Status", border_style="blue")
    )


def print_internet_info():
    data = fetch_router_data("wan_lan", "wan_setup.html")

    table = Table(title="WAN Interfaces (Internet)", show_lines=True)
    table.add_column("ID", style="cyan")
    table.add_column("Interface", style="blue")
    table.add_column("IP Address", style="green")
    table.add_column("Connection")
    table.add_column("PPPoE Username", style="yellow")
    table.add_column("PPPoE Password", style="red")
    table.add_column("MTU", style="magenta")
    table.add_column("MAC", style="cyan")
    table.add_column("DNS1", style="green")
    table.add_column("DNS2", style="green")

    for wan in data.get("wan", []):
        ppp_user = (
            b64dec(wan.get("pppUsername", "")) if wan.get("pppUsername") else "N/A"
        )
        ppp_pass = (
            b64dec(wan.get("pppPassword", "")) if wan.get("pppPassword") else "N/A"
        )
        mtu = wan.get("pppMtu", "N/A")
        mac = wan.get("mac", "N/A")
        dns1 = wan.get("dns1", "") or "N/A"
        dns2 = wan.get("dns2", "") or "N/A"

        table.add_row(
            wan.get("id", "N/A"),
            wan.get("ifname", "N/A"),
            wan.get("ipAddr", "N/A"),
            "[bold red]OFFLINE[/bold red]"
            if data.get("connected", "0") == "0"
            else f"[bold green]ONLINE[/bold green] [bold grey50]{float(data.get('conn_speed', '0')) / 100}K[/bold grey50]",
            ppp_user,
            ppp_pass,
            str(mtu),
            mac,
            dns1,
            dns2,
        )
    console.print(table)


def print_wlan_info():
    data = fetch_router_data("wlan", "wlan_setup.html")

    table = Table(title="Wi-Fi Networks (WLAN)", show_lines=True)
    table.add_column("ID", justify="center", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Visibility", justify="center")
    table.add_column("SSID", style="green")
    table.add_column("Password", style="red")

    for net in data.get("wlan", []):
        if net.get("ssid"):
            ssid = b64dec(net["ssid"])
            pwd = b64dec(net.get("wpaPsk", ""))
            status = (
                "[bold green]ON[/bold green]"
                if net.get("wlanEnabled") == "1"
                else "[bold red]OFF[/bold red]"
            )
            visibility = (
                "[bold blue]VISIBLE[/bold blue]"
                if net.get("broadSSID") == "1"
                else "[bold grey50]HIDDEN[/bold grey50]"
            )

            table.add_row(str(net.get("id", "N/A")), status, visibility, ssid, pwd)

    console.print(table)


def print_wps_info():
    data = fetch_router_data("wlan", "wps.html")
    wps = data.get("wlan", [{}])[0]

    enabled = (
        "⚠️ [bold red]ENABLED[/bold red]"
        if wps.get("wscEnabled") == "1"
        else "✅ [bold green]DISABLED[/bold green]"
    )
    pin = wps.get("wscPin", "Unknown")
    lockdown = (
        "[bold red]ATTACK DETECTED (LOCKED)[/bold red]"
        if wps.get("lockDown") == "1"
        else "[bold green]Normal[/bold green]"
    )

    text = f"Status: {enabled}\nPIN: [bold yellow]{pin}[/bold yellow]\nSecurity State: {lockdown}"
    console.print(Panel(text, title="WPS Security Audit", border_style="red"))


def print_connected_devices():
    data = fetch_router_data("routerState")

    table = Table(title="Connected Devices (ARP List)", show_lines=True)
    table.add_column("IP Address", style="green")
    table.add_column("MAC Address", style="cyan")
    table.add_column("Hostname", style="yellow")
    table.add_column("Speed Limit", justify="center")

    for dev in data.get("arpList", []):
        hostname = b64dec(dev.get("arp_host_name", ""))
        qos_idx = dev.get("is_qos_idx", "0")
        if qos_idx != "0":
            down = dev.get("qos_down_limit", "0")
            up = dev.get("qos_up_limit", "0")
            if down == "0" and up == "0":
                speed_text = "[bold yellow]Rule assigned[/bold yellow]"
            else:
                speed_text = f"[bold yellow]D:{down} U:{up}[/bold yellow]"
        else:
            speed_text = "[grey50]Unlimited[/grey50]"
        table.add_row(dev.get("arp_ip"), dev.get("arp_mac"), hostname, speed_text)

    console.print(table)


def print_mac_filter_info():
    data = fetch_router_data("routerState", "host_list.html")
    filters = data.get("macFilterList", [])
    blocked = [f for f in filters if f.get("rule") == "0"]

    if not blocked:
        console.print("[bold yellow]No devices are currently blocked.[/bold yellow]")
        return

    table = Table(
        title=f"Blocked Devices (MAC Filter) — {len(blocked)} device(s)",
        show_lines=True,
    )
    table.add_column("ID", style="cyan")
    table.add_column("MAC Address", style="magenta")
    table.add_column("Device Name", style="yellow")
    table.add_column("Days", style="green")
    table.add_column("Time", style="blue")
    for f in blocked:
        table.add_row(
            f.get("id", "N/A"),
            f.get("macAddr", "N/A"),
            b64dec(f.get("comment", "")),
            f.get("day", "N/A"),
            f.get("time", "N/A"),
        )
    console.print(table)


def add_mac_filter(mac_addr, comment=""):
    """Block a device by MAC address."""
    headers_mac = HEADERS.copy()
    headers_mac["Referer"] = f"http://{ROUTER_IP}/host_list.html"
    payload = {
        "comment": b64enc(comment),
        "id": "0",
        "rule": "0",
        "macAddr": mac_addr,
        "day": "127",
        "time": "all",
        "macFilterList": "add",
        "app": "mac_filter",
        "wl_link": "0",
    }
    try:
        url = f"{BASE_URL}/skk_set.cgi"
        res = requests.post(url, headers=headers_mac, data=payload, timeout=5)
        debug_req("POST", url, payload, res.status_code, res.text)
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException as e:
        debug_req("POST", url, payload, "EXCEPTION", str(e))
        return True
    return False


def remove_mac_filter(entry_id, mac_addr):
    """Remove a device from the MAC filter."""
    headers_mac = HEADERS.copy()
    headers_mac["Referer"] = f"http://{ROUTER_IP}/host_list.html"
    payload = {
        "macFilterList": "del",
        "id": str(entry_id),
        "macAddr": mac_addr,
        "app": "mac_filter",
        "wl_link": "0",
    }
    try:
        url = f"{BASE_URL}/skk_set.cgi"
        res = requests.post(url, headers=headers_mac, data=payload, timeout=5)
        debug_req("POST", url, payload, res.status_code, res.text)
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException as e:
        debug_req("POST", url, payload, "EXCEPTION", str(e))
        return True
    return False


def print_qos_info():
    """Display all QoS speed-limited devices."""
    data = fetch_router_data("qos", "host_list.html")
    entries = data.get("qosList", [])

    if not entries:
        console.print("[bold yellow]No speed-limited devices configured.[/bold yellow]")
        return

    table = Table(
        title=f"Speed Limited Devices (QoS) — {len(entries)} device(s)", show_lines=True
    )
    table.add_column("ID", style="cyan")
    table.add_column("MAC Address", style="magenta")
    table.add_column("Device Name", style="yellow")
    table.add_column("Download (KB/s)", justify="right", style="green")
    table.add_column("Upload (KB/s)", justify="right", style="green")
    for e in entries:
        down = e.get("rate_down", "0")
        up = e.get("rate_up", "0")
        down_text = down if down != "0" else "[grey50]Unlimited[/grey50]"
        up_text = up if up != "0" else "[grey50]Unlimited[/grey50]"
        table.add_row(
            e.get("id", "N/A"),
            e.get("macAddr", "N/A"),
            b64dec(e.get("comment", "")),
            down_text,
            up_text,
        )
    console.print(table)


def add_qos_rule(mac_addr, comment="", rate_down=0, rate_up=0):
    """Add or update a QoS speed limit rule."""
    headers_qos = HEADERS.copy()
    headers_qos["Referer"] = f"http://{ROUTER_IP}/host_list.html"
    payload = {
        "qosList": "mod",
        "id": "0",
        "macAddr": mac_addr,
        "comment": b64enc(comment),
        "rate_up": str(rate_up),
        "rate_down": str(rate_down),
        "ceil_up": str(rate_up),
        "ceil_down": str(rate_down),
        "app": "qos",
        "wl_link": "0",
    }
    try:
        url = f"{BASE_URL}/skk_set.cgi"
        res = requests.post(url, headers=headers_qos, data=payload, timeout=5)
        debug_req("POST", url, payload, res.status_code, res.text)
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException as e:
        debug_req("POST", url, payload, "EXCEPTION", str(e))
        return True
    return False


def remove_qos_rule(entry_id, mac_addr):
    """Remove a QoS speed limit rule."""
    headers_qos = HEADERS.copy()
    headers_qos["Referer"] = f"http://{ROUTER_IP}/host_list.html"
    payload = {
        "qosList": "del",
        "id": str(entry_id),
        "macAddr": mac_addr,
        "comment": "",
        "app": "qos",
        "wl_link": "0",
    }
    try:
        url = f"{BASE_URL}/skk_set.cgi"
        res = requests.post(url, headers=headers_qos, data=payload, timeout=5)
        debug_req("POST", url, payload, res.status_code, res.text)
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException as e:
        debug_req("POST", url, payload, "EXCEPTION", str(e))
        return True
    return False


def update_wan_settings(
    wan_id="1",
    username=None,
    password=None,
    mtu=None,
    mac=None,
    dns1=None,
    dns2=None,
):
    """Update WAN/PPPoE settings via skk_set.cgi (two-step process)."""
    headers_step1 = HEADERS.copy()
    headers_step1["Referer"] = f"http://{ROUTER_IP}/wan_setup.html"

    # Step 1: interface_mode save
    payload1 = {
        "wanPortMode": "",
        "wanPortMode_set": "save",
        "app": "interface_mode",
        "wl_link": "0",
    }

    try:
        url = f"{BASE_URL}/skk_set.cgi"
        res1 = requests.post(url, headers=headers_step1, data=payload1, timeout=5)
        debug_req("POST", url, payload1, res1.status_code, res1.text)
    except RequestException as e:
        debug_req("POST", f"{BASE_URL}/skk_set.cgi", payload1, "EXCEPTION", str(e))

    # Step 2: fetch current state to fill missing fields
    data = fetch_router_data("wan_lan", "wan_setup.html")
    current = next((w for w in data.get("wan", []) if w.get("id") == str(wan_id)), {})

    final_username = (
        b64enc(username) if username is not None else current.get("pppUsername", "")
    )
    final_password = (
        b64enc(password) if password is not None else current.get("pppPassword", "")
    )
    final_mtu = str(mtu) if mtu is not None else current.get("pppMtu", "1492")
    final_mac = mac if mac is not None else current.get("mac", "")
    final_dns1 = dns1 if dns1 is not None else current.get("dns1", "")
    final_dns2 = dns2 if dns2 is not None else current.get("dns2", "")
    conn_type = current.get("connType", "0")
    wan_type = current.get("type", "2")
    dhcp_val = current.get("dhcp", "2")

    payload2 = {
        "mode_name": "skk_set",
        "wan": "mod",
        "id": str(wan_id),
        "ifIndex": "1",
        "app": "wan",
        "wl_link": "0",
        "type": str(wan_type),
        "dhcp": str(dhcp_val),
        "connType": str(conn_type),
        "pppUsername": str(final_username),
        "pppPassword": str(final_password),
        "pppMtu": str(final_mtu),
        "acName": "",
        "servName": "",
        "mac": final_mac,
        "dnsMode": "0",
        "dns1": final_dns1,
        "dns2": final_dns2,
    }

    try:
        url = f"{BASE_URL}/skk_set.cgi"
        res = requests.post(url, headers=headers_step1, data=payload2, timeout=15)
        debug_req("POST", url, payload2, res.status_code, res.text)
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException as e:
        debug_req("POST", f"{BASE_URL}/skk_set.cgi", payload2, "EXCEPTION", str(e))
        return True

    return False


def reboot_system():
    payload = {
        "mode_name": "skk_set",
        "reboot_set": "1",
        "app": "reboot",
        "wl_link": "0",
    }

    try:
        url = f"{BASE_URL}/skk_set.cgi"
        res = requests.post(url, headers=HEADERS, data=payload, timeout=5)
        debug_req("POST", url, payload, res.status_code, res.text)
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException as e:
        debug_req("POST", url, payload, "EXCEPTION", str(e))
        return True

    return False


# --- Interactive TUI ---
def interactive_menu():
    while True:
        console.print(
            "\n[bold magenta]=== NETIS Router CLI Dashboard ===[/bold magenta]"
        )
        console.print("1. Get Full Report")
        console.print("2. Router Hardware Info")
        console.print("3. Internet/WAN Info")
        console.print("4. Wi-Fi (WLAN) Info")
        console.print("5. Connected Devices")
        console.print("6. Speed Limit (QoS)")
        console.print("7. MAC Filter (Blocked Devices)")
        console.print("8. WPS Audit")
        console.print("9. Update Wi-Fi Settings")
        console.print("10. Update Admin Password")
        console.print("11. Update WAN/PPPoE Settings")
        console.print("12. Reboot")
        console.print("13. Exit")

        choice = Prompt.ask(
            "\nSelect an option",
            choices=[
                "1",
                "2",
                "3",
                "4",
                "5",
                "6",
                "7",
                "8",
                "9",
                "10",
                "11",
                "12",
                "13",
            ],
            default="1",
        )

        if choice == "1":
            print_router_info()
            print_internet_info()
            print_wlan_info()
            print_connected_devices()
            print_mac_filter_info()
            print_wps_info()
        elif choice == "2":
            print_router_info()
        elif choice == "3":
            print_internet_info()
        elif choice == "4":
            print_wlan_info()
        elif choice == "5":
            print_connected_devices()
        elif choice == "6":
            interactive_qos()
        elif choice == "7":
            interactive_mac_filter()
        elif choice == "8":
            print_wps_info()
        elif choice == "9":
            interactive_wlan_update()
        elif choice == "10":
            interactive_password_update()
        elif choice == "11":
            interactive_wan_update()
        elif choice == "12":
            interactive_reboot()
        elif choice == "13":
            console.print("[bold green]Goodbye![/bold green]")
            break


def interactive_wan_update():
    print_internet_info()
    console.print(
        "[italic grey50](Leave fields empty to keep current settings)[/italic grey50]"
    )
    wan_id = Prompt.ask("WAN ID", default="1")
    mtu = Prompt.ask("MTU (e.g. 1492)")
    mac = Prompt.ask("MAC Address (e.g. 88:bd:09:ae:10:56)")
    dns1 = Prompt.ask("Primary DNS")
    dns2 = Prompt.ask("Secondary DNS")
    username = Prompt.ask("PPPoE Username")
    password = Prompt.ask("PPPoE Password")

    mtu = int(mtu) if mtu else None
    mac = mac if mac else None
    dns1 = dns1 if dns1 else None
    dns2 = dns2 if dns2 else None
    username = username if username else None
    password = password if password else None

    if Confirm.ask("Are you sure you want to update WAN settings?"):
        with console.status(
            "[bold green]Updating WAN settings (this may take a few seconds)..."
        ):
            success = update_wan_settings(
                wan_id=wan_id,
                username=username,
                password=password,
                mtu=mtu,
                mac=mac,
                dns1=dns1,
                dns2=dns2,
            )

        if success:
            console.print(
                "[bold green]✅ WAN settings updated successfully![/bold green]"
            )
            print_internet_info()
        else:
            console.print("[bold red]❌ Failed to update WAN settings.[/bold red]")


def interactive_mac_filter():
    while True:
        console.print(
            "\n[bold magenta]=== MAC Filter (Blocked Devices) ===[/bold magenta]"
        )
        console.print("1. Show Blocked Devices")
        console.print("2. Block a Device")
        console.print("3. Unblock a Device")
        console.print("4. Back to Main Menu")

        sub = Prompt.ask(
            "Select an option",
            choices=["1", "2", "3", "4"],
            default="1",
        )

        if sub == "1":
            print_mac_filter_info()
        elif sub == "2":
            mac = Prompt.ask("MAC Address to block (e.g. aa:bb:cc:dd:ee:ff)")
            comment = Prompt.ask("Device name (optional)")
            if Confirm.ask(f"Block {mac}?"):
                with console.status("[bold green]Blocking device..."):
                    success = add_mac_filter(mac, comment)
                if success:
                    console.print("[bold green]✅ Device blocked![/bold green]")
                    print_mac_filter_info()
                else:
                    console.print("[bold red]❌ Failed to block device.[/bold red]")
        elif sub == "3":
            print_mac_filter_info()
            eid = Prompt.ask("Enter the ID of the device to unblock")
            mac = Prompt.ask("Enter the MAC address")
            if Confirm.ask(f"Unblock device ID {eid}?"):
                with console.status("[bold green]Unblocking device..."):
                    success = remove_mac_filter(eid, mac)
                if success:
                    console.print("[bold green]✅ Device unblocked![/bold green]")
                    print_mac_filter_info()
                else:
                    console.print("[bold red]❌ Failed to unblock device.[/bold red]")
        elif sub == "4":
            break


def interactive_qos():
    while True:
        console.print("\n[bold magenta]=== Speed Limit (QoS) ===[/bold magenta]")
        console.print("1. Show Speed Limited Devices")
        console.print("2. Set Speed Limit")
        console.print("3. Remove Speed Limit")
        console.print("4. Back to Main Menu")

        sub = Prompt.ask(
            "Select an option",
            choices=["1", "2", "3", "4"],
            default="1",
        )

        if sub == "1":
            print_qos_info()
        elif sub == "2":
            print_connected_devices()
            mac = Prompt.ask("MAC Address (e.g. aa:bb:cc:dd:ee:ff)")
            name = Prompt.ask("Device name (optional)")
            rate_down = Prompt.ask(
                "Download limit in KB/s (0 = unlimited)", default="0"
            )
            rate_up = Prompt.ask("Upload limit in KB/s (0 = unlimited)", default="0")
            if Confirm.ask(
                f"Set speed limit for {mac}? (D:{rate_down} U:{rate_up} KB/s)"
            ):
                with console.status("[bold green]Applying speed limit..."):
                    success = add_qos_rule(mac, name, int(rate_down), int(rate_up))
                if success:
                    console.print("[bold green]✅ Speed limit applied![/bold green]")
                    print_qos_info()
                else:
                    console.print(
                        "[bold red]❌ Failed to apply speed limit.[/bold red]"
                    )
        elif sub == "3":
            print_qos_info()
            eid = Prompt.ask("Enter the ID of the rule to remove")
            mac = Prompt.ask("Enter the MAC address")
            if Confirm.ask(f"Remove speed limit for {mac}?"):
                with console.status("[bold green]Removing speed limit..."):
                    success = remove_qos_rule(eid, mac)
                if success:
                    console.print("[bold green]✅ Speed limit removed![/bold green]")
                    print_qos_info()
                else:
                    console.print(
                        "[bold red]❌ Failed to remove speed limit.[/bold red]"
                    )
        elif sub == "4":
            break


def interactive_reboot():
    with console.status(
        "[bold green]Rebooting the router (this may take a few seconds)..."
    ):
        success = reboot_system()

        if success:
            console.print("[bold green]✅ Rebooted successfully![/bold green]")
        else:
            console.print("[bold red]❌ Failed to reboot.[/bold red]")


def interactive_password_update():
    password = Prompt.ask("New Password")
    password_confirm = Prompt.ask("New Password Again")
    if password != password_confirm:
        console.print("[bold red]Wrong matching[/bold red]")
        return
    if Confirm.ask(f"Are you sure you want to update admin password to {password}?"):
        with console.status(
            "[bold green]Updating router settings (this may take a few seconds)..."
        ):
            success = update_admin_password(password)

        if success:
            console.print(
                "[bold green]✅ Admin's password updated successfully![/bold green]"
            )
            print_wlan_info()
        else:
            console.print("[bold red]❌ Failed to update router settings.[/bold red]")


def interactive_wlan_update():
    print_wlan_info()
    wl_idx = Prompt.ask(
        "Enter the [bold cyan]ID[/bold cyan] of the Wi-Fi network to update"
    )

    console.print(
        "[italic grey50](Leave fields empty to keep current settings)[/italic grey50]"
    )
    ssid = Prompt.ask("New SSID")
    password = Prompt.ask("New Password")
    broad = Prompt.ask("Visibility", choices=["1", "0"], default="1")
    status = Prompt.ask("Status", choices=["1", "0"], default="1")

    # Parse inputs
    ssid = ssid if ssid else None
    password = password if password else None

    broad_val = int(broad)

    enabled_val = None
    if status == "1":
        enabled_val = True
    elif status == "2":
        enabled_val = False

    if Confirm.ask(f"Are you sure you want to update WLAN ID {wl_idx}?"):
        with console.status(
            "[bold green]Updating router settings (this may take a few seconds)..."
        ):
            success = update_wlan(wl_idx, ssid, password, broad_val, enabled_val)

        if success:
            console.print(
                "[bold green]✅ Wi-Fi updated successfully![/bold green] (Router network interface may be restarting)."
            )
            print_wlan_info()
        else:
            console.print("[bold red]❌ Failed to update Wi-Fi settings.[/bold red]")


# --- Main Entry Point ---
def main(argv: list[str] | None = None) -> int:
    global ROUTER_IP, BASE_URL
    parser = argparse.ArgumentParser(description="Netis Router Administration CLI")
    parser.add_argument(
        "--router", type=str, help="Router's IP, default to 192.168.1.1"
    )

    # Information Flags
    parser.add_argument(
        "--full-report", action="store_true", help="Print all router information"
    )
    parser.add_argument(
        "--wlan-info", action="store_true", help="Print Wi-Fi networks status"
    )
    parser.add_argument(
        "--internet-info", action="store_true", help="Print WAN and PPPoE info"
    )
    parser.add_argument(
        "--wps-info", action="store_true", help="Print WPS Security Audit"
    )
    parser.add_argument(
        "--devices-info", action="store_true", help="Print connected devices (ARP)"
    )
    parser.add_argument(
        "--mac-filter-info",
        action="store_true",
        help="Print blocked devices (MAC filter)",
    )
    parser.add_argument(
        "--qos-info", action="store_true", help="Print speed limited devices (QoS)"
    )
    parser.add_argument(
        "--admin-password", type=str, help="New Password for the admin panel"
    )
    parser.add_argument("--reboot", action="store_true", help="Reboot the router")
    parser.add_argument(
        "--mac-filter-add",
        type=str,
        metavar="MAC",
        help="Block a device by MAC address",
    )
    parser.add_argument(
        "--mac-filter-name", type=str, help="Device name for --mac-filter-add"
    )
    parser.add_argument(
        "--mac-filter-del", type=str, metavar="ID", help="Unblock a device by filter ID"
    )
    parser.add_argument(
        "--mac-filter-del-mac", type=str, help="MAC address for --mac-filter-del"
    )
    parser.add_argument(
        "--qos-set", type=str, metavar="MAC", help="Set speed limit for a MAC address"
    )
    parser.add_argument("--qos-name", type=str, help="Device name for --qos-set")
    parser.add_argument(
        "--qos-rate-down",
        type=int,
        metavar="KB",
        help="Download limit in KB/s (0=unlimited)",
    )
    parser.add_argument(
        "--qos-rate-up",
        type=int,
        metavar="KB",
        help="Upload limit in KB/s (0=unlimited)",
    )
    parser.add_argument(
        "--qos-del", type=str, metavar="ID", help="Remove speed limit rule by ID"
    )
    parser.add_argument("--qos-del-mac", type=str, help="MAC address for --qos-del")

    # Mutation Flags
    parser.add_argument(
        "--wlan-set",
        type=int,
        metavar="ID",
        help="The ID of the Wi-Fi network to update",
    )
    parser.add_argument(
        "--ssid", type=str, help="New SSID (Name) for the Wi-Fi network"
    )
    parser.add_argument(
        "--password", type=str, help="New Password for the Wi-Fi network"
    )
    parser.add_argument(
        "--broad", type=int, choices=[0, 1], help="1 for Visible, 0 for Hidden"
    )

    # WAN/PPPoE Settings
    parser.add_argument(
        "--wan-set", type=str, metavar="ID", help="Update WAN/PPPoE settings by ID"
    )
    parser.add_argument("--wan-username", type=str, help="New PPPoE username")
    parser.add_argument("--wan-password", type=str, help="New PPPoE password")
    parser.add_argument(
        "--wan-mtu", type=int, metavar="MTU", help="PPPoE MTU (e.g. 1492)"
    )
    parser.add_argument(
        "--wan-mac", type=str, help="WAN MAC address (e.g. 88:bd:09:ae:10:56)"
    )
    parser.add_argument("--wan-dns1", type=str, help="Primary DNS server")
    parser.add_argument("--wan-dns2", type=str, help="Secondary DNS server")

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print outgoing/incoming payloads and status codes",
    )

    # Mutually Exclusive enable/disable
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--enable", action="store_true", help="Enable the specified Wi-Fi network"
    )
    group.add_argument(
        "--disable", action="store_true", help="Disable the specified Wi-Fi network"
    )

    args = parser.parse_args(argv)

    # Determine if any CLI flags were used
    action_taken = False

    global DEBUG
    if args.debug:
        DEBUG = True

    if args.router is not None:
        ROUTER_IP = args.router
        BASE_URL = f"http://{ROUTER_IP}/cgi-bin"

    if args.full_report:
        print_router_info()
        print_internet_info()
        print_wlan_info()
        print_connected_devices()
        print_mac_filter_info()
        print_qos_info()
        print_wps_info()
        action_taken = True
    else:
        if args.wlan_info:
            print_wlan_info()
            action_taken = True
        if args.internet_info:
            print_internet_info()
            action_taken = True
        if args.wps_info:
            print_wps_info()
            action_taken = True
        if args.devices_info:
            print_connected_devices()
            action_taken = True
        if args.mac_filter_info:
            print_mac_filter_info()
            action_taken = True
        if args.qos_info:
            print_qos_info()
            action_taken = True

    # Handle Set Command
    if args.wlan_set is not None:
        action_taken = True
        enabled = None
        if args.enable:
            enabled = True
        elif args.disable:
            enabled = False

        console.print(
            f"[yellow]Attempting to update Wi-Fi ID {args.wlan_set}...[/yellow]"
        )
        success = update_wlan(
            wl_idx=args.wlan_set,
            ssid=args.ssid,
            password=args.password,
            broad=args.broad,
            enabled=enabled,
        )
        if success:
            console.print("[bold green]✅ Wi-Fi updated successfully![/bold green]")
        else:
            console.print("[bold red]❌ Update failed.[/bold red]")

    if args.wan_set is not None:
        action_taken = True
        console.print(f"[yellow]Attempting to update WAN ID {args.wan_set}...[/yellow]")
        success = update_wan_settings(
            wan_id=args.wan_set,
            username=args.wan_username,
            password=args.wan_password,
            mtu=args.wan_mtu,
            mac=args.wan_mac,
            dns1=args.wan_dns1,
            dns2=args.wan_dns2,
        )
        if success:
            console.print(
                "[bold green]✅ WAN settings updated successfully![/bold green]"
            )
        else:
            console.print("[bold red]❌ Update failed.[/bold red]")

    if args.admin_password is not None:
        action_taken = True
        update_admin_password(args.admin_password)

    if args.mac_filter_add is not None:
        action_taken = True
        name = args.mac_filter_name or ""
        console.print(f"[yellow]Blocking {args.mac_filter_add}...[/yellow]")
        success = add_mac_filter(args.mac_filter_add, name)
        if success:
            console.print("[bold green]✅ Device blocked![/bold green]")
        else:
            console.print("[bold red]❌ Failed to block device.[/bold red]")

    if args.mac_filter_del is not None:
        action_taken = True
        mac = args.mac_filter_del_mac or ""
        console.print(f"[yellow]Unblocking filter ID {args.mac_filter_del}...[/yellow]")
        success = remove_mac_filter(args.mac_filter_del, mac)
        if success:
            console.print("[bold green]✅ Device unblocked![/bold green]")
        else:
            console.print("[bold red]❌ Failed to unblock device.[/bold red]")

    if args.qos_set is not None:
        action_taken = True
        name = args.qos_name or ""
        rate_down = args.qos_rate_down or 0
        rate_up = args.qos_rate_up or 0
        console.print(
            f"[yellow]Setting speed limit for {args.qos_set} (D:{rate_down} U:{rate_up} KB/s)...[/yellow]"
        )
        success = add_qos_rule(args.qos_set, name, rate_down, rate_up)
        if success:
            console.print("[bold green]✅ Speed limit applied![/bold green]")
        else:
            console.print("[bold red]❌ Failed to apply speed limit.[/bold red]")

    if args.qos_del is not None:
        action_taken = True
        mac = args.qos_del_mac or ""
        console.print(
            f"[yellow]Removing speed limit rule ID {args.qos_del}...[/yellow]"
        )
        success = remove_qos_rule(args.qos_del, mac)
        if success:
            console.print("[bold green]✅ Speed limit removed![/bold green]")
        else:
            console.print("[bold red]❌ Failed to remove speed limit.[/bold red]")

    if args.reboot:
        action_taken = True
        interactive_reboot()

    # If no flags were provided, launch interactive TUI
    if not action_taken:
        interactive_menu()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        console.print("\n[bold red]Operation cancelled by user. Exiting...[/bold red]")
        sys.exit(0)
