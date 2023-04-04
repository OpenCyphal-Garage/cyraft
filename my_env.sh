export UAVCAN__NODE__ID=42                           # Set the local node-ID 42 (anonymous by default)
export UAVCAN__UDP__IFACE=127.0.0.1                  # Use Cyphal/UDP transport via localhost
export UAVCAN__SUB__TEMPERATURE_SETPOINT__ID=2345    # Subject "temperature_setpoint"    on ID 2345
export UAVCAN__SUB__TEMPERATURE_MEASUREMENT__ID=2346 # Subject "temperature_measurement" on ID 2346
export UAVCAN__PUB__HEATER_VOLTAGE__ID=2347          # Subject "heater_voltage"          on ID 2347
export UAVCAN__SRV__LEAST_SQUARES__ID=123            # Service "least_squares"           on ID 123
export UAVCAN__DIAGNOSTIC__SEVERITY=2                # This is optional to enable logging via Cyphal