Robust Presence-Based Heating Control Blueprint for Home Assistant
This blueprint creates a powerful and highly customizable automation to control your climate entities (thermostats, TRVs) based on the occupancy state of your home. It's designed to be robust, handling various edge cases and prioritizing user actions to create a predictable and efficient heating schedule.

The logic prioritizes specific states, handles manual overrides gracefully, and includes special modes for guests and vacations, ensuring your heating is always in the right state without wasting energy.

🚀 Features
Occupancy-Based Control: Automatically adjusts heating based on an input_select helper that defines your home's status (e.g., Home, Away, Sleep).

Global Master Switch: A main input_boolean acts as a "Winter Mode" switch to enable or disable the entire heating logic with a single click.

Intelligent State-Saving: Saves the state of your thermostats (temperature and mode) when you leave and restores it upon your return. This ensures that any manual adjustments you make are not lost.

Priority-Driven Logic: The automation follows a strict priority list to avoid conflicts:

Off/Vacation: Highest priority; always turns the heating off.

Away/Sleep: Forces the heating to a configurable Eco temperature.

Scheduler: A time-based scheduler can set a Comfort temperature, but only if no higher-priority state is active.

Special Modes:

Guest Mode: Prevents the scheduler from running and can optionally set the heating to Eco temperature at night.

Vacation Mode: Turns all heating completely off.

Smart Activation: When you enable the master switch, the automation immediately checks the current occupancy state and applies the correct temperature, rather than waiting for the next state change.

Configurable Temperatures: Easily set your desired Eco and Comfort temperatures directly in the blueprint UI.

📋 Requirements
Before using this blueprint, you need to create a few Helper entities in Home Assistant (under Settings > Devices & Services > Helpers).

Occupancy Status (input_select): This helper tracks the presence status.

Required Options: Home, Away, Sleep, Leaving, Coming Home, Guest Mode, Vacation. You can add more, but these are essential for the logic.

Example Configuration (in configuration.yaml):

YAML

input_select:
  flat_occupation_state:
    name: Occupancy Status
    options:
      - "Home"
      - "Away"
      - "Sleep"
      - "Leaving"
      - "Coming Home"
      - "Guest Mode"
      - "Vacation"
    icon: mdi:home-account
Master Heating Switch (input_boolean): This is your main on/off switch for the heating season.

Example Name: Heating Season On

Scheduler Switch (input_boolean) (Optional): If you want to use a time-based schedule (e.g., from the scheduler-card integration or another automation), create a boolean helper that turns on when the schedule is active.

Example Name: Heating Schedule Active

🛠️ Blueprint Inputs
Input	Description
Heating Entities	(Required) The climate entities you want to control (e.g., your thermostats).
Occupancy Status	(Required) The input_select helper you created for tracking the home's status.
Heating On/Off	(Required) The input_boolean master switch for enabling/disabling the logic.
Eco Temperature	The temperature (in °C) to be set for Away, Sleep, and during the night in Guest Mode.
Comfort Temperature	The temperature (in °C) to be set when the optional scheduler is active.
Enable Scheduler	A checkbox to enable or disable the scheduler functionality.
Scheduler Switch	(Optional) The input_boolean helper that indicates if your heating schedule is currently active.
Nightly Eco Time	The time of day to switch the heating to Eco temperature when Guest Mode is active.

Export to Sheets
⚙️ How It Works
The automation is triggered by any change in the selected input entities. It then follows a choose logic to determine the correct action based on a clear hierarchy.

Is the system off?

If the Heating On/Off switch is off OR the Occupancy Status is Vacation, the thermostats are turned off. This is the highest priority.

Is anyone Away or Sleeping?

If the Occupancy Status changes to Away or Sleep, the heating is immediately set to the Eco Temperature. This overrides the scheduler.

Is it nighttime for guests?

If Guest Mode is active and the current time matches the Nightly Eco Time, the heating is set to Eco Temperature (only if it wasn't already off).

Is someone leaving?

When the status changes from Home to Away or Leaving, the automation calls scene.create to take a "snapshot" of the current thermostat states (temperature, mode, etc.).

Note: This snapshot is stored in memory and does not survive a Home Assistant restart.

Is someone coming home?

When the status changes to Coming Home or Home, the previously saved scene is restored. This brings your heating back to the exact state it was in before you left.

This logic is skipped if changing from states like Guest Mode or Vacation to prevent restoring an irrelevant temperature.

Should the scheduler run?

If none of the above conditions are met, the automation checks if the Scheduler is enabled and its corresponding input_boolean is on.

If so, and if the Occupancy Status is Home, it sets the heating to the Comfort Temperature.

📦 Setup Instructions
Import the Blueprint: Click the button below to import this blueprint into your Home Assistant instance.

Create an Automation:

Navigate to Settings > Automations & Scenes.

Click Create Automation and select the Robust Presence-Based Heating Control blueprint.

Fill in all the required entities and configure the temperatures and optional features to your liking.

Save the automation.

Your intelligent heating control is now ready!
