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
from rich import print as rprint

ROUTER_IP = "192.168.1.1"
BASE_URL = f"http://{ROUTER_IP}/cgi-bin"
HEADERS = {
    "Accept": "text/plain, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Cookie": "password=MTkxOTE5MTk=",
    "Referer": f"http://{ROUTER_IP}/",
    "X-Requested-With": "XMLHttpRequest",
}

console = Console()


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
        res = requests.post(
            f"{BASE_URL}/skk_get.cgi", headers=headers, data=payload, timeout=5
        )
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
        res = requests.post(
            f"{BASE_URL}/skk_set.cgi", headers=HEADERS, data=payload, timeout=5
        )
        # The router usually returns a timeout error intentionally after saving, we check if request went through
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException:
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
        res = requests.post(
            f"{BASE_URL}/skk_set.cgi", headers=HEADERS, data=payload, timeout=5
        )
        # The router usually returns a timeout error intentionally after saving, we check if request went through
        if res.status_code == 200 or res.status_code == 504:
            return True
    except RequestException:
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
    table.add_column("PPPoE Username", style="yellow")
    table.add_column("PPPoE Password", style="red")

    for wan in data.get("wan", []):
        ppp_user = (
            b64dec(wan.get("pppUsername", "")) if wan.get("pppUsername") else "N/A"
        )
        ppp_pass = (
            b64dec(wan.get("pppPassword", "")) if wan.get("pppPassword") else "N/A"
        )

        table.add_row(
            wan.get("id", "N/A"),
            wan.get("ifname", "N/A"),
            wan.get("ipAddr", "N/A"),
            ppp_user,
            ppp_pass,
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

    table = Table(title="Connected Devices (ARP List)")
    table.add_column("IP Address", style="green")
    table.add_column("MAC Address", style="cyan")
    table.add_column("Hostname", style="yellow")

    for dev in data.get("arpList", []):
        hostname = b64dec(dev.get("arp_host_name", ""))
        table.add_row(dev.get("arp_ip"), dev.get("arp_mac"), hostname)

    console.print(table)


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
        console.print("6. WPS Audit")
        console.print("7. Update Wi-Fi Settings")
        console.print("8. Update Admin Password")
        console.print("9. Exit")

        choice = Prompt.ask(
            "\nSelect an option",
            choices=["1", "2", "3", "4", "5", "6", "7", "8", "9"],
            default="1",
        )

        if choice == "1":
            print_router_info()
            print_internet_info()
            print_wlan_info()
            print_connected_devices()
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
            print_wps_info()
        elif choice == "7":
            interactive_wlan_update()
        elif choice == "8":
            interactive_password_update()
        elif choice == "9":
            console.print("[bold green]Goodbye![/bold green]")
            break


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
def main():
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
        "--admin-password", type=str, help="New Password for the admin panel"
    )

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

    # Mutually Exclusive enable/disable
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--enable", action="store_true", help="Enable the specified Wi-Fi network"
    )
    group.add_argument(
        "--disable", action="store_true", help="Disable the specified Wi-Fi network"
    )

    args = parser.parse_args()

    # Determine if any CLI flags were used
    action_taken = False

    if args.router is not None:
        ROUTER_IP = args.router
        BASE_URL = f"http://{ROUTER_IP}/cgi-bin"

    if args.full_report:
        print_router_info()
        print_internet_info()
        print_wlan_info()
        print_connected_devices()
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

    if args.admin_password is not None:
        action_taken = True
        update_admin_password(args.admin_password)

    # If no flags were provided, launch interactive TUI
    if not action_taken:
        interactive_menu()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]Operation cancelled by user. Exiting...[/bold red]")
        sys.exit(0)
