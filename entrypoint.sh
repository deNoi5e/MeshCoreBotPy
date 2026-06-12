#!/bin/bash

# Автоопределение порта, если MESHCORE_PORT не задан
if [ -z "$MESHCORE_PORT" ]; then
    if [ -e "/dev/ttyACM0" ]; then
        export MESHCORE_PORT="/dev/ttyACM0"
        echo "Auto-detected port: /dev/ttyACM0"
    elif [ -e "/dev/tty.usbmodem101" ]; then
        export MESHCORE_PORT="/dev/tty.usbmodem101"
        echo "Auto-detected port: /dev/tty.usbmodem101"
    else
        echo "Warning: MESHCORE_PORT not set and no USB device found"
        exit 1
    fi
fi

exec python bot.py
