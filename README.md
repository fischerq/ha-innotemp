# Innotemp Heating Controller Home Assistant Integration

This is a custom component for Home Assistant to integrate with Innotemp heating controllers. It allows you to read sensor data and control certain parameters exposed by the controller's web API.

**Please Note:** This is a work in progress and may not support all features of the Innotemp controller.

## Supported Features

*   Reading sensor values (e.g., temperatures, battery SOC)
*   Controlling switch-like parameters (ON/OFF/AUTO)

## Installation

### Via HACS (Recommended)

1.  Ensure you have HACS (Home Assistant Community Store) installed.
2.  In Home Assistant, navigate to HACS -> Integrations.
3.  Click on the three dots in the top right corner and select "Custom repositories".
4.  Add the URL of this repository (`https://github.com/your-github-username/ha-innotemp`) as a `Integration` type.
5.  Close the custom repositories dialog.
6.  Search for "Innotemp" in the HACS Integrations section and click on it.
7.  Click "Download" and select the latest version.
8.  Restart Home Assistant.

### Manual Installation

1.  Navigate to your Home Assistant configuration directory (where your `configuration.yaml` is located).
2.  Create a `custom_components` folder if it doesn't exist.
3.  Inside `custom_components`, create a folder named `innotemp`.
4.  Copy all files from the `custom_components/innotemp` directory of this repository into the `custom_components/innotemp` folder you created.
5.  Restart Home Assistant.

## Configuration

1.  After restarting Home Assistant, go to Settings -> Devices & Services.
2.  Click on "Add Integration".
3.  Search for "Innotemp Heating Controller".
4.  Enter the required information:
    *   **Host:** The IP address or hostname of your Innotemp controller (e.g., `192.168.25.204`).
    *   **Username:** Your username for the Innotemp web interface.
    *   **Password:** Your password for the Innotemp web interface.
5.  Click "Submit".

The integration should now be configured and your Innotemp sensors and controls should appear in Home Assistant.

## Development

If you wish to contribute to the development of this integration, please refer to the documentation within the repository.
