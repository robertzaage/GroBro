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