#### Vscode debug setup

- Edit `CYPHAL_PATH` in `.env-variables` to your home directory:

    ```
    CYPHAL_PATH="/Users/maksimdrachov/cyraft/demo/custom_data_types:/Users/maksimdrachov/cyraft/demo/public_regulated_data_types"
    UAVCAN__NODE__ID=42
    UAVCAN__UDP__IFACE=127.0.0.1
    UAVCAN__SRV__REQUEST_VOTE__ID=123
    UAVCAN__DIAGNOSTIC__SEVERITY=2
    ```

- In `~/cyraft/.vscode/settings.json`:

    ```
    {
    "python.envFile": "${workspaceFolder}/.env-variables",
    }
    ```

- Vscode: Run and Debug (on `demo_cyraft.py`)

    ![run-and-debug](images/run_and_debug.png)

### Request Vote

While running the previous `demo_cyraft.py`, in a new terminal window:

- Setup

    ```bash
    cd ~/cyraft
    source env/bin/activate
    export CYPHAL_PATH="$HOME/cyraft/demo/custom_data_types:$HOME/cyraft/demo/public_regulated_data_types"
    source my_env.sh
    export UAVCAN__UDP__IFACE=127.0.0.1
    export UAVCAN__NODE__ID=111
    ```

- Send an RPC to request_vote (using `yakut`)

    ```bash
    y q 42 request_vote '[1,1,1,1]'
    ```

    ![request-vote-rpc](images/request_vote_rpc.png)

### Orchestration

<details>
<summary>`cyraft/demo/launch.orc.yaml`</summary>

```yaml
#!/usr/bin/env -S yakut --verbose orchestrate
# Read the docs about the orc-file syntax: yakut orchestrate --help

# Shared environment variables for all nodes/processes (can be overridden or selectively removed in local scopes).
CYPHAL_PATH: "./public_regulated_data_types;./custom_data_types"
# PYCYPHAL_PATH: ".pycyphal_generated"  # This one is optional; the default is "~/.pycyphal".

# Shared registers for all nodes/processes (can be overridden or selectively removed in local scopes).
# See the docs for pycyphal.application.make_node() to see which registers can be used here.
uavcan:
  # Use Cyphal/UDP via localhost:
  udp.iface: 127.0.0.1
  # If you have Ncat or some other TCP broker, you can use Cyphal/serial tunneled over TCP (in a heterogeneous
  # redundant configuration with UDP or standalone). Ncat launch example: ncat --broker --listen --source-port 50905
  serial.iface: "" # socket://127.0.0.1:50905
  # It is recommended to explicitly assign unused transports to ensure that previously stored transport
  # configurations are not accidentally reused:
  can.iface: ""
  # Configure diagnostic publishing, too:
  diagnostic:
    severity: 2
    timestamp: true

# Keys with "=" define imperatives rather than registers or environment variables.
$=:
- $=:
  # Wait a bit to let the diagnostic subscriber get ready (it is launched below).
  - sleep 2
  - # An empty statement is a join statement -- wait for the previously launched processes to exit before continuing.

  # Launch the demo app that implements the thermostat.
  - $=: python3 demo_cyraft.py
    uavcan:
      node.id: 11
      srv.request_vote.id: 101

  # Launch the controlled plant simulator.
  - $=: python3 demo_cyraft.py
    uavcan:
      node.id: 12
      srv.request_vote.id: 102

  # Launch the controlled plant simulator.
  - $=: python3 demo_cyraft.py
    uavcan:
      node.id: 13
      srv.request_vote.id: 103

# Exit automatically if STOP_AFTER is defined (frankly, this is just a testing aid, feel free to ignore).
- ?=: test -n "$STOP_AFTER"
  $=: sleep $STOP_AFTER && exit 111
```
</details>

![orchestration](images/yakut-orchestration.png)

```bash
cd ~/cyraft
source env/bin/activate
cd demo
yakut orc launch.orc.yaml
```

```bash
cd ~/cyraft
source env/bin/activate
export CYPHAL_PATH="$HOME/cyraft/demo/custom_data_types:$HOME/cyraft/demo/public_regulated_data_types"
export UAVCAN__UDP__IFACE=127.0.0.1
export UAVCAN__NODE__ID=123
```