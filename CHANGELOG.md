## v2.6.3

### Bug Fixes
+ Added missing device class `battery` to individual battery SOC sensors for NOAH and SPF devices, so they display with colored badges in Home Assistant (#181)

## v2.6.2

### New Features
+ `MAX_BAT=auto` is now the default. The number of battery packs is detected automatically. You can still override with `MAX_BAT=1`, `MAX_BAT=2`, etc.
+ Battery serial numbers are now combined into a single sensor per battery (`Bat2/3/4 Serial`) instead of 4 separate part-sensors. Battery 1 uses the device serial ("Device SN").
+ PTQ inverters (NEO 1000M-X via LoRa) behind ShineWeLink dongles are now extracted from config messages and registered as separate NEO devices in Home Assistant, with modbus data routed to them automatically.

### Bug Fixes
+ Fixed MQTT topic serial containing control characters being used as HA device identifier
+ Fixed ShineWeLink config messages (type 0x0129, function 0x29) being silently dropped

## v2.5.2

### Bug Fixes
+ Fixed NOAH FE19 config messages using the config's serial number (data logger serial) as HA device identifier instead of the MQTT topic serial. This caused multiple devices behind a shared data logger to be merged into one device in Home Assistant (#178)

## v2.5.1

### Bug Fixes
+ Fixed regression where NOAH message dispatcher consumed ALL device types' function 3 (holding register) and function 16 (preset multiple) messages, causing them to never reach the modbus parser. This broke NEO, NEXA, and SPF devices that send these function codes.

## v2.5.0

### New Features
+ Added `MAX_BAT` environment variable to control how many battery packs appear in Home Assistant. For example, `MAX_BAT=1` hides all but the first battery module. Set it to match how many batteries your system actually has.
+ Added `FILTER_DATA_GLITCHES` option (default: off) to prevent glitches on total_increasing sensors after a device reconnects (#154)
+ Added 37 missing NOAH input registers: PV1/PV2 temperature, battery SOH, PV3/PV4 voltage/current/temperature, battery 1 serial number, all battery warning and protection statuses, work mode, charging status/power, fault status, AC couple statuses, CT/grid/household load registers, system temperature, and cell voltage limits
+ Parser now supports the `mult` field (in addition to `float_options`) for simpler register definitions
+ Added support for ShineWeLink-X2 data loggers (RAQ serial prefix). These dongles bridge LoRa-connected inverters like the NEO 1000M-X into MQTT. Their messages are now properly routed, including config telemetry (firmware version, serial number).
+ Added parser for new message type `0x6F64` (EcoTracker JSON data). EcoTracker sensor data is now forwarded to HA instead of being silently dropped (#176)

### Bug Fixes
+ Fixed missing `device_class: voltage` for Output Voltage sensor
+ Removed duplicate `out_voltage` register definition
+ Added `VENDOR_100` to modbus function enum and downgraded unknown function log from INFO to DEBUG to reduce log noise (#176)
+ Fixed NOAH config messages (FE19) not being processed - firmware version, serial number and other device info are now properly saved in Home Assistant

## v2.4.0

### New Features
+ Added NEXA register to allow grid charging

### Bug Fixes
+ Fixed wrong NEXA battery 1 temperature reading (#162)
+ Fixed bug which always blocks config messages from Growatt cloud

## v2.3.0

### New Features
+ Added most of the NEXA 2000 config and holding registers that were still missing (#157)
+ Improved logging for MQTT config (#55)

### Bug Fixes
+ NEXA: "Battery Count" and "Battery Cycle Count" should be INT, not FLOAT (#161)

## v2.2.0

### Bug Fixes
+ Fixed NOAH battery temperature accuracy by correcting offsets and mapping
+ Fixed NOAH battery temperature display to show "unknown" instead of stale data when offline
+ Fixed missing `AVAILABILITY_SENSOR` configuration in the Home Assistant add-on (#132)
+ Fixed message parsing order to prevent read from wrong parser
+ Improved logging for configuration read responses and general system events

### New Features
+ Added `GROWATT_CLOUD_CONFIG_FILTER` option to prevent the Growatt Cloud from remotely changing datalogger settings (#38)
+ Added interactive control features: Datalogger restart button and Time Synchronization button (#122) and (#81)
+ Added ability to configure the data reporting interval
+ Added configurable MQTT Client IDs to allow running multiple GroBro instances in parallel (#142)
+ Added support for reading configuration messages and updated all register maps with config controls
+ Enhanced UI with updated control icons for configuration settings
+ Added `TZ` option to HA-Addon to set the desired time zone for logging (#149)

## v2.1.0

### Bug Fixes

+ Fixed pydantic warnings on startup
+ Continue with local message processing if cloud forwarding fails
+ Fixed object_id deprecated messages in HA

### New Features

+ Added support for SPF models (via Shine WiFi X dongle)
+ Added smart mode (NOAH) which works only in connection with Growatt cloud again
+ Extended NOAH device infos
+ Added option to expose device availability via dedicated online sensor and to send sensor states retain via MQTT

## v2.0.1

### Bug Fixes

+ Subscribe to MQTT topic again after broken connection

### New Features

+ Added possibility to select the smart mode for slots of a NOAH

## v2.0.0

⚠️ This is a major release with **breaking changes**. Please read carefully before upgrading.
+ Double-check and **update your configuration**
+ Remove any old Growatt devices from Home Assistant if you experience problems

### Breaking Changes

+ **Home Assistant auto-discovery** is now **device-based** (was previously global).
+ The following environment variables have been **deprecated**:
  + `REGISTER_FILTER` → replaced by automatic device detection and is removed
  + `ACTIVATE_COMMUNICATION_GROWATT_SERVER` → replaced by `GROWATT_CLOUD` with optional **selective forwarding** per device serial
+ **Sensor names may have changed**, and **additional sensors were added**.  

### New Environment Variables

+ `GROWATT_CLOUD`: Enables Growatt cloud communication with **selective forwarding**
+ `DEVICE_TIMEOUT`: Marks device as inactive after a specified timeout without data
+ `MAX_SLOTS`: Sets number of Home Assistant control timeslots for **NOAH batteries**

### New Features

+ Full codebase refactor
+ **Control support** for Inverters and Batteries
+ Added support for **NEXA-series batteries**

## v1.7.4

+ Enabled automatic selection of the correct register map based on device ID, deprecating the user-unfriendly `REGISTER_FILTER` variable.
+ Replaced `ACTIVATE_COMMUNICATION_GROWATT_SERVER` with `GROWATT_CLOUD`, enabling selective message forwarding via a comma-separated list.

## v1.7.3

+ Fix #52: missing ssl import in ha client

## v1.7.2

+ Fix broken shebang in run.sh
+ Add missing jq dependency in docker build

## v1.7.0
+ Introduced semantic versioning.
+ Refactoring for a new modular design.
+ Added a new unavailability option.

## v1.6
+ Large update of NOAH mappings.
+ Small fixes in message detection.

## v1.5
+ Updated the NOAH and NEO mappings.
+ Added a new message dump option, DUMP_MESSAGES=True, which writes all incoming messages to /data.
+ Introduced a LOG_LEVEL option for configurable logging.

## v1.4
Thanks to @justinh998 for adding two-way message forwarding to Growatt Cloud! 🎉

You can enable the relay by setting:
--env ACTIVATE_COMMUNICATION_GROWATT_SERVER=True

Note: Once enabled, your device can be controlled by Growatt. This can be seen as both a benefit and a potential risk, depending on your use case.

## v1.3
Use REGISTER_FILTER variable to set the right mapping for your Inverters and batteries.

Example: --env REGISTER_FILTER=QMN000XXXXXXXX:NEO800,YYYYYYYYXXXXX:NOAH

## v1.2
Good news, everyone!

In this release, NOAH-series batteries are now partially (mapping isn't complete yet) supported and will show up in Home Assistant as—yep, you guessed it—battery.

The updated register mapping results in a large number of new sensors appearing in Home Assistant. This will be addressed in the upcoming release through device-based register masks.

## v1.1
Added support for config messages and enhanced device information

## v1.0
Another try
