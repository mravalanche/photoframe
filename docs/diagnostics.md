# Diagnostics and connectivity

Open **Settings → Advanced & recovery → Activity & diagnostics** for recent app
events. **Refresh log** reloads the latest 100 events; choose **Errors and warnings**
to focus on failures. **Download log** exports all retained events, regardless of
the selected filter. Timestamps use UTC, newest first.

## What the app records

- Normal process startup and shutdown, and physical display detection.
- Photo catalog refresh success and failure, including unattended retry failures.
- Provider connection verification failures and successful connections.
- Manual and scheduled frame update completion or failure.
- Unexpected background worker and application request failures.

Failure details identify known causes such as provider HTTP status, connection
failure, timeout, operating system error code, image decode failure or display
failure. Unknown exceptions receive a generic description. The log deliberately
does not store arbitrary exception messages, tracebacks, request bodies, URLs,
photo names, Wi-Fi names or credentials. Detailed third-party driver messages may
still require the Pi's service logs.

History is stored in `PHOTOFRAME_DATA_DIR/diagnostics.json` (default
`data/diagnostics.json`) with owner-only file permissions. Each event atomically
replaces the file and flushes it to disk. The latest 500 events are retained, so
older events are removed automatically; the file is bounded below 256 KiB. A
reset retains this operational history; it contains no account or photo metadata.
If disk persistence fails, the app keeps the bounded history in memory and shows
a warning. A successful later write restores persistence. Unreadable/corrupt
history produces a warning and is replaced when a new event is recorded.

The app's existing LAN/device access setting also controls access to these logs;
there is no separate diagnostics login. See the [security model](../PROJECT.md#security-model-and-limitations).

## When the frame stops responding

The web log can only be opened while the app and network are reachable. A sudden
power cut cannot record its own shutdown, and it is possible for the most recent
event to be missing after storage or power failure. A startup following no normal
shutdown is a clue, not proof of the cause. The history starts with the version
that introduces diagnostics; it cannot recover earlier incidents.

After reconnecting, first download the app history. If it ends without explaining
the failure, collect the relevant service logs on the Pi (not from the development
VM):

```sh
sudo journalctl -u photoframe -u photoframe-updater --since '2 hours ago' --no-pager
sudo journalctl -u NetworkManager --since '2 hours ago' --no-pager
journalctl --list-boots
```

Where persistent journal storage is configured, `journalctl -b -1` selects the
previous boot. Previous-boot logs may be unavailable on systems with a volatile
journal. System logs can include network names, addresses, paths and upstream
error details: review them before sharing. The app does not request journal
permissions or copy the system journal into the browser.

Both the source-install and managed app service use `Restart=on-failure` and
`RestartSec=5`. This restarts a process that exits unsuccessfully. It does not
detect an alive-but-hung process, a lost Wi-Fi connection, an unreachable router,
or a DNS failure. Photoframe retries catalog refreshes using its configured retry
interval and its background worker continues after unexpected exceptions. Neither
mechanism repairs the operating system's Wi-Fi connection. The `/health` endpoint
helps distinguish provider/render health while the app remains reachable.

## Raspberry Pi Wi-Fi investigation

Raspberry Pi OS uses NetworkManager by default from Bookworm onwards. Older or
custom images may use a different network manager. Inspect the actual Pi before
changing configuration. These read-only checks do not request Wi-Fi passwords:

```sh
nmcli --fields DEVICE,TYPE,STATE device status
nmcli --fields GENERAL.STATE,GENERAL.TYPE,GENERAL.DEVICE,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS device show
nmcli --fields IN-USE,SIGNAL device wifi list --rescan no
nmcli --fields connection.autoconnect,connection.autoconnect-retries connection show '<saved-profile>'
```

The signal command uses cached scan results and does not force a Wi-Fi scan.
Connection profiles may have names that differ from their Wi-Fi SSID. On supported
NetworkManager versions, `connection.autoconnect=yes` lets a saved profile connect
when suitable resources are available. `connection.autoconnect-retries=-1` uses
the global default (four attempts if not overridden); `0` means keep trying.
After a failed attempt budget and a timeout, NetworkManager can try autoconnect
again. Profile priority can select between known networks. Autoconnect does not
replace an already active profile just because an application cannot reach its
photo provider, so a server or DNS outage is a separate diagnosis.

No Wi-Fi configuration, reconnect service or reboot watchdog is installed by this
feature. Any change to the Pi's profile, power saving, network services, or router
needs a separately authorised administrative step. Check the event timeline and
NetworkManager journal before choosing a fix. Do not give the app root access or
network-change permissions merely to display diagnostics.

A useful next increment would be an optional, read-only network status panel:
interface state, current local address, cached Wi-Fi signal, and NetworkManager
availability. It should time out quickly and work without NetworkManager on
development hosts. Recording connection-state transitions locally would help
explain an outage after recovery; a browser snapshot alone cannot explain why
the Pi was unreachable earlier. SSIDs, MAC addresses and saved profiles need not
be included in an exported app log. Automatic reconnect/reboot controls should
remain a separate decision because they can interrupt remote access.

References:

- [Raspberry Pi networking configuration](https://www.raspberrypi.com/documentation/computers/configuration.html#configure-networking)
- [Raspberry Pi remote-access network information](https://www.raspberrypi.com/documentation/computers/remote-access.html)
- [NetworkManager connection settings and autoconnect retries](https://www.networkmanager.dev/docs/api/latest/settings-connection.html)
- [NetworkManager startup and network-online behavior](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/NetworkManager-wait-online.service.html)
