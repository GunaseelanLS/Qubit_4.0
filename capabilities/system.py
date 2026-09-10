"""System query capabilities for Qubit."""

import datetime
import psutil


def get_current_time():
    """Get the current system time in HH:MM AM/PM format."""
    return datetime.datetime.now().strftime("%I:%M %p")


def get_current_date():
    """Get the current date in DD-MM-YYYY format."""
    return datetime.datetime.now().strftime("%d-%m-%Y")


def get_battery_status():
    """Get the current battery percentage and status."""
    battery = psutil.sensors_battery()

    if battery:
        return f"Battery is at {battery.percent}%."

    return "Battery information unavailable."
